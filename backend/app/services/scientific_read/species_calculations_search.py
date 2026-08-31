"""Service implementation for /api/v1/scientific/species-calculations/search.

Chemistry-first species calculation/conformer search. The endpoint is
calculation-centered: response records are calculations with attached
species identity, energy (when applicable), level of theory, software,
optional conformer context, geometry IDs, validation, review state, and
provenance.

Composition strategy (additive on top of Phase 6):

1. Resolve species/species_entries via :func:`search_species`. If
   ``species_entry_id`` was supplied as a handle, validate it exists
   (404 otherwise) and use it directly.
2. Pull calculations for the resolved species_entries with the requested
   calculation/LoT/software/quality/review filters applied at the SQL
   level so the candidate set stays tight.
3. Bulk-load the supporting blocks (energies, frequency results,
   geometries, conformer context, validation/SCF, artifacts presence,
   dependencies) keyed by the candidate calculation IDs. Every one of
   them is one statement for the whole page: this is a paginated search,
   so anything issued per record is multiplied by ``limit``.
4. Apply ranking (default vs latest/earliest/lowest_energy), then
   collapse and pagination per Phase 2.1 rules.

LoT filtering targets ``Calculation.lot_id`` directly here — different
from thermo/kinetics search, which target the primary source calc's LoT.

Conformer context is only populated when ``Calculation.conformer_observation_id``
is set; the service must never fabricate associations.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError, reject_unsupported_filters
from app.api.errors import NotFoundError, not_found
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationDependency,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSCFStability,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
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
from app.schemas.reads.scientific_calculation import (
    CalculationFreqModeSummary,
    CalculationFreqResultSummary,
)
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    CollapseMode,
    LevelOfTheorySummary,
    ReviewStatusSummary,
    SCFStabilitySummary,
    SoftwareReleaseSummary,
    ValidationSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_species import SpeciesSearchRequest
from app.schemas.reads.scientific_species_calculations import (
    CalculationCoreBlock,
    CalculationEnergyBlock,
    CalculationProvenanceBlock,
    CalculationRanking,
    ConformerContextBlock,
    DerivedSinglePointEnergy,
    GeometryBlock,
    GeometryRef,
    RequestEcho,
    ScientificSpeciesCalculationsSearchResponse,
    SpeciesCalculationsSearchRecord,
    SpeciesCalculationsSearchRequest,
    SpeciesCalculationsSpeciesContext,
    SupportingCalculationRef,
    ValidationBlock,
)
from app.services.scientific_read.calculations import (
    _count_at_or_above_tau,
)
from app.services.scientific_read.common import (
    build_pagination,
    collect_bounded_pages,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    slice_for_pagination,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.handles import (
    NO_MATCH,
    reconcile_level_of_theory_pair,
    reconcile_species_entry_pair,
    reconcile_species_pair,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.species import search_species
from app.services.scientific_read.species_identity import (
    species_entry_label_for,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "provenance",
    "calculations",
    "artifacts",
    "review",
    "conformers",
    "geometry",
    "validation",
    "scf_stability",
    # The per-mode harmonic frequency array. Public (so ``include=all``
    # expands to it) but opt-in, because it is the only block on this
    # surface whose size grows with the molecule *and* with the page.
    "freq_modes",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

_LOWEST_ENERGY_LEGAL_TYPES: set[CalculationType] = {
    CalculationType.sp,
    CalculationType.opt,
}


@dataclass
class _CalcRow:
    """Raw join result we then turn into a Pydantic record."""

    calc_id: int
    calc_ref: str
    calc_type: CalculationType
    quality: CalculationQuality
    created_at: object
    species_entry_id: int
    lot_id: int | None
    lot_ref: str | None
    lot_method: str | None
    lot_basis: str | None
    lot_dispersion: str | None
    lot_solvent: str | None
    software_release_id: int | None
    software_release_ref: str | None
    software_name: str | None
    software_version: str | None
    workflow_tool_release_id: int | None
    workflow_tool_release_ref: str | None
    workflow_tool_name: str | None
    workflow_tool_version: str | None
    conformer_observation_id: int | None
    energy_hartree: float | None
    energy_kind: str | None  # "electronic_energy" | "final_energy" | None


def search_species_calculations(
    session: Session, request: SpeciesCalculationsSearchRequest
) -> ScientificSpeciesCalculationsSearchResponse:
    """Chemistry-first species calculation/conformer search.

    Returns calculations for species matching the request identity, with
    full per-record context (energy/conformer/geometry/validation/review).
    See ``docs/specs/species_calculation_search_api.md`` for the contract.

    :param session: SQLAlchemy session.
    :param request: Parsed request model.
    :returns: ``ScientificSpeciesCalculationsSearchResponse``.
    :raises NotFoundError: 404 when an explicit handle id does not exist.
    :raises ValueError: 422 for sort/include/pagination/ranking validation failures.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/species-calculations/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)
    reject_unsupported_filters(
        {
            "inchi": request.inchi,
            "scientific_origin": request.scientific_origin,
        },
        endpoint="/scientific/species-calculations/search",
    )
    _validate_ranking(request)

    # Phase C: reconcile species_id+species_ref, species_entry_id+
    # species_entry_ref, and level_of_theory_id+level_of_theory_ref.
    species_pair = reconcile_species_pair(
        session,
        id_value=request.species_id,
        ref_value=request.species_ref,
    )
    species_entry_pair = reconcile_species_entry_pair(
        session,
        id_value=request.species_entry_id,
        ref_value=request.species_entry_ref,
    )
    lot_pair = reconcile_level_of_theory_pair(
        session,
        id_value=request.level_of_theory_id,
        ref_value=request.level_of_theory_ref,
    )
    # An explicit ref that resolves to no row → match nothing.
    if (
        species_pair is NO_MATCH
        or species_entry_pair is NO_MATCH
        or lot_pair is NO_MATCH
    ):
        return _empty_response(request, includes, offset, limit)
    effective_species_id: int | None = species_pair  # type: ignore[assignment]
    effective_species_entry_id: int | None = species_entry_pair  # type: ignore[assignment]
    effective_lot_id: int | None = lot_pair  # type: ignore[assignment]

    # Resolve the candidate species_entry_ids that match the identity portion
    # of the request. Three paths: explicit species_entry_id handle (404 if
    # missing), explicit species_id handle (entries of that species), or
    # chemistry-first via the existing search_species service.
    entry_id_to_species_context = _resolve_species_entry_context(
        session,
        request,
        effective_species_id=effective_species_id,
        effective_species_entry_id=effective_species_entry_id,
    )
    if not entry_id_to_species_context:
        return _empty_response(request, includes, offset, limit)

    # Pull candidate calculations with all the simple column filters applied
    # at the SQL level so the candidate set stays tight.
    rows = _query_candidate_calculations(
        session,
        request,
        list(entry_id_to_species_context.keys()),
        effective_lot_id=effective_lot_id,
    )
    if not rows:
        return _empty_response(request, includes, offset, limit)

    # Apply review-status filter (post-query) using the existing helpers so
    # the trust posture stays consistent with other scientific endpoints.
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=[r.calc_id for r in rows],
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    rows = [r for r in rows if badges[r.calc_id].status in visible]
    if not rows:
        return _empty_response(request, includes, offset, limit)

    if (
        request.ranking == CalculationRanking.lowest_energy
        and all(row.energy_hartree is None for row in rows)
    ):
        energy_field = (
            "electronic_energy_hartree"
            if request.calculation_type == CalculationType.sp
            else "final_energy_hartree"
        )
        raise CodedValueError(
            "lowest_energy_unavailable",
            "No lowest energy is available because none of the matching "
            f"calculations has a recorded {energy_field} value.",
            context={
                "candidate_count": len(rows),
                "calculation_type": request.calculation_type.value,
                "energy_field": energy_field,
                "species_entry_ref": request.species_entry_ref,
                "level_of_theory_ref": request.level_of_theory_ref,
            },
        )

    # Bulk-load supporting blocks keyed by calculation_id.
    calc_ids = [r.calc_id for r in rows]
    geometry_by_calc = _load_geometries(session, calc_ids)
    validations_by_calc = _load_validation_summaries(session, calc_ids)
    scf_by_calc = _load_scf_summaries(session, calc_ids)
    artifacts_present = _load_artifacts_present(session, calc_ids)
    dependencies_by_child = _load_dependencies(session, calc_ids)
    conformer_by_obs = _load_conformer_contexts(
        session, [r.conformer_observation_id for r in rows if r.conformer_observation_id]
    )
    freq_by_calc = _load_freq_summaries(session, calc_ids)
    # One statement for the page, and only when asked. ``None`` here is
    # "the caller did not request the modes"; the empty list a record
    # gets below is "requested, and this calculation has none".
    modes_by_calc = (
        _load_freq_modes(session, calc_ids) if "freq_modes" in includes else None
    )
    # Observation -> set of lot_ids carrying a real ``sp`` calculation, so
    # ``_build_energy_block`` can decide whether an ``opt`` record's own
    # final energy may be offered as a single-point-equivalent (only when
    # no real sp exists at that observation's own lot). Scoped to the
    # observations of opt rows that could actually use it, not the whole
    # page.
    sp_lot_pairs_by_obs = _load_sp_lot_pairs(
        session,
        {
            r.conformer_observation_id
            for r in rows
            if r.calc_type == CalculationType.opt
            and r.energy_hartree is not None
            and r.conformer_observation_id is not None
            and r.lot_id is not None
        },
    )

    # Build response records.
    records: list[SpeciesCalculationsSearchRecord] = []
    for r in rows:
        species_ctx = entry_id_to_species_context[r.species_entry_id]
        records.append(
            SpeciesCalculationsSearchRecord(
                species=species_ctx,
                calculation=CalculationCoreBlock(
                    calculation_id=r.calc_id,
                    calculation_ref=r.calc_ref,
                    calculation_type=r.calc_type,
                    calculation_quality=r.quality,
                    created_at=r.created_at,
                    review=badges[r.calc_id],
                ),
                energy=_build_energy_block(r, sp_lot_pairs_by_obs),
                frequency=_build_frequency_block(r, freq_by_calc.get(r.calc_id)),
                freq_modes=(
                    None
                    if modes_by_calc is None
                    else modes_by_calc.get(r.calc_id, [])
                ),
                level_of_theory=_lot_summary_from_row(r),
                software_release=_software_summary_from_row(r),
                workflow_tool_release=_workflow_tool_summary_from_row(r),
                conformer=conformer_by_obs.get(r.conformer_observation_id)
                if r.conformer_observation_id is not None
                else None,
                geometry=geometry_by_calc.get(r.calc_id, GeometryBlock()),
                validation=ValidationBlock(
                    geometry_validation=validations_by_calc.get(r.calc_id),
                    scf_stability=scf_by_calc.get(r.calc_id),
                ),
                provenance=CalculationProvenanceBlock(
                    supporting_calculation_ids=[
                        sc.calculation_id
                        for sc in dependencies_by_child.get(r.calc_id, [])
                    ],
                    supporting_calculations=dependencies_by_child.get(r.calc_id, []),
                    submission_id=None,
                    submission_ref=None,
                    artifacts_available=r.calc_id in artifacts_present,
                ),
            )
        )

    summary = review_summary(badges[r.calc_id] for r in rows)
    sort_echo = _apply_ranking_sort(records, request)

    pre_collapse_total = len(records)
    collapse_first = request.collapse.value == "first"
    returned = slice_for_pagination(
        records,
        offset=offset,
        limit=limit,
        collapse_first=collapse_first,
    )

    return ScientificSpeciesCalculationsSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            ranking=request.ranking,
            sort=sort_echo,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(returned),
            total=pre_collapse_total,
            collapse_first=collapse_first,
        ),
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_ranking(request: SpeciesCalculationsSearchRequest) -> None:
    """Validate that lowest-energy candidates are physically comparable."""
    if request.ranking != CalculationRanking.lowest_energy:
        return
    if request.calculation_type not in _LOWEST_ENERGY_LEGAL_TYPES:
        raise ValueError(
            "unsupported_ranking_for_calculation_type: ranking=lowest_energy "
            "requires calculation_type=sp or calculation_type=opt."
        )
    if request.species_entry_ref is None or request.level_of_theory_ref is None:
        raise ValueError(
            "unsafe_lowest_energy_comparison: ranking=lowest_energy requires "
            "exact species_entry_ref and level_of_theory_ref filters."
        )


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


