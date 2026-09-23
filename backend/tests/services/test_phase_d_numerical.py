"""Independent analytical fixtures for Phase D; no production evaluator oracle."""

import builtins
import json
import sys
import types
from math import exp, log

import pytest

from app.db.models.common import PhaseKind
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.db.models.species import Species, SpeciesEntry
from app.db.models.thermo import Thermo, ThermoNASA, ThermoNASA9Interval, ThermoPoint, ThermoWilhoit
from app.services.consistency import engine
from app.services.consistency.kinetics import compare_kinetics
from app.services.consistency.thermo import compare_thermo

# SI defining constants, independent of Cantera and the production module.
R = 8.31446261815324
AVOGADRO = 6.02214076e23


def _thermo(key=1, *, cp_r=3.5, entropy_constant=2.0, pressure=1.0, smiles="[H][H]", charge=0):
    species = Species(id=key, public_ref=f"sp_test_{key}", smiles=smiles, charge=charge)
    entry = SpeciesEntry(id=key, public_ref=f"spe_test_{key}", species_id=key, species=species)
    row = Thermo(
        id=key, public_ref=f"th_test_{key}", species_entry_id=key, species_entry=entry,
        scientific_origin="computed", phase="gas", reference_pressure_bar=pressure,
        tmin_k=200.0, tmax_k=3000.0,
    )
    coefficients = {f"{p}{i}": 0.0 for p in ("a", "b") for i in range(1, 8)}
    coefficients.update(a1=cp_r, b1=cp_r, a7=entropy_constant, b7=entropy_constant)
    row.nasa = ThermoNASA(t_low=200.0, t_mid=1000.0, t_high=3000.0, **coefficients)
    return row


def _nasa9(index, low, high, cp_r):
    coefficients = {f"a{i}": 0.0 for i in range(1, 10)}
    coefficients["a3"] = cp_r
    return ThermoNASA9Interval(interval_index=index, t_min_k=low, t_max_k=high, **coefficients)


def _details(result, key):
    rows = [json.loads(f.message) for f in result.findings]
    matches = [row for row in rows if key in row]
    assert matches, f"zero evaluated/diagnostic cases containing {key}"
    assert all(f.severity == "info" for f in result.findings)
    return matches


def test_nasa7_cp_entropy_and_s298_are_independently_analytical():
    thermo = _thermo()
    thermo.nasa.b1 = 4.5
    thermo.s298_j_mol_k = R * (3.5 * log(298.15) + 2.0) + 3.0
    thermo.points = [
        ThermoPoint(temperature_k=t, cp_j_mol_k=cp * R + 2.0,
                    s_j_mol_k=R * (cp * log(t) + 2.0) + 4.0)
        for t, cp in ((298.15, 3.5), (1000.0, 3.5), (1500.0, 4.5))
    ]
    rows = _details(compare_thermo(thermo), "residual")
    fit_points = [r for r in rows if r["left"]["representation"] == "nasa7"
                  and r["right"]["representation"] == "point"]
    assert len(fit_points) == 6
    for row in fit_points:
        assert row["reason"] is None
        assert row["residual"] == pytest.approx(-2.0 if row["quantity"] == "cp" else -4.0)
    scalar = [r for r in rows if r["left"]["representation"] == "nasa7"
              and r["right"]["representation"] == "s298" and r["temperature_k"] == 298.15]
    assert len(scalar) == 1
    assert scalar[0]["residual"] == pytest.approx(-3.0)


def test_every_fit_is_compared_and_disagreement_is_not_hidden():
    thermo = _thermo(cp_r=3.0)
    thermo.nasa9_intervals = [_nasa9(1, 200.0, 3000.0, 5.0)]
    thermo.points = [ThermoPoint(temperature_k=500.0, cp_j_mol_k=4.0 * R)]
    rows = [r for r in _details(compare_thermo(thermo), "residual") if r["quantity"] == "cp"]
    assert len(rows) == 3
    by_pair = {(r["left"]["representation"], r["right"]["representation"]): r for r in rows}
    assert by_pair["nasa7", "nasa9"]["residual"] == pytest.approx(-2 * R)
    assert by_pair["nasa7", "point"]["residual"] == pytest.approx(-R)
    assert by_pair["nasa9", "point"]["residual"] == pytest.approx(R)


