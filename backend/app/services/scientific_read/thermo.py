"""Service implementation for /api/v1/scientific/species-entries/{id}/thermo.

See docs/specs/read_api_mvp.md §Endpoint 4.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationSCFStability,
)
from app.db.models.common import (
    SCFStabilityStatus,
    SubmissionRecordType,
    ThermoCalculationRole,
    ValidationStatus,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import SpeciesEntry
from app.db.models.statmech import Statmech
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoPoint,
    ThermoSourceCalculation,
)
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    CalculationEvidenceSummary,
    EvidenceCompletenessBreakdown,
    LevelOfTheorySummary,
    SoftwareReleaseSummary,
)
from app.schemas.reads.scientific_thermo import (
    RequestEcho,
    ScientificSpeciesThermoResponse,
    ThermoModelKindQuery,
    ThermoNASABlock,
    ThermoPointBlock,
    ThermoProvenance,
    ThermoReadRequest,
    ThermoRecord,
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
    "statmech",
    "review",
    "artifacts",
    "all",
}

_DEFAULT_SORT_ECHO = (
    "covers_requested_temperature_range,extrapolation_distance_k,review_rank,"
    "evidence_completeness,created_at,id"
)

# Priority order per Phase 2.3 spec: sp → composite → freq → opt → any.
_LOT_FILTER_ROLE_PRIORITY = (
    ThermoCalculationRole.sp,
    ThermoCalculationRole.composite,
    ThermoCalculationRole.freq,
    ThermoCalculationRole.opt,
)


def get_species_thermo(
    session: Session,
    *,
    species_entry_id: int,
    request: ThermoReadRequest,
) -> ScientificSpeciesThermoResponse:
    """Return thermo records for a species entry, sorted per spec L3.

    The species_entry_id path parameter is strictly ``species_entry.id``;
    ``species.id`` is rejected with 404. Sort: covers_requested_temperature_range
    DESC, extrapolation_distance_k ASC, review_rank ASC, evidence_completeness
    DESC, created_at DESC, id DESC. Client-supplied sort= rejected (v0).

    :raises NotFoundError: 404 when species_entry_id is unknown.
    :raises ValueError: 422 for sort/include/pagination/temperature validation.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/species-entries/{id}/thermo",
    )
    validate_temperature_range(request.temperature_min, request.temperature_max)

    if session.get(SpeciesEntry, species_entry_id) is None:
        raise NotFoundError(
            f"species_entry not found (species_entry_id={species_entry_id})"
        )

    thermo_rows = list(
        session.scalars(
            select(Thermo).where(Thermo.species_entry_id == species_entry_id)
        ).all()
    )
    if not thermo_rows:
        return _empty_response(species_entry_id, request, includes, offset, limit)

    sources_by_thermo = _load_sources(session, [t.id for t in thermo_rows])
    nasa_by_thermo = _load_nasa(session, [t.id for t in thermo_rows])
    points_by_thermo = _load_points(session, [t.id for t in thermo_rows])
    statmech_ids_by_entry = _load_statmech_ids(session, species_entry_id)

    # Determine model_kind per record (nasa | points | scalar) and apply filter.
    classified: list[tuple[Thermo, ThermoModelKindQuery]] = []
    for t in thermo_rows:
        if t.id in nasa_by_thermo:
            kind = ThermoModelKindQuery.nasa
        elif points_by_thermo.get(t.id):
            kind = ThermoModelKindQuery.points
        else:
            kind = ThermoModelKindQuery.scalar
        if request.model_kind is not None and kind != request.model_kind:
            continue
        classified.append((t, kind))
    if not classified:
        return _empty_response(species_entry_id, request, includes, offset, limit)

    # LoT filter applied against primary source calc (per Phase 2.3 spec).
    if request.level_of_theory_id is not None:
        classified = [
            (t, kind)
            for t, kind in classified
            if _primary_lot_id(sources_by_thermo.get(t.id, []))
            == request.level_of_theory_id
        ]
    if not classified:
        return _empty_response(species_entry_id, request, includes, offset, limit)

    # Software filter.
    if request.software is not None:
        kept = []
        for t, kind in classified:
            sw_name = _primary_software_name(
                session, sources_by_thermo.get(t.id, [])
            )
            if sw_name == request.software:
                kept.append((t, kind))
        classified = kept
    if not classified:
        return _empty_response(species_entry_id, request, includes, offset, limit)

    # Review badges + visibility.
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.thermo,
        record_ids=[t.id for t, _ in classified],
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    classified = [(t, kind) for t, kind in classified if badges[t.id].status in visible]
    if not classified:
        return _empty_response(species_entry_id, request, includes, offset, limit)

    # Pre-fetch validation/SCF data for ALL relevant source calcs.
    all_source_calc_ids = {
        sc.calculation_id
        for srcs in sources_by_thermo.values()
        for sc in srcs
    }
    geom_vals = _geometry_validations(session, all_source_calc_ids)
    scf_vals = _scf_stabilities(session, all_source_calc_ids)
    calc_meta = _calc_lot_meta(session, all_source_calc_ids)

    records: list[ThermoRecord] = []
    for t, model_kind in classified:
        sources = sources_by_thermo.get(t.id, [])

        # Determine the "effective" temperature range for coverage:
        # - If the record has a NASA block, use its t_low / t_high.
        # - Else use Thermo.tmin_k / Thermo.tmax_k (which may be null too).
        # - Scalar records with no range produce covers=False whenever a
        #   bound was requested (handled by temperature_coverage helper).
        nasa_block = nasa_by_thermo.get(t.id)
        if nasa_block is not None:
            record_min = nasa_block.t_low
            record_max = nasa_block.t_high
        else:
            record_min = t.tmin_k
            record_max = t.tmax_k

        coverage = temperature_coverage(
            requested_min=request.temperature_min,
            requested_max=request.temperature_max,
            record_min=record_min,
            record_max=record_max,
        )

        # Evidence completeness (thermo flavor, max=8).
        evidence = _evidence_breakdown(
            thermo=t,
            sources=sources,
            statmech_ids_for_entry=statmech_ids_by_entry,
            nasa_present=nasa_block is not None,
            points_count=len(points_by_thermo.get(t.id, [])),
            geom_vals=geom_vals,
            scf_vals=scf_vals,
        )

        # Build provenance.
        provenance = _build_provenance(
            sources=sources,
            calc_meta=calc_meta,
            statmech_ids=statmech_ids_by_entry,
        )

        record = ThermoRecord(
            thermo_id=t.id,
            scientific_origin=t.scientific_origin,
            model_kind=model_kind,
            review=badges[t.id],
            h298_kj_mol=t.h298_kj_mol,
            s298_j_mol_k=t.s298_j_mol_k,
            h298_uncertainty_kj_mol=t.h298_uncertainty_kj_mol,
            s298_uncertainty_j_mol_k=t.s298_uncertainty_j_mol_k,
            nasa=_build_nasa_block(nasa_block) if nasa_block is not None else None,
            points=(
                [
                    ThermoPointBlock(
                        temperature_k=p.temperature_k,
                        cp_j_mol_k=p.cp_j_mol_k,
                        h_kj_mol=p.h_kj_mol,
                        s_j_mol_k=p.s_j_mol_k,
                        g_kj_mol=p.g_kj_mol,
                    )
                    for p in points_by_thermo.get(t.id, [])
                ]
                if model_kind == ThermoModelKindQuery.points
                else None
            ),
            temperature_coverage=coverage,
            evidence_completeness=evidence,
            provenance=provenance,
        )
        records.append(record)

    summary = review_summary(badges[t.id] for t, _ in classified)

    # L3 thermo sort.
    created_at = {t.id: t.created_at for t, _ in classified}

    def sort_key(rec: ThermoRecord) -> tuple:
        cov = rec.temperature_coverage
        return (
            -int(cov.covers_requested_range) if cov is not None else 0,
            cov.extrapolation_distance_k if cov is not None else 0.0,
            REVIEW_RANK[rec.review.status],
            -rec.evidence_completeness.score,
            -created_at[rec.thermo_id].timestamp(),
            -rec.thermo_id,
        )

    records.sort(key=sort_key)

    pre_collapse_total = len(records)
    collapse_first = request.collapse.value == "first"
    if collapse_first:
        returned = records[:1]
    else:
        returned = records[offset : offset + limit]

    return ScientificSpeciesThermoResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        species_entry_id=species_entry_id,
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
# Loaders
# ---------------------------------------------------------------------------


