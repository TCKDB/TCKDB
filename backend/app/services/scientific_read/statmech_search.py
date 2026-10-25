"""Service implementation for /api/v1/scientific/statmech/search.

Records reuse :class:`ScientificStatmechRecord` from the detail
endpoint so search and detail return identical per-record payloads
for the same include set.

Composition mirrors the conformer / TS / calculation search services:

1. Validate include / sort / pagination via shared helpers.
2. Reject empty-filter request with 422 ``missing_filter`` (explicit
   ``False`` counts as meaningful — see the conformer / TS fix
   precedent; only ``None`` skips).
3. Resolve owner/parent refs to integer ids (422 on malformed /
   wrong-prefix; empty short-circuit on unknown refs).
4. Build the candidate SQL query joining ``statmech`` to its
   species/source-calc/torsion tables.
5. Bulk-load review badges; apply the visible-statuses gate.
6. Sort deterministically (review rank → created_at desc → id desc).
7. Slice for pagination, then materialize each page row via the
   shared :func:`build_statmech_record` helper.

See ``backend/docs/specs/scientific_statmech_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewStatus,
    StatmechCalculationRole,
    StatmechModelScopeKind,
    SubmissionRecordType,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
)
from app.db.models.statmech_declaration import StatmechModelDeclaration
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.reads.scientific_common import (
    RecordReviewBadge,
    SelectionPolicy,
    browse_order,
    simple_selection_sort_key,
)
from app.schemas.reads.scientific_statmech import (
    ScientificStatmechRecord,
)
from app.schemas.reads.scientific_statmech_search import (
    RequestEcho,
    ScientificStatmechSearchResponse,
    StatmechSearchRequest,
)
from app.services.scientific_read.calculation_provenance_filters import apply_calculation_provenance_filter
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    linked_evidence_predicate,
    reject_client_sort,
    review_summary,
    review_visibility_echo,
    validate_includes,
    validate_pagination,
    visible_statuses,
    where_visible,
)
from app.services.scientific_read.handles import resolve_filter_ref
from app.services.scientific_read.hidden_by_default import (
    hidden_by_default_from_statuses,
    record_hidden_by_default,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.keyset import Paging
from app.services.scientific_read.statmech import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    STATMECH_TRUST_EAGER_LOADS,
    build_statmech_record,
)
from app.services.scientific_read.supersession import fetch_supersession_notices

_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "species_ref",
    "species_entry_ref",
    "statmech_ref",
    "conformer_group_ref",
    "conformer_observation_ref",
    "transition_state_entry_ref",
    "declared_conformer_group_ref",
    "model_kind",
    "scientific_origin",
    "has_source_calculations",
    "has_freq_calculation",
    "has_rotor_scans",
    "has_torsions",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
)


_DEFAULT_SORT_ECHO = browse_order("review_rank", "created_at", "id")


def search_statmech(
    session: Session, request: StatmechSearchRequest
) -> ScientificStatmechSearchResponse:
    """Multi-axis statmech search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    paging = Paging("/scientific/statmech/search", request, offset=offset, limit=limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/statmech/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    # --- ref resolution -----------------------------------------------------
    species_id, short_circuit = _resolve_filter_ref(
        session, Species, request.species_ref, "species"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit, _visible(request))

    species_entry_id, short_circuit = _resolve_filter_ref(
        session, SpeciesEntry, request.species_entry_ref, "species_entry"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit, _visible(request))

    statmech_id, short_circuit = _resolve_filter_ref(
        session, Statmech, request.statmech_ref, "statmech"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit, _visible(request))

    conformer_group_id, short_circuit = _resolve_filter_ref(
        session,
        ConformerGroup,
        request.conformer_group_ref,
        "conformer_group",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit, _visible(request))

    conformer_observation_id, short_circuit = _resolve_filter_ref(
        session,
        ConformerObservation,
        request.conformer_observation_ref,
        "conformer_observation",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit, _visible(request))

    transition_state_entry_id, short_circuit = _resolve_filter_ref(
        session,
        TransitionStateEntry,
        request.transition_state_entry_ref,
        "transition_state_entry",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit, _visible(request))

    declared_conformer_group_id, short_circuit = _resolve_filter_ref(
        session,
        ConformerGroup,
        request.declared_conformer_group_ref,
        "conformer_group",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit, _visible(request))

    # --- candidate query ----------------------------------------------------
    stmt = select(Statmech.id, Statmech.created_at)
    stmt = _apply_identity_filters(
        stmt,
        species_id=species_id,
        species_entry_id=species_entry_id,
        statmech_id=statmech_id,
        conformer_group_id=conformer_group_id,
        conformer_observation_id=conformer_observation_id,
        transition_state_entry_id=transition_state_entry_id,
        declared_conformer_group_id=declared_conformer_group_id,
    )
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit, _visible(request))

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.statmech,
        record_ids=candidate_ids,
    )
    visible = _visible(request)
    record_hidden_by_default(
        hidden_by_default_from_statuses(
            (badges[cid].status for cid in candidate_ids),
            include_rejected=request.include_rejected,
            include_deprecated=request.include_deprecated,
            min_review_status=request.min_review_status,
        )
    )
    visible_ids = [cid for cid in candidate_ids if badges[cid].status in visible]
    if not visible_ids:
        return _empty_response(request, includes, offset, limit, _visible(request))

    visible_ids = paging.snapshot(visible_ids, id_of=lambda cid: cid)
    summary = review_summary(badges[cid] for cid in visible_ids)

    # Same key function as the species read: review rank, then newest, then id.
    review_status_by_id = {cid: badges[cid].status for cid in visible_ids}
    page = paging.window(
        visible_ids,
        key_of=lambda cid: simple_selection_sort_key(
            cid,
            policy=SelectionPolicy.default,
            review_status_by_id=review_status_by_id,
            created_at_by_id=created_at_by_id,
        ),
    )
    total = page.total
    page_ids = page.items
    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificStatmechSearchResponse(
        request=_request_echo(request, includes, visible),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total, page=page
        ),
        **page.envelope(),
    )


