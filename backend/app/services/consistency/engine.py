"""The pinned Cantera boundary. No local NASA or rate polynomial evaluator."""
from math import isfinite

from app.db.models.common import PhaseKind

ENGINE_VERSION = "3.2.0"

#: Phases these advisory NASA/rate consistency checks support today (decided
#: 2026-09-23). A record whose phase is outside this set is declared out of
#: scope -- never refused as though the phase were incompatible, and never
#: silently treated as gas. Widening support to another phase is a one-line
#: addition here; :func:`gas_state_reason` reads this constant and nowhere
#: else compares ``thermo.phase`` against a hardcoded value.
SUPPORTED_PHASES = frozenset({PhaseKind.gas})

#: Quantities whose applicability does not depend on a recorded reference
#: pressure (decided 2026-09-23 for Cp, 2026-09-27 for H). An ideal-gas heat
#: capacity or enthalpy carries no standard-state pressure; only entropy,
#: and anything built from it, does. :func:`gas_state_reason` reads this
#: constant and nowhere else special-cases a quantity name, so a later
#: check that needs another pressure-free quantity adds its token here.
PRESSURE_INDEPENDENT_QUANTITIES = frozenset({"cp", "h"})


class ConfigurationError(RuntimeError):
    """Missing or incompatible numerical engine; never a scientific finding."""


def cantera():
    try:
        import cantera as ct
    except Exception as exc:
        raise ConfigurationError(
            f"Cantera {ENGINE_VERSION} is required for advisory NASA/rate evaluation. "
            "Install the 'chemkin' extra: pip install 'tckdb-backend[chemkin]' "
            f"(or `mamba install -n tckdb_env -c conda-forge cantera={ENGINE_VERSION}`)."
        ) from exc
    if ct.__version__ != ENGINE_VERSION:
        raise ConfigurationError(f"Cantera {ENGINE_VERSION} is required; found {ct.__version__}")
    return ct


#: Neutral reference pressure (bar) supplied to Cantera's NASA-polynomial
#: constructors only when the quantity is in
#: :data:`PRESSURE_INDEPENDENT_QUANTITIES` and the record has none. Cp and H
#: are pressure-independent for these polynomials -- Cantera returns the same
#: ``cp`` (verified 2026-09-23) and the same ``h`` (verified 2026-09-27, for
#: NASA7 and for a NASA9 region) at 1e5, 101325 and 2.5e5 Pa -- so the exact
#: value is inert; it exists only to satisfy the constructor's signature.
_NEUTRAL_REFERENCE_PRESSURE_BAR = 1.0

#: Cantera's NASA thermo returns Cp and S in J/kmol/K and H in J/kmol
#: (measured 2026-09-27 against the hand formula H/R = sum a_k T^k/k + a6).
#: The advisory checks report Cp and S in J/mol/K and H in kJ/mol.
_ENGINE_DIVISOR = {"cp": 1000.0, "s": 1000.0, "h": 1e6}

#: Stored point column per quantity; ``h_kj_mol`` is already kJ/mol.
_POINT_COLUMN = {"cp": "cp_j_mol_k", "s": "s_j_mol_k", "h": "h_kj_mol"}

#: The only temperature at which a stored 298.15 K scalar is a value.
_T298_K = 298.15

_NASA7_BRANCHES = ("low", "high")


def gas_state_reason(thermo, *, quantity=None):
    """Applicability gate, split per quantity (decided 2026-09-23).

    ``reference_pressure_bar`` fixes the standard-state pressure baked into
    the entropy coefficient, so a missing/invalid value makes any entropy
    comparison unavailable. Heat capacity and enthalpy do not depend on the
    reference pressure at all, so a quantity in
    :data:`PRESSURE_INDEPENDENT_QUANTITIES` skips that check -- only the
    phase requirement applies. Every other caller (entropy, and the full
    thermo used by the D3 kinetics/equilibrium comparison) keeps requiring a
    valid reference pressure via the default ``quantity=None``.

    Phase scope is declared, not implied (decided 2026-09-23): a record
    whose phase was never recorded (``phase is None``, documented on
    ``Thermo.phase`` as unspecified, never as non-gas) is out of scope for a
    different reason than a record explicitly recorded as a phase this check
    does not support -- these checks support :data:`SUPPORTED_PHASES` only.
    Collapsing the two into one reason would make an unspecified record look
    like a positive claim of an incompatible phase, and would make a
    genuinely incompatible record look like it was merely never recorded.
    Neither is compared under a gas assumption.
    """
    if thermo.phase is None:
        return "phase_not_recorded"
    if thermo.phase not in SUPPORTED_PHASES:
        return "non_gas_phase_unsupported"
    if quantity in PRESSURE_INDEPENDENT_QUANTITIES:
        return None
    p = thermo.reference_pressure_bar
    if p is None or not isfinite(p) or p <= 0:
        return "missing_or_invalid_reference_pressure"
    return None


