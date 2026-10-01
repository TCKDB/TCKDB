"""Recomputing an assembled composite's total, and the correlation convention (ADR 0021, P5).

Pure arithmetic over plain numbers: the backend and a producer's own pre-flight
check run the same functions. What is pinned:

* the **triples convention** is read off the stored row, both ways, and a row it
  cannot be read from makes the total *unverifiable* rather than guessed;
* the tolerance ``max(1e-6, 5e-7 * n)`` at its boundary, with ``n`` counted from
  the numbers actually consumed;
* a mismatch is refused with its code and context, and the recomputed value is
  only ever in the refusal, never returned for storage.
"""

from __future__ import annotations

import pytest

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.composite_formulas import extrapolate
from tckdb_schemas.composite_total import (
    InputEnergies,
    assert_composite_total_recomputes,
    check_composite_total,
    component_value,
)
from tckdb_schemas.enums import CompositeExtrapolationFormula as F
from tckdb_schemas.enums import CompositeInputSlot as Slot
from tckdb_schemas.enums import EnergyComponentKind as K
from tckdb_schemas.fragments.refs import CompositeSchemeDefinition

TZ = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
QZ = {"method": "CCSD(T)", "basis": "cc-pVQZ"}

SCHEME = CompositeSchemeDefinition(
    kind="extrapolation",
    terms=[
        {
            "key": "ref",
            "operation": "value",
            "energy_component": "reference",
            "inputs": [{"slot": "value", "level_of_theory": QZ}],
        },
        {
            "key": "corr",
            "operation": "extrapolation",
            "energy_component": "correlation",
            "formula": "inverse_power",
            "exponent": 3,
            "inputs": [
                {"slot": "cardinal", "cardinal_number": 3, "level_of_theory": TZ},
                {"slot": "cardinal", "cardinal_number": 4, "level_of_theory": QZ},
            ],
        },
    ],
)

# Water, ORCA 6.1 manual: SCF and CCSD(T)-labelled correlation energies at cc-pVTZ / cc-pVQZ.
R_T, C_T = -76.056728252, -0.275383016
R_Q, C_Q = -76.064381269, -0.295324345
# Independent hand arithmetic: (4^3 C_Q - 3^3 C_T) / (4^3 - 3^3) = -11.465416648 / 37.
CORR_CBS = -0.3098761256216216
TOTAL = R_Q + CORR_CBS  # -76.37425739462162


def _including(r, c):
    """ORCA's convention: correlation includes (T); reference + correlation is the energy."""
    return InputEnergies(total=r + c, components={K.reference: r, K.correlation: c})


def _separate(r, ccsd, triples):
    """Molpro's: correlation is the CCSD part, (T) separate; all three add to the energy."""
    return InputEnergies(
        total=r + ccsd + triples, components={K.reference: r, K.correlation: ccsd, K.triples: triples}
    )


def _lookup(tz: InputEnergies | None, qz: InputEnergies | None):
    def energies_for(position, slot, cardinal):
        if position == 0:
            return qz
        return tz if cardinal == 3 else qz

    return energies_for


def test_the_stated_total_follows_from_the_energies_it_names():
    check = check_composite_total(SCHEME, _lookup(_including(R_T, C_T), _including(R_Q, C_Q)), TOTAL)
    assert check.status == "ok"
    assert check.recomputed == pytest.approx(TOTAL, abs=1e-12)
    assert check.gap == pytest.approx(0.0, abs=1e-12)
    assert check.quantities == 3  # the QZ reference and the two correlation energies


# ---------------------------------------------------------------------------
# the triples convention, both ways
# ---------------------------------------------------------------------------


def test_correlation_that_includes_triples_is_read_as_stored():
    value = component_value(_including(R_Q, C_Q), K.correlation)
    assert (value.value, value.quantities) == (C_Q, 1)


def test_correlation_with_separate_triples_is_ccsd_plus_triples():
    """Molpro: the whole correlation energy a scheme term means is CCSD + (T)."""
    ccsd, triples = -0.28, -0.015324345
    value = component_value(_separate(R_Q, ccsd, triples), K.correlation)
    assert value.value == pytest.approx(ccsd + triples, abs=1e-15)
    assert value.quantities == 2


