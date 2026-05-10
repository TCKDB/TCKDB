"""Service implementation for /api/v1/scientific/reaction-entries/{id}/kinetics.

See docs/specs/read_api_mvp.md §Endpoint 3.

Provenance keys are always present per Phase 2.2; TS-chain fields are populated
only for TS-backed records. Evidence completeness uses the L1 kinetics
checklist; TS-related predicates are ``False`` (not missing) for non-TS-backed
records and never gate validity.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    PathSearchMethod,
    SCFStabilityStatus,
    SubmissionRecordType,
    ValidationStatus,
)
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.reaction import ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    EvidenceCompletenessBreakdown,
    LevelOfTheorySummary,
    LiteratureSummary,
    PathSearchSummary,
    SCFStabilitySummary,
    SoftwareReleaseSummary,
    ValidationSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_kinetics import (
    ArrheniusParameters,
    KineticsProvenance,
    KineticsReadRequest,
    KineticsRecord,
    KineticsUncertainty,
    RequestEcho,
    ScientificReactionKineticsResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    temperature_coverage,
    validate_includes,
    validate_pagination,
    validate_temperature_range,
    visible_statuses,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "provenance",
    "calculations",
    "transition_states",
    "path_search",
    "irc",
    "review",
    "artifacts",
    "all",
}

_DEFAULT_SORT_ECHO = (
    "covers_requested_range,extrapolation_distance_k,review_rank,"
    "evidence_completeness,created_at,id"
)

# Priority order for resolving the "primary" source calculation per spec.
_LOT_FILTER_ROLE_PRIORITY = (
    KineticsCalculationRole.ts_energy,
    KineticsCalculationRole.fit_source,
    KineticsCalculationRole.freq,
)


def get_reaction_kinetics(
    session: Session,
    *,
    reaction_entry_id: int,
    request: KineticsReadRequest,
) -> ScientificReactionKineticsResponse:
    """Return kinetics records for a reaction entry, sorted per D9.

    Filters apply shallow per D7. Provenance keys are always present, with
    null TS-chain fields for non-TS-backed records (Phase 2.2). TS-related
    evidence checklist items are ``False`` for non-TS-backed records but do
    not gate visibility (the spec D9 sort places ``evidence_completeness``
    behind temperature coverage and review rank).

    :param session: SQLAlchemy session.
    :param reaction_entry_id: ``reaction_entry.id`` (not ``chem_reaction.id``).
    :param request: Parsed request model.
    :returns: ``ScientificReactionKineticsResponse`` Pydantic model.
    :raises NotFoundError: 404 when reaction_entry_id is unknown.
    :raises ValueError: 422 for sort/include/pagination/temperature validation.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/reaction-entries/{id}/kinetics",
    )
    validate_temperature_range(request.temperature_min, request.temperature_max)

    entry = session.get(ReactionEntry, reaction_entry_id)
    if entry is None:
        raise NotFoundError(
            f"reaction_entry not found (reaction_entry_id={reaction_entry_id})"
        )

    # Load kinetics rows for this entry, applying simple column filters.
    stmt = select(Kinetics).where(Kinetics.reaction_entry_id == reaction_entry_id)
    if request.model_kind is not None:
        stmt = stmt.where(Kinetics.model_kind == request.model_kind)
    kinetics_rows: list[Kinetics] = list(session.scalars(stmt).all())

    if not kinetics_rows:
        return _empty_response(reaction_entry_id, request, includes, offset, limit)

    # Bulk-load source calculations and dependent provenance.
    sources_by_kinetics = _load_source_calculations(
        session, [k.id for k in kinetics_rows]
    )

    # Apply level_of_theory_id filter (operates on the primary source calc).
    if request.level_of_theory_id is not None:
        kinetics_rows = [
            k
            for k in kinetics_rows
            if _primary_lot_id(sources_by_kinetics.get(k.id, []))
            == request.level_of_theory_id
        ]
    if not kinetics_rows:
        return _empty_response(reaction_entry_id, request, includes, offset, limit)

    # Software filter — match against primary source calc's software release name.
    if request.software is not None:
        software_name = request.software
        software_id_by_release = _software_id_by_release_id(
            session,
            {
                sc.software_release_id
                for srcs in sources_by_kinetics.values()
                for sc in srcs
                if sc.software_release_id is not None
            },
        )
        kinetics_rows = [
            k
            for k in kinetics_rows
            if _primary_software_name(
                sources_by_kinetics.get(k.id, []),
                software_id_by_release,
                session,
            )
            == software_name
        ]
    if not kinetics_rows:
        return _empty_response(reaction_entry_id, request, includes, offset, limit)

    # Review badges + visibility filtering.
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.kinetics,
        record_ids=[k.id for k in kinetics_rows],
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    kinetics_rows = [k for k in kinetics_rows if badges[k.id].status in visible]
    if not kinetics_rows:
        return _empty_response(reaction_entry_id, request, includes, offset, limit)

    # Bulk-load TS data for the entry to determine has_transition_state and
    # path-search/IRC chain reachability.
    ts_entry_ids = _ts_entry_ids_for_reaction(session, reaction_entry_id)
    ts_calc_types = _ts_calc_types_for_entries(session, ts_entry_ids)

    # Bulk-load source-calculation metadata used by provenance + checklist.
    all_source_calc_ids = {
        sc.calculation_id for srcs in sources_by_kinetics.values() for sc in srcs
    }
    calc_meta = _calc_metadata(session, all_source_calc_ids)
    geometry_validations = _geometry_validations(session, all_source_calc_ids)
    scf_stabilities = _scf_stabilities(session, all_source_calc_ids)

    # Bulk-load lit / software / workflow tool referenced directly by the
    # kinetics rows.
    lit_summaries = _literature_summaries(
        session, {k.literature_id for k in kinetics_rows if k.literature_id}
    )
    sw_summaries = _software_release_summaries(
        session,
        {k.software_release_id for k in kinetics_rows if k.software_release_id},
    )
    wt_summaries = _workflow_tool_release_summaries(
        session,
        {
            k.workflow_tool_release_id
            for k in kinetics_rows
            if k.workflow_tool_release_id
        },
    )

    # Build per-record output.
    records: list[KineticsRecord] = []
    for k in kinetics_rows:
        sources = sources_by_kinetics.get(k.id, [])
        provenance = _build_provenance(
            kinetics=k,
            sources=sources,
            calc_meta=calc_meta,
            geometry_validations=geometry_validations,
            scf_stabilities=scf_stabilities,
            lit_summaries=lit_summaries,
            sw_summaries=sw_summaries,
            wt_summaries=wt_summaries,
        )

        coverage = temperature_coverage(
            requested_min=request.temperature_min,
            requested_max=request.temperature_max,
            record_min=k.tmin_k,
            record_max=k.tmax_k,
        )

        ts_opt_calc_id = provenance.ts_opt_calculation_id
        ts_sp_calc_id = provenance.ts_sp_calculation_id
        ts_freq_calc_id = provenance.ts_freq_calculation_id

        evidence = _evidence_breakdown(
            kinetics=k,
            sources=sources,
            ts_entry_ids=ts_entry_ids,
            ts_calc_types=ts_calc_types,
            geometry_validations=geometry_validations,
            scf_stabilities=scf_stabilities,
            ts_opt_calc_id=ts_opt_calc_id,
            ts_sp_calc_id=ts_sp_calc_id,
        )

        records.append(
            KineticsRecord(
                kinetics_id=k.id,
                scientific_origin=k.scientific_origin,
                model_kind=k.model_kind,
                review=badges[k.id],
                parameters=ArrheniusParameters(
                    A=k.a, A_units=k.a_units, n=k.n, Ea_kj_mol=k.ea_kj_mol
                ),
                tunneling_model=k.tunneling_model,
                uncertainty=KineticsUncertainty(
                    A_uncertainty=k.a_uncertainty,
                    A_uncertainty_kind=k.a_uncertainty_kind,
                    n_uncertainty=k.n_uncertainty,
                    Ea_uncertainty_kj_mol=k.ea_uncertainty_kj_mol,
                ),
                temperature_coverage=coverage,
                evidence_completeness=evidence,
                provenance=provenance,
            )
        )

    summary = review_summary(badges[k.id] for k in kinetics_rows)

    # D9 sort.
    created_at = {k.id: k.created_at for k in kinetics_rows}

    def sort_key(rec: KineticsRecord) -> tuple:
        cov = rec.temperature_coverage
        return (
            -int(cov.covers_requested_range) if cov is not None else 0,
            cov.extrapolation_distance_k if cov is not None else 0.0,
            REVIEW_RANK[rec.review.status],
            -rec.evidence_completeness.score,
            -created_at[rec.kinetics_id].timestamp(),
            -rec.kinetics_id,
        )

    records.sort(key=sort_key)

    pre_collapse_total = len(records)
    collapse_first = request.collapse.value == "first"
    if collapse_first:
        returned = records[:1]
    else:
        returned = records[offset : offset + limit]

    return ScientificReactionKineticsResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        reaction_entry_id=reaction_entry_id,
        review_summary=summary,
        records=returned,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(returned),
            total=pre_collapse_total,
        ),
    )


