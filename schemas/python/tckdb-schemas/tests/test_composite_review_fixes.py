"""Review fixes on P5: weights, the CCSD-only correlation, stable three-point, rules (ADR 0021).

Each test names the defect it keeps out. The textbook scheme (extrapolate the CCSD
correlation energy, add (T) at a smaller basis as its own term) is the case that had
no spelling before ``correlation_excluding_triples``: written with ``correlation`` it
counted (T) twice and missed by ~15 mEh.
"""

from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.composite_formulas import (
    ExtrapolationError,
    extrapolate,
    extrapolate_exponential_three_point,
    extrapolation_weights,
)
from tckdb_schemas.composite_total import InputEnergies, check_composite_total, component_value
from tckdb_schemas.enums import CompositeExtrapolationFormula as F
from tckdb_schemas.enums import EnergyComponentKind as K
from tckdb_schemas.fragments.calculation import (
    CompositeResultPayload,
    assert_assembled_not_primary,
)
from tckdb_schemas.fragments.refs import CompositeSchemeDefinition, LevelOfTheoryRef
from tckdb_schemas.sp_energy_components import check_sp_energy_components
from tckdb_schemas.workflows.computed_species_upload import ConformerInBundle

TZ = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
QZ = {"method": "CCSD(T)", "basis": "cc-pVQZ"}


def _coded(exc: pytest.ExceptionInfo) -> CodedValidationError:
    if isinstance(exc.value, ValidationError):
        error = exc.value.errors()[0]["ctx"]["error"]
        assert isinstance(error, CodedValidationError), error
        return error
    assert isinstance(exc.value, CodedValidationError)
    return exc.value


# ---------------------------------------------------------------------------
# #8 the three-point exponential does not cancel at large |E|
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [-5300.0, -76.0, 0.0])
def test_three_point_exponential_keeps_its_digits_at_5300_hartree(offset):
    """The product form ``E_n E_{n+2} - E_{n+1}^2`` loses its digits at |E| = 5300; the stable form does not.

    A converged series (cardinals 5, 6, 7) has second differences of ~3e-5 Eh, which the product form divides
    products of 2.8e7 Eh^2 by.
    """
    limit, a, c = offset - 0.4, 0.9, 2.0
    e = [limit + a * math.exp(-c * n) for n in (5, 6, 7)]
    assert extrapolate_exponential_three_point(5, *e) == pytest.approx(limit, abs=5e-9)
    if offset == -5300.0:
        naive = (e[0] * e[2] - e[1] ** 2) / (e[0] + e[2] - 2 * e[1])
        assert abs(naive - limit) > 1e-6  # the defect this form had


def test_three_point_collinear_points_are_still_refused():
    with pytest.raises(ExtrapolationError):
        extrapolate(F.exponential_three_point, [(2, -1.0), (3, -1.5), (4, -2.0)])


# ---------------------------------------------------------------------------
# #1 weights are the partial derivatives
# ---------------------------------------------------------------------------


def _finite_difference_weights(formula, points, exponent):
    base = extrapolate(formula, points, exponent)
    weights = []
    for i in range(len(points)):
        step = 1e-7

        def bumped(sign: float, i: int = i) -> float:
            moved = [(m, v + (sign * step if j == i else 0.0)) for j, (m, v) in enumerate(sorted(points))]
            return extrapolate(formula, moved, exponent)

        weights.append(abs((bumped(1.0) - bumped(-1.0)) / (2 * step)))
    assert base is not None
    return weights


@pytest.mark.parametrize(
    ("formula", "points", "exponent"),
    [
        (F.inverse_power, [(3, -0.275), (4, -0.295)], 3.0),
        (F.inverse_power, [(2, -0.21), (3, -0.27)], 2.46),
        (F.inverse_power_shifted_half, [(3, -0.275), (4, -0.295)], 4.0),
        (F.karton_martin_scf, [(3, -76.05), (4, -76.06)], None),
        (F.exponential_three_point, [(3, -76.30), (4, -76.36), (5, -76.38)], None),
    ],
)
def test_the_weights_are_the_partial_derivatives_of_the_extrapolation(formula, points, exponent):
    got = extrapolation_weights(formula, points, exponent)
    assert got == pytest.approx(_finite_difference_weights(formula, points, exponent), rel=1e-4)


