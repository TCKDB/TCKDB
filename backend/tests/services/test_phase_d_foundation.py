"""Phase D foundation (WP-F): enthalpy evaluation and shared helpers.

Expectations come from hand formulas and SI constants, never from the
production evaluator. Every test that iterates asserts the exact number of
evaluated cases first, so an empty set cannot pass.
"""
from math import log

import pytest

from app.db.models.species import Species
from app.db.models.thermo import ThermoPoint
from app.services.consistency import engine
from app.services.consistency.stoichiometry import (
    element_balance,
    is_balanced,
    participant_slots,
    species_facts,
    species_scope_reason,
)
from tests.services.test_phase_d_numerical import R, _nasa9, _thermo

LOW = (3.0, 2e-3, -4e-7, 3e-11, -1e-15, 20.0, 7.0)
HIGH = (3.4, 1.1e-3, -2e-7, 1e-11, -3e-16, 120.0, 5.0)
NASA9 = (20.0, -5.0, 3.0, 2e-3, -4e-7, 3e-11, -1e-15, 12.0, 7.0)


def _h_nasa7_kj(a, t):
    """H/R = sum_{k=1..5} a_k T^k / k + a6, in kJ/mol."""
    return R * (sum(a[k - 1] * t ** k / k for k in range(1, 6)) + a[5]) / 1000.0


def _h_nasa9_kj(a, t):
    """H/R = -a1/T + a2 ln T + a3 T + a4 T^2/2 + a5 T^3/3 + a6 T^4/4 + a7 T^5/5 + a8, in kJ/mol."""
    return R * (-a[0] / t + a[1] * log(t) + a[2] * t + a[3] * t ** 2 / 2 + a[4] * t ** 3 / 3
                + a[5] * t ** 4 / 4 + a[6] * t ** 5 / 5 + a[7]) / 1000.0


def _nasa7_thermo(pressure=1.0):
    thermo = _thermo(pressure=pressure)
    for i in range(7):
        setattr(thermo.nasa, f"a{i + 1}", LOW[i])
        setattr(thermo.nasa, f"b{i + 1}", HIGH[i])
    return thermo


def _nasa9_thermo():
    thermo = _thermo()
    thermo.nasa = None
    interval = _nasa9(1, 200.0, 2000.0, 0.0)
    for i, value in enumerate(NASA9, 1):
        setattr(interval, f"a{i}", value)
    thermo.nasa9_intervals = [interval]
    return thermo


# -- enthalpy from fits (catches H converted with /1e3, or the wrong branch) --

NASA7_CASES = [(300.0, LOW), (600.0, LOW), (999.0, LOW), (1000.0, LOW), (1001.0, HIGH), (2500.0, HIGH)]


def test_nasa7_enthalpy_matches_the_hand_formula_on_both_branches():
    assert len(NASA7_CASES) == 6
    thermo = _nasa7_thermo()
    for temperature, coefficients in NASA7_CASES:
        value, reason = engine.evaluate(thermo, "nasa7", temperature, "h")
        assert reason is None
        assert value == pytest.approx(_h_nasa7_kj(coefficients, temperature), rel=1e-12)


NASA9_TEMPERATURES = [250.0, 600.0, 1500.0]


def test_nasa9_enthalpy_matches_the_hand_formula():
    assert len(NASA9_TEMPERATURES) == 3
    thermo = _nasa9_thermo()
    for temperature in NASA9_TEMPERATURES:
        value, reason = engine.evaluate(thermo, "nasa9", temperature, "h")
        assert reason is None
        assert value == pytest.approx(_h_nasa9_kj(NASA9, temperature), rel=1e-12)


def test_cp_and_entropy_conversions_are_unchanged_by_adding_h():
    thermo = _nasa7_thermo()
    t = 600.0
    cp, reason_cp = engine.evaluate(thermo, "nasa7", t, "cp")
    s, reason_s = engine.evaluate(thermo, "nasa7", t, "s")
    assert reason_cp is None and reason_s is None
    assert cp == pytest.approx(R * sum(LOW[k] * t ** k for k in range(5)), rel=1e-12)
    assert s == pytest.approx(R * (LOW[0] * log(t) + sum(LOW[k] * t ** k / k for k in range(1, 5)) + LOW[6]),
                              rel=1e-12)


def test_point_enthalpy_is_the_stored_kj_value():
    thermo = _thermo()
    thermo.points = [ThermoPoint(temperature_k=500.0, h_kj_mol=-74.5, cp_j_mol_k=40.0)]
    assert engine.evaluate(thermo, "point", 500.0, "h") == (-74.5, None)
    assert engine.evaluate(thermo, "point", 500.0, "cp") == (40.0, None)
    assert engine.evaluate(thermo, "point", 501.0, "h") == (None, "no_exact_matching_point")