# ---------------------------------------------------------------------------
# Filter rule + ref resolution
# ---------------------------------------------------------------------------


def _enforce_at_least_one_filter(request: StatmechSearchRequest) -> None:
    """Reject requests with no meaningful filter.

    Only ``None`` skips: bool filter fields default to ``None`` and
    explicit ``False`` is a meaningful filter (e.g.
    ``has_torsions=false`` selects statmech rows without torsion
    treatment).
    """
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/statmech/search."
    )


def _resolve_filter_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    if ref is None:
        return None, False
    resolved = resolve_filter_ref(
        session, model_cls, ref, kind_label=kind_label
    )
    if resolved is None:
        return None, True
    return resolved, False


# ---------------------------------------------------------------------------
# WHERE-clause builders
# ---------------------------------------------------------------------------


def _apply_identity_filters(
    stmt,
    *,
    species_id: int | None,
    species_entry_id: int | None,
    statmech_id: int | None,
    conformer_group_id: int | None,
    conformer_observation_id: int | None,
    transition_state_entry_id: int | None,
    declared_conformer_group_id: int | None,
):
    if statmech_id is not None:
        stmt = stmt.where(Statmech.id == statmech_id)
    if species_entry_id is not None:
        stmt = stmt.where(Statmech.species_entry_id == species_entry_id)
    elif species_id is not None:
        stmt = stmt.join(
            SpeciesEntry, SpeciesEntry.id == Statmech.species_entry_id
        ).where(SpeciesEntry.species_id == species_id)
    if conformer_group_id is not None:
        # Statmech does not link to a conformer group directly. Narrow by
        # the species_entry that owns the supplied conformer group.
        stmt = stmt.where(
            Statmech.species_entry_id.in_(
                select(ConformerGroup.species_entry_id).where(
                    ConformerGroup.id == conformer_group_id
                )
            )
        )
    if conformer_observation_id is not None:
        stmt = stmt.where(
            Statmech.species_entry_id.in_(
                select(ConformerGroup.species_entry_id)
                .join(
                    ConformerObservation,
                    ConformerObservation.conformer_group_id == ConformerGroup.id,
                )
                .where(ConformerObservation.id == conformer_observation_id)
            )
        )
    if transition_state_entry_id is not None:
        # Owner filter. ANDs with the species-axis filters above, so a
        # species-owned filter plus a TS filter is an empty page, never an OR.
        stmt = stmt.where(Statmech.transition_state_entry_id == transition_state_entry_id)
    if declared_conformer_group_id is not None:
        # The record's OWN declaration is scoped to exactly this basin
        # (owner decision D3): not owning-entry context, not undeclared legacy
        # rows, not ensemble members.
        stmt = stmt.where(
            Statmech.id.in_(
                select(StatmechModelDeclaration.statmech_id).where(
                    StatmechModelDeclaration.scope_kind == StatmechModelScopeKind.basin,
                    StatmechModelDeclaration.conformer_group_id == declared_conformer_group_id,
                )
            )
        )
    return stmt


def _apply_scalar_filters(stmt, request: StatmechSearchRequest):
    if request.model_kind is not None:
        stmt = stmt.where(Statmech.statmech_treatment == request.model_kind)
    if request.scientific_origin is not None:
        stmt = stmt.where(Statmech.scientific_origin == request.scientific_origin)
    return stmt


