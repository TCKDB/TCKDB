"""Unit-conversion invariants.

Silent unit mistakes — a missing factor of 1000, a mis-mapped enum, a
swapped cal/J factor — cannot be caught by schema validation because the
number remains a valid ``float``. These tests pin the handful of
conversion paths that currently live in production code.
"""

from __future__ import annotations

import pytest

from app.chemistry.units import (
    convert_ea_to_kj_mol,
    validate_a_units_for_molecularity,
)
from app.db.models.common import ActivationEnergyUnits, ArrheniusAUnits

# ---------------------------------------------------------------------------
# Invariant 1: activation-energy unit conversion is physically correct
# ---------------------------------------------------------------------------


def test_activation_energy_kcal_to_kj_uses_thermochemical_calorie() -> None:
    """1 kcal/mol must convert to exactly 4.184 kJ/mol.

    The backend uses the thermochemical calorie (4.184 J). Swapping that
    for the 15 C calorie (4.1855 J) or the IT calorie (4.1868 J) would
    drift kinetics fits by a fraction of a percent — small enough to
    pass every sanity check but large enough to matter at high T.
    """
    assert convert_ea_to_kj_mol(1.0, ActivationEnergyUnits.kcal_mol) == pytest.approx(
        4.184, abs=0.0
    )


@pytest.mark.parametrize(
    "value_in,units,expected_kj",
    [
        (50.0, ActivationEnergyUnits.kj_mol, 50.0),
        (50_000.0, ActivationEnergyUnits.j_mol, 50.0),
        (10.0, ActivationEnergyUnits.kcal_mol, 41.84),
        (10_000.0, ActivationEnergyUnits.cal_mol, 41.84),
    ],
)
def test_activation_energy_conversion_round_trips_through_canonical_unit(
    value_in: float, units: ActivationEnergyUnits, expected_kj: float,
) -> None:
    """Equivalent activation energies expressed in any supported unit must
    collapse to the same kJ/mol value within float precision.

    This is the invariant the kinetics upload pipeline relies on for its
    canonical ``ea_kj_mol`` column — a regression here would silently
    change stored activation energies by orders of magnitude.
    """
    assert convert_ea_to_kj_mol(value_in, units) == pytest.approx(expected_kj, rel=1e-12)


def test_all_activation_energy_units_round_trip_equivalently() -> None:
    """Construct one reference value in kJ/mol, reverse-compute its value
    in every other supported unit, and confirm the round-trip lands back
    on the original within numerical precision."""
    reference_kj_mol = 42.0
    factors = {
        ActivationEnergyUnits.kj_mol: 1.0,
        ActivationEnergyUnits.j_mol: 1e3,
        ActivationEnergyUnits.kcal_mol: 1.0 / 4.184,
        ActivationEnergyUnits.cal_mol: 1e3 / 4.184,
    }
    for unit, multiplier in factors.items():
        reconstructed = convert_ea_to_kj_mol(reference_kj_mol * multiplier, unit)
        assert reconstructed == pytest.approx(reference_kj_mol, rel=1e-12), (
            f"{unit.value} round-trip produced {reconstructed}, expected {reference_kj_mol}"
        )


# ---------------------------------------------------------------------------
# Invariant 2: Arrhenius A-unit enum molecularity validator is consistent
# ---------------------------------------------------------------------------
#
# ``ArrheniusAUnits`` values get persisted as-is in Postgres enum form, and
# the kinetics upload validator rejects mismatches against reaction
# molecularity. This invariant test protects the mapping between the
# enum and the molecularity check — a silent edit to either would allow
# scientifically meaningless rate expressions through.


def test_every_arrhenius_a_unit_is_accepted_at_exactly_one_molecularity() -> None:
    """Each ``ArrheniusAUnits`` value must correspond to exactly one
    reaction order (1, 2, or 3). If a new unit is introduced without
    updating ``_A_UNITS_BY_ORDER`` this test fires loudly."""
    for a_units in ArrheniusAUnits:
        accepted = [
            molecularity for molecularity in (1, 2, 3)
            if _accepts(a_units, molecularity)
        ]
        assert len(accepted) == 1, (
            f"{a_units.value!r} should map to exactly one molecularity, "
            f"but validator accepts it at: {accepted}"
        )


def _accepts(a_units: ArrheniusAUnits, molecularity: int) -> bool:
    try:
        validate_a_units_for_molecularity(a_units, molecularity)
        return True
    except ValueError:
        return False


def test_arrhenius_a_units_enum_values_are_stable_tokens() -> None:
    """``ArrheniusAUnits`` members must use machine-friendly tokens.
    Persisted DB enum values depend on the member ``.value`` strings, so
    any renaming would require a migration. This test pins the canonical
    set so a rename gets caught early."""
    assert {a.value for a in ArrheniusAUnits} == {
        "per_s",
        "cm3_mol_s",
        "cm3_molecule_s",
        "m3_mol_s",
        "cm6_mol2_s",
        "cm6_molecule2_s",
        "m6_mol2_s",
    }
