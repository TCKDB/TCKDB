"""D5: Kirchhoff consistency of supplied enthalpies -- advisory, residuals only.

Under ``enthalpy_reference_kind = formation_298k`` a record's enthalpy at T is

    H(T) = dfH(298.15 K) + integral_{298.15}^{T} Cp dT,

so the change of a species' enthalpy between two temperatures is its own
heat-capacity integral; no element term enters. Inside one fit interval
dH/dT = Cp holds by construction, so a fit is never compared with itself
(no finding). What can disagree, and is reported, is:

``anchor``
    A = H_r(T) - H_q(T) for two representations r, q of
    {h298, point, nasa7, nasa9} at one exact grid temperature.
``increment``
    K = [H_r(T) - H_r(T0)] - I_q(T0, T), where I_q is the exact integral of
    the fit q's Cp, taken interval by interval with the single-branch
    evaluators, so q's own boundary jumps are excluded. q is a fit; r is a
    fit or tabulated points (h298 has one temperature, so no increment).
``boundary_jump``
    J = H_upper(Tb) - H_lower(Tb) at a NASA-7 ``t_mid`` and at every NASA-9
    shared interval boundary.

T0 is 298.15 K when both r and q evaluate there, else the lowest grid
temperature at which both do (recorded as ``increment_from_k``). A pair whose
only grid temperature is T0 has nothing to integrate over and says so
(``no_comparison_pairs_or_temperatures``) rather than vanishing. Nothing is
judged against a threshold and nothing changes status, selection, trust or
approval.

Boundary ownership is the engine's, unchanged: at NASA-7 ``t_mid`` the
combined fit answers with the LOW branch, at a NASA-9 shared boundary with
the UPPER interval. So when r is a fit evaluated exactly at a boundary, its
stated change H_r(T) - H_r(T0) includes that boundary's jump for NASA-9 and
excludes it for NASA-7. Every finding therefore names the branch/interval
each fit value came from (``segment`` on anchor sides; ``r_segment_at_T0``,
``r_segment_at_T``, ``q_segment_at_T0``, ``q_segment_at_T`` on increments),
so a jump's contribution is visible rather than silent.

A record is never its own neighbour (that would compare it with itself and
report every jump twice); passing one raises ``ValueError``. A stored point
or h298 that is not finite is ``nonfinite_stored_value``, never a residual.

Decisions (2026-09-27): a record that does not declare its enthalpy
reference is excluded (``enthalpy_reference_unrecorded``) and two declared
records on different bases are ``enthalpy_reference_mixed`` -- a basis is
never inferred. Reference pressure never gates H; the recorded value, null
included, is shown in every finding. Cross-record anchors run although no
record states its element-reference compilation; every such finding says
so and says that cancellation between the two records is assumed.

Out of scope: reaction-level Kirchhoff (a linear recombination of these
per-species findings; nothing reaction-level is stored at two
temperatures) and formation-increment claims (they need element Cp data
that is held).
"""
from itertools import combinations, pairwise, product
from math import isfinite

from tckdb_schemas.enthalpy_reference import shared_enthalpy_reference

from app.services.consistency import engine
from app.services.consistency.core import AdvisoryResult, encoded, finding, temperatures, thermo_inputs
from app.services.consistency.stoichiometry import species_scope_reason
from app.services.trust.rubrics import KIRCHHOFF_CONSISTENCY_V1

RUNNER = "kirchhoff_consistency"

T298_K = 298.15
UNIT = "kJ/mol"
FITS = ("nasa7", "nasa9")

#: Cantera returns H in J/kmol; findings are in kJ/mol (as engine.evaluate).
_J_PER_KMOL_PER_KJ_PER_MOL = 1e6

NASA9_GAP_NO_SHARED_BOUNDARY = "nasa9_gap_no_shared_boundary"
INTEGRATION_PATH_CROSSES_NASA9_GAP = "integration_path_crosses_nasa9_gap"
INCREMENT_REFERENCE_TEMPERATURE_UNAVAILABLE = "increment_reference_temperature_unavailable"
NO_COMPARISON_PAIRS = "no_comparison_pairs_or_temperatures"
NONFINITE_STORED_VALUE = "nonfinite_stored_value"


def representations(thermo):
    """The enthalpy representations a record supplies, scalars first."""
    names = []
    if thermo.h298_kj_mol is not None:
        names.append("h298")
    if any(p.h_kj_mol is not None for p in thermo.points):
        names.append("point")
    names += [name for name in engine.fit_names(thermo) if name in FITS]
    return names


