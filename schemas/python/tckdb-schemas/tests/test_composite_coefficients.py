"""Signed linear coefficients of the extrapolation formulas (ADR 0021, P7a).

For the three formulas that are linear in the input energies,
``E_CBS = sum_i c_i * E_i``, and a read of a scheme reports the ``c_i`` per input. The
exponential three-point formula is a ratio of differences of the energies, so it has
no fixed coefficients: ``None`` is the answer, never a made-up weight.

Each test names what a one-line change to :func:`extrapolation_coefficients` would break.
"""

from __future__ import annotations

import pytest

from tckdb_schemas.composite_formulas import (
    ExtrapolationError,
    extrapolate,
    extrapolation_coefficients,
    extrapolation_weights,
    is_linear_formula,
)
from tckdb_schemas.enums import CompositeExtrapolationFormula as F

LINEAR_CASES = [
    (F.inverse_power, 3.0, (3, 4)),
    (F.inverse_power, 2.46, (2, 3)),
    (F.inverse_power_shifted_half, 4.0, (3, 4)),
    (F.karton_martin_scf, None, (2, 3)),
    (F.karton_martin_scf, None, (4, 5)),
]


@pytest.mark.parametrize(("formula", "exponent", "cardinals"), LINEAR_CASES)
def test_the_coefficients_reproduce_the_extrapolation_for_any_energies(formula, exponent, cardinals):
    """sum c_i E_i equals the formula's own limit for arbitrary energies: they are the formula, not a look-alike."""
    coefficients = extrapolation_coefficients(formula, cardinals, exponent)
    assert coefficients is not None and len(coefficients) == 2
    for energies in ([-0.30, -0.32], [-1.25, -0.5], [10.0, -3.0]):
        points = list(zip(cardinals, energies, strict=True))
        linear = sum(c * e for c, e in zip(coefficients, energies, strict=True))
        assert linear == pytest.approx(extrapolate(formula, points, exponent), abs=1e-12)


@pytest.mark.parametrize(("formula", "exponent", "cardinals"), LINEAR_CASES)
def test_the_coefficients_sum_to_one_and_are_the_signed_weights(formula, exponent, cardinals):
    coefficients = extrapolation_coefficients(formula, cardinals, exponent)
    assert coefficients is not None
    # A constant shift of every energy shifts the limit by the same amount.
    assert sum(coefficients) == pytest.approx(1.0, abs=1e-12)
    # The smaller cardinal is down-weighted (negative), the larger up-weighted: the larger basis dominates.
    assert coefficients[0] < 0 < coefficients[1]
    # ``extrapolation_weights`` is their absolute value, so the tolerance and the read agree.
    points = [(c, -0.1 * c) for c in cardinals]
    assert [abs(c) for c in coefficients] == pytest.approx(extrapolation_weights(formula, points, exponent))


def test_the_x_minus_three_coefficients_by_hand():
    """f(n) = n^-3: c_3 = f4/(f4 - f3) = -27/37, c_4 = -f3/(f4 - f3) = 64/37."""
    got = extrapolation_coefficients(F.inverse_power, [4, 3], 3.0)  # unsorted on purpose
    assert got == pytest.approx([-27 / 37, 64 / 37], abs=1e-15)


def test_the_coefficients_follow_the_cardinals_not_the_order_given():
    assert extrapolation_coefficients(F.inverse_power, [4, 3], 3.0) == extrapolation_coefficients(
        F.inverse_power, [3, 4], 3.0
    )


def test_the_exponent_and_the_shift_change_the_coefficients():
    base = extrapolation_coefficients(F.inverse_power, [3, 4], 3.0)
    assert extrapolation_coefficients(F.inverse_power, [3, 4], 3.4) != base
    assert extrapolation_coefficients(F.inverse_power_shifted_half, [3, 4], 3.0) != base


def test_the_exponential_three_point_has_no_coefficients_and_says_so():
    """Not linear in the energies: a ratio of differences. ``None``, never a made-up weight."""
    assert is_linear_formula(F.exponential_three_point) is False
    assert extrapolation_coefficients(F.exponential_three_point, [2, 3, 4]) is None
    assert all(is_linear_formula(f) for f in (F.inverse_power, F.inverse_power_shifted_half, F.karton_martin_scf))


def test_the_exponential_really_is_not_linear():
    """The reason ``None`` is right: equal steps in one energy do not give equal steps in the limit."""
    a = extrapolate(F.exponential_three_point, [(2, -1.0), (3, -1.5), (4, -1.75)])
    b = extrapolate(F.exponential_three_point, [(2, -1.0), (3, -1.5), (4, -1.80)])
    c = extrapolate(F.exponential_three_point, [(2, -1.0), (3, -1.5), (4, -1.85)])
    assert (b - a) != pytest.approx(c - b, abs=1e-6)


@pytest.mark.parametrize(
    ("formula", "cardinals", "exponent"),
    [
        (F.inverse_power, [3], 3.0),  # one point
        (F.inverse_power, [3, 3], 3.0),  # equal cardinals
        (F.inverse_power, [3, 4], None),  # no exponent
        (F.inverse_power_shifted_half, [3, 4], None),
        (F.exponential_three_point, [2, 3], None),  # the wrong count is refused before the non-linear answer
        (F.karton_martin_scf, [3, 4, 5], None),
    ],
)
def test_coefficients_that_cannot_be_given_are_refused_not_guessed(formula, cardinals, exponent):
    with pytest.raises(ExtrapolationError):
        extrapolation_coefficients(formula, cardinals, exponent)
