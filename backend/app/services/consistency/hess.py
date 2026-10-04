"""D6: a kinetics record's stated reaction energy against Hess's law over mapped thermo.

One explicit cycle, never searched for: the kinetics record names the
reaction energy (its ``kinetics_tunneling_application`` row) and the caller
names one thermo record per participant (``--thermo spe=thm[:rep]``).

    dH_formation(Tb) = sum_i nu_i H_i(Tb)
    dE_claim         = product_energy_kj_mol - reactant_energy_kj_mol
    residual         = dE_claim - dH_formation

``nu_i`` counts ``reaction_entry_structure_participant`` *slots* (products
minus reactants), as D3 does, so ``2 CH3 -> C2H6`` has coefficient 2 on
CH3. The reaction is required to balance, so the element reference terms
inside each formation enthalpy cancel. Which element-reference compilation
the thermo used is not recorded anywhere, so that cancellation is assumed,
never verified, and every finding says so.

Which formation quantity pairs with the reaction energy is fixed by the
tunneling row's ``energy_correction_convention`` (decided 2026-09-27):

* ``thermal_enthalpy_298k`` -> Tb = 298.15 K, H_i from ``h298``, ``nasa7``,
  ``nasa9`` or an exact ``point`` at 298.15 K, every term declaring one
  shared ``enthalpy_reference_kind``.
* ``electronic_plus_zpe`` / ``atom_and_bond_corrected`` -> Tb = 0 K, H_i =
  ``enthalpy_formation_0k_kj_mol``, whose meaning is fixed by the column
  itself, so no ``enthalpy_reference_kind`` is consulted.
* ``electronic_only`` and ``other`` have no thermo counterpart.

Advisory residuals only: no threshold, no combined uncertainty (per-term
uncertainties are listed as supplied), no automatic thermo selection, no
barrier closure (the reverse barrier's zero is undocumented), and no change
to any status, selection, trust or approval. Pressure never gates H.

Gates, first failing reason wins, in this order:

1. reaction side -- ``no_reaction_level_energy`` (no tunneling row, or it
   carries no reactant/product energy), ``tunneling_orientation_not_declared``
   (``kinetics.direction`` is not ``forward``),
   ``tunneling_transition_state_on_other_reaction_entry``,
   ``energy_zero_convention_not_separated_species``,
   ``energy_correction_convention_not_declared``,
   ``energy_correction_convention_without_thermo_counterpart``,
   ``energy_convention_other``, ``reaction_energy_source_untraceable`` (no
   source calculation, or one with no recorded level of theory -- the
   level the energies stand at is part of tracing them),
   ``reaction_energy_solvated_out_of_scope``, ``nonfinite_stored_value``,
   ``reaction_energy_not_separated_species`` (``separated_reactants`` with a
   reactant energy that is not exactly zero -- a pre-reactive complex well,
   say, which the upload allows since barriers may be submerged);
2. participants -- ``missing_or_unsupported_participants``,
   ``incomplete_or_incompatible_thermo_mapping``, the per-species scope
   reasons of :func:`species_scope_reason`, ``unbalanced_stoichiometry``;
3. thermo -- the engine's phase reasons, then per branch
   ``enthalpy_reference_unrecorded`` / ``enthalpy_reference_mixed`` and
   ``participant_enthalpy_unavailable`` (298.15 K), or
   ``participant_formation_0k_absent`` (0 K); then per evaluated term the
   engine's own reason or ``nonfinite_stored_value``.
"""
from enum import Enum
from itertools import product
from math import isfinite

from tckdb_schemas.enthalpy_reference import shared_enthalpy_reference

from app.services.consistency import engine
from app.services.consistency.core import (
    KINETICS_HASH_EXCLUDED_COLUMNS,
    AdvisoryResult,
    encoded,
    finding,
    snapshot,
    thermo_inputs,
)
from app.services.consistency.stoichiometry import (
    element_balance,
    entry_facts,
    is_balanced,
    participant_slots,
    species_scope_reason,
)
from app.services.machine_review.schemas import MachineReviewFinding
from app.services.trust.rubrics import HESS_CONSISTENCY_V1

RUNNER = "hess_consistency"

#: Stated on every finding (decided 2026-09-27): the check runs although no
#: thermo record says which element-reference compilation it used.
ELEMENT_REFERENCE_COMPILATION = "not_recorded; cancellation across terms assumed, unverifiable"

#: Stated on every finding (review of #550): the tunneling row names no
#: endpoint structures. Under ``separated_reactants`` a zero reactant energy
#: is checked, but the product end (a post-reaction complex?) never can be,
#: and under ``absolute`` neither end can. Separated species are assumed.
ENDPOINT_IDENTITY = "not_recorded; separated species assumed"

