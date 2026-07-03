"""Service implementation for /api/v1/scientific/conformers/search.

Returns records at the conformer-group grain (one record per
`conformer_group` row that matches the filter set). Shares the
`ScientificConformerGroupRecord` shape with the group detail endpoint
so search and detail callers parse responses with one set of code.

Composition mirrors the calculations / TS search services:

1. Validate include / sort / pagination via shared helpers.
2. Reject empty-filter request with 422 ``missing_filter``.
3. Resolve owner/parent/scheme refs to integer ids (422 on malformed /
   wrong-prefix; empty short-circuit on unknown refs).
4. Build the candidate SQL query joining ``conformer_group`` to its
   observations, selections, calculations, and provenance tables.
5. Bulk-load review badges; apply the visible-statuses gate.
6. Sort deterministically (review rank → created_at desc → id desc).
7. Slice for pagination, then materialize each page row via the
   shared :func:`build_group_record` helper.

See ``backend/docs/specs/scientific_conformer_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationOutputGeometry,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationType,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import (
    ConformerAssignmentScheme,
    ConformerGroup,
    ConformerObservation,
    ConformerSelection,
    Species,
    SpeciesEntry,
)
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_conformer import (
    ScientificConformerGroupRecord,
)
from app.schemas.reads.scientific_conformer_search import (
    ConformersSearchRequest,
    RequestEcho,
    ScientificConformersSearchResponse,
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
from app.services.scientific_read.conformers import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_group_record,
)
from app.services.scientific_read.handles import resolve_filter_ref
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

# Filter knobs that count as "meaningful" for the at-least-one-filter rule.
_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "species_ref",
    "species_entry_ref",
    "conformer_group_ref",
    "conformer_observation_ref",
    "selection_kind",
    "has_selection",
    "assignment_scheme_ref",
    "has_observations",
    "has_calculations",
    "has_geometries",
    "has_opt",
    "has_freq",
    "has_sp",
    "has_geometry_validation",
    "has_scf_stability",
    "scientific_origin",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
)


_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_conformers(
    session: Session, request: ConformersSearchRequest
) -> ScientificConformersSearchResponse:
    """Multi-axis conformer-group search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/conformers/search",
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

    assignment_scheme_id, short_circuit = _resolve_filter_ref(
        session,
        ConformerAssignmentScheme,
        request.assignment_scheme_ref,
        "conformer_assignment_scheme",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    # --- candidate query ----------------------------------------------------
    stmt = select(ConformerGroup.id, ConformerGroup.created_at)
    stmt = _apply_identity_filters(
        stmt,
        species_id=species_id,
        species_entry_id=species_entry_id,
        conformer_group_id=conformer_group_id,
        conformer_observation_id=conformer_observation_id,
    )
    stmt = _apply_selection_filters(
        stmt,
        selection_kind=request.selection_kind,
        has_selection=request.has_selection,
        assignment_scheme_id=assignment_scheme_id,
    )
    stmt = _apply_observation_filters(
        stmt,
        has_observations=request.has_observations,
        scientific_origin=request.scientific_origin,
    )
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}

    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    # --- review filter ------------------------------------------------------
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.conformer_group,
        record_ids=candidate_ids,
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_ids = [
        cid for cid in candidate_ids if badges[cid].status in visible
    ]
    if not visible_ids:
        return _empty_response(request, includes, offset, limit)

    summary = review_summary(badges[cid] for cid in visible_ids)

    # --- deterministic sort -------------------------------------------------
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

    return ScientificConformersSearchResponse(
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


def _enforce_at_least_one_filter(request: ConformersSearchRequest) -> None:
    """Reject requests with no meaningful filter.

    Bool filters default to ``None`` — an explicit ``False`` from the
    caller is a meaningful filter (e.g. ``has_selection=false`` selects
    groups *without* selections), so only ``None`` skips here.
    """
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/conformers/search."
    )


def _resolve_filter_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    """Resolve an optional ``*_ref`` filter to (resolved_id, short_circuit)."""
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
    conformer_group_id: int | None,
    conformer_observation_id: int | None,
):
    if conformer_group_id is not None:
        stmt = stmt.where(ConformerGroup.id == conformer_group_id)
    if species_entry_id is not None:
        stmt = stmt.where(ConformerGroup.species_entry_id == species_entry_id)
    elif species_id is not None:
        # Narrow to groups whose species_entry belongs to the species.
        stmt = stmt.join(
            SpeciesEntry,
            SpeciesEntry.id == ConformerGroup.species_entry_id,
        ).where(SpeciesEntry.species_id == species_id)
    if conformer_observation_id is not None:
        # Match the group that owns this observation.
        stmt = stmt.where(
            exists().where(
                and_(
                    ConformerObservation.conformer_group_id
                    == ConformerGroup.id,
                    ConformerObservation.id == conformer_observation_id,
                )
            )
        )
    return stmt


def _apply_selection_filters(
    stmt,
    *,
    selection_kind,
    has_selection: bool | None,
    assignment_scheme_id: int | None,
):
    if has_selection is not None and selection_kind is None and assignment_scheme_id is None:
        ex = exists().where(
            ConformerSelection.conformer_group_id == ConformerGroup.id
        )
        stmt = stmt.where(ex if has_selection else ~ex)
    if selection_kind is not None or assignment_scheme_id is not None:
        clauses = [
            ConformerSelection.conformer_group_id == ConformerGroup.id,
        ]
        if selection_kind is not None:
            clauses.append(ConformerSelection.selection_kind == selection_kind)
        if assignment_scheme_id is not None:
            clauses.append(
                ConformerSelection.assignment_scheme_id
                == assignment_scheme_id
            )
        ex = exists().where(and_(*clauses))
        # If has_selection is explicitly False alongside kind/scheme it's
        # contradictory; treat it as "must NOT exist" — caller wanted
        # absence of that specific selection.
        if has_selection is False:
            stmt = stmt.where(~ex)
        else:
            stmt = stmt.where(ex)
    return stmt


