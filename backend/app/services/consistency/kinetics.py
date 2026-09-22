"""D3: supplied elementary forward/reverse rates against supplied NASA thermo."""
from collections import Counter
from itertools import product
from math import isfinite, log

from rdkit import Chem

from app.chemistry.species import element_counts_from_smiles
from app.services.consistency import engine
from app.services.consistency.core import AdvisoryResult, encoded, finding, snapshot, temperatures, thermo_inputs
from app.services.trust.rubrics import THERMO_KINETICS_CONSISTENCY_V1

RUNNER = "thermo_kinetics_consistency"


def _rate(record, order, ct):
    units = {
        "per_s": (1, 1.0), "m3_mol_s": (2, 1000.0), "cm3_mol_s": (2, 0.001),
        "cm3_molecule_s": (2, 1e-6 * ct.avogadro),
        "m6_mol2_s": (3, 1e6), "cm6_mol2_s": (3, 1e-6),
        "cm6_molecule2_s": (3, (1e-6 * ct.avogadro) ** 2),
    }
    if record.model_kind not in ("arrhenius", "modified_arrhenius"):
        return None, "unsupported_rate_representation"
    if (record.is_third_body or record.falloff is not None or record.plog_entries
            or record.chebyshev is not None or record.third_body_efficiencies or record.arrhenius_entries
            or record.network_kinetics_id is not None or record.pressure_context != "high_p_limit"):
        return None, "non_elementary_or_ambiguous_effective_rate"
    if record.a_units not in units or units[record.a_units][0] != order:
        return None, "incompatible_or_missing_rate_units"
    if any(v is None or not isfinite(v) for v in (record.a, record.n, record.ea_kj_mol)) or record.a <= 0:
        return None, "incomplete_or_invalid_rate_coefficients"
    if record.tmin_k is None or record.tmax_k is None or not 0 < record.tmin_k <= record.tmax_k:
        return None, "missing_or_invalid_rate_domain"
    factor = units[record.a_units][1]
    if record.degeneracy_convention == "not_applied":
        if record.degeneracy is None or not isfinite(record.degeneracy) or record.degeneracy <= 0:
            return None, "missing_or_invalid_degeneracy"
        factor *= record.degeneracy
    elif record.degeneracy_convention != "already_applied":
        return None, "ambiguous_degeneracy_convention"
    return ct.ArrheniusRate(record.a * factor, record.n, record.ea_kj_mol * 1e6), None


