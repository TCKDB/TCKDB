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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import (
    MAX_LIMIT,
    REVIEW_RANK,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
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
    temperature_min: float | None, temperature_max: float | None
) -> None:
    """Reject temperature_min > temperature_max with a 422."""
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
            "temperature_min must be <= temperature_max",
            context={
                "temperature_min_k": temperature_min,
                "temperature_max_k": temperature_max,
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


def review_rank(badge: RecordReviewBadge) -> int:
    """Return the L2 review_rank integer for a badge (lower = better)."""
    return REVIEW_RANK[badge.status]


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------


def build_pagination(
    *,
    offset: int,
    limit: int,
    returned: int,
    total: int,
    collapse_first: bool = False,
) -> Pagination:
    """Construct the response Pagination block.

    ``total`` is the pre-collapse, post-filter count. The additive
    ``post_collapse_total`` is the count after collapse and before offset/limit
    slicing; it equals ``total`` unless ``collapse=first`` was requested.
    """
    return Pagination(
        offset=offset,
        limit=limit,
        returned=returned,
        total=total,
        post_collapse_total=min(total, 1) if collapse_first else total,
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
    collapse_first: bool,
) -> list:
    """Apply pagination after sort/collapse.

    Collapse is applied first, then ``offset`` and ``limit`` are applied to
    the collapsed or complete list.
    """
    collapsed = items[:1] if collapse_first else items
    return collapsed[offset : offset + limit]


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