def _resolve_species_entry_context(
    session: Session,
    request: SpeciesCalculationsSearchRequest,
    *,
    effective_species_id: int | None = None,
    effective_species_entry_id: int | None = None,
) -> dict[int, SpeciesCalculationsSpeciesContext]:
    """Return mapping from species_entry_id → SpeciesCalculationsSpeciesContext.

    Phase C: callers pre-reconcile id/ref pairs and pass the effective
    integer ids in. Falls back to ``request.species_id`` / ``request.
    species_entry_id`` for backwards compatibility.
    """
    species_entry_id = (
        effective_species_entry_id
        if effective_species_entry_id is not None
        else request.species_entry_id
    )
    species_id = (
        effective_species_id
        if effective_species_id is not None
        else request.species_id
    )

    # Path 1: explicit species_entry handle (404 if not found).
    if species_entry_id is not None:
        entry = session.get(SpeciesEntry, species_entry_id)
        if entry is None:
            raise not_found("species_entry", row_id=species_entry_id)
        species = session.get(Species, entry.species_id)
        if species is None:  # pragma: no cover — referential integrity
            raise NotFoundError("species_entry references a missing species row")
        return {
            entry.id: _species_context_from_orm(species, entry),
        }

    # Path 2: explicit species handle (404 if not found).
    if species_id is not None:
        species = session.get(Species, species_id)
        if species is None:
            raise not_found("species", row_id=species_id)
        entries = list(species.entries)
        return {
            e.id: _species_context_from_orm(species, e) for e in entries
        }

    # Path 3: chemistry-first via search_species. Require at least one
    # identifier (matching Phase 6 search_thermo behavior). Phase C adds
    # species_ref / species_entry_ref / level_of_theory_ref as valid
    # identifier sources, but those are already consumed by the
    # reconcile pass above — if we got here, none of those resolved.
    if not any(
        v is not None
        for v in (request.smiles, request.inchi, request.inchi_key, request.formula)
    ):
        raise ValueError(
            "missing_identifier: at least one of {smiles, inchi, inchi_key, "
            "formula, species_id, species_ref, species_entry_id, "
            "species_entry_ref} is required."
        )

    def fetch_species_page(page_offset: int, page_limit: int):
        return search_species(
            session,
            SpeciesSearchRequest(
                smiles=request.smiles,
                inchi=request.inchi,
                inchi_key=request.inchi_key,
                formula=request.formula,
                charge=request.charge,
                multiplicity=request.multiplicity,
                electronic_state_kind=request.electronic_state_kind,
                species_entry_kind=request.species_entry_kind,
                min_review_status=None,
                include_rejected=request.include_rejected,
                include_deprecated=request.include_deprecated,
                offset=page_offset,
                limit=page_limit,
                collapse=CollapseMode.all,
                include=[],
            ),
        )

    species_records = collect_bounded_pages(
        fetch_species_page,
        resource_name="species-calculation discovery candidates",
    )

    out: dict[int, SpeciesCalculationsSpeciesContext] = {}
    for sp_record in species_records:
        for entry in sp_record.entries:
            out[entry.species_entry_id] = SpeciesCalculationsSpeciesContext(
                species_id=sp_record.species_id,
                species_ref=sp_record.species_ref,
                species_entry_id=entry.species_entry_id,
                species_entry_ref=entry.species_entry_ref,
                species_entry_label=entry.species_entry_label,
                canonical_smiles=sp_record.canonical_smiles,
                inchi_key=sp_record.inchi_key,
                charge=sp_record.charge,
                multiplicity=sp_record.multiplicity,
                species_entry_kind=entry.species_entry_kind,
                electronic_state_kind=entry.electronic_state_kind,
            )
    return out