def _record_reason(thermo):
    """Scope, then declared basis, then phase. Pressure is never consulted for H."""
    return (species_scope_reason(thermo.species_entry)
            or shared_enthalpy_reference([thermo.enthalpy_reference_kind])[1]
            or engine.gas_state_reason(thermo, quantity="h"))


def _pair_reason(left, right):
    if left is right:
        return _record_reason(left)
    if left.species_entry_id != right.species_entry_id:
        return "different_species_entries"
    return (_record_reason(left) or _record_reason(right)
            or shared_enthalpy_reference([left.enthalpy_reference_kind, right.enthalpy_reference_kind])[1])


def _pressures(*records):
    return {t.public_ref: t.reference_pressure_bar for t in records}


def _difference(x, y, reason):
    """x - y when the comparison is available; a reason always means no residual."""
    return x - y if reason is None and x is not None and y is not None else None


def _h(thermo, representation, temperature):
    value, reason = engine.evaluate(thermo, representation, temperature, "h")
    if reason is None and not isfinite(value):
        return None, NONFINITE_STORED_VALUE
    return value, reason


def _owning_segment(thermo, representation, temperature):
    """The branch/interval the combined fit answers from at ``temperature``; None for non-fits.

    Mirrors the engine's ownership (``engine.polynomial``): NASA-7 ``t_mid``
    belongs to the low branch, a NASA-9 shared boundary to the upper interval.
    """
    if representation == "nasa7":
        return "low" if temperature <= thermo.nasa.t_mid else "high"
    if representation == "nasa9":
        owner = None
        for interval in sorted(thermo.nasa9_intervals, key=lambda iv: iv.interval_index):
            if interval.t_min_k <= temperature <= interval.t_max_k:
                owner = interval.interval_index
        return owner
    return None


def _segment_h(thermo, representation, segment, temperature):
    """H (kJ/mol) of ONE NASA-7 branch or ONE NASA-9 interval at ``temperature``."""
    if representation == "nasa7":
        poly, reason = engine.nasa7_branch(thermo, segment, temperature, quantity="h")
    else:
        poly, reason = engine.nasa9_interval(thermo, segment, temperature, quantity="h")
    if reason:
        return None, reason
    value = poly.h(temperature) / _J_PER_KMOL_PER_KJ_PER_MOL
    return (value, None) if isfinite(value) else (None, "nonfinite_engine_result")


def _segments(thermo, representation, low, high):
    """Split [low, high] into (segment, a, b) pieces; both ends already evaluate.

    NASA-7 splits at ``t_mid``. NASA-9 walks intervals in index order and
    refuses to bridge a region no interval covers.
    """
    if representation == "nasa7":
        t_mid = thermo.nasa.t_mid
        if high <= t_mid:
            return [("low", low, high)], None
        if low >= t_mid:
            return [("high", low, high)], None
        return [("low", low, t_mid), ("high", t_mid, high)], None
    pieces, cursor = [], low
    for interval in sorted(thermo.nasa9_intervals, key=lambda iv: iv.interval_index):
        if interval.t_max_k <= cursor:
            continue
        if interval.t_min_k > cursor:
            return None, INTEGRATION_PATH_CROSSES_NASA9_GAP
        end = min(high, interval.t_max_k)
        pieces.append((interval.interval_index, cursor, end))
        cursor = end
        if cursor >= high:
            return pieces, None
    return None, INTEGRATION_PATH_CROSSES_NASA9_GAP


def _integral(thermo, representation, start, end):
    """Return (I_q(start, end), segments, reason): interval-local, own jumps excluded."""
    low, high = sorted((start, end))
    for temperature in (low, high):
        _, reason = _h(thermo, representation, temperature)
        if reason:
            return None, None, reason
    pieces, reason = _segments(thermo, representation, low, high)
    if reason:
        return None, None, reason
    total = 0.0
    for segment, a, b in pieces:
        h_a, reason_a = _segment_h(thermo, representation, segment, a)
        h_b, reason_b = _segment_h(thermo, representation, segment, b)
        if reason_a or reason_b:
            return None, None, reason_a or reason_b
        total += h_b - h_a
    segments = [{"segment": s, "from_k": a, "to_k": b} for s, a, b in pieces]
    return (total if end >= start else -total), segments, None


def _increment_from(r_thermo, r, q_thermo, q, grid):
    for temperature in (T298_K, *grid):
        if _h(r_thermo, r, temperature)[1] is None and _h(q_thermo, q, temperature)[1] is None:
            return temperature
    return None