#: Representations that give an enthalpy at exactly 298.15 K, in the order
#: they are enumerated. Wilhoit is not evaluated by the engine.
ENTHALPY_298_REPRESENTATIONS = ("h298", "nasa7", "nasa9", "point")

MAX_COMBINATIONS = 64

#: Meaning of each evaluated term row, ``terms[thermo_ref]``; the
#: uncertainty is the one supplied for that value, listed, never combined.
TERM_COLUMNS = ("coefficient", "representation", "h_kj_mol", "uncertainty_kj_mol")

#: The size bound on one finding's message, read from the model that
#: enforces it. A payload over it is split (terms) or trimmed (detail lists),
#: never left to fail validation.
_MESSAGE_LIMIT = next(m.max_length for m in MachineReviewFinding.model_fields["message"].metadata
                      if getattr(m, "max_length", None) is not None)
FINDING_TOO_LARGE = "finding_too_large"

_T298_K = 298.15
_SEPARATED_SPECIES_ZEROS = frozenset({"separated_reactants", "absolute"})
_ZERO_KELVIN_CONVENTIONS = frozenset({"electronic_plus_zpe", "atom_and_bond_corrected"})


def _token(value):
    return value.value if isinstance(value, Enum) else value


def _finite(*values):
    return all(isfinite(v) for v in values)


def _reaction_side_reason(kinetics, tunneling):
    """Return the first reason the reaction-energy side cannot be compared, or None."""
    if tunneling is None or tunneling.reactant_energy_kj_mol is None or tunneling.product_energy_kj_mol is None:
        return "no_reaction_level_energy"
    if _token(kinetics.direction) != "forward":
        return "tunneling_orientation_not_declared"
    if tunneling.transition_state_entry.transition_state.reaction_entry_id != kinetics.reaction_entry_id:
        return "tunneling_transition_state_on_other_reaction_entry"
    if _token(tunneling.energy_zero_convention) not in _SEPARATED_SPECIES_ZEROS:
        return "energy_zero_convention_not_separated_species"
    correction = _token(tunneling.energy_correction_convention)
    if correction is None:
        return "energy_correction_convention_not_declared"
    if correction == "electronic_only":
        return "energy_correction_convention_without_thermo_counterpart"
    if correction != "thermal_enthalpy_298k" and correction not in _ZERO_KELVIN_CONVENTIONS:
        return "energy_convention_other"
    calculation = tunneling.source_calculation
    if calculation is None or calculation.lot is None:
        return "reaction_energy_source_untraceable"
    if calculation.lot.solvent is not None or calculation.lot.solvent_model is not None:
        return "reaction_energy_solvated_out_of_scope"
    if not _finite(tunneling.reactant_energy_kj_mol, tunneling.product_energy_kj_mol):
        return "nonfinite_stored_value"
    # On the separated-reactants scale the reactant end IS the zero. Exact
    # comparison, no tolerance: the upload stores the depositor's number as
    # given, and a stated zero is 0.0. Anything else is some other endpoint
    # (a pre-reactive complex well, for instance) reported on that scale.
    if _token(tunneling.energy_zero_convention) == "separated_reactants" and tunneling.reactant_energy_kj_mol != 0:
        return "reaction_energy_not_separated_species"
    return None


def _available_298(thermo):
    names = []
    if thermo.h298_kj_mol is not None:
        names.append("h298")
    names.extend(name for name in engine.fit_names(thermo) if name in ENTHALPY_298_REPRESENTATIONS)
    # A point is an enthalpy option only if it carries an enthalpy; Cp/S-only
    # points would add a combination that can only ever be unavailable.
    if any(point.h_kj_mol is not None for point in thermo.points):
        names.append("point")
    return names


def _fits(payload):
    return len(encoded(payload)) <= _MESSAGE_LIMIT


def _bounded_findings(kinetics, payload, refs, *, combination, base, droppable=()):
    """Findings for one payload, each inside the message bound.

    Evaluated terms that do not fit move, in thermo-ref order, to numbered
    continuation findings (``terms_part`` 0..n-1, ``term_findings`` = n on
    the head). Detail lists on a reason finding are dropped, and the drop
    stated, before anything is allowed to fail validation.
    """
    if _fits(payload):
        return [finding(kinetics, payload, refs)]
    head = {k: v for k, v in payload.items() if k not in ("terms", *droppable)}
    head["combination"] = combination
    if any(k in payload for k in droppable):
        head["detail_omitted"] = FINDING_TOO_LARGE
    if "terms" not in payload:
        return [finding(kinetics, head, refs)]
    parts, part = [], {}

    def part_payload(index, terms):
        return {**base, "combination": combination, "terms_part": index, "term_columns": TERM_COLUMNS,
                "terms": terms}

    for ref, row in payload["terms"].items():
        if part and not _fits(part_payload(len(parts), {**part, ref: row})):
            parts.append(part)
            part = {}
        part[ref] = row
    parts.append(part)
    head["term_findings"] = len(parts)
    return [finding(kinetics, head, refs)] + [
        finding(kinetics, part_payload(index, terms), refs) for index, terms in enumerate(parts)]


