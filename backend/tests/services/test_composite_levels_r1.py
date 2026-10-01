"""R1 with a composite energy (ADR 0021, decision 4), and the thermo provenance picker.

``derive_levels`` is pure, so these tests say exactly which input produced which
answer. The ids are arbitrary labels: 1 = an opt's level, 2 = an sp's, 3 and 4 =
two composite levels, 5 / 6 = a composite scheme's internal geometry / frequency
levels.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.models.common import CalculationType, ThermoCalculationRole
from app.services.calculation_levels import RoleCalcInfo, derive_levels
from app.services.scientific_read.kinetics import _build_kinetics_levels, _CalcMeta
from app.services.scientific_read.thermo import _LOT_FILTER_ROLE_PRIORITY, _primary_calc_id

OPT = RoleCalcInfo(lot_id=1)
SP = RoleCalcInfo(lot_id=2)
COMPOSITE = RoleCalcInfo(lot_id=3)
OTHER_COMPOSITE = RoleCalcInfo(lot_id=4)
IMPORTED = RoleCalcInfo(lot_id=7)


def _composite(*, geometry: bool = True, recipe_geometry: int | None = 5, recipe_frequency: int | None = 6):
    return RoleCalcInfo(
        lot_id=3,
        has_output_geometry=geometry,
        recipe_geometry_lot_id=recipe_geometry,
        recipe_frequency_lot_id=recipe_frequency,
    )


# -- energy ---------------------------------------------------------------


def test_a_composite_outranks_an_sp_an_opt_and_an_imported():
    derived = derive_levels(opts=[OPT], sps=[SP], composites=[COMPOSITE], importeds=[IMPORTED])
    assert (derived.energy_lot_id, derived.energy_source) == (3, "composite")


def test_without_a_composite_an_sp_still_outranks_an_opt():
    derived = derive_levels(opts=[OPT], sps=[SP], importeds=[IMPORTED])
    assert (derived.energy_lot_id, derived.energy_source) == (2, "sp")


def test_without_a_composite_or_an_sp_an_opt_outranks_an_imported():
    derived = derive_levels(opts=[OPT], importeds=[IMPORTED])
    assert (derived.energy_lot_id, derived.energy_source) == (1, "opt")


def test_an_imported_energy_is_the_last_resort():
    derived = derive_levels(importeds=[IMPORTED])
    assert (derived.energy_lot_id, derived.energy_source) == (7, "imported")


def test_two_distinct_composite_levels_are_ambiguous_even_beside_an_sp():
    derived = derive_levels(sps=[SP], composites=[COMPOSITE, OTHER_COMPOSITE])
    assert (derived.energy_lot_id, derived.energy_source) == (None, "ambiguous")


def test_two_composites_at_one_level_are_not_ambiguous():
    derived = derive_levels(composites=[COMPOSITE, COMPOSITE])
    assert (derived.energy_lot_id, derived.energy_source) == (3, "composite")


def test_a_composite_with_no_resolved_level_is_not_evidence_of_a_different_level():
    derived = derive_levels(composites=[COMPOSITE, RoleCalcInfo(lot_id=None)])
    assert (derived.energy_lot_id, derived.energy_source) == (3, "composite")


# -- geometry and frequency -----------------------------------------------


def test_an_opt_and_a_freq_win_over_the_recipe():
    derived = derive_levels(opts=[OPT], freqs=[RoleCalcInfo(lot_id=8)], composites=[_composite()])
    assert (derived.geometry_lot_id, derived.geometry_source) == (1, "opt")
    assert (derived.frequency_lot_id, derived.frequency_source) == (8, "freq")


def test_a_composite_with_its_own_geometry_supplies_both_from_its_recipe():
    derived = derive_levels(composites=[_composite()])
    assert (derived.geometry_lot_id, derived.geometry_source) == (5, "composite_recipe")
    assert (derived.frequency_lot_id, derived.frequency_source) == (6, "composite_recipe")


def test_a_linked_freq_takes_the_frequency_but_the_geometry_stays_the_recipes():
    derived = derive_levels(freqs=[RoleCalcInfo(lot_id=8)], composites=[_composite()])
    assert (derived.geometry_lot_id, derived.geometry_source) == (5, "composite_recipe")
    assert (derived.frequency_lot_id, derived.frequency_source) == (8, "freq")


def test_an_opt_carrying_frequencies_beats_the_recipe_frequency():
    derived = derive_levels(opts=[RoleCalcInfo(lot_id=1, carries_frequencies=True)], composites=[_composite()])
    assert (derived.frequency_lot_id, derived.frequency_source) == (1, "opt")


def test_a_composite_without_an_output_geometry_says_nothing_about_geometry():
    derived = derive_levels(composites=[_composite(geometry=False)])
    assert (derived.geometry_lot_id, derived.geometry_source) == (None, None)
    assert (derived.frequency_lot_id, derived.frequency_source) == (None, None)
    assert derived.energy_source == "composite"


def test_a_scheme_that_does_not_state_a_level_supplies_none():
    """NULL in the scheme is absence, never a default."""
    derived = derive_levels(composites=[_composite(recipe_geometry=None, recipe_frequency=None)])
    assert (derived.geometry_lot_id, derived.geometry_source) == (None, None)
    assert (derived.frequency_lot_id, derived.frequency_source) == (None, None)


def test_the_first_composite_with_a_geometry_supplies_the_recipe():
    no_geometry = _composite(geometry=False, recipe_geometry=9, recipe_frequency=9)
    derived = derive_levels(composites=[no_geometry, _composite()])
    assert derived.geometry_lot_id == 5


# -- the thermo provenance picker aligns with R1 --------------------------


def _link(role: ThermoCalculationRole, calculation_id: int):
    return SimpleNamespace(role=role, calculation_id=calculation_id, calculation=None)


@pytest.mark.parametrize("composite_first", [True, False])
def test_the_thermo_picker_prefers_the_composite_to_the_sp_whatever_the_link_order(composite_first):
    links = [_link(ThermoCalculationRole.sp, 11), _link(ThermoCalculationRole.composite, 12)]
    if composite_first:
        links.reverse()
    assert _primary_calc_id(links) == 12


def test_the_thermo_picker_still_prefers_the_sp_to_freq_and_opt():
    links = [
        _link(ThermoCalculationRole.opt, 1),
        _link(ThermoCalculationRole.freq, 2),
        _link(ThermoCalculationRole.sp, 3),
    ]
    assert _primary_calc_id(links) == 3


def test_the_thermo_picker_energy_order_is_the_r1_energy_order():
    """composite before sp before opt, in the same order ``derive_levels`` ranks them."""
    energy_roles = [r for r in _LOT_FILTER_ROLE_PRIORITY if r is not ThermoCalculationRole.freq]
    assert energy_roles == [
        ThermoCalculationRole.composite,
        ThermoCalculationRole.sp,
        ThermoCalculationRole.opt,
    ]


# -- the kinetics read maps a ts_energy citation of a composite to composite ----


def _meta(calc_id: int, calc_type: CalculationType, lot_id: int, method: str):
    return _CalcMeta(
        id=calc_id,
        type=calc_type,
        transition_state_entry_id=1,
        lot_id=lot_id,
        lot_ref=f"lot_{lot_id}",
        lot_method=method,
        lot_basis=None,
        lot_dispersion=None,
        lot_solvent=None,
        software_release_id=None,
        software_release_ref=None,
        software_name=None,
        software_version=None,
        parameters_json=None,
    )


@pytest.mark.parametrize(
    ("energy_type", "expected_source"),
    [(CalculationType.composite, "composite"), (CalculationType.sp, "sp")],
)
def test_the_kinetics_levels_name_the_role_the_ts_energy_calculation_played(energy_type, expected_source):
    calc_meta = {
        10: _meta(10, CalculationType.opt, 1, "B3LYP"),
        11: _meta(11, energy_type, 3, "CBS-QB3"),
    }
    levels = _build_kinetics_levels(
        ts_opt_calc_id=10, ts_freq_calc_id=None, ts_sp_calc_id=11, calc_meta=calc_meta
    )
    assert levels.energy_source == expected_source
    assert levels.energy is not None and levels.energy.method == "CBS-QB3"
    assert levels.geometry is not None and levels.geometry.method == "B3LYP"
    assert levels.geometry_source == "opt"


def test_the_kinetics_levels_of_a_composite_with_no_opt_state_no_geometry():
    """The kinetics read has no session here, so it never invents a recipe level."""
    calc_meta = {11: _meta(11, CalculationType.composite, 3, "CBS-QB3")}
    levels = _build_kinetics_levels(
        ts_opt_calc_id=None, ts_freq_calc_id=None, ts_sp_calc_id=11, calc_meta=calc_meta
    )
    assert levels.energy_source == "composite"
    assert levels.geometry is None and levels.geometry_source is None