@pytest.mark.parametrize("representation", ["nasa7", "nasa9"])
def test_full_nasa_coefficient_cp_and_entropy_formulas(representation):
    thermo = _thermo()
    temperature = 600.0
    if representation == "nasa7":
        a = (3.0, 2e-3, -4e-7, 3e-11, -1e-15, 20.0, 7.0)
        for i, coefficient in enumerate(a, 1):
            setattr(thermo.nasa, f"a{i}", coefficient)
        expected_cp = R * sum(a[i] * temperature ** i for i in range(5))
        expected_s = R * (a[0] * log(temperature) + sum(
            a[i] * temperature ** i / i for i in range(1, 5)) + a[6])
    else:
        thermo.nasa = None
        a = (20.0, -5.0, 3.0, 2e-3, -4e-7, 3e-11, -1e-15, 12.0, 7.0)
        interval = _nasa9(1, 200, 2000, 3)
        for i, coefficient in enumerate(a, 1):
            setattr(interval, f"a{i}", coefficient)
        thermo.nasa9_intervals = [interval]
        expected_cp = R * sum(a[i] * temperature ** (i - 2) for i in range(7))
        expected_s = R * (-a[0] / (2 * temperature ** 2) - a[1] / temperature
                          + a[2] * log(temperature) + sum(
                              a[i] * temperature ** (i - 2) / (i - 2) for i in range(3, 7)) + a[8])
    thermo.points = [ThermoPoint(temperature_k=temperature, cp_j_mol_k=expected_cp, s_j_mol_k=expected_s)]
    rows = _details(compare_thermo(thermo), "residual")
    assert len(rows) == 2
    assert all(r["reason"] is None and abs(r["residual"]) < 1e-10 for r in rows)


@pytest.mark.parametrize("temperature,expected", [(999.0, 3.0), (1000.0, 5.0), (2000.0, 5.0)])
def test_nasa9_upper_interval_owns_shared_boundary(temperature, expected):
    thermo = _thermo()
    thermo.nasa = None
    thermo.nasa9_intervals = [_nasa9(2, 1000, 2000, 5), _nasa9(1, 200, 1000, 3)]
    thermo.points = [ThermoPoint(temperature_k=temperature, cp_j_mol_k=0.0)]
    row = next(r for r in _details(compare_thermo(thermo), "residual") if r["quantity"] == "cp")
    assert row["reason"] is None
    assert row["residual"] == pytest.approx(expected * R)


def test_nasa9_gap_is_visible_and_never_extrapolated():
    thermo = _thermo()
    thermo.nasa = None
    thermo.nasa9_intervals = [_nasa9(1, 200, 700, 3), _nasa9(2, 900, 2000, 5)]
    thermo.points = [ThermoPoint(temperature_k=800, cp_j_mol_k=30)]
    rows = _details(compare_thermo(thermo), "residual")
    assert all(r["reason"] == "temperature_outside_fit_or_in_gap" for r in rows)
    assert all(r["residual"] is None for r in rows)


def test_neighbour_comparison_requires_exact_points_and_preserves_pressure():
    left = _thermo(pressure=2.5)
    right = _thermo(pressure=2.5)
    right.public_ref = "th_neighbour"
    right.nasa = None
    right.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=3.5 * R,
                               s_j_mol_k=R * (3.5 * log(500) + 2))]
    poly, reason = engine.polynomial(left, "nasa7", 500)
    assert reason is None
    assert poly.reference_pressure == 250000.0
    with pytest.raises(ValueError, match="explicit temperatures"):
        compare_thermo(left, comparison=right)
    rows = _details(compare_thermo(left, comparison=right, temperature_grid=[500, 500.00001]), "residual")
    exact = [r for r in rows if r["temperature_k"] == 500]
    missed = [r for r in rows if r["temperature_k"] != 500]
    assert len(exact) == len(missed) == 2
    assert all(r["residual"] == pytest.approx(0, abs=1e-10) for r in exact)
    assert all(r["reason"] == "no_exact_matching_point" and r["residual"] is None for r in missed)


def test_neighbour_comparison_across_species_entries_is_unavailable():
    """Review round 2, guard #2: ``different_species_entries`` had no test.

    Two species entries (keys 1 and 2, distinct ``species_entry_id``) with
    matching exact points at the same temperature -- if this guard were
    deleted the residual would read 0 (a spurious pass comparing one
    species' Cp/S against another's), instead of the unavailable reason.
    """
    left = _thermo(1)
    right = _thermo(2)
    right.public_ref = "th_test_2_neighbour"
    same_point = {"temperature_k": 500.0, "cp_j_mol_k": 3.5 * R, "s_j_mol_k": R * (3.5 * log(500.0) + 2.0)}
    left.points = [ThermoPoint(**same_point)]
    right.points = [ThermoPoint(**same_point)]

    rows = _details(compare_thermo(left, comparison=right, temperature_grid=[500.0]), "residual")
    assert rows
    assert all(r["reason"] == "different_species_entries" for r in rows)
    assert all(r["residual"] is None for r in rows)