def compare_kinetics(forward, reverse, thermo_by_entry, *, temperature_grid):
    """Pure comparison. Mapping keys are resolved species-entry ids, never guessed."""
    grid = temperatures(temperature_grid)
    if not grid:
        raise ValueError("thermo-kinetics comparisons require explicit temperatures")
    ct = engine.cantera()
    entry = forward.reaction_entry
    participants = list(entry.structure_participants)
    entries = {p.species_entry_id: p.species_entry for p in participants}
    thermo_items = sorted(thermo_by_entry.items())
    refs = [forward.public_ref, reverse.public_ref, entry.public_ref]
    # Per-input findings keep citation cardinality and JSON message sizes bounded.
    findings = []
    for record in (forward, reverse):
        findings.append(finding(forward, {
            "input": record.public_ref, "a_uncertainty": record.a_uncertainty,
            "a_uncertainty_kind": record.a_uncertainty_kind, "a_units": record.a_units,
            "n_uncertainty": record.n_uncertainty, "ea_uncertainty_kj_mol": record.ea_uncertainty_kj_mol,
            "uncertainty_propagation": None, "coverage_covariance_independence": "unspecified",
            "degeneracy": record.degeneracy, "degeneracy_convention": record.degeneracy_convention,
        }, [record.public_ref]))
    for _, thermo in thermo_items:
        findings.append(finding(forward, {
            "input": thermo.public_ref, "species_entry": thermo.species_entry.public_ref,
            "reference_pressure_bar": thermo.reference_pressure_bar,
            "s298_uncertainty_j_mol_k": thermo.s298_uncertainty_j_mol_k,
            "h298_uncertainty_kj_mol": thermo.h298_uncertainty_kj_mol,
            "fit_uncertainty": None, "coverage_covariance_independence": "unspecified",
        }, [thermo.public_ref, thermo.species_entry.public_ref]))
    reason = None
    if forward.reaction_entry_id != reverse.reaction_entry_id:
        reason = "different_reaction_entries"
    elif forward.direction != "forward" or reverse.direction != "reverse":
        reason = "explicit_opposite_directions_required"
    elif set(entries) != set(thermo_by_entry) or any(t.species_entry_id != key for key, t in thermo_items):
        reason = "incomplete_or_incompatible_thermo_mapping"
    reactants = Counter(p.species_entry_id for p in participants if p.role == "reactant")
    products = Counter(p.species_entry_id for p in participants if p.role == "product")
    if not reactants or not products or len(participants) != sum(reactants.values()) + sum(products.values()):
        reason = reason or "missing_or_unsupported_participants"
    compositions = {}
    if reason is None:
        balance = Counter()
        charge = 0
        for key, species_entry in entries.items():
            species = species_entry.species
            molecule = Chem.MolFromSmiles(species.smiles)
            if molecule is None:
                reason = "unusable_species_composition"
                break
            if any(atom.GetIsotope() for atom in molecule.GetAtoms()):
                reason = "isotope_specific_equilibrium_unsupported"
                break
            try:
                compositions[key] = dict(element_counts_from_smiles(species.smiles))
            except ValueError:
                reason = "unusable_species_composition"
                break
            coefficient = products[key] - reactants[key]
            for element, count in compositions[key].items():
                balance[element] += coefficient * count
            charge += coefficient * species.charge
        if any(balance.values()) or charge:
            reason = reason or "unbalanced_stoichiometry"
    if reason is None:
        for _, thermo in thermo_items:
            reason = reason or engine.gas_state_reason(thermo)
        if len({t.reference_pressure_bar for _, t in thermo_items}) != 1:
            reason = reason or "incompatible_reference_pressures"
    kf = kr = None
    if reason is None:
        kf, error_f = _rate(forward, sum(reactants.values()), ct)
        kr, error_r = _rate(reverse, sum(products.values()), ct)
        reason = error_f or error_r
    choices = [engine.fit_names(t) for _, t in thermo_items]
    if any(not names for names in choices):
        reason = reason or "missing_nasa_thermo"
    combinations = list(product(*choices)) if reason is None else [()]
    if len(combinations) > 64:
        raise ValueError("comparison exceeds 64 explicit representation combinations")
    for fits in combinations:
        fit_refs = [f"{t.public_ref}:{fit}" for (_, t), fit in zip(thermo_items, fits, strict=False)]
        for temperature in grid:
            error = reason
            payload = {"temperature_k": temperature, "rate_units": "kmol,m,s", "reason": error}
            if error is None and any(not r.tmin_k <= temperature <= r.tmax_k for r in (forward, reverse)):
                error = "temperature_outside_rate_domain"
            species_list = []
            if error is None:
                for (key, thermo), fit in zip(thermo_items, fits, strict=False):
                    poly, error = engine.polynomial(thermo, fit, temperature)
                    if error:
                        break
                    composition = dict(compositions[key])
                    if entries[key].species.charge:
                        composition["E"] = -entries[key].species.charge
                    species = ct.Species(entries[key].public_ref, composition)
                    species.thermo = poly
                    species_list.append(species)
            if error is None:
                reaction = ct.Reaction(
                    reactants={entries[k].public_ref: n for k, n in reactants.items()},
                    products={entries[k].public_ref: n for k, n in products.items()}, rate=kf,
                )
                if reaction.third_body is not None:
                    payload["reason"] = "inferred_third_body_unsupported"
                    findings.append(finding(forward, payload, refs + fit_refs))
                    continue
                reaction.reversible = True
                gas = ct.Solution(thermo="ideal-gas", kinetics="gas", species=species_list, reactions=[reaction])
                pressure = thermo_items[0][1].reference_pressure_bar * 100000.0
                gas.TP = temperature, pressure
                forward_rate = float(gas.forward_rate_constants[0])
                reverse_rate = float(kr(temperature))
                equilibrium = float(gas.equilibrium_constants[0])
                implied_reverse = float(gas.reverse_rate_constants[0])
                if all(isfinite(v) and v > 0 for v in (forward_rate, reverse_rate, equilibrium, implied_reverse)):
                    payload.update(k_forward=forward_rate, k_reverse_supplied=reverse_rate,
                                   k_reverse_thermo=implied_reverse, equilibrium_kc=equilibrium,
                                   reverse_residual=reverse_rate - implied_reverse,
                                   log_ratio_residual=log(forward_rate) - log(reverse_rate) - log(equilibrium),
                                   reference_pressure_bar=pressure / 100000.0)
                else:
                    error = "nonfinite_or_nonpositive_engine_result"
            payload["reason"] = error
            findings.append(finding(forward, payload, refs + fit_refs))
    inputs = {
        "forward": snapshot(forward, ("source_calculations", "falloff", "plog_entries", "chebyshev",
                                      "third_body_efficiencies", "arrhenius_entries", "literature")),
        "reverse": snapshot(reverse, ("source_calculations", "falloff", "plog_entries", "chebyshev",
                                      "third_body_efficiencies", "arrhenius_entries", "literature")),
        "participants": sorted((snapshot(p, ("species_entry",)) for p in participants), key=encoded),
        "species": sorted((snapshot(e.species) for e in entries.values()), key=encoded),
        "thermo_mapping": [(key, thermo_inputs(t)) for key, t in thermo_items],
        "grid": grid, "engine": f"cantera/{engine.ENGINE_VERSION}", "units": "kmol,m,s;bar;K;kJ/mol",
    }
    return AdvisoryResult(forward, RUNNER, THERMO_KINETICS_CONSISTENCY_V1, tuple(findings), encoded(inputs))