# ---------------------------------------------------------------------------
# Provenance + checklist
# ---------------------------------------------------------------------------


def _build_provenance(
    *,
    kinetics: Kinetics,
    sources: list[KineticsSourceCalculation],
    calc_meta: dict[int, "_CalcMeta"],
    geometry_validations: dict[int, ValidationStatus],
    scf_stabilities: dict[int, SCFStabilityStatus],
    lit_summaries: dict[int, LiteratureSummary],
    sw_summaries: dict[int, SoftwareReleaseSummary],
    wt_summaries: dict[int, WorkflowToolReleaseSummary],
) -> KineticsProvenance:
    """Assemble a KineticsProvenance — every key always present, ``None`` if absent."""
    by_role: dict[KineticsCalculationRole, list[KineticsSourceCalculation]] = (
        defaultdict(list)
    )
    for sc in sources:
        by_role[sc.role].append(sc)

    ts_opt_calc_id = _first_calc_with_type(by_role, calc_meta, CalculationType.opt)
    ts_freq_calc_id = _first_calc_with_type(
        by_role, calc_meta, CalculationType.freq
    )
    ts_sp_calc_id = _first_calc_with_type(by_role, calc_meta, CalculationType.sp)

    path_search_calc = _first_path_search_calc(by_role, calc_meta)
    path_search_summary: PathSearchSummary | None = None
    if path_search_calc is not None:
        meta = calc_meta[path_search_calc]
        path_search_summary = PathSearchSummary(
            calculation_id=path_search_calc,
            method=_extract_path_method(meta),
            converged=None,  # not stored on Calculation directly
        )

    irc_calc = _first_calc_with_type(by_role, calc_meta, CalculationType.irc)
    irc_summary = (
        {"calculation_id": irc_calc, "type": "irc"} if irc_calc is not None else None
    )

    transition_state_entry_id = _first_ts_entry_id(sources, calc_meta)

    primary_calc_id = _primary_calc_id(sources)
    primary_lot = (
        _lot_summary_for_calc(calc_meta.get(primary_calc_id))
        if primary_calc_id is not None
        else None
    )
    primary_sw = (
        _software_summary_for_calc(calc_meta.get(primary_calc_id))
        if primary_calc_id is not None
        else None
    )

    geometry_validation: ValidationSummary | None = None
    if ts_opt_calc_id is not None:
        status = geometry_validations.get(ts_opt_calc_id)
        if status is not None:
            geometry_validation = ValidationSummary(
                status=status.value, calculation_id=ts_opt_calc_id
            )
        else:
            geometry_validation = ValidationSummary(
                status="not_present", calculation_id=ts_opt_calc_id
            )

    scf_stability: SCFStabilitySummary | None = None
    scf_target = ts_sp_calc_id if ts_sp_calc_id is not None else ts_opt_calc_id
    if scf_target is not None:
        status_scf = scf_stabilities.get(scf_target)
        if status_scf is not None:
            scf_stability = SCFStabilitySummary(
                status=status_scf.value, calculation_id=scf_target
            )
        else:
            scf_stability = SCFStabilitySummary(
                status="not_present", calculation_id=scf_target
            )

    return KineticsProvenance(
        transition_state_entry_id=transition_state_entry_id,
        ts_opt_calculation_id=ts_opt_calc_id,
        ts_freq_calculation_id=ts_freq_calc_id,
        ts_sp_calculation_id=ts_sp_calc_id,
        path_search=path_search_summary,
        irc=irc_summary,
        primary_level_of_theory=primary_lot,
        primary_software=primary_sw,
        geometry_validation=geometry_validation,
        scf_stability=scf_stability,
        literature=lit_summaries.get(kinetics.literature_id) if kinetics.literature_id else None,
        software_release=(
            sw_summaries.get(kinetics.software_release_id)
            if kinetics.software_release_id
            else None
        ),
        workflow_tool_release=(
            wt_summaries.get(kinetics.workflow_tool_release_id)
            if kinetics.workflow_tool_release_id
            else None
        ),
    )