def test_temperature_outside_record_range_is_visible_even_within_fit_bounds():
    """Review round 2, guard #4: the record's own tmin_k/tmax_k, independent
    of the NASA fit's own [t_low, t_high]. Here the fit bounds are [200,
    3000] but the record's tmin_k is narrowed to 500, and 300 K sits inside
    the fit's bounds -- so only the record-range guard, not the fit-range
    guard, can catch it.
    """
    thermo = _thermo()
    thermo.tmin_k = 500.0
    poly, reason = engine.polynomial(thermo, "nasa7", 300.0)
    assert poly is None
    assert reason == "temperature_outside_record_range"


def test_mismatched_cantera_version_is_a_configuration_error(monkeypatch):
    """Review round 2, guard #5: no test exercised the runtime version pin."""
    fake_cantera = types.SimpleNamespace(__version__="3.1.0")
    monkeypatch.setitem(sys.modules, "cantera", fake_cantera)
    with pytest.raises(engine.ConfigurationError, match="3.2.0"):
        engine.cantera()


def test_thermo_with_only_a_fit_and_no_points_or_s298_is_unavailable():
    """Review round 2, guard #6: ``no_comparison_pairs_or_temperatures``.

    A single NASA-7 fit and nothing else gives exactly one representation
    per quantity, so there is no pair to compare and no exact point/s298
    temperature to grid on -- the defence against a silent empty-set pass.
    """
    thermo = _thermo()  # NASA-7 only: no points, no s298
    rows = _details(compare_thermo(thermo), "quantity")
    assert len(rows) == 2
    assert {r["quantity"] for r in rows} == {"cp", "s"}
    assert all(r["reason"] == "no_comparison_pairs_or_temperatures" for r in rows)


@pytest.mark.parametrize("field,value,reason", [
    # A phase never recorded (documented on Thermo.phase as unspecified,
    # never as non-gas -- backend/app/db/models/thermo.py) is a different
    # situation from a phase explicitly recorded as one this check does
    # not support, and must not collapse to the same reason (decided
    # 2026-09-23; see engine.gas_state_reason and engine.SUPPORTED_PHASES).
    ("phase", None, "phase_not_recorded"),
    ("phase", "liquid", "non_gas_phase_unsupported"),
])
def test_thermo_missing_or_unsupported_phase_remains_visible(field, value, reason):
    """Each parametrize case pins a distinct reason token, so this goes red
    under the mutation that collapses ``phase_not_recorded`` and
    ``non_gas_phase_unsupported`` back into one shared reason -- whichever
    single token the collapse picks, at least one of the two cases here
    expects the other and fails.
    """
    thermo = _thermo()
    setattr(thermo, field, value)
    thermo.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=30, s_j_mol_k=100)]
    rows = _details(compare_thermo(thermo), "residual")
    assert all(r["reason"] == reason and r["residual"] is None for r in rows)


def test_supported_phases_constant_is_what_the_check_consults(monkeypatch):
    """``gas_state_reason`` must read ``engine.SUPPORTED_PHASES`` rather than
    compare against a hardcoded phase value, so widening scope later is a
    one-line change to the constant (decided 2026-09-23).

    Mutation: hardcode ``if thermo.phase != PhaseKind.gas`` in
    ``gas_state_reason`` instead of consulting the constant -- monkeypatching
    ``SUPPORTED_PHASES`` then no longer changes the outcome, and the
    previously-refused liquid-phase record stays unavailable instead of
    becoming comparable, so this goes red.
    """
    thermo = _thermo()
    thermo.phase = PhaseKind.liquid
    thermo.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=30, s_j_mol_k=100)]

    rows = _details(compare_thermo(thermo), "residual")
    assert all(r["reason"] == "non_gas_phase_unsupported" and r["residual"] is None for r in rows)

    monkeypatch.setattr(engine, "SUPPORTED_PHASES", frozenset({PhaseKind.gas, PhaseKind.liquid}))

    rows = _details(compare_thermo(thermo), "residual")
    assert any(r["reason"] is None and r["residual"] is not None for r in rows)