def _apply_observation_filters(
    stmt,
    *,
    has_observations: bool | None,
    scientific_origin,
):
    if scientific_origin is not None:
        # ``A group matches scientific_origin=X iff it has at least one
        # observation with that origin.''
        stmt = stmt.where(
            exists().where(
                and_(
                    ConformerObservation.conformer_group_id
                    == ConformerGroup.id,
                    ConformerObservation.scientific_origin == scientific_origin,
                )
            )
        )
        # scientific_origin implies observations exist; if the caller
        # also said has_observations=False, that's a contradiction —
        # honor the explicit boolean and let the conjunction be empty.
    if has_observations is not None:
        ex = exists().where(
            ConformerObservation.conformer_group_id == ConformerGroup.id
        )
        stmt = stmt.where(ex if has_observations else ~ex)
    return stmt


def _apply_evidence_filters(stmt, request: ConformersSearchRequest):
    """Evidence filters at the conformer-group grain.

    ``has_calculations`` / ``has_opt`` / ``has_freq`` / ``has_sp`` etc.
    semantics: a group matches iff **at least one** of its
    observations has at least one matching calculation. This mirrors
    the TS search's ``has_*`` semantics at TS-entry grain.
    """
    if request.has_calculations is not None:
        ex = _calc_exists_in_group()
        stmt = stmt.where(ex if request.has_calculations else ~ex)

    type_filters: list[tuple[bool | None, CalculationType]] = [
        (request.has_opt, CalculationType.opt),
        (request.has_freq, CalculationType.freq),
        (request.has_sp, CalculationType.sp),
    ]
    for want, calc_type in type_filters:
        if want is None:
            continue
        ex = _calc_exists_in_group(extra_clause=(Calculation.type == calc_type))
        stmt = stmt.where(ex if want else ~ex)

    if request.has_geometries is not None:
        ex = (
            select(CalculationOutputGeometry.geometry_id)
            .join(
                Calculation,
                Calculation.id == CalculationOutputGeometry.calculation_id,
            )
            .join(
                ConformerObservation,
                ConformerObservation.id == Calculation.conformer_observation_id,
            )
            .where(
                ConformerObservation.conformer_group_id == ConformerGroup.id
            )
            .exists()
        )
        stmt = stmt.where(ex if request.has_geometries else ~ex)

    if request.has_geometry_validation is not None:
        ex = _calc_exists_in_group(
            extra_join=(
                CalculationGeometryValidation,
                CalculationGeometryValidation.calculation_id == Calculation.id,
            ),
        )
        stmt = stmt.where(ex if request.has_geometry_validation else ~ex)

    if request.has_scf_stability is not None:
        ex = _calc_exists_in_group(
            extra_join=(
                CalculationSCFStability,
                CalculationSCFStability.calculation_id == Calculation.id,
            ),
        )
        stmt = stmt.where(ex if request.has_scf_stability else ~ex)

    return stmt


def _calc_exists_in_group(*, extra_clause=None, extra_join=None):
    """Build an EXISTS clause: a calculation reachable via this conformer
    group (via observation linkage), with optional extra filters."""
    sub = (
        select(Calculation.id)
        .join(
            ConformerObservation,
            ConformerObservation.id == Calculation.conformer_observation_id,
        )
        .where(ConformerObservation.conformer_group_id == ConformerGroup.id)
    )
    if extra_join is not None:
        sub = sub.join(*extra_join)
    if extra_clause is not None:
        sub = sub.where(extra_clause)
    return sub.exists()


def _apply_method_basis_software_filters(
    stmt, request: ConformersSearchRequest
):
    """Provenance filters narrow conformer groups to those whose
    calculation evidence includes a row matching the supplied
    method/basis/software/workflow."""
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
            ConformerObservation,
            ConformerObservation.id == Calculation.conformer_observation_id,
        )
        .where(ConformerObservation.conformer_group_id == ConformerGroup.id)
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
            sub = sub.where(SoftwareRelease.version == request.software_version)
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
) -> list[ScientificConformerGroupRecord]:
    if not page_ids:
        return []
    groups = session.scalars(
        select(ConformerGroup).where(ConformerGroup.id.in_(page_ids))
    ).all()
    by_id = {g.id: g for g in groups}
    records: list[ScientificConformerGroupRecord] = []
    for cid in page_ids:
        cg = by_id.get(cid)
        if cg is None:  # pragma: no cover — race with delete
            continue
        records.append(
            build_group_record(
                session,
                cg=cg,
                cg_badge=badges[cid],
                includes=includes,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Empty + echo helpers
# ---------------------------------------------------------------------------


def _empty_response(
    request: ConformersSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificConformersSearchResponse:
    return ScientificConformersSearchResponse(
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


def _request_filter_echo(request: ConformersSearchRequest) -> dict[str, Any]:
    """Return the caller's filter inputs verbatim (post-parse)."""
    out: dict[str, Any] = {}
    for name in (*_MEANINGFUL_FILTER_FIELDS, "include_rejected", "include_deprecated", "min_review_status"):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = [
    "search_conformers",
]