def _apply_evidence_filters(stmt, request: StatmechSearchRequest):
    if request.has_source_calculations is not None:
        ex = _shown_source_calculations().exists()
        stmt = stmt.where(ex if request.has_source_calculations else ~ex)
    if request.has_freq_calculation is not None:
        ex = _shown_source_calculations().where(StatmechSourceCalculation.role == StatmechCalculationRole.freq).exists()
        stmt = stmt.where(ex if request.has_freq_calculation else ~ex)
    if request.has_torsions is not None:
        ex = exists().where(StatmechTorsion.statmech_id == Statmech.id)
        stmt = stmt.where(ex if request.has_torsions else ~ex)
    if request.has_rotor_scans is not None:
        # A scan whose calculation the evidence rule withholds (rejected / deprecated under curated) is not a scan.
        scans = select(StatmechTorsion.id).where(
            StatmechTorsion.statmech_id == Statmech.id,
            StatmechTorsion.source_scan_calculation_id.is_not(None),
        )
        ex = where_visible(
            scans, linked_evidence_predicate(StatmechTorsion.source_scan_calculation_id, SubmissionRecordType.calculation)
        ).exists()
        stmt = stmt.where(ex if request.has_rotor_scans else ~ex)
    return stmt


def _apply_method_basis_software_filters(
    stmt, request: StatmechSearchRequest
):
    """Method/basis/software/workflow filters narrow statmech rows to
    those whose source-calculation graph carries a row matching the
    supplied provenance. The match is OR-across-calc: at least one
    linked source calculation the evidence rule leaves visible must match (a rejected or
    deprecated one contributes nothing under curated, ADR 0023).
    """
    sub = _shown_source_calculations()
    return apply_calculation_provenance_filter(stmt, request, sub)


def _shown_source_calculations():
    """``select(Calculation.id)`` over this statmech row's source calculations the active profile shows."""
    sub = (
        select(Calculation.id)
        .join(
            StatmechSourceCalculation,
            StatmechSourceCalculation.calculation_id == Calculation.id,
        )
        .where(StatmechSourceCalculation.statmech_id == Statmech.id)
    )
    return where_visible(sub, linked_evidence_predicate(Calculation.id, SubmissionRecordType.calculation))


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _materialize_records(
    session: Session,
    page_ids: list[int],
    badges: dict[int, RecordReviewBadge],
    includes: set[str],
) -> list[ScientificStatmechRecord]:
    if not page_ids:
        return []
    stmt = select(Statmech).where(Statmech.id.in_(page_ids))
    if "trust" in includes:
        # The trust evaluator walks a 19-entry graph. Loading it here, as
        # options on the page query, costs a fixed number of statements
        # whatever ``limit`` is: ``selectinload`` issues one query per
        # relationship for the whole batch. Letting ``build_statmech_record``
        # trip the lazy loads per row instead would multiply that by the page
        # size, which is the cost the old search vocabulary avoided by
        # refusing the token outright.
        stmt = stmt.options(*STATMECH_TRUST_EAGER_LOADS)
    rows = session.scalars(stmt).all()
    by_id = {r.id: r for r in rows}
    # Correction notices for the page, batched. Two queries whatever the page
    # size — resolving a chain per row would be the N+1 trap this shape
    # exists to avoid.
    supersessions = fetch_supersession_notices(
        session,
        record_type=SubmissionRecordType.statmech,
        record_ids=page_ids,
    )
    out: list[ScientificStatmechRecord] = []
    for cid in page_ids:
        sm = by_id.get(cid)
        if sm is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_statmech_record(
                session,
                sm=sm,
                badge=badges[cid],
                includes=includes,
                supersession=supersessions.get(cid),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Empty + echo helpers
# ---------------------------------------------------------------------------


def _empty_response(
    request: StatmechSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
    visible: set[RecordReviewStatus],
) -> ScientificStatmechSearchResponse:
    return ScientificStatmechSearchResponse(
        request=_request_echo(request, includes, visible),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


def _visible(request: StatmechSearchRequest) -> set[RecordReviewStatus]:
    return visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )


def _request_echo(
    request: StatmechSearchRequest,
    includes: set[str],
    visible: set[RecordReviewStatus],
) -> RequestEcho:
    # ``filter`` keeps what was *requested*; ``review_visibility`` is what was
    # effective (a read-profile floor can override ``include_rejected``).
    return RequestEcho(
        filter=_request_filter_echo(request),
        sort=_DEFAULT_SORT_ECHO,
        include=sorted(includes),
        review_visibility=review_visibility_echo(visible),
    )


def _request_filter_echo(request: StatmechSearchRequest) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in (*_MEANINGFUL_FILTER_FIELDS, "include_rejected", "include_deprecated", "min_review_status"):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = ["search_statmech"]