def test_missing_reference_pressure_blocks_entropy_but_not_heat_capacity():
    """Decision (2026-09-23): a missing ``reference_pressure_bar`` makes
    entropy unavailable (it fixes the standard-state pressure baked into
    the entropy coefficient) but must not block heat capacity, since
    Cantera returns an identical Cp at any reference pressure. Was one
    undifferentiated case in ``test_thermo_missing_state_remains_visible``
    above until the D2/D1 pressure-applicability split (review round 2).
    """
    thermo = _thermo()
    thermo.reference_pressure_bar = None
    thermo.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=30, s_j_mol_k=100)]
    rows = _details(compare_thermo(thermo), "residual")
    cp_rows = [r for r in rows if r["quantity"] == "cp"]
    s_rows = [r for r in rows if r["quantity"] == "s"]
    assert cp_rows and s_rows
    assert all(r["reason"] is None and r["residual"] is not None for r in cp_rows)
    assert all(r["reason"] == "missing_or_invalid_reference_pressure" and r["residual"] is None for r in s_rows)


def test_unsupported_wilhoit_and_missing_uncertainty_remain_visible():
    thermo = _thermo()
    thermo.nasa = None
    thermo.wilhoit = ThermoWilhoit()
    thermo.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=30)]
    thermo.s298_uncertainty_j_mol_k = 0.75
    result = compare_thermo(thermo)
    assert all(r["reason"] == "unsupported_representation" for r in _details(result, "residual"))
    metadata = _details(result, "input")
    # s298_uncertainty_j_mol_k is a real field read off ``thermo`` -- pin a
    # non-None value so this actually exercises the pass-through rather than
    # merely confirming an unset default. fit_and_point_uncertainty is a
    # hardcoded ``None`` placeholder in thermo.py's payload (no fit/point
    # uncertainty is modeled yet), so asserting it is None can never fail
    # and is not checked here -- see thermo.py's finding payload.
    assert metadata[0]["s298_uncertainty_j_mol_k"] == pytest.approx(0.75)


def _reaction(*, pressure=1.0, reverse_scale=1.0, reverse_units="m3_mol_s"):
    # H2 -> 2 H. These NASA polynomials give ΔCp=ΔH=ΔS=0 exactly.
    molecule = _thermo(1, cp_r=2, entropy_constant=0, pressure=pressure)
    atom = _thermo(2, cp_r=1, entropy_constant=0, pressure=pressure, smiles="[H]")
    entry = ReactionEntry(id=1, public_ref="re_test")
    entry.structure_participants = [
        ReactionEntryStructureParticipant(species_entry_id=t.species_entry_id, species_entry=t.species_entry,
                                          role=role, participant_index=index)
        for t, role, index in ((molecule, "reactant", 1), (atom, "product", 1), (atom, "product", 2))
    ]
    # Production uses Cantera; this fixture derives reverse coefficients from
    # Kp=1 and Kc=p0/(R_kmol*T), using only SI constants above.
    a_forward, exponent, ea = 2.0, 0.5, 7.0
    a_reverse = a_forward * R / (pressure * 100000.0) * reverse_scale
    if reverse_units == "cm3_mol_s":
        a_reverse *= 1e6
    elif reverse_units == "cm3_molecule_s":
        a_reverse *= 1e6 / AVOGADRO
    rates = []
    for key, direction, a, n, units in (
        (1, "forward", a_forward, exponent, "per_s"),
        (2, "reverse", a_reverse, exponent + 1, reverse_units),
    ):
        rates.append(Kinetics(id=key, public_ref=f"kin_test_{key}", reaction_entry_id=1,
                              reaction_entry=entry, direction=direction, model_kind="modified_arrhenius",
                              a=a, n=n, ea_kj_mol=ea, a_units=units, is_third_body=False,
                              tmin_k=200, tmax_k=3000, pressure_context="high_p_limit",
                              degeneracy_convention="already_applied"))
    return *rates, {1: molecule, 2: atom}


