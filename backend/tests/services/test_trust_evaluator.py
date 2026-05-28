"""Tests for the deterministic trust / evidence evaluator (computed_calculation_v1).

These tests pin the contract of
:mod:`app.services.trust` for the first MVP rubric. They use the
per-test rollback transaction in ``conftest.py`` and direct ORM
inserts; no upload pipeline is exercised.

Each test focuses on one promise from the spec
(``backend/docs/specs/automated_trust_layer.md``):

* deterministic label-threshold mapping,
* hard-fail behaviour for the three structural signals,
* result-block detection per :class:`CalculationType`,
* "more provenance scores higher",
* the evaluator is a pure read (no record mutation, no LLM, no network).
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationSPResult,
)
from app.db.models.common import (
    ArrheniusAUnits,
    ArtifactKind,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    FrequencyScaleKind,
    KineticsCalculationRole,
    KineticsModelKind,
    KineticsUncertaintyKind,
    MoleculeKind,
    ParameterSource,
    ReactionRole,
    RecordReviewStatus,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    StereoKind,
    ThermoCalculationRole,
    TorsionTreatmentKind,
    TransportCalculationRole,
    ValidationStatus,
)
from app.db.models.energy_correction import FrequencyScaleFactor
from app.db.models.geometry import Geometry
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoPoint,
    ThermoSourceCalculation,
)
from app.db.models.transport import Transport, TransportSourceCalculation
from app.services.trust import (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    COMPUTED_STATMECH_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_TRANSPORT_V1,
    EvidenceBadge,
    EvidenceEvaluation,
    HardFailReason,
    build_trust_fragment,
    evaluate_computed_calculation,
    evaluate_computed_kinetics,
    evaluate_computed_statmech,
    evaluate_computed_thermo,
    evaluate_computed_transport,
    evaluate_loaded_calculation,
    evaluate_loaded_kinetics,
    evaluate_loaded_statmech,
    evaluate_loaded_thermo,
    evaluate_loaded_transport,
    label_from_completeness,
    select_rubric,
)

# ---------------------------------------------------------------------------
# Local ORM helpers — direct inserts; rolled back at end of test
# ---------------------------------------------------------------------------


_INCHI_COUNTER = iter(range(10_000))
_GEOM_COUNTER = iter(range(10_000))


def _next_inchi_key() -> str:
    """Return a synthetic 27-char InChI key unique within the test session."""
    return f"TRUST-EVAL-INCHI-KEY-{next(_INCHI_COUNTER):03d}A"[:27].ljust(27, "X")


def _next_geom_hash() -> str:
    """Return a synthetic 64-char geometry hash unique within the test session."""
    return hashlib.sha256(f"trust-eval-geom-{next(_GEOM_COUNTER)}".encode()).hexdigest()


def _make_species(db_session: Session) -> Species:
    sp = Species(
        kind=MoleculeKind.molecule,
        smiles="CCO",
        inchi_key=_next_inchi_key(),
        charge=0,
        multiplicity=1,
        stereo_kind=StereoKind.achiral,
    )
    db_session.add(sp)
    db_session.flush()
    return sp


def _make_species_entry(db_session: Session, species: Species) -> SpeciesEntry:
    entry = SpeciesEntry(species_id=species.id, unmapped_smiles=species.smiles)
    db_session.add(entry)
    db_session.flush()
    return entry


def _make_lot(db_session: Session) -> LevelOfTheory:
    raw = f"trust-eval-lot-{next(_INCHI_COUNTER)}"
    lot = LevelOfTheory(
        method="wb97xd",
        basis="def2tzvp",
        lot_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    db_session.add(lot)
    db_session.flush()
    return lot


def _make_software_release(db_session: Session) -> SoftwareRelease:
    sw = Software(name=f"trust-eval-sw-{next(_INCHI_COUNTER)}")
    db_session.add(sw)
    db_session.flush()
    release = SoftwareRelease(software_id=sw.id, version="1.0")
    db_session.add(release)
    db_session.flush()
    return release


def _make_geometry(db_session: Session) -> Geometry:
    g = Geometry(natoms=3, geom_hash=_next_geom_hash(), xyz_text="dummy")
    db_session.add(g)
    db_session.flush()
    return g


def _make_minimal_opt_calc(
    db_session: Session,
    *,
    quality: CalculationQuality = CalculationQuality.raw,
    lot: bool = True,
    software_release: bool = True,
    input_geom: bool = True,
    output_geom: bool = True,
    opt_result: bool = True,
    artifact: bool = True,
    parameters: bool = True,
    geom_validation: ValidationStatus | None = ValidationStatus.passed,
) -> Calculation:
    """Build an opt calc with a configurable subset of provenance attached."""
    species = _make_species(db_session)
    entry = _make_species_entry(db_session, species)

    lot_row = _make_lot(db_session) if lot else None
    release = _make_software_release(db_session) if software_release else None

    calc = Calculation(
        type=CalculationType.opt,
        quality=quality,
        species_entry_id=entry.id,
        lot_id=lot_row.id if lot_row is not None else None,
        software_release_id=release.id if release is not None else None,
    )
    db_session.add(calc)
    db_session.flush()

    if input_geom:
        in_g = _make_geometry(db_session)
        db_session.add(
            CalculationInputGeometry(
                calculation_id=calc.id, geometry_id=in_g.id, input_order=1
            )
        )
    if output_geom:
        out_g = _make_geometry(db_session)
        db_session.add(
            CalculationOutputGeometry(
                calculation_id=calc.id,
                geometry_id=out_g.id,
                output_order=1,
                role=CalculationGeometryRole.final,
            )
        )
    if opt_result:
        db_session.add(
            CalculationOptResult(
                calculation_id=calc.id,
                final_energy_hartree=-100.0,
                converged=True,
            )
        )
    if artifact:
        db_session.add(
            CalculationArtifact(
                calculation_id=calc.id,
                kind=ArtifactKind.output_log,
                uri="s3://test/log",
                filename="log.out",
            )
        )
    if parameters:
        db_session.add(
            CalculationParameter(
                calculation_id=calc.id,
                raw_key="opt",
                raw_value="tight",
                source=ParameterSource.parser,
            )
        )
    if geom_validation is not None:
        db_session.add(
            CalculationGeometryValidation(
                calculation_id=calc.id,
                validation_status=geom_validation,
                species_smiles="CCO",
                is_isomorphic=True,
            )
        )

    db_session.flush()
    db_session.refresh(calc)
    return calc


def _make_minimal_freq_calc(db_session: Session) -> Calculation:
    """Build a frequency calc with the provenance statmech checks need."""
    species = _make_species(db_session)
    entry = _make_species_entry(db_session, species)
    lot = _make_lot(db_session)
    release = _make_software_release(db_session)
    calc = Calculation(
        type=CalculationType.freq,
        quality=CalculationQuality.raw,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()
    db_session.add(
        CalculationInputGeometry(
            calculation_id=calc.id,
            geometry_id=_make_geometry(db_session).id,
            input_order=1,
        )
    )
    db_session.add(
        CalculationFreqResult(
            calculation_id=calc.id,
            n_imag=0,
            zpe_hartree=0.05,
        )
    )
    db_session.add(
        CalculationArtifact(
            calculation_id=calc.id,
            kind=ArtifactKind.output_log,
            uri="s3://test/freq-log",
            filename="freq.log",
        )
    )
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _make_reaction_entry(db_session: Session) -> ReactionEntry:
    """Build a minimal reaction_entry with one reactant and one product."""
    reactant_species = _make_species(db_session)
    product_species = _make_species(db_session)
    reactant_entry = _make_species_entry(db_session, reactant_species)
    product_entry = _make_species_entry(db_session, product_species)
    reaction = ChemReaction(reversible=False)
    db_session.add(reaction)
    db_session.flush()
    entry = ReactionEntry(reaction_id=reaction.id)
    db_session.add(entry)
    db_session.flush()
    db_session.add_all(
        [
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=reactant_entry.id,
                role=ReactionRole.reactant,
                participant_index=1,
            ),
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=product_entry.id,
                role=ReactionRole.product,
                participant_index=1,
            ),
        ]
    )
    db_session.flush()
    db_session.refresh(entry)
    return entry


def _make_kinetics(
    db_session: Session,
    *,
    reaction_entry: ReactionEntry | None = None,
    arrhenius: bool = True,
    temperature_range: bool = True,
    uncertainty: bool = False,
    tmin_k: float | None = 300.0,
    tmax_k: float | None = 2000.0,
) -> Kinetics:
    """Build a kinetics row with configurable scalar evidence."""
    entry = reaction_entry or _make_reaction_entry(db_session)
    kinetics = Kinetics(
        reaction_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
        model_kind=KineticsModelKind.modified_arrhenius,
        a=1.0e12 if arrhenius else None,
        a_units=ArrheniusAUnits.per_s if arrhenius else None,
        n=0.5 if arrhenius else None,
        ea_kj_mol=42.0 if arrhenius else None,
        tmin_k=tmin_k if temperature_range else None,
        tmax_k=tmax_k if temperature_range else None,
        a_uncertainty=1.5 if uncertainty else None,
        a_uncertainty_kind=(
            KineticsUncertaintyKind.multiplicative if uncertainty else None
        ),
    )
    db_session.add(kinetics)
    db_session.flush()
    db_session.refresh(kinetics)
    return kinetics


def _link_kinetics_source(
    db_session: Session,
    *,
    kinetics: Kinetics,
    calculation: Calculation,
    role: KineticsCalculationRole,
) -> None:
    db_session.add(
        KineticsSourceCalculation(
            kinetics_id=kinetics.id,
            calculation_id=calculation.id,
            role=role,
        )
    )
    db_session.flush()
    db_session.refresh(kinetics)


def _make_thermo(
    db_session: Session,
    *,
    species_entry: SpeciesEntry | None = None,
    scalar: bool = True,
    nasa: bool = False,
    points: bool = False,
    temperature_range: bool = True,
    uncertainty: bool = False,
    tmin_k: float | None = 300.0,
    tmax_k: float | None = 2000.0,
) -> Thermo:
    """Build a thermo row with configurable representation evidence."""
    entry = species_entry or _make_species_entry(db_session, _make_species(db_session))
    thermo = Thermo(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
        h298_kj_mol=-50.0 if scalar else None,
        s298_j_mol_k=220.0 if scalar else None,
        h298_uncertainty_kj_mol=1.2 if uncertainty else None,
        tmin_k=tmin_k if temperature_range else None,
        tmax_k=tmax_k if temperature_range else None,
    )
    db_session.add(thermo)
    db_session.flush()

    if nasa:
        db_session.add(
            ThermoNASA(
                thermo_id=thermo.id,
                t_low=300.0,
                t_mid=1000.0,
                t_high=3000.0,
                a1=1.0,
                a2=2.0,
                a3=3.0,
                a4=4.0,
                a5=5.0,
                a6=6.0,
                a7=7.0,
                b1=1.5,
                b2=2.5,
                b3=3.5,
                b4=4.5,
                b5=5.5,
                b6=6.5,
                b7=7.5,
            )
        )
    if points:
        db_session.add_all(
            [
                ThermoPoint(
                    thermo_id=thermo.id,
                    temperature_k=300.0,
                    cp_j_mol_k=35.0,
                ),
                ThermoPoint(
                    thermo_id=thermo.id,
                    temperature_k=500.0,
                    h_kj_mol=-47.5,
                ),
            ]
        )

    db_session.flush()
    db_session.refresh(thermo)
    return thermo


def _link_thermo_source(
    db_session: Session,
    *,
    thermo: Thermo,
    calculation: Calculation,
    role: ThermoCalculationRole,
) -> None:
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=thermo.id,
            calculation_id=calculation.id,
            role=role,
        )
    )
    db_session.flush()
    db_session.refresh(thermo)


def _make_transport(
    db_session: Session,
    *,
    species_entry: SpeciesEntry | None = None,
    lj: bool = True,
    dipole: bool = False,
    polarizability: bool = False,
    rotational_relaxation: bool = False,
    software_release: bool = False,
) -> Transport:
    """Build a transport row with configurable structured evidence."""
    entry = species_entry or _make_species_entry(db_session, _make_species(db_session))
    release = _make_software_release(db_session) if software_release else None
    transport = Transport(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
        software_release_id=release.id if release is not None else None,
        sigma_angstrom=3.8 if lj else None,
        epsilon_over_k_k=120.0 if lj else None,
        dipole_debye=1.1 if dipole else None,
        polarizability_angstrom3=2.3 if polarizability else None,
        rotational_relaxation=4.0 if rotational_relaxation else None,
    )
    db_session.add(transport)
    db_session.flush()
    db_session.refresh(transport)
    return transport


def _link_transport_source(
    db_session: Session,
    *,
    transport: Transport,
    calculation: Calculation,
    role: TransportCalculationRole,
) -> None:
    db_session.add(
        TransportSourceCalculation(
            transport_id=transport.id,
            calculation_id=calculation.id,
            role=role,
        )
    )
    db_session.flush()
    db_session.refresh(transport)


def _make_frequency_scale_factor(db_session: Session) -> FrequencyScaleFactor:
    fsf = FrequencyScaleFactor(
        level_of_theory_id=_make_lot(db_session).id,
        scale_kind=FrequencyScaleKind.fundamental,
        value=0.99,
    )
    db_session.add(fsf)
    db_session.flush()
    return fsf


def _make_statmech(
    db_session: Session,
    *,
    species_entry: SpeciesEntry | None = None,
    metadata: bool = True,
    frequency_scale_factor: bool = False,
    treatment: StatmechTreatmentKind = StatmechTreatmentKind.rrho,
) -> Statmech:
    """Build a statmech row with configurable structured evidence."""
    entry = species_entry or _make_species_entry(db_session, _make_species(db_session))
    fsf = _make_frequency_scale_factor(db_session) if frequency_scale_factor else None
    statmech = Statmech(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
        external_symmetry=1 if metadata else None,
        point_group="C1" if metadata else None,
        is_linear=False if metadata else None,
        rigid_rotor_kind=RigidRotorKind.asymmetric_top if metadata else None,
        statmech_treatment=treatment if metadata else None,
        frequency_scale_factor_id=fsf.id if fsf is not None else None,
        uses_projected_frequencies=False if metadata else None,
    )
    db_session.add(statmech)
    db_session.flush()
    db_session.refresh(statmech)
    return statmech


def _link_statmech_source(
    db_session: Session,
    *,
    statmech: Statmech,
    calculation: Calculation,
    role: StatmechCalculationRole,
) -> None:
    db_session.add(
        StatmechSourceCalculation(
            statmech_id=statmech.id,
            calculation_id=calculation.id,
            role=role,
        )
    )
    db_session.flush()
    db_session.refresh(statmech)


def _add_statmech_torsion(
    db_session: Session,
    *,
    statmech: Statmech,
    source_scan: Calculation | None = None,
) -> StatmechTorsion:
    torsion = StatmechTorsion(
        statmech_id=statmech.id,
        torsion_index=1,
        symmetry_number=3,
        treatment_kind=TorsionTreatmentKind.hindered_rotor,
        dimension=1,
        source_scan_calculation_id=source_scan.id if source_scan is not None else None,
    )
    db_session.add(torsion)
    db_session.flush()
    db_session.add(
        StatmechTorsionDefinition(
            torsion_id=torsion.id,
            coordinate_index=1,
            atom1_index=1,
            atom2_index=2,
            atom3_index=3,
            atom4_index=4,
        )
    )
    db_session.flush()
    db_session.refresh(statmech)
    db_session.refresh(torsion)
    return torsion


# ---------------------------------------------------------------------------
# 1. Pure-unit: deterministic label-threshold mapping
# ---------------------------------------------------------------------------


class TestLabelThresholdMapping:
    """Pin the deterministic mapping documented in spec §6.1."""

    @pytest.mark.parametrize(
        "ratio,all_required_passed,expected",
        [
            (1.00, True, EvidenceBadge.well_supported),
            (0.91, True, EvidenceBadge.well_supported),
            (0.91, False, EvidenceBadge.mostly_supported),
            (0.90, True, EvidenceBadge.well_supported),
            (0.89, True, EvidenceBadge.mostly_supported),
            (0.75, True, EvidenceBadge.mostly_supported),
            (0.74, True, EvidenceBadge.partial),
            (0.50, True, EvidenceBadge.partial),
            (0.49, True, EvidenceBadge.sparse),
            (0.25, True, EvidenceBadge.sparse),
            (0.24, True, EvidenceBadge.unsupported),
            (0.00, True, EvidenceBadge.unsupported),
        ],
    )
    def test_mapping(self, ratio, all_required_passed, expected):
        assert (
            label_from_completeness(ratio, all_required_passed=all_required_passed)
            is expected
        )

    def test_well_supported_requires_all_required_passed(self):
        """A high ratio with a failing required check cannot reach well_supported."""
        assert (
            label_from_completeness(0.95, all_required_passed=False)
            is EvidenceBadge.mostly_supported
        )

    def test_mapping_is_deterministic(self):
        """Two evaluations with the same inputs return the same label."""
        a = label_from_completeness(0.73, all_required_passed=True)
        b = label_from_completeness(0.73, all_required_passed=True)
        assert a is b


# ---------------------------------------------------------------------------
# 2. Rubric registry shape
# ---------------------------------------------------------------------------


class TestRubricRegistry:
    """Sanity-check the rubric registry surface."""

    def test_calculation_rubric_registered(self):
        rubric = select_rubric("calculation")
        assert rubric is not None
        assert rubric.name == "computed_calculation"
        assert rubric.version == 1
        assert rubric is COMPUTED_CALCULATION_V1

    def test_unknown_record_type_returns_none(self):
        assert select_rubric("not_a_real_record_type") is None

    def test_rubric_contains_expected_checks(self):
        names = {c.name for c in COMPUTED_CALCULATION_V1.checks}
        # Spot-check a representative subset of names from spec §9.5.
        expected_subset = {
            "calculation_has_owner",
            "calculation_type_present",
            "level_of_theory_present",
            "software_release_present",
            "input_geometry_present",
            "result_block_present",
            "geometry_validation_present",
            "geometry_validation_passed_or_warning",
            "artifacts_present",
            "parameters_parsed",
        }
        assert expected_subset.issubset(names)


# ---------------------------------------------------------------------------
# 3. Missing-record path
# ---------------------------------------------------------------------------


class TestMissingCalculation:
    """A non-existent calculation_id returns a structured hard-fail."""

    def test_missing_loaded_calculation_returns_hard_failed(self):
        result = evaluate_loaded_calculation(None)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.calculation_missing
        assert result.record_type == "calculation"
        assert result.record_id is None
        assert result.rubric == "computed_calculation"

    def test_missing_calculation_id_returns_hard_failed(self, db_session):
        result = evaluate_computed_calculation(db_session, calculation_id=999_999_999)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.calculation_missing
        assert result.passed_count == 0
        assert result.evidence_completeness == 0.0
        # Should still name the rubric, not bail with an exception.
        assert result.rubric == "computed_calculation"
        assert result.rubric_version == 1
        assert result.record_id == 999_999_999

    def test_missing_calculation_does_not_raise(self, db_session):
        evaluate_computed_calculation(db_session, calculation_id=999_999_999)


def test_trust_fragment_normalizes_public_shape():
    evaluation = EvidenceEvaluation(
        record_type="calculation",
        record_id=123,
        rubric="computed_calculation",
        rubric_version=1,
        label=EvidenceBadge.hard_failed,
        passed_checks=("owner_present",),
        missing_checks=("result_block_present",),
        warning_checks=("geometry_validation_passed_or_warning",),
        not_applicable_checks=("output_geometry_present",),
        passed_count=1,
        possible_count=2,
        evidence_completeness=0.5,
        hard_fail_reason=HardFailReason.geometry_validation_failed,
    )

    fragment = build_trust_fragment(
        evaluation,
        review_status=RecordReviewStatus.approved,
    ).model_dump(mode="json")

    assert fragment["review_status"] == "approved"
    assert fragment["trust_status"] == "hard_failed"
    assert fragment["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert fragment["is_certified"] is False
    assert fragment["evidence"] == {
        "record_type": "calculation",
        "record_id": 123,
        "rubric": "computed_calculation_v1",
        "rubric_version": 1,
        "label": "hard_failed",
        "passed_checks": ["owner_present"],
        "missing_checks": ["result_block_present"],
        "warning_checks": ["geometry_validation_passed_or_warning"],
        "not_applicable_checks": ["output_geometry_present"],
        "passed_count": 1,
        "possible_count": 2,
        "evidence_completeness": 0.5,
        "is_certified": False,
        "hard_fail_reason": "geometry_validation_failed",
    }


# ---------------------------------------------------------------------------
# 4. Provenance richness raises completeness
# ---------------------------------------------------------------------------


class TestEvidenceCompletenessOrdering:
    """Adding provenance to a calculation should not lower the badge."""

    def test_loaded_calculation_matches_id_entrypoint(self, db_session):
        calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.curated,
        )

        loaded = evaluate_loaded_calculation(calc)
        by_id = evaluate_computed_calculation(db_session, calc.id)

        assert loaded == by_id

    def test_full_provenance_beats_sparse(self, db_session):
        rich_calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.curated,
        )
        sparse_calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.raw,
            software_release=False,
            output_geom=False,
            artifact=False,
            parameters=False,
            geom_validation=None,
        )

        rich = evaluate_computed_calculation(db_session, rich_calc.id)
        sparse = evaluate_computed_calculation(db_session, sparse_calc.id)

        assert rich.evidence_completeness >= sparse.evidence_completeness
        assert rich.passed_count >= sparse.passed_count
        # The rich calc should not be 'sparse' or worse.
        assert rich.label not in {EvidenceBadge.sparse, EvidenceBadge.unsupported}

    def test_calculation_with_no_artifact_reports_missing(self, db_session):
        calc = _make_minimal_opt_calc(db_session, artifact=False)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert "artifacts_present" in result.missing_checks
        assert "artifacts_present" not in result.passed_checks


# ---------------------------------------------------------------------------
# 5. Geometry validation
# ---------------------------------------------------------------------------


class TestGeometryValidation:
    """Geometry-validation status drives passed / warning / hard-fail behaviour."""

    def test_passed_status_records_passed_evidence(self, db_session):
        calc = _make_minimal_opt_calc(
            db_session, geom_validation=ValidationStatus.passed
        )
        result = evaluate_computed_calculation(db_session, calc.id)
        assert "geometry_validation_present" in result.passed_checks
        assert "geometry_validation_passed_or_warning" not in result.warning_checks
        assert result.hard_fail_reason is None

    def test_warning_status_is_advisory_not_hard_fail(self, db_session):
        calc = _make_minimal_opt_calc(
            db_session, geom_validation=ValidationStatus.warning
        )
        result = evaluate_computed_calculation(db_session, calc.id)
        assert "geometry_validation_passed_or_warning" in result.warning_checks
        assert result.label is not EvidenceBadge.hard_failed

    def test_fail_status_is_hard_failed(self, db_session):
        calc = _make_minimal_opt_calc(db_session, geom_validation=ValidationStatus.fail)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.geometry_validation_failed


# ---------------------------------------------------------------------------
# 6. Quality rejected
# ---------------------------------------------------------------------------


class TestRejectedQuality:
    """A rejected calculation should be hard-failed regardless of other checks."""

    def test_rejected_quality_hard_failed(self, db_session):
        calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.rejected,
        )
        result = evaluate_computed_calculation(db_session, calc.id)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.calculation_rejected


# ---------------------------------------------------------------------------
# 7. Result-block detection per calculation type
# ---------------------------------------------------------------------------


class TestResultBlockDetection:
    """``result_block_present`` resolves to the correct calc_*_result table."""

    def test_sp_with_result_passes(self, db_session):
        species = _make_species(db_session)
        entry = _make_species_entry(db_session, species)
        lot = _make_lot(db_session)
        in_g = _make_geometry(db_session)
        calc = Calculation(
            type=CalculationType.sp,
            quality=CalculationQuality.raw,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )
        db_session.add(calc)
        db_session.flush()
        db_session.add(
            CalculationInputGeometry(
                calculation_id=calc.id, geometry_id=in_g.id, input_order=1
            )
        )
        db_session.add(
            CalculationSPResult(
                calculation_id=calc.id, electronic_energy_hartree=-100.0
            )
        )
        db_session.flush()
        db_session.refresh(calc)

        result = evaluate_computed_calculation(db_session, calc.id)
        assert "result_block_present" in result.passed_checks
        # SP should not require an output geometry.
        assert "output_geometry_present" in result.not_applicable_checks

    def test_opt_with_result_passes(self, db_session):
        calc = _make_minimal_opt_calc(db_session)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert "result_block_present" in result.passed_checks
        assert "output_geometry_present" in result.passed_checks

    def test_freq_with_result_passes(self, db_session):
        species = _make_species(db_session)
        entry = _make_species_entry(db_session, species)
        lot = _make_lot(db_session)
        in_g = _make_geometry(db_session)
        calc = Calculation(
            type=CalculationType.freq,
            quality=CalculationQuality.raw,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )
        db_session.add(calc)
        db_session.flush()
        db_session.add(
            CalculationInputGeometry(
                calculation_id=calc.id, geometry_id=in_g.id, input_order=1
            )
        )
        db_session.add(
            CalculationFreqResult(
                calculation_id=calc.id,
                n_imag=0,
                zpe_hartree=0.05,
            )
        )
        db_session.flush()
        db_session.refresh(calc)

        result = evaluate_computed_calculation(db_session, calc.id)
        assert "result_block_present" in result.passed_checks
        # Freq does not produce a separate output geometry; check is N/A.
        assert "output_geometry_present" in result.not_applicable_checks

    def test_opt_without_result_block_is_missing(self, db_session):
        calc = _make_minimal_opt_calc(db_session, opt_result=False)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert "result_block_present" in result.missing_checks


# ---------------------------------------------------------------------------
# 8. Evaluator side-effect freedom
# ---------------------------------------------------------------------------


class TestEvaluatorPurity:
    """The evaluator must not mutate scientific records and must not need an LLM."""

    def test_evaluator_does_not_mutate(self, db_session):
        calc = _make_minimal_opt_calc(db_session)
        before_quality = calc.quality
        before_lot_id = calc.lot_id
        before_n_artifacts = len(calc.artifacts)

        evaluate_computed_calculation(db_session, calc.id)
        db_session.refresh(calc)

        assert calc.quality is before_quality
        assert calc.lot_id == before_lot_id
        assert len(calc.artifacts) == before_n_artifacts

    def test_evaluator_does_not_require_llm_config(self, db_session, monkeypatch):
        """Stripping LLM-shaped env vars must not affect the evaluation.

        The deterministic evaluator never reads any LLM provider config.
        We assert this by removing every env var that *looks* like an
        LLM key and confirming the evaluator still returns a complete
        evaluation.
        """
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

        calc = _make_minimal_opt_calc(db_session)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert result.rubric == "computed_calculation"
        assert result.evidence_completeness >= 0.0
        assert result.is_certified is False


# ---------------------------------------------------------------------------
# 9. Aggregation invariants
# ---------------------------------------------------------------------------


class TestAggregationInvariants:
    """Numerator/denominator counts and bucket sets must be internally consistent."""

    def test_passed_plus_missing_equals_possible_count(self, db_session):
        calc = _make_minimal_opt_calc(db_session, artifact=False, parameters=False)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert (
            len(result.passed_checks) + len(result.missing_checks)
            == result.possible_count
        )

    def test_passed_count_matches_passed_checks(self, db_session):
        calc = _make_minimal_opt_calc(db_session)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert result.passed_count == len(result.passed_checks)

    def test_completeness_in_unit_interval(self, db_session):
        calc = _make_minimal_opt_calc(db_session)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert 0.0 <= result.evidence_completeness <= 1.0

    def test_is_certified_always_false_from_evaluator(self, db_session):
        calc = _make_minimal_opt_calc(db_session, quality=CalculationQuality.curated)
        result = evaluate_computed_calculation(db_session, calc.id)
        assert result.is_certified is False


# ---------------------------------------------------------------------------
# 10. Computed kinetics rubric
# ---------------------------------------------------------------------------


class TestComputedKineticsEvaluator:
    """Focused tests for computed_kinetics_v1 evidence completeness."""

    def test_kinetics_rubric_registered(self):
        rubric = select_rubric("kinetics")
        assert rubric is COMPUTED_KINETICS_V1
        assert rubric.name == "computed_kinetics"
        assert rubric.version == 1

    def test_missing_loaded_kinetics_returns_hard_failed(self):
        result = evaluate_loaded_kinetics(None)
        assert result.label is EvidenceBadge.hard_failed
        assert result.record_type == "kinetics"
        assert result.record_id is None
        assert result.rubric == "computed_kinetics"
        assert result.hard_fail_reason is HardFailReason.kinetics_missing

    def test_sparse_computed_kinetics_has_low_evidence(self, db_session):
        kinetics = _make_kinetics(db_session, arrhenius=False, temperature_range=False)
        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert result.label in {EvidenceBadge.sparse, EvidenceBadge.unsupported}
        assert "source_calculations_present" in result.missing_checks
        assert "arrhenius_parameters_complete" in result.missing_checks

    def test_core_arrhenius_and_temperature_checks_pass(self, db_session):
        kinetics = _make_kinetics(db_session)
        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert "reaction_entry_present" in result.passed_checks
        assert "kinetics_model_present" in result.passed_checks
        assert "arrhenius_parameters_complete" in result.passed_checks
        assert "arrhenius_units_present" in result.passed_checks
        assert "temperature_range_present" in result.passed_checks
        assert "temperature_range_valid" in result.passed_checks

    def test_missing_temperature_range_reports_missing(self, db_session):
        kinetics = _make_kinetics(db_session, temperature_range=False)
        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert "temperature_range_present" in result.missing_checks
        assert "temperature_range_valid" in result.not_applicable_checks
        assert result.hard_fail_reason is None

    def test_invalid_temperature_range_hard_fails(self, db_session):
        kinetics = _make_kinetics(db_session, tmin_k=500.0, tmax_k=500.0)
        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.invalid_temperature_range
        assert "temperature_range_valid" in result.missing_checks

    def test_source_calculations_raise_evidence_completeness(self, db_session):
        sparse = _make_kinetics(db_session)
        rich = _make_kinetics(db_session)
        calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.curated,
        )
        _link_kinetics_source(
            db_session,
            kinetics=rich,
            calculation=calc,
            role=KineticsCalculationRole.ts_energy,
        )

        sparse_result = evaluate_computed_kinetics(db_session, sparse.id)
        rich_result = evaluate_computed_kinetics(db_session, rich.id)

        assert rich_result.evidence_completeness > sparse_result.evidence_completeness
        assert "source_calculations_present" in rich_result.passed_checks
        assert "source_calculation_lot_present" in rich_result.passed_checks

    def test_ts_and_frequency_roles_pass_corresponding_checks(self, db_session):
        kinetics = _make_kinetics(db_session)
        ts_calc = _make_minimal_opt_calc(db_session)
        freq_calc = _make_minimal_opt_calc(db_session)
        _link_kinetics_source(
            db_session,
            kinetics=kinetics,
            calculation=ts_calc,
            role=KineticsCalculationRole.ts_energy,
        )
        _link_kinetics_source(
            db_session,
            kinetics=kinetics,
            calculation=freq_calc,
            role=KineticsCalculationRole.freq,
        )

        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert "ts_energy_source_present" in result.passed_checks
        assert "frequency_source_present" in result.passed_checks

    def test_missing_uncertainty_is_not_hard_fail(self, db_session):
        kinetics = _make_kinetics(db_session, uncertainty=False)
        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert "uncertainty_present" in result.missing_checks
        assert result.label is not EvidenceBadge.hard_failed
        assert result.hard_fail_reason is None

    def test_source_geometry_failure_hard_fails_required_role(self, db_session):
        kinetics = _make_kinetics(db_session)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.fail,
        )
        _link_kinetics_source(
            db_session,
            kinetics=kinetics,
            calculation=calc,
            role=KineticsCalculationRole.ts_energy,
        )

        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert result.label is EvidenceBadge.hard_failed
        assert (
            result.hard_fail_reason
            is HardFailReason.source_calculation_hard_failed_for_required_role
        )

    def test_source_geometry_warning_is_advisory(self, db_session):
        kinetics = _make_kinetics(db_session)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.warning,
        )
        _link_kinetics_source(
            db_session,
            kinetics=kinetics,
            calculation=calc,
            role=KineticsCalculationRole.ts_energy,
        )

        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert (
            "geometry_validation_not_failed_for_source_calculations"
            in result.warning_checks
        )
        assert result.label is not EvidenceBadge.hard_failed

    def test_loaded_kinetics_matches_id_entrypoint(self, db_session):
        kinetics = _make_kinetics(db_session, uncertainty=True)
        calc = _make_minimal_opt_calc(db_session)
        _link_kinetics_source(
            db_session,
            kinetics=kinetics,
            calculation=calc,
            role=KineticsCalculationRole.ts_energy,
        )
        db_session.refresh(kinetics)

        loaded = evaluate_loaded_kinetics(kinetics)
        by_id = evaluate_computed_kinetics(db_session, kinetics.id)
        assert loaded == by_id

    def test_evaluator_does_not_mutate_kinetics(self, db_session):
        kinetics = _make_kinetics(db_session, uncertainty=True)
        before_a = kinetics.a
        before_tmin = kinetics.tmin_k
        before_sources = len(kinetics.source_calculations)

        evaluate_computed_kinetics(db_session, kinetics.id)
        db_session.refresh(kinetics)

        assert kinetics.a == before_a
        assert kinetics.tmin_k == before_tmin
        assert len(kinetics.source_calculations) == before_sources

    def test_kinetics_evaluator_does_not_require_llm_config(
        self, db_session, monkeypatch
    ):
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

        kinetics = _make_kinetics(db_session)
        result = evaluate_computed_kinetics(db_session, kinetics.id)
        assert result.rubric == "computed_kinetics"
        assert result.evidence_completeness >= 0.0
        assert result.is_certified is False


# ---------------------------------------------------------------------------
# 11. Computed thermo rubric
# ---------------------------------------------------------------------------


class TestComputedThermoEvaluator:
    """Focused tests for computed_thermo_v1 evidence completeness."""

    def test_thermo_rubric_registered(self):
        rubric = select_rubric("thermo")
        assert rubric is COMPUTED_THERMO_V1
        assert rubric.name == "computed_thermo"
        assert rubric.version == 1

    def test_missing_loaded_thermo_returns_hard_failed(self):
        result = evaluate_loaded_thermo(None)
        assert result.label is EvidenceBadge.hard_failed
        assert result.record_type == "thermo"
        assert result.record_id is None
        assert result.rubric == "computed_thermo"
        assert result.hard_fail_reason is HardFailReason.thermo_missing

    def test_sparse_computed_thermo_has_low_evidence(self, db_session):
        thermo = _make_thermo(db_session, scalar=True, temperature_range=False)
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert result.label in {EvidenceBadge.sparse, EvidenceBadge.unsupported}
        assert "source_calculations_present" in result.missing_checks
        assert "source_calculation_lot_present" in result.missing_checks

    def test_species_and_scalar_representation_checks_pass(self, db_session):
        thermo = _make_thermo(db_session, scalar=True, temperature_range=False)
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert "species_entry_present" in result.passed_checks
        assert "thermo_origin_is_computed" in result.passed_checks
        assert "thermo_model_present" in result.passed_checks
        assert "scalar_thermo_present" in result.passed_checks
        assert "at_least_one_thermo_representation_present" in result.passed_checks
        assert "nasa_coefficients_present" in result.not_applicable_checks
        assert "thermo_points_present" in result.not_applicable_checks

    def test_nasa_representation_checks_pass(self, db_session):
        thermo = _make_thermo(db_session, scalar=False, nasa=True)
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert "nasa_coefficients_present" in result.passed_checks
        assert "at_least_one_thermo_representation_present" in result.passed_checks
        assert "temperature_range_present_if_applicable" in result.passed_checks
        assert "temperature_range_valid" in result.passed_checks

    def test_points_representation_checks_pass(self, db_session):
        thermo = _make_thermo(
            db_session,
            scalar=False,
            points=True,
            temperature_range=False,
        )
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert "thermo_points_present" in result.passed_checks
        assert "at_least_one_thermo_representation_present" in result.passed_checks
        assert "scalar_thermo_present" in result.not_applicable_checks

    def test_missing_all_representations_hard_fails(self, db_session):
        thermo = _make_thermo(
            db_session,
            scalar=False,
            nasa=False,
            points=False,
            temperature_range=False,
        )
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert result.label is EvidenceBadge.hard_failed
        assert (
            result.hard_fail_reason is HardFailReason.no_thermo_representation_present
        )
        assert "at_least_one_thermo_representation_present" in result.missing_checks

    def test_missing_temperature_range_reports_not_applicable_for_scalar(
        self, db_session
    ):
        thermo = _make_thermo(db_session, scalar=True, temperature_range=False)
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert "temperature_range_present_if_applicable" in result.not_applicable_checks
        assert "temperature_range_valid" in result.not_applicable_checks
        assert result.hard_fail_reason is None

    def test_invalid_temperature_range_hard_fails(self, db_session):
        thermo = _make_thermo(db_session, scalar=True, tmin_k=500.0, tmax_k=500.0)
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.invalid_temperature_range
        assert "temperature_range_valid" in result.missing_checks

    def test_source_calculations_raise_evidence_completeness(self, db_session):
        sparse = _make_thermo(db_session, scalar=True)
        rich = _make_thermo(db_session, scalar=True)
        calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.curated,
        )
        _link_thermo_source(
            db_session,
            thermo=rich,
            calculation=calc,
            role=ThermoCalculationRole.opt,
        )

        sparse_result = evaluate_computed_thermo(db_session, sparse.id)
        rich_result = evaluate_computed_thermo(db_session, rich.id)

        assert rich_result.evidence_completeness > sparse_result.evidence_completeness
        assert "source_calculations_present" in rich_result.passed_checks
        assert "source_calculation_lot_present" in rich_result.passed_checks

    def test_opt_freq_and_sp_roles_pass_corresponding_checks(self, db_session):
        thermo = _make_thermo(db_session, scalar=True)
        opt_calc = _make_minimal_opt_calc(db_session)
        freq_calc = _make_minimal_opt_calc(db_session)
        sp_calc = _make_minimal_opt_calc(db_session)
        _link_thermo_source(
            db_session,
            thermo=thermo,
            calculation=opt_calc,
            role=ThermoCalculationRole.opt,
        )
        _link_thermo_source(
            db_session,
            thermo=thermo,
            calculation=freq_calc,
            role=ThermoCalculationRole.freq,
        )
        _link_thermo_source(
            db_session,
            thermo=thermo,
            calculation=sp_calc,
            role=ThermoCalculationRole.sp,
        )

        result = evaluate_computed_thermo(db_session, thermo.id)
        assert "opt_source_present" in result.passed_checks
        assert "freq_source_present" in result.passed_checks
        assert "sp_or_composite_source_present_if_applicable" in result.passed_checks

    def test_missing_uncertainty_is_not_hard_fail(self, db_session):
        thermo = _make_thermo(db_session, scalar=True, uncertainty=False)
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert "uncertainty_present" in result.missing_checks
        assert result.label is not EvidenceBadge.hard_failed
        assert result.hard_fail_reason is None

    def test_source_geometry_failure_hard_fails_required_role(self, db_session):
        thermo = _make_thermo(db_session, scalar=True)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.fail,
        )
        _link_thermo_source(
            db_session,
            thermo=thermo,
            calculation=calc,
            role=ThermoCalculationRole.opt,
        )

        result = evaluate_computed_thermo(db_session, thermo.id)
        assert result.label is EvidenceBadge.hard_failed
        assert (
            result.hard_fail_reason
            is HardFailReason.source_calculation_hard_failed_for_required_role
        )
        assert (
            "source_calculation_has_non_hard_failed_evidence" in result.missing_checks
        )

    def test_source_geometry_warning_is_advisory(self, db_session):
        thermo = _make_thermo(db_session, scalar=True)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.warning,
        )
        _link_thermo_source(
            db_session,
            thermo=thermo,
            calculation=calc,
            role=ThermoCalculationRole.opt,
        )

        result = evaluate_computed_thermo(db_session, thermo.id)
        assert (
            "geometry_validation_not_failed_for_source_calculations"
            in result.warning_checks
        )
        assert result.label is not EvidenceBadge.hard_failed

    def test_loaded_thermo_matches_id_entrypoint(self, db_session):
        thermo = _make_thermo(db_session, scalar=True, uncertainty=True)
        calc = _make_minimal_opt_calc(db_session)
        _link_thermo_source(
            db_session,
            thermo=thermo,
            calculation=calc,
            role=ThermoCalculationRole.opt,
        )
        db_session.refresh(thermo)

        loaded = evaluate_loaded_thermo(thermo)
        by_id = evaluate_computed_thermo(db_session, thermo.id)
        assert loaded == by_id

    def test_evaluator_does_not_mutate_thermo(self, db_session):
        thermo = _make_thermo(db_session, scalar=True, uncertainty=True)
        before_h298 = thermo.h298_kj_mol
        before_tmin = thermo.tmin_k
        before_sources = len(thermo.source_calculations)

        evaluate_computed_thermo(db_session, thermo.id)
        db_session.refresh(thermo)

        assert thermo.h298_kj_mol == before_h298
        assert thermo.tmin_k == before_tmin
        assert len(thermo.source_calculations) == before_sources

    def test_thermo_evaluator_does_not_require_llm_config(
        self, db_session, monkeypatch
    ):
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

        thermo = _make_thermo(db_session, scalar=True)
        result = evaluate_computed_thermo(db_session, thermo.id)
        assert result.rubric == "computed_thermo"
        assert result.evidence_completeness >= 0.0
        assert result.is_certified is False


# ---------------------------------------------------------------------------
# 12. Computed statmech rubric
# ---------------------------------------------------------------------------


class TestComputedStatmechEvaluator:
    """Focused tests for computed_statmech_v1 evidence completeness."""

    def test_statmech_rubric_registered(self):
        rubric = select_rubric("statmech")
        assert rubric is COMPUTED_STATMECH_V1
        assert rubric.name == "computed_statmech"
        assert rubric.version == 1

    def test_missing_loaded_statmech_returns_hard_failed(self):
        result = evaluate_loaded_statmech(None)
        assert result.label is EvidenceBadge.hard_failed
        assert result.record_type == "statmech"
        assert result.record_id is None
        assert result.rubric == "computed_statmech"
        assert result.hard_fail_reason is HardFailReason.statmech_missing

    def test_sparse_computed_statmech_has_low_evidence(self, db_session):
        statmech = _make_statmech(db_session, metadata=False)
        result = evaluate_computed_statmech(db_session, statmech.id)
        assert result.label in {EvidenceBadge.sparse, EvidenceBadge.unsupported}
        assert "source_calculations_present" in result.missing_checks
        assert "statmech_treatment_present" in result.missing_checks
        assert "rigid_rotor_kind_present" in result.missing_checks

    def test_species_treatment_and_rotor_metadata_checks_pass(self, db_session):
        statmech = _make_statmech(db_session)
        result = evaluate_computed_statmech(db_session, statmech.id)
        assert "species_entry_present" in result.passed_checks
        assert "statmech_origin_is_computed" in result.passed_checks
        assert "statmech_treatment_present" in result.passed_checks
        assert "rigid_rotor_kind_present" in result.passed_checks
        assert "external_symmetry_present" in result.passed_checks
        assert "point_group_present" in result.passed_checks
        assert "is_linear_present" in result.passed_checks
        assert "uses_projected_frequencies_recorded" in result.passed_checks

    def test_frequency_scale_factor_passes_when_frequency_source_exists(
        self, db_session
    ):
        statmech = _make_statmech(db_session, frequency_scale_factor=True)
        freq_calc = _make_minimal_freq_calc(db_session)
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=freq_calc,
            role=StatmechCalculationRole.freq,
        )

        result = evaluate_computed_statmech(db_session, statmech.id)
        assert "frequency_scale_factor_present_if_applicable" in result.passed_checks

    def test_source_calculations_raise_evidence_completeness(self, db_session):
        sparse = _make_statmech(db_session)
        rich = _make_statmech(db_session)
        calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.curated,
        )
        _link_statmech_source(
            db_session,
            statmech=rich,
            calculation=calc,
            role=StatmechCalculationRole.opt,
        )

        sparse_result = evaluate_computed_statmech(db_session, sparse.id)
        rich_result = evaluate_computed_statmech(db_session, rich.id)

        assert rich_result.evidence_completeness > sparse_result.evidence_completeness
        assert "source_calculations_present" in rich_result.passed_checks
        assert "source_calculation_lot_present" in rich_result.passed_checks

    def test_opt_freq_and_sp_roles_pass_corresponding_checks(self, db_session):
        statmech = _make_statmech(db_session, frequency_scale_factor=True)
        opt_calc = _make_minimal_opt_calc(db_session)
        freq_calc = _make_minimal_freq_calc(db_session)
        sp_calc = _make_minimal_opt_calc(db_session)
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=opt_calc,
            role=StatmechCalculationRole.opt,
        )
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=freq_calc,
            role=StatmechCalculationRole.freq,
        )
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=sp_calc,
            role=StatmechCalculationRole.sp,
        )

        result = evaluate_computed_statmech(db_session, statmech.id)
        assert "opt_source_present" in result.passed_checks
        assert "freq_source_present" in result.passed_checks
        assert "sp_or_composite_source_present" in result.passed_checks

    def test_missing_frequency_source_reports_missing_not_hard_fail(self, db_session):
        statmech = _make_statmech(db_session)
        result = evaluate_computed_statmech(db_session, statmech.id)
        assert "freq_source_present" in result.missing_checks
        assert (
            "frequency_scale_factor_present_if_applicable"
            in result.not_applicable_checks
        )
        assert result.hard_fail_reason is None

    def test_torsion_checks_not_applicable_without_torsion_treatment(self, db_session):
        statmech = _make_statmech(db_session, treatment=StatmechTreatmentKind.rrho)
        result = evaluate_computed_statmech(db_session, statmech.id)
        assert (
            "torsions_recorded_if_hindered_rotor_treatment"
            in result.not_applicable_checks
        )
        assert "torsion_definitions_present" in result.not_applicable_checks
        assert "torsion_symmetry_recorded" in result.not_applicable_checks
        assert "scan_source_present_if_torsions_present" in result.not_applicable_checks

    def test_torsion_rows_and_definitions_pass_when_present(self, db_session):
        statmech = _make_statmech(
            db_session,
            treatment=StatmechTreatmentKind.rrho_1d,
        )
        scan_calc = _make_minimal_opt_calc(db_session)
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=scan_calc,
            role=StatmechCalculationRole.scan,
        )
        _add_statmech_torsion(db_session, statmech=statmech, source_scan=scan_calc)

        result = evaluate_computed_statmech(db_session, statmech.id)
        assert "torsions_recorded_if_hindered_rotor_treatment" in result.passed_checks
        assert "torsion_definitions_present" in result.passed_checks
        assert "torsion_symmetry_recorded" in result.passed_checks
        assert "scan_source_present_if_torsions_present" in result.passed_checks

    def test_source_geometry_failure_hard_fails_required_role(self, db_session):
        statmech = _make_statmech(db_session)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.fail,
        )
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=calc,
            role=StatmechCalculationRole.opt,
        )

        result = evaluate_computed_statmech(db_session, statmech.id)
        assert result.label is EvidenceBadge.hard_failed
        assert (
            result.hard_fail_reason
            is HardFailReason.source_calculation_hard_failed_for_required_role
        )
        assert (
            "source_calculation_has_non_hard_failed_evidence" in result.missing_checks
        )

    def test_source_geometry_warning_is_advisory(self, db_session):
        statmech = _make_statmech(db_session)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.warning,
        )
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=calc,
            role=StatmechCalculationRole.opt,
        )

        result = evaluate_computed_statmech(db_session, statmech.id)
        assert (
            "geometry_validation_not_failed_for_source_calculations"
            in result.warning_checks
        )
        assert result.label is not EvidenceBadge.hard_failed

    def test_loaded_statmech_matches_id_entrypoint(self, db_session):
        statmech = _make_statmech(db_session)
        calc = _make_minimal_opt_calc(db_session)
        _link_statmech_source(
            db_session,
            statmech=statmech,
            calculation=calc,
            role=StatmechCalculationRole.opt,
        )
        db_session.refresh(statmech)

        loaded = evaluate_loaded_statmech(statmech)
        by_id = evaluate_computed_statmech(db_session, statmech.id)
        assert loaded == by_id

    def test_evaluator_does_not_mutate_statmech(self, db_session):
        statmech = _make_statmech(db_session, frequency_scale_factor=True)
        before_treatment = statmech.statmech_treatment
        before_external_symmetry = statmech.external_symmetry
        before_sources = len(statmech.source_calculations)

        evaluate_computed_statmech(db_session, statmech.id)
        db_session.refresh(statmech)

        assert statmech.statmech_treatment is before_treatment
        assert statmech.external_symmetry == before_external_symmetry
        assert len(statmech.source_calculations) == before_sources

    def test_statmech_evaluator_does_not_require_llm_config(
        self, db_session, monkeypatch
    ):
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

        statmech = _make_statmech(db_session)
        result = evaluate_computed_statmech(db_session, statmech.id)
        assert result.rubric == "computed_statmech"
        assert result.evidence_completeness >= 0.0
        assert result.is_certified is False


# ---------------------------------------------------------------------------
# 13. Computed transport rubric
# ---------------------------------------------------------------------------


class TestComputedTransportEvaluator:
    """Focused tests for computed_transport_v1 evidence completeness."""

    def test_transport_rubric_registered(self):
        rubric = select_rubric("transport")
        assert rubric is COMPUTED_TRANSPORT_V1
        assert rubric.name == "computed_transport"
        assert rubric.version == 1

    def test_missing_loaded_transport_returns_hard_failed(self):
        result = evaluate_loaded_transport(None)
        assert result.label is EvidenceBadge.hard_failed
        assert result.record_type == "transport"
        assert result.record_id is None
        assert result.rubric == "computed_transport"
        assert result.hard_fail_reason is HardFailReason.transport_missing

    def test_sparse_computed_transport_has_low_evidence(self, db_session):
        transport = _make_transport(db_session, lj=True)
        result = evaluate_computed_transport(db_session, transport.id)
        assert result.label in {EvidenceBadge.sparse, EvidenceBadge.partial}
        assert "source_calculations_present" in result.missing_checks
        assert "source_calculation_lot_present" in result.missing_checks

    def test_species_and_lj_pair_checks_pass(self, db_session):
        transport = _make_transport(db_session, lj=True)
        result = evaluate_computed_transport(db_session, transport.id)
        assert "species_entry_present" in result.passed_checks
        assert "transport_origin_is_computed" in result.passed_checks
        assert "transport_model_present" in result.passed_checks
        assert "transport_property_present" in result.passed_checks
        assert "lj_pair_present_if_applicable" in result.passed_checks
        assert "sigma_present" in result.passed_checks
        assert "epsilon_present" in result.passed_checks
        assert "sigma_epsilon_pair_consistent" in result.passed_checks

    def test_dipole_and_polarizability_property_checks_pass(self, db_session):
        transport = _make_transport(
            db_session,
            lj=False,
            dipole=True,
            polarizability=True,
            rotational_relaxation=True,
        )
        result = evaluate_computed_transport(db_session, transport.id)
        assert "dipole_present" in result.passed_checks
        assert "polarizability_present" in result.passed_checks
        assert "rotational_relaxation_present" in result.passed_checks
        assert "lj_pair_present_if_applicable" in result.not_applicable_checks

    def test_missing_all_transport_properties_hard_fails(self, db_session):
        transport = _make_transport(
            db_session,
            lj=False,
            dipole=False,
            polarizability=False,
            rotational_relaxation=False,
        )
        result = evaluate_computed_transport(db_session, transport.id)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.no_transport_property_present
        assert "transport_property_present" in result.missing_checks

    def test_invalid_lj_pair_hard_fails_loaded_object(self, db_session):
        entry = _make_species_entry(db_session, _make_species(db_session))
        transport = Transport(
            species_entry_id=entry.id,
            species_entry=entry,
            scientific_origin=ScientificOriginKind.computed,
            sigma_angstrom=3.8,
            epsilon_over_k_k=None,
        )
        result = evaluate_loaded_transport(transport)
        assert result.label is EvidenceBadge.hard_failed
        assert result.hard_fail_reason is HardFailReason.invalid_lj_pair
        assert "sigma_epsilon_pair_consistent" in result.missing_checks

    def test_source_calculations_raise_evidence_completeness(self, db_session):
        sparse = _make_transport(db_session, lj=True)
        rich = _make_transport(db_session, lj=True)
        calc = _make_minimal_opt_calc(db_session, quality=CalculationQuality.curated)
        _link_transport_source(
            db_session,
            transport=rich,
            calculation=calc,
            role=TransportCalculationRole.full_transport,
        )

        sparse_result = evaluate_computed_transport(db_session, sparse.id)
        rich_result = evaluate_computed_transport(db_session, rich.id)

        assert rich_result.evidence_completeness > sparse_result.evidence_completeness
        assert "source_calculations_present" in rich_result.passed_checks
        assert "source_calculation_lot_present" in rich_result.passed_checks

    def test_transport_source_roles_pass_corresponding_checks(self, db_session):
        transport = _make_transport(
            db_session,
            lj=True,
            dipole=True,
            polarizability=True,
        )
        full_calc = _make_minimal_opt_calc(db_session)
        dipole_calc = _make_minimal_opt_calc(db_session)
        polarizability_calc = _make_minimal_opt_calc(db_session)
        geometry_calc = _make_minimal_opt_calc(db_session)
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=full_calc,
            role=TransportCalculationRole.full_transport,
        )
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=dipole_calc,
            role=TransportCalculationRole.dipole,
        )
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=polarizability_calc,
            role=TransportCalculationRole.polarizability,
        )
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=geometry_calc,
            role=TransportCalculationRole.supporting_geometry,
        )

        result = evaluate_computed_transport(db_session, transport.id)
        assert "full_transport_source_present" in result.passed_checks
        assert "dipole_source_present_if_dipole_present" in result.passed_checks
        assert (
            "polarizability_source_present_if_polarizability_present"
            in result.passed_checks
        )
        assert "supporting_geometry_source_present" in result.passed_checks

    def test_missing_optional_source_roles_not_hard_fail(self, db_session):
        transport = _make_transport(db_session, lj=True, dipole=True)
        result = evaluate_computed_transport(db_session, transport.id)
        assert "full_transport_source_present" in result.missing_checks
        assert "dipole_source_present_if_dipole_present" in result.missing_checks
        assert (
            "polarizability_source_present_if_polarizability_present"
            in result.not_applicable_checks
        )
        assert result.hard_fail_reason is None

    def test_source_geometry_failure_hard_fails_required_role(self, db_session):
        transport = _make_transport(db_session, lj=True)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.fail,
        )
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=calc,
            role=TransportCalculationRole.full_transport,
        )

        result = evaluate_computed_transport(db_session, transport.id)
        assert result.label is EvidenceBadge.hard_failed
        assert (
            result.hard_fail_reason
            is HardFailReason.source_calculation_hard_failed_for_required_role
        )
        assert (
            "source_calculation_has_non_hard_failed_evidence" in result.missing_checks
        )

    def test_source_calculation_rejected_quality_affects_transport(self, db_session):
        transport = _make_transport(db_session, lj=True)
        calc = _make_minimal_opt_calc(
            db_session,
            quality=CalculationQuality.rejected,
        )
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=calc,
            role=TransportCalculationRole.full_transport,
        )

        result = evaluate_computed_transport(db_session, transport.id)
        assert result.label is EvidenceBadge.hard_failed
        assert (
            result.hard_fail_reason
            is HardFailReason.source_calculation_hard_failed_for_required_role
        )

    def test_source_geometry_warning_is_advisory(self, db_session):
        transport = _make_transport(db_session, lj=True)
        calc = _make_minimal_opt_calc(
            db_session,
            geom_validation=ValidationStatus.warning,
        )
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=calc,
            role=TransportCalculationRole.full_transport,
        )

        result = evaluate_computed_transport(db_session, transport.id)
        assert (
            "geometry_validation_not_failed_for_source_calculations"
            in result.warning_checks
        )
        assert result.label is not EvidenceBadge.hard_failed

    def test_loaded_transport_matches_id_entrypoint(self, db_session):
        transport = _make_transport(db_session, lj=True, dipole=True)
        calc = _make_minimal_opt_calc(db_session)
        _link_transport_source(
            db_session,
            transport=transport,
            calculation=calc,
            role=TransportCalculationRole.full_transport,
        )

        by_id = evaluate_computed_transport(db_session, transport.id)
        loaded = evaluate_loaded_transport(transport)
        assert loaded == by_id

    def test_evaluator_does_not_mutate_transport(self, db_session):
        transport = _make_transport(db_session, lj=True, dipole=True)
        before_sigma = transport.sigma_angstrom
        before_dipole = transport.dipole_debye
        before_sources = len(transport.source_calculations)

        evaluate_computed_transport(db_session, transport.id)
        db_session.refresh(transport)

        assert transport.sigma_angstrom == before_sigma
        assert transport.dipole_debye == before_dipole
        assert len(transport.source_calculations) == before_sources

    def test_transport_evaluator_does_not_require_llm_config(
        self, db_session, monkeypatch
    ):
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "AZURE_OPENAI_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)

        transport = _make_transport(db_session, lj=True)
        result = evaluate_computed_transport(db_session, transport.id)
        assert result.rubric == "computed_transport"
        assert result.evidence_completeness >= 0.0
        assert result.is_certified is False