def _evidence_breakdown(
    *,
    kinetics: Kinetics,
    sources: list[KineticsSourceCalculation],
    ts_entry_ids: set[int],
    ts_calc_types: dict[int, set[CalculationType]],
    geometry_validations: dict[int, ValidationStatus],
    scf_stabilities: dict[int, SCFStabilityStatus],
    ts_opt_calc_id: int | None,
    ts_sp_calc_id: int | None,
) -> EvidenceCompletenessBreakdown:
    """L1 kinetics checklist — endpoint-local, additive, max=9."""
    has_sources = len(sources) > 0
    has_ts_entry = bool(ts_entry_ids)

    source_roles = {sc.role for sc in sources}
    reachable_calc_types = set().union(*ts_calc_types.values()) if ts_calc_types else set()

    has_ts_opt = (
        KineticsCalculationRole.ts_energy in source_roles
        or CalculationType.opt in reachable_calc_types
    )
    has_ts_freq = (
        KineticsCalculationRole.freq in source_roles
        or CalculationType.freq in reachable_calc_types
    )
    has_ts_sp = (
        KineticsCalculationRole.ts_energy in source_roles
        or CalculationType.sp in reachable_calc_types
    )
    has_path_or_irc = (
        KineticsCalculationRole.irc in source_roles
        or CalculationType.path_search in reachable_calc_types
        or CalculationType.irc in reachable_calc_types
    )
    has_uncertainty = any(
        v is not None
        for v in (
            kinetics.a_uncertainty,
            kinetics.n_uncertainty,
            kinetics.ea_uncertainty_kj_mol,
        )
    )

    has_geom_val = False
    if ts_opt_calc_id is not None:
        gv = geometry_validations.get(ts_opt_calc_id)
        has_geom_val = gv in {ValidationStatus.passed, ValidationStatus.warning}

    has_scf = False
    scf_target = ts_sp_calc_id if ts_sp_calc_id is not None else ts_opt_calc_id
    if scf_target is not None:
        s = scf_stabilities.get(scf_target)
        has_scf = s in {SCFStabilityStatus.stable, SCFStabilityStatus.stabilized}

    checklist = {
        "has_source_calculations": has_sources,
        "has_transition_state_entry": has_ts_entry,
        "has_ts_opt_evidence": has_ts_opt,
        "has_ts_freq_evidence": has_ts_freq,
        "has_ts_sp_evidence": has_ts_sp,
        "has_path_search_or_irc_evidence": has_path_or_irc,
        "has_uncertainty": has_uncertainty,
        "has_geometry_validation": has_geom_val,
        "has_scf_stability": has_scf,
    }
    return EvidenceCompletenessBreakdown(
        score=sum(checklist.values()),
        max=len(checklist),
        checklist=checklist,
    )


