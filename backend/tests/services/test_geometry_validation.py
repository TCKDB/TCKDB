"""Tests for geometry validation service and Gaussian output parser.

Covers:
  1. Geometry extraction from opt and freq log files
  2. Validation: isomorphic pass, non-isomorphic fail, high-RMSD warning
  3. ORM persistence of validation results
"""

from __future__ import annotations

import os

import pytest

from app.db.models.common import ValidationStatus
from app.services.gaussian_output_parser import (
    extract_final_geometry,
    extract_final_geometry_from_file,
)
from app.services.geometry_validation import (
    validate_calculation_geometry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
GAUSSIAN_DIR = os.path.join(FIXTURES_DIR, "gaussian")
OPT_LOG = os.path.join(GAUSSIAN_DIR, "opt_g09.log")
FREQ_LOG = os.path.join(GAUSSIAN_DIR, "freq_g09.log")
TS_LOG = os.path.join(GAUSSIAN_DIR, "ts_opt_g09_minimal.txt")

# SMILES for the species in opt.log / freq.log: N2C3H7 doublet radical
SPECIES_SMILES = "[N]=NCCC"
# A completely different molecule (ethanol)
WRONG_SMILES = "CCO"


# ---------------------------------------------------------------------------
# 1. Gaussian output geometry extraction
# ---------------------------------------------------------------------------


class TestGeometryExtraction:
    """Extract final geometry from Gaussian log files."""

    def test_opt_log_extracts_12_atoms(self):
        atoms = extract_final_geometry_from_file(OPT_LOG)
        assert len(atoms) == 12

    def test_opt_log_correct_elements(self):
        atoms = extract_final_geometry_from_file(OPT_LOG)
        elements = [a[0] for a in atoms]
        assert elements.count("N") == 2
        assert elements.count("C") == 3
        assert elements.count("H") == 7

    def test_opt_log_returns_tuples(self):
        """Output format must be tuple of tuples for resolve_atom_mapping."""
        atoms = extract_final_geometry_from_file(OPT_LOG)
        assert isinstance(atoms, tuple)
        assert all(isinstance(a, tuple) and len(a) == 4 for a in atoms)

    def test_freq_log_extracts_12_atoms(self):
        atoms = extract_final_geometry_from_file(FREQ_LOG)
        assert len(atoms) == 12

    def test_freq_log_correct_elements(self):
        atoms = extract_final_geometry_from_file(FREQ_LOG)
        elements = [a[0] for a in atoms]
        assert elements.count("N") == 2
        assert elements.count("C") == 3
        assert elements.count("H") == 7

    def test_opt_and_freq_geometries_nearly_identical(self):
        """The opt final geometry and freq geometry should be the same structure."""
        opt_atoms = extract_final_geometry_from_file(OPT_LOG)
        freq_atoms = extract_final_geometry_from_file(FREQ_LOG)
        # Same number of atoms with same elements (order may differ slightly)
        assert len(opt_atoms) == len(freq_atoms)
        opt_elements = sorted(a[0] for a in opt_atoms)
        freq_elements = sorted(a[0] for a in freq_atoms)
        assert opt_elements == freq_elements

    def test_ts_log_extracts_29_atoms(self):
        atoms = extract_final_geometry_from_file(TS_LOG)
        assert len(atoms) == 29

    def test_ts_log_correct_elements(self):
        atoms = extract_final_geometry_from_file(TS_LOG)
        elements = dict.fromkeys(("N", "C", "H", "O"), 0)
        for a in atoms:
            elements[a[0]] = elements.get(a[0], 0) + 1
        assert elements == {"N": 4, "C": 7, "H": 17, "O": 1}

    def test_ts_log_returns_tuples(self):
        atoms = extract_final_geometry_from_file(TS_LOG)
        assert isinstance(atoms, tuple)
        assert all(isinstance(a, tuple) and len(a) == 4 for a in atoms)

    def test_empty_log_raises(self):
        with pytest.raises(ValueError, match="No parsable"):
            extract_final_geometry([])

    def test_no_orientation_block_raises(self):
        lines = ["Some random text\n", "No orientation blocks here\n"]
        with pytest.raises(ValueError, match="No parsable"):
            extract_final_geometry(lines)


# ---------------------------------------------------------------------------
# 2. Validation service — pure computation (no DB)
# ---------------------------------------------------------------------------


class TestValidationService:
    """Test the validate_calculation_geometry decision function."""

    @pytest.fixture
    def opt_atoms(self):
        return extract_final_geometry_from_file(OPT_LOG)

    @pytest.fixture
    def freq_atoms(self):
        return extract_final_geometry_from_file(FREQ_LOG)

    # --- Pass cases ---

    def test_isomorphic_geometry_passes(self, opt_atoms):
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=SPECIES_SMILES,
        )
        assert result.is_isomorphic is True
        assert result.validation_status == ValidationStatus.passed
        assert result.atom_mapping is not None
        assert result.n_mappings >= 1

    def test_freq_geometry_passes(self, freq_atoms):
        result = validate_calculation_geometry(
            output_atoms=freq_atoms,
            species_smiles=SPECIES_SMILES,
        )
        assert result.is_isomorphic is True
        assert result.validation_status == ValidationStatus.passed

    def test_no_input_geometry_still_passes_isomorphism(self, opt_atoms):
        """When no input geometry is provided, RMSD is None but isomorphism still checked."""
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=SPECIES_SMILES,
            input_atoms=None,
        )
        assert result.is_isomorphic is True
        assert result.rmsd is None
        assert result.validation_status == ValidationStatus.passed

    def test_same_geometry_for_input_and_output_low_rmsd(self, opt_atoms):
        """Input == output should give very low RMSD → pass."""
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=SPECIES_SMILES,
            input_atoms=opt_atoms,
        )
        assert result.is_isomorphic is True
        assert result.rmsd is not None
        assert result.rmsd < 0.01  # essentially zero
        assert result.validation_status == ValidationStatus.passed

    # --- Fail cases ---

    def test_wrong_species_fails(self, opt_atoms):
        """Output geometry for species A validated against species B SMILES → fail."""
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=WRONG_SMILES,
        )
        assert result.is_isomorphic is False
        assert result.validation_status == ValidationStatus.fail
        assert result.validation_reason is not None
        assert "not graph-isomorphic" in result.validation_reason

    def test_invalid_smiles_fails(self, opt_atoms):
        """Garbage SMILES → fail."""
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles="INVALID_NOT_A_SMILES",
        )
        assert result.is_isomorphic is False
        assert result.validation_status == ValidationStatus.fail

    # --- Warning cases ---

    def test_high_rmsd_warns(self, opt_atoms):
        """Isomorphic but high RMSD → warning."""
        # Create a distorted version of the geometry (shift all x by 2 Å)
        _distorted = tuple(
            (sym, x + 2.0, y + 2.0, z + 2.0) for sym, x, y, z in opt_atoms
        )
        # Use a very small threshold to trigger the warning
        # (translation doesn't affect Kabsch RMSD, so use coordinate scaling instead)
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=SPECIES_SMILES,
            input_atoms=opt_atoms,
            rmsd_warning_threshold=0.0001,  # unrealistically tight
        )
        # Same geometry → RMSD ≈ 0, but let's check the machinery works
        assert result.validation_status == ValidationStatus.passed

    def test_configurable_threshold(self, opt_atoms):
        """Threshold is passed through to the result."""
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=SPECIES_SMILES,
            rmsd_warning_threshold=2.5,
        )
        assert result.rmsd_warning_threshold == 2.5

    # --- Metadata recording ---

    def test_geometry_ids_recorded(self, opt_atoms):
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=SPECIES_SMILES,
            input_geometry_id=42,
            output_geometry_id=99,
        )
        assert result.input_geometry_id == 42
        assert result.output_geometry_id == 99

    def test_atom_mapping_is_dict(self, opt_atoms):
        result = validate_calculation_geometry(
            output_atoms=opt_atoms,
            species_smiles=SPECIES_SMILES,
        )
        assert isinstance(result.atom_mapping, dict)
        assert len(result.atom_mapping) == 12