def _load_sources(
    session: Session, thermo_ids: list[int]
) -> dict[int, list[ThermoSourceCalculation]]:
    if not thermo_ids:
        return {}
    rows = session.scalars(
        select(ThermoSourceCalculation).where(
            ThermoSourceCalculation.thermo_id.in_(thermo_ids)
        )
    ).all()
    grouped: dict[int, list[ThermoSourceCalculation]] = {tid: [] for tid in thermo_ids}
    for sc in rows:
        grouped[sc.thermo_id].append(sc)
    return grouped


def _load_nasa(
    session: Session, thermo_ids: list[int]
) -> dict[int, ThermoNASA]:
    if not thermo_ids:
        return {}
    rows = session.scalars(
        select(ThermoNASA).where(ThermoNASA.thermo_id.in_(thermo_ids))
    ).all()
    return {n.thermo_id: n for n in rows}


def _load_points(
    session: Session, thermo_ids: list[int]
) -> dict[int, list[ThermoPoint]]:
    if not thermo_ids:
        return {}
    rows = session.scalars(
        select(ThermoPoint).where(ThermoPoint.thermo_id.in_(thermo_ids))
    ).all()
    grouped: dict[int, list[ThermoPoint]] = {tid: [] for tid in thermo_ids}
    for p in rows:
        grouped[p.thermo_id].append(p)
    # Already ordered by temperature_k via the ORM relationship default.
    return grouped


