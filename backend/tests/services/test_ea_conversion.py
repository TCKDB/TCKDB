"""Tests for activation energy unit conversion, A-units molecularity validation,
and upload schema validation."""

import pytest
from pydantic import ValidationError

from app.chemistry.units import convert_ea_to_kj_mol, validate_a_units_for_molecularity
from app.db.models.common import ActivationEnergyUnits, ArrheniusAUnits
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest


class TestConvertEaToKjMol:
    def test_kj_mol_identity(self) -> None:
        assert convert_ea_to_kj_mol(50.0, ActivationEnergyUnits.kj_mol) == 50.0

    def test_j_mol(self) -> None:
        result = convert_ea_to_kj_mol(41840.0, ActivationEnergyUnits.j_mol)
        assert abs(result - 41.84) < 1e-10

    def test_cal_mol(self) -> None:
        result = convert_ea_to_kj_mol(10000.0, ActivationEnergyUnits.cal_mol)
        assert abs(result - 41.84) < 1e-10

    def test_kcal_mol(self) -> None:
        result = convert_ea_to_kj_mol(10.0, ActivationEnergyUnits.kcal_mol)
        assert abs(result - 41.84) < 1e-10

    def test_zero(self) -> None:
        assert convert_ea_to_kj_mol(0.0, ActivationEnergyUnits.kcal_mol) == 0.0

    def test_negative(self) -> None:
        result = convert_ea_to_kj_mol(-10.0, ActivationEnergyUnits.kj_mol)
        assert result == -10.0


_MINIMAL_REACTION = {
    "reversible": True,
    "reactants": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
    ],
}


class TestKineticsUploadEaValidation:
    def test_accepts_reported_ea_with_units(self) -> None:
        req = KineticsUploadRequest(
            reaction=_MINIMAL_REACTION,
            scientific_origin="computed",
            reported_ea=10.0,
            reported_ea_units="kcal_mol",
        )
        assert req.reported_ea == 10.0
        assert req.reported_ea_units == ActivationEnergyUnits.kcal_mol

    def test_accepts_no_ea(self) -> None:
        req = KineticsUploadRequest(
            reaction=_MINIMAL_REACTION,
            scientific_origin="computed",
        )
        assert req.reported_ea is None
        assert req.reported_ea_units is None

    def test_rejects_ea_without_units(self) -> None:
        with pytest.raises(ValidationError, match="both be provided or both omitted"):
            KineticsUploadRequest(
                reaction=_MINIMAL_REACTION,
                scientific_origin="computed",
                reported_ea=10.0,
            )

    def test_rejects_units_without_ea(self) -> None:
        with pytest.raises(ValidationError, match="both be provided or both omitted"):
            KineticsUploadRequest(
                reaction=_MINIMAL_REACTION,
                scientific_origin="computed",
                reported_ea_units="kj_mol",
            )

    def test_rejects_invalid_units(self) -> None:
        with pytest.raises(ValidationError):
            KineticsUploadRequest(
                reaction=_MINIMAL_REACTION,
                scientific_origin="computed",
                reported_ea=10.0,
                reported_ea_units="eV",
            )


_H_SPECIES = {"smiles": "[H]", "charge": 0, "multiplicity": 2}


def _reaction_with_n_reactants(n: int) -> dict:
    return {
        "reversible": True,
        "reactants": [{"species_entry": _H_SPECIES} for _ in range(n)],
        "products": [{"species_entry": _H_SPECIES}],
    }


class TestValidateAUnitsForMolecularity:
    def test_unimolecular_accepts_per_s(self) -> None:
        validate_a_units_for_molecularity(ArrheniusAUnits.per_s, 1)

    def test_unimolecular_rejects_second_order(self) -> None:
        with pytest.raises(ValueError, match="incompatible with unimolecular"):
            validate_a_units_for_molecularity(ArrheniusAUnits.cm3_mol_s, 1)

    def test_bimolecular_accepts_cm3_mol_s(self) -> None:
        validate_a_units_for_molecularity(ArrheniusAUnits.cm3_mol_s, 2)

    def test_bimolecular_accepts_cm3_molecule_s(self) -> None:
        validate_a_units_for_molecularity(ArrheniusAUnits.cm3_molecule_s, 2)

    def test_bimolecular_accepts_m3_mol_s(self) -> None:
        validate_a_units_for_molecularity(ArrheniusAUnits.m3_mol_s, 2)

    def test_bimolecular_rejects_per_s(self) -> None:
        with pytest.raises(ValueError, match="incompatible with bimolecular"):
            validate_a_units_for_molecularity(ArrheniusAUnits.per_s, 2)

    def test_bimolecular_rejects_third_order(self) -> None:
        with pytest.raises(ValueError, match="incompatible with bimolecular"):
            validate_a_units_for_molecularity(ArrheniusAUnits.cm6_mol2_s, 2)

    def test_termolecular_accepts_cm6_mol2_s(self) -> None:
        validate_a_units_for_molecularity(ArrheniusAUnits.cm6_mol2_s, 3)

    def test_termolecular_accepts_m6_mol2_s(self) -> None:
        validate_a_units_for_molecularity(ArrheniusAUnits.m6_mol2_s, 3)

    def test_termolecular_rejects_per_s(self) -> None:
        with pytest.raises(ValueError, match="incompatible with termolecular"):
            validate_a_units_for_molecularity(ArrheniusAUnits.per_s, 3)

    def test_unsupported_molecularity(self) -> None:
        with pytest.raises(ValueError, match="Unsupported reaction molecularity"):
            validate_a_units_for_molecularity(ArrheniusAUnits.per_s, 4)


class TestKineticsUploadAUnitsMolecularity:
    def test_unimolecular_with_per_s_accepted(self) -> None:
        req = KineticsUploadRequest(
            reaction=_reaction_with_n_reactants(1),
            scientific_origin="computed",
            a=1e13,
            a_units="per_s",
        )
        assert req.a_units == ArrheniusAUnits.per_s

    def test_bimolecular_with_cm3_mol_s_accepted(self) -> None:
        req = KineticsUploadRequest(
            reaction=_reaction_with_n_reactants(2),
            scientific_origin="computed",
            a=1e12,
            a_units="cm3_mol_s",
        )
        assert req.a_units == ArrheniusAUnits.cm3_mol_s

    def test_bimolecular_with_per_s_rejected(self) -> None:
        with pytest.raises(ValidationError, match="incompatible with bimolecular"):
            KineticsUploadRequest(
                reaction=_reaction_with_n_reactants(2),
                scientific_origin="computed",
                a=1e12,
                a_units="per_s",
            )

    def test_unimolecular_with_cm3_mol_s_rejected(self) -> None:
        with pytest.raises(ValidationError, match="incompatible with unimolecular"):
            KineticsUploadRequest(
                reaction=_reaction_with_n_reactants(1),
                scientific_origin="computed",
                a=1e12,
                a_units="cm3_mol_s",
            )

    def test_no_a_units_skips_validation(self) -> None:
        req = KineticsUploadRequest(
            reaction=_reaction_with_n_reactants(2),
            scientific_origin="computed",
            a=1e12,
        )
        assert req.a_units is None
