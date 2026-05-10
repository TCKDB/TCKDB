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
3. Bulk-load the supporting blocks (energies, geometries, conformer
   context, validation/SCF, artifacts presence, dependencies) keyed by
   the candidate calculation IDs.
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

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationDependency,
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
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    ConformerSelection,
    Species,
    SpeciesEntry,
)
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    LevelOfTheorySummary,
    Pagination,
    RecordReviewBadge,
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
    GeometryBlock,
    RequestEcho,
    ScientificSpeciesCalculationsSearchResponse,
    SpeciesCalculationsSearchRecord,
    SpeciesCalculationsSearchRequest,
    SpeciesCalculationsSpeciesContext,
    ValidationBlock,
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
from app.services.scientific_read.species import search_species

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "provenance",
    "calculations",
    "artifacts",
    "review",
    "conformers",
    "geometry",
    "validation",
    "scf_stability",
    "all",
}

_LOWEST_ENERGY_LEGAL_TYPES: set[CalculationType] = {
    CalculationType.sp,
    CalculationType.opt,
}


@dataclass
class _CalcRow:
    """Raw join result we then turn into a Pydantic record."""

    calc_id: int
    calc_type: CalculationType
    quality: CalculationQuality
    created_at: object
    species_entry_id: int
    lot_id: int | None
    lot_method: str | None
    lot_basis: str | None
    lot_dispersion: str | None
    lot_solvent: str | None
    software_release_id: int | None
    software_name: str | None
    software_version: str | None
    workflow_tool_release_id: int | None
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
    )
    _validate_ranking(request)

    # Resolve the candidate species_entry_ids that match the identity portion
    # of the request. Three paths: explicit species_entry_id handle (404 if
    # missing), explicit species_id handle (entries of that species), or
    # chemistry-first via the existing search_species service.
    entry_id_to_species_context = _resolve_species_entry_context(
        session, request
    )
    if not entry_id_to_species_context:
        return _empty_response(request, includes, offset, limit)

    # Pull candidate calculations with all the simple column filters applied
    # at the SQL level so the candidate set stays tight.
    rows = _query_candidate_calculations(
        session, request, list(entry_id_to_species_context.keys())
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

    # Build response records.
    records: list[SpeciesCalculationsSearchRecord] = []
    for r in rows:
        species_ctx = entry_id_to_species_context[r.species_entry_id]
        records.append(
            SpeciesCalculationsSearchRecord(
                species=species_ctx,
                calculation=CalculationCoreBlock(
                    calculation_id=r.calc_id,
                    calculation_type=r.calc_type,
                    calculation_quality=r.quality,
                    created_at=r.created_at,
                    review=badges[r.calc_id],
                ),
                energy=_build_energy_block(r),
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
                    supporting_calculation_ids=dependencies_by_child.get(
                        r.calc_id, []
                    ),
                    submission_id=None,
                    artifacts_available=r.calc_id in artifacts_present,
                ),
            )
        )

    summary = review_summary(badges[r.calc_id] for r in rows)
    sort_echo = _apply_ranking_sort(records, request)

    pre_collapse_total = len(records)
    collapse_first = request.collapse.value == "first"
    if collapse_first:
        returned = records[:1]
    else:
        returned = records[offset : offset + limit]

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
        ),
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_ranking(request: SpeciesCalculationsSearchRequest) -> None:
    """``ranking=lowest_energy`` requires ``calculation_type`` in {sp, opt}."""
    if request.ranking != CalculationRanking.lowest_energy:
        return
    if request.calculation_type not in _LOWEST_ENERGY_LEGAL_TYPES:
        raise ValueError(
            "unsupported_ranking_for_calculation_type: ranking=lowest_energy "
            "requires calculation_type=sp or calculation_type=opt."
        )


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