# -- h298 is a value only at exactly 298.15 K --


@pytest.mark.parametrize("temperature,quantity,h298,expected", [
    (298.15, "h", -74.6, (-74.6, None)),
    (298.15000000001, "h", -74.6, (None, "no_exact_h298_value")),
    (298.0, "h", -74.6, (None, "no_exact_h298_value")),
    (300.0, "h", -74.6, (None, "no_exact_h298_value")),
    (298.15, "s", -74.6, (None, "no_exact_h298_value")),
    (298.15, "h", None, (None, "no_exact_h298_value")),
])
def test_h298_is_only_a_value_at_exactly_298_15(temperature, quantity, h298, expected):
    thermo = _thermo()
    thermo.h298_kj_mol = h298
    assert engine.evaluate(thermo, "h298", temperature, quantity) == expected


# -- pressure independence --


def test_pressure_independent_quantities_are_exactly_cp_and_h():
    assert engine.PRESSURE_INDEPENDENT_QUANTITIES == frozenset({"cp", "h"})


@pytest.mark.parametrize("quantity,expected", [
    ("h", None),
    ("cp", None),
    ("s", "missing_or_invalid_reference_pressure"),
    (None, "missing_or_invalid_reference_pressure"),
])
def test_missing_pressure_is_a_reason_for_entropy_but_not_for_enthalpy(quantity, expected):
    thermo = _nasa7_thermo(pressure=None)
    assert engine.gas_state_reason(thermo, quantity=quantity) == expected


def test_enthalpy_evaluates_without_a_pressure_and_does_not_depend_on_it():
    values = []
    for pressure in (None, 1.0, 2.5):
        value, reason = engine.evaluate(_nasa7_thermo(pressure=pressure), "nasa7", 700.0, "h")
        assert reason is None
        values.append(value)
    assert len(values) == 3
    assert values[0] == values[1] == values[2]
    value, reason = engine.evaluate(_nasa7_thermo(pressure=None), "nasa7", 700.0, "s")
    assert (value, reason) == (None, "missing_or_invalid_reference_pressure")


# -- single-branch evaluators --


def test_nasa7_branches_at_t_mid_differ_by_r_times_the_constant_difference():
    """a6 != b6 and every other coefficient equal: the branches differ only by R*(a6-b6)."""
    thermo = _thermo()
    same = (3.0, 2e-3, -4e-7, 3e-11, -1e-15)
    for i, value in enumerate(same, 1):
        setattr(thermo.nasa, f"a{i}", value)
        setattr(thermo.nasa, f"b{i}", value)
    thermo.nasa.a6, thermo.nasa.b6 = 20.0, 120.0
    t_mid = thermo.nasa.t_mid
    low, reason_low = engine.nasa7_branch(thermo, "low", t_mid, quantity="h")
    high, reason_high = engine.nasa7_branch(thermo, "high", t_mid, quantity="h")
    assert reason_low is None and reason_high is None
    h_low, h_high = low.h(t_mid) / 1e6, high.h(t_mid) / 1e6
    assert h_low - h_high == pytest.approx(R * (20.0 - 120.0) / 1000.0, rel=1e-9)
    assert h_low == pytest.approx(_h_nasa7_kj((*same, 20.0, 0.0), t_mid), rel=1e-12)
    assert h_high == pytest.approx(_h_nasa7_kj((*same, 120.0, 0.0), t_mid), rel=1e-12)
    # Measured Cantera 3.2.0 behaviour the combined fit relies on: exactly at
    # t_mid the combined NASA7 answers with the LOW branch.
    combined, value_reason = engine.evaluate(thermo, "nasa7", t_mid, "h")
    assert value_reason is None
    assert combined == h_low


def test_each_nasa7_branch_evaluates_its_own_coefficients_across_its_closed_interval():
    thermo = _nasa7_thermo()
    cases = [("low", 200.0, LOW), ("low", 1000.0, LOW), ("high", 1000.0, HIGH), ("high", 3000.0, HIGH)]
    assert len(cases) == 4
    for branch, t, coefficients in cases:
        poly, reason = engine.nasa7_branch(thermo, branch, t, quantity="h")
        assert reason is None
        assert poly.h(t) / 1e6 == pytest.approx(_h_nasa7_kj(coefficients, t), rel=1e-12)


@pytest.mark.parametrize("branch,temperature", [("low", 1000.5), ("high", 999.5)])
def test_nasa7_branch_outside_its_interval_is_a_reason(branch, temperature):
    thermo = _nasa7_thermo()
    assert engine.nasa7_branch(thermo, branch, temperature, quantity="h") == (
        None, "temperature_outside_nasa7_branch")