@pytest.mark.parametrize("pressure", [1.0, 1.01325, 2.5])
@pytest.mark.parametrize("units", ["m3_mol_s", "cm3_mol_s", "cm3_molecule_s"])
def test_elementary_rates_match_independent_equilibrium_and_unit_factors(pressure, units):
    forward, reverse, mapping = _reaction(pressure=pressure, reverse_units=units)
    result = compare_kinetics(forward, reverse, mapping, temperature_grid=[300, 700, 1500])
    rows = _details(result, "k_forward")
    assert len(rows) == 3
    for row in rows:
        temperature = row["temperature_k"]
        expected_forward = 2 * temperature ** 0.5 * exp(-7000 / (R * temperature))
        expected_kc = pressure * 100000 / (1000 * R * temperature)
        assert row["k_forward"] == pytest.approx(expected_forward, rel=1e-12)
        assert row["equilibrium_kc"] == pytest.approx(expected_kc, rel=1e-12)
        assert row["k_reverse_supplied"] == pytest.approx(expected_forward / expected_kc, rel=1e-12)
        assert row["k_reverse_thermo"] == pytest.approx(expected_forward / expected_kc, rel=1e-12)
        assert row["log_ratio_residual"] == pytest.approx(0, abs=1e-12)
        assert row["reference_pressure_bar"] == pressure


def test_supplied_reverse_disagreement_is_informational_and_not_synthesized():
    forward, reverse, mapping = _reaction(reverse_scale=1.5)
    rows = _details(compare_kinetics(forward, reverse, mapping, temperature_grid=[700]), "k_forward")
    assert len(rows) == 1
    assert rows[0]["k_reverse_supplied"] == pytest.approx(1.5 * rows[0]["k_reverse_thermo"])
    assert rows[0]["reverse_residual"] == pytest.approx(0.5 * rows[0]["k_reverse_thermo"])
    assert rows[0]["log_ratio_residual"] == pytest.approx(-log(1.5))


@pytest.mark.parametrize("field,value,reason", [
    ("direction", "forward", "explicit_opposite_directions_required"),
    ("is_third_body", True, "non_elementary_or_ambiguous_effective_rate"),
    ("pressure_context", "apparent_at_pressure", "non_elementary_or_ambiguous_effective_rate"),
    ("degeneracy_convention", "unknown", "ambiguous_degeneracy_convention"),
    ("a_units", "per_s", "incompatible_or_missing_rate_units"),
    ("tmin_k", None, "missing_or_invalid_rate_domain"),
])
def test_inapplicable_rates_are_reasoned_findings(field, value, reason):
    forward, reverse, mapping = _reaction()
    setattr(reverse, field, value)
    rows = _details(compare_kinetics(forward, reverse, mapping, temperature_grid=[500]), "rate_units")
    assert len(rows) == 1
    assert rows[0]["reason"] == reason
    assert "k_forward" not in rows[0]


def test_incomplete_mapping_and_unbalanced_stoichiometry_are_visible():
    forward, reverse, mapping = _reaction()
    missing = compare_kinetics(forward, reverse, {1: mapping[1]}, temperature_grid=[500])
    assert _details(missing, "rate_units")[0]["reason"] == "incomplete_or_incompatible_thermo_mapping"
    forward.reaction_entry.structure_participants.pop()
    unbalanced = compare_kinetics(forward, reverse, mapping, temperature_grid=[500])
    assert _details(unbalanced, "rate_units")[0]["reason"] == "unbalanced_stoichiometry"


@pytest.mark.parametrize("degeneracy", [1.0, 2.0, 6.0])
def test_not_applied_degeneracy_convention_scales_forward_rate_exactly(degeneracy):
    """Review round 2, guard #1: ``record.degeneracy`` multiplying the rate
    factor in ``kinetics._rate`` had no test. Compare against an
    "already_applied" baseline (no multiplier at all) built from the same
    fixture, so the ratio pins the multiplier exactly, not just "changed".
    """
    baseline_forward, baseline_reverse, baseline_mapping = _reaction()
    baseline = _details(
        compare_kinetics(baseline_forward, baseline_reverse, baseline_mapping, temperature_grid=[500]),
        "k_forward",
    )[0]

    forward, reverse, mapping = _reaction()
    forward.degeneracy_convention = "not_applied"
    forward.degeneracy = degeneracy
    scaled = _details(compare_kinetics(forward, reverse, mapping, temperature_grid=[500]), "k_forward")[0]

    assert scaled["k_forward"] == pytest.approx(degeneracy * baseline["k_forward"], rel=1e-12)


def test_not_applied_degeneracy_with_no_value_is_missing_or_invalid():
    """Review round 2, guard #1 (second half): ``not_applied`` with no
    supplied degeneracy must be a named unavailable reason, not a silent
    fall-through (e.g. treating a missing degeneracy as a no-op factor).
    """
    forward, reverse, mapping = _reaction()
    forward.degeneracy_convention = "not_applied"
    forward.degeneracy = None
    rows = _details(compare_kinetics(forward, reverse, mapping, temperature_grid=[500]), "rate_units")
    assert len(rows) == 1
    assert rows[0]["reason"] == "missing_or_invalid_degeneracy"
    assert "k_forward" not in rows[0]


def test_unbalanced_charge_is_visible_even_when_elements_balance():
    """Review round 2, guard #3: the charge term in the stoichiometric
    balance. H -> H+ has matching element counts (one H each side) but a
    net charge of +1 -- only the charge accumulator, not the per-element
    balance, can catch this.
    """
    molecule = _thermo(1, cp_r=2, entropy_constant=0, smiles="[H]", charge=0)
    ion = _thermo(2, cp_r=2, entropy_constant=0, smiles="[H+]", charge=1)
    entry = ReactionEntry(id=1, public_ref="re_charge")
    entry.structure_participants = [
        ReactionEntryStructureParticipant(species_entry_id=molecule.species_entry_id,
                                          species_entry=molecule.species_entry, role="reactant", participant_index=1),
        ReactionEntryStructureParticipant(species_entry_id=ion.species_entry_id,
                                          species_entry=ion.species_entry, role="product", participant_index=1),
    ]
    common = {
        "reaction_entry_id": 1, "reaction_entry": entry, "model_kind": "modified_arrhenius",
        "a": 1.0, "n": 0.0, "ea_kj_mol": 0.0, "a_units": "per_s", "is_third_body": False,
        "tmin_k": 200, "tmax_k": 3000, "pressure_context": "high_p_limit",
        "degeneracy_convention": "already_applied",
    }
    forward = Kinetics(id=1, public_ref="kin_charge_f", direction="forward", **common)
    reverse = Kinetics(id=2, public_ref="kin_charge_r", direction="reverse", **common)

    mapping = {molecule.species_entry_id: molecule, ion.species_entry_id: ion}
    rows = _details(compare_kinetics(forward, reverse, mapping, temperature_grid=[500]), "rate_units")
    assert len(rows) == 1
    assert rows[0]["reason"] == "unbalanced_stoichiometry"
    assert "k_forward" not in rows[0]


def test_thermo_kinetics_domain_and_reference_pressure_mismatch_are_visible():
    forward, reverse, mapping = _reaction()
    out_of_range = compare_kinetics(forward, reverse, mapping, temperature_grid=[100])
    assert _details(out_of_range, "rate_units")[0]["reason"] == "temperature_outside_rate_domain"
    mapping[2].reference_pressure_bar = 2
    incompatible = compare_kinetics(forward, reverse, mapping, temperature_grid=[500])
    assert _details(incompatible, "rate_units")[0]["reason"] == "incompatible_reference_pressures"


def test_kinetics_currency_inputs_change_and_reordered_sets_do_not():
    forward, reverse, mapping = _reaction()
    baseline = compare_kinetics(forward, reverse, mapping, temperature_grid=[500, 700])
    forward.reaction_entry.structure_participants.reverse()
    reordered = compare_kinetics(forward, reverse, dict(reversed(list(mapping.items()))), temperature_grid=[700, 500, 500])
    assert baseline.digest == reordered.digest
    reverse.a_uncertainty = 0.2
    reverse.a_uncertainty_kind = "additive"
    changed = compare_kinetics(forward, reverse, mapping, temperature_grid=[500, 700])
    assert baseline.digest != changed.digest


def test_unbalanced_isotope_conversion_is_unavailable():
    forward, reverse, mapping = _reaction()
    mapping[2].species_entry.species.smiles = "[2H]"
    rows = _details(compare_kinetics(forward, reverse, mapping, temperature_grid=[500]), "rate_units")
    assert len(rows) == 1
    # Review round 2: name the expected reason. "reason is not None" alone does
    # not say which guard fired, and a future guard returning some other reason
    # would satisfy it silently. (element_counts_from_smiles does collapse
    # isotopes -- '[2H]' and '[H]' both count as one H -- so stoichiometry
    # balances here and only the isotope guard can fire today.)
    assert rows[0]["reason"] == "isotope_specific_equilibrium_unsupported"
    assert "k_forward" not in rows[0]


@pytest.mark.parametrize("exception", [ImportError("missing"), OSError("broken numerical library")])
def test_engine_load_failures_are_configuration_errors(monkeypatch, exception):
    original = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "cantera":
            raise exception
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    thermo = _thermo()
    thermo.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=30)]
    with pytest.raises(engine.ConfigurationError):
        compare_thermo(thermo)