def test_x_cubed_weights_and_the_exponential_weights_sum_as_stated():
    assert extrapolation_weights(F.inverse_power, [(3, -1.0), (4, -1.1)], 3) == pytest.approx([27 / 37, 64 / 37])
    # d_i/D squares and the cross term of a three-point exponential sum to one when signed; unsigned, they bound it.
    weights = extrapolation_weights(F.exponential_three_point, [(3, -76.30), (4, -76.36), (5, -76.38)])
    assert sum(weights) >= 1.0


# ---------------------------------------------------------------------------
# #2 the CCSD-only correlation, and the textbook scheme
# ---------------------------------------------------------------------------

R_T, R_Q = -76.0567, -76.0644
CCSD_T, CCSD_Q = -0.2600, -0.2800
T_T, T_Q = -0.0153, -0.0161  # (T) at each cardinal


def _orca(r, ccsd, t):
    """ORCA writes correlation including (T); (T) is stored beside it."""
    return InputEnergies(
        total=r + ccsd + t, components={K.reference: r, K.correlation: ccsd + t, K.triples: t}
    )


def _molpro(r, ccsd, t):
    """Molpro writes the CCSD part as correlation and (T) separately."""
    return InputEnergies(total=r + ccsd + t, components={K.reference: r, K.correlation: ccsd, K.triples: t})


TEXTBOOK = CompositeSchemeDefinition(
    kind="extrapolation",
    terms=[
        {"key": "scf", "operation": "value", "energy_component": "reference",
         "inputs": [{"slot": "value", "level_of_theory": QZ}]},
        {"key": "ccsd", "operation": "extrapolation", "energy_component": "correlation_excluding_triples",
         "formula": "inverse_power", "exponent": 3,
         "inputs": [
             {"slot": "cardinal", "cardinal_number": 3, "level_of_theory": TZ},
             {"slot": "cardinal", "cardinal_number": 4, "level_of_theory": QZ},
         ]},
        {"key": "t", "operation": "value", "energy_component": "triples",
         "inputs": [{"slot": "value", "level_of_theory": TZ}]},
    ],
)
#: By hand: reference at QZ, CCSD limit (64 CCSD_Q - 27 CCSD_T) / 37, and (T) once, at TZ.
TEXTBOOK_TOTAL = R_Q + (64 * CCSD_Q - 27 * CCSD_T) / 37 + T_T


def _lookup(make, scheme_levels=("tz", "qz")):
    tz, qz = make(R_T, CCSD_T, T_T), make(R_Q, CCSD_Q, T_Q)

    def energies_for(position, slot, cardinal):
        if position == 0:
            return qz
        if position == 1:
            return tz if cardinal == 3 else qz
        return tz

    return energies_for


@pytest.mark.parametrize("make", [_orca, _molpro], ids=["orca_includes_T", "molpro_separate_T"])
def test_the_textbook_scheme_counts_triples_once_whichever_convention_the_inputs_use(make):
    check = check_composite_total(TEXTBOOK, _lookup(make), TEXTBOOK_TOTAL)
    assert check.status == "ok", check
    assert check.recomputed == pytest.approx(TEXTBOOK_TOTAL, abs=1e-12)


def test_written_with_plain_correlation_the_same_scheme_double_counts_triples():
    wrong = copy.deepcopy(TEXTBOOK.model_dump(mode="json"))
    wrong["terms"][1]["energy_component"] = "correlation"
    check = check_composite_total(CompositeSchemeDefinition(**wrong), _lookup(_orca), TEXTBOOK_TOTAL)
    assert check.status == "mismatch"
    assert abs(check.gap) > 1e-3  # the (T) counted twice: ~15 mEh


def test_excluding_triples_is_read_from_the_row_by_its_convention():
    orca = component_value(_orca(R_Q, CCSD_Q, T_Q), K.correlation_excluding_triples)
    molpro = component_value(_molpro(R_Q, CCSD_Q, T_Q), K.correlation_excluding_triples)
    assert orca.value == pytest.approx(CCSD_Q, abs=1e-12) and orca.quantities == 2  # correlation - triples
    assert molpro.value == CCSD_Q and molpro.quantities == 1  # correlation as stored


@pytest.mark.parametrize(
    "energies",
    [
        InputEnergies(total=R_Q + CCSD_Q + T_Q, components={K.correlation: CCSD_Q + T_Q}),  # no reference
        InputEnergies(total=R_Q + CCSD_Q + T_Q + 0.01, components={K.reference: R_Q, K.correlation: CCSD_Q, K.triples: T_Q}),
        InputEnergies(total=R_Q + CCSD_Q + T_Q, components={K.reference: R_Q, K.correlation: CCSD_Q + T_Q}),
    ],
    ids=["no_reference", "sums_to_neither", "includes_but_no_triples_stored"],
)
def test_excluding_triples_that_cannot_be_derived_is_unverifiable(energies):
    read = component_value(energies, K.correlation_excluding_triples)
    assert read.value is None
    assert read.reason in {"correlation_convention_undeterminable", "component_not_stated"}


