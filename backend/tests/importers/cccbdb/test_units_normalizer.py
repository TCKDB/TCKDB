"""Unit-conversion tests for the CCCBDB importer normalizer."""

from __future__ import annotations

import math

import pytest

from app.importers.cccbdb.normalizers.units import (
    UnsupportedUnitError,
    canonical_unit_for,
    convert_to_canonical,
)


class TestEnergyConversions:
    def test_kj_per_mol_is_identity(self):
        value, unit = convert_to_canonical(-241.826, "kJ/mol", "energy")
        assert value == pytest.approx(-241.826)
        assert unit == "kJ/mol"

    def test_kcal_per_mol_converts_to_kj_per_mol(self):
        value, unit = convert_to_canonical(19.820, "kcal/mol", "energy")
        assert value == pytest.approx(19.820 * 4.184)
        assert unit == "kJ/mol"

    def test_kj_mol_minus_one_alias(self):
        value, unit = convert_to_canonical(1.0, "kJ mol-1", "energy")
        assert value == pytest.approx(1.0)
        assert unit == "kJ/mol"

    def test_hartree_converts_to_kj_per_mol(self):
        value, unit = convert_to_canonical(1.0, "hartree", "energy")
        # CODATA-ish 2625.5 kJ/mol per hartree, give or take.
        assert 2625.0 < value < 2626.0
        assert unit == "kJ/mol"


class TestEntropyHeatCapacityConversions:
    def test_j_per_mol_per_k_is_identity(self):
        value, unit = convert_to_canonical(
            188.834, "J/mol/K", "entropy_or_heat_capacity"
        )
        assert value == pytest.approx(188.834)
        assert unit == "J/mol/K"

    def test_cal_per_mol_per_k_converts(self):
        value, unit = convert_to_canonical(
            64.340, "cal/mol/K", "entropy_or_heat_capacity"
        )
        assert value == pytest.approx(64.340 * 4.184)
        assert unit == "J/mol/K"

    def test_j_k_minus_one_mol_minus_one_alias(self):
        value, unit = convert_to_canonical(
            1.0, "J K-1 mol-1", "entropy_or_heat_capacity"
        )
        assert value == pytest.approx(1.0)
        assert unit == "J/mol/K"


class TestFrequencyConversions:
    def test_cm_minus_one_remains_cm_minus_one(self):
        value, unit = convert_to_canonical(4401.21, "cm^-1", "frequency")
        assert value == pytest.approx(4401.21)
        assert unit == "cm^-1"

    @pytest.mark.parametrize("raw", ["cm-1", "1/cm", "wavenumber"])
    def test_frequency_aliases(self, raw):
        value, unit = convert_to_canonical(100.0, raw, "frequency")
        assert value == pytest.approx(100.0)
        assert unit == "cm^-1"


class TestRotationalConstantConversions:
    def test_mhz_converts_to_ghz(self):
        value, unit = convert_to_canonical(
            835_840.0, "MHz", "rotational_constant"
        )
        assert value == pytest.approx(835.840)
        assert unit == "GHz"

    def test_ghz_is_identity(self):
        value, unit = convert_to_canonical(
            835.840, "GHz", "rotational_constant"
        )
        assert value == pytest.approx(835.840)
        assert unit == "GHz"


class TestLengthConversions:
    def test_angstrom_is_identity(self):
        value, unit = convert_to_canonical(0.7572, "angstrom", "length")
        assert value == pytest.approx(0.7572)
        assert unit == "angstrom"

    def test_pm_converts_to_angstrom(self):
        value, unit = convert_to_canonical(120.0, "pm", "length")
        assert value == pytest.approx(1.20)
        assert unit == "angstrom"

    def test_bohr_converts_to_angstrom(self):
        value, unit = convert_to_canonical(1.0, "bohr", "length")
        # ~0.529 Å per bohr
        assert math.isclose(value, 0.529, abs_tol=1e-3)
        assert unit == "angstrom"


class TestUnsupportedUnits:
    def test_unknown_unit_raises_for_energy(self):
        with pytest.raises(UnsupportedUnitError) as excinfo:
            convert_to_canonical(1.0, "rydberg", "energy")
        assert excinfo.value.raw_units == "rydberg"
        assert excinfo.value.dimension == "energy"

    def test_unknown_unit_raises_for_frequency(self):
        with pytest.raises(UnsupportedUnitError):
            convert_to_canonical(1.0, "THz", "frequency")

    def test_canonical_unit_for(self):
        assert canonical_unit_for("energy") == "kJ/mol"
        assert canonical_unit_for("entropy_or_heat_capacity") == "J/mol/K"
        assert canonical_unit_for("frequency") == "cm^-1"
        assert canonical_unit_for("rotational_constant") == "GHz"
        assert canonical_unit_for("length") == "angstrom"