def _species_context_from_orm(
    species: Species, entry: SpeciesEntry
) -> SpeciesCalculationsSpeciesContext:
    return SpeciesCalculationsSpeciesContext(
        species_id=species.id,
        species_ref=species.public_ref,
        species_entry_id=entry.id,
        species_entry_ref=entry.public_ref,
        species_entry_label=species_entry_label_for(entry),
        canonical_smiles=species.smiles,
        inchi_key=species.inchi_key,
        charge=species.charge,
        multiplicity=species.multiplicity,
        species_entry_kind=entry.kind,
        electronic_state_kind=entry.electronic_state_kind,
    )


# ---------------------------------------------------------------------------
# Calculation query
# ---------------------------------------------------------------------------


def _query_candidate_calculations(
    session: Session,
    request: SpeciesCalculationsSearchRequest,
    species_entry_ids: list[int],
    *,
    effective_lot_id: int | None = None,
) -> list[_CalcRow]:
    """Pull calculations for the resolved species_entries with column filters.

    Phase C: ``effective_lot_id`` is the reconciled
    level_of_theory_id (after merging the optional
    ``level_of_theory_ref``). Falls back to
    ``request.level_of_theory_id`` for backwards compatibility.
    """
    stmt = (
        select(
            Calculation.id,
            Calculation.public_ref,
            Calculation.type,
            Calculation.quality,
            Calculation.created_at,
            Calculation.species_entry_id,
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
            Calculation.workflow_tool_release_id,
            WorkflowToolRelease.public_ref,
            WorkflowTool.name,
            WorkflowToolRelease.version,
            Calculation.conformer_observation_id,
            CalculationSPResult.electronic_energy_hartree,
            CalculationOptResult.final_energy_hartree,
        )
        .join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id, isouter=True)
        .join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
            isouter=True,
        )
        .join(Software, Software.id == SoftwareRelease.software_id, isouter=True)
        .join(
            WorkflowToolRelease,
            WorkflowToolRelease.id == Calculation.workflow_tool_release_id,
            isouter=True,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
            isouter=True,
        )
        .join(
            CalculationSPResult,
            CalculationSPResult.calculation_id == Calculation.id,
            isouter=True,
        )
        .join(
            CalculationOptResult,
            CalculationOptResult.calculation_id == Calculation.id,
            isouter=True,
        )
        .where(Calculation.species_entry_id.in_(species_entry_ids))
    )

    if request.calculation_type is not None:
        stmt = stmt.where(Calculation.type == request.calculation_type)
    lot_filter = (
        effective_lot_id
        if effective_lot_id is not None
        else request.level_of_theory_id
    )
    if lot_filter is not None:
        stmt = stmt.where(Calculation.lot_id == lot_filter)
    if request.method is not None:
        stmt = stmt.where(LevelOfTheory.method == request.method)
    if request.basis is not None:
        stmt = stmt.where(LevelOfTheory.basis == request.basis)
    if request.software is not None:
        stmt = stmt.where(Software.name == request.software)
    if request.workflow_tool is not None:
        stmt = stmt.where(WorkflowTool.name == request.workflow_tool)
    # CalculationQuality filter (separate from review's "rejected").
    if request.calculation_quality is not None:
        stmt = stmt.where(Calculation.quality == request.calculation_quality)
    elif not request.include_rejected_quality:
        stmt = stmt.where(Calculation.quality != CalculationQuality.rejected)

    raw_rows = session.execute(stmt).all()

    out: list[_CalcRow] = []
    for row in raw_rows:
        sp_energy = row[21]
        opt_energy = row[22]
        calc_type_v = row[2]
        # Pick the per-type energy if the calculation type matches; this
        # is how lowest_energy ranking picks a column without a SQL CASE.
        if calc_type_v == CalculationType.sp and sp_energy is not None:
            energy_hartree, energy_kind = sp_energy, "electronic_energy"
        elif calc_type_v == CalculationType.opt and opt_energy is not None:
            energy_hartree, energy_kind = opt_energy, "final_energy"
        elif calc_type_v == CalculationType.sp:
            energy_hartree, energy_kind = None, "electronic_energy"
        elif calc_type_v == CalculationType.opt:
            energy_hartree, energy_kind = None, "final_energy"
        else:
            energy_hartree, energy_kind = None, None

        out.append(
            _CalcRow(
                calc_id=row[0],
                calc_ref=row[1],
                calc_type=row[2],
                quality=row[3],
                created_at=row[4],
                species_entry_id=row[5],
                lot_id=row[6],
                lot_ref=row[7],
                lot_method=row[8],
                lot_basis=row[9],
                lot_dispersion=row[10],
                lot_solvent=row[11],
                software_release_id=row[12],
                software_release_ref=row[13],
                software_name=row[14],
                software_version=row[15],
                workflow_tool_release_id=row[16],
                workflow_tool_release_ref=row[17],
                workflow_tool_name=row[18],
                workflow_tool_version=row[19],
                conformer_observation_id=row[20],
                energy_hartree=energy_hartree,
                energy_kind=energy_kind,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Bulk loaders
# ---------------------------------------------------------------------------


def _load_geometries(
    session: Session, calc_ids: list[int]
) -> dict[int, GeometryBlock]:
    if not calc_ids:
        return {}

    inputs_by_calc: dict[int, list[int]] = defaultdict(list)
    for calc_id, geom_id in session.execute(
        select(
            CalculationInputGeometry.calculation_id,
            CalculationInputGeometry.geometry_id,
        )
        .where(CalculationInputGeometry.calculation_id.in_(calc_ids))
        .order_by(CalculationInputGeometry.input_order)
    ).all():
        inputs_by_calc[calc_id].append(geom_id)

    outputs_by_calc: dict[int, list[tuple[int, CalculationGeometryRole | None]]] = (
        defaultdict(list)
    )
    for calc_id, geom_id, role in session.execute(
        select(
            CalculationOutputGeometry.calculation_id,
            CalculationOutputGeometry.geometry_id,
            CalculationOutputGeometry.role,
        )
        .where(CalculationOutputGeometry.calculation_id.in_(calc_ids))
        .order_by(CalculationOutputGeometry.output_order)
    ).all():
        outputs_by_calc[calc_id].append((geom_id, role))

    # Bulk-load Geometry public_refs for every geometry referenced above.
    all_geom_ids: set[int] = set()
    for gids in inputs_by_calc.values():
        all_geom_ids.update(gids)
    for outs in outputs_by_calc.values():
        all_geom_ids.update(g for g, _ in outs)
    geom_refs: dict[int, str] = {}
    if all_geom_ids:
        geom_refs = dict(session.execute(
                select(Geometry.id, Geometry.public_ref).where(
                    Geometry.id.in_(all_geom_ids)
                )
            ).all())

    out: dict[int, GeometryBlock] = {}
    for cid in calc_ids:
        outputs = outputs_by_calc.get(cid, [])
        primary_id: int | None = None
        primary_role: CalculationGeometryRole | None = None
        for geom_id, role in outputs:
            if role == CalculationGeometryRole.final:
                primary_id, primary_role = geom_id, role
                break
        if primary_id is None and outputs:
            primary_id, primary_role = outputs[-1]  # most recent output_order
        input_ids = inputs_by_calc.get(cid, [])
        out[cid] = GeometryBlock(
            primary_output_geometry_id=primary_id,
            primary_output_geometry_ref=(
                geom_refs.get(primary_id) if primary_id is not None else None
            ),
            primary_output_geometry_role=primary_role,
            input_geometry_ids=input_ids,
            output_geometry_ids=[g for g, _ in outputs],
            input_geometries=[
                GeometryRef(geometry_id=g, geometry_ref=geom_refs.get(g, ""))
                for g in input_ids
            ],
            output_geometries=[
                GeometryRef(
                    geometry_id=g,
                    geometry_ref=geom_refs.get(g, ""),
                    role=role,
                )
                for g, role in outputs
            ],
        )
    return out


def _load_validation_summaries(
    session: Session, calc_ids: list[int]
) -> dict[int, ValidationSummary]:
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            CalculationGeometryValidation.calculation_id,
            CalculationGeometryValidation.validation_status,
            Calculation.public_ref,
        )
        .join(Calculation, Calculation.id == CalculationGeometryValidation.calculation_id)
        .where(CalculationGeometryValidation.calculation_id.in_(calc_ids))
    ).all()
    return {
        cid: ValidationSummary(
            status=status.value, calculation_id=cid, calculation_ref=ref
        )
        for cid, status, ref in rows
    }