def test_nasa7_branch_name_is_checked():
    with pytest.raises(ValueError, match="branch"):
        engine.nasa7_branch(_nasa7_thermo(), "middle", 500.0)


def test_nasa9_interval_evaluates_the_named_region_at_a_shared_boundary():
    thermo = _thermo()
    thermo.nasa = None
    thermo.nasa9_intervals = [_nasa9(2, 1000.0, 2000.0, 5.0), _nasa9(1, 200.0, 1000.0, 3.0)]
    # a3 is the only non-zero coefficient: H/R = a3*T.
    lower, reason_lower = engine.nasa9_interval(thermo, 1, 1000.0, quantity="h")
    upper, reason_upper = engine.nasa9_interval(thermo, 2, 1000.0, quantity="h")
    assert reason_lower is None and reason_upper is None
    assert lower.h(1000.0) / 1e6 == pytest.approx(R * 3.0 * 1000.0 / 1000.0, rel=1e-12)
    assert upper.h(1000.0) / 1e6 == pytest.approx(R * 5.0 * 1000.0 / 1000.0, rel=1e-12)
    # The combined evaluator gives the boundary to the upper interval.
    combined, reason = engine.evaluate(thermo, "nasa9", 1000.0, "h")
    assert reason is None
    assert combined == pytest.approx(R * 5.0, rel=1e-12)
    assert engine.nasa9_interval(thermo, 1, 1000.5, quantity="h") == (None, "temperature_outside_nasa9_interval")
    assert engine.nasa9_interval(thermo, 3, 1000.0, quantity="h") == (None, "nasa9_interval_not_found")


def test_nasa9_interval_never_bridges_an_invalid_set():
    thermo = _thermo()
    thermo.nasa = None
    thermo.nasa9_intervals = [_nasa9(1, 200.0, 1100.0, 3.0), _nasa9(2, 1000.0, 2000.0, 5.0)]
    assert engine.nasa9_interval(thermo, 1, 500.0, quantity="h") == (None, "invalid_nasa9_intervals")


# -- stoichiometry: neutral facts --


def _species(smiles, charge=0):
    return Species(smiles=smiles, charge=charge)


class _Participant:
    def __init__(self, key, role, smiles):
        self.species_entry_id = key
        self.role = role
        self.species_entry = type("Entry", (), {"species": _species(smiles)})()


def test_slots_count_repeated_participants_not_unique_species():
    """2 CH3 -> C2H6 lists CH3 in two slots; unique counting would unbalance it."""
    participants = [_Participant(1, "reactant", "[CH3]"), _Participant(1, "reactant", "[CH3]"),
                    _Participant(2, "product", "CC")]
    slots = participant_slots(participants)
    assert slots.accounted
    assert (slots.coefficient(1), slots.coefficient(2)) == (-2, 1)
    compositions = {key: species_facts(e.species).composition for key, e in slots.entries.items()}
    assert compositions == {1: {"C": 1, "H": 3}, 2: {"C": 2, "H": 6}}
    balance, charge = element_balance(slots, compositions, {1: 0, 2: 0})
    assert (dict(balance), charge) == ({"C": 0, "H": 0}, 0)
    assert is_balanced(balance, charge)
    one_slot = participant_slots(participants[1:])
    assert not is_balanced(*element_balance(one_slot, compositions, {1: 0, 2: 0}))


def test_a_role_that_is_neither_side_leaves_slots_unaccounted():
    slots = participant_slots([_Participant(1, "reactant", "C"), _Participant(2, "spectator", "C")])
    assert not slots.accounted


def test_species_facts_report_without_judging():
    facts = species_facts(_species("[2H]C([2H])([2H])[2H]"))
    assert (facts.parsed, facts.has_isotopes, facts.has_dummy_atoms) == (True, True, False)
    assert facts.composition == {"C": 1, "H": 4}
    assert species_facts(_species("C((")).parsed is False


# -- species scope: one case per reason, plus in scope --


@pytest.mark.parametrize("smiles,charge,expected", [
    ("C", 0, None),
    ("[OH-]", -1, "charged_species_out_of_scope"),
    ("[NH4+]", 1, "charged_species_out_of_scope"),
    ("[2H]C([2H])([2H])[2H]", 0, "isotope_labelled_species_out_of_scope"),
    ("[13CH4]", 0, "isotope_labelled_species_out_of_scope"),
    ("C((", 0, "unusable_species_composition"),
    ("", 0, "unusable_species_composition"),
    ("*C", 0, "unusable_species_composition"),
    ("C", None, "unusable_species_composition"),
    # Charge is read from the column, so it is reported even on an unreadable SMILES.
    ("C((", 1, "charged_species_out_of_scope"),
])
def test_species_scope_reason(smiles, charge, expected):
    assert species_scope_reason(_species(smiles, charge)) == expected
