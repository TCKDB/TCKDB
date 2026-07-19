"""Service implementation for /api/v1/scientific/species-entries/{id}/thermo.

See docs/specs/read_api_mvp.md §Endpoint 4.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationSCFStability,
)
from app.db.models.common import (
    RecordReviewStatus,
    SCFStabilityStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
    ThermoCalculationRole,
    ThermoModelKind,
    ValidationStatus,
)
from app.db.models.group_additivity import (
    AppliedGroupAdditivity,
    GroupAdditivityScheme,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import SpeciesEntry
from app.db.models.statmech import Statmech, StatmechSourceCalculation
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoNASA9Interval,
    ThermoPoint,
    ThermoSourceCalculation,
    ThermoWilhoit,
)
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    CalculationEvidenceSummary,
    EvidenceCompletenessBreakdown,
    LevelOfTheorySummary,
    SelectionPolicy,
    SoftwareReleaseSummary,
    simple_selection_sort_key,
)
from app.schemas.reads.scientific_thermo import (
    GroupAdditivityBlock,
    GroupAdditivityComponentBlock,
    RequestEcho,
    ScientificSpeciesThermoResponse,
    ThermoModelKindQuery,
    ThermoNASA9IntervalBlock,
    ThermoNASABlock,
    ThermoPointBlock,
    ThermoProvenance,
    ThermoReadRequest,
    ThermoRecord,
    ThermoWilhoitBlock,
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
from app.services.scientific_read.handles import (
    NO_MATCH,
    reconcile_level_of_theory_pair,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.trust import (
    TrustFragment,
    build_trust_fragment,
    evaluate_loaded_thermo,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "provenance",
    "calculations",
    "statmech",
    "review",
    "artifacts",
    "internal_ids",
    "all",
    "trust",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}
_TRUST_EAGER_LOADS = (
    selectinload(Thermo.species_entry),
    selectinload(Thermo.nasa),
    selectinload(Thermo.points),
    selectinload(Thermo.nasa9_intervals),
    selectinload(Thermo.wilhoit),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.lot),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.software_release),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.workflow_tool_release),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.artifacts),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.sp_result),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.opt_result),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.freq_result),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.scan_result),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.irc_result),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.path_search_result),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.geometry_validation),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.scf_stability),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.input_geometries),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.output_geometries),
    selectinload(Thermo.source_calculations)
    .selectinload(ThermoSourceCalculation.calculation)
    .selectinload(Calculation.child_dependencies),
)

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
        internal_tokens=_INTERNAL_INCLUDE_TOKENS | {"trust"},
    )
    includes = filter_internal_ids_from_resolved(includes)
    validate_temperature_range(request.temperature_min, request.temperature_max)

    species_entry = session.get(SpeciesEntry, species_entry_id)
    if species_entry is None:
        raise NotFoundError(
            f"species_entry not found (species_entry_id={species_entry_id})"
        )
    species_entry_ref = species_entry.public_ref

    # Phase C: reconcile level_of_theory_id + level_of_theory_ref into a
    # single integer id. Unknown ref returns NO_MATCH → empty response.
    lot_id_or_match = reconcile_level_of_theory_pair(
        session,
        id_value=request.level_of_theory_id,
        ref_value=request.level_of_theory_ref,
    )
    if lot_id_or_match is NO_MATCH:
        return _empty_response(
            species_entry_id, species_entry_ref, request, includes, offset, limit
        )
    effective_lot_id: int | None = lot_id_or_match  # type: ignore[assignment]

    stmt = select(Thermo).where(Thermo.species_entry_id == species_entry_id)
    if "trust" in includes:
        stmt = stmt.options(*_TRUST_EAGER_LOADS)
    thermo_rows = list(session.scalars(stmt).all())
    if not thermo_rows:
        return _empty_response(species_entry_id, species_entry_ref, request, includes, offset, limit)

    sources_by_thermo = _load_sources(session, [t.id for t in thermo_rows])
    nasa_by_thermo = _load_nasa(session, [t.id for t in thermo_rows])
    nasa9_by_thermo = _load_nasa9(session, [t.id for t in thermo_rows])
    wilhoit_by_thermo = _load_wilhoit(session, [t.id for t in thermo_rows])
    points_by_thermo = _load_points(session, [t.id for t in thermo_rows])
    ga_by_thermo = _load_group_additivity(session, [t.id for t in thermo_rows])
    statmech_ids_by_entry = _load_statmech_ids(session, species_entry_id)
    statmech_refs = _load_statmech_refs(session, statmech_ids_by_entry)
    # Phase 2 audit (read half, #3b): load source-calc rows for EVERY
    # statmech of the entry so thermo provenance / evidence completeness can
    # borrow the freq / SP / opt calcs from the statmech a record actually
    # derives from. Each thermo record resolves its basis PER RECORD from its
    # own ``thermo.statmech_id`` FK (populated by the write fix); the borrowed
    # source calcs then come from that exact statmech, not an entry-wide pick.
    statmech_sources_by_id: dict[int, list[StatmechSourceCalculation]] = {
        sid: _load_statmech_sources(session, sid) for sid in statmech_ids_by_entry
    }
    # Fallback basis for records whose ``thermo.statmech_id`` is NULL
    # (experimental thermo, or legacy computed rows written before the FK was
    # populated): the lowest statmech id. ``min`` is deterministic and keeps
    # the fallback reproducible. It does not imply the picked statmech is "the"
    # statmech for the entry — it is only consulted when a record has no
    # linked statmech of its own.
    picked_statmech_id = min(statmech_ids_by_entry) if statmech_ids_by_entry else None

    # Determine model_kind per record from the stored thermo.model_kind
    # (falling back to child-row inference for legacy NULL rows) and filter.
    classified: list[tuple[Thermo, ThermoModelKindQuery]] = []
    for t in thermo_rows:
        kind = _classify_model_kind(
            t,
            has_nasa=t.id in nasa_by_thermo,
            has_nasa9=bool(nasa9_by_thermo.get(t.id)),
            has_wilhoit=t.id in wilhoit_by_thermo,
            has_points=bool(points_by_thermo.get(t.id)),
        )
        if request.model_kind is not None and kind != request.model_kind:
            continue
        classified.append((t, kind))
    if not classified:
        return _empty_response(species_entry_id, species_entry_ref, request, includes, offset, limit)

    # LoT filter applied against primary source calc (per Phase 2.3 spec).
    if effective_lot_id is not None:
        classified = [
            (t, kind)
            for t, kind in classified
            if _primary_lot_id(sources_by_thermo.get(t.id, []))
            == effective_lot_id
        ]
    if not classified:
        return _empty_response(species_entry_id, species_entry_ref, request, includes, offset, limit)

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
        return _empty_response(species_entry_id, species_entry_ref, request, includes, offset, limit)

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
        return _empty_response(species_entry_id, species_entry_ref, request, includes, offset, limit)

    # Pre-fetch validation/SCF data for ALL relevant source calcs. Phase 2
    # audit: include statmech-linked source calcs in the lookup set so the
    # fallback inside ``_build_provenance`` / ``_evidence_breakdown`` can
    # render LoT, software, geom-validation, and SCF stability for them.
    all_source_calc_ids = {
        sc.calculation_id
        for srcs in sources_by_thermo.values()
        for sc in srcs
    } | {
        sc.calculation_id
        for srcs in statmech_sources_by_id.values()
        for sc in srcs
    }
    geom_vals = _geometry_validations(session, all_source_calc_ids)
    scf_vals = _scf_stabilities(session, all_source_calc_ids)
    calc_meta = _calc_lot_meta(session, all_source_calc_ids)
    calc_refs = _calc_refs(session, all_source_calc_ids)

    records: list[ThermoRecord] = []
    for t, model_kind in classified:
        sources = sources_by_thermo.get(t.id, [])

        # Per-record statmech resolution: prefer the record's own FK, falling
        # back to the entry-min only when the thermo has no linked statmech.
        record_statmech_id = (
            t.statmech_id if t.statmech_id is not None else picked_statmech_id
        )
        record_statmech_sources = (
            statmech_sources_by_id.get(record_statmech_id, [])
            if record_statmech_id is not None
            else []
        )

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
            statmech_sources=record_statmech_sources,
            nasa_present=nasa_block is not None,
            points_count=len(points_by_thermo.get(t.id, [])),
            has_nasa9=bool(nasa9_by_thermo.get(t.id)),
            has_wilhoit=t.id in wilhoit_by_thermo,
            geom_vals=geom_vals,
            scf_vals=scf_vals,
        )

        # Build provenance.
        provenance = _build_provenance(
            sources=sources,
            calc_meta=calc_meta,
            calc_refs=calc_refs,
            statmech_id=record_statmech_id,
            statmech_refs=statmech_refs,
            statmech_sources=record_statmech_sources,
        )

        record = ThermoRecord(
            thermo_id=t.id,
            thermo_ref=t.public_ref,
            scientific_origin=t.scientific_origin,
            model_kind=model_kind,
            review=badges[t.id],
            h298_kj_mol=t.h298_kj_mol,
            s298_j_mol_k=t.s298_j_mol_k,
            h298_uncertainty_kj_mol=t.h298_uncertainty_kj_mol,
            s298_uncertainty_j_mol_k=t.s298_uncertainty_j_mol_k,
            nasa=_build_nasa_block(nasa_block) if nasa_block is not None else None,
            nasa9=(
                _build_nasa9_blocks(nasa9_by_thermo[t.id])
                if model_kind == ThermoModelKindQuery.nasa9
                and nasa9_by_thermo.get(t.id)
                else None
            ),
            wilhoit=(
                _build_wilhoit_block(wilhoit_by_thermo[t.id])
                if model_kind == ThermoModelKindQuery.wilhoit
                and t.id in wilhoit_by_thermo
                else None
            ),
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
            group_additivity=_build_group_additivity_block(ga_by_thermo.get(t.id)),
            trust=(
                build_thermo_trust_fragment(
                    t,
                    review_status=badges[t.id].status,
                )
                if "trust" in includes
                else None
            ),
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
        if request.selection_policy is SelectionPolicy.default:
            # Standard thermo ranking (sort_key) already applied above.
            returned = records[:1]
        else:
            # Named policy re-ranks the selected record only; the default
            # candidate order (collapse=all) is unaffected.
            review_status_by_id = {
                t.id: badges[t.id].status for t, _ in classified
            }
            ranked = sorted(
                records,
                key=lambda rec: simple_selection_sort_key(
                    rec.thermo_id,
                    policy=request.selection_policy,
                    review_status_by_id=review_status_by_id,
                    created_at_by_id=created_at,
                ),
            )
            returned = ranked[:1]
    else:
        returned = records[offset : offset + limit]

    return ScientificSpeciesThermoResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            selection_policy=request.selection_policy,
            include=sorted(includes),
        ),
        species_entry_id=species_entry_id,
        species_entry_ref=species_entry_ref,
        review_summary=summary,
        records=returned,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(returned),
            total=pre_collapse_total,
        ),
    )


def build_thermo_trust_fragment(
    thermo: Thermo,
    review_status: RecordReviewStatus | None = None,
) -> TrustFragment:
    """Build the read-layer trust fragment for a thermo record."""
    evaluation = evaluate_loaded_thermo(thermo)
    return build_trust_fragment(evaluation, review_status=review_status)


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


def _load_nasa9(
    session: Session, thermo_ids: list[int]
) -> dict[int, list[ThermoNASA9Interval]]:
    if not thermo_ids:
        return {}
    rows = session.scalars(
        select(ThermoNASA9Interval)
        .where(ThermoNASA9Interval.thermo_id.in_(thermo_ids))
        .order_by(ThermoNASA9Interval.interval_index)
    ).all()
    grouped: dict[int, list[ThermoNASA9Interval]] = {tid: [] for tid in thermo_ids}
    for iv in rows:
        grouped[iv.thermo_id].append(iv)
    return grouped


def _load_wilhoit(
    session: Session, thermo_ids: list[int]
) -> dict[int, ThermoWilhoit]:
    if not thermo_ids:
        return {}
    rows = session.scalars(
        select(ThermoWilhoit).where(ThermoWilhoit.thermo_id.in_(thermo_ids))
    ).all()
    return {w.thermo_id: w for w in rows}


def _load_group_additivity(
    session: Session, thermo_ids: list[int]
) -> dict[int, AppliedGroupAdditivity]:
    """Load the applied GA breakdown (scheme + components) per thermo id.

    At most one ``applied_group_additivity`` row exists per thermo
    (``thermo_id`` UNIQUE), so the result maps each thermo id to a single
    row. Only estimated thermo records have one; others are absent from the
    map and surface ``group_additivity=null`` in the read.
    """
    if not thermo_ids:
        return {}
    rows = session.scalars(
        select(AppliedGroupAdditivity)
        .where(AppliedGroupAdditivity.thermo_id.in_(thermo_ids))
        .options(
            selectinload(AppliedGroupAdditivity.scheme),
            selectinload(AppliedGroupAdditivity.components),
        )
    ).all()
    return {row.thermo_id: row for row in rows if row.thermo_id is not None}


def _build_group_additivity_block(
    applied: AppliedGroupAdditivity | None,
) -> GroupAdditivityBlock | None:
    """Build the read-layer GA block, or ``None`` when no breakdown exists."""
    if applied is None:
        return None
    scheme: GroupAdditivityScheme = applied.scheme
    components = sorted(applied.components, key=lambda c: c.id)
    return GroupAdditivityBlock(
        scheme_id=scheme.id,
        scheme_ref=scheme.public_ref,
        scheme_name=scheme.name,
        scheme_version=scheme.version,
        code_commit=scheme.code_commit,
        note=applied.note,
        components=[
            GroupAdditivityComponentBlock(
                component_kind=c.component_kind,
                group_label=c.group_label,
                count=c.count,
                h298_contribution_kj_mol=c.h298_contribution_kj_mol,
                s298_contribution_j_mol_k=c.s298_contribution_j_mol_k,
                cp298_contribution_j_mol_k=c.cp298_contribution_j_mol_k,
            )
            for c in components
        ],
    )


def _load_statmech_ids(session: Session, species_entry_id: int) -> set[int]:
    rows = session.scalars(
        select(Statmech.id).where(Statmech.species_entry_id == species_entry_id)
    ).all()
    return set(rows)


def _load_statmech_sources(
    session: Session, statmech_id: int | None
) -> list[StatmechSourceCalculation]:
    """Load source-calculation links for the picked statmech.

    Used by ``_build_provenance`` / ``_evidence_breakdown`` as a fallback
    when a thermo record's own ``ThermoSourceCalculation`` rows do not
    cover the freq / SP / composite roles. Returns ``[]`` when no
    statmech was picked.
    """
    if statmech_id is None:
        return []
    return list(
        session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == statmech_id
            )
        ).all()
    )


def _statmech_calc_id_for_role(
    statmech_sources: list[StatmechSourceCalculation],
    role: StatmechCalculationRole,
) -> int | None:
    """Return the first statmech-source calc id matching *role*, or ``None``."""
    for sc in statmech_sources:
        if sc.role == role:
            return sc.calculation_id
    return None


def _statmech_primary_calc_id(
    statmech_sources: list[StatmechSourceCalculation],
) -> int | None:
    """Pick a primary calculation id from statmech sources using the same
    role priority the thermo service uses (sp → composite → freq → opt).
    """
    for role in (
        StatmechCalculationRole.sp,
        StatmechCalculationRole.composite,
        StatmechCalculationRole.freq,
        StatmechCalculationRole.opt,
    ):
        calc_id = _statmech_calc_id_for_role(statmech_sources, role)
        if calc_id is not None:
            return calc_id
    if statmech_sources:
        return statmech_sources[0].calculation_id
    return None


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
    return dict(rows)


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
    return dict(rows)


def _calc_lot_meta(session: Session, calc_ids: set[int]) -> dict[int, dict]:
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            Calculation.id,
            Calculation.type,
            Calculation.lot_id,
            LevelOfTheory.public_ref,
            LevelOfTheory.method,
            LevelOfTheory.basis,
            LevelOfTheory.dispersion,
            LevelOfTheory.solvent,
            Calculation.software_release_id,
            SoftwareRelease.public_ref,
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
            "lot_ref": row[3],
            "lot_method": row[4],
            "lot_basis": row[5],
            "lot_dispersion": row[6],
            "lot_solvent": row[7],
            "software_release_id": row[8],
            "software_release_ref": row[9],
            "software_name": row[10],
            "software_version": row[11],
            "geometry_validation": row[12],
            "scf_stability": row[13],
        }
        for row in rows
    }


def _calc_refs(session: Session, calc_ids: set[int]) -> dict[int, str]:
    """Bulk-load {calculation_id: public_ref} for a set of calc ids."""
    if not calc_ids:
        return {}
    rows = session.execute(
        select(Calculation.id, Calculation.public_ref).where(
            Calculation.id.in_(calc_ids)
        )
    ).all()
    return dict(rows)


def _load_statmech_refs(
    session: Session, statmech_ids: set[int]
) -> dict[int, str]:
    if not statmech_ids:
        return {}
    rows = session.execute(
        select(Statmech.id, Statmech.public_ref).where(
            Statmech.id.in_(statmech_ids)
        )
    ).all()
    return dict(rows)


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
    calc_refs: dict[int, str],
    statmech_id: int | None,
    statmech_refs: dict[int, str],
    statmech_sources: list[StatmechSourceCalculation],
) -> ThermoProvenance:
    """Build a ``ThermoProvenance`` block for one thermo record.

    Phase 2 audit (thermo provenance / geometry): when the thermo's own
    ``ThermoSourceCalculation`` rows are empty or do not cover a role,
    fall back to the record's resolved statmech ``StatmechSourceCalculation``
    rows for that role. Explicit thermo sources always win. This makes
    computed-thermo provenance reflect the freq / SP / LoT / software
    that live on the statmech the thermo derives from, instead of
    coming back uniformly ``null``.

    ``statmech_id`` is the record's already-resolved basis (its own
    ``thermo.statmech_id`` FK, or the entry-min fallback for records with no
    linked statmech). ``statmech_sources`` are that same statmech's source
    calcs — so the surfaced ``statmech_ref`` and the borrowed source calcs
    always come from the one statmech the record actually derives from.
    """
    primary_calc_id_v = _primary_calc_id(sources)
    # Fall back to a statmech-derived primary when thermo declared none.
    if primary_calc_id_v is None:
        primary_calc_id_v = _statmech_primary_calc_id(statmech_sources)
    primary_meta = calc_meta.get(primary_calc_id_v) if primary_calc_id_v else None

    primary_calc_summary: CalculationEvidenceSummary | None = None
    primary_lot: LevelOfTheorySummary | None = None
    primary_sw: SoftwareReleaseSummary | None = None

    if primary_meta is not None:
        gv = primary_meta["geometry_validation"]
        scf = primary_meta["scf_stability"]
        primary_calc_summary = CalculationEvidenceSummary(
            calculation_id=primary_calc_id_v,
            calculation_ref=calc_refs.get(primary_calc_id_v),
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
    if freq_calc_id is None:
        freq_calc_id = _statmech_calc_id_for_role(
            statmech_sources, StatmechCalculationRole.freq
        )
    sp_calc_id = _calc_id_for_role(sources, ThermoCalculationRole.sp)
    if sp_calc_id is None:
        sp_calc_id = _statmech_calc_id_for_role(
            statmech_sources, StatmechCalculationRole.sp
        )

    # ``statmech_id`` is resolved per record by the caller: the thermo's own
    # ``statmech_id`` FK when set, else the entry-min fallback. The surfaced
    # ref and the borrowed source calcs above therefore come from the exact
    # statmech this record derives from.
    return ThermoProvenance(
        primary_calculation=primary_calc_summary,
        level_of_theory=primary_lot,
        software=primary_sw,
        statmech_id=statmech_id,
        statmech_ref=statmech_refs.get(statmech_id) if statmech_id is not None else None,
        freq_calculation_id=freq_calc_id,
        freq_calculation_ref=calc_refs.get(freq_calc_id) if freq_calc_id is not None else None,
        sp_calculation_id=sp_calc_id,
        sp_calculation_ref=calc_refs.get(sp_calc_id) if sp_calc_id is not None else None,
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
        level_of_theory_ref=meta["lot_ref"],
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
        software_release_ref=meta["software_release_ref"],
        software=meta["software_name"] or "",
        version=meta["software_version"],
    )


def _evidence_breakdown(
    *,
    thermo: Thermo,
    sources: list[ThermoSourceCalculation],
    statmech_ids_for_entry: set[int],
    statmech_sources: list[StatmechSourceCalculation],
    nasa_present: bool,
    points_count: int,
    has_nasa9: bool = False,
    has_wilhoit: bool = False,
    geom_vals: dict[int, ValidationStatus],
    scf_vals: dict[int, SCFStabilityStatus],
) -> EvidenceCompletenessBreakdown:
    """Thermo L1 checklist, max=8.

    Phase 2 audit (thermo provenance / geometry): for computed thermo
    that derives from a statmech without denormalized thermo-source
    rows, count the statmech's freq / SP / opt source calculations
    toward the checklist. Direct thermo source-calculation rows still
    win when present; statmech-linked rows are a fallback. The
    predicate names and the total ``max`` are unchanged.
    """
    statmech_roles = {sc.role for sc in statmech_sources}
    has_sources = len(sources) > 0 or len(statmech_sources) > 0
    has_statmech = len(statmech_ids_for_entry) > 0

    source_roles = {sc.role for sc in sources}
    has_freq_evidence = (
        ThermoCalculationRole.freq in source_roles
        or StatmechCalculationRole.freq in statmech_roles
    )
    has_sp_or_energy_evidence = (
        ThermoCalculationRole.sp in source_roles
        or ThermoCalculationRole.composite in source_roles
        or StatmechCalculationRole.sp in statmech_roles
        or StatmechCalculationRole.composite in statmech_roles
    )
    has_temperature_dependent_model = (
        nasa_present or has_nasa9 or has_wilhoit or points_count >= 2
    )

    has_uncertainty = any(
        v is not None
        for v in (thermo.h298_uncertainty_kj_mol, thermo.s298_uncertainty_j_mol_k)
    )

    # Geometry validation: target = opt source calc, falling back to the
    # statmech's opt source if thermo declared none.
    opt_calc_id = _calc_id_for_role(sources, ThermoCalculationRole.opt)
    if opt_calc_id is None:
        opt_calc_id = _statmech_calc_id_for_role(
            statmech_sources, StatmechCalculationRole.opt
        )
    has_geom_val = False
    if opt_calc_id is not None:
        gv = geom_vals.get(opt_calc_id)
        has_geom_val = gv in {ValidationStatus.passed, ValidationStatus.warning}

    # SCF stability: target = sp source calc (with the same statmech fallback).
    sp_calc_id = _calc_id_for_role(sources, ThermoCalculationRole.sp)
    if sp_calc_id is None:
        sp_calc_id = _statmech_calc_id_for_role(
            statmech_sources, StatmechCalculationRole.sp
        )
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


_STORED_KIND_TO_QUERY: dict[ThermoModelKind, ThermoModelKindQuery] = {
    ThermoModelKind.nasa7: ThermoModelKindQuery.nasa,
    ThermoModelKind.nasa9: ThermoModelKindQuery.nasa9,
    ThermoModelKind.wilhoit: ThermoModelKindQuery.wilhoit,
    ThermoModelKind.tabulated: ThermoModelKindQuery.points,
    ThermoModelKind.scalar: ThermoModelKindQuery.scalar,
}


def _classify_model_kind(
    thermo: Thermo,
    *,
    has_nasa: bool,
    has_nasa9: bool,
    has_wilhoit: bool,
    has_points: bool,
) -> ThermoModelKindQuery:
    """Map the stored ``thermo.model_kind`` to the read query enum.

    When ``thermo.model_kind`` is set it wins (nasa7→nasa, nasa9→nasa9,
    wilhoit→wilhoit, tabulated→points, scalar→scalar). When it is NULL —
    legacy rows the backfill could not classify — fall back to deriving the
    kind from which child rows exist.
    """
    if thermo.model_kind is not None:
        return _STORED_KIND_TO_QUERY[thermo.model_kind]
    if has_nasa:
        return ThermoModelKindQuery.nasa
    if has_nasa9:
        return ThermoModelKindQuery.nasa9
    if has_wilhoit:
        return ThermoModelKindQuery.wilhoit
    if has_points:
        return ThermoModelKindQuery.points
    return ThermoModelKindQuery.scalar


def _build_nasa9_blocks(
    intervals: list[ThermoNASA9Interval],
) -> list[ThermoNASA9IntervalBlock]:
    return [
        ThermoNASA9IntervalBlock(
            interval_index=iv.interval_index,
            t_min_k=iv.t_min_k,
            t_max_k=iv.t_max_k,
            a1=iv.a1,
            a2=iv.a2,
            a3=iv.a3,
            a4=iv.a4,
            a5=iv.a5,
            a6=iv.a6,
            a7=iv.a7,
            a8=iv.a8,
            a9=iv.a9,
        )
        for iv in intervals
    ]


def _build_wilhoit_block(wilhoit: ThermoWilhoit) -> ThermoWilhoitBlock:
    return ThermoWilhoitBlock(
        cp0_j_mol_k=wilhoit.cp0_j_mol_k,
        cp_inf_j_mol_k=wilhoit.cp_inf_j_mol_k,
        b_k=wilhoit.b_k,
        a0=wilhoit.a0,
        a1=wilhoit.a1,
        a2=wilhoit.a2,
        a3=wilhoit.a3,
        h0_kj_mol=wilhoit.h0_kj_mol,
        s0_j_mol_k=wilhoit.s0_j_mol_k,
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
    if request.level_of_theory_ref is not None:
        echo["level_of_theory_ref"] = request.level_of_theory_ref
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
    species_entry_ref: str,
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
            selection_policy=request.selection_policy,
            include=sorted(includes),
        ),
        species_entry_id=species_entry_id,
        species_entry_ref=species_entry_ref,
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )
