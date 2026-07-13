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
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSCFStability,
    CalculationSPResult,
)
from app.db.models.common import (
    AppUserRole,
    ArrheniusAUnits,
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
    ConformerSelectionKind,
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    EnergyUnit,
    FrequencyScaleKind,
    HessianSource,
    KineticsModelKind,
    MeliusBacComponentKind,
    MoleculeKind,
    ReactionRole,
    RecordReviewStatus,
    SCFStabilityStatus,
    ScientificOriginKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    StereoKind,
    SubmissionRecordType,
    TorsionTreatmentKind,
    TransitionStateEntryStatus,
    TransportCalculationRole,
    ValidationStatus,
)
from app.db.models.common import (
    ScientificOriginKind as _ScientificOriginKind,
)
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    EnergyCorrectionSchemeComponentParam,
    FrequencyScaleFactor,
)
from app.db.models.geometry import Geometry, GeometryAtom
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionParticipant,
)
from app.db.models.record_review import RecordReview
from app.db.models.software import Software
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    ConformerSelection,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.thermo import Thermo, ThermoNASA, ThermoPoint
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport, TransportSourceCalculation
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease

_INCHI_COUNTER = 0


def next_inchi_key(prefix: str = "ABCDEF") -> str:
    """Generate a unique-looking InChI key for tests (27 chars total)."""
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


_SMILES_COUNTER = 0


def unique_smiles() -> str:
    """Return a distinct, RDKit-valid SMILES per call (linear alkane).

    Species identity is (smiles, charge, multiplicity) (DR-0031), so
    fixtures that need several *distinct* species — or that run against the
    shared session-scoped DB where other tests committed common SMILES —
    must not reuse a literal like ``"CCO"``. Callers that assert on a
    specific SMILES still pass one explicitly.
    """
    global _SMILES_COUNTER
    _SMILES_COUNTER += 1
    return "C" * _SMILES_COUNTER