def _load_statmech_ids(session: Session, species_entry_id: int) -> set[int]:
    rows = session.scalars(
        select(Statmech.id).where(Statmech.species_entry_id == species_entry_id)
    ).all()
    return set(rows)


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


def _calc_lot_meta(session: Session, calc_ids: set[int]) -> dict[int, dict]:
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            Calculation.id,
            Calculation.type,
            Calculation.lot_id,
            LevelOfTheory.method,
            LevelOfTheory.basis,
            LevelOfTheory.dispersion,
            LevelOfTheory.solvent,
            Calculation.software_release_id,
            Software.name,
            SoftwareRelease.version,
            CalculationGeometryValidation.validation_status,
            CalculationSCFStability.status,
        )
        .join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id, isouter=True)
        .join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
            isouter=True,
        )
        .join(Software, Software.id == SoftwareRelease.software_id, isouter=True)
        .join(
            CalculationGeometryValidation,
            CalculationGeometryValidation.calculation_id == Calculation.id,
            isouter=True,
        )
        .join(
            CalculationSCFStability,
            CalculationSCFStability.calculation_id == Calculation.id,
            isouter=True,
        )
        .where(Calculation.id.in_(calc_ids))
    ).all()
    return {
        row[0]: {
            "type": row[1],
            "lot_id": row[2],
            "lot_method": row[3],
            "lot_basis": row[4],
            "lot_dispersion": row[5],
            "lot_solvent": row[6],
            "software_release_id": row[7],
            "software_name": row[8],
            "software_version": row[9],
            "geometry_validation": row[10],
            "scf_stability": row[11],
        }
        for row in rows
    }


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------


def _primary_calc_id(sources: list[ThermoSourceCalculation]) -> int | None:
    for role in _LOT_FILTER_ROLE_PRIORITY:
        for sc in sources:
            if sc.role == role:
                return sc.calculation_id
    return sources[0].calculation_id if sources else None


def _primary_lot_id(sources: list[ThermoSourceCalculation]) -> int | None:
    primary = _primary_calc_id(sources)
    if primary is None:
        return None
    for sc in sources:
        if sc.calculation_id == primary and sc.calculation is not None:
            return sc.calculation.lot_id
    return None


def _primary_software_name(
    session: Session, sources: list[ThermoSourceCalculation]
) -> str | None:
    primary = _primary_calc_id(sources)
    if primary is None:
        return None
    name = session.scalar(
        select(Software.name)
        .join(SoftwareRelease, SoftwareRelease.software_id == Software.id)
        .join(Calculation, Calculation.software_release_id == SoftwareRelease.id)
        .where(Calculation.id == primary)
    )
    return name


def _build_provenance(
    *,
    sources: list[ThermoSourceCalculation],
    calc_meta: dict[int, dict],
    statmech_ids: set[int],
) -> ThermoProvenance:
    primary_calc_id_v = _primary_calc_id(sources)
    primary_meta = calc_meta.get(primary_calc_id_v) if primary_calc_id_v else None

    primary_calc_summary: CalculationEvidenceSummary | None = None
    primary_lot: LevelOfTheorySummary | None = None
    primary_sw: SoftwareReleaseSummary | None = None

    if primary_meta is not None:
        gv = primary_meta["geometry_validation"]
        scf = primary_meta["scf_stability"]
        primary_calc_summary = CalculationEvidenceSummary(
            calculation_id=primary_calc_id_v,
            calculation_type=primary_meta["type"].value,
            converged=None,
            geometry_validation_status=gv.value if gv is not None else "not_present",
            scf_stability_status=scf.value if scf is not None else "not_present",
            level_of_theory=_lot_summary(primary_meta),
            software=_sw_summary(primary_meta),
        )
        primary_lot = _lot_summary(primary_meta)
        primary_sw = _sw_summary(primary_meta)

    freq_calc_id = _calc_id_for_role(sources, ThermoCalculationRole.freq)
    sp_calc_id = _calc_id_for_role(sources, ThermoCalculationRole.sp)

    statmech_id = next(iter(statmech_ids), None) if statmech_ids else None

    return ThermoProvenance(
        primary_calculation=primary_calc_summary,
        level_of_theory=primary_lot,
        software=primary_sw,
        statmech_id=statmech_id,
        freq_calculation_id=freq_calc_id,
        sp_calculation_id=sp_calc_id,
    )


