"""D1: every supplied Cp/entropy representation remains separately visible."""
from itertools import combinations, product

from app.services.consistency import engine
from app.services.consistency.core import AdvisoryResult, encoded, finding, temperatures, thermo_inputs
from app.services.trust.rubrics import THERMO_CONSISTENCY_V1

RUNNER = "thermo_consistency"


def compare_thermo(thermo, *, comparison=None, temperature_grid=()):
    """Pure comparison over resolved records, with exact tabulated temperatures."""
    if comparison is not None and not temperature_grid:
        raise ValueError("neighbour comparisons require explicit temperatures")
    grid = temperatures(temperature_grid or (
        [p.temperature_k for p in thermo.points] + ([298.15] if thermo.s298_j_mol_k is not None else [])
    ))
    records = [thermo] if comparison is None else [thermo, comparison]
    if any(engine.fit_names(t) for t in records):
        engine.cantera()  # Configuration failure precedes any finding.
    findings = []
    refs = [t.public_ref for t in records]
    for t in records:
        findings.append(finding(thermo, {
            "input": t.public_ref, "phase": t.phase, "reference_pressure_bar": t.reference_pressure_bar,
            "s298_uncertainty_j_mol_k": t.s298_uncertainty_j_mol_k,
            "uncertainty_meaning": "as_supplied; coverage_and_covariance_unspecified",
            "fit_and_point_uncertainty": None,
        }, [t.public_ref]))
    for quantity in ("cp", "s"):
        def representations(t, quantity=quantity):
            names = engine.fit_names(t)
            if t.points:
                names.append("point")
            if quantity == "s" and t.s298_j_mol_k is not None:
                names.append("s298")
            return names

        if comparison is None:
            pairs = [(thermo, a, thermo, b) for a, b in combinations(representations(thermo), 2)]
        else:
            pairs = [(thermo, a, comparison, b) for a, b in product(
                representations(thermo), representations(comparison))]
        if not pairs or not grid:
            findings.append(finding(thermo, {"quantity": quantity, "reason": "no_comparison_pairs_or_temperatures"}, refs))
        for left, a, right, b in pairs:
            for temperature in grid:
                reason = None
                if left.species_entry_id != right.species_entry_id:
                    reason = "different_species_entries"
                for t in (left, right):
                    reason = reason or engine.gas_state_reason(t, quantity=quantity)
                # Reference pressure cannot change a heat capacity (decided
                # 2026-09-23): only entropy needs matching reference
                # pressures to be comparable.
                if quantity != "cp" and left.reference_pressure_bar != right.reference_pressure_bar:
                    reason = reason or "incompatible_reference_pressures"
                x = y = None
                if reason is None:
                    x, error_x = engine.evaluate(left, a, temperature, quantity)
                    y, error_y = engine.evaluate(right, b, temperature, quantity)
                    reason = error_x or error_y
                findings.append(finding(thermo, {
                    "quantity": quantity, "unit": "J/mol/K", "temperature_k": temperature,
                    "left": {"ref": left.public_ref, "representation": a, "value": x},
                    "right": {"ref": right.public_ref, "representation": b, "value": y},
                    "residual": x - y if reason is None else None, "reason": reason,
                }, refs))
    return AdvisoryResult(thermo, RUNNER, THERMO_CONSISTENCY_V1, tuple(findings), encoded({
        "engine": f"cantera/{engine.ENGINE_VERSION}", "grid": grid,
        "target": thermo_inputs(thermo), "comparison": thermo_inputs(comparison) if comparison else None,
        "units": "J/mol/K;bar;K", "boundary_policy": "nasa7-low;nasa9-upper;exact-points-v1",
    }))