# ---------------------------------------------------------------------------
# Bulk loaders / lookups
# ---------------------------------------------------------------------------


class _CalcMeta:
    """Lightweight container for calculation metadata used in provenance."""

    __slots__ = (
        "id",
        "type",
        "transition_state_entry_id",
        "lot_id",
        "lot_method",
        "lot_basis",
        "lot_dispersion",
        "lot_solvent",
        "software_release_id",
        "software_name",
        "software_version",
        "parameters_json",
    )

    def __init__(
        self,
        *,
        id: int,
        type: CalculationType,
        transition_state_entry_id: int | None,
        lot_id: int | None,
        lot_method: str | None,
        lot_basis: str | None,
        lot_dispersion: str | None,
        lot_solvent: str | None,
        software_release_id: int | None,
        software_name: str | None,
        software_version: str | None,
        parameters_json: dict | None,
    ):
        self.id = id
        self.type = type
        self.transition_state_entry_id = transition_state_entry_id
        self.lot_id = lot_id
        self.lot_method = lot_method
        self.lot_basis = lot_basis
        self.lot_dispersion = lot_dispersion
        self.lot_solvent = lot_solvent
        self.software_release_id = software_release_id
        self.software_name = software_name
        self.software_version = software_version
        self.parameters_json = parameters_json


def _load_source_calculations(
    session: Session, kinetics_ids: list[int]
) -> dict[int, list[KineticsSourceCalculation]]:
    if not kinetics_ids:
        return {}
    rows = session.scalars(
        select(KineticsSourceCalculation).where(
            KineticsSourceCalculation.kinetics_id.in_(kinetics_ids)
        )
    ).all()
    grouped: dict[int, list[KineticsSourceCalculation]] = {kid: [] for kid in kinetics_ids}
    for sc in rows:
        grouped[sc.kinetics_id].append(sc)
    return grouped