def _boundaries(thermo):
    """Yield (representation, boundary_k, (lower segment, upper segment) or None, reason)."""
    if thermo.nasa is not None:
        yield "nasa7", thermo.nasa.t_mid, ("low", "high"), None
    ordered = sorted(thermo.nasa9_intervals, key=lambda iv: iv.interval_index)
    for lower, upper in pairwise(ordered):
        if lower.t_max_k < upper.t_min_k:
            yield "nasa9", None, (lower.interval_index, upper.interval_index), NASA9_GAP_NO_SHARED_BOUNDARY
        else:
            # An overlap is reported by the single-interval evaluator itself
            # (invalid_nasa9_intervals); it is never treated as a boundary.
            yield "nasa9", lower.t_max_k, (lower.interval_index, upper.interval_index), None


def _interval_bound(thermo, index, field):
    return next(getattr(iv, field) for iv in thermo.nasa9_intervals if iv.interval_index == index)


def _jump_findings(target, thermo):
    rows = []
    base = _record_reason(thermo)
    for representation, boundary, (lower, upper), reason in _boundaries(thermo):
        reason = base or reason
        values = {}
        if reason is None:
            for side, segment in (("lower", lower), ("upper", upper)):
                values[side], error = _segment_h(thermo, representation, segment, boundary)
                reason = reason or error
        payload = {
            "kind": "boundary_jump", "quantity": "h", "unit": UNIT, "input": thermo.public_ref,
            "representation": representation, "boundary_k": boundary,
            "lower": {"segment": lower, "value": values.get("lower")},
            "upper": {"segment": upper, "value": values.get("upper")},
            "residual": values["upper"] - values["lower"] if reason is None else None,
            "reason": reason, "reference_pressure_bar": _pressures(thermo),
        }
        if representation == "nasa9" and boundary is None:
            payload["gap_k"] = [_interval_bound(thermo, lower, "t_max_k"), _interval_bound(thermo, upper, "t_min_k")]
        rows.append(finding(target, payload, [thermo.public_ref]))
    return rows


def _scope_inputs(thermo):
    entry = thermo.species_entry
    return {"species_entry": entry.public_ref, "isotope_key": entry.isotope_key,
            "smiles": entry.species.smiles, "charge": entry.species.charge}