def _load_scf_summaries(
    session: Session, calc_ids: list[int]
) -> dict[int, SCFStabilitySummary]:
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            CalculationSCFStability.calculation_id,
            CalculationSCFStability.status,
            Calculation.public_ref,
        )
        .join(Calculation, Calculation.id == CalculationSCFStability.calculation_id)
        .where(CalculationSCFStability.calculation_id.in_(calc_ids))
    ).all()
    return {
        cid: SCFStabilitySummary(
            status=status.value, calculation_id=cid, calculation_ref=ref
        )
        for cid, status, ref in rows
    }


def _load_artifacts_present(
    session: Session, calc_ids: list[int]
) -> set[int]:
    if not calc_ids:
        return set()
    rows = session.execute(
        select(CalculationArtifact.calculation_id)
        .where(CalculationArtifact.calculation_id.in_(calc_ids))
        .distinct()
    ).all()
    return {row[0] for row in rows}


def _load_dependencies(
    session: Session, calc_ids: list[int]
) -> dict[int, list[SupportingCalculationRef]]:
    """Return for each calc_id the list of parent calculations that
    point at it via ``CalculationDependency``.

    Spec field ``provenance.supporting_calculations`` is the set of
    calcs that *support* this one — i.e. parent calcs in dependency edges
    where this calc is the child. The ``supporting_calculation_ids`` echo
    is derived from the same list at record-build time.
    """
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            CalculationDependency.child_calculation_id,
            CalculationDependency.parent_calculation_id,
            Calculation.public_ref,
        )
        .join(Calculation, Calculation.id == CalculationDependency.parent_calculation_id)
        .where(CalculationDependency.child_calculation_id.in_(calc_ids))
    ).all()
    out: dict[int, list[SupportingCalculationRef]] = defaultdict(list)
    for child_id, parent_id, parent_ref in rows:
        out[child_id].append(
            SupportingCalculationRef(
                calculation_id=parent_id, calculation_ref=parent_ref
            )
        )
    # Deterministic order so test snapshots stay stable.
    for k in out:
        out[k].sort(key=lambda sc: sc.calculation_id)
    return out


