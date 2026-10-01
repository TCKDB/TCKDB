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
    ``E_CBS = (E_n * E_{n+2} - E_{n+1}**2) / (E_n + E_{n+2} - 2 E_{n+1})``.

What is checked against published numbers
-----------------------------------------
``inverse_power`` reproduces five extrapolated correlation energies printed in
the ORCA 5.0.4 and 6.1 manuals to the digits printed
(``backend`` and wire tests: ``test_composite_formulas.py``). The other three
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
    """``E_n = E_CBS + A exp(-C n)`` through cardinals ``n, n+1, n+2``."""
    denominator = e_n + e_n2 - 2.0 * e_n1
    if denominator == 0.0:
        raise ExtrapolationError("the three energies are collinear, so the exponential has no limit")
    return (e_n * e_n2 - e_n1 * e_n1) / denominator


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
