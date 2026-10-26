"""``withhold_levels_from_calculations``: a level goes with the calculations that supplied it (ADR 0023, step 3).

The function is pure. Each test names the mutation it kills.
"""

from __future__ import annotations

from app.services.calculation_levels import RoleCalcInfo, derive_levels, withhold_levels_from_calculations

OPT_LOT, FREQ_LOT, SP_LOT, OTHER_LOT = 1, 2, 3, 4


def _roles(**kw):
    return {name: [(cid, RoleCalcInfo(lot_id=lot)) for cid, lot in pairs] for name, pairs in kw.items()}


def _derive(roles):
    return derive_levels(**{name: [info for _, info in pairs] for name, pairs in roles.items()})


def _mask(roles, withheld):
    return withhold_levels_from_calculations(_derive(roles), withheld=withheld, **roles)


def test_nothing_withheld_is_the_derivation_untouched():
    """Kills: altering the derivation when no profile withholds anything."""
    roles = _roles(opts=[(10, OPT_LOT)], sps=[(11, SP_LOT)])
    assert _mask(roles, set()) == _derive(roles)


def test_a_level_supplied_only_by_a_withheld_calculation_goes_with_its_source():
    """Kills: leaving the level, or its source, behind when its only supplier is withheld."""
    roles = _roles(opts=[(10, OPT_LOT)], freqs=[(12, FREQ_LOT)], sps=[(11, SP_LOT)])
    out = _mask(roles, {11})
    assert (out.energy_lot_id, out.energy_source) == (None, None)
    assert (out.geometry_lot_id, out.geometry_source) == (OPT_LOT, "opt")
    assert (out.frequency_lot_id, out.frequency_source) == (FREQ_LOT, "freq")


def test_the_optimisation_level_is_not_put_in_place_of_a_withheld_single_point():
    """Kills: dropping the withheld calculation BEFORE deriving (the energy would fall to the opt's level)."""
    roles = _roles(opts=[(10, OPT_LOT)], sps=[(11, SP_LOT)])
    assert _derive(_roles(opts=[(10, OPT_LOT)])).energy_source == "opt"  # the substitution this guards against
    out = _mask(roles, {11})
    assert out.energy_lot_id is None and out.energy_source is None


def test_a_level_another_shown_calculation_also_supplies_stays():
    """Kills: nulling a level while a shown calculation at the same level still supports it."""
    roles = _roles(sps=[(11, SP_LOT), (13, SP_LOT)])
    out = _mask(roles, {11})
    assert (out.energy_lot_id, out.energy_source) == (SP_LOT, "sp")


def test_the_pick_does_not_move_to_the_next_calculation_at_another_level():
    """Kills: promoting the next calculation of the role when the lowest-id one is withheld."""
    roles = _roles(opts=[(10, OPT_LOT), (14, OTHER_LOT)])
    out = _mask(roles, {10})
    assert out.geometry_lot_id is None and out.geometry_source is None


def test_an_unreviewed_calculation_is_not_in_the_withheld_set_and_keeps_its_level():
    """Kills: treating every calculation below the approved floor as withheld."""
    roles = _roles(freqs=[(12, FREQ_LOT)], sps=[(11, SP_LOT)])
    out = _mask(roles, {999})
    assert out.frequency_source == "freq" and out.energy_source == "sp"


def test_ambiguous_energy_is_nulled_only_when_every_disagreeing_calculation_is_withheld():
    """Kills: nulling an ambiguity a shown calculation still takes part in, or keeping one with no shown supplier."""
    roles = _roles(sps=[(11, SP_LOT), (13, OTHER_LOT)])
    assert _derive(roles).energy_source == "ambiguous"
    assert _mask(roles, {11}).energy_source == "ambiguous"
    assert _mask(roles, {11, 13}).energy_source is None


def test_a_frequency_level_taken_from_the_optimisation_goes_when_the_optimisation_does():
    """Kills: nulling only the geometry when the opt that also supplied the frequencies is withheld."""
    roles = {"opts": [(10, RoleCalcInfo(lot_id=OPT_LOT, carries_frequencies=True))]}
    derived = _derive(roles)
    assert derived.frequency_source == "opt"
    out = withhold_levels_from_calculations(derived, withheld={10}, **roles)
    assert (out.geometry_source, out.frequency_source, out.energy_source) == (None, None, None)
