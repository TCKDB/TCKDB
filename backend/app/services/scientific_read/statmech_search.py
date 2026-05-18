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

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
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
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_statmech import (
    ScientificStatmechRecord,
)
from app.schemas.reads.scientific_statmech_search import (
    RequestEcho,
    ScientificStatmechSearchResponse,
    StatmechSearchRequest,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.handles import resolve_filter_ref
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.statmech import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_statmech_record,
)


_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "species_ref",
    "species_entry_ref",
    "statmech_ref",
    "conformer_group_ref",
    "conformer_observation_ref",
    "model_kind",
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


_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_statmech(
    session: Session, request: StatmechSearchRequest
) -> ScientificStatmechSearchResponse:
    """Multi-axis statmech search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
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
        return _empty_response(request, includes, offset, limit)

    species_entry_id, short_circuit = _resolve_filter_ref(
        session, SpeciesEntry, request.species_entry_ref, "species_entry"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    statmech_id, short_circuit = _resolve_filter_ref(
        session, Statmech, request.statmech_ref, "statmech"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    conformer_group_id, short_circuit = _resolve_filter_ref(
        session,
        ConformerGroup,
        request.conformer_group_ref,
        "conformer_group",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    conformer_observation_id, short_circuit = _resolve_filter_ref(
        session,
        ConformerObservation,
        request.conformer_observation_ref,
        "conformer_observation",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    # --- candidate query ----------------------------------------------------
    stmt = select(Statmech.id, Statmech.created_at)
    stmt = _apply_identity_filters(
        stmt,
        species_id=species_id,
        species_entry_id=species_entry_id,
        statmech_id=statmech_id,
        conformer_group_id=conformer_group_id,
        conformer_observation_id=conformer_observation_id,
    )
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.statmech,
        record_ids=candidate_ids,
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_ids = [cid for cid in candidate_ids if badges[cid].status in visible]
    if not visible_ids:
        return _empty_response(request, includes, offset, limit)

    summary = review_summary(badges[cid] for cid in visible_ids)

    visible_ids.sort(
        key=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            -created_at_by_id[cid].timestamp(),
            -cid,
        )
    )
    total = len(visible_ids)
    page_ids = visible_ids[offset : offset + limit]
    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificStatmechSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total
        ),
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
    return stmt


def _apply_scalar_filters(stmt, request: StatmechSearchRequest):
    if request.model_kind is not None:
        stmt = stmt.where(Statmech.statmech_treatment == request.model_kind)
    return stmt


def _apply_evidence_filters(stmt, request: StatmechSearchRequest):
    if request.has_source_calculations is not None:
        ex = exists().where(
            StatmechSourceCalculation.statmech_id == Statmech.id
        )
        stmt = stmt.where(ex if request.has_source_calculations else ~ex)
    if request.has_freq_calculation is not None:
        ex = exists().where(
            and_(
                StatmechSourceCalculation.statmech_id == Statmech.id,
                StatmechSourceCalculation.role == StatmechCalculationRole.freq,
            )
        )
        stmt = stmt.where(ex if request.has_freq_calculation else ~ex)
    if request.has_torsions is not None:
        ex = exists().where(StatmechTorsion.statmech_id == Statmech.id)
        stmt = stmt.where(ex if request.has_torsions else ~ex)
    if request.has_rotor_scans is not None:
        ex = exists().where(
            and_(
                StatmechTorsion.statmech_id == Statmech.id,
                StatmechTorsion.source_scan_calculation_id.is_not(None),
            )
        )
        stmt = stmt.where(ex if request.has_rotor_scans else ~ex)
    return stmt


def _apply_method_basis_software_filters(
    stmt, request: StatmechSearchRequest
):
    """Method/basis/software/workflow filters narrow statmech rows to
    those whose source-calculation graph carries a row matching the
    supplied provenance. The match is OR-across-calc: at least one
    linked source calculation must match.
    """
    method_or_basis = request.method is not None or request.basis is not None
    sw_filter = (
        request.software is not None or request.software_version is not None
    )
    wf_filter = (
        request.workflow_tool is not None
        or request.workflow_tool_version is not None
    )
    if not (method_or_basis or sw_filter or wf_filter):
        return stmt

    sub = (
        select(Calculation.id)
        .join(
            StatmechSourceCalculation,
            StatmechSourceCalculation.calculation_id == Calculation.id,
        )
        .where(StatmechSourceCalculation.statmech_id == Statmech.id)
    )
    if method_or_basis:
        sub = sub.join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        if request.method is not None:
            sub = sub.where(LevelOfTheory.method == request.method)
        if request.basis is not None:
            sub = sub.where(LevelOfTheory.basis == request.basis)
    if sw_filter:
        sub = sub.join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
        ).join(Software, Software.id == SoftwareRelease.software_id)
        if request.software is not None:
            sub = sub.where(Software.name == request.software)
        if request.software_version is not None:
            sub = sub.where(
                SoftwareRelease.version == request.software_version
            )
    if wf_filter:
        sub = sub.join(
            WorkflowToolRelease,
            WorkflowToolRelease.id == Calculation.workflow_tool_release_id,
        ).join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        if request.workflow_tool is not None:
            sub = sub.where(WorkflowTool.name == request.workflow_tool)
        if request.workflow_tool_version is not None:
            sub = sub.where(
                WorkflowToolRelease.version == request.workflow_tool_version
            )
    return stmt.where(sub.exists())


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
    rows = session.scalars(
        select(Statmech).where(Statmech.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
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
) -> ScientificStatmechSearchResponse:
    return ScientificStatmechSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


def _request_filter_echo(request: StatmechSearchRequest) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _MEANINGFUL_FILTER_FIELDS + (
        "include_rejected",
        "include_deprecated",
        "min_review_status",
    ):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = ["search_statmech"]
