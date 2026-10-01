"""The four extrapolation formulas against published numbers and against their own models (ADR 0021, P5).

Two kinds of evidence, and the module says which each test is:

* **Published.** ``inverse_power`` reproduces five extrapolated correlation
  energies printed in the ORCA manuals (5.0.4 and 6.1, "Automatic extrapolation
  to the basis set limit"), each from the two printed input energies and the
  printed exponent, to the nine decimals the manual prints. The other three
  formulas have no worked number in the sources the group's knowledge base
  holds.
* **Self-consistency.** For every formula, energies generated from the model the
  formula inverts (``E_n = E_CBS + A f(n)``) with a chosen ``E_CBS`` must give
  that ``E_CBS`` back. A formula with the wrong form (a swapped weight, a missing
  shift) fails this.

Each test would fail under a one-line change to the formula it names; the PR
lists the mutations.
"""

from __future__ import annotations

import math

import pytest

from tckdb_schemas.composite_formulas import (
    ExtrapolationError,
    extrapolate,
    extrapolate_exponential_three_point,
    extrapolate_inverse_power,
    extrapolate_inverse_power_shifted_half,
    extrapolate_karton_martin_scf,
)
from tckdb_schemas.enums import CompositeExtrapolationFormula as F

# (X, E_X, Y, E_Y, exponent, printed extrapolated value, where it is printed)
ORCA_PUBLISHED = [
    # ORCA 6.1 manual: Extrapolate(2/3) and Extrapolate(3/4), cc-pVnZ, water, MDCI correlation energy.
    (2, -0.214591061, 3, -0.275383015, 2.46, -0.310905962, "ORCA 6.1, Beta(2/3)=2.460"),
    (3, -0.275383016, 4, -0.295324345, 3.05, -0.309520368, "ORCA 6.1, Beta(3/4)=3.050"),
    # ORCA 5.0.4 manual: ExtrapolateEP2 / ExtrapolateEP3 on the same molecule.
    (2, -0.214429497, 3, -0.275299699, 2.46, -0.310868368, "ORCA 5.0.4, EP2(2/3,cc,DLPNO-CCSD(T))"),
    (3, -0.275299699, 4, -0.295229871, 3.05, -0.309417951, "ORCA 5.0.4, EP3(CC)"),
    (2, -0.219202871, 3, -0.267058634, 2.43, -0.295568604, "ORCA 5.0.4, EP2(2/3,ANO,MP2), Beta=2.430"),
]


@pytest.mark.parametrize(("x", "e_x", "y", "e_y", "exponent", "printed", "source"), ORCA_PUBLISHED)
def test_inverse_power_reproduces_the_correlation_energies_the_orca_manuals_print(
    x, e_x, y, e_y, exponent, printed, source
):
    got = extrapolate_inverse_power(x, e_x, y, e_y, exponent)
    assert got == pytest.approx(printed, abs=2e-9), source
    # The dispatcher gives the same number, in either order of the points.
    assert extrapolate(F.inverse_power, [(y, e_y), (x, e_x)], exponent) == pytest.approx(printed, abs=2e-9)


def test_the_published_numbers_would_not_survive_the_wrong_exponent():
    """The reproduction above is not vacuous: exponent 3 instead of 3.05 misses by 3.5e-4 Eh."""
    wrong = extrapolate_inverse_power(3, -0.275383016, 4, -0.295324345, 3.0)
    assert abs(wrong - (-0.309520368)) > 1e-4


E_CBS = -76.3755
A = 1.7


@pytest.mark.parametrize("exponent", [3.0, 3.4, 2.46])
@pytest.mark.parametrize(("x", "y"), [(2, 3), (3, 4), (4, 5)])
def test_inverse_power_recovers_the_limit_of_its_own_model(x, y, exponent):
    e_x = E_CBS + A * x**-exponent
    e_y = E_CBS + A * y**-exponent
    assert extrapolate_inverse_power(x, e_x, y, e_y, exponent) == pytest.approx(E_CBS, abs=1e-12)


@pytest.mark.parametrize("exponent", [4.0, 3.0])
@pytest.mark.parametrize(("x", "y"), [(2, 3), (3, 4), (4, 5)])
def test_shifted_half_recovers_the_limit_of_its_own_model_and_differs_from_the_plain_form(x, y, exponent):
    e_x = E_CBS + A * (x + 0.5) ** -exponent
    e_y = E_CBS + A * (y + 0.5) ** -exponent
    assert extrapolate_inverse_power_shifted_half(x, e_x, y, e_y, exponent) == pytest.approx(E_CBS, abs=1e-12)
    # Fed to the unshifted formula the same energies do not give the limit: the shift is real.
    assert abs(extrapolate_inverse_power(x, e_x, y, e_y, exponent) - E_CBS) > 1e-6


def test_shifted_half_hand_computed_case():
    """(L+1/2)^-4 at L=3,4 with E_3=-0.30, E_4=-0.32: weights 3.5^4=150.0625, 4.5^4=410.0625.

    E = (150.0625 * -0.30 - 410.0625 * -0.32) / (150.0625 - 410.0625) = 86.20125 / -260 = -0.3315433.
    """
    got = extrapolate_inverse_power_shifted_half(3, -0.30, 4, -0.32, 4.0)
    expected = (150.0625 * -0.30 - 410.0625 * -0.32) / (150.0625 - 410.0625)
    assert got == pytest.approx(expected, abs=1e-15)
    assert got == pytest.approx(-0.33154326923, abs=1e-10)


def _km(n: int) -> float:
    return (n + 1) * math.exp(-9.0 * math.sqrt(n))


@pytest.mark.parametrize(("x", "y"), [(2, 3), (3, 4), (4, 5)])
def test_karton_martin_recovers_the_limit_of_its_own_model(x, y):
    e_x = E_CBS + A * _km(x)
    e_y = E_CBS + A * _km(y)
    assert extrapolate_karton_martin_scf(x, e_x, y, e_y) == pytest.approx(E_CBS, abs=1e-12)


def test_karton_martin_hand_computed_case():
    """f(3) = 4 exp(-9 sqrt 3) = 6.2e-7, f(4) = 5 exp(-18) = 7.6e-8: the limit sits just past E_4.

    With E_3 = -76.05 and E_4 = -76.06 the model gives E_CBS = E_4 - (E_3 - E_4) * f(4) / (f(3) - f(4)).
    """
    f3, f4 = 4 * math.exp(-9 * math.sqrt(3)), 5 * math.exp(-18)
    expected = -76.06 - (-76.05 - -76.06) * f4 / (f3 - f4)
    assert extrapolate_karton_martin_scf(3, -76.05, 4, -76.06) == pytest.approx(expected, abs=1e-12)
    assert expected < -76.06  # below the larger basis: the limit is lower, as for an HF series


@pytest.mark.parametrize("start", [2, 3, 4])
@pytest.mark.parametrize("c", [0.8, 1.6])
def test_exponential_three_point_recovers_the_limit_of_its_own_model(start, c):
    e = [E_CBS + A * math.exp(-c * (start + i)) for i in range(3)]
    # The closed form divides by a second difference that shrinks as the series
    # converges, so it loses digits at high cardinal numbers: 1e-8 Eh is its floor here.
    assert extrapolate_exponential_three_point(start, *e) == pytest.approx(E_CBS, abs=1e-8)
    assert extrapolate(F.exponential_three_point, [(start + 2, e[2]), (start, e[0]), (start + 1, e[1])]) == (
        pytest.approx(E_CBS, abs=1e-8)
    )


def test_exponential_three_point_hand_computed_case():
    """E = (E_n E_{n+2} - E_{n+1}^2) / (E_n + E_{n+2} - 2 E_{n+1}) with -1.0, -1.5, -1.75 gives -2.0."""
    assert extrapolate_exponential_three_point(2, -1.0, -1.5, -1.75) == pytest.approx(-2.0, abs=1e-15)


@pytest.mark.parametrize(
    ("formula", "points", "exponent"),
    [
        (F.inverse_power, [(3, -0.3)], 3.0),  # one point
        (F.inverse_power, [(3, -0.3), (3, -0.31)], 3.0),  # equal cardinals
        (F.inverse_power, [(3, -0.3), (4, -0.31)], None),  # no exponent
        (F.exponential_three_point, [(2, -1.0), (3, -1.5), (5, -1.9)], None),  # not consecutive
        (F.exponential_three_point, [(2, -1.0), (3, -1.5), (4, -2.0)], None),  # collinear: no limit
    ],
)
def test_points_that_do_not_fit_the_formula_are_refused_not_guessed(formula, points, exponent):
    with pytest.raises(ExtrapolationError):
        extrapolate(formula, points, exponent)