def _load_freq_summaries(
    session: Session, calc_ids: list[int]
) -> dict[int, CalculationFreqResultSummary]:
    """Bulk-project ``calc_freq_result`` for a whole page, in one statement.

    The two mode counts ADR 0012 needs ride along as correlated
    aggregates on the same ``SELECT`` — the identical construction
    :func:`app.services.scientific_read.calculations._build_freq_summary`
    uses for a single record, widened from ``= :id`` to ``IN (:ids)``.
    That is the whole reason this is a bulk loader and not a call to that
    builder in a loop: a builder run per record is multiplied by the page
    size, and every other supporting block on this surface
    (``_load_geometries``, ``_load_validation_summaries``, …) is loaded
    the same way for the same reason. Pinned by
    ``tests/services/scientific_read/test_record_builder_statement_cost.py
    ::test_a_species_calculations_page_costs_one_freq_statement``.

    Keyed by ``calculation_id``; a calculation with no row is simply
    absent from the mapping, and the caller decides what absence means.
    """
    if not calc_ids:
        return {}

    imaginary = (
        CalculationFreqMode.calculation_id == CalculationFreqResult.calculation_id,
        CalculationFreqMode.is_imaginary.is_(True),
    )
    stored_imaginary_modes = (
        select(func.count())
        .select_from(CalculationFreqMode)
        .where(*imaginary)
        .correlate(CalculationFreqResult)
        .scalar_subquery()
    )
    at_or_above_tau = (
        select(func.count())
        .select_from(CalculationFreqMode)
        .where(
            *imaginary,
            func.abs(CalculationFreqMode.frequency_cm1)
            >= CalculationFreqResult.imaginary_mode_tau_cm1,
        )
        .correlate(CalculationFreqResult)
        .scalar_subquery()
    )

    out: dict[int, CalculationFreqResultSummary] = {}
    for row, stored_imaginary, above_tau in session.execute(
        select(
            CalculationFreqResult, stored_imaginary_modes, at_or_above_tau
        ).where(CalculationFreqResult.calculation_id.in_(calc_ids))
    ).all():
        out[row.calculation_id] = CalculationFreqResultSummary(
            n_imag=row.n_imag,
            imag_freq_cm1=row.imag_freq_cm1,
            zpe_hartree=row.zpe_hartree,
            zpe_uncertainty_hartree=row.zpe_uncertainty_hartree,
            reaction_coordinate_mode_index=row.reaction_coordinate_mode_index,
            imaginary_mode_tau_cm1=row.imaginary_mode_tau_cm1,
            imaginary_mode_tau_basis=row.imaginary_mode_tau_basis,
            imaginary_mode_structural_flag=row.imaginary_mode_structural_flag,
            # ADR 0012's rule for when this count cannot honestly be
            # taken has exactly one implementation, and it is the one the
            # calculation detail surface uses. Re-deriving it here would
            # make two surfaces able to disagree about the same stored
            # row, which is the failure that decision exists to prevent.
            n_imag_at_or_above_tau=_count_at_or_above_tau(
                tau_cm1=row.imaginary_mode_tau_cm1,
                n_imag=row.n_imag,
                stored_imaginary_modes=stored_imaginary,
                at_or_above_tau=above_tau,
            ),
        )
    return out