def compare_kirchhoff(thermo, *, comparison=None, temperature_grid=()):
    """Pure comparison over resolved records.

    Grid: {298.15} U temperatures of points carrying h U explicit
    temperatures. A neighbour comparison requires explicit temperatures,
    as in D1, and then compares across the two records only.
    """
    if comparison is not None and not temperature_grid:
        raise ValueError("neighbour comparisons require explicit temperatures")
    if comparison is not None and (comparison is thermo or comparison.public_ref == thermo.public_ref):
        raise ValueError("a thermo record cannot be its own neighbour; omit the comparison for a single-record check")
    records = [thermo] if comparison is None else [thermo, comparison]
    grid = temperatures([T298_K, *temperature_grid,
                         *(p.temperature_k for t in records for p in t.points if p.h_kj_mol is not None)])
    if any(engine.fit_names(t) for t in records):
        engine.cantera()  # Configuration failure precedes any finding.
    refs = [t.public_ref for t in records]
    findings = []
    for t in records:
        findings.append(finding(thermo, {
            "input": t.public_ref, "species_entry": t.species_entry.public_ref, "phase": t.phase,
            "reference_pressure_bar": _pressures(t), "enthalpy_reference_kind": t.enthalpy_reference_kind,
            "element_reference_compilation": "not_recorded", "species_scope": species_scope_reason(t.species_entry),
            "h298_uncertainty_kj_mol": t.h298_uncertainty_kj_mol,
            "uncertainty_meaning": "as_supplied; coverage_and_covariance_unspecified",
            "unevaluated_representations": ["wilhoit"] if t.wilhoit is not None else [],
        }, [t.public_ref]))

    if comparison is None:
        names = representations(thermo)
        anchors = [(thermo, a, thermo, b) for a, b in combinations(names, 2)]
        increments = [(thermo, r, thermo, q) for r in names if r != "h298" for q in names if q in FITS and q != r]
    else:
        left, right = representations(thermo), representations(comparison)
        anchors = [(thermo, a, comparison, b) for a, b in product(left, right)]
        increments = ([(thermo, r, comparison, q) for r in left if r != "h298" for q in right if q in FITS]
                      + [(comparison, r, thermo, q) for r in right if r != "h298" for q in left if q in FITS])

    if not anchors:
        findings.append(finding(thermo, {"kind": "anchor", "reason": NO_COMPARISON_PAIRS,
                                         "reference_pressure_bar": _pressures(*records)}, refs))
    for left, a, right, b in anchors:
        base = _pair_reason(left, right)
        for temperature in grid:
            x = y = None
            reason = base
            if reason is None:
                x, error_x = _h(left, a, temperature)
                y, error_y = _h(right, b, temperature)
                reason = error_x or error_y
            findings.append(finding(thermo, {
                "kind": "anchor", "quantity": "h", "unit": UNIT, "temperature_k": temperature,
                "left": {"ref": left.public_ref, "representation": a, "value": x,
                         "segment": _owning_segment(left, a, temperature) if x is not None else None},
                "right": {"ref": right.public_ref, "representation": b, "value": y,
                          "segment": _owning_segment(right, b, temperature) if y is not None else None},
                "residual": _difference(x, y, reason), "reason": reason,
                "reference_pressure_bar": _pressures(left, right),
                "element_reference_compilation": "not_recorded",
                "element_reference_cancellation": "same_record" if left is right else "assumed",
            }, refs))

    if not increments:
        findings.append(finding(thermo, {"kind": "increment", "reason": NO_COMPARISON_PAIRS,
                                         "reference_pressure_bar": _pressures(*records)}, refs))
    for r_thermo, r, q_thermo, q in increments:
        pair = {"kind": "increment", "quantity": "h", "unit": UNIT,
                "enthalpy": {"ref": r_thermo.public_ref, "representation": r},
                "integral": {"ref": q_thermo.public_ref, "representation": q, "own_boundary_jumps": "excluded"},
                "reference_pressure_bar": _pressures(r_thermo, q_thermo)}
        reason = _pair_reason(r_thermo, q_thermo)
        start = None if reason else _increment_from(r_thermo, r, q_thermo, q, grid)
        if reason or start is None:
            findings.append(finding(thermo, {
                **pair, "temperature_k": None, "increment_from_k": None, "residual": None,
                "reason": reason or INCREMENT_REFERENCE_TEMPERATURE_UNAVAILABLE,
            }, refs))
            continue
        h_start, _ = _h(r_thermo, r, start)
        others = [temperature for temperature in grid if temperature != start]
        if not others:
            # A real pair with nothing to integrate over is stated, never silently dropped.
            findings.append(finding(thermo, {
                **pair, "temperature_k": None, "increment_from_k": start, "residual": None,
                "reason": NO_COMPARISON_PAIRS,
            }, refs))
        for temperature in others:
            h_end, reason = _h(r_thermo, r, temperature)
            integral = segments = None
            if reason is None:
                integral, segments, reason = _integral(q_thermo, q, start, temperature)
            increment = h_end - h_start if h_end is not None else None
            upward = temperature > start
            findings.append(finding(thermo, {
                **pair, "temperature_k": temperature, "increment_from_k": start,
                "enthalpy": {**pair["enthalpy"], "value_at_from": h_start, "value_at_temperature": h_end,
                             "increment": increment},
                "integral": {**pair["integral"], "value": integral, "segments": segments},
                "r_segment_at_T0": _owning_segment(r_thermo, r, start),
                "r_segment_at_T": _owning_segment(r_thermo, r, temperature) if h_end is not None else None,
                "q_segment_at_T0": segments[0 if upward else -1]["segment"] if segments else None,
                "q_segment_at_T": segments[-1 if upward else 0]["segment"] if segments else None,
                "residual": _difference(increment, integral, reason), "reason": reason,
            }, refs))

    for t in records:
        findings.extend(_jump_findings(thermo, t))

    return AdvisoryResult(thermo, RUNNER, KIRCHHOFF_CONSISTENCY_V1, tuple(findings), encoded({
        "engine": f"cantera/{engine.ENGINE_VERSION}", "grid": grid,
        "target": thermo_inputs(thermo), "target_scope": _scope_inputs(thermo),
        "comparison": thermo_inputs(comparison) if comparison else None,
        "comparison_scope": _scope_inputs(comparison) if comparison else None,
        "units": "kJ/mol;K;bar",
        "boundary_policy": "nasa7-branches-at-t_mid;nasa9-own-interval;exact-points;h298-at-298.15-only",
        "increment_from_policy": "298.15-else-lowest-common-grid",
    }))