def _calc_metadata(
    session: Session, calc_ids: set[int]
) -> dict[int, _CalcMeta]:
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            Calculation.id,
            Calculation.type,
            Calculation.transition_state_entry_id,
            Calculation.lot_id,
            Calculation.parameters_json,
            LevelOfTheory.method,
            LevelOfTheory.basis,
            LevelOfTheory.dispersion,
            LevelOfTheory.solvent,
            Calculation.software_release_id,
            Software.name,
            SoftwareRelease.version,
        )
        .join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id, isouter=True)
        .join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
            isouter=True,
        )
        .join(Software, Software.id == SoftwareRelease.software_id, isouter=True)
        .where(Calculation.id.in_(calc_ids))
    ).all()
    return {
        row[0]: _CalcMeta(
            id=row[0],
            type=row[1],
            transition_state_entry_id=row[2],
            lot_id=row[3],
            parameters_json=row[4],
            lot_method=row[5],
            lot_basis=row[6],
            lot_dispersion=row[7],
            lot_solvent=row[8],
            software_release_id=row[9],
            software_name=row[10],
            software_version=row[11],
        )
        for row in rows
    }


def _geometry_validations(
    session: Session, calc_ids: set[int]
) -> dict[int, ValidationStatus]:
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            CalculationGeometryValidation.calculation_id,
            CalculationGeometryValidation.validation_status,
        ).where(CalculationGeometryValidation.calculation_id.in_(calc_ids))
    ).all()
    return {cid: status for cid, status in rows}


def _scf_stabilities(
    session: Session, calc_ids: set[int]
) -> dict[int, SCFStabilityStatus]:
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            CalculationSCFStability.calculation_id,
            CalculationSCFStability.status,
        ).where(CalculationSCFStability.calculation_id.in_(calc_ids))
    ).all()
    return {cid: status for cid, status in rows}


def _ts_entry_ids_for_reaction(session: Session, reaction_entry_id: int) -> set[int]:
    rows = session.scalars(
        select(TransitionStateEntry.id)
        .join(TransitionState, TransitionState.id == TransitionStateEntry.transition_state_id)
        .where(TransitionState.reaction_entry_id == reaction_entry_id)
    ).all()
    return set(rows)


def _ts_calc_types_for_entries(
    session: Session, ts_entry_ids: set[int]
) -> dict[int, set[CalculationType]]:
    if not ts_entry_ids:
        return {}
    rows = session.execute(
        select(Calculation.transition_state_entry_id, Calculation.type)
        .where(Calculation.transition_state_entry_id.in_(ts_entry_ids))
    ).all()
    grouped: dict[int, set[CalculationType]] = defaultdict(set)
    for ts_entry_id, calc_type in rows:
        grouped[ts_entry_id].add(calc_type)
    return grouped


def _literature_summaries(
    session: Session, lit_ids: set[int]
) -> dict[int, LiteratureSummary]:
    if not lit_ids:
        return {}
    rows = session.scalars(select(Literature).where(Literature.id.in_(lit_ids))).all()
    return {
        lit.id: LiteratureSummary(
            id=lit.id, title=lit.title, year=lit.year, doi=lit.doi
        )
        for lit in rows
    }


def _software_release_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, SoftwareReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            SoftwareRelease.id, Software.name, SoftwareRelease.version
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id.in_(release_ids))
    ).all()
    return {
        rid: SoftwareReleaseSummary(
            software_release_id=rid, software=name, version=version
        )
        for rid, name, version in rows
    }


def _workflow_tool_release_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, WorkflowToolReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowTool.name,
            WorkflowToolRelease.version,
        )
        .join(WorkflowTool, WorkflowTool.id == WorkflowToolRelease.workflow_tool_id)
        .where(WorkflowToolRelease.id.in_(release_ids))
    ).all()
    return {
        rid: WorkflowToolReleaseSummary(
            workflow_tool_release_id=rid,
            workflow_tool=name,
            version=version,
        )
        for rid, name, version in rows
    }


def _software_id_by_release_id(
    session: Session, release_ids: set[int]
) -> dict[int, int]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(SoftwareRelease.id, SoftwareRelease.software_id).where(
            SoftwareRelease.id.in_(release_ids)
        )
    ).all()
    return {rid: sid for rid, sid in rows}