def fit_names(thermo):
    names = []
    if thermo.nasa is not None:
        names.append("nasa7")
    if thermo.nasa9_intervals:
        names.append("nasa9")
    if thermo.wilhoit is not None:
        names.append("wilhoit")
    return names


def _record_gate(thermo, temperature, quantity):
    """Return (reference pressure in Pa, reason) shared by every fit evaluator."""
    reason = gas_state_reason(thermo, quantity=quantity)
    if reason:
        return None, reason
    if ((thermo.tmin_k is not None and temperature < thermo.tmin_k)
            or (thermo.tmax_k is not None and temperature > thermo.tmax_k)):
        return None, "temperature_outside_record_range"
    reference_pressure_bar = thermo.reference_pressure_bar
    if reference_pressure_bar is None:
        reference_pressure_bar = _NEUTRAL_REFERENCE_PRESSURE_BAR
    return reference_pressure_bar * 100000.0, None


def _nasa7_parts(thermo):
    """Return ((t_low, t_mid, t_high), low a1..a7, high b1..b7, reason)."""
    nasa = thermo.nasa
    values = [getattr(nasa, f"{prefix}{i}") for prefix in ("a", "b") for i in range(1, 8)]
    bounds = (nasa.t_low, nasa.t_mid, nasa.t_high)
    if any(v is None or not isfinite(v) for v in (*bounds, *values)):
        return None, None, None, "incomplete_nasa7"
    if not 0 < bounds[0] < bounds[1] < bounds[2]:
        return None, None, None, "invalid_nasa7_intervals"
    return bounds, values[:7], values[7:], None


def _nasa9_checked(thermo):
    """Return (intervals sorted by index, reason); never bridges a gap."""
    intervals = sorted(thermo.nasa9_intervals, key=lambda iv: iv.interval_index)
    previous = None
    for iv in intervals:
        values = [getattr(iv, f"a{i}") for i in range(1, 10)]
        if any(v is None or not isfinite(v) for v in (iv.t_min_k, iv.t_max_k, *values)):
            return None, "incomplete_nasa9"
        if not 0 < iv.t_min_k < iv.t_max_k or (previous is not None and iv.t_min_k < previous):
            return None, "invalid_nasa9_intervals"
        previous = iv.t_max_k
    return intervals, None


def _nasa9_region(ct, iv, pressure):
    coeffs = [1, iv.t_min_k, iv.t_max_k, *[getattr(iv, f"a{i}") for i in range(1, 10)]]
    return ct.Nasa9PolyMultiTempRegion(iv.t_min_k, iv.t_max_k, pressure, coeffs)


def polynomial(thermo, representation, temperature, *, quantity=None):
    """Return (Cantera species thermo, reason), without extrapolating across gaps.

    At a NASA9 shared boundary the upper interval owns the temperature, as
    in Cantera's multi-region model. NASA7's midpoint belongs to the low range.
    Each NASA9 region is passed separately to avoid implicitly bridging gaps.

    ``quantity`` is forwarded to :func:`gas_state_reason`: with a quantity
    in :data:`PRESSURE_INDEPENDENT_QUANTITIES` a missing
    ``reference_pressure_bar`` is not an applicability failure, and
    :data:`_NEUTRAL_REFERENCE_PRESSURE_BAR` stands in purely to satisfy the
    polynomial constructor.
    """
    ct = cantera()
    pressure, reason = _record_gate(thermo, temperature, quantity)
    if reason:
        return None, reason
    if representation == "nasa7":
        bounds, low, high, reason = _nasa7_parts(thermo)
        if reason:
            return None, reason
        if not bounds[0] <= temperature <= bounds[2]:
            return None, "temperature_outside_fit_range"
        return ct.NasaPoly2(bounds[0], bounds[2], pressure, [bounds[1], *high, *low]), None
    if representation == "nasa9":
        intervals, reason = _nasa9_checked(thermo)
        if reason:
            return None, reason
        selected = None
        for iv in intervals:
            if iv.t_min_k <= temperature <= iv.t_max_k:
                selected = iv
        if selected is None:
            return None, "temperature_outside_fit_or_in_gap"
        return _nasa9_region(ct, selected, pressure), None
    return None, "unsupported_representation"


