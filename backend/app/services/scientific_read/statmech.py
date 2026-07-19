"""Service implementations for the scientific statmech read surface.

One detail surface today; search ships in a sibling module:

- ``GET /scientific/statmech/{ref_or_id}`` — one statmech record.

Statmech rows attach at the **species_entry** level (direct FK), not
at the conformer level. Frequencies live on ``calc_freq_result`` of
the source freq calculation(s), not on the statmech row itself —
``include=frequencies`` therefore surfaces a list of source freq
calculation refs plus the resolved scaling factor; full mode arrays
remain behind ``GET /scientific/calculations/{ref}``.

See ``backend/docs/specs/scientific_statmech_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
)
from app.db.models.energy_correction import FrequencyScaleFactor
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import ConformerGroup, Species, SpeciesEntry
from app.db.models.statmech import (
    Statmech,
    StatmechElectronicLevel,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_statmech import (
    AvailableStatmechSections,
    RequestEcho,
    ScientificStatmechDetailResponse,
    ScientificStatmechRecord,
    StatmechConformerContextItem,
    StatmechCoreBlock,
    StatmechElectronicLevelSummary,
    StatmechEvidenceSummary,
    StatmechFrequenciesSummary,
    StatmechFrequencyScaleFactorSummary,
    StatmechReviewEntry,
    StatmechSourceCalculationSummary,
    StatmechSpeciesContext,
    StatmechTorsionCoordinateSummary,
    StatmechTorsionSummary,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_summary,
    validate_includes,
)
from app.services.scientific_read.handles import resolve_statmech_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.trust import (
    TrustFragment,
    build_trust_fragment,
    evaluate_loaded_statmech,
)

# ---------------------------------------------------------------------------
# Include policy
# ---------------------------------------------------------------------------


# Public include tokens shared by the search and species-entry surfaces.
# ``trust`` is deliberately **absent** here: search/list endpoints never
# expose trust, so a caller passing ``include=trust`` to
# ``/scientific/statmech/search`` gets a 422 ``unknown_include_token`` and
# ``include=all`` cannot expand to it.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "source_calculations",
    "torsions",
    "electronic_levels",
    "frequencies",
    "conformers",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

# ``trust`` is legal only on the trust-bearing surfaces: the standalone
# statmech detail endpoint and the species-entry statmech subresource. Like
# ``internal_ids``, it is internal-tokenized on those surfaces so
# ``include=all`` does not pull it in — callers must opt in with
# ``include=trust`` explicitly. The narrower ``_LEGAL_INCLUDE_TOKENS`` set
# used by search keeps trust out of broad list/search responses entirely.
_DETAIL_LEGAL_INCLUDE_TOKENS: set[str] = _LEGAL_INCLUDE_TOKENS | {"trust"}
_TRUST_EAGER_LOADS = (
    selectinload(Statmech.species_entry),
    selectinload(Statmech.frequency_scale_factor),
    selectinload(Statmech.torsions).selectinload(StatmechTorsion.coordinates),
    selectinload(Statmech.torsions)
    .selectinload(StatmechTorsion.source_scan_calculation)
    .selectinload(Calculation.artifacts),
    selectinload(Statmech.torsions)
    .selectinload(StatmechTorsion.source_scan_calculation)
    .selectinload(Calculation.geometry_validation),
    selectinload(Statmech.torsions)
    .selectinload(StatmechTorsion.source_scan_calculation)
    .selectinload(Calculation.scan_result),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.lot),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.software_release),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.workflow_tool_release),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.artifacts),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.geometry_validation),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.scf_stability),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.sp_result),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.opt_result),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.freq_result),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.irc_result),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.scan_result),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.path_search_result),
    selectinload(Statmech.source_calculations)
    .selectinload(StatmechSourceCalculation.calculation)
    .selectinload(Calculation.child_dependencies),
)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_statmech(
    session: Session,
    *,
    statmech_handle: str,
    include: list[str] | None = None,
) -> ScientificStatmechDetailResponse:
    """Resolve a statmech handle and return its scientific projection.

    Path-handle semantics match the rest of the scientific surface:

    - Integer string: SELECT by id.
    - Public ref ``sm_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.

    Default response carries the statmech core block + species
    context + optional frequency-scale-factor / software / workflow /
    literature provenance pointers + bounded evidence and
    available_sections summaries. Heavy include blocks
    (``source_calculations`` / ``torsions`` / ``electronic_levels`` /
    ``frequencies`` / ``conformers`` / ``review``) populate only when
    the caller opts in.
    """
    includes = validate_includes(
        include or [],
        _DETAIL_LEGAL_INCLUDE_TOKENS,
        "/scientific/statmech/{statmech_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS | {"trust"},
    )
    includes = filter_internal_ids_from_resolved(includes)

    sm_id = resolve_statmech_handle(session, statmech_handle)
    if "trust" in includes:
        sm = session.scalars(
            select(Statmech)
            .where(Statmech.id == sm_id)
            .options(*_TRUST_EAGER_LOADS)
        ).one_or_none()
    else:
        sm = session.get(Statmech, sm_id)
    if sm is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"statmech not found (statmech_id={sm_id})",
            code="handle_not_found",
        )

    badge = _load_review_badge(session, sm.id)
    record = build_statmech_record(
        session, sm=sm, badge=badge, includes=includes
    )

    return ScientificStatmechDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([badge]),
        record=record,
    )


# ---------------------------------------------------------------------------
# Shared per-record builder (reused by search)
# ---------------------------------------------------------------------------


def build_statmech_record(
    session: Session,
    *,
    sm: Statmech,
    badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificStatmechRecord:
    """Project one Statmech row into the public scientific record shape.

    Exported so the statmech search service can produce records with
    the same shape as the detail endpoint. Caller passes the resolved
    include set (post-`validate_includes`, post-Phase-D).
    """
    species_context = _build_species_context(session, sm.species_entry_id)
    fsf_summary = _build_fsf_summary(session, sm.frequency_scale_factor_id)
    fsf_value = fsf_summary.value if fsf_summary is not None else None

    sw_summary = _build_software_summary(session, sm.software_release_id)
    wf_summary = _build_workflow_summary(session, sm.workflow_tool_release_id)
    lit_summary = _build_literature_summary(session, sm.literature_id)

    source_rows = _load_source_rows(session, sm.id)
    torsion_rows = _load_torsion_rows(session, sm.id)
    electronic_level_rows = _load_electronic_level_rows(session, sm.id)
    has_conformer_context = bool(
        session.scalar(
            select(
                exists().where(
                    ConformerGroup.species_entry_id == sm.species_entry_id
                )
            )
        )
    )

    evidence = _build_evidence_summary(
        source_rows=source_rows,
        torsion_rows=torsion_rows,
        has_frequency_scale_factor=fsf_summary is not None,
        has_conformer_context=has_conformer_context,
    )
    available = AvailableStatmechSections(
        has_source_calculations=bool(source_rows),
        has_torsions=bool(torsion_rows),
        has_electronic_levels=bool(electronic_level_rows),
        has_frequencies=any(
            r.role == StatmechCalculationRole.freq for r in source_rows
        ),
        has_conformers=has_conformer_context,
        has_review=_exists_review_for(
            session, SubmissionRecordType.statmech, sm.id
        ),
    )

    core = StatmechCoreBlock(
        statmech_id=sm.id,
        statmech_ref=sm.public_ref,
        scientific_origin=sm.scientific_origin,
        statmech_treatment=sm.statmech_treatment,
        rigid_rotor_kind=sm.rigid_rotor_kind,
        point_group=sm.point_group,
        external_symmetry=sm.external_symmetry,
        is_linear=sm.is_linear,
        uses_projected_frequencies=sm.uses_projected_frequencies,
        optical_isomers=sm.optical_isomers,
        rotational_constant_a_cm1=sm.rotational_constant_a_cm1,
        rotational_constant_b_cm1=sm.rotational_constant_b_cm1,
        rotational_constant_c_cm1=sm.rotational_constant_c_cm1,
        frequency_scale_factor_value=fsf_value,
        note=sm.note,
        created_at=sm.created_at,
        review=badge,
    )

    source_block: list[StatmechSourceCalculationSummary] | None = None
    if "source_calculations" in includes:
        source_block = _build_source_calculations(session, source_rows)

    torsions_block: list[StatmechTorsionSummary] | None = None
    if "torsions" in includes:
        torsions_block = _build_torsions(session, torsion_rows)

    electronic_levels_block: list[StatmechElectronicLevelSummary] | None = None
    if "electronic_levels" in includes:
        electronic_levels_block = _build_electronic_levels(electronic_level_rows)

    frequencies_block: StatmechFrequenciesSummary | None = None
    if "frequencies" in includes:
        frequencies_block = _build_frequencies_summary(
            session, source_rows, fsf_value=fsf_value
        )

    conformers_block: list[StatmechConformerContextItem] | None = None
    if "conformers" in includes:
        conformers_block = _build_conformer_context(
            session, sm.species_entry_id
        )

    review_block: list[StatmechReviewEntry] | None = None
    if "review" in includes:
        review_block = _build_review_history(session, sm.id)

    trust_block: TrustFragment | None = None
    if "trust" in includes:
        trust_block = build_statmech_trust_fragment(
            sm,
            review_status=badge.status,
        )

    return ScientificStatmechRecord(
        statmech=core,
        species=species_context,
        frequency_scale_factor=fsf_summary,
        software_release=sw_summary,
        workflow_tool_release=wf_summary,
        literature=lit_summary,
        evidence_summary=evidence,
        available_sections=available,
        source_calculations=source_block,
        torsions=torsions_block,
        electronic_levels=electronic_levels_block,
        frequencies=frequencies_block,
        conformers=conformers_block,
        review_history=review_block,
        trust=trust_block,
    )


def build_statmech_trust_fragment(
    statmech: Statmech,
    review_status: RecordReviewStatus | None = None,
) -> TrustFragment:
    """Build the read-layer trust fragment for a statmech record."""
    evaluation = evaluate_loaded_statmech(statmech)
    return build_trust_fragment(evaluation, review_status=review_status)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_source_rows(
    session: Session, statmech_id: int
) -> list[StatmechSourceCalculation]:
    return session.scalars(
        select(StatmechSourceCalculation)
        .where(StatmechSourceCalculation.statmech_id == statmech_id)
        .order_by(
            StatmechSourceCalculation.role.asc(),
            StatmechSourceCalculation.calculation_id.asc(),
        )
    ).all()


def _load_torsion_rows(
    session: Session, statmech_id: int
) -> list[StatmechTorsion]:
    return session.scalars(
        select(StatmechTorsion)
        .where(StatmechTorsion.statmech_id == statmech_id)
        .order_by(StatmechTorsion.torsion_index.asc())
    ).all()


def _load_electronic_level_rows(
    session: Session, statmech_id: int
) -> list[StatmechElectronicLevel]:
    return session.scalars(
        select(StatmechElectronicLevel)
        .where(StatmechElectronicLevel.statmech_id == statmech_id)
        .order_by(StatmechElectronicLevel.level_index.asc())
    ).all()


def _exists_review_for(
    session: Session,
    record_type: SubmissionRecordType,
    record_id: int,
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    and_(
                        RecordReview.record_type == record_type,
                        RecordReview.record_id == record_id,
                    )
                )
            )
        )
    )


# ---------------------------------------------------------------------------
# Species context
# ---------------------------------------------------------------------------


def _build_species_context(
    session: Session, species_entry_id: int
) -> StatmechSpeciesContext:
    row = session.execute(
        select(
            SpeciesEntry.id.label("entry_id"),
            SpeciesEntry.public_ref.label("entry_ref"),
            Species.id.label("species_id"),
            Species.public_ref.label("species_ref"),
            Species.smiles.label("smiles"),
            Species.inchi_key.label("inchi_key"),
            Species.charge.label("charge"),
            Species.multiplicity.label("multiplicity"),
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.id == species_entry_id)
    ).one_or_none()
    if row is None:  # pragma: no cover — FK guarantees existence
        return StatmechSpeciesContext(species_ref="", species_entry_ref="")
    return StatmechSpeciesContext(
        species_id=row.species_id,
        species_ref=row.species_ref,
        species_entry_id=row.entry_id,
        species_entry_ref=row.entry_ref,
        canonical_smiles=row.smiles,
        inchi_key=row.inchi_key,
        charge=row.charge,
        multiplicity=row.multiplicity,
    )


# ---------------------------------------------------------------------------
# FSF / provenance summaries
# ---------------------------------------------------------------------------


def _build_fsf_summary(
    session: Session, frequency_scale_factor_id: int | None
) -> StatmechFrequencyScaleFactorSummary | None:
    if frequency_scale_factor_id is None:
        return None
    fsf = session.get(FrequencyScaleFactor, frequency_scale_factor_id)
    if fsf is None:
        return None
    return StatmechFrequencyScaleFactorSummary(
        frequency_scale_factor_id=fsf.id,
        frequency_scale_factor_ref=fsf.public_ref,
        value=fsf.value,
        scale_kind=(
            fsf.scale_kind.value
            if hasattr(fsf.scale_kind, "value")
            else str(fsf.scale_kind)
        ),
        level_of_theory=_build_lot_summary(session, fsf.level_of_theory_id),
        software=_build_software_for_software_id(session, fsf.software_id),
        source_literature=_build_literature_summary(
            session, fsf.source_literature_id
        ),
    )


def _build_lot_summary(
    session: Session, lot_id: int | None
) -> LevelOfTheorySummary | None:
    if lot_id is None:
        return None
    lot = session.get(LevelOfTheory, lot_id)
    if lot is None:
        return None
    return LevelOfTheorySummary(
        level_of_theory_id=lot.id,
        level_of_theory_ref=lot.public_ref,
        method=lot.method,
        basis=lot.basis,
        dispersion=lot.dispersion,
        solvent=lot.solvent,
        label=None,
    )


def _build_software_summary(
    session: Session, software_release_id: int | None
) -> SoftwareReleaseSummary | None:
    if software_release_id is None:
        return None
    row = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id == software_release_id)
    ).one_or_none()
    if row is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=row.id,
        software_release_ref=row.public_ref,
        software=row.name,
        version=row.version,
    )


def _build_software_for_software_id(
    session: Session, software_id: int | None
) -> SoftwareReleaseSummary | None:
    """Project a bare ``software`` row (no release) into a
    SoftwareReleaseSummary so the FSF summary can carry it without a
    second schema. ``software_release_id`` and ``version`` are left
    null when the FSF only carries the software dimension and not a
    specific release.
    """
    if software_id is None:
        return None
    sw = session.get(Software, software_id)
    if sw is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=0,  # placeholder; FSF doesn't reference a release
        software_release_ref="",
        software=sw.name,
        version=None,
    )


def _build_workflow_summary(
    session: Session, workflow_tool_release_id: int | None
) -> WorkflowToolReleaseSummary | None:
    if workflow_tool_release_id is None:
        return None
    row = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id == workflow_tool_release_id)
    ).one_or_none()
    if row is None:
        return None
    return WorkflowToolReleaseSummary(
        workflow_tool_release_id=row.id,
        workflow_tool_release_ref=row.public_ref,
        workflow_tool=row.name,
        version=row.version,
    )


def _build_literature_summary(
    session: Session, literature_id: int | None
) -> LiteratureSummary | None:
    if literature_id is None:
        return None
    lit = session.get(Literature, literature_id)
    if lit is None:
        return None
    return LiteratureSummary(
        id=lit.id,
        literature_ref=lit.public_ref,
        title=getattr(lit, "title", None),
        year=getattr(lit, "year", None),
        doi=getattr(lit, "doi", None),
    )


# ---------------------------------------------------------------------------
# Source-calculation summaries (include=source_calculations)
# ---------------------------------------------------------------------------


def _build_source_calculations(
    session: Session, source_rows: list[StatmechSourceCalculation]
) -> list[StatmechSourceCalculationSummary]:
    if not source_rows:
        return []
    calc_ids = [r.calculation_id for r in source_rows]
    calcs = session.scalars(
        select(Calculation).where(Calculation.id.in_(calc_ids))
    ).all()
    calc_by_id = {c.id: c for c in calcs}
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=calc_ids,
    )
    lot_ids = {c.lot_id for c in calcs if c.lot_id is not None}
    sw_ids = {
        c.software_release_id
        for c in calcs
        if c.software_release_id is not None
    }
    wf_ids = {
        c.workflow_tool_release_id
        for c in calcs
        if c.workflow_tool_release_id is not None
    }
    lot_by_id = _bulk_lot_summaries(session, lot_ids)
    sw_by_id = _bulk_software_summaries(session, sw_ids)
    wf_by_id = _bulk_workflow_summaries(session, wf_ids)

    out: list[StatmechSourceCalculationSummary] = []
    for r in source_rows:
        calc = calc_by_id.get(r.calculation_id)
        if calc is None:  # pragma: no cover — race with delete
            continue
        out.append(
            StatmechSourceCalculationSummary(
                role=r.role,
                calculation_id=calc.id,
                calculation_ref=calc.public_ref,
                calculation_type=calc.type,
                quality=(
                    calc.quality.value
                    if hasattr(calc.quality, "value")
                    else str(calc.quality)
                ),
                created_at=calc.created_at,
                review=badges.get(
                    calc.id,
                    RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
                ),
                level_of_theory=lot_by_id.get(calc.lot_id),
                software_release=sw_by_id.get(calc.software_release_id),
                workflow_tool_release=wf_by_id.get(
                    calc.workflow_tool_release_id
                ),
            )
        )
    return out


def _bulk_lot_summaries(
    session: Session, lot_ids: set[int]
) -> dict[int, LevelOfTheorySummary]:
    if not lot_ids:
        return {}
    rows = session.scalars(
        select(LevelOfTheory).where(LevelOfTheory.id.in_(lot_ids))
    ).all()
    return {
        lot.id: LevelOfTheorySummary(
            level_of_theory_id=lot.id,
            level_of_theory_ref=lot.public_ref,
            method=lot.method,
            basis=lot.basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            label=None,
        )
        for lot in rows
    }


def _bulk_software_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, SoftwareReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: SoftwareReleaseSummary(
            software_release_id=row.id,
            software_release_ref=row.public_ref,
            software=row.name,
            version=row.version,
        )
        for row in rows
    }


def _bulk_workflow_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, WorkflowToolReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: WorkflowToolReleaseSummary(
            workflow_tool_release_id=row.id,
            workflow_tool_release_ref=row.public_ref,
            workflow_tool=row.name,
            version=row.version,
        )
        for row in rows
    }


# ---------------------------------------------------------------------------
# Torsion summaries (include=torsions)
# ---------------------------------------------------------------------------


def _build_torsions(
    session: Session, torsion_rows: list[StatmechTorsion]
) -> list[StatmechTorsionSummary]:
    if not torsion_rows:
        return []
    torsion_ids = [t.id for t in torsion_rows]
    coord_rows = session.scalars(
        select(StatmechTorsionDefinition)
        .where(StatmechTorsionDefinition.torsion_id.in_(torsion_ids))
        .order_by(
            StatmechTorsionDefinition.torsion_id.asc(),
            StatmechTorsionDefinition.coordinate_index.asc(),
        )
    ).all()
    coords_by_torsion: dict[int, list[StatmechTorsionDefinition]] = {
        tid: [] for tid in torsion_ids
    }
    for c in coord_rows:
        coords_by_torsion.setdefault(c.torsion_id, []).append(c)

    scan_calc_ids = {
        t.source_scan_calculation_id
        for t in torsion_rows
        if t.source_scan_calculation_id is not None
    }
    scan_refs_by_id: dict[int, str] = {}
    if scan_calc_ids:
        rows = session.execute(
            select(Calculation.id, Calculation.public_ref).where(
                Calculation.id.in_(scan_calc_ids)
            )
        ).all()
        scan_refs_by_id = {row.id: row.public_ref for row in rows}

    out: list[StatmechTorsionSummary] = []
    for t in torsion_rows:
        out.append(
            StatmechTorsionSummary(
                torsion_index=t.torsion_index,
                treatment_kind=t.treatment_kind,
                symmetry_number=t.symmetry_number,
                dimension=t.dimension,
                top_description=t.top_description,
                invalidated_reason=t.invalidated_reason,
                note=t.note,
                source_scan_calculation_ref=(
                    scan_refs_by_id.get(t.source_scan_calculation_id)
                    if t.source_scan_calculation_id is not None
                    else None
                ),
                source_scan_calculation_id=t.source_scan_calculation_id,
                coordinates=[
                    StatmechTorsionCoordinateSummary(
                        coordinate_index=c.coordinate_index,
                        atom1_index=c.atom1_index,
                        atom2_index=c.atom2_index,
                        atom3_index=c.atom3_index,
                        atom4_index=c.atom4_index,
                    )
                    for c in coords_by_torsion.get(t.id, [])
                ],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Electronic-level summaries (include=electronic_levels)
# ---------------------------------------------------------------------------


def _build_electronic_levels(
    rows: list[StatmechElectronicLevel],
) -> list[StatmechElectronicLevelSummary]:
    return [
        StatmechElectronicLevelSummary(
            level_index=r.level_index,
            energy_cm1=r.energy_cm1,
            degeneracy=r.degeneracy,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Frequencies pointer (include=frequencies)
# ---------------------------------------------------------------------------


def _build_frequencies_summary(
    session: Session,
    source_rows: list[StatmechSourceCalculation],
    *,
    fsf_value: float | None,
) -> StatmechFrequenciesSummary:
    freq_calc_ids = [
        r.calculation_id
        for r in source_rows
        if r.role == StatmechCalculationRole.freq
    ]
    refs: list[str] = []
    if freq_calc_ids:
        rows = session.execute(
            select(Calculation.id, Calculation.public_ref)
            .where(Calculation.id.in_(freq_calc_ids))
            .order_by(Calculation.id.asc())
        ).all()
        refs = [row.public_ref for row in rows]
        ids = [row.id for row in rows]
    else:
        ids = []
    return StatmechFrequenciesSummary(
        source_freq_calculation_refs=refs,
        source_freq_calculation_ids=ids,
        frequency_scale_factor_value=fsf_value,
        note=(
            "Per-mode frequency arrays live on the source freq "
            "calculation(s); fetch the full array via "
            "/api/v1/scientific/calculations/{calculation_ref}?include=freq_modes."
        ),
    )


# ---------------------------------------------------------------------------
# Conformer-context (include=conformers)
# ---------------------------------------------------------------------------


def _build_conformer_context(
    session: Session, species_entry_id: int
) -> list[StatmechConformerContextItem]:
    rows = session.scalars(
        select(ConformerGroup)
        .where(ConformerGroup.species_entry_id == species_entry_id)
        .order_by(ConformerGroup.id.asc())
    ).all()
    return [
        StatmechConformerContextItem(
            conformer_group_id=cg.id,
            conformer_group_ref=cg.public_ref,
            label=cg.label,
        )
        for cg in rows
    ]


# ---------------------------------------------------------------------------
# Evidence summary
# ---------------------------------------------------------------------------


def _build_evidence_summary(
    *,
    source_rows: list[StatmechSourceCalculation],
    torsion_rows: list[StatmechTorsion],
    has_frequency_scale_factor: bool,
    has_conformer_context: bool,
) -> StatmechEvidenceSummary:
    roles = {r.role for r in source_rows}
    has_rotor_scans = any(
        t.source_scan_calculation_id is not None for t in torsion_rows
    )
    return StatmechEvidenceSummary(
        source_calculation_count=len(source_rows),
        has_opt_calculation=StatmechCalculationRole.opt in roles,
        has_freq_calculation=StatmechCalculationRole.freq in roles,
        has_sp_calculation=StatmechCalculationRole.sp in roles,
        has_rotor_scans=has_rotor_scans,
        torsion_count=len(torsion_rows),
        has_frequency_scale_factor=has_frequency_scale_factor,
        has_conformer_context=has_conformer_context,
    )


# ---------------------------------------------------------------------------
# Review history loader (include=review) + badge loader
# ---------------------------------------------------------------------------


def _build_review_history(
    session: Session, statmech_id: int
) -> list[StatmechReviewEntry]:
    rows = session.scalars(
        select(RecordReview)
        .where(
            RecordReview.record_type == SubmissionRecordType.statmech,
            RecordReview.record_id == statmech_id,
        )
        .order_by(RecordReview.reviewed_at.asc().nulls_last())
    ).all()
    return [
        StatmechReviewEntry(
            status=(
                row.status.value
                if hasattr(row.status, "value")
                else str(row.status)
            ),
            reviewed_at=row.reviewed_at,
            reviewed_by=row.reviewed_by,
            note=row.note,
        )
        for row in rows
    ]


def _load_review_badge(session: Session, statmech_id: int) -> RecordReviewBadge:
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.statmech,
        record_ids=[statmech_id],
    )
    return badges.get(
        statmech_id, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
    )


__all__ = [
    "_DETAIL_LEGAL_INCLUDE_TOKENS",
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "build_statmech_record",
    "get_statmech",
]