def _load_freq_modes(
    session: Session, calc_ids: list[int]
) -> dict[int, list[CalculationFreqModeSummary]]:
    """Bulk-project ``calc_freq_mode`` for a whole page, in one statement.

    Ordered by ``(calculation_id, mode_index)`` so each list comes back
    in the row's natural scientific order without a per-record sort.

    Only called when the caller asked for ``include=freq_modes``; the
    ``[]`` a calculation with no parsed modes gets is supplied by the
    record builder, not by this mapping, so "no rows" and "did not load"
    stay distinguishable here.
    """
    if not calc_ids:
        return {}
    out: dict[int, list[CalculationFreqModeSummary]] = defaultdict(list)
    for row in session.scalars(
        select(CalculationFreqMode)
        .where(CalculationFreqMode.calculation_id.in_(calc_ids))
        .order_by(
            CalculationFreqMode.calculation_id.asc(),
            CalculationFreqMode.mode_index.asc(),
        )
    ).all():
        out[row.calculation_id].append(
            CalculationFreqModeSummary(
                mode_index=row.mode_index,
                frequency_cm1=row.frequency_cm1,
                is_imaginary=row.is_imaginary,
                reduced_mass_amu=row.reduced_mass_amu,
                force_constant_mdyne_angstrom=row.force_constant_mdyne_angstrom,
                imaginary_disposition=row.imaginary_disposition,
            )
        )
    return dict(out)