def _calc_id_for_role(
    sources: list[ThermoSourceCalculation], role: ThermoCalculationRole
) -> int | None:
    for sc in sources:
        if sc.role == role:
            return sc.calculation_id
    return None


def _lot_summary(meta: dict) -> LevelOfTheorySummary | None:
    lot_id = meta["lot_id"]
    if lot_id is None:
        return None
    label_parts = [meta["lot_method"] or ""]
    if meta["lot_basis"]:
        label_parts.append(meta["lot_basis"])
    return LevelOfTheorySummary(
        level_of_theory_id=lot_id,
        method=meta["lot_method"] or "",
        basis=meta["lot_basis"],
        dispersion=meta["lot_dispersion"],
        solvent=meta["lot_solvent"],
        label="/".join(p for p in label_parts if p),
    )


def _sw_summary(meta: dict) -> SoftwareReleaseSummary | None:
    sr_id = meta["software_release_id"]
    if sr_id is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=sr_id,
        software=meta["software_name"] or "",
        version=meta["software_version"],
    )


def _evidence_breakdown(
    *,
    thermo: Thermo,
    sources: list[ThermoSourceCalculation],
    statmech_ids_for_entry: set[int],
    nasa_present: bool,
    points_count: int,
    geom_vals: dict[int, ValidationStatus],
    scf_vals: dict[int, SCFStabilityStatus],
) -> EvidenceCompletenessBreakdown:
    """Thermo L1 checklist, max=8."""
    has_sources = len(sources) > 0
    has_statmech = len(statmech_ids_for_entry) > 0

    source_roles = {sc.role for sc in sources}
    has_freq_evidence = ThermoCalculationRole.freq in source_roles
    has_sp_or_energy_evidence = (
        ThermoCalculationRole.sp in source_roles
        or ThermoCalculationRole.composite in source_roles
    )
    has_temperature_dependent_model = nasa_present or points_count >= 2

    has_uncertainty = any(
        v is not None
        for v in (thermo.h298_uncertainty_kj_mol, thermo.s298_uncertainty_j_mol_k)
    )

    # Geometry validation: target = opt source calc.
    opt_calc_id = _calc_id_for_role(sources, ThermoCalculationRole.opt)
    has_geom_val = False
    if opt_calc_id is not None:
        gv = geom_vals.get(opt_calc_id)
        has_geom_val = gv in {ValidationStatus.passed, ValidationStatus.warning}

    # SCF stability: target = sp source calc.
    sp_calc_id = _calc_id_for_role(sources, ThermoCalculationRole.sp)
    has_scf = False
    if sp_calc_id is not None:
        s = scf_vals.get(sp_calc_id)
        has_scf = s in {SCFStabilityStatus.stable, SCFStabilityStatus.stabilized}

    checklist = {
        "has_source_calculations": has_sources,
        "has_statmech_source": has_statmech,
        "has_frequency_evidence": has_freq_evidence,
        "has_sp_or_energy_evidence": has_sp_or_energy_evidence,
        "has_temperature_dependent_model": has_temperature_dependent_model,
        "has_uncertainty": has_uncertainty,
        "has_geometry_validation": has_geom_val,
        "has_scf_stability": has_scf,
    }
    return EvidenceCompletenessBreakdown(
        score=sum(checklist.values()),
        max=len(checklist),
        checklist=checklist,
    )


def _build_nasa_block(nasa: ThermoNASA) -> ThermoNASABlock:
    return ThermoNASABlock(
        t_low=nasa.t_low,
        t_mid=nasa.t_mid,
        t_high=nasa.t_high,
        low_temperature_coefficients=[
            nasa.a1, nasa.a2, nasa.a3, nasa.a4, nasa.a5, nasa.a6, nasa.a7
        ],
        high_temperature_coefficients=[
            nasa.b1, nasa.b2, nasa.b3, nasa.b4, nasa.b5, nasa.b6, nasa.b7
        ],
    )


# ---------------------------------------------------------------------------
# Echo + empty
# ---------------------------------------------------------------------------


def _filter_echo(request: ThermoReadRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    if request.temperature_min is not None:
        echo["temperature_min"] = request.temperature_min
    if request.temperature_max is not None:
        echo["temperature_max"] = request.temperature_max
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
    species_entry_id: int,
    request: ThermoReadRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificSpeciesThermoResponse:
    return ScientificSpeciesThermoResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        species_entry_id=species_entry_id,
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )
