"""Basis-set extrapolation arithmetic for user-built composite schemes (ADR 0021, P5).

A user-built composite scheme (``extrapolation`` or ``additive``) names the
formula that turns energies at several cardinal numbers into a basis-set limit.
TCKDB never stores the result: the formulas here run only to *check* a deposited
total against the energies it was built from (``composite_total_mismatch``).
They live in the wire package so a producer can recompute the same number before
it sends one, and so the backend and the client cannot disagree on a formula.

Each function takes plain floats. ``n`` is the **declared** cardinal number (2
for a double-zeta basis, 3 for triple-zeta, ...), never derived from a basis
name. Every form is the closed-form solution of the model for the energy limit
``E_CBS`` from the stated number of points.

The four formulas (names are the ``CompositeExtrapolationFormula`` members)
----------------------------------------------------------------------------
``inverse_power`` -- ``E_n = E_CBS + A * n**(-x)``, two points, exponent ``x``
    ``E_CBS = (X**x * E_X - Y**x * E_Y) / (X**x - Y**x)``. With ``x = 3`` this
    is the standard correlation-energy extrapolation (Halkier, Helgaker,
    Jorgensen, Klopper, Koch, Olsen, Wilson, Chem. Phys. Lett. 286, 243 (1998)).
    The exponent is the scheme's own: ORCA's optimised values are 2.46 for cc-pV(D,T)Z
    and 3.05 for cc-pV(T,Q)Z (ORCA 6.1 manual, "Automatic extrapolation to the
    basis set limit"), Molpro's default (``L3``) is 3. The functional form is
    Molpro 2026 manual, ``EXTRAPOLATE``: ``E_n = E_CBS + A (n + p)**(-x)``.

``inverse_power_shifted_half`` -- ``E_n = E_CBS + A * (n + 1/2)**(-x)``, two points, exponent ``x``
    ``E_CBS = ((X+1/2)**x * E_X - (Y+1/2)**x * E_Y) / ((X+1/2)**x - (Y+1/2)**x)``.
    The ``p = 1/2`` case of the Molpro form above; Martin's ``(L+1/2)**-4``
    extrapolation (J. M. L. Martin, Chem. Phys. Lett. 259, 669 (1996)) is
    ``x = 4``.

``karton_martin_scf`` -- ``E_n = E_CBS + A * (n + 1) * exp(-9 * sqrt(n))``, two points, no exponent
    The Hartree-Fock reference-energy extrapolation of A. Karton and
    J. M. L. Martin, Theor. Chem. Acc. 115, 330 (2006); Molpro 2026 manual,
    ``EXTRAPOLATE`` method ``KM``. With ``f(n) = (n+1) exp(-9 sqrt(n))``,
    ``E_CBS = (E_X * f(Y) - E_Y * f(X)) / (f(Y) - f(X))``.

``exponential_three_point`` -- ``E_n = E_CBS + A * exp(-C * n)``, three consecutive points, no exponent
    The three-point exponential of D. Feller, J. Chem. Phys. 96, 6104 (1992);
    Molpro 2026 manual, ``EXTRAPOLATE`` ``E_n = E_CBS + A exp(-C n)``. For
    consecutive cardinal numbers ``n, n+1, n+2`` the three equations give
    ``E_CBS = (E_n * E_{n+2} - E_{n+1}**2) / (E_n + E_{n+2} - 2 E_{n+1})``, which
    is evaluated as ``E_{n+2} - d2**2 / (d1 - d2)`` (``d1 = E_n - E_{n+1}``,
    ``d2 = E_{n+1} - E_{n+2}``) so it does not cancel at large ``|E|``.

What is checked against published numbers
-----------------------------------------
``inverse_power`` reproduces five extrapolated correlation energies printed in
the ORCA 5.0.4 and 6.1 manuals to the digits printed
(``tests/test_composite_formulas.py``). The other three
have no worked number in the sources the group's knowledge base holds, so they
are tested by recovering a known ``E_CBS`` from energies generated with the
stated model, and by a hand-computed case.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from tckdb_schemas.enums import CompositeExtrapolationFormula

__all__ = [
    "EXPONENT_FORMULAS",
    "FORMULA_POINT_COUNT",
    "ExtrapolationError",
    "extrapolate",
    "extrapolate_exponential_three_point",
    "extrapolate_inverse_power",
    "extrapolate_inverse_power_shifted_half",
    "extrapolate_karton_martin_scf",
    "extrapolation_coefficients",
    "extrapolation_weights",
    "is_linear_formula",
]

#: Formulas whose scheme term must state an exponent, and the ones that must not.
EXPONENT_FORMULAS = frozenset(
    {
        CompositeExtrapolationFormula.inverse_power,
        CompositeExtrapolationFormula.inverse_power_shifted_half,
    }
)

#: How many ``(cardinal, energy)`` points each formula consumes.
FORMULA_POINT_COUNT: dict[CompositeExtrapolationFormula, int] = {
    CompositeExtrapolationFormula.inverse_power: 2,
    CompositeExtrapolationFormula.inverse_power_shifted_half: 2,
    CompositeExtrapolationFormula.karton_martin_scf: 2,
    CompositeExtrapolationFormula.exponential_three_point: 3,
}


#: Formulas whose limit is a fixed linear combination of the input energies. The
#: three-point exponential is the one that is not: its coefficients depend on the
#: energies themselves.
NONLINEAR_FORMULAS = frozenset({CompositeExtrapolationFormula.exponential_three_point})


class ExtrapolationError(ValueError):
    """The points cannot be extrapolated (equal cardinals, a degenerate denominator)."""


def _two_point(f_x: float, e_x: float, f_y: float, e_y: float) -> float:
    """``E_CBS`` from ``E_n = E_CBS + A * g(n)`` at two points, ``f = g(n)``.

    ``E_x - E_y = A (f_x - f_y)``, so ``E_CBS = (E_x f_y - E_y f_x) / (f_y - f_x)``.
    """
    denominator = f_y - f_x
    if denominator == 0.0:
        raise ExtrapolationError("the two cardinal numbers give the same basis-set function")
    return (e_x * f_y - e_y * f_x) / denominator


def extrapolate_inverse_power(x: int, e_x: float, y: int, e_y: float, exponent: float) -> float:
    """``E_n = E_CBS + A n**(-exponent)`` through ``(x, e_x)`` and ``(y, e_y)``."""
    return _two_point(float(x) ** -exponent, e_x, float(y) ** -exponent, e_y)


def extrapolate_inverse_power_shifted_half(x: int, e_x: float, y: int, e_y: float, exponent: float) -> float:
    """``E_n = E_CBS + A (n + 1/2)**(-exponent)`` through the two points."""
    return _two_point((x + 0.5) ** -exponent, e_x, (y + 0.5) ** -exponent, e_y)


def _karton_martin(n: int) -> float:
    return (n + 1) * math.exp(-9.0 * math.sqrt(n))


def extrapolate_karton_martin_scf(x: int, e_x: float, y: int, e_y: float) -> float:
    """``E_n = E_CBS + A (n+1) exp(-9 sqrt(n))`` through the two points."""
    return _two_point(_karton_martin(x), e_x, _karton_martin(y), e_y)


def extrapolate_exponential_three_point(n: int, e_n: float, e_n1: float, e_n2: float) -> float:
    """``E_n = E_CBS + A exp(-C n)`` through cardinals ``n, n+1, n+2``.

    Written in the cancellation-stable form ``E_{n+2} - d2**2 / (d1 - d2)`` with
    ``d1 = E_n - E_{n+1}`` and ``d2 = E_{n+1} - E_{n+2}``. It is algebraically the
    closed form ``(E_n E_{n+2} - E_{n+1}**2) / (E_n + E_{n+2} - 2 E_{n+1})`` but
    it never subtracts two products of absolute energies, which at ``|E|`` in the
    thousands of hartree loses every digit of the answer.
    """
    d1 = e_n - e_n1
    d2 = e_n1 - e_n2
    denominator = d1 - d2
    if denominator == 0.0:
        raise ExtrapolationError("the three energies are collinear, so the exponential has no limit")
    return e_n2 - (d2 * d2) / denominator


def extrapolation_weights(
    formula: CompositeExtrapolationFormula | str,
    points: Sequence[tuple[int, float]],
    exponent: float | None = None,
) -> list[float]:
    """Absolute sensitivity ``|d E_CBS / d E_i|`` of the limit to each input energy.

    Returned in the order of ``points`` sorted by cardinal number (the order
    :func:`extrapolate` uses). They say how much a rounding error of one stored
    energy can move the extrapolated value, and set the tolerance of the total
    check (``composite_total``). The two-point formulas are linear, so the weights
    are constants of the cardinals and exponent; the three-point exponential is
    not, and its weights are the analytic partial derivatives
    ``(d2/D)**2``, ``2 d1 d2 / D**2`` and ``(d1/D)**2`` for ``E_n``, ``E_{n+1}``,
    ``E_{n+2}`` with ``D = d1 - d2`` (they sum to 1).

    :raises ExtrapolationError: as :func:`extrapolate`.
    """
    kind = formula if isinstance(formula, CompositeExtrapolationFormula) else CompositeExtrapolationFormula(formula)
    ordered = sorted(points, key=lambda point: point[0])
    if len(ordered) != FORMULA_POINT_COUNT[kind]:
        raise ExtrapolationError(f"{kind.value} takes {FORMULA_POINT_COUNT[kind]} points, got {len(ordered)}")
    if kind is CompositeExtrapolationFormula.exponential_three_point:
        (_, a), (_, b), (_, c) = ordered
        d1, d2 = a - b, b - c
        denominator = d1 - d2
        if denominator == 0.0:
            raise ExtrapolationError("the three energies are collinear, so the exponential has no limit")
        return [(d2 / denominator) ** 2, abs(2.0 * d1 * d2) / denominator**2, (d1 / denominator) ** 2]
    (x, _), (y, _) = ordered
    if kind is CompositeExtrapolationFormula.inverse_power:
        if exponent is None:
            raise ExtrapolationError(f"{kind.value} needs an exponent")
        f_x, f_y = float(x) ** -exponent, float(y) ** -exponent
    elif kind is CompositeExtrapolationFormula.inverse_power_shifted_half:
        if exponent is None:
            raise ExtrapolationError(f"{kind.value} needs an exponent")
        f_x, f_y = (x + 0.5) ** -exponent, (y + 0.5) ** -exponent
    else:
        f_x, f_y = _karton_martin(x), _karton_martin(y)
    denominator = f_y - f_x
    if denominator == 0.0:
        raise ExtrapolationError("the two cardinal numbers give the same basis-set function")
    return [abs(f_y / denominator), abs(f_x / denominator)]


def is_linear_formula(formula: CompositeExtrapolationFormula | str) -> bool:
    """Whether the limit is a fixed linear combination of the input energies.

    ``True`` for the three two-point formulas, ``False`` for
    ``exponential_three_point``.
    """
    kind = formula if isinstance(formula, CompositeExtrapolationFormula) else CompositeExtrapolationFormula(formula)
    return kind not in NONLINEAR_FORMULAS


def extrapolation_coefficients(
    formula: CompositeExtrapolationFormula | str,
    cardinals: Sequence[int],
    exponent: float | None = None,
) -> list[float] | None:
    """Signed weights ``c_i`` with ``E_CBS = sum_i c_i * E_i`` for a linear formula.

    Returned in the order of ``cardinals`` sorted ascending (the order
    :func:`extrapolate` uses). For the two-point formulas, with ``f`` the
    formula's basis-set function of the cardinal number
    (:func:`extrapolate_inverse_power` and its siblings), the limit is
    ``(E_X f_Y - E_Y f_X) / (f_Y - f_X)``, so the smaller cardinal ``X`` has
    coefficient ``f_Y / (f_Y - f_X)`` and the larger ``Y`` has
    ``-f_X / (f_Y - f_X)``. They sum to 1, and their absolute values are the
    sensitivities :func:`extrapolation_weights` returns.

    :param formula: The formula.
    :param cardinals: The declared cardinal numbers of the inputs (two for the
        two-point formulas).
    :param exponent: The exponent; required by ``inverse_power`` and
        ``inverse_power_shifted_half``.
    :returns: The coefficients, or ``None`` for ``exponential_three_point``,
        which is not linear in the energies (its limit is a ratio of them), so no
        fixed coefficients exist and none are invented.
    :raises ExtrapolationError: when the cardinal count does not fit the formula, the
        cardinals repeat, an exponent is missing, or the arithmetic is degenerate.
    """
    kind = formula if isinstance(formula, CompositeExtrapolationFormula) else CompositeExtrapolationFormula(formula)
    ordered = sorted(cardinals)
    if len(ordered) != FORMULA_POINT_COUNT[kind]:
        raise ExtrapolationError(f"{kind.value} takes {FORMULA_POINT_COUNT[kind]} points, got {len(ordered)}")
    if len(set(ordered)) != len(ordered):
        raise ExtrapolationError("the cardinal numbers must be distinct")
    if kind in NONLINEAR_FORMULAS:
        return None
    x, y = ordered
    if kind is CompositeExtrapolationFormula.inverse_power:
        if exponent is None:
            raise ExtrapolationError(f"{kind.value} needs an exponent")
        f_x, f_y = float(x) ** -exponent, float(y) ** -exponent
    elif kind is CompositeExtrapolationFormula.inverse_power_shifted_half:
        if exponent is None:
            raise ExtrapolationError(f"{kind.value} needs an exponent")
        f_x, f_y = (x + 0.5) ** -exponent, (y + 0.5) ** -exponent
    else:
        f_x, f_y = _karton_martin(x), _karton_martin(y)
    denominator = f_y - f_x
    if denominator == 0.0:
        raise ExtrapolationError("the two cardinal numbers give the same basis-set function")
    return [f_y / denominator, -f_x / denominator]


def extrapolate(
    formula: CompositeExtrapolationFormula | str,
    points: Sequence[tuple[int, float]],
    exponent: float | None = None,
) -> float:
    """Extrapolate ``(cardinal, energy)`` points with one of the four formulas.

    :param formula: The formula.
    :param points: ``(cardinal_number, energy)`` pairs, in any order. Two for the
        two-point formulas; three consecutive cardinals for the exponential.
    :param exponent: The exponent ``x``; required by ``inverse_power`` and
        ``inverse_power_shifted_half``, ignored by the others.
    :returns: The extrapolated energy. Nothing is stored by the caller.
    :raises ExtrapolationError: when the point count or cardinals do not fit the
        formula, or the arithmetic is degenerate.
    """
    kind = formula if isinstance(formula, CompositeExtrapolationFormula) else CompositeExtrapolationFormula(formula)
    ordered = sorted(points, key=lambda point: point[0])
    expected = FORMULA_POINT_COUNT[kind]
    if len(ordered) != expected:
        raise ExtrapolationError(f"{kind.value} takes {expected} points, got {len(ordered)}")
    cardinals = [cardinal for cardinal, _ in ordered]
    if len(set(cardinals)) != len(cardinals):
        raise ExtrapolationError("the cardinal numbers must be distinct")
    if kind in EXPONENT_FORMULAS:
        if exponent is None:
            raise ExtrapolationError(f"{kind.value} needs an exponent")
        (x, e_x), (y, e_y) = ordered
        if kind is CompositeExtrapolationFormula.inverse_power:
            return extrapolate_inverse_power(x, e_x, y, e_y, exponent)
        return extrapolate_inverse_power_shifted_half(x, e_x, y, e_y, exponent)
    if kind is CompositeExtrapolationFormula.karton_martin_scf:
        (x, e_x), (y, e_y) = ordered
        return extrapolate_karton_martin_scf(x, e_x, y, e_y)
    (n, e_n), (n1, e_n1), (n2, e_n2) = ordered
    if n1 != n + 1 or n2 != n + 2:
        raise ExtrapolationError("exponential_three_point needs three consecutive cardinal numbers")
    return extrapolate_exponential_three_point(n, e_n, e_n1, e_n2)