#: The stored uncertainty that belongs to a term's value. Fits and points
#: carry none of their own; a record's h298 uncertainty is never lent to them.
_UNCERTAINTY_COLUMN = {"h298": "h298_uncertainty_kj_mol", "formation_0k": "enthalpy_formation_0k_uncertainty_kj_mol"}


def _uncertainty(thermo, representation):
    column = _UNCERTAINTY_COLUMN.get(representation)
    return getattr(thermo, column) if column else None


def compare_hess(kinetics, thermo_by_entry, *, representations=None):
    """Pure comparison. Mapping keys are resolved species-entry ids, never guessed.

    ``representations`` optionally pins one 298.15 K representation per
    species-entry id; it applies only to ``thermal_enthalpy_298k`` records.
    """
    representations = dict(representations or {})
    for key, name in representations.items():
        if name not in ENTHALPY_298_REPRESENTATIONS:
            raise ValueError(f"unknown enthalpy representation {name!r}; expected one of {ENTHALPY_298_REPRESENTATIONS}")
        if key not in thermo_by_entry:
            raise ValueError("a representation selection must name a mapped species entry")
    entry = kinetics.reaction_entry
    participants = list(entry.structure_participants)
    slots = participant_slots(participants)
    entries = slots.entries
    thermo_items = sorted(thermo_by_entry.items())
    tunnelings = list(kinetics.tunneling_applications)
    tunneling = tunnelings[0] if tunnelings else None
    refs = [kinetics.public_ref, entry.public_ref]
    if tunneling is not None:
        refs.append(tunneling.transition_state_entry.public_ref)
    base = {"element_reference_compilation": ELEMENT_REFERENCE_COMPILATION, "endpoint_identity": ENDPOINT_IDENTITY}

    calculation = tunneling.source_calculation if tunneling is not None else None
    reason = _reaction_side_reason(kinetics, tunneling)
    correction = _token(tunneling.energy_correction_convention) if tunneling is not None else None
    findings = [finding(kinetics, {
        **base, "input": kinetics.public_ref, "direction": _token(kinetics.direction),
        "tunneling_transition_state_entry": tunneling.transition_state_entry.public_ref if tunneling else None,
        "reactant_energy_kj_mol": tunneling.reactant_energy_kj_mol if tunneling else None,
        "product_energy_kj_mol": tunneling.product_energy_kj_mol if tunneling else None,
        "energy_zero_convention": _token(tunneling.energy_zero_convention) if tunneling else None,
        "energy_correction_convention": correction,
        "source_calculation": calculation.public_ref if calculation is not None else None,
        "level_of_theory": calculation.lot.public_ref if calculation is not None and calculation.lot else None,
        "reaction_energy_uncertainty": None, "uncertainty_propagation": None,
    }, [kinetics.public_ref])]
    for _, thermo in thermo_items:
        findings.append(finding(kinetics, {
            **base, "input": thermo.public_ref, "species_entry": thermo.species_entry.public_ref,
            "enthalpy_reference_kind": _token(thermo.enthalpy_reference_kind),
            "h298_uncertainty_kj_mol": thermo.h298_uncertainty_kj_mol,
            "enthalpy_formation_0k_uncertainty_kj_mol": thermo.enthalpy_formation_0k_uncertainty_kj_mol,
            "fit_uncertainty": None, "coverage_covariance_independence": "unspecified",
        }, [thermo.public_ref, thermo.species_entry.public_ref]))

    zero_kelvin = correction in _ZERO_KELVIN_CONVENTIONS
    extra = {}
    if reason is None and not slots.accounted:
        reason = "missing_or_unsupported_participants"
    if reason is None:
        missing = [e.public_ref for key, e in entries.items() if key not in thermo_by_entry]
        unexpected = [t.public_ref for key, t in thermo_items if key not in entries]
        mismatched = [t.public_ref for key, t in thermo_items if key in entries and t.species_entry_id != key]
        if missing or unexpected or mismatched:
            reason = "incomplete_or_incompatible_thermo_mapping"
            extra = {"missing_participants": missing, "unexpected_mappings": unexpected,
                     "mismatched_mappings": mismatched}
    if reason is None:
        reason = next((r for r in (species_scope_reason(e) for e in entries.values()) if r), None)
    if reason is None:
        compositions = {key: entry_facts(e).composition for key, e in entries.items()}
        charges = {key: e.species.charge for key, e in entries.items()}
        if not is_balanced(*element_balance(slots, compositions, charges)):
            reason = "unbalanced_stoichiometry"
    if reason is None:
        reason = next((r for r in (engine.gas_state_reason(t, quantity="h") for _, t in thermo_items) if r), None)
    if reason is None and zero_kelvin and representations:
        raise ValueError("a representation selection applies only to thermal_enthalpy_298k reaction energies")

    choices = [["formation_0k"] if zero_kelvin else [] for _ in thermo_items]
    if reason is None and zero_kelvin:
        absent = [t.public_ref for _, t in thermo_items if t.enthalpy_formation_0k_kj_mol is None]
        if absent:
            reason, extra = "participant_formation_0k_absent", {"absent_thermo": absent}
    elif reason is None:
        _, reason = shared_enthalpy_reference(t.enthalpy_reference_kind for _, t in thermo_items)
        unavailable = []
        for index, (key, thermo) in enumerate(thermo_items):
            available = _available_298(thermo)
            selected = representations.get(key)
            choices[index] = ([selected] if selected in available else []) if selected else available
            if not choices[index]:
                unavailable.append({"thermo": thermo.public_ref, "selected": selected, "available": available})
        if reason is None and unavailable:
            reason, extra = "participant_enthalpy_unavailable", {"unavailable_participants": unavailable}

    temperature = 0.0 if zero_kelvin else _T298_K
    evaluation = {**base, "quantity": "reaction_enthalpy_residual", "unit": "kJ/mol",
                  "energy_correction_convention": correction, "uncertainty_propagation": None}
    combinations = list(product(*choices)) if reason is None else []
    if len(combinations) > MAX_COMBINATIONS:
        raise ValueError(f"comparison exceeds {MAX_COMBINATIONS} explicit representation combinations")
    if reason is not None:
        findings.extend(_bounded_findings(
            kinetics, {**evaluation, **extra, "temperature_k": None, "reason": reason},
            refs + [t.public_ref for _, t in thermo_items], combination=None, base=base, droppable=tuple(extra)))
    for index, combination in enumerate(combinations):
        error, terms = None, []
        for (key, thermo), representation in zip(thermo_items, combination, strict=True):
            if representation == "formation_0k":
                value = thermo.enthalpy_formation_0k_kj_mol
            else:
                value, error = engine.evaluate(thermo, representation, temperature, "h")
            if error is None and not isfinite(value):
                error = "nonfinite_stored_value"
            if error is not None:
                break
            terms.append((thermo.public_ref, slots.coefficient(key), representation, value,
                          _uncertainty(thermo, representation)))
        payload = {**evaluation, "temperature_k": temperature, "reason": error}
        if error is None:
            delta_e = tunneling.product_energy_kj_mol - tunneling.reactant_energy_kj_mol
            delta_h = sum(term[1] * term[3] for term in terms)
            # Compact rows keep the message inside the finding's size bound.
            payload.update(terms={term[0]: list(term[1:]) for term in terms}, term_columns=TERM_COLUMNS,
                           delta_e_claim_kj_mol=delta_e, delta_h_formation_kj_mol=delta_h,
                           residual_kj_mol=delta_e - delta_h)
        fit_refs = [f"{t.public_ref}:{r}" for (_, t), r in zip(thermo_items, combination, strict=True)]
        findings.extend(_bounded_findings(kinetics, payload, refs + fit_refs, combination=index, base=base))

    inputs = {
        "kinetics": snapshot(kinetics, exclude=KINETICS_HASH_EXCLUDED_COLUMNS),
        "tunneling": snapshot(tunneling),
        "transition_state_entry": (snapshot(tunneling.transition_state_entry, ("transition_state",))
                                   if tunneling is not None else None),
        "source_calculation": snapshot(calculation, ("lot",)) if calculation is not None else None,
        "participants": sorted((snapshot(p, ("species_entry",)) for p in participants), key=encoded),
        "species": sorted((snapshot(e.species) for e in entries.values()), key=encoded),
        "thermo_mapping": [(key, thermo_inputs(t)) for key, t in thermo_items],
        "representation_selection": sorted(representations.items()),
        "engine": f"cantera/{engine.ENGINE_VERSION}", "units": "kJ/mol;K",
        "element_reference_compilation": ELEMENT_REFERENCE_COMPILATION,
    }
    return AdvisoryResult(kinetics, RUNNER, HESS_CONSISTENCY_V1, tuple(findings), encoded(inputs))