def _load_conformer_contexts(
    session: Session, observation_ids: list[int]
) -> dict[int, ConformerContextBlock]:
    """Build ConformerContextBlock keyed by ConformerObservation.id.

    Compact ``torsion_fingerprint_json`` summary by default per spec
    (``{"present": bool}``); full JSON would surface via include=conformers
    in a future enhancement.
    """
    obs_ids = [oid for oid in observation_ids if oid is not None]
    if not obs_ids:
        return {}

    obs_rows = session.execute(
        select(
            ConformerObservation.id,
            ConformerObservation.public_ref,
            ConformerObservation.conformer_group_id,
            ConformerObservation.assignment_scheme_id,
            ConformerObservation.torsion_fingerprint_json,
        ).where(ConformerObservation.id.in_(obs_ids))
    ).all()

    group_ids = {row[2] for row in obs_rows}
    group_label_by_id: dict[int, str | None] = {}
    group_ref_by_id: dict[int, str] = {}
    if group_ids:
        for gid, ref, label in session.execute(
            select(
                ConformerGroup.id, ConformerGroup.public_ref, ConformerGroup.label
            ).where(ConformerGroup.id.in_(group_ids))
        ).all():
            group_label_by_id[gid] = label
            group_ref_by_id[gid] = ref

    scheme_ids = {row[3] for row in obs_rows if row[3] is not None}
    scheme_ref_by_id: dict[int, str] = {}
    if scheme_ids:
        scheme_ref_by_id = dict(session.execute(
                select(
                    ConformerAssignmentScheme.id,
                    ConformerAssignmentScheme.public_ref,
                ).where(ConformerAssignmentScheme.id.in_(scheme_ids))
            ).all())

    selections_by_group: dict[int, list[str]] = defaultdict(list)
    if group_ids:
        for gid, kind in session.execute(
            select(ConformerSelection.conformer_group_id, ConformerSelection.selection_kind).where(
                ConformerSelection.conformer_group_id.in_(group_ids)
            )
        ).all():
            selections_by_group[gid].append(kind)

    out: dict[int, ConformerContextBlock] = {}
    for obs_id, obs_ref, group_id, scheme_id, fingerprint in obs_rows:
        out[obs_id] = ConformerContextBlock(
            conformer_observation_id=obs_id,
            conformer_observation_ref=obs_ref,
            conformer_group_id=group_id,
            conformer_group_ref=group_ref_by_id.get(group_id, ""),
            conformer_assignment_scheme_id=scheme_id,
            conformer_assignment_scheme_ref=(
                scheme_ref_by_id.get(scheme_id) if scheme_id is not None else None
            ),
            conformer_group_label=group_label_by_id.get(group_id),
            torsion_fingerprint_json={"present": fingerprint is not None},
            selection_kinds=selections_by_group.get(group_id, []),
        )
    return out


# ---------------------------------------------------------------------------
# Per-row builders
# ---------------------------------------------------------------------------


def _load_sp_lot_pairs(
    session: Session, observation_ids: set[int]
) -> dict[int, set[int]]:
    """Map conformer_observation_id -> set of lot_ids with a real ``sp`` row.

    Used only to decide whether an ``opt`` record's final energy may be
    served as a single-point-equivalent (see :func:`_build_energy_block`):
    that requires *no* real ``sp`` calculation at the same observation +
    level-of-theory pair. This checks existence only — quality or review
    status never gate it, because the question is "was an independent sp
    actually run at this level of theory", not "was it a good one". A
    rejected-quality or unreviewed sp still means one was run, so the
    derivation must not fire.
    """
    if not observation_ids:
        return {}
    rows = session.execute(
        select(Calculation.conformer_observation_id, Calculation.lot_id)
        .where(
            Calculation.type == CalculationType.sp,
            Calculation.conformer_observation_id.in_(observation_ids),
            Calculation.lot_id.is_not(None),
        )
        .distinct()
    ).all()
    out: defaultdict[int, set[int]] = defaultdict(set)
    for obs_id, lot_id in rows:
        out[obs_id].add(lot_id)
    return dict(out)


def _build_energy_block(
    row: _CalcRow, sp_lot_pairs_by_obs: dict[int, set[int]]
) -> CalculationEnergyBlock | None:
    if row.energy_kind is None:
        return None
    single_point_equivalent: DerivedSinglePointEnergy | None = None
    if (
        row.calc_type == CalculationType.opt
        and row.energy_hartree is not None
        and row.conformer_observation_id is not None
        and row.lot_id is not None
        and row.lot_id
        not in sp_lot_pairs_by_obs.get(row.conformer_observation_id, set())
    ):
        single_point_equivalent = DerivedSinglePointEnergy(
            energy_hartree=row.energy_hartree
        )
    return CalculationEnergyBlock(
        energy_hartree=row.energy_hartree,
        energy_kind=row.energy_kind,
        single_point_equivalent=single_point_equivalent,
    )


def _build_frequency_block(
    row: _CalcRow, stored: CalculationFreqResultSummary | None
) -> CalculationFreqResultSummary | None:
    """``energy``'s mirror: which kind of result belongs on this record.

    Three outcomes, and they are the same three
    :func:`_build_energy_block` produces with the calculation types
    swapped:

    - a stored ``calc_freq_result`` row -> that row, projected;
    - no row but ``type == freq`` -> an all-``null`` block, meaning "a
      frequency result belongs here and none was parsed";
    - no row and some other type -> ``None``, meaning "this kind of
      record has no frequency result".

    The first branch is checked before the type rather than after it on
    purpose. A composite job deposited as ``opt`` that also carried a
    Hessian has a real ``calc_freq_result`` row, and keying the block off
    the type alone would store that row and never serve it. Presence of
    the row is the stronger fact and wins.
    """
    if stored is not None:
        return stored
    if row.calc_type == CalculationType.freq:
        return CalculationFreqResultSummary()
    return None


