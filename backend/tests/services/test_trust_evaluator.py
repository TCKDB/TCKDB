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
    ArtifactKind,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    MoleculeKind,
    ParameterSource,
    StereoKind,
    ValidationStatus,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.services.trust import (
    COMPUTED_CALCULATION_V1,
    EvidenceBadge,
    HardFailReason,
    evaluate_computed_calculation,
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


# ---------------------------------------------------------------------------
# 4. Provenance richness raises completeness
# ---------------------------------------------------------------------------


class TestEvidenceCompletenessOrdering:
    """Adding provenance to a calculation should not lower the badge."""

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
        calc = _make_minimal_opt_calc(
            db_session, geom_validation=ValidationStatus.fail
        )
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
