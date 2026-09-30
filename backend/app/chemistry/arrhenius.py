"""The scalar Arrhenius expression with a reference temperature.

A ``kinetics`` row stores ``a``, ``n``, ``ea_kj_mol`` and ``t0_k``, meaning::

    k(T) = A * (T / T0)**n * exp(-Ea / (R * T))

``t0_k`` is 1 K for every row that does not say otherwise, which is the plain
``A * T**n`` form. Everything outside the database that has no T0 of its own
(CHEMKIN's ``A n Ea`` line, Cantera's ``ArrheniusRate``) wants that plain form,
so a consumer converts once with :func:`a_at_unit_t0` rather than each of them
re-deriving the algebra.
"""

from __future__ import annotations

import math

#: J mol^-1 K^-1, the CODATA 2018 value (exact since the 2019 SI redefinition).
GAS_CONSTANT_J_MOL_K = 8.314462618


class ArrheniusRangeError(ValueError):
    """A schema-valid T0 and n whose rescaled A is not a finite number.

    The schemas bound T0, but ``T0**n`` still overflows for a large enough |n|;
    callers turn this into a gap or a coded reason rather than a 500.
    """


def a_at_unit_t0(a: float, n: float | None, t0_k: float | None) -> float:
    """Return the A of the equivalent ``A' * T**n`` expression (T0 = 1 K).

    ``A * (T/T0)**n == (A / T0**n) * T**n`` for every T, so this changes how the
    rate is written and not what it is.

    :param a: Pre-exponential factor at reference temperature ``t0_k``.
    :param n: Temperature exponent; ``None`` is read as 0, as everywhere else.
    :param t0_k: Reference temperature, K. Must be > 0. ``None`` is read as
        1 K: a stored row always has a value (the column is NOT NULL), so
        ``None`` only arises on a transient row that has not been flushed.
    """
    if t0_k is None:
        t0_k = 1.0
    if not (t0_k > 0) or math.isinf(t0_k):
        raise ValueError(f"t0_k must be a finite positive number, got {t0_k!r}")
    try:
        rescaled = a / t0_k ** (n if n is not None else 0.0)
    except (ZeroDivisionError, OverflowError) as exc:
        raise ArrheniusRangeError(
            f"A / T0**n is out of numeric range for T0={t0_k!r}, n={n!r}"
        ) from exc
    if not math.isfinite(rescaled):
        raise ArrheniusRangeError(f"A / T0**n is not finite for T0={t0_k!r}, n={n!r}")
    return rescaled


def arrhenius_k(
    temperature_k: float,
    *,
    a: float,
    n: float | None,
    ea_kj_mol: float | None,
    t0_k: float = 1.0,
) -> float:
    """Evaluate ``A * (T/T0)**n * exp(-Ea/RT)`` for one scalar Arrhenius row.

    :param temperature_k: Temperature, K. Must be > 0.
    :param a: Pre-exponential factor at ``t0_k``, in the row's own units.
    :param n: Temperature exponent; ``None`` is read as 0.
    :param ea_kj_mol: Activation energy, kJ/mol; ``None`` is read as 0.
    :param t0_k: Reference temperature, K (1 K is the plain ``A * T**n`` form).
    """
    if not (temperature_k > 0) or math.isinf(temperature_k):
        raise ValueError(f"temperature_k must be a finite positive number, got {temperature_k!r}")
    exponent = n if n is not None else 0.0
    ea_over_rt = (ea_kj_mol or 0.0) * 1000.0 / (GAS_CONSTANT_J_MOL_K * temperature_k)
    return a * (temperature_k / t0_k) ** exponent * math.exp(-ea_over_rt)