def _lot_summary_from_row(row: _CalcRow) -> LevelOfTheorySummary | None:
    if row.lot_id is None:
        return None
    label_parts = [row.lot_method or ""]
    if row.lot_basis:
        label_parts.append(row.lot_basis)
    return LevelOfTheorySummary(
        level_of_theory_id=row.lot_id,
        level_of_theory_ref=row.lot_ref,
        method=row.lot_method or "",
        basis=row.lot_basis,
        dispersion=row.lot_dispersion,
        solvent=row.lot_solvent,
        label="/".join(p for p in label_parts if p),
    )


def _software_summary_from_row(row: _CalcRow) -> SoftwareReleaseSummary | None:
    if row.software_release_id is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=row.software_release_id,
        software_release_ref=row.software_release_ref,
        software=row.software_name or "",
        version=row.software_version,
    )


def _workflow_tool_summary_from_row(
    row: _CalcRow,
) -> WorkflowToolReleaseSummary | None:
    if row.workflow_tool_release_id is None:
        return None
    return WorkflowToolReleaseSummary(
        workflow_tool_release_id=row.workflow_tool_release_id,
        workflow_tool_release_ref=row.workflow_tool_release_ref,
        workflow_tool=row.workflow_tool_name or "",
        version=row.workflow_tool_version,
    )


# ---------------------------------------------------------------------------
# Ranking / sort
# ---------------------------------------------------------------------------


def _apply_ranking_sort(
    records: list[SpeciesCalculationsSearchRecord],
    request: SpeciesCalculationsSearchRequest,
) -> str:
    """Sort records in-place per the requested ranking; return sort echo."""
    ranking = request.ranking

    if ranking == CalculationRanking.lowest_energy:
        # Energy ASC NULLS LAST, then default tiebreakers.
        def key(rec: SpeciesCalculationsSearchRecord) -> tuple:
            energy = rec.energy.energy_hartree if rec.energy else None
            energy_is_null = energy is None
            return (
                energy_is_null,                       # nulls last
                energy if energy is not None else 0.0,
                REVIEW_RANK[rec.calculation.review.status],
                -rec.calculation.created_at.timestamp(),
                -rec.calculation.calculation_id,
            )

        records.sort(key=key)
        return "energy_hartree_asc_nulls_last,review_rank,created_at_desc,id_desc"

    if ranking == CalculationRanking.latest:
        records.sort(
            key=lambda r: (-r.calculation.created_at.timestamp(), -r.calculation.calculation_id)
        )
        return "created_at_desc,id_desc"

    if ranking == CalculationRanking.earliest:
        records.sort(
            key=lambda r: (r.calculation.created_at.timestamp(), r.calculation.calculation_id)
        )
        return "created_at_asc,id_asc"

    # default and review_rank are equivalent for v0.
    records.sort(
        key=lambda r: (
            REVIEW_RANK[r.calculation.review.status],
            -r.calculation.created_at.timestamp(),
            -r.calculation.calculation_id,
        )
    )
    return "review_rank,created_at_desc,id_desc"


# ---------------------------------------------------------------------------
# Echo + empty
# ---------------------------------------------------------------------------


def _filter_echo(request: SpeciesCalculationsSearchRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    for field in (
        "smiles",
        "inchi",
        "inchi_key",
        "formula",
        "charge",
        "multiplicity",
        "species_id",
        "species_ref",
        "species_entry_id",
        "species_entry_ref",
        "level_of_theory_id",
        "level_of_theory_ref",
        "method",
        "basis",
        "software",
        "workflow_tool",
    ):
        v = getattr(request, field)
        if v is not None:
            echo[field] = v
    if request.electronic_state_kind is not None:
        echo["electronic_state_kind"] = request.electronic_state_kind.value
    if request.species_entry_kind is not None:
        echo["species_entry_kind"] = request.species_entry_kind.value
    if request.calculation_type is not None:
        echo["calculation_type"] = request.calculation_type.value
    if request.scientific_origin is not None:
        echo["scientific_origin"] = request.scientific_origin.value
    if request.calculation_quality is not None:
        echo["calculation_quality"] = request.calculation_quality.value
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    if request.include_rejected_quality:
        echo["include_rejected_quality"] = True
    return echo


def _empty_response(
    request: SpeciesCalculationsSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificSpeciesCalculationsSearchResponse:
    sort_echo = {
        CalculationRanking.lowest_energy: "energy_hartree_asc_nulls_last,review_rank,created_at_desc,id_desc",
        CalculationRanking.latest: "created_at_desc,id_desc",
        CalculationRanking.earliest: "created_at_asc,id_asc",
    }.get(request.ranking, "review_rank,created_at_desc,id_desc")
    return ScientificSpeciesCalculationsSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            ranking=request.ranking,
            sort=sort_echo,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=ReviewStatusSummary(),
        records=[],
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=0,
            total=0,
        ),
    )