def make_species(
    session: Session,
    *,
    smiles: str | None = None,
    inchi_key: str | None = None,
    charge: int = 0,
    multiplicity: int = 1,
    kind: MoleculeKind = MoleculeKind.molecule,
) -> Species:
    """Create a Species row. ``smiles`` defaults to a unique value."""
    species = Species(
        kind=kind,
        smiles=smiles if smiles is not None else unique_smiles(),
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
    """Create a SpeciesEntry row attached to a Species.

    Populates the RDKit cartridge ``mol`` column from the parent
    species's SMILES so structure-search tests exercise the same
    indexed path as production (the real resolver does the same on
    insert via :mod:`app.services.species_resolution`).

    Some tests use placeholder SMILES (e.g. ``"C1"``) that the
    cartridge cannot parse; for those we leave ``mol`` NULL — same as
    the migration backfill behavior and the structure-search service
    treats NULL ``mol`` rows as un-searchable.
    """
    from rdkit import Chem

    mol_value: str | None = None
    if species.smiles is not None:
        parsed = Chem.MolFromSmiles(species.smiles)
        if parsed is not None:
            mol_value = Chem.MolToSmiles(parsed, canonical=True)

    entry = SpeciesEntry(
        species_id=species.id,
        kind=kind,
        electronic_state_kind=electronic_state_kind,
        mol=mol_value,
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
    """Create or fetch a LevelOfTheory row.

    The ``lot_hash`` column is uniquely constrained; repeated calls with
    the same (method, basis) reuse the existing row so factory callers
    don't trip the constraint when they don't care which LOT they get.
    """
    import hashlib

    from sqlalchemy import select as _select

    raw = f"{method}|{basis or ''}".encode()
    lot_hash = hashlib.sha256(raw).hexdigest()
    existing = session.scalar(
        _select(LevelOfTheory).where(LevelOfTheory.lot_hash == lot_hash)
    )
    if existing is not None:
        return existing
    lot = LevelOfTheory(method=method, basis=basis, lot_hash=lot_hash)
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
    stoichiometry_hash: str | None = None,
) -> ChemReaction:
    """Get-or-create a ChemReaction with participants, keyed by ``stoichiometry_hash``.

    Behavior:

    - ``stoichiometry_hash`` is computed from participants by default
      (mirroring ``app.services.reaction_resolution``), so the public-ref
      listener takes the content-derived path.
    - If a ChemReaction with that hash already exists in the session, it is
      returned as-is. This mirrors the resolver's dedup contract and means
      successive calls with the same participants do not violate the
      ``stoichiometry_hash`` unique constraint.
    - To force a distinct row, pass distinct participants or supply an
      explicit ``stoichiometry_hash`` override.
    """
    from sqlalchemy import select as _select

    if stoichiometry_hash is None:
        from collections import Counter

        from app.services.reaction_resolution import reaction_stoichiometry_hash

        stoichiometry_hash = reaction_stoichiometry_hash(
            reversible=reversible,
            reactants=dict(Counter(sp.id for sp in reactants)),
            products=dict(Counter(sp.id for sp in products)),
        )

    existing = session.scalar(
        _select(ChemReaction).where(
            ChemReaction.stoichiometry_hash == stoichiometry_hash
        )
    )
    if existing is not None:
        return existing

    reaction = ChemReaction(
        reversible=reversible,
        stoichiometry_hash=stoichiometry_hash,
    )
    session.add(reaction)
    session.flush()

    # Collapse duplicates into stoichiometry (A + A -> stoichiometry=2),
    # mirroring the compressed participant contract of the resolver and
    # matching the Counter-based hash computed above.
    from collections import Counter as _Counter

    for sp_id, count in _Counter(sp.id for sp in reactants).items():
        session.add(
            ReactionParticipant(
                reaction_id=reaction.id,
                species_id=sp_id,
                role=ReactionRole.reactant,
                stoichiometry=count,
            )
        )
    for sp_id, count in _Counter(sp.id for sp in products).items():
        session.add(
            ReactionParticipant(
                reaction_id=reaction.id,
                species_id=sp_id,
                role=ReactionRole.product,
                stoichiometry=count,
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


def attach_geometry_atoms(
    session: Session,
    *,
    geometry: Geometry,
    symbols: list[str],
    coords: list[list[float]],
) -> list[GeometryAtom]:
    """Attach per-atom coordinate rows to a geometry (1-based atom_index)."""
    rows: list[GeometryAtom] = []
    for i, (sym, (x, y, z)) in enumerate(zip(symbols, coords, strict=True), start=1):
        atom = GeometryAtom(
            geometry_id=geometry.id,
            atom_index=i,
            element=sym,
            x=x,
            y=y,
            z=z,
        )
        session.add(atom)
        rows.append(atom)
    session.flush()
    return rows


def attach_input_geometry(
    session: Session,
    *,
    calculation: Calculation,
    geometry: Geometry,
    input_order: int = 1,
) -> CalculationInputGeometry:
    """Attach a CalculationInputGeometry link (for sp/freq calcs)."""
    row = CalculationInputGeometry(
        calculation_id=calculation.id,
        geometry_id=geometry.id,
        input_order=input_order,
    )
    session.add(row)
    session.flush()
    return row


def attach_freq_result(
    session: Session,
    *,
    calculation: Calculation,
    frequencies_cm1: list[float],
    zpe_hartree: float | None = None,
) -> CalculationFreqResult:
    """Attach a CalculationFreqResult plus its per-mode rows.

    Imaginary modes are supplied as negative wavenumbers; ``n_imag`` and
    ``imag_freq_cm1`` are derived from the sign, matching the schema's
    signed-frequency convention.
    """
    imag = [f for f in frequencies_cm1 if f < 0]
    result = CalculationFreqResult(
        calculation_id=calculation.id,
        n_imag=len(imag),
        imag_freq_cm1=imag[0] if imag else None,
        zpe_hartree=zpe_hartree,
    )
    session.add(result)
    for i, freq in enumerate(frequencies_cm1, start=1):
        session.add(
            CalculationFreqMode(
                calculation_id=calculation.id,
                mode_index=i,
                frequency_cm1=freq,
                is_imaginary=freq < 0,
            )
        )
    session.flush()
    return result


def attach_hessian(
    session: Session,
    *,
    calculation: Calculation,
    geometry: Geometry,
    natoms: int,
    source: HessianSource = HessianSource.parsed_log,
) -> CalculationHessian:
    """Attach a CalculationHessian with a correctly-sized zero lower triangle."""
    n = 3 * natoms
    lower_triangle = [0.0] * (n * (n + 1) // 2)
    row = CalculationHessian(
        calculation_id=calculation.id,
        geometry_id=geometry.id,
        natoms=natoms,
        lower_triangle_hartree_bohr2=lower_triangle,
        source=source,
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


def make_transport(
    session: Session,
    *,
    species_entry,
    scientific_origin: _ScientificOriginKind = _ScientificOriginKind.computed,
    sigma_angstrom: float | None = 3.5,
    epsilon_over_k_k: float | None = 200.0,
    dipole_debye: float | None = None,
    polarizability_angstrom3: float | None = None,
    rotational_relaxation: float | None = None,
    software_release_id: int | None = None,
    workflow_tool_release_id: int | None = None,
    literature_id: int | None = None,
    note: str | None = None,
) -> Transport:
    """Create a Transport row attached to a species entry.

    Defaults populate the LJ pair (sigma + epsilon_over_k_k) so the
    schema's ``lj_pair_both_or_neither`` constraint is satisfied. Pass
    both as ``None`` to create a transport row without LJ params.
    """
    tr = Transport(
        species_entry_id=species_entry.id,
        scientific_origin=scientific_origin,
        sigma_angstrom=sigma_angstrom,
        epsilon_over_k_k=epsilon_over_k_k,
        dipole_debye=dipole_debye,
        polarizability_angstrom3=polarizability_angstrom3,
        rotational_relaxation=rotational_relaxation,
        software_release_id=software_release_id,
        workflow_tool_release_id=workflow_tool_release_id,
        literature_id=literature_id,
        note=note,
    )
    session.add(tr)
    session.flush()
    return tr


def attach_transport_source_calculation(
    session: Session,
    *,
    transport: Transport,
    calculation,
    role: TransportCalculationRole = TransportCalculationRole.full_transport,
) -> TransportSourceCalculation:
    """Link a Calculation to a Transport with a given role."""
    row = TransportSourceCalculation(
        transport_id=transport.id,
        calculation_id=calculation.id,
        role=role,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Network / PDep factories
# ---------------------------------------------------------------------------


def make_network(
    session: Session,
    *,
    name: str | None = "test-network",
    description: str | None = None,
    literature_id: int | None = None,
    software_release_id: int | None = None,
    workflow_tool_release_id: int | None = None,
):
    """Create a Network row."""
    from app.db.models.network import Network

    n = Network(
        name=name,
        description=description,
        literature_id=literature_id,
        software_release_id=software_release_id,
        workflow_tool_release_id=workflow_tool_release_id,
    )
    session.add(n)
    session.flush()
    return n


def attach_network_species(
    session: Session,
    *,
    network,
    species_entry,
    role,
):
    """Attach a SpeciesEntry to a Network with a given role."""
    from app.db.models.network import NetworkSpecies

    row = NetworkSpecies(
        network_id=network.id,
        species_entry_id=species_entry.id,
        role=role,
    )
    session.add(row)
    session.flush()
    return row


def attach_network_reaction(session: Session, *, network, reaction_entry):
    """Attach a ReactionEntry to a Network."""
    from app.db.models.network import NetworkReaction

    row = NetworkReaction(
        network_id=network.id,
        reaction_entry_id=reaction_entry.id,
    )
    session.add(row)
    session.flush()
    return row


def make_network_state(
    session: Session,
    *,
    network,
    kind,
    composition_hash: str,
    label: str | None = None,
):
    from app.db.models.network_pdep import NetworkState

    row = NetworkState(
        network_id=network.id,
        kind=kind,
        composition_hash=composition_hash,
        label=label,
    )
    session.add(row)
    session.flush()
    return row


def attach_network_state_participant(
    session: Session,
    *,
    state,
    species_entry,
    stoichiometry: int = 1,
):
    from app.db.models.network_pdep import NetworkStateParticipant

    row = NetworkStateParticipant(
        state_id=state.id,
        species_entry_id=species_entry.id,
        stoichiometry=stoichiometry,
    )
    session.add(row)
    session.flush()
    return row


def make_network_channel(
    session: Session,
    *,
    network,
    source_state,
    sink_state,
    kind,
):
    from app.db.models.network_pdep import NetworkChannel

    row = NetworkChannel(
        network_id=network.id,
        source_state_id=source_state.id,
        sink_state_id=sink_state.id,
        kind=kind,
    )
    session.add(row)
    session.flush()
    return row


def make_network_solve(
    session: Session,
    *,
    network,
    me_method: str | None = "RRKM/ME",
    tmin_k: float | None = 300.0,
    tmax_k: float | None = 2000.0,
    pmin_bar: float | None = 0.01,
    pmax_bar: float | None = 100.0,
    software_release_id: int | None = None,
    workflow_tool_release_id: int | None = None,
    literature_id: int | None = None,
    note: str | None = None,
):
    from app.db.models.network_pdep import NetworkSolve

    row = NetworkSolve(
        network_id=network.id,
        me_method=me_method,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
        pmin_bar=pmin_bar,
        pmax_bar=pmax_bar,
        software_release_id=software_release_id,
        workflow_tool_release_id=workflow_tool_release_id,
        literature_id=literature_id,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def attach_network_solve_source_calculation(
    session: Session,
    *,
    solve,
    calculation,
    role,
):
    from app.db.models.network_pdep import NetworkSolveSourceCalculation

    row = NetworkSolveSourceCalculation(
        solve_id=solve.id,
        calculation_id=calculation.id,
        role=role,
    )
    session.add(row)
    session.flush()
    return row


def attach_network_solve_bath_gas(
    session: Session,
    *,
    solve,
    species_entry,
    mole_fraction: float = 1.0,
):
    from app.db.models.network_pdep import NetworkSolveBathGas

    row = NetworkSolveBathGas(
        solve_id=solve.id,
        species_entry_id=species_entry.id,
        mole_fraction=mole_fraction,
    )
    session.add(row)
    session.flush()
    return row


def make_network_kinetics(
    session: Session,
    *,
    channel,
    solve,
    model_kind,
    tmin_k: float | None = 300.0,
    tmax_k: float | None = 2000.0,
    pmin_bar: float | None = 0.01,
    pmax_bar: float | None = 100.0,
):
    from app.db.models.network_pdep import NetworkKinetics

    row = NetworkKinetics(
        channel_id=channel.id,
        solve_id=solve.id,
        model_kind=model_kind,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
        pmin_bar=pmin_bar,
        pmax_bar=pmax_bar,
    )
    session.add(row)
    session.flush()
    return row


def attach_network_kinetics_chebyshev(
    session: Session, *, kinetics, n_temperature: int = 6, n_pressure: int = 4,
    coefficients: dict | None = None,
):
    from app.db.models.network_pdep import NetworkKineticsChebyshev

    row = NetworkKineticsChebyshev(
        network_kinetics_id=kinetics.id,
        n_temperature=n_temperature,
        n_pressure=n_pressure,
        coefficients=coefficients or {"coeffs": [[0.0] * n_pressure] * n_temperature},
    )
    session.add(row)
    session.flush()
    return row


def attach_network_kinetics_plog(
    session: Session,
    *,
    kinetics,
    pressure_bar: float = 1.0,
    entry_index: int = 1,
    a: float = 1e12,
    n: float = 0.0,
    ea_kj_mol: float = 0.0,
):
    from app.db.models.network_pdep import NetworkKineticsPlog

    row = NetworkKineticsPlog(
        network_kinetics_id=kinetics.id,
        pressure_bar=pressure_bar,
        entry_index=entry_index,
        a=a,
        n=n,
        ea_kj_mol=ea_kj_mol,
    )
    session.add(row)
    session.flush()
    return row


def attach_network_kinetics_point(
    session: Session,
    *,
    kinetics,
    temperature_k: float,
    pressure_bar: float,
    rate_value: float,
):
    from app.db.models.network_pdep import NetworkKineticsPoint

    row = NetworkKineticsPoint(
        network_kinetics_id=kinetics.id,
        temperature_k=temperature_k,
        pressure_bar=pressure_bar,
        rate_value=rate_value,
    )
    session.add(row)
    session.flush()
    return row

# ---------------------------------------------------------------------------
# Energy correction / frequency scale factor factories
# ---------------------------------------------------------------------------


_FSF_COUNTER = 0
_LITERATURE_COUNTER = 0


def _next_fsf_value() -> float:
    """Return a unique-ish FSF value for test data.

    Stays within a plausible-looking range (0.90xx); only used to keep the
    ``FrequencyScaleFactor`` natural-identity index distinct across
    successive factory calls in one test. Not a production value generator.
    """
    global _FSF_COUNTER
    _FSF_COUNTER += 1
    return 0.9000 + _FSF_COUNTER * 0.0001


def _next_literature_doi() -> str:
    """Return a unique synthetic DOI for test literature rows.

    Independent of :func:`_next_fsf_value` so the two domains can't drift
    into shared state (the prior code reused the FSF counter for DOIs,
    coupling unrelated uniqueness needs).
    """
    global _LITERATURE_COUNTER
    _LITERATURE_COUNTER += 1
    return f"10.0000/test-{_LITERATURE_COUNTER:06d}"


def make_literature(
    session: Session,
    *,
    title: str = "Test paper",
    doi: str | None = None,
    year: int | None = 2024,
) -> Literature:
    """Create a Literature row. DOI defaults to a unique counter-derived value."""
    from app.db.models.common import LiteratureKind

    doi = doi or _next_literature_doi()
    lit = Literature(
        kind=LiteratureKind.article,
        title=title,
        year=year,
        doi=doi,
    )
    session.add(lit)
    session.flush()
    return lit


def make_software(session: Session, *, name: str = "gaussian") -> Software:
    """Create a Software row."""
    sw = Software(name=name)
    session.add(sw)
    session.flush()
    return sw


def make_workflow_tool_release(
    session: Session,
    *,
    name: str = "arc",
    version: str = "1.2.3",
    git_commit: str | None = None,
) -> WorkflowToolRelease:
    """Get-or-create a WorkflowTool + WorkflowToolRelease pair.

    Mirrors the real uniqueness constraints:

    - ``WorkflowTool`` is uniquely keyed by ``name``; repeated calls with
      the same ``name`` reuse the existing tool rather than violating the
      ``UniqueConstraint("name")``.
    - ``WorkflowToolRelease`` is uniquely keyed by
      ``(workflow_tool_id, version, git_commit)`` (``NULLS NOT DISTINCT``);
      repeated calls with the same tuple reuse the existing release.

    Callers that want a fresh distinct release should pass a different
    ``version`` or ``git_commit``.
    """
    from sqlalchemy import select as _select

    wt = session.scalar(_select(WorkflowTool).where(WorkflowTool.name == name))
    if wt is None:
        wt = WorkflowTool(name=name)
        session.add(wt)
        session.flush()

    existing = session.scalar(
        _select(WorkflowToolRelease).where(
            WorkflowToolRelease.workflow_tool_id == wt.id,
            WorkflowToolRelease.version == version,
            WorkflowToolRelease.git_commit.is_(git_commit)
            if git_commit is None
            else WorkflowToolRelease.git_commit == git_commit,
        )
    )
    if existing is not None:
        return existing

    wtr = WorkflowToolRelease(
        workflow_tool_id=wt.id, version=version, git_commit=git_commit
    )
    session.add(wtr)
    session.flush()
    return wtr


def make_frequency_scale_factor(
    session: Session,
    *,
    lot: LevelOfTheory | None = None,
    software: Software | None = None,
    scale_kind: FrequencyScaleKind = FrequencyScaleKind.fundamental,
    value: float | None = None,
    source_literature: Literature | None = None,
    workflow_tool_release: WorkflowToolRelease | None = None,
    note: str | None = None,
) -> FrequencyScaleFactor:
    """Create a FrequencyScaleFactor row.

    The natural-identity uniqueness index covers (lot, software,
    scale_kind, value, source_literature, workflow_tool_release); the
    factory bumps ``value`` per call by default so successive calls
    with identical other-keys still insert successfully.
    """
    if lot is None:
        lot = make_lot(session)
    fsf = FrequencyScaleFactor(
        level_of_theory_id=lot.id,
        software_id=software.id if software is not None else None,
        scale_kind=scale_kind,
        value=value if value is not None else _next_fsf_value(),
        source_literature_id=(
            source_literature.id if source_literature is not None else None
        ),
        workflow_tool_release_id=(
            workflow_tool_release.id
            if workflow_tool_release is not None
            else None
        ),
        note=note,
    )
    session.add(fsf)
    session.flush()
    return fsf


def make_energy_correction_scheme(
    session: Session,
    *,
    name: str = "test_scheme",
    kind: EnergyCorrectionSchemeKind = EnergyCorrectionSchemeKind.bac_petersson,
    lot: LevelOfTheory | None = None,
    source_literature: Literature | None = None,
    version: str | None = None,
    units: EnergyUnit | None = EnergyUnit.hartree,
    note: str | None = None,
) -> EnergyCorrectionScheme:
    """Create an EnergyCorrectionScheme row."""
    ecs = EnergyCorrectionScheme(
        kind=kind,
        name=name,
        level_of_theory_id=lot.id if lot is not None else None,
        source_literature_id=(
            source_literature.id if source_literature is not None else None
        ),
        version=version,
        units=units,
        note=note,
    )
    session.add(ecs)
    session.flush()
    return ecs


def attach_ecs_atom_param(
    session: Session,
    *,
    scheme: EnergyCorrectionScheme,
    element: str,
    value: float,
) -> EnergyCorrectionSchemeAtomParam:
    row = EnergyCorrectionSchemeAtomParam(
        scheme_id=scheme.id, element=element, value=value
    )
    session.add(row)
    session.flush()
    return row


def attach_ecs_bond_param(
    session: Session,
    *,
    scheme: EnergyCorrectionScheme,
    bond_key: str,
    value: float,
) -> EnergyCorrectionSchemeBondParam:
    row = EnergyCorrectionSchemeBondParam(
        scheme_id=scheme.id, bond_key=bond_key, value=value
    )
    session.add(row)
    session.flush()
    return row


def attach_ecs_component_param(
    session: Session,
    *,
    scheme: EnergyCorrectionScheme,
    component_kind: MeliusBacComponentKind,
    key: str,
    value: float,
) -> EnergyCorrectionSchemeComponentParam:
    row = EnergyCorrectionSchemeComponentParam(
        scheme_id=scheme.id,
        component_kind=component_kind,
        key=key,
        value=value,
    )
    session.add(row)
    session.flush()
    return row


def make_applied_energy_correction(
    session: Session,
    *,
    target_species_entry=None,
    target_reaction_entry=None,
    target_transition_state_entry=None,
    scheme: EnergyCorrectionScheme | None = None,
    frequency_scale_factor: FrequencyScaleFactor | None = None,
    application_role: EnergyCorrectionApplicationRole = (
        EnergyCorrectionApplicationRole.bac_total
    ),
    value: float = 0.001,
    value_unit: EnergyUnit = EnergyUnit.hartree,
    source_calculation=None,
    source_conformer_observation=None,
    temperature_k: float | None = None,
) -> AppliedEnergyCorrection:
    """Create an AppliedEnergyCorrection row with the requested provenance.

    Exactly one of ``target_*_entry`` and exactly one of ``scheme`` /
    ``frequency_scale_factor`` must be supplied (db CHECK enforces this).
    """
    row = AppliedEnergyCorrection(
        target_species_entry_id=(
            target_species_entry.id if target_species_entry is not None else None
        ),
        target_reaction_entry_id=(
            target_reaction_entry.id if target_reaction_entry is not None else None
        ),
        target_transition_state_entry_id=(
            target_transition_state_entry.id
            if target_transition_state_entry is not None
            else None
        ),
        scheme_id=scheme.id if scheme is not None else None,
        frequency_scale_factor_id=(
            frequency_scale_factor.id
            if frequency_scale_factor is not None
            else None
        ),
        application_role=application_role,
        value=value,
        value_unit=value_unit,
        source_calculation_id=(
            source_calculation.id if source_calculation is not None else None
        ),
        source_conformer_observation_id=(
            source_conformer_observation.id
            if source_conformer_observation is not None
            else None
        ),
        temperature_k=temperature_k,
    )
    session.add(row)
    session.flush()
    return row
