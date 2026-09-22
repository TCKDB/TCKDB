"""The pinned Cantera boundary. No local NASA or rate polynomial evaluator."""
from math import isfinite

ENGINE_VERSION = "3.2.0"


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
#: constructors only when ``quantity="cp"`` and the record has none. Cp is
#: pressure-independent for these polynomials -- Cantera returns the same
#: ``cp`` at 1e5, 101325 and 2.5e5 Pa (verified 2026-09-23) -- so the exact
#: value is inert; it exists only to satisfy the constructor's signature.
_NEUTRAL_REFERENCE_PRESSURE_BAR = 1.0


def gas_state_reason(thermo, *, quantity=None):
    """Applicability gate, split per quantity (decided 2026-09-23).

    ``reference_pressure_bar`` fixes the standard-state pressure baked into
    the entropy coefficient, so a missing/invalid value makes any entropy
    comparison unavailable. Heat capacity does not depend on the reference
    pressure at all, so ``quantity="cp"`` skips that check -- only the gas
    phase requirement applies. Every other caller (entropy, and the full
    thermo used by the D3 kinetics/equilibrium comparison) keeps requiring
    a valid reference pressure via the default ``quantity=None``.
    """
    if thermo.phase != "gas":
        return "missing_or_incompatible_gas_phase"
    if quantity == "cp":
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


def polynomial(thermo, representation, temperature, *, quantity=None):
    """Return (Cantera species thermo, reason), without extrapolating across gaps.

    At a NASA9 shared boundary the upper interval owns the temperature, as
    in Cantera's multi-region model. NASA7's midpoint belongs to the low range.
    Each NASA9 region is passed separately to avoid implicitly bridging gaps.

    ``quantity`` is forwarded to :func:`gas_state_reason`: with
    ``quantity="cp"`` a missing ``reference_pressure_bar`` is not an
    applicability failure, and :data:`_NEUTRAL_REFERENCE_PRESSURE_BAR`
    stands in purely to satisfy the polynomial constructor.
    """
    ct = cantera()
    reason = gas_state_reason(thermo, quantity=quantity)
    if reason:
        return None, reason
    if ((thermo.tmin_k is not None and temperature < thermo.tmin_k)
            or (thermo.tmax_k is not None and temperature > thermo.tmax_k)):
        return None, "temperature_outside_record_range"
    reference_pressure_bar = thermo.reference_pressure_bar
    if reference_pressure_bar is None:
        reference_pressure_bar = _NEUTRAL_REFERENCE_PRESSURE_BAR
    pressure = reference_pressure_bar * 100000.0
    if representation == "nasa7":
        nasa = thermo.nasa
        values = [getattr(nasa, f"{prefix}{i}") for prefix in ("a", "b") for i in range(1, 8)]
        bounds = (nasa.t_low, nasa.t_mid, nasa.t_high)
        if any(v is None or not isfinite(v) for v in (*bounds, *values)):
            return None, "incomplete_nasa7"
        if not 0 < bounds[0] < bounds[1] < bounds[2]:
            return None, "invalid_nasa7_intervals"
        if not bounds[0] <= temperature <= bounds[2]:
            return None, "temperature_outside_fit_range"
        return ct.NasaPoly2(bounds[0], bounds[2], pressure, [bounds[1], *values[7:], *values[:7]]), None
    if representation == "nasa9":
        intervals = sorted(thermo.nasa9_intervals, key=lambda iv: iv.interval_index)
        previous = None
        selected = None
        for iv in intervals:
            values = [getattr(iv, f"a{i}") for i in range(1, 10)]
            if any(v is None or not isfinite(v) for v in (iv.t_min_k, iv.t_max_k, *values)):
                return None, "incomplete_nasa9"
            if not 0 < iv.t_min_k < iv.t_max_k or (previous is not None and iv.t_min_k < previous):
                return None, "invalid_nasa9_intervals"
            previous = iv.t_max_k
            if iv.t_min_k <= temperature <= iv.t_max_k:
                selected = iv
        if selected is None:
            return None, "temperature_outside_fit_or_in_gap"
        iv = selected
        coeffs = [1, iv.t_min_k, iv.t_max_k, *[getattr(iv, f"a{i}") for i in range(1, 10)]]
        return ct.Nasa9PolyMultiTempRegion(iv.t_min_k, iv.t_max_k, pressure, coeffs), None
    return None, "unsupported_representation"


def evaluate(thermo, representation, temperature, quantity):
    if representation == "point":
        point = next((p for p in thermo.points if p.temperature_k == temperature), None)
        value = getattr(point, f"{quantity}_j_mol_k", None)
        return value, None if value is not None else "no_exact_matching_point"
    if representation == "s298":
        value = thermo.s298_j_mol_k if temperature == 298.15 and quantity == "s" else None
        return value, None if value is not None else "no_exact_s298_value"
    poly, reason = polynomial(thermo, representation, temperature, quantity=quantity)
    if reason:
        return None, reason
    value = getattr(poly, quantity)(temperature) / 1000.0
    return (value, None) if isfinite(value) else (None, "nonfinite_engine_result")