def _resolve_species_entry_context(
    session: Session, request: SpeciesCalculationsSearchRequest
) -> dict[int, SpeciesCalculationsSpeciesContext]:
    """Return mapping from species_entry_id → SpeciesCalculationsSpeciesContext."""

    # Path 1: explicit species_entry_id handle (404 if not found).
    if request.species_entry_id is not None:
        entry = session.get(SpeciesEntry, request.species_entry_id)
        if entry is None:
            raise NotFoundError(
                f"species_entry not found (species_entry_id={request.species_entry_id})"
            )
        species = session.get(Species, entry.species_id)
        if species is None:  # pragma: no cover — referential integrity
            raise NotFoundError("species_entry references a missing species row")
        return {
            entry.id: _species_context_from_orm(species, entry),
        }

    # Path 2: explicit species_id handle (404 if not found).
    if request.species_id is not None:
        species = session.get(Species, request.species_id)
        if species is None:
            raise NotFoundError(
                f"species not found (species_id={request.species_id})"
            )
        entries = list(species.entries)
        return {
            e.id: _species_context_from_orm(species, e) for e in entries
        }

    # Path 3: chemistry-first via search_species. Require at least one
    # identifier (matching Phase 6 search_thermo behavior).
    if not any(
        v is not None
        for v in (request.smiles, request.inchi, request.inchi_key, request.formula)
    ):
        raise ValueError(
            "missing_identifier: at least one of {smiles, inchi, inchi_key, "
            "formula, species_id, species_entry_id} is required."
        )

    species_request = SpeciesSearchRequest(
        smiles=request.smiles,
        inchi=request.inchi,
        inchi_key=request.inchi_key,
        formula=request.formula,
        charge=request.charge,
        multiplicity=request.multiplicity,
        electronic_state_kind=request.electronic_state_kind,
        species_entry_kind=request.species_entry_kind,
        # Don't push min_review_status here — calc-level review is shallow per D7.
        min_review_status=None,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
        offset=0,
        limit=200,
        collapse=request.collapse,
        include=[],
    )
    species_resp = search_species(session, species_request)

    out: dict[int, SpeciesCalculationsSpeciesContext] = {}
    for sp_record in species_resp.records:
        for entry in sp_record.entries:
            out[entry.species_entry_id] = SpeciesCalculationsSpeciesContext(
                species_id=sp_record.species_id,
                species_entry_id=entry.species_entry_id,
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
        species_entry_id=entry.id,
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
) -> list[_CalcRow]:
    """Pull calculations for the resolved species_entries with column filters."""
    stmt = (
        select(
            Calculation.id,
            Calculation.type,
            Calculation.quality,
            Calculation.created_at,
            Calculation.species_entry_id,
            Calculation.lot_id,
            LevelOfTheory.method,
            LevelOfTheory.basis,
            LevelOfTheory.dispersion,
            LevelOfTheory.solvent,
            Calculation.software_release_id,
            Software.name,
            SoftwareRelease.version,
            Calculation.workflow_tool_release_id,
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
    if request.level_of_theory_id is not None:
        stmt = stmt.where(Calculation.lot_id == request.level_of_theory_id)
    if request.method is not None:
        stmt = stmt.where(LevelOfTheory.method == request.method)
    if request.basis is not None:
        stmt = stmt.where(LevelOfTheory.basis == request.basis)
    if request.software is not None:
        stmt = stmt.where(Software.name == request.software)
    if request.workflow_tool is not None:
        stmt = stmt.where(WorkflowTool.name == request.workflow_tool)
    # scientific_origin: Calculation rows are computed by definition; the
    # filter is provided for parity with other endpoints but will only match
    # if a populated equivalent column exists. Calculation has no
    # scientific_origin column, so this filter is a no-op in v0 (parity
    # only). Future schema work could add it.

    # CalculationQuality filter (separate from review's "rejected").
    if request.calculation_quality is not None:
        stmt = stmt.where(Calculation.quality == request.calculation_quality)
    elif not request.include_rejected_quality:
        stmt = stmt.where(Calculation.quality != CalculationQuality.rejected)

    raw_rows = session.execute(stmt).all()

    out: list[_CalcRow] = []
    for row in raw_rows:
        sp_energy = row[17]
        opt_energy = row[18]
        # Pick the per-type energy if the calculation type matches; this
        # is how lowest_energy ranking picks a column without a SQL CASE.
        if row[1] == CalculationType.sp and sp_energy is not None:
            energy_hartree, energy_kind = sp_energy, "electronic_energy"
        elif row[1] == CalculationType.opt and opt_energy is not None:
            energy_hartree, energy_kind = opt_energy, "final_energy"
        elif row[1] == CalculationType.sp:
            energy_hartree, energy_kind = None, "electronic_energy"
        elif row[1] == CalculationType.opt:
            energy_hartree, energy_kind = None, "final_energy"
        else:
            energy_hartree, energy_kind = None, None

        out.append(
            _CalcRow(
                calc_id=row[0],
                calc_type=row[1],
                quality=row[2],
                created_at=row[3],
                species_entry_id=row[4],
                lot_id=row[5],
                lot_method=row[6],
                lot_basis=row[7],
                lot_dispersion=row[8],
                lot_solvent=row[9],
                software_release_id=row[10],
                software_name=row[11],
                software_version=row[12],
                workflow_tool_release_id=row[13],
                workflow_tool_name=row[14],
                workflow_tool_version=row[15],
                conformer_observation_id=row[16],
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
        out[cid] = GeometryBlock(
            primary_output_geometry_id=primary_id,
            primary_output_geometry_role=primary_role,
            input_geometry_ids=inputs_by_calc.get(cid, []),
            output_geometry_ids=[g for g, _ in outputs],
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
        ).where(CalculationGeometryValidation.calculation_id.in_(calc_ids))
    ).all()
    return {
        cid: ValidationSummary(status=status.value, calculation_id=cid)
        for cid, status in rows
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
        ).where(CalculationSCFStability.calculation_id.in_(calc_ids))
    ).all()
    return {
        cid: SCFStabilitySummary(status=status.value, calculation_id=cid)
        for cid, status in rows
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
) -> dict[int, list[int]]:
    """Return for each calc_id the list of parent calculation ids that
    point at it via ``CalculationDependency``.

    Spec field ``provenance.supporting_calculation_ids`` is the set of
    calcs that *support* this one — i.e. parent calcs in dependency edges
    where this calc is the child.
    """
    if not calc_ids:
        return {}
    rows = session.execute(
        select(
            CalculationDependency.child_calculation_id,
            CalculationDependency.parent_calculation_id,
        ).where(CalculationDependency.child_calculation_id.in_(calc_ids))
    ).all()
    out: dict[int, list[int]] = defaultdict(list)
    for child_id, parent_id in rows:
        out[child_id].append(parent_id)
    # Deterministic order so test snapshots stay stable.
    for k in out:
        out[k].sort()
    return out


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
            ConformerObservation.conformer_group_id,
            ConformerObservation.assignment_scheme_id,
            ConformerObservation.torsion_fingerprint_json,
        ).where(ConformerObservation.id.in_(obs_ids))
    ).all()

    group_ids = {row[1] for row in obs_rows}
    group_label_by_id: dict[int, str | None] = {}
    if group_ids:
        for gid, label in session.execute(
            select(ConformerGroup.id, ConformerGroup.label).where(
                ConformerGroup.id.in_(group_ids)
            )
        ).all():
            group_label_by_id[gid] = label

    selections_by_group: dict[int, list[str]] = defaultdict(list)
    if group_ids:
        for gid, kind in session.execute(
            select(ConformerSelection.conformer_group_id, ConformerSelection.selection_kind).where(
                ConformerSelection.conformer_group_id.in_(group_ids)
            )
        ).all():
            selections_by_group[gid].append(kind)

    out: dict[int, ConformerContextBlock] = {}
    for obs_id, group_id, scheme_id, fingerprint in obs_rows:
        out[obs_id] = ConformerContextBlock(
            conformer_observation_id=obs_id,
            conformer_group_id=group_id,
            conformer_assignment_scheme_id=scheme_id,
            conformer_group_label=group_label_by_id.get(group_id),
            torsion_fingerprint_json={"present": fingerprint is not None},
            selection_kinds=selections_by_group.get(group_id, []),
        )
    return out


# ---------------------------------------------------------------------------
# Per-row builders
# ---------------------------------------------------------------------------


def _build_energy_block(row: _CalcRow) -> CalculationEnergyBlock | None:
    if row.energy_kind is None:
        return None
    return CalculationEnergyBlock(
        energy_hartree=row.energy_hartree, energy_kind=row.energy_kind
    )


def _lot_summary_from_row(row: _CalcRow) -> LevelOfTheorySummary | None:
    if row.lot_id is None:
        return None
    label_parts = [row.lot_method or ""]
    if row.lot_basis:
        label_parts.append(row.lot_basis)
    return LevelOfTheorySummary(
        level_of_theory_id=row.lot_id,
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
        "species_entry_id",
        "level_of_theory_id",
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
        pagination=Pagination(offset=offset, limit=limit, returned=0, total=0),
    )