def test_a_derived_component_is_never_a_stored_single_point_component():
    with pytest.raises(CodedValidationError) as exc:
        check_sp_energy_components(
            [("correlation_excluding_triples", -0.26)], calculation_type="sp", electronic_energy_hartree=-76.3
        )
    assert exc.value.code == "sp_energy_component_derived"


# ---------------------------------------------------------------------------
# #5 identity: a cardinal on a non-cardinal slot changes nothing but the hash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("term", "slot_index"), [("scf", 0), ("t", 0)])
def test_a_cardinal_on_a_non_cardinal_slot_is_refused(term, slot_index):
    scheme = copy.deepcopy(TEXTBOOK.model_dump(mode="json", exclude_none=True))
    next(t for t in scheme["terms"] if t["key"] == term)["inputs"][slot_index]["cardinal_number"] = 4
    with pytest.raises(ValidationError) as exc:
        CompositeSchemeDefinition(**scheme)
    error = _coded(exc)
    assert error.code == "composite_scheme_malformed"
    assert error.context["rule"] == "cardinal_on_non_cardinal_slot"


def test_a_cardinal_on_a_difference_slot_is_refused_too():
    scheme = {
        "kind": "additive",
        "terms": [
            {"key": "d", "operation": "difference", "energy_component": "total", "inputs": [
                {"slot": "high", "cardinal_number": 3, "level_of_theory": TZ},
                {"slot": "low", "level_of_theory": QZ}]},
        ],
    }
    with pytest.raises(ValidationError) as exc:
        CompositeSchemeDefinition(**scheme)
    assert _coded(exc).context["rule"] == "cardinal_on_non_cardinal_slot"


# ---------------------------------------------------------------------------
# #6 the total is deposited
# ---------------------------------------------------------------------------


def test_an_assembled_composite_without_its_total_is_refused_by_name():
    inputs = [{"term_key": "x", "slot": "value", "calculation_key": "a"}]
    with pytest.raises(ValidationError) as exc:
        CompositeResultPayload(assembly="assembled", inputs=inputs)
    assert _coded(exc).code == "composite_total_required"


# ---------------------------------------------------------------------------
# #4 an assembled composite is never a primary
# ---------------------------------------------------------------------------

_WATER_XYZ = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"


def _assembled_block():
    return {
        "assembly": "assembled",
        "electronic_energy_hartree": TEXTBOOK_TOTAL,
        "inputs": [
            {"term_key": "scf", "slot": "value", "calculation_key": "a"},
            {"term_key": "ccsd", "slot": "cardinal", "cardinal_number": 3, "calculation_key": "a"},
            {"term_key": "ccsd", "slot": "cardinal", "cardinal_number": 4, "calculation_key": "a"},
            {"term_key": "t", "slot": "value", "calculation_key": "a"},
        ],
    }


def test_the_primary_rule_refuses_an_assembled_block_and_accepts_a_program_run():
    block = CompositeResultPayload(**_assembled_block())
    with pytest.raises(CodedValidationError) as exc:
        assert_assembled_not_primary(block, subject="calculation")
    assert exc.value.code == "composite_assembled_cannot_be_primary"
    assert_assembled_not_primary(CompositeResultPayload(assembly="program_run", electronic_energy_hartree=-1.0), subject="x")
    assert_assembled_not_primary(None, subject="x")


def test_a_conformer_in_a_bundle_cannot_have_an_assembled_primary():
    calc = {
        "key": "p",
        "type": "composite",
        "level_of_theory": {"composite_scheme": TEXTBOOK.model_dump(mode="json", exclude_none=True)},
        "composite_result": _assembled_block(),
    }
    with pytest.raises(ValidationError) as exc:
        ConformerInBundle(key="c0", geometry={"xyz_text": _WATER_XYZ}, primary_calculation=calc)
    assert _coded(exc).code == "composite_assembled_cannot_be_primary"


def test_an_ordinary_level_with_no_scheme_is_untouched():
    assert LevelOfTheoryRef(method="b3lyp", basis="def2-tzvp").composite_scheme is None
