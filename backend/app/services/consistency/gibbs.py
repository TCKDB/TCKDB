"""D4: a thermo record's stored Gibbs values against its own H and S.

``ThermoPoint.g_kj_mol`` is defined (model docstring) as ``H(T) - T*S(T)``
on the record's enthalpy zero. Under the only declared basis,
``formation_298k``, that is ``dfH(298.15) + [H(T) - H(298.15)] - T*S_abs(T)``
-- the convention Cantera's ``mu0``, RMG's free energy and ARC's
``g_kj_mol`` share. It is NOT the Gibbs energy of formation; that check is
held (Phase D plan, D4). There is no separate G declaration: the record's
``enthalpy_reference_kind`` governs ``g_kj_mol`` too, so an undeclared
record is excluded, and a G that follows some other convention on a
declared record is reported as its raw residual -- never relabelled and
never guessed at.

For every stored G, each representation the record carries supplies BOTH
H and S at that point's temperature, and the residual is

    r_G(T) = g(T) - [h(T) - T*s(T)/1000]      (kJ/mol)

H and S are never mixed across representations. Only the point source (the
same row's own h and s) is an identity, so only it gets a float-precision
flag; every other source reports its residual and nothing else. Advisory
only: no threshold, no status, selection, trust or approval effect.
"""
import sys
from math import isfinite

from tckdb_schemas.enthalpy_reference import shared_enthalpy_reference

from app.db.models.common import EnthalpyReferenceKind
from app.services.consistency import engine
from app.services.consistency.core import AdvisoryResult, encoded, finding, snapshot, thermo_inputs
from app.services.consistency.stoichiometry import species_scope_reason
from app.services.trust.rubrics import GIBBS_SELF_CONSISTENCY_V1

RUNNER = "gibbs_self_consistency"

G_DEFINITION = "formation_298k H(T) - T*S(T)"
RESIDUAL_DEFINITION = "g - (h - T*s/1000)"

NO_STORED_GIBBS_VALUES = "no_stored_gibbs_values"
POINT_MISSING_ENTHALPY_OR_ENTROPY = "gibbs_point_missing_enthalpy_or_entropy"
NONFINITE_STORED_VALUE = "nonfinite_stored_value"

#: The float-precision identity flag for the point source: the residual of
#: three stored doubles combined by one multiply and two subtractions can
#: be nonzero only by rounding, bounded by a few ulps of the largest term.
#: 16 epsilon of the summed magnitudes is that bound with headroom. It is a
#: statement about arithmetic, not an accuracy threshold.
_FLOAT_PRECISION_ULPS = 16
_EPSILON = sys.float_info.epsilon

#: D4's own pressure exemption (decided 2026-09-27). g, h and s of one
#: record share one standard state by construction, so the recorded
#: reference pressure -- whatever it is, even null or invalid -- cannot
#: change r_G, and it never gates this check. G itself does depend on
#: pressure (through S), so ``"g"`` is deliberately NOT added to
#: ``engine.PRESSURE_INDEPENDENT_QUANTITIES``; the fit gate is asked with
#: the enthalpy token, which selects exactly the phase-and-range gate. The
#: entropy read from that same polynomial does not depend on the constructor
#: pressure either (measured 2026-09-27 on Cantera 3.2.0: NASA7 and a NASA9
#: region return the same ``s`` at 1e5, 101325, 2.5e5, 0, -1, NaN and inf Pa).
_PRESSURE_FREE_GATE = "h"

_SCALAR_SOURCE = "scalar298"


def _sources(thermo):
    names = ["point", *engine.fit_names(thermo)]
    if thermo.h298_kj_mol is not None or thermo.s298_j_mol_k is not None:
        names.append(_SCALAR_SOURCE)
    return names


def _finite(*values):
    return all(isfinite(v) for v in values)