def test_the_same_scheme_recomputes_the_same_total_under_either_convention():
    """One physical calculation, written in each program's convention, gives one total."""
    ccsd_q, tri_q = -0.28, C_Q - (-0.28)
    ccsd_t, tri_t = -0.26, C_T - (-0.26)
    molpro = check_composite_total(
        SCHEME, _lookup(_separate(R_T, ccsd_t, tri_t), _separate(R_Q, ccsd_q, tri_q)), TOTAL
    )
    orca = check_composite_total(SCHEME, _lookup(_including(R_T, C_T), _including(R_Q, C_Q)), TOTAL)
    assert molpro.status == orca.status == "ok"
    assert molpro.recomputed == pytest.approx(orca.recomputed, abs=1e-12)
    # Reading the Molpro rows as if correlation included (T) would miss by |(T)| >> the tolerance.
    wrong = extrapolate(F.inverse_power, [(3, ccsd_t), (4, ccsd_q)], 3) + R_Q
    assert abs(wrong - TOTAL) > 1e-3


@pytest.mark.parametrize(
    "energies",
    [
        InputEnergies(total=R_Q + C_Q, components={K.correlation: C_Q}),  # no reference
        InputEnergies(total=None, components={K.reference: R_Q, K.correlation: C_Q}),  # no energy
        InputEnergies(total=R_Q + C_Q + 0.01, components={K.reference: R_Q, K.correlation: C_Q}),  # sums to neither
        InputEnergies(
            total=R_Q + C_Q + 0.01, components={K.reference: R_Q, K.correlation: C_Q, K.triples: -0.002}
        ),  # nor with triples
    ],
    ids=["no_reference", "no_energy", "sums_to_neither", "neither_even_with_triples"],
)
def test_a_correlation_whose_convention_cannot_be_read_is_unverifiable_not_guessed(energies):
    read = component_value(energies, K.correlation)
    assert read.value is None
    assert read.reason == "correlation_convention_undeterminable"
    # The row under test is the TZ one, so the QZ reference term reads a clean row first.
    check = check_composite_total(SCHEME, _lookup(energies, _including(R_Q, C_Q)), TOTAL)
    assert check.status == "unverifiable"
    assert check.reason == "correlation_convention_undeterminable"
    assert check.recomputed is None  # nothing is formed from a guess


def test_a_triples_part_below_the_tolerance_does_not_change_the_value():
    """|(T)| under 1e-6 makes both sums match: the stored correlation is used, either way is within tolerance."""
    tiny = 4e-7
    energies = InputEnergies(
        total=R_Q + C_Q, components={K.reference: R_Q, K.correlation: C_Q, K.triples: tiny}
    )
    assert component_value(energies, K.correlation).value == C_Q


# ---------------------------------------------------------------------------
# unverifiable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tz", "qz", "reason"),
    [
        (None, _including(R_Q, C_Q), "input_energy_not_stated"),
        (
            InputEnergies(total=R_T + C_T, components={K.reference: R_T}),
            _including(R_Q, C_Q),
            "component_not_stated",
        ),
        (_including(R_T, C_T), InputEnergies(total=None, components={}), "component_not_stated"),
    ],
    ids=["no_calculation", "no_correlation_component", "no_stored_components"],
)
def test_a_needed_energy_or_component_that_is_not_stated_makes_the_total_unverifiable(tz, qz, reason):
    check = check_composite_total(SCHEME, _lookup(tz, qz), TOTAL)
    assert check.status == "unverifiable"
    assert check.reason == reason
    assert check.term_key in {"ref", "corr"}
    assert_composite_total_recomputes(check)  # unverifiable is a warning, not a refusal


def test_no_deposited_total_leaves_nothing_to_verify():
    check = check_composite_total(SCHEME, _lookup(_including(R_T, C_T), _including(R_Q, C_Q)), None)
    assert (check.status, check.reason) == ("unverifiable", "no_total_deposited")


# ---------------------------------------------------------------------------
# the tolerance, at its boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "status"),
    [(0.0, "ok"), (1.9e-6, "ok"), (-1.9e-6, "ok"), (2.1e-6, "mismatch"), (-2.1e-6, "mismatch"), (1e-3, "mismatch")],
)
def test_the_boundary_for_four_rounded_quantities_is_two_microhartree(offset, status):
    """The total plus three consumed numbers: n = 4, so max(1e-6, 5e-7 * 4) = 2e-6 Eh."""
    check = check_composite_total(SCHEME, _lookup(_including(R_T, C_T), _including(R_Q, C_Q)), TOTAL + offset)
    assert check.quantities == 3
    assert check.tolerance == pytest.approx(2e-6)
    assert check.status == status


def test_the_tolerance_counts_triples_under_the_separate_convention_as_two_numbers():
    ccsd_q, tri_q = -0.28, C_Q - (-0.28)
    ccsd_t, tri_t = -0.26, C_T - (-0.26)
    check = check_composite_total(
        SCHEME, _lookup(_separate(R_T, ccsd_t, tri_t), _separate(R_Q, ccsd_q, tri_q)), TOTAL
    )
    assert check.quantities == 5  # the reference, and CCSD + (T) at each cardinal
    assert check.tolerance == pytest.approx(3e-6)