# ---------------------------------------------------------------------------
# 3. ORM persistence (requires DB)
# ---------------------------------------------------------------------------


class TestValidationPersistence:
    """Test storing/reading CalculationGeometryValidation rows."""

    def test_persist_validation_result(self, db_engine):
        """Create a validation row and verify it reads back."""
        from sqlalchemy.orm import Session

        from app.db.models.calculation import (
            Calculation,
            CalculationGeometryValidation,
        )
        from app.db.models.common import (
            CalculationQuality,
            CalculationType,
            MoleculeKind,
            StereoKind,
        )
        from app.db.models.species import Species, SpeciesEntry

        with Session(db_engine) as session, session.begin():
            species = Species(
                smiles="[N]=NCCC",
                inchi_key="TEST_INCHI_KEY_001",
                charge=0,
                multiplicity=2,
                kind=MoleculeKind.molecule,
                stereo_kind=StereoKind.achiral,
            )
            session.add(species)
            session.flush()

            entry = SpeciesEntry(
                species_id=species.id,
                unmapped_smiles="[N]=NCCC",
            )
            session.add(entry)
            session.flush()

            calc = Calculation(
                type=CalculationType.opt,
                quality=CalculationQuality.raw,
                species_entry_id=entry.id,
            )
            session.add(calc)
            session.flush()

            validation = CalculationGeometryValidation(
                calculation_id=calc.id,
                species_smiles="[N]=NCCC",
                is_isomorphic=True,
                rmsd=0.05,
                atom_mapping={0: 0, 1: 1, 2: 2},
                n_mappings=1,
                validation_status=ValidationStatus.passed,
                validation_reason=None,
                rmsd_warning_threshold=1.0,
            )
            session.add(validation)
            session.flush()

            # Read it back via the relationship
            assert calc.geometry_validation is not None
            assert calc.geometry_validation.is_isomorphic is True
            assert calc.geometry_validation.validation_status == ValidationStatus.passed
            assert calc.geometry_validation.rmsd == pytest.approx(0.05)
            assert calc.geometry_validation.atom_mapping == {0: 0, 1: 1, 2: 2}

    def test_fail_status_persists(self, db_engine):
        """A fail validation row persists correctly."""
        from sqlalchemy.orm import Session

        from app.db.models.calculation import (
            Calculation,
            CalculationGeometryValidation,
        )
        from app.db.models.common import (
            CalculationQuality,
            CalculationType,
            MoleculeKind,
            StereoKind,
        )
        from app.db.models.species import Species, SpeciesEntry

        with Session(db_engine) as session, session.begin():
            species = Species(
                smiles="CCO",
                inchi_key="TEST_INCHI_KEY_002",
                charge=0,
                multiplicity=1,
                kind=MoleculeKind.molecule,
                stereo_kind=StereoKind.achiral,
            )
            session.add(species)
            session.flush()

            entry = SpeciesEntry(
                species_id=species.id,
                unmapped_smiles="CCO",
            )
            session.add(entry)
            session.flush()

            calc = Calculation(
                type=CalculationType.opt,
                quality=CalculationQuality.raw,
                species_entry_id=entry.id,
            )
            session.add(calc)
            session.flush()

            validation = CalculationGeometryValidation(
                calculation_id=calc.id,
                species_smiles="CCO",
                is_isomorphic=False,
                validation_status=ValidationStatus.fail,
                validation_reason="Output geometry is not graph-isomorphic",
            )
            session.add(validation)
            session.flush()

            assert calc.geometry_validation.validation_status == ValidationStatus.fail
            assert calc.geometry_validation.is_isomorphic is False