def _read_source(thermo, point, source):
    """Return (h kJ/mol, s J/mol/K, reason) from ONE representation at the point's T."""
    temperature = point.temperature_k
    if source == "point":
        h, s = point.h_kj_mol, point.s_j_mol_k
        if h is None or s is None:
            return h, s, POINT_MISSING_ENTHALPY_OR_ENTROPY
        return h, s, None if _finite(h, s) else NONFINITE_STORED_VALUE
    if source == _SCALAR_SOURCE:
        h, reason_h = engine.evaluate(thermo, "h298", temperature, "h")
        s, reason_s = engine.evaluate(thermo, "s298", temperature, "s")
        reason = reason_h or reason_s
        if reason:
            return h, s, reason
        return h, s, None if _finite(h, s) else NONFINITE_STORED_VALUE
    poly, reason = engine.polynomial(thermo, source, temperature, quantity=_PRESSURE_FREE_GATE)
    if reason:
        return None, None, reason
    # One Cantera object supplies both, so H and S cannot come from different
    # representations or different NASA7 branches / NASA9 intervals.
    h = poly.h(temperature) / engine._ENGINE_DIVISOR["h"]
    s = poly.s(temperature) / engine._ENGINE_DIVISOR["s"]
    return (h, s, None) if _finite(h, s) else (None, None, "nonfinite_engine_result")


def _record_reason(thermo):
    """Record-level exclusion, first match wins: scope, enthalpy basis, phase."""
    reason = species_scope_reason(thermo.species_entry)
    if reason:
        return reason
    kind, reason = shared_enthalpy_reference([thermo.enthalpy_reference_kind])
    if reason:
        return reason
    if kind != EnthalpyReferenceKind.formation_298k.value:
        # G_DEFINITION names formation_298k; a future basis must be wired
        # here deliberately rather than inherit a definition it does not have.
        raise ValueError(f"gibbs self-consistency is defined only on formation_298k, not {kind!r}")
    return engine.gas_state_reason(thermo, quantity=_PRESSURE_FREE_GATE)


def compare_gibbs(thermo):
    """Pure comparison of one resolved thermo record's stored G values."""
    refs = [thermo.public_ref, thermo.species_entry.public_ref]
    context = {
        "g_definition": G_DEFINITION,
        "enthalpy_reference_kind": thermo.enthalpy_reference_kind,
        "reference_pressure_bar": thermo.reference_pressure_bar,
        "phase": thermo.phase,
    }
    eligible = sorted((p for p in thermo.points if p.g_kj_mol is not None), key=lambda p: p.temperature_k)
    findings = []
    if not eligible:
        findings.append(finding(thermo, {**context, "reason": NO_STORED_GIBBS_VALUES,
                                         "points_examined": len(thermo.points)}, refs))
    else:
        sources = _sources(thermo)
        if engine.fit_names(thermo):
            engine.cantera()  # Configuration failure precedes any finding.
        record_reason = _record_reason(thermo)
        for point in eligible:
            temperature, g = point.temperature_k, point.g_kj_mol
            for source in sources:
                h = s = residual = within = bound = None
                reason = record_reason or (None if isfinite(g) else NONFINITE_STORED_VALUE)
                if reason is None:
                    h, s, reason = _read_source(thermo, point, source)
                if reason is None:
                    residual = g - (h - temperature * s / 1000.0)
                    if source == "point":
                        bound = _FLOAT_PRECISION_ULPS * _EPSILON * (
                            abs(g) + abs(h) + temperature * abs(s) / 1000.0)
                        within = abs(residual) <= bound
                findings.append(finding(thermo, {
                    **context, "source": source, "temperature_k": temperature, "unit": "kJ/mol",
                    "g_kj_mol": g, "h_kj_mol": h, "s_j_mol_k": s,
                    "residual_definition": RESIDUAL_DEFINITION, "residual_kj_mol": residual,
                    "within_float_precision": within, "float_precision_bound_kj_mol": bound,
                    "reason": reason,
                }, refs))
    return AdvisoryResult(thermo, RUNNER, GIBBS_SELF_CONSISTENCY_V1, tuple(findings), encoded({
        "engine": f"cantera/{engine.ENGINE_VERSION}", "target": thermo_inputs(thermo),
        "species_entry": snapshot(thermo.species_entry, ("species",)),
        "g_definition": G_DEFINITION, "residual_definition": RESIDUAL_DEFINITION,
        "units": "kJ/mol;J/mol/K;K;bar",
        "policy": "point-identity-16eps;same-representation-h-s;scalar298-exact;nasa7-low;nasa9-upper;"
                  "pressure-not-gated",
    }))