def nasa7_branch(thermo, branch, temperature, *, quantity=None):
    """Return (Cantera thermo carrying ONE NASA7 branch, reason).

    ``branch`` is ``"low"`` (coefficients a1..a7, closed interval
    [t_low, t_mid]) or ``"high"`` (b1..b7, closed [t_mid, t_high]). The
    polynomial is built as ``NasaPoly2`` with the same seven coefficients
    on both sides of ``t_mid`` (``[t_mid, c, c]``), so Cantera evaluates
    that one branch everywhere and does all the arithmetic.

    This exists because the combined fit returned by :func:`polynomial`
    answers "what does the record say at T" -- and exactly at ``t_mid``
    Cantera answers with the LOW branch (measured 2026-09-27 with Cantera
    3.2.0: the combined ``h(t_mid)`` equals the low branch's to the last
    bit, and ``h(t_mid + 1e-9)`` already equals the high branch's). The
    high branch's own value at ``t_mid`` is therefore unreachable through
    the combined fit; a boundary-jump comparison needs both.
    """
    if branch not in _NASA7_BRANCHES:
        raise ValueError(f"NASA7 branch must be one of {_NASA7_BRANCHES}, not {branch!r}")
    ct = cantera()
    pressure, reason = _record_gate(thermo, temperature, quantity)
    if reason:
        return None, reason
    bounds, low, high, reason = _nasa7_parts(thermo)
    if reason:
        return None, reason
    t_low, t_mid, t_high = bounds
    lower, upper, coefficients = (t_low, t_mid, low) if branch == "low" else (t_mid, t_high, high)
    if not lower <= temperature <= upper:
        return None, "temperature_outside_nasa7_branch"
    return ct.NasaPoly2(t_low, t_high, pressure, [t_mid, *coefficients, *coefficients]), None


def nasa9_interval(thermo, index, temperature, *, quantity=None):
    """Return (Cantera thermo for the ONE NASA9 region ``interval_index == index``, reason).

    The region is valid on its own closed [t_min_k, t_max_k]. Unlike
    :func:`polynomial`, where the upper interval owns a shared boundary, the
    caller names the interval, so the lower interval can be evaluated at
    its own upper end. The whole interval set is validated exactly as
    :func:`polynomial` validates it; a gap is never bridged.
    """
    ct = cantera()
    pressure, reason = _record_gate(thermo, temperature, quantity)
    if reason:
        return None, reason
    intervals, reason = _nasa9_checked(thermo)
    if reason:
        return None, reason
    selected = next((iv for iv in intervals if iv.interval_index == index), None)
    if selected is None:
        return None, "nasa9_interval_not_found"
    if not selected.t_min_k <= temperature <= selected.t_max_k:
        return None, "temperature_outside_nasa9_interval"
    return _nasa9_region(ct, selected, pressure), None


def evaluate(thermo, representation, temperature, quantity):
    """Return (value, reason) for one representation at one exact temperature.

    Units: Cp and S in J/mol/K; H in kJ/mol. ``"point"`` needs a stored
    point at exactly ``temperature``; ``"s298"`` and ``"h298"`` are values
    only at exactly 298.15 K and only for their own quantity -- never
    interpolated, never shifted by a fit.
    """
    if representation == "point":
        point = next((p for p in thermo.points if p.temperature_k == temperature), None)
        value = getattr(point, _POINT_COLUMN.get(quantity, f"{quantity}_j_mol_k"), None)
        return value, None if value is not None else "no_exact_matching_point"
    if representation == "s298":
        value = thermo.s298_j_mol_k if temperature == _T298_K and quantity == "s" else None
        return value, None if value is not None else "no_exact_s298_value"
    if representation == "h298":
        value = thermo.h298_kj_mol if temperature == _T298_K and quantity == "h" else None
        return value, None if value is not None else "no_exact_h298_value"
    poly, reason = polynomial(thermo, representation, temperature, quantity=quantity)
    if reason:
        return None, reason
    value = getattr(poly, quantity)(temperature) / _ENGINE_DIVISOR.get(quantity, 1000.0)
    return (value, None) if isfinite(value) else (None, "nonfinite_engine_result")
