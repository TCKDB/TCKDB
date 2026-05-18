"""Lightweight ORM factory helpers for scientific_read service tests.

These build up the minimum fixture data needed to exercise service-layer
behavior without going through the upload workflow. They commit nothing —
the per-test transaction in conftest rolls back at end of test.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationDependency,
    CalculationGeometryValidation,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSCFStability,
    CalculationSPResult,
)
from app.db.models.geometry import Geometry
from app.db.models.common import (
    AppUserRole,
    ArrheniusAUnits,
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConformerSelectionKind,
    KineticsModelKind,
    MoleculeKind,
    RecordReviewStatus,
    ReactionRole,
    SCFStabilityStatus,
    ScientificOriginKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
    SubmissionRecordType,
    ValidationStatus,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    ConformerSelection,
)
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionParticipant,
)
from app.db.models.record_review import RecordReview
from app.db.models.species import Species, SpeciesEntry
from app.db.models.thermo import Thermo, ThermoNASA, ThermoPoint
from app.db.models.common import (
    ScientificOriginKind as _ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
    TransitionStateEntryStatus,
)
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.transition_state import TransitionState, TransitionStateEntry

_INCHI_COUNTER = 0


def next_inchi_key(prefix: str = "ABCDEF") -> str:
    """Generate a unique-looking InChI key for tests (27 chars total)."""
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def make_species(
    session: Session,
    *,
    smiles: str,
    inchi_key: str | None = None,
    charge: int = 0,
    multiplicity: int = 1,
    kind: MoleculeKind = MoleculeKind.molecule,
) -> Species:
    """Create a Species row."""
    species = Species(
        kind=kind,
        smiles=smiles,
        inchi_key=inchi_key or next_inchi_key(),
        charge=charge,
        multiplicity=multiplicity,
        stereo_kind=StereoKind.achiral,
    )
    session.add(species)
    session.flush()
    return species


def make_species_entry(
    session: Session,
    species: Species,
    *,
    kind: StationaryPointKind = StationaryPointKind.minimum,
    electronic_state_kind: SpeciesEntryStateKind = SpeciesEntryStateKind.ground,
) -> SpeciesEntry:
    """Create a SpeciesEntry row attached to a Species."""
    entry = SpeciesEntry(
        species_id=species.id,
        kind=kind,
        electronic_state_kind=electronic_state_kind,
    )
    session.add(entry)
    session.flush()
    return entry


_TERMINAL_STATUSES = {
    RecordReviewStatus.approved,
    RecordReviewStatus.rejected,
    RecordReviewStatus.deprecated,
}

_REVIEWER_COUNTER = 0


def _ensure_reviewer(session: Session) -> int:
    """Create a curator AppUser for tests requiring a reviewed_by FK."""
    global _REVIEWER_COUNTER
    _REVIEWER_COUNTER += 1
    user = AppUser(
        username=f"sci_read_reviewer_{_REVIEWER_COUNTER}",
        role=AppUserRole.curator,
    )
    session.add(user)
    session.flush()
    return user.id


def set_review(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
    status: RecordReviewStatus,
    reviewed_by: int | None = None,
) -> RecordReview:
    """Set a polymorphic RecordReview row for a record.

    Terminal statuses (``approved``, ``rejected``, ``deprecated``) require
    a ``reviewed_by`` user — one is created on the fly if not supplied.
    """
    if status in _TERMINAL_STATUSES and reviewed_by is None:
        reviewed_by = _ensure_reviewer(session)

    review = RecordReview(
        record_type=record_type,
        record_id=record_id,
        status=status,
        reviewed_at=datetime.now(timezone.utc),
        reviewed_by=reviewed_by,
    )
    session.add(review)
    session.flush()
    return review


def make_lot(
    session: Session, *, method: str = "wb97xd", basis: str | None = "def2tzvp"
) -> LevelOfTheory:
    """Create a LevelOfTheory row."""
    import hashlib

    raw = f"{method}|{basis or ''}".encode()
    lot = LevelOfTheory(
        method=method,
        basis=basis,
        lot_hash=hashlib.sha256(raw).hexdigest(),
    )
    session.add(lot)
    session.flush()
    return lot


def make_calculation(
    session: Session,
    *,
    type: CalculationType = CalculationType.opt,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
    lot_id: int | None = None,
) -> Calculation:
    """Create a minimal Calculation row.

    Either ``species_entry_id`` or ``transition_state_entry_id`` should be
    provided in realistic data, but neither is enforced here — tests can
    override. ``quality`` defaults to ``CalculationQuality.raw`` per the schema.
    """
    calc = Calculation(
        type=type,
        species_entry_id=species_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        lot_id=lot_id,
    )
    session.add(calc)
    session.flush()
    return calc


def make_chem_reaction(
    session: Session,
    *,
    reactants: list[Species],
    products: list[Species],
    reversible: bool = True,
) -> ChemReaction:
    """Create a ChemReaction with participants."""
    reaction = ChemReaction(reversible=reversible)
    session.add(reaction)
    session.flush()

    for sp in reactants:
        session.add(
            ReactionParticipant(
                reaction_id=reaction.id,
                species_id=sp.id,
                role=ReactionRole.reactant,
                stoichiometry=1,
            )
        )
    for sp in products:
        session.add(
            ReactionParticipant(
                reaction_id=reaction.id,
                species_id=sp.id,
                role=ReactionRole.product,
                stoichiometry=1,
            )
        )
    session.flush()
    return reaction


def make_reaction_entry(
    session: Session,
    *,
    reaction: ChemReaction,
    reactant_entries: list[SpeciesEntry],
    product_entries: list[SpeciesEntry],
) -> ReactionEntry:
    """Create a ReactionEntry plus its structure participants."""
    entry = ReactionEntry(reaction_id=reaction.id)
    session.add(entry)
    session.flush()

    for idx, se in enumerate(reactant_entries, start=1):
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=se.id,
                role=ReactionRole.reactant,
                participant_index=idx,
            )
        )
    for idx, se in enumerate(product_entries, start=1):
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=se.id,
                role=ReactionRole.product,
                participant_index=idx,
            )
        )
    session.flush()
    return entry


def make_transition_state(
    session: Session,
    *,
    reaction_entry: ReactionEntry,
    label: str | None = None,
    note: str | None = None,
) -> TransitionState:
    """Create a TransitionState attached to *reaction_entry*."""
    ts = TransitionState(
        reaction_entry_id=reaction_entry.id,
        label=label,
        note=note,
    )
    session.add(ts)
    session.flush()
    return ts


def make_transition_state_entry(
    session: Session,
    *,
    transition_state: TransitionState,
    charge: int = 0,
    multiplicity: int = 2,
    status: TransitionStateEntryStatus = TransitionStateEntryStatus.optimized,
    unmapped_smiles: str | None = None,
) -> TransitionStateEntry:
    """Create a TransitionStateEntry attached to *transition_state*."""
    tse = TransitionStateEntry(
        transition_state_id=transition_state.id,
        charge=charge,
        multiplicity=multiplicity,
        status=status,
        unmapped_smiles=unmapped_smiles,
    )
    session.add(tse)
    session.flush()
    return tse


def make_kinetics(
    session: Session,
    *,
    reaction_entry: ReactionEntry,
    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed,
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius,
    a: float = 1.2e-12,
    a_units: ArrheniusAUnits = ArrheniusAUnits.cm3_molecule_s,
    n: float | None = 2.1,
    ea_kj_mol: float | None = 15.4,
    tmin_k: float | None = 300.0,
    tmax_k: float | None = 2000.0,
    tunneling_model: str | None = None,
) -> Kinetics:
    """Create a Kinetics row attached to a reaction entry."""
    k = Kinetics(
        reaction_entry_id=reaction_entry.id,
        scientific_origin=scientific_origin,
        model_kind=model_kind,
        a=a,
        a_units=a_units,
        n=n,
        ea_kj_mol=ea_kj_mol,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
        tunneling_model=tunneling_model,
    )
    session.add(k)
    session.flush()
    return k


def make_thermo_scalar(
    session: Session,
    *,
    species_entry: SpeciesEntry,
    h298_kj_mol: float = -12.3,
    s298_j_mol_k: float = 250.1,
    tmin_k: float | None = None,
    tmax_k: float | None = None,
    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed,
) -> Thermo:
    """Create a Thermo row with only scalar h298/s298 (no NASA, no points)."""
    t = Thermo(
        species_entry_id=species_entry.id,
        scientific_origin=scientific_origin,
        h298_kj_mol=h298_kj_mol,
        s298_j_mol_k=s298_j_mol_k,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
    )
    session.add(t)
    session.flush()
    return t


def attach_thermo_nasa(
    session: Session,
    *,
    thermo: Thermo,
    t_low: float = 200.0,
    t_mid: float = 1000.0,
    t_high: float = 6000.0,
) -> ThermoNASA:
    """Attach a ThermoNASA child to an existing Thermo row."""
    nasa = ThermoNASA(
        thermo_id=thermo.id,
        t_low=t_low,
        t_mid=t_mid,
        t_high=t_high,
        a1=3.5,
        a2=0.0,
        a3=0.0,
        a4=0.0,
        a5=0.0,
        a6=-1000.0,
        a7=4.0,
        b1=3.2,
        b2=0.0,
        b3=0.0,
        b4=0.0,
        b5=0.0,
        b6=-950.0,
        b7=5.0,
    )
    session.add(nasa)
    session.flush()
    return nasa


def attach_thermo_points(
    session: Session,
    *,
    thermo: Thermo,
    temperatures_k: list[float],
) -> list[ThermoPoint]:
    """Attach a list of ThermoPoint rows to an existing Thermo row."""
    rows = [
        ThermoPoint(thermo_id=thermo.id, temperature_k=t) for t in temperatures_k
    ]
    session.add_all(rows)
    session.flush()
    return rows


# ---------------------------------------------------------------------------
# Phase 7 helpers: calc results, conformer, artifacts, dependencies, geometry
# ---------------------------------------------------------------------------


_GEOM_HASH_COUNTER = 0


def _next_geom_hash() -> str:
    global _GEOM_HASH_COUNTER
    _GEOM_HASH_COUNTER += 1
    return f"{_GEOM_HASH_COUNTER:0>64}"


def make_geometry(
    session: Session, *, natoms: int = 3, xyz_text: str | None = None
) -> Geometry:
    """Create a minimal Geometry row."""
    g = Geometry(natoms=natoms, geom_hash=_next_geom_hash(), xyz_text=xyz_text)
    session.add(g)
    session.flush()
    return g


def attach_sp_result(
    session: Session,
    *,
    calculation: Calculation,
    electronic_energy_hartree: float,
) -> CalculationSPResult:
    """Attach a CalculationSPResult row to an existing SP calculation."""
    row = CalculationSPResult(
        calculation_id=calculation.id,
        electronic_energy_hartree=electronic_energy_hartree,
    )
    session.add(row)
    session.flush()
    return row


def attach_opt_result(
    session: Session,
    *,
    calculation: Calculation,
    final_energy_hartree: float | None = None,
    converged: bool = True,
) -> CalculationOptResult:
    """Attach a CalculationOptResult row to an existing opt calculation."""
    row = CalculationOptResult(
        calculation_id=calculation.id,
        final_energy_hartree=final_energy_hartree,
        converged=converged,
    )
    session.add(row)
    session.flush()
    return row


def attach_output_geometry(
    session: Session,
    *,
    calculation: Calculation,
    geometry: Geometry,
    role: CalculationGeometryRole = CalculationGeometryRole.final,
    output_order: int = 1,
) -> CalculationOutputGeometry:
    """Attach a CalculationOutputGeometry link with the given role."""
    row = CalculationOutputGeometry(
        calculation_id=calculation.id,
        geometry_id=geometry.id,
        output_order=output_order,
        role=role,
    )
    session.add(row)
    session.flush()
    return row


def attach_geometry_validation(
    session: Session,
    *,
    calculation: Calculation,
    status: ValidationStatus = ValidationStatus.passed,
    species_smiles: str = "C",
    is_isomorphic: bool = True,
) -> CalculationGeometryValidation:
    """Attach a CalculationGeometryValidation row.

    Required fields per the schema: species_smiles (text), is_isomorphic
    (bool). Tests only care about the validation_status surfacing through
    the read API, so defaults are deliberately minimal.
    """
    row = CalculationGeometryValidation(
        calculation_id=calculation.id,
        validation_status=status,
        species_smiles=species_smiles,
        is_isomorphic=is_isomorphic,
    )
    session.add(row)
    session.flush()
    return row


def attach_scf_stability(
    session: Session,
    *,
    calculation: Calculation,
    status: SCFStabilityStatus = SCFStabilityStatus.stable,
) -> CalculationSCFStability:
    """Attach a CalculationSCFStability row."""
    row = CalculationSCFStability(
        calculation_id=calculation.id,
        status=status,
    )
    session.add(row)
    session.flush()
    return row


def attach_artifact(
    session: Session,
    *,
    calculation: Calculation,
    kind: ArtifactKind = ArtifactKind.output_log,
    filename: str = "output.log",
    uri: str = "s3://bucket/output.log",
) -> CalculationArtifact:
    """Attach a CalculationArtifact row."""
    row = CalculationArtifact(
        calculation_id=calculation.id,
        kind=kind,
        uri=uri,
        filename=filename,
    )
    session.add(row)
    session.flush()
    return row


def attach_dependency(
    session: Session,
    *,
    parent: Calculation,
    child: Calculation,
    role: CalculationDependencyRole = CalculationDependencyRole.optimized_from,
) -> CalculationDependency:
    """Attach a CalculationDependency edge from parent → child."""
    row = CalculationDependency(
        parent_calculation_id=parent.id,
        child_calculation_id=child.id,
        dependency_role=role,
    )
    session.add(row)
    session.flush()
    return row


def make_conformer_group(
    session: Session, species_entry, *, label: str | None = None
) -> ConformerGroup:
    """Create a ConformerGroup row attached to a species entry."""
    g = ConformerGroup(species_entry_id=species_entry.id, label=label)
    session.add(g)
    session.flush()
    return g


def make_conformer_observation(
    session: Session,
    *,
    conformer_group: ConformerGroup,
    torsion_fingerprint_json: dict | None = None,
) -> ConformerObservation:
    """Create a ConformerObservation row attached to a conformer group."""
    o = ConformerObservation(
        conformer_group_id=conformer_group.id,
        torsion_fingerprint_json=torsion_fingerprint_json,
    )
    session.add(o)
    session.flush()
    return o


def attach_conformer_selection(
    session: Session,
    *,
    conformer_group: ConformerGroup,
    selection_kind: ConformerSelectionKind = ConformerSelectionKind.lowest_energy,
) -> ConformerSelection:
    """Attach a ConformerSelection row to a conformer group."""
    row = ConformerSelection(
        conformer_group_id=conformer_group.id,
        selection_kind=selection_kind,
    )
    session.add(row)
    session.flush()
    return row


def make_calculation_with_conformer(
    session: Session,
    *,
    species_entry,
    conformer_observation: ConformerObservation,
    type=CalculationType.sp,
    lot_id: int | None = None,
):
    """Create a Calculation row tied to a conformer observation."""
    calc = Calculation(
        type=type,
        species_entry_id=species_entry.id,
        lot_id=lot_id,
        conformer_observation_id=conformer_observation.id,
    )
    session.add(calc)
    session.flush()
    return calc


def make_statmech(
    session: Session,
    *,
    species_entry,
    scientific_origin: _ScientificOriginKind = _ScientificOriginKind.computed,
    external_symmetry: int | None = 1,
    point_group: str | None = "C2v",
    is_linear: bool | None = False,
    statmech_treatment: StatmechTreatmentKind | None = StatmechTreatmentKind.rrho,
    frequency_scale_factor_id: int | None = None,
    software_release_id: int | None = None,
    workflow_tool_release_id: int | None = None,
    literature_id: int | None = None,
    note: str | None = None,
) -> Statmech:
    """Create a Statmech row attached to a species entry."""
    sm = Statmech(
        species_entry_id=species_entry.id,
        scientific_origin=scientific_origin,
        external_symmetry=external_symmetry,
        point_group=point_group,
        is_linear=is_linear,
        statmech_treatment=statmech_treatment,
        frequency_scale_factor_id=frequency_scale_factor_id,
        software_release_id=software_release_id,
        workflow_tool_release_id=workflow_tool_release_id,
        literature_id=literature_id,
        note=note,
    )
    session.add(sm)
    session.flush()
    return sm


def attach_statmech_source_calculation(
    session: Session,
    *,
    statmech: Statmech,
    calculation,
    role: StatmechCalculationRole = StatmechCalculationRole.freq,
) -> StatmechSourceCalculation:
    """Link a Calculation to a Statmech with a given role."""
    row = StatmechSourceCalculation(
        statmech_id=statmech.id,
        calculation_id=calculation.id,
        role=role,
    )
    session.add(row)
    session.flush()
    return row


def attach_statmech_torsion(
    session: Session,
    *,
    statmech: Statmech,
    torsion_index: int = 1,
    treatment_kind: TorsionTreatmentKind | None = TorsionTreatmentKind.hindered_rotor,
    dimension: int = 1,
    symmetry_number: int | None = 1,
    source_scan_calculation=None,
    atoms: tuple[int, int, int, int] | None = (1, 2, 3, 4),
    note: str | None = None,
) -> StatmechTorsion:
    """Create a StatmechTorsion + optional torsion definition row."""
    row = StatmechTorsion(
        statmech_id=statmech.id,
        torsion_index=torsion_index,
        treatment_kind=treatment_kind,
        dimension=dimension,
        symmetry_number=symmetry_number,
        source_scan_calculation_id=(
            source_scan_calculation.id if source_scan_calculation is not None else None
        ),
        note=note,
    )
    session.add(row)
    session.flush()
    if atoms is not None:
        a1, a2, a3, a4 = atoms
        session.add(
            StatmechTorsionDefinition(
                torsion_id=row.id,
                coordinate_index=1,
                atom1_index=a1,
                atom2_index=a2,
                atom3_index=a3,
                atom4_index=a4,
            )
        )
        session.flush()
    return row