# ---------------------------------------------------------------------------
# Small inference helpers
# ---------------------------------------------------------------------------


def _primary_calc_id(sources: list[KineticsSourceCalculation]) -> int | None:
    for role in _LOT_FILTER_ROLE_PRIORITY:
        for sc in sources:
            if sc.role == role:
                return sc.calculation_id
    if sources:
        return sources[0].calculation_id
    return None


def _primary_lot_id(sources: list[KineticsSourceCalculation]) -> int | None:
    """Return the LoT id the primary source calculation points at, if any."""
    primary = _primary_calc_id(sources)
    if primary is None:
        return None
    return next(
        (sc.calculation.lot_id for sc in sources if sc.calculation_id == primary and sc.calculation is not None),
        None,
    )


def _primary_software_name(
    sources: list[KineticsSourceCalculation],
    software_id_by_release: dict[int, int],
    session: Session,
) -> str | None:
    """Return the software name for the primary source calc, if any."""
    for sc in sources:
        if sc.software_release_id is not None:
            sw_id = software_id_by_release.get(sc.software_release_id)
            if sw_id is not None:
                name = session.scalar(select(Software.name).where(Software.id == sw_id))
                if name is not None:
                    return name
    return None


def _first_calc_with_type(
    by_role: dict[KineticsCalculationRole, list[KineticsSourceCalculation]],
    calc_meta: dict[int, _CalcMeta],
    type_: CalculationType,
) -> int | None:
    for srcs in by_role.values():
        for sc in srcs:
            meta = calc_meta.get(sc.calculation_id)
            if meta is not None and meta.type == type_:
                return sc.calculation_id
    return None


def _first_path_search_calc(
    by_role: dict[KineticsCalculationRole, list[KineticsSourceCalculation]],
    calc_meta: dict[int, _CalcMeta],
) -> int | None:
    return _first_calc_with_type(by_role, calc_meta, CalculationType.path_search)


def _first_ts_entry_id(
    sources: list[KineticsSourceCalculation],
    calc_meta: dict[int, _CalcMeta],
) -> int | None:
    for sc in sources:
        meta = calc_meta.get(sc.calculation_id)
        if meta is not None and meta.transition_state_entry_id is not None:
            return meta.transition_state_entry_id
    return None


def _lot_summary_for_calc(meta: _CalcMeta | None) -> LevelOfTheorySummary | None:
    if meta is None or meta.lot_id is None:
        return None
    label_parts = [meta.lot_method or ""]
    if meta.lot_basis:
        label_parts.append(meta.lot_basis)
    return LevelOfTheorySummary(
        level_of_theory_id=meta.lot_id,
        method=meta.lot_method or "",
        basis=meta.lot_basis,
        dispersion=meta.lot_dispersion,
        solvent=meta.lot_solvent,
        label="/".join(p for p in label_parts if p),
    )


def _software_summary_for_calc(meta: _CalcMeta | None) -> SoftwareReleaseSummary | None:
    if meta is None or meta.software_release_id is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=meta.software_release_id,
        software=meta.software_name or "",
        version=meta.software_version,
    )


def _extract_path_method(meta: _CalcMeta) -> str | None:
    """Best-effort extraction of path-search method from parameters JSON.

    The schema doesn't expose method as a typed column on Calculation; if the
    caller supplied a parameters_json snapshot with a 'method' key, return it.
    """
    if meta.parameters_json is None:
        return None
    method = meta.parameters_json.get("method")
    return method if isinstance(method, str) else None


# ---------------------------------------------------------------------------
# Echo + empty response
# ---------------------------------------------------------------------------


def _filter_echo(request: KineticsReadRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    if request.temperature_min is not None:
        echo["temperature_min"] = request.temperature_min
    if request.temperature_max is not None:
        echo["temperature_max"] = request.temperature_max
    if request.pressure is not None:
        echo["pressure"] = request.pressure
    if request.model_kind is not None:
        echo["model_kind"] = request.model_kind.value
    if request.level_of_theory_id is not None:
        echo["level_of_theory_id"] = request.level_of_theory_id
    if request.software is not None:
        echo["software"] = request.software
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_response(
    reaction_entry_id: int,
    request: KineticsReadRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificReactionKineticsResponse:
    return ScientificReactionKineticsResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        reaction_entry_id=reaction_entry_id,
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )
