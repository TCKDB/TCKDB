"""Independent analytical fixtures for Phase D; no production evaluator oracle."""

import builtins
import json
from math import exp, log

import pytest

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


def _thermo(key=1, *, cp_r=3.5, entropy_constant=2.0, pressure=1.0, smiles="[H][H]"):
    species = Species(id=key, public_ref=f"sp_test_{key}", smiles=smiles, charge=0)
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


@pytest.mark.parametrize("field,value,reason", [
    ("phase", None, "missing_or_incompatible_gas_phase"),
    ("phase", "liquid", "missing_or_incompatible_gas_phase"),
    ("reference_pressure_bar", None, "missing_or_invalid_reference_pressure"),
])
def test_thermo_missing_state_remains_visible(field, value, reason):
    thermo = _thermo()
    setattr(thermo, field, value)
    thermo.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=30, s_j_mol_k=100)]
    rows = _details(compare_thermo(thermo), "residual")
    assert all(r["reason"] == reason and r["residual"] is None for r in rows)


def test_unsupported_wilhoit_and_missing_uncertainty_remain_visible():
    thermo = _thermo()
    thermo.nasa = None
    thermo.wilhoit = ThermoWilhoit()
    thermo.points = [ThermoPoint(temperature_k=500, cp_j_mol_k=30)]
    result = compare_thermo(thermo)
    assert all(r["reason"] == "unsupported_representation" for r in _details(result, "residual"))
    metadata = _details(result, "input")
    assert metadata[0]["s298_uncertainty_j_mol_k"] is None
    assert metadata[0]["fit_and_point_uncertainty"] is None


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
    assert rows[0]["reason"] is not None
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
