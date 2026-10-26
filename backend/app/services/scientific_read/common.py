"""Shared helpers for the scientific_read service layer.

Centralizes:
- request validation that's identical across endpoints (sort rejection,
  pagination bounds, include-token validation, temperature-range coherence)
- review-rank lookup
- review summary construction from a ``RecordReview`` set
- temperature coverage computation per spec D8
- review-status filtering

Each service module imports from here so the rules are defined once.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import Text, func, select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import (
    MAX_LIMIT,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
    ReviewVisibilityEcho,
    TemperatureCoverage,
    default_visible_statuses,
    status_at_or_above,
)
from app.services.scientific_read.profile import current_read_profile

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PaginatedResponse(Protocol):
    """Minimal response surface consumed by composed-search pagination."""

    records: list[Any]
    pagination: Pagination


def molecular_formula_expr(smiles_column):
    """Hill-notation formula for *smiles_column*, derived by the RDKit cartridge.

    ``mol_formula()`` over ``mol_from_smiles(smiles_column)`` yields Hill
    notation (e.g. ``H2O``, ``C3H6``), with a trailing charge suffix for
    ions (``HO-``, ``H4N+``, ``Fe+2``). Radicals carry no marker: ``[CH3]``
    is ``CH3``. Isotopes are not distinguished — the cartridge's default
    ``mol_formula()`` ignores isotope labels, so heavy water reports as
    ``H2O``, the same as light water. ``mol_from_smiles()`` returns SQL
    NULL for any row whose SMILES fails to parse, so such a row yields a
    NULL formula rather than raising.

    **One expression, every caller.** ``species.py``, ``geometry.py``,
    ``conformers.py`` and ``calculations.py`` each redefined this exact
    string locally rather than importing across a private module boundary.
    That precedent held while there was one shape of caller; the reaction
    read surface (``provenance.py::_build_species_section``) is now a
    fifth, so the expression is lifted here once and the other four
    modules delegate to it instead of keeping their own copy in sync by
    hand. Depending on the cartridge here costs nothing new: the base
    Alembic revision ``60b67e360daf`` runs ``CREATE EXTENSION IF NOT
    EXISTS rdkit`` before any table exists, so a database this code can
    talk to has it.
    """
    return func.mol_formula(func.mol_from_smiles(smiles_column)).cast(Text)


# ---------------------------------------------------------------------------
# Request-side validation helpers
# ---------------------------------------------------------------------------


def reject_client_sort(sort: str | None) -> None:
    """Reject any client-supplied sort (v0 rule, per spec §Sort vocabulary).

    Raises ValueError → 422 ``client_sort_not_supported``.
    """
    if sort is not None:
        raise ValueError(
            "client_sort_not_supported: sort= is not accepted in v0; the "
            "per-endpoint default sort applies."
        )


def software_names_by_calculation(session: Session, calculation_ids: Iterable[int]) -> dict[int, str]:
    """Name of the software that ran each calculation, in one statement (a software filter's match set).

    A calculation with no software release is absent from the result.
    """
    from app.db.models.calculation import Calculation
    from app.db.models.software import Software, SoftwareRelease

    ids = set(calculation_ids)
    if not ids:
        return {}
    rows = session.execute(
        select(Calculation.id, Software.name)
        .join(SoftwareRelease, SoftwareRelease.id == Calculation.software_release_id)
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(Calculation.id.in_(ids))
    ).all()
    return dict(rows)


def validate_pagination(offset: int, limit: int) -> tuple[int, int]:
    """Validate offset/limit per L5 and return them coerced to defaults.

    Raises ValueError → 422 on out-of-range values. The ``limit`` cap
    is the lesser of the per-endpoint ``MAX_LIMIT`` and the hosted
    abuse-control setting ``settings.public_max_limit``; the
    ``offset`` cap protects deep pagination as required by the public
    abuse-controls policy.

    Three codes, not one, because the remedies are three
    ---------------------------------------------------
    ``invalid_pagination`` is the malformed request: a negative offset or
    a limit below one is a client bug, and there is nothing to do about
    it but fix the caller. The two caps are not that. They are hosting
    policy — both are settings, and a self-hosted deployment sets them
    differently — so hitting one is not a mistake the caller could have
    avoided by reading the schema:

    * ``limit_too_large`` — resend the identical query with a smaller
      page size and it succeeds. Mechanically recoverable.
    * ``offset_too_large`` — deep pagination is refused. Retrying with a
      smaller offset returns *different rows*, so the remedy is to
      narrow the query, not to retry. The composed-search path already
      gives that same situation its own code
      (``composed_search_candidate_limit_exceeded``, "narrow the
      query"); reporting it generically here was the inconsistency.

    All three used to be spelled ``invalid_pagination``, and the two
    below carried their real name as a *second* token inside the message
    (``"invalid_pagination: limit_too_large: …"``). A message naming two
    candidate codes cannot pick one honestly, so
    :func:`app.api.error_contract.validation_detail_code` refused to
    promote either and a client received the generic ``validation_error``
    — the code was catalogued, exported, and unreachable on these two
    paths. One token per message is now checked statically by
    ``tests/api/test_error_contract_catalogue_gate.py``.

    Both caps are published in ``context``
    --------------------------------------
    ``limit_too_large`` and ``offset_too_large`` are
    :attr:`~app.api.code_catalogue.Shape.relationship` codes as of
    Calvin's overrule of 2026-08-18: each asserts that a supplied value
    and a server-side cap are in the wrong order, and the code names
    neither. A refusal that does not state the limit cannot be acted on
    without guessing, and both caps are *settings* — a deployment that
    lowers ``public_max_limit`` is not documented anywhere the client can
    read.

    Both values are safe to publish under the disclosure line in
    :class:`~app.api.code_catalogue.Shape`: the cap is TCKDB's own
    configuration, and the supplied value is the caller's own request
    echoed back. Neither measures the corpus.

    ``message_prefix=True`` keeps ``str(exc)`` byte-identical to the
    plain ``ValueError`` these replaced, so the published ``detail`` does
    not move; only the route into ``code`` changes, which is why the
    catalogue entries move to
    :attr:`~app.api.code_catalogue.Surface.coded_exception`.
    """
    if offset < 0:
        raise ValueError("invalid_pagination: offset must be >= 0")
    if limit < 1:
        raise ValueError("invalid_pagination: limit must be >= 1")
    effective_limit_cap = min(MAX_LIMIT, settings.public_max_limit)
    if limit > effective_limit_cap:
        raise CodedValueError(
            "limit_too_large",
            f"limit must be <= {effective_limit_cap} (got {limit})",
            context={"limit_max": effective_limit_cap, "limit": limit},
        )
    if offset > settings.public_max_offset:
        raise CodedValueError(
            "offset_too_large",
            f"offset must be <= {settings.public_max_offset} (got {offset})",
            context={
                "offset_max": settings.public_max_offset,
                "offset": offset,
            },
        )
    return offset, limit


def collect_bounded_pages(
    fetch_page: Callable[[int, int], PaginatedResponse],
    *,
    resource_name: str,
) -> list[Any]:
    """Collect every reachable page for an internal composed search.

    Composed endpoints must not silently treat the first 200 rows as the
    complete set. Walk all pages within the hosted offset bound and fail
    explicitly when the complete result is not reachable.

    ``composed_search_candidate_limit_exceeded`` publishes the bound and
    not the match count
    -------------------------------------------------------------------
    This is the sharp case for the disclosure line in
    :class:`~app.api.code_catalogue.Shape`. The refusal compares two
    numbers and they are not the same kind of number:

    * ``max_traversable`` — ``public_max_offset + page_size`` — is
      TCKDB's own configuration. Publishing it is what makes the refusal
      actionable, and a caller can derive it in about three requests by
      halving a page size anyway.
    * ``expected_total`` is **how many records in the corpus matched the
      query**. That is a measurement of TCKDB's holdings, not of the
      request, and it is exactly the enumeration signal
      ``docs/specs/public_identifier_policy.md`` (§"Why this matters
      now", item 3) rejects sequential integer primary keys for: it
      leaks the total count of objects and roughly the upload schedule.
      A caller who can turn any filter into an exact row count has a
      free census endpoint that no route offers.

    So the count is logged and not published — neither in ``context``
    nor in ``detail``, because ``detail`` is published too and putting it
    there would make the ``context`` omission decorative.
    ``tests/api/test_api_query_caps.py`` asserts its absence from the
    whole body.
    """
    page_size = min(MAX_LIMIT, settings.public_max_limit)
    max_reachable = settings.public_max_offset + page_size
    offset = 0
    expected_total: int | None = None
    records: list[Any] = []

    while True:
        response = fetch_page(offset, page_size)
        page_total = response.pagination.total
        page_records = list(response.records)

        if expected_total is None:
            expected_total = page_total
            if expected_total > max_reachable:
                # Phrased so the code is not in the code position: a log
                # line declares nothing, and a snake_case token in front
                # of a colon is how this codebase declares an error code.
                # See TestNoLoggerFormatStringSitsInTheCodePosition.
                logger.info(
                    "composed search refused as "
                    "composed_search_candidate_limit_exceeded; %s matched %d "
                    "records against a traversable bound of %d",
                    resource_name,
                    expected_total,
                    max_reachable,
                )
                raise CodedValueError(
                    "composed_search_candidate_limit_exceeded",
                    f"{resource_name} matched more records than the "
                    f"{max_reachable} that can be traversed; narrow the query.",
                    context={
                        "resource": resource_name,
                        "max_traversable": max_reachable,
                    },
                )
        elif page_total != expected_total:
            raise ValueError(
                "composed_search_pagination_changed: "
                f"{resource_name} total changed from {expected_total} to "
                f"{page_total} while traversing pages."
            )

        if response.pagination.returned != len(page_records):
            raise ValueError(
                "composed_search_invalid_page: "
                f"{resource_name} pagination.returned did not match records."
            )

        records.extend(page_records)
        if len(records) >= expected_total:
            return records[:expected_total]
        if not page_records:
            raise ValueError(
                "composed_search_pagination_stalled: "
                f"{resource_name} returned an empty page before its reported total."
            )

        offset += len(page_records)
        if offset > settings.public_max_offset:
            # The second site of the same code. Nothing here measures the
            # corpus: the traversal simply ran past a configured offset
            # bound, so the bound is the whole fact and it is published.
            raise CodedValueError(
                "composed_search_candidate_limit_exceeded",
                f"{resource_name} requires an offset beyond "
                f"{settings.public_max_offset}; narrow the query.",
                context={
                    "resource": resource_name,
                    "offset_max": settings.public_max_offset,
                },
            )


def validate_includes(
    requested: Iterable[str],
    legal: set[str],
    endpoint_name: str,
    *,
    internal_tokens: set[str] = frozenset(),
) -> set[str]:
    """Resolve ``include=`` tokens for an endpoint.

    - ``all`` expands to ``legal - {"all"}`` **minus** ``internal_tokens``.
      Tokens marked internal must be requested explicitly; supplying
      ``include=all`` alone does not include them.
    - Tokens not in ``legal`` raise ValueError → 422.
    - Empty / duplicate input is normalized to a set.

    Phase D: callers pass ``internal_tokens={"internal_ids"}`` so the
    ``internal_ids`` opt-in stays out of the ``all`` expansion. Callers
    that genuinely want everything must say ``include=all,internal_ids``.
    """
    requested_set = {t for t in requested if t}
    if not requested_set:
        return set()
    legal_no_all = legal - {"all"}
    if "all" in requested_set:
        # ``all`` expands to public tokens only. Internal tokens still
        # require explicit opt-in.
        public_expansion = legal_no_all - internal_tokens
        # Validate any non-``all`` tokens supplied alongside ``all``.
        explicit = requested_set - {"all"}
        bad = explicit - legal
        if bad:
            sorted_legal = [*sorted(legal_no_all), "all"]
            raise ValueError(
                "unknown_include_token: "
                f"token(s) {sorted(bad)!r} not legal for {endpoint_name}. "
                f"Legal tokens: {sorted_legal!r}"
            )
        return public_expansion | (explicit & legal_no_all)

    bad = requested_set - legal
    if bad:
        sorted_legal = [*sorted(legal_no_all), "all"]
        raise ValueError(
            "unknown_include_token: "
            f"token(s) {sorted(bad)!r} not legal for {endpoint_name}. "
            f"Legal tokens: {sorted_legal!r}"
        )
    return requested_set & legal_no_all


def validate_temperature_range(
    temperature_min: float | None,
    temperature_max: float | None,
    *,
    min_name: str = "temperature_min",
    max_name: str = "temperature_max",
) -> None:
    """Reject a window whose minimum exceeds its maximum with a 422.

    ``min_name`` / ``max_name`` are the caller-facing parameter names (``coverage_temperature_min_k`` on the thermo
    and kinetics reads, where the window filters nothing and only sets what coverage is computed against).
    """
    if (
        temperature_min is not None
        and temperature_max is not None
        and temperature_min > temperature_max
    ):
        # ``invalid_temperature_range`` names a relationship -- two bounds
        # that are the wrong way round -- and names neither value, which is
        # the case ``Shape.relationship`` in ``app.api.code_catalogue`` is
        # about. Its sibling ``invalid_range`` in ``scientific_read/
        # analytics.py`` has carried its two bounds in ``context`` since it
        # was written; this one is the same shape and had nothing.
        #
        # ``message_prefix=True`` keeps ``str(exc)`` -- and so the
        # response's ``detail`` -- byte-identical to the plain
        # ``ValueError`` this replaced. Only the route into ``code``
        # changes, from promoted-out-of-the-sentence to declared on the
        # exception, which is why the catalogue entry moves to
        # ``Surface.coded_exception``. Both values are the caller's own
        # query parameters, so nothing here is a row id.
        raise CodedValueError(
            "invalid_temperature_range",
            f"{min_name} must be <= {max_name}",
            context={
                (min_name if min_name.endswith("_k") else f"{min_name}_k"): temperature_min,
                (max_name if max_name.endswith("_k") else f"{max_name}_k"): temperature_max,
            },
        )


# ---------------------------------------------------------------------------
# Review state helpers
# ---------------------------------------------------------------------------


def visible_statuses(
    *,
    min_review_status: RecordReviewStatus | None,
    include_rejected: bool,
    include_deprecated: bool,
) -> set[RecordReviewStatus]:
    """Compute the set of review statuses visible per the request.

    Combines the default-trust posture (D5) with an optional ``min_review_status``
    threshold (D7, shallow). Rejected and deprecated statuses are excluded
    unless their respective opt-in flag is set; ``min_review_status`` further
    restricts the set to statuses at or above the threshold.

    **Read-profile floor.** This is the one function every scientific read
    service calls to decide what is visible, which makes it the single place
    the ``curated`` profile can be applied without an endpoint being able to
    forget it. Under ``profile=curated`` the floor is raised to ``approved``
    and the ``include_rejected`` / ``include_deprecated`` opt-ins are
    overridden — "show me what TCKDB stands behind, but also include the
    rejected records" is not a coherent request, and honouring it would let a
    caller cite a rejected record under the curated banner.

    The floor only ever *narrows*: a caller who passes a stricter
    ``min_review_status`` than the profile requires keeps their stricter
    value. Outside a scientific HTTP request the profile is ``exploratory``,
    so this function behaves exactly as it did before Stage 3.
    """
    profile_floor = current_read_profile().review_floor

    base = default_visible_statuses(
        include_rejected=include_rejected and profile_floor is None,
        include_deprecated=include_deprecated and profile_floor is None,
    )
    thresholds = [s for s in (min_review_status, profile_floor) if s is not None]
    for threshold in thresholds:
        base = base & status_at_or_above(threshold)
    return base


def review_visibility_echo(
    visible: set[RecordReviewStatus],
) -> ReviewVisibilityEcho:
    """Report an *effective* visibility set from :func:`visible_statuses`.

    Takes the set the read already computed and filtered with, so the echo is
    derived from it and never restated by hand.
    """
    return ReviewVisibilityEcho(
        visible_statuses=sorted(visible, key=lambda s: s.value),
        excluded_statuses=sorted(
            (s for s in RecordReviewStatus if s not in visible), key=lambda s: s.value
        ),
        profile_floor_applied=current_read_profile().review_floor is not None,
    )


def fetch_review_badges(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: Iterable[int],
) -> dict[int, RecordReviewBadge]:
    """Bulk-load review badges for ``(record_type, record_id)`` pairs.

    Returns a mapping from record_id → RecordReviewBadge. IDs without a
    RecordReview row map to a ``not_reviewed`` badge so every record always
    gets a deterministic status.
    """
    record_ids = list(record_ids)
    if not record_ids:
        return {}

    rows = session.scalars(
        select(RecordReview).where(
            RecordReview.record_type == record_type,
            RecordReview.record_id.in_(record_ids),
        )
    ).all()

    badges: dict[int, RecordReviewBadge] = {
        row.record_id: RecordReviewBadge(
            status=row.status,
            reviewed_at=row.reviewed_at,
            reviewer_kind="human" if row.reviewed_by is not None else None,
            note=row.note,
        )
        for row in rows
    }
    # Fill in defaults for records with no review row.
    for rid in record_ids:
        badges.setdefault(
            rid, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
        )
    return badges


def review_summary(badges: Iterable[RecordReviewBadge]) -> ReviewStatusSummary:
    """Build a ReviewStatusSummary from an iterable of RecordReviewBadge."""
    summary = ReviewStatusSummary()
    for badge in badges:
        if badge.status is RecordReviewStatus.approved:
            summary.approved += 1
        elif badge.status is RecordReviewStatus.under_review:
            summary.under_review += 1
        elif badge.status is RecordReviewStatus.not_reviewed:
            summary.not_reviewed += 1
        elif badge.status is RecordReviewStatus.deprecated:
            summary.deprecated += 1
        elif badge.status is RecordReviewStatus.rejected:
            summary.rejected += 1
        summary.total += 1
    return summary


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------


def build_pagination(
    *,
    offset: int,
    limit: int,
    returned: int,
    total: int,
    page: Any = None,
) -> Pagination:
    """Construct the response Pagination block.

    ``total`` is the post-filter count, before offset/limit slicing. Pass the
    :class:`~app.services.scientific_read.keyset.Page` the records were cut from so the block says which paging
    contract served it.
    """
    return Pagination(
        offset=offset,
        limit=limit,
        returned=returned,
        total=total,
        mode=page.mode if page is not None else "offset",
    )


# ---------------------------------------------------------------------------
# Temperature coverage (D8)
# ---------------------------------------------------------------------------


def temperature_coverage(
    *,
    requested_min: float | None,
    requested_max: float | None,
    record_min: float | None,
    record_max: float | None,
) -> TemperatureCoverage:
    """Compute the TemperatureCoverage fragment per D8.

    - If ``requested_min`` is supplied, full coverage requires record_min <= requested_min.
    - If ``requested_max`` is supplied, full coverage requires record_max >= requested_max.
    - extrapolation_distance_k sums the missing K on each side.
    - overlap_fraction is diagnostic only and computed only when both
      requested bounds and both record bounds are present.
    """
    covers = True
    extrapolation = 0.0

    if requested_min is not None:
        if record_min is None or record_min > requested_min:
            covers = False
        if record_min is not None and record_min > requested_min:
            extrapolation += record_min - requested_min

    if requested_max is not None:
        if record_max is None or record_max < requested_max:
            covers = False
        if record_max is not None and record_max < requested_max:
            extrapolation += requested_max - record_max

    overlap_fraction: float | None = None
    if (
        requested_min is not None
        and requested_max is not None
        and record_min is not None
        and record_max is not None
        and requested_max > requested_min
    ):
        overlap_lo = max(record_min, requested_min)
        overlap_hi = min(record_max, requested_max)
        overlap = max(0.0, overlap_hi - overlap_lo)
        overlap_fraction = overlap / (requested_max - requested_min)

    return TemperatureCoverage(
        requested_min_k=requested_min,
        requested_max_k=requested_max,
        record_min_k=record_min,
        record_max_k=record_max,
        covers_requested_range=covers,
        overlap_fraction=overlap_fraction,
        extrapolation_distance_k=extrapolation,
    )


# ---------------------------------------------------------------------------
# Misc small helpers
# ---------------------------------------------------------------------------


def slice_for_pagination(
    items: list,
    *,
    offset: int,
    limit: int,
) -> list:
    """Apply ``offset`` and ``limit`` to the ordered list. Every matching record is in the list; none is picked."""
    return items[offset : offset + limit]


# ---------------------------------------------------------------------------
# Public-ref bulk loaders (Phase B)
# ---------------------------------------------------------------------------


def refs_for_ids(
    session: Session, model_cls, ids: Iterable[int]
) -> dict[int, str]:
    """Bulk-load ``{id: public_ref}`` for ``model_cls`` over the given ids.

    Always single SQL round-trip; returns an empty dict for empty input.
    Used by Phase B service builders to populate ``*_ref`` sibling fields
    without an N+1 query per record.
    """
    id_list = [i for i in ids if i is not None]
    if not id_list:
        return {}
    rows = session.execute(
        select(model_cls.id, model_cls.public_ref).where(
            model_cls.id.in_(id_list)
        )
    ).all()
    return {row.id: row.public_ref for row in rows}


def ids_below_review_floor(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: Iterable[int],
) -> set[int]:
    """Which of ``record_ids`` the active read profile's review floor hides from a LIST.

    The record floor only: since step 7 of ADR 0023 the detail-by-ref gate (``handles.py``) no longer mirrors it, and answers
    404 for the withheld set alone. For the RECORD floor on a count or a matching filter (owner decision D5): a count of
    records must not include a record the profile hides. A count or level of a returned record's linked EVIDENCE uses the
    evidence rule instead (``ids_withheld_as_evidence``, ADR 0023). It is not a ref rule: refs are withheld at the
    response boundary by ``ref_guard``. Without a review floor (exploratory, or outside a scientific request) nothing is
    hidden and no query is made. Otherwise one statement.
    """
    ids = set(record_ids)
    if not ids or current_read_profile().review_floor is None:
        return set()
    from app.services.scientific_read.handles import status_hidden_under_profile

    badges = fetch_review_badges(session, record_type=record_type, record_ids=ids)
    return {rid for rid, b in badges.items() if status_hidden_under_profile(b.status)}


def review_floor_predicate(id_column: Any, record_type: SubmissionRecordType, *, profile: Any = None) -> Any:
    """A SQL condition true for the rows of ``record_type`` the active profile's review floor leaves visible.

    ``id_column`` is the column holding the record id (``Calculation.id``, or a foreign key such as
    ``Evidence.reconstruction_calculation_id``). Returns ``None`` when there is no floor (the default profile) or the
    floor hides nothing, so a caller adds no condition and its statement is unchanged.

    ``profile`` defaults to the request's. A streaming caller, which outlives the request context, passes the profile it
    captured while the request was alive.

    The RECORD floor, for a count or flag of records a list would show (owner decision D5) and for matching filters. The
    count or flag of a returned record's linked evidence ("this entry has an IRC", "3 calculations") is
    :func:`linked_evidence_predicate` since step 3 of ADR 0023. A missing review row reads as ``not_reviewed``, as everywhere
    else.
    """
    from app.services.scientific_read.handles import status_hidden_under_profile

    profile = profile if profile is not None else current_read_profile()
    if profile.review_floor is None:
        return None
    hidden = [s for s in RecordReviewStatus if status_hidden_under_profile(s, profile=profile)]
    if not hidden:
        return None

    def review_of(*conditions: Any) -> Any:
        return select(RecordReview.id).where(
            RecordReview.record_type == record_type, RecordReview.record_id == id_column, *conditions
        )

    if status_hidden_under_profile(RecordReviewStatus.not_reviewed, profile=profile):
        return review_of(RecordReview.status.not_in(hidden)).exists()
    return ~review_of(RecordReview.status.in_(hidden)).exists()


def where_visible(stmt: Any, predicate: Any) -> Any:
    """``stmt`` restricted by ``predicate`` (from :func:`review_floor_predicate`); untouched when it is ``None``."""
    return stmt if predicate is None else stmt.where(predicate)


# ---------------------------------------------------------------------------
# The evidence rule (owner decision 2026-10-25, DR/ADR 0023)
# ---------------------------------------------------------------------------
#
# Three things are assigned to three rules under the curated profile. They must not be mixed up.
#
#   RECORD FLOOR   ``review_floor_predicate`` / ``ids_below_review_floor`` / ``status_hidden_under_profile``.
#                  Covers lists, searches and selects: which RECORDS are returned. Under ``curated`` anything below
#                  ``approved`` is not returned.
#
#   DETAIL-BY-REF  ``resolve_path_handle`` -> ``_gated_status`` in ``handles.py``, and the legacy detail reads
#                  (``_legacy_reads.py``). Under ``curated`` they answer 404 for the withheld set below (rejected /
#                  deprecated) only and serve every other record, badged (step 7 of ADR 0023, amending ADR 0006's second
#                  seam). What a served detail embeds (a ``/full`` participant, a supersession successor, the usage a
#                  reference row lists) follows the evidence rule: ``detail_visible_statuses``, ``shown_predicate``.
#
#   EVIDENCE RULE  this section. Covers LINKED EVIDENCE of a returned record (source calculations, levels derived from
#                  them, conformer observations inside a group, evidence counts, nested refs). Under ``curated`` such
#                  evidence is shown with its review badge, except ``rejected`` and ``deprecated`` evidence, which is
#                  withheld ("absent" semantics). A missing review row reads as ``not_reviewed`` and is visible.
#
# The evidence rule withholds the same statuses the default (exploratory) profile hides by default; unlike the default it
# cannot be lifted by request. Under exploratory nothing is withheld as evidence, so every function here answers
# "visible" and issues no query.
#
# STATUS (steps 2 to 7 of ADR 0023): matching in search, browse and select (``linked_evidence_predicate`` and
# ``ids_withheld_as_evidence`` in every filter on linked calculations and observations), the service counts, flags and derived
# levels of a returned record, the response-level ref guard (``ref_guard.py``: pointers in responses, the document modes, the
# selection loaders' unavailable-evidence seam, the export batcher), the legacy entity reads, the ML export's conformer refs,
# the trust evaluators (``trust_floor.py``) and the selection loaders' own evidence populations (``evidence_usable_statuses``
# and ``evidence_visible_statuses`` in the structure, statmech, network and relative-energy loaders and the source-finding
# gate) call this section. Named exception: the ML export's energy rows keep their calculations approved-only (owner decision
# Q7, ``ml_dataset.ml_energy_row_floor``), and the made-under comparisons (step 6: a stored stamp is compared with a
# profile-free recomputation, so the answer is the same under every profile; ``public_assessments._reproducibility_summary``).
# Step 7: detail-by-ref (``resolve_path_handle``, the legacy detail reads) answers 404 for the withheld set only, and the
# records a served detail embeds follow this rule. Nothing is still on the earlier rule (D6): the record floor decides lists,
# searches and selects, and every other record a read serves or names, that is not withheld, is shown badged.

#: Review statuses whose linked evidence is withheld under a profile with a review floor.
WITHHELD_EVIDENCE_STATUSES: frozenset[RecordReviewStatus] = frozenset(
    {RecordReviewStatus.rejected, RecordReviewStatus.deprecated}
)


def evidence_withheld_under_profile(status: RecordReviewStatus, *, profile: Any = None) -> bool:
    """Whether linked evidence at ``status`` is withheld by the evidence rule under the profile.

    The evidence-rule counterpart of ``status_hidden_under_profile`` (the record floor). Without a review floor
    (exploratory, or outside a scientific request) nothing is withheld. ``profile`` defaults to the request's; a
    caller that outlives the request passes the one it captured.
    """
    profile = profile if profile is not None else current_read_profile()
    if profile.review_floor is None:
        return False
    return status in WITHHELD_EVIDENCE_STATUSES


def linked_evidence_withheld_values(*, profile: Any = None) -> list[str]:
    """The review statuses, as sorted tokens, whose linked evidence the profile withholds (``[]`` without a floor).

    What a recorded selection manifest says about the evidence rule it was made under, so a reader of the manifest does not
    have to know which release wrote it.
    """
    profile = profile if profile is not None else current_read_profile()
    return sorted(s.value for s in RecordReviewStatus if evidence_withheld_under_profile(s, profile=profile))


def read_profile_block(*, profile: Any = None) -> dict[str, Any]:
    """The ``read_profile`` block of a selection manifest: the profile, its review floor, and the evidence rule it applied.

    ``linked_evidence_withheld`` is what ADR 0023 adds in manifest format 4: the review statuses whose linked evidence the
    profile withheld (``["deprecated", "rejected"]`` under ``curated``, ``[]`` otherwise).
    """
    profile = profile if profile is not None else current_read_profile()
    floor = profile.review_floor
    return {
        "profile": profile.profile.value,
        "review_floor": floor.value if floor is not None else None,
        "linked_evidence_withheld": linked_evidence_withheld_values(profile=profile),
    }


def check_read_profile_block(block: Any) -> str | None:
    """Why a recorded ``read_profile`` block is not one this release writes, or ``None``.

    The block names the profile and its floor; the evidence rule it records must be the one that profile applies, so a
    manifest relabelled by hand (or recorded under another rule) is refused rather than read as this one.
    """
    if not isinstance(block, dict):
        return "the manifest records no read profile"
    recorded = block.get("linked_evidence_withheld")
    expected = ["deprecated", "rejected"] if block.get("review_floor") is not None else []
    if recorded != expected:
        return f"the recorded linked-evidence rule {recorded!r} is not what the recorded read profile applies ({expected!r})"
    return None


def evidence_visible_statuses(*, profile: Any = None) -> frozenset[RecordReviewStatus]:
    """The review statuses at which linked evidence is shown (badged) under the profile."""
    profile = profile if profile is not None else current_read_profile()
    return frozenset(s for s in RecordReviewStatus if not evidence_withheld_under_profile(s, profile=profile))


def detail_visible_statuses(
    *,
    min_review_status: RecordReviewStatus | None,
    include_rejected: bool,
    include_deprecated: bool,
) -> set[RecordReviewStatus]:
    """The review statuses a DETAIL read shows of the records it embeds (a ``/full`` participant, a conformer group).

    Under ``curated`` a served detail shows every embedded record that is not in the withheld set (``rejected``,
    ``deprecated``), badged (ADR 0023 step 7, owner decision Q9): the approved floor decides which records a list returns,
    not what a detail page rests on. The caller's own ``min_review_status`` still narrows, and the ``include_*`` opt-ins stay
    overridden. Without a floor (exploratory) this is :func:`visible_statuses`, so the default behaviour is unchanged.
    """
    if current_read_profile().review_floor is None:
        return visible_statuses(
            min_review_status=min_review_status,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
        )
    return set(evidence_usable_statuses(min_review_status))


def evidence_usable_statuses(min_review_status: RecordReviewStatus | None = None) -> frozenset[RecordReviewStatus]:
    """The review statuses at which a selection engine USES linked evidence (a source calculation, a member, an observation).

    The evidence-rule counterpart of ``visible_statuses(min_review_status=..., include_rejected=False,
    include_deprecated=False)``: the same set without the profile's record floor, because the floor decides which RECORDS a
    select returns, not what a returned record rests on (ADR 0023). Terminal statuses are out; the caller's own
    ``min_review_status`` still narrows. It does not read the request's profile, so it is the same pure function of the
    caller's floor under every profile; under ``exploratory`` it equals ``visible_statuses(...)``, so an exploratory
    selection is unchanged. A replay judges a recorded manifest by this function, not by the process's own profile.
    """
    base = default_visible_statuses(include_rejected=False, include_deprecated=False)
    if min_review_status is not None:
        base = base & status_at_or_above(min_review_status)
    return frozenset(base)


def shown_predicate(id_column: Any, record_type: SubmissionRecordType, *, detail: bool, profile: Any = None) -> Any:
    """The SQL condition for the records of ``record_type`` a read shows, by what kind of read it is.

    A LIST (a search row, a count of what a list would show) keeps the approved record floor
    (:func:`review_floor_predicate`). A served DETAIL embeds its records as linked evidence, so under ``curated`` it shows
    everything but the withheld set, badged (:func:`linked_evidence_predicate`, ADR 0023 step 7). ``None`` when the profile
    has no floor, so an exploratory statement is unchanged either way.
    """
    if detail:
        return linked_evidence_predicate(id_column, record_type, profile=profile)
    return review_floor_predicate(id_column, record_type, profile=profile)


def evidence_withheld_predicate(id_column: Any, record_type: SubmissionRecordType, *, profile: Any = None) -> Any:
    """A SQL condition true for the rows of ``record_type`` the evidence rule withholds under the profile.

    The positive form of :func:`linked_evidence_predicate`, for a statement that wants to read the verdict beside the row
    (``SELECT row, <this>``) instead of filtering by it, so a builder that already queries per record learns which of its
    links are withheld without another statement. ``None`` when the profile has no floor (nothing is withheld).
    """
    profile = profile if profile is not None else current_read_profile()
    if profile.review_floor is None:
        return None
    return (
        select(RecordReview.id)
        .where(
            RecordReview.record_type == record_type,
            RecordReview.record_id == id_column,
            RecordReview.status.in_(WITHHELD_EVIDENCE_STATUSES),
        )
        .exists()
    )


def linked_evidence_predicate(id_column: Any, record_type: SubmissionRecordType, *, profile: Any = None) -> Any:
    """A SQL condition true for the rows of ``record_type`` whose evidence the profile leaves visible.

    ``id_column`` holds the evidence record id, as for :func:`review_floor_predicate`. The condition is "no review
    row of ``rejected`` or ``deprecated`` status exists", so a record with no review row is visible. Returns ``None``
    when the profile has no floor (exploratory), so a caller adds no condition and its statement is unchanged.
    """
    withheld = evidence_withheld_predicate(id_column, record_type, profile=profile)
    return None if withheld is None else ~withheld


def ids_withheld_as_evidence(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: Iterable[int],
    profile: Any = None,
) -> set[int]:
    """Which of ``record_ids`` the evidence rule withholds under the profile.

    No floor, or no ids: nothing is withheld and no query is made. Otherwise one statement.
    """
    ids = set(record_ids)
    profile = profile if profile is not None else current_read_profile()
    if not ids or profile.review_floor is None:
        return set()
    rows = session.scalars(
        select(RecordReview.record_id).where(
            RecordReview.record_type == record_type,
            RecordReview.record_id.in_(ids),
            RecordReview.status.in_(WITHHELD_EVIDENCE_STATUSES),
        )
    )
    return set(rows)