def test_a_mismatch_is_refused_with_its_code_and_the_recomputed_value_only_in_the_refusal():
    check = check_composite_total(SCHEME, _lookup(_including(R_T, C_T), _including(R_Q, C_Q)), TOTAL + 1e-3)
    with pytest.raises(CodedValidationError) as exc:
        assert_composite_total_recomputes(check)
    error = exc.value
    assert error.code == "composite_total_mismatch"
    assert error.context["deposited_hartree"] == pytest.approx(TOTAL + 1e-3)
    assert error.context["recomputed_hartree"] == pytest.approx(TOTAL, abs=1e-12)
    assert error.context["tolerance_hartree"] == pytest.approx(2e-6)


# ---------------------------------------------------------------------------
# every term operation and formula contributes
# ---------------------------------------------------------------------------


def test_a_difference_and_a_value_term_are_summed_with_their_signs():
    scheme = CompositeSchemeDefinition(
        kind="additive",
        terms=[
            {"key": "base", "operation": "base", "energy_component": "total",
             "inputs": [{"slot": "value", "level_of_theory": QZ}]},
            {"key": "dcv", "operation": "difference", "energy_component": "total",
             "inputs": [
                 {"slot": "high", "level_of_theory": {**TZ, "core_treatment": "all_electron"}},
                 {"slot": "low", "level_of_theory": {**TZ, "core_treatment": "frozen_core"}},
             ]},
            {"key": "dboc", "operation": "value", "energy_component": "dboc",
             "inputs": [{"slot": "value", "level_of_theory": {"method": "HF", "basis": "cc-pVDZ"}}]},
        ],
    )
    table = {
        (0, Slot.value, None): InputEnergies(total=-76.30, components={}),
        (1, Slot.high, None): InputEnergies(total=-76.20, components={}),
        (1, Slot.low, None): InputEnergies(total=-76.19, components={}),
        (2, Slot.value, None): InputEnergies(total=-76.0, components={K.dboc: 0.0027}),
    }
    check = check_composite_total(scheme, lambda p, s, c: table[(p, s, c)], -76.30 + (-76.20 - -76.19) + 0.0027)
    assert check.status == "ok"
    assert check.recomputed == pytest.approx(-76.30 - 0.01 + 0.0027, abs=1e-12)
    flipped = {**table, (1, Slot.high, None): table[(1, Slot.low, None)], (1, Slot.low, None): table[(1, Slot.high, None)]}
    other = check_composite_total(scheme, lambda p, s, c: flipped[(p, s, c)], -76.30 + (-76.20 - -76.19) + 0.0027)
    assert other.status == "mismatch"  # high minus low, not low minus high


@pytest.mark.parametrize(
    ("formula", "exponent", "cardinals"),
    [
        ("inverse_power", 3.4, (3, 4)),
        ("inverse_power_shifted_half", 4, (3, 4)),
        ("karton_martin_scf", None, (3, 4)),
        ("exponential_three_point", None, (2, 3, 4)),
    ],
)
def test_every_formula_runs_inside_a_scheme_and_recovers_the_limit_it_was_built_from(formula, exponent, cardinals):
    import math

    limit, a = -76.4, 1.3
    if formula == "inverse_power":
        shape = lambda n: n**-exponent  # noqa: E731
    elif formula == "inverse_power_shifted_half":
        shape = lambda n: (n + 0.5) ** -exponent  # noqa: E731
    elif formula == "karton_martin_scf":
        shape = lambda n: (n + 1) * math.exp(-9 * math.sqrt(n))  # noqa: E731
    else:
        shape = lambda n: math.exp(-1.1 * n)  # noqa: E731
    term = {
        "key": "x",
        "operation": "extrapolation",
        "energy_component": "reference" if formula == "karton_martin_scf" else "total",
        "formula": formula,
        "inputs": [
            {"slot": "cardinal", "cardinal_number": n, "level_of_theory": {"method": "HF", "basis": f"b{n}"}}
            for n in cardinals
        ],
    }
    if exponent is not None:
        term["exponent"] = exponent
    scheme = CompositeSchemeDefinition(kind="extrapolation", terms=[term])
    component = K(term["energy_component"])
    energies = {
        n: InputEnergies(total=limit + a * shape(n), components={K.reference: limit + a * shape(n)})
        for n in cardinals
    }
    check = check_composite_total(scheme, lambda p, s, c: energies[c], limit)
    assert component is not None
    assert check.status == "ok", check
    assert check.recomputed == pytest.approx(limit, abs=1e-8)
