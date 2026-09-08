"""Pure evaluation of pressure-dependent k(T,P) from stored PDep fits.

Two parameterizations are supported, matching what
``app/db/models/network_pdep.py`` (``NetworkKineticsChebyshev`` /
``NetworkKineticsPlog``) actually stores:

* **Chebyshev** — a double Chebyshev-polynomial expansion of ``log10 k``
  (or, when ``stores_log10_k is False``, of ``k`` directly) in two
  *reduced* variables, one for temperature and one for pressure. Reduced
  variables map the fit's own ``[Tmin, Tmax]`` / ``[Pmin, Pmax]`` onto
  ``[-1, 1]``. This is the convention used throughout the pressure-
  dependent-kinetics literature and by every mainstream combustion-kinetics
  tool that reads Chebyshev PDep blocks — Chemkin, Cantera, and RMG/Arkane
  all implement the identical mapping:

      T̃ = (2/T − 1/Tmin − 1/Tmax) / (1/Tmax − 1/Tmin)
      P̃ = (2 log10(P) − log10(Pmin) − log10(Pmax)) / (log10(Pmax) − log10(Pmin))
      log10 k(T, P) = Σ_i Σ_j α_ij · φ_i(T̃) · φ_j(P̃)

  where ``φ_n`` is the Chebyshev polynomial of the first kind and ``α``
  is indexed ``[temperature_order][pressure_order]`` — the same axis
  order the read surface projects
  (``NetworkKineticsChebyshevCoefficient.temperature_order`` /
  ``.pressure_order``). See R.J. Kee et al., "Chemkin Theory Manual" §
  Chebyshev Reaction Rate Expressions, and the Cantera documentation
  page of the same name (cantera.org/science/reactions.html), both of
  which state this exact mapping; the underlying method is due to
  P.K. Venkatesh et al., *AIChE J.* 43(5), 1331-1340 (1997), "Bimolecular
  reaction rates from double Chebyshev polynomial fits". A reduced
  variable *outside* ``[-1, 1]`` (a request outside the fit's stated
  range) is not a domain error for the recurrence used below — it is
  exactly what "extrapolating a polynomial fit" means, so it is computed
  and flagged, never refused outright or silently clamped.

* **PLOG** ("pressure-logarithmic" / p-dependent Arrhenius) — a table of
  ordinary modified-Arrhenius expressions, each keyed to one fitted
  pressure. ``k(T)`` at an arbitrary pressure is obtained by evaluating
  the two bracketing pressure-keyed Arrhenius expressions at the
  requested temperature and interpolating **linearly in log10(k) against
  log10(P)** between them — never a linear interpolation in ``k`` itself,
  and never against natural-log ``P``. This is the Chemkin/Cantera PLOG
  convention (Cantera: "PlogRate" / "pressure-dependent-Arrhenius";
  original description: A.W. Jasper & J.A. Miller-style multi-pressure
  Arrhenius tables, formalized in Chemkin-Pro's ``PLOG`` keyword). Two or
  more entries sharing the same fitted pressure (the schema's
  ``entry_index`` discriminator) are *summed* at that pressure before
  interpolating — the Chemkin ``DUPLICATE`` convention for PLOG. Outside
  the table's own pressure range, the convention is flat extrapolation:
  the nearest single bracketing Arrhenius expression is used unchanged
  (never extrapolated in log-log slope), and the point is flagged
  ``in_range=False``. Validity on the **pressure** axis is judged
  against the table's own fitted pressures (``min(entries.pressure_bar)``
  .. ``max(...)``), not the parent row's declared ``pmin_bar``/
  ``pmax_bar`` — deliberately: the fitted pressures are exactly where
  the interpolation has real anchors, and are always present (a PLOG
  row cannot exist with zero entries), whereas ``pmin_bar``/``pmax_bar``
  are optional metadata that need not exactly bracket the table. Judging
  by the entries is the scientifically tighter, more honest claim: it is
  never wider than the metadata could claim, only ever equal or
  narrower. Validity on the **temperature** axis, by contrast, has no
  per-entry analogue to fall back on — a PLOG entry is a modified-
  Arrhenius expression valid over *some* T range that the table itself
  does not encode point-by-point — so it is judged against the parent
  row's declared ``tmin_k``/``tmax_k`` (the same bounds Chebyshev uses).
  When those are not recorded, the temperature axis cannot be judged and
  is treated as unbounded (never the reason a point is flagged
  out-of-range) rather than refusing evaluation outright.

No database or HTTP dependency: every function here takes plain floats /
sequences and returns a plain result. The caller (a service module) owns
resolving a ``NetworkKinetics`` row, reading its JSONB/child-table shape,
and deciding what "no data" means for that row.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

#: CODATA 2018 molar gas constant, J/(mol*K). Matches the value already
#: used for Arrhenius Ea/R conversion elsewhere in the backend
#: (``app/services/scientific_read/chemkin_serialize.py``'s
#: ``_KJ_PER_MOL_TO_KELVIN``) so the two Arrhenius evaluators in this
#: codebase cannot silently disagree on R.
GAS_CONSTANT_J_MOL_K = 8.314462618


class KineticsEvaluationError(ValueError):
    """A fit cannot be evaluated as stored (no coefficients/entries, a
    non-finite bound, etc).

    Distinguished from a generic ``ValueError`` so a service layer can
    catch precisely this and translate it into a coded 4xx/5xx without
    also swallowing a genuine programming mistake (e.g. a bad call
    signature) under the same except clause.
    """


@dataclass(frozen=True)
class PlogEntry:
    """One ``network_kinetics_plog`` row, as plain floats.

    :param pressure_bar: Fitted pressure, bar.
    :param a: Arrhenius pre-exponential factor, in whatever units the
        parent ``NetworkKinetics.rate_units`` names.
    :param n: Temperature exponent.
    :param ea_kj_mol: Activation energy, kJ/mol.
    :param a_units: This entry's own recorded units, if the ingester
        populated the per-row ``a_units`` rather than leaving it to the
        parent ``NetworkKinetics.rate_units``. Optional and otherwise
        unused by evaluation — its only job here is letting
        :func:`evaluate_plog` refuse to silently sum two Chemkin
        ``DUPLICATE`` entries that disagree about what unit their sum
        would even be in.
    """

    pressure_bar: float
    a: float
    n: float
    ea_kj_mol: float
    a_units: str | None = None


@dataclass(frozen=True)
class EvaluatedKPoint:
    """One evaluated (T, P) -> k point.

    :param k: Rate coefficient in the units the caller supplied the fit
        under (Chebyshev's ``rate_units`` / PLOG's ``a_units``) — this
        module never converts units, only evaluates the stored numbers.
    :param in_range: ``True`` iff both T and P fall within the fit's own
        validity bounds (inclusive). ``False`` marks the point as an
        extrapolation: still computed, never silently presented as
        interpolated. What counts as "the fit's own bounds" differs by
        model kind — for Chebyshev it is ``[tmin_k, tmax_k]`` x
        ``[pmin_bar, pmax_bar]`` exactly; for PLOG the pressure axis
        uses the table's own fitted pressures rather than any bound
        passed in separately, while the temperature axis uses
        ``tmin_k``/``tmax_k`` the same way Chebyshev does. See
        :func:`evaluate_plog` for why. Either way, both axes are always
        judged — a point cannot be "in range" merely because one axis
        happens to be.
    """

    temperature_k: float
    pressure_bar: float
    k: float
    in_range: bool


def modified_arrhenius_k(
    temperature_k: float, *, a: float, n: float, ea_kj_mol: float
) -> float:
    """Evaluate the modified-Arrhenius expression ``k = A * T^n * exp(-Ea/RT)``.

    :param temperature_k: Temperature, K. Must be > 0.
    :param a: Pre-exponential factor.
    :param n: Temperature exponent.
    :param ea_kj_mol: Activation energy, kJ/mol.
    """
    if not (temperature_k > 0) or math.isnan(temperature_k) or math.isinf(temperature_k):
        raise KineticsEvaluationError(
            f"temperature_k must be a finite positive number, got {temperature_k!r}"
        )
    ea_over_r_kelvin = ea_kj_mol * 1000.0 / GAS_CONSTANT_J_MOL_K
    return a * temperature_k**n * math.exp(-ea_over_r_kelvin / temperature_k)


# ---------------------------------------------------------------------------
# Chebyshev
# ---------------------------------------------------------------------------


def chebyshev_reduced_temperature(
    temperature_k: float, *, tmin_k: float, tmax_k: float
) -> float:
    """Map ``temperature_k`` onto the fit's reduced temperature variable.

    ``T̃ = (2/T - 1/Tmin - 1/Tmax) / (1/Tmax - 1/Tmin)``. Note the mapping
    is built on **inverse** temperature (1/T), not T itself — a Chebyshev
    PDep fit is linear in reciprocal temperature by convention (matching
    the Arrhenius ``exp(-Ea/RT)`` form), and mapping T directly onto
    ``[-1, 1]`` instead is the classic way to get this formula backwards:
    it still returns a plausible-looking number, just the wrong one.
    """
    if not (temperature_k > 0) or not math.isfinite(temperature_k):
        raise KineticsEvaluationError(
            f"temperature_k must be a finite positive number, got "
            f"{temperature_k!r}"
        )
    if not (tmin_k > 0 and tmax_k > 0 and tmax_k > tmin_k):
        raise KineticsEvaluationError(
            f"invalid Chebyshev temperature bounds: tmin_k={tmin_k!r}, "
            f"tmax_k={tmax_k!r}"
        )
    return (2.0 / temperature_k - 1.0 / tmin_k - 1.0 / tmax_k) / (
        1.0 / tmax_k - 1.0 / tmin_k
    )


def chebyshev_reduced_pressure(
    pressure_bar: float, *, pmin_bar: float, pmax_bar: float
) -> float:
    """Map ``pressure_bar`` onto the fit's reduced pressure variable.

    ``P̃ = (2 log10(P) - log10(Pmin) - log10(Pmax)) / (log10(Pmax) - log10(Pmin))``.
    Built on ``log10(P)``, not P itself — pressure dependence in PDep
    theory is logarithmic (falloff scales with ``log(P)``, not P
    linearly), so a Chebyshev PDep fit's pressure axis is always a
    log-pressure axis.
    """
    if not (pressure_bar > 0) or not math.isfinite(pressure_bar):
        raise KineticsEvaluationError(
            f"pressure_bar must be a finite positive number, got "
            f"{pressure_bar!r}"
        )
    if not (pmin_bar > 0 and pmax_bar > 0 and pmax_bar > pmin_bar):
        raise KineticsEvaluationError(
            f"invalid Chebyshev pressure bounds: pmin_bar={pmin_bar!r}, "
            f"pmax_bar={pmax_bar!r}"
        )
    log_p, log_pmin, log_pmax = (
        math.log10(pressure_bar),
        math.log10(pmin_bar),
        math.log10(pmax_bar),
    )
    return (2.0 * log_p - log_pmin - log_pmax) / (log_pmax - log_pmin)


def _chebyshev_polynomial_values(order_count: int, x: float) -> list[float]:
    """``[T_0(x), T_1(x), ..., T_{order_count-1}(x)]`` via the standard
    three-term recurrence ``T_n(x) = 2x T_{n-1}(x) - T_{n-2}(x)``.

    Deliberately *not* ``cos(n * arccos(x))`` — that closed form is only
    real-valued for ``|x| <= 1`` and would raise/NaN for exactly the
    out-of-range (extrapolated) points this module must still be able to
    evaluate. The recurrence is the polynomial itself; it stays real and
    well-defined for any real ``x``, which is what makes it able to
    represent "extrapolated, and flagged as such" instead of "refused".
    """
    if order_count <= 0:
        return []
    values = [1.0]
    if order_count == 1:
        return values
    values.append(x)
    for _ in range(2, order_count):
        values.append(2.0 * x * values[-1] - values[-2])
    return values


def evaluate_chebyshev(
    temperature_k: float,
    pressure_bar: float,
    *,
    coefficients: Sequence[Sequence[float]],
    tmin_k: float,
    tmax_k: float,
    pmin_bar: float,
    pmax_bar: float,
    stores_log10_k: bool | None = True,
) -> EvaluatedKPoint:
    """Evaluate a Chebyshev PDep fit at one (T, P) point.

    :param coefficients: ``coefficients[i][j]`` is ``alpha_ij``, the
        coefficient on ``T_i(T̃) * T_j(P̃)`` — indexed
        ``[temperature_order][pressure_order]``, matching the read
        surface's ``NetworkKineticsChebyshevCoefficient`` projection and
        the stored ``network_kinetics_chebyshev.coefficients`` JSONB
        matrix's row/column order.
    :param stores_log10_k: ``True`` (the default, matching every PDep
        Chebyshev convention this codebase has seen — Chemkin, Cantera,
        RMG/Arkane all fit ``log10 k``) means the double sum is
        ``log10 k`` and the returned ``k`` is ``10**sum``. ``None`` is
        treated the same as ``True``: a fit that never recorded which
        convention it used is read under the near-universal one, the
        same "assumed program default, stated as such" policy this
        codebase already applies to an unrecorded Hessian method. Only
        an explicit ``False`` switches to "the sum *is* k directly".
    :raises KineticsEvaluationError: empty/ragged coefficient matrix, or
        non-finite/invalid T or P bounds.
    """
    if not coefficients or not coefficients[0]:
        raise KineticsEvaluationError(
            "Chebyshev coefficient matrix is empty; this channel's fit "
            "carries no coefficients to evaluate (a channel/fit with no "
            "kinetics must not be evaluated as if it had a k of 0)."
        )
    n_temperature = len(coefficients)
    n_pressure = len(coefficients[0])
    for row in coefficients:
        if len(row) != n_pressure:
            raise KineticsEvaluationError(
                "Chebyshev coefficient matrix is ragged: every row must "
                f"have {n_pressure} entries."
            )

    t_tilde = chebyshev_reduced_temperature(
        temperature_k, tmin_k=tmin_k, tmax_k=tmax_k
    )
    p_tilde = chebyshev_reduced_pressure(
        pressure_bar, pmin_bar=pmin_bar, pmax_bar=pmax_bar
    )
    phi_t = _chebyshev_polynomial_values(n_temperature, t_tilde)
    phi_p = _chebyshev_polynomial_values(n_pressure, p_tilde)

    total = 0.0
    for i in range(n_temperature):
        row = coefficients[i]
        phi_ti = phi_t[i]
        for j in range(n_pressure):
            total += row[j] * phi_ti * phi_p[j]

    k = total if stores_log10_k is False else 10.0**total
    in_range = (tmin_k <= temperature_k <= tmax_k) and (
        pmin_bar <= pressure_bar <= pmax_bar
    )
    return EvaluatedKPoint(
        temperature_k=temperature_k, pressure_bar=pressure_bar, k=k, in_range=in_range
    )


# ---------------------------------------------------------------------------
# PLOG
# ---------------------------------------------------------------------------


def evaluate_plog(
    temperature_k: float,
    pressure_bar: float,
    *,
    entries: Sequence[PlogEntry],
    tmin_k: float | None = None,
    tmax_k: float | None = None,
) -> EvaluatedKPoint:
    """Evaluate a PLOG fit at one (T, P) point.

    Bracketing: the two nearest fitted pressures straddling
    ``pressure_bar`` are located (entries are grouped by
    ``pressure_bar`` first, summing every entry sharing a pressure —
    the Chemkin ``DUPLICATE`` PLOG convention). ``k(T)`` is evaluated at
    each bracketing pressure via :func:`modified_arrhenius_k`, and the
    two results are interpolated **linearly in log10(k) against
    log10(P)**:

        log10 k(T, P) = log10 k(T, Plo)
            + (log10 P - log10 Plo) / (log10 Phi - log10 Plo)
              * (log10 k(T, Phi) - log10 k(T, Plo))

    Using ``log10`` (not natural log) in both numerator and denominator
    is what keeps the interpolation *fraction* correct — the fraction is
    a ratio of two logs in the same base, so the base itself cancels and
    either base gives the identical fraction. What must not happen is
    mixing bases *within* one call (e.g. natural-log spacing against a
    log10 P-axis, or vice versa) or, worse, interpolating ``k`` linearly
    instead of ``log10 k`` — PLOG tables are fit assuming log-linear
    pressure falloff, so a linear-in-k interpolation is a different,
    wrong curve between the fitted points even though it agrees with the
    fit exactly *at* them.

    ``in_range`` is the AND of two independently-judged axes:

    * **Pressure** — against ``[min(entries.pressure_bar),
      max(entries.pressure_bar)]``, i.e. the table's own fitted
      pressures, not any bound passed in separately. Outside that
      range: flat extrapolation — the nearest single bracketing
      Arrhenius expression is evaluated as-is (never log-log
      extrapolated past it).
    * **Temperature** — against ``[tmin_k, tmax_k]`` when both are
      supplied (typically the parent ``NetworkKinetics`` row's own
      declared bounds — the same ones Chebyshev uses). A PLOG entry is
      a modified-Arrhenius expression with no per-row temperature bound
      of its own, unlike pressure, so there is nothing else to judge it
      against; when one or both bounds are unavailable the temperature
      axis is treated as unbounded (never the reason a point is flagged
      out of range) rather than refusing evaluation. Getting this axis
      wrong is a real, live-reachable defect this signature closes: a
      pressure-only ``in_range`` would call a PLOG evaluation at 5000 K
      against a table only ever fit to 2000 K "in range" merely because
      the pressure happened to fall inside the table, understating how
      far outside the fit's actual validity the point sits — while the
      Chebyshev fit of the very same physical channel correctly flags
      the same point as extrapolated.

    :raises KineticsEvaluationError: no entries (a channel/fit with no
        kinetics must not be evaluated as if it had a k of 0); two
        entries sharing one fitted pressure disagree about their
        recorded ``a_units`` (summing them would not even be summing
        the same unit); or a bracket's summed rate coefficient is
        non-positive or non-finite (a Chemkin ``DUPLICATE`` pair with a
        negative pre-exponential factor, used to fit curvature, can
        legitimately drive the sum to <= 0 in some region of T — that
        region cannot be log-interpolated or served as a rate
        coefficient, so it is refused with a coded error rather than
        crashing on an unguarded ``math.log10``).
    """
    if not entries:
        raise KineticsEvaluationError(
            "PLOG entry list is empty; this channel's fit carries no "
            "pressure-keyed Arrhenius entries to evaluate."
        )
    if not math.isfinite(temperature_k) or not (temperature_k > 0):
        raise KineticsEvaluationError(
            f"temperature_k must be a finite positive number, got "
            f"{temperature_k!r}"
        )
    if not math.isfinite(pressure_bar) or not (pressure_bar > 0):
        raise KineticsEvaluationError(
            f"pressure_bar must be a finite positive number, got "
            f"{pressure_bar!r}"
        )

    by_pressure: dict[float, list[PlogEntry]] = defaultdict(list)
    for entry in entries:
        if not (entry.pressure_bar > 0):
            raise KineticsEvaluationError(
                f"PLOG entry pressure_bar must be > 0, got {entry.pressure_bar!r}"
            )
        by_pressure[entry.pressure_bar].append(entry)
    fitted_pressures = sorted(by_pressure)

    def k_at_fitted_pressure(p: float) -> float:
        # Chemkin DUPLICATE convention: entries sharing one fitted
        # pressure contribute additively, not by overwrite/average --
        # but only once they are confirmed to be additions of the same
        # unit. A per-entry a_units that disagrees with its siblings at
        # the same pressure means "sum" is not even a well-defined
        # operation on these numbers.
        group = by_pressure[p]
        units = {e.a_units for e in group if e.a_units is not None}
        if len(units) > 1:
            raise KineticsEvaluationError(
                f"PLOG entries at pressure_bar={p!r} disagree on a_units "
                f"({sorted(units)!r}); refusing to sum them as if they "
                "shared one unit."
            )
        return sum(
            modified_arrhenius_k(temperature_k, a=e.a, n=e.n, ea_kj_mol=e.ea_kj_mol)
            for e in group
        )

    def positive_finite_k(k: float, *, where: str) -> float:
        if not math.isfinite(k) or not (k > 0):
            raise KineticsEvaluationError(
                f"PLOG evaluation produced a non-positive or non-finite "
                f"rate coefficient ({k!r}) {where}; a summed Chemkin "
                "DUPLICATE pair with a negative pre-exponential factor "
                "can legitimately drive the sum negative in some region "
                "of T -- refusing rather than serving a value that "
                "cannot be log-interpolated or interpreted as a rate "
                "coefficient."
            )
        return k

    p_min, p_max = fitted_pressures[0], fitted_pressures[-1]
    in_range_pressure = p_min <= pressure_bar <= p_max
    in_range_temperature = True
    if tmin_k is not None and tmax_k is not None:
        in_range_temperature = tmin_k <= temperature_k <= tmax_k
    in_range = in_range_pressure and in_range_temperature

    if pressure_bar <= p_min:
        k = positive_finite_k(
            k_at_fitted_pressure(p_min), where=f"at pressure_bar={p_min!r}"
        )
    elif pressure_bar >= p_max:
        k = positive_finite_k(
            k_at_fitted_pressure(p_max), where=f"at pressure_bar={p_max!r}"
        )
    else:
        lower = max(p for p in fitted_pressures if p <= pressure_bar)
        upper = min(p for p in fitted_pressures if p >= pressure_bar)
        if lower == upper:
            k = positive_finite_k(
                k_at_fitted_pressure(lower), where=f"at pressure_bar={lower!r}"
            )
        else:
            k_lower = positive_finite_k(
                k_at_fitted_pressure(lower), where=f"at the lower bracket ({lower!r} bar)"
            )
            k_upper = positive_finite_k(
                k_at_fitted_pressure(upper), where=f"at the upper bracket ({upper!r} bar)"
            )
            log_p = math.log10(pressure_bar)
            log_lower, log_upper = math.log10(lower), math.log10(upper)
            fraction = (log_p - log_lower) / (log_upper - log_lower)
            log_k = math.log10(k_lower) + fraction * (
                math.log10(k_upper) - math.log10(k_lower)
            )
            k = 10.0**log_k

    return EvaluatedKPoint(
        temperature_k=temperature_k, pressure_bar=pressure_bar, k=k, in_range=in_range
    )


__all__ = [
    "GAS_CONSTANT_J_MOL_K",
    "EvaluatedKPoint",
    "KineticsEvaluationError",
    "PlogEntry",
    "chebyshev_reduced_pressure",
    "chebyshev_reduced_temperature",
    "evaluate_chebyshev",
    "evaluate_plog",
    "modified_arrhenius_k",
]
