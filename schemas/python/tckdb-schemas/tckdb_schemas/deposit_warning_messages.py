"""The one table of what a deposit warning says, in TCKDB's own words.

A warning's sentence is built from its ``code`` and its structured ``context``, and from nothing else. The same
:func:`render_message` is called at both ends, so a depositor and a later reader are told the same thing:

* a producer builds ``UploadWarning.message`` with it (:meth:`tckdb_schemas.upload_warning.UploadWarning.from_code`),
  so the upload response, the job result and the stored row carry the sentence;
* a public read renders a stored row with it.

The sentence is a fixed one per code (:data:`RENDERED`), or, for a code whose sentence uses the producer's values, a
sentence built from them (:data:`_WITH_CONTEXT`). The values are numbers, TCKDB tokens (an enum value, a parsed program
name, an element symbol) and counts. It never contains text the depositor typed: a transition-state label, a local
payload key, a conformer label, a file name, a free-text name or title. A label that helps the depositor find the
problem is put under ``context["depositor_supplied"]`` (a dict of named strings), which is depositor-typed text and
is marked as such; the sentence does not read it.

A stored row keeps the ``message`` and ``field`` it was deposited with (rows are append-only). They are shown, as
``depositor_message`` and ``depositor_field``, only to the deposit's owner and to curators and admins (the access rule
``GET /jobs/{id}`` uses). The public ``field`` is the stored one with every depositor key replaced
(``species['h'].thermo`` becomes ``species[*].thermo``); positional indexes (``kinetics[0]``) are kept, because they are
TCKDB's own ordinals.

:data:`RENDERED` has an entry for every warning code a producer can emit. ``tests/api/test_deposit_warning_reads.py``
sweeps the producers and fails when a code has none, and ``tests/api/test_warning_messages_from_codes.py`` fails when a
producer's ``message`` is not exactly ``render_message(code, context)``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: One fixed sentence per code. A code that needs the producer's numbers is in :data:`_WITH_CONTEXT` as well, and
#: this sentence is what it falls back to when the row predates its context.
RENDERED: dict[str, str] = {
    # -- stationary points and frequency lists ------------------------------------------------------------------
    "n_imag_contradicts_minimum": (
        "The frequency analysis reports an imaginary mode for a structure declared a minimum or a van der Waals "
        "complex. The record was accepted and flagged."
    ),
    "n_imag_higher_order_saddle": (
        "The frequency analysis reports more than one imaginary mode for a structure declared a van der Waals "
        "complex. The record was accepted and flagged."
    ),
    "n_imag_suggests_transition_state": (
        "The imaginary mode of a structure declared a van der Waals complex is too stiff to be intermolecular, "
        "which suggests a transition state. The record was accepted and flagged."
    ),
    "minimum_imaginary_modes_below_tau": (
        "Every imaginary mode of a structure declared a minimum is below the tolerance of the protocol that "
        "produced it. The record was accepted with a warning."
    ),
    "transition_state_extra_imaginary_modes_below_tau": (
        "The transition state has imaginary modes besides its reaction coordinate, all below the tolerance of the "
        "protocol that produced them. The record was accepted with a warning."
    ),
    "transition_state_extra_imaginary_mode_above_tau": (
        "The transition state has an imaginary mode at or above the protocol's tolerance besides its reaction "
        "coordinate, so it is a higher-order saddle point. The record was accepted and flagged."
    ),
    "transition_state_extra_imaginary_modes_not_assessable": (
        "The transition state has imaginary modes besides its reaction coordinate that cannot be assessed against "
        "a tolerance from what was deposited. The record was accepted with a warning."
    ),
    "transition_state_imaginary_frequency_too_small": (
        "The reaction coordinate of the transition state is softer than the usual lower bound for one, which can "
        "mean an under-converged geometry or a coarse integration grid."
    ),
    "hessian_method_disagrees_with_stored_tau_basis": (
        "An input deck attached after the deposit states a different Hessian method from the one the tolerance "
        "stored with this frequency calculation rests on. The stored tolerance was not changed."
    ),
    "freq_list_incomplete_for_geometry": (
        "The frequency list carries fewer modes than the geometry it is attached to has vibrations."
    ),
    "freq_list_linear_mode_count_for_bent_geometry": (
        "The frequency list has the mode count of a linear molecule, but its geometry is not linear."
    ),
    "freq_list_bent_mode_count_for_linear_geometry": (
        "The frequency list has the mode count of a bent molecule, but its geometry is linear."
    ),
    "freq_parser_extracted_but_modes_missing": (
        "A frequency calculation was read from its output but no per-mode frequencies were stored with it."
    ),
    # -- transition-state evidence and atom maps ----------------------------------------------------------------
    "transition_state_missing_irc_evidence": (
        "The transition state was deposited without passed intrinsic-reaction-coordinate evidence, so nothing in "
        "the deposit shows that it connects the declared reactants and products."
    ),
    "transition_state_energy_ordering_mixed_levels": (
        "The energies of a transition-state energy ordering are not all taken at one level of theory per energy kind."
    ),
    "transition_state_energy_ordering_not_compared": (
        "An energy stated in a transition-state energy ordering could not be compared with the energy TCKDB stores "
        "for the calculation it cites."
    ),
    "reaction_atom_map_absent": (
        "The reaction has a transition state but was deposited without an atom map saying which atom of the "
        "reactants is which atom of the saddle point and of the products."
    ),
    "reaction_atom_map_participants_incomplete": (
        "The atom map does not cover every declared participant molecule."
    ),
    "reaction_atom_map_atoms_incomplete": (
        "The atom map leaves atoms unmapped on a mapped participant or on the saddle point."
    ),
    # -- identity-adjacent --------------------------------------------------------------------------------------
    "species_entry_stereo_unresolved": (
        "A stereo element of the species entry is still unknown after the deposit."
    ),
    "species_entry_stereo_not_represented": (
        "The species entry has a stereo element that the stereo vocabulary cannot yet state."
    ),
    "term_symbol_mismatch": "The declared term symbol disagrees with the one deduced from the deposited evidence.",
    "term_symbol_contradicts_multiplicity": "The declared term symbol contradicts the declared spin multiplicity.",
    "term_symbol_contradicts_ground_term": (
        "The declared term symbol contradicts the ground term expected for this atom."
    ),
    "electronic_state_contradicts_method": (
        "The declared electronic state disagrees with what the calculation's method indicates."
    ),
    "atomic_electronic_degeneracy_contradicts_term": (
        "The degeneracy of the lowest deposited electronic level of this atom contradicts its term symbol."
    ),
    "missing_atomic_electronic_levels": "A single atom was deposited without its electronic levels.",
    "missing_atomic_spin_orbit_correction": "A single atom was deposited without a spin-orbit correction.",
    "reaction_reversible_twin": (
        "The same participants are also stored with the opposite reversibility, as a separate reaction."
    ),
    "reaction_family_not_applied": (
        "The reaction family named in the deposit was not applied, because this record is anchored to an existing "
        "reaction entry whose family is set on the shared reaction identity."
    ),
    # -- charge, multiplicity and logs --------------------------------------------------------------------------
    "charge_mismatch": (
        "The declared charge disagrees with the charge stated by the uploaded output log. The declared value was kept."
    ),
    "multiplicity_mismatch": (
        "The declared spin multiplicity disagrees with the multiplicity stated by the uploaded output log. The "
        "declared value was kept."
    ),
    "sp_energy_payload_log_mismatch": (
        "The single-point energy deposited disagrees with the value re-derived from the uploaded output log. The "
        "deposited value was kept."
    ),
    "sp_energy_filled_from_log": (
        "No single-point energy was deposited; the value re-derived from the uploaded output log was stored."
    ),
    "software_release_identity_corrected_from_artifact": (
        "The declared software name disagreed with the program identified in the uploaded artifact, so the "
        "calculation now points at the program the artifact names."
    ),
    "software_release_version_filled_from_artifact": (
        "The software version the deposit lacked was filled in from the uploaded artifact."
    ),
    "hessian_not_stored_geometry_mismatch": (
        "A Hessian was found in an uploaded artifact but not stored: its geometry does not match the calculation's."
    ),
    "hessian_not_stored_no_unique_input_geometry": (
        "A Hessian was found in an uploaded artifact but not stored: the calculation has no single input geometry "
        "to bind it to."
    ),
    "hessian_not_stored_unparseable": (
        "A Hessian was found in an uploaded artifact but not stored: it could not be read."
    ),
    "hessian_not_stored_failed": "A Hessian was found in an uploaded artifact but storing it failed.",
    "hessian_not_stored_not_frequency_calculation": (
        "A Hessian was found in an uploaded artifact but not stored: the calculation is not a frequency calculation."
    ),
    # -- calculations, composites and links ---------------------------------------------------------------------
    "converged_opt_no_usable_energy": (
        "The optimisation is recorded as converged, but no usable energy backs it: there is no same-owner single "
        "point and its own energy is not stated with supporting evidence."
    ),
    "dependency_edge_not_inferred": (
        "The calculation was stored but not linked to the primary calculation, because the link its type implies "
        "is not allowed for the primary calculation's type."
    ),
    "calculation_conformer_anchor_unresolved": (
        "The calculation named neither a conformer nor a geometry, so it is tied to no conformer basin."
    ),
    "scf_stability_source_conformer_unverified": (
        "Whether the stability verdict's source calculation and its carrying calculation are on the same conformer "
        "cannot be checked, because neither is tied to a conformer."
    ),
    "scf_stability_source_level_mismatch": (
        "The job that measured a stability verdict ran at a different level of theory than the calculation that "
        "carries the verdict."
    ),
    "named_composite_deposited_as_opt": (
        "An optimisation was deposited at the level of a named composite method, which is one program run and not "
        "an optimisation. It was stored as sent."
    ),
    "named_composite_deposited_as_sp": (
        "A single point was deposited at the level of a named composite method, which is one program run and not a "
        "single point. It was stored as sent."
    ),
    "composite_role_on_non_composite_calculation": (
        "A composite role was given to a calculation that is not a composite calculation."
    ),
    "composite_frequency_level_differs_from_recipe": (
        "The frequency calculation linked to a composite is at a different level of theory than its recipe prescribes."
    ),
    "composite_input_geometry_undeclared": (
        "An input of the composite declares no geometry, so TCKDB cannot check that every input was computed at "
        "one geometry."
    ),
    "composite_total_unverifiable": "The deposited composite total could not be checked against its inputs.",
    "composite_log_method_mismatch": (
        "The method named in the attached output log differs from the composite method of the calculation."
    ),
    "composite_energy_log_available": (
        "The attached output log carries a composite energy summary that the deposit did not state."
    ),
    "composite_energy_log_mismatch": (
        "The composite energy deposited disagrees with the composite energy in the attached output log."
    ),
    # -- statmech, thermo, kinetics, corrections ----------------------------------------------------------------
    "statmech_frequency_scale_factor_software_mismatch": (
        "The frequency scale factor was derived for a different program than the one that ran the frequency "
        "calculation it is applied to."
    ),
    "missing_statmech_source_calculations": (
        "A computed statmech record was deposited without source calculations."
    ),
    "missing_statmech_frequency_source": (
        "A computed statmech record was deposited without a frequency source calculation."
    ),
    "missing_kinetics_interpretation_assignments": (
        "A computed rate was deposited without the statmech interpretation assignments it was computed under."
    ),
    "missing_kinetics_transition_state_interpretation": (
        "A computed rate was deposited without the interpretation of its transition state."
    ),
    "missing_tunneling_application_evidence": (
        "A computed rate was deposited without evidence of the tunneling correction applied to it."
    ),
    "energy_level_stored_without_source_calculations": (
        "The declared energy level of theory was stored, but no source calculations were linked to the record."
    ),
    "composite_delta_prefer_scheme_terms": (
        "The correction was applied with the role of a composite delta. Such deltas are expected to become terms "
        "of a composite scheme. It was stored as sent."
    ),
    "missing_energy_correction_scheme_software": (
        "An energy correction scheme was deposited without the software release that produced it."
    ),
    "ambiguous_energy_correction_scheme_without_literature": (
        "An energy correction scheme that could refer to more than one parameter set was deposited without a "
        "literature citation to tell them apart."
    ),
    "level_of_theory_method_names_correction_table": (
        "The method of a level of theory ends in a correction-table label. It was stored as sent."
    ),
    "software_release_version_is_composite": (
        "A software version carried the program name with it and was split into a name and a version."
    ),
    "software_release_name_looks_wrong": (
        "The software name and the program named in the software version disagree. Both were left as sent."
    ),
    # -- provenance gaps ----------------------------------------------------------------------------------------
    "missing_literature_provenance": "No literature provenance was supplied.",
    "missing_software_release_provenance": "No software release was supplied.",
    "missing_workflow_tool_provenance": "No workflow tool release was supplied.",
    "missing_level_of_theory_provenance": "No level-of-theory provenance was supplied.",
    "missing_frequency_scale_factor_provenance": "No frequency scale factor provenance was supplied.",
    "literature_title_mismatch": (
        "The title supplied for the cited literature differs from the one fetched for its identifier. The fetched "
        "title was stored."
    ),
    "literature_year_mismatch": (
        "The year supplied for the cited literature differs from the one fetched for its identifier. The fetched "
        "year was stored."
    ),
    # -- networks -----------------------------------------------------------------------------------------------
    "network_state_energy_sum_not_compared": (
        "A network state energy that cites source calculations could not be compared with the sum of the energies "
        "TCKDB stores for them."
    ),
    "network_state_energy_sources_partial": (
        "The source calculations of a network state energy cover only some of the state's participants."
    ),
    "network_energy_zpe_source_absent": (
        "A composed network energy cites an electronic energy but the deposit has no frequency calculation to "
        "supply its zero-point energy."
    ),
    "reported_network_solve": (
        "The pressure-dependent rates were transcribed from a publication and not solved from a master equation here."
    ),
    "network_wide_energy_transfer_scope": (
        "The collisional energy-transfer model was declared once for the whole network, not per well and collider."
    ),
}


def _signed(value: Any) -> str | None:
    return f"{value:+d}" if isinstance(value, int) and not isinstance(value, bool) else None


def _hartree(value: Any) -> str | None:
    return f"{value:.8f} Ha" if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _hartree6(value: Any) -> str | None:
    return f"{value:.6f} Ha" if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _cm1(value: Any) -> str | None:
    return f"{value:.1f} cm-1" if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _tau(context: Mapping[str, Any]) -> str | None:
    value = context.get("tau_cm1")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:.0f} cm-1"
    return None


#: Why part of an entry's stereochemistry cannot be stated yet, by the reason token the stereo descriptor reports
#: (``R_*`` in ``app.chemistry.stereo``).
STEREO_NOT_REPRESENTED_REASONS: dict[str, str] = {
    "axial_small_ring": (
        "a cyclic allene in a ring of eight or fewer atoms is not represented, whatever the geometry: both "
        "mirror-image forms are stored in one entry"
    ),
    "cumulene_tied_end": (
        "a cumulated chain has a pair of stereo-bearing arms at one end (for example two allenyl or two carbinol "
        "groups), so its configuration depends on the senses of those arms, which is not represented: its "
        "configurations are stored in one entry"
    ),
    "allene_dependent": (
        "a stereocentre or double bond whose stereogenicity depends on the sense of an allene is not "
        "represented: its configurations are stored in one entry"
    ),
    "axial_marks": (
        "the only stereo information is a SMILES mark on an allene, and allene marks are not read: supply a "
        "geometry or a stereo_label (Ra or Sa) to settle the axis"
    ),
    "cumulene_marks": (
        "the only stereo information is the / and \\ marks on a cumulene, and those marks are not read: supply a "
        "geometry or a stereo_label (E or Z) to settle the cis/trans form"
    ),
    "symmetry": (
        "this molecule is too symmetric for the stereo descriptor to tell its configurations apart: different "
        "configurations of the stereo elements inside its symmetry classes are stored in one entry"
    ),
}


def _stereo_not_represented(context: Mapping[str, Any]) -> str | None:
    reasons = context.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        return None
    texts = [STEREO_NOT_REPRESENTED_REASONS.get(r) if isinstance(r, str) else None for r in reasons]
    if any(t is None for t in texts):
        return None
    unknown = _int(context.get("unknown_elements"))
    also = (
        f" {unknown} other stereo element(s) are also unknown to the data (no geometry, mark or stereo_label "
        "settled them)."
        if unknown
        else ""
    )
    return (
        f"Part of this species entry's stereochemistry cannot be represented yet: {'; '.join(t for t in texts if t)}. "
        f"Real stereoisomers may therefore share this entry, and a later deposit of the other form can land in it.{also}"
    )


def _stereo_unresolved(context: Mapping[str, Any]) -> str | None:
    resolution = context.get("resolution")
    if resolution not in ("partial", "unresolved"):
        return None
    return (
        f"The stereochemistry of this species entry is {resolution}: at least one stereo element (R/S centre, E/Z "
        "double bond, Ra/Sa allene axis or E/Z cumulene) is unknown, because no geometry resolved it, the SMILES "
        "carries no readable mark for it and no stereo_label was supplied. The entry was stored as its own entry; a "
        "later deposit that specifies the configuration will not merge into it."
    )


def _version_is_composite(context: Mapping[str, Any]) -> str | None:
    if context.get("normalised") is False:
        return (
            "A software version embeds a revision label, but a revision was also supplied, so both fields were left "
            "as sent rather than choosing between them."
        )
    return None


def _program(context: Mapping[str, Any]) -> str:
    software = context.get("software")
    return f"{software} " if isinstance(software, str) and software else ""


#: A value a context may put in a sentence as a word: a TCKDB token (an enum value, a parsed program key, an element
#: symbol, a term symbol from the NIST table). Whatever does not look like one is left out, so a sentence can never
#: grow a space-bearing string.
_TOKEN = re.compile(r"[A-Za-z0-9_.+\-/^*]{1,40}")


def _token(value: Any, allowed: frozenset[str] | None = None) -> str | None:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        return None
    if allowed is not None and value not in allowed:
        return None
    return value


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


#: TCKDB's own Hessian-method tokens (``HessianMethod``): the only strings a context may put in a sentence.
_HESSIAN_METHODS = frozenset({"analytic", "finite_difference_gradient", "finite_difference_energy"})

#: The sides of a reaction a participant is on (``ReactionRole`` tokens the atom map uses).
_SIDES = frozenset({"reactant", "product"})

#: The energy kinds of a transition-state energy ordering.
_ENERGY_KINDS = frozenset({"electronic", "e0"})

#: Why a stated energy could not be held against a stored one, for a transition-state energy ordering and for a network
#: state energy. TCKDB's own stable tokens (``NOT_COMPARED_*`` in the services that emit them).
_NOT_COMPARED_REASONS = frozenset(
    {
        "stored_energy_not_stated",
        "zpe_not_stated",
        "no_electronic_energy_to_pair",
        "geometry_not_paired",
        "zpe_scaling_unstated",
        "no_source_stated",
        "sources_incomplete",
        "convention_not_summable",
        "energy_zero_not_comparable",
        "zpe_not_in_source",
        "no_second_state_on_the_same_zero",
        "stated_precision_unknown",
    }
)

#: What a composite total could not be checked for. TCKDB's tokens, with the clause each reads as.
COMPOSITE_TOTAL_UNVERIFIABLE_REASONS: dict[str, str] = {
    "input_energy_not_stated": "an input calculation has no stored energy",
    "component_not_stated": "an input does not state the energy component the scheme reads",
    "correlation_convention_undeterminable": (
        "an input's correlation component cannot be tied to its energy (the reference energy, the "
        "correlation or the energy is not stated, or they do not add up), so whether (T) is inside "
        "it is not known"
    ),
    "extrapolation_degenerate": "the extrapolation is degenerate for these energies",
    "no_total_deposited": "no electronic_energy_hartree was deposited to compare",
}

#: The quantities of a composite energy a log can disagree with.
_COMPOSITE_QUANTITIES = frozenset({"e0_hartree", "recipe_zpe_hartree", "electronic_energy_hartree"})


def _modes(context: Mapping[str, Any]) -> str | None:
    """``mode 2 at 31.0 cm-1 (torsion)`` for each extra imaginary mode of a transition state."""
    modes = context.get("extra_imaginary_modes")
    if not isinstance(modes, list) or not modes:
        return None
    parts = []
    for mode in modes:
        if not isinstance(mode, Mapping):
            return None
        size = _cm1(mode.get("magnitude_cm1"))
        if size is None:
            return None
        index = _int(mode.get("mode_index"))
        disposition = _token(mode.get("disposition")) or "undeclared"
        parts.append(f"{'a mode' if index is None else f'mode {index}'} at {size} ({disposition})")
    return ", ".join(parts)


def _participants(value: Any) -> str | None:
    """``reactant 2, product 1`` from ``[{"side": ..., "participant_index": ...}]``."""
    if not isinstance(value, list) or not value:
        return None
    parts = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        side, index = _token(item.get("side"), _SIDES), _int(item.get("participant_index"))
        if side is None or index is None:
            return None
        parts.append(f"{side} {index}")
    return ", ".join(parts)


def _term_symbol_contradicts_multiplicity(context: Mapping[str, Any]) -> str | None:
    stated, declared = _int(context.get("term_multiplicity")), _int(context.get("declared_multiplicity"))
    if stated is None or declared is None:
        return None
    return (
        f"The declared term symbol states spin multiplicity {stated}, but this species entry declares "
        f"multiplicity {declared}."
    )


def _term_symbol_contradicts_ground_term(context: Mapping[str, Any]) -> str | None:
    element, term = _token(context.get("element")), _token(context.get("ground_term"))
    j = context.get("ground_j")
    if element is None or term is None or not isinstance(j, (int, float)) or isinstance(j, bool):
        return None
    return (
        f"The declared term symbol is not the NIST ground level of neutral {element} ({term}, ground J={j:g}) "
        "for a ground-state entry."
    )


def _missing_atomic_electronic_levels(context: Mapping[str, Any]) -> str | None:
    element, term = _token(context.get("element")), _token(context.get("ground_term"))
    multiplicity = _int(context.get("multiplicity"))
    q_el, error = context.get("electronic_partition_function"), context.get("free_energy_error_kj_mol")
    if element is None or term is None or multiplicity is None:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (q_el, error)):
        return None
    return (
        f"{element} atom has a {term} ground term with fine structure but this statmech carries no electronic "
        f"levels, so its electronic partition function is only the spin degeneracy {multiplicity}. Its entropy is "
        f"low and its free energy too high by about {error:.2f} kJ/mol at 298.15 K (q_el = {q_el:.2f} against "
        f"{multiplicity})."
    )


def _missing_atomic_spin_orbit_correction(context: Mapping[str, Any]) -> str | None:
    element, term = _token(context.get("element")), _token(context.get("ground_term"))
    shift = context.get("spin_orbit_shift_kj_mol")
    if element is None or term is None or not isinstance(shift, (int, float)) or isinstance(shift, bool):
        return None
    return (
        f"{element} atom has a {term} ground term whose spin-orbit splitting lowers the ground level, but no "
        f"soc_total energy correction was supplied, so its energy is the spin-orbit-free value, which lies "
        f"{abs(shift):.2f} kJ/mol above the ground level."
    )


def _degeneracy(context: Mapping[str, Any]) -> str | None:
    element, term = _token(context.get("element")), _token(context.get("ground_term"))
    lowest, allowed = _int(context.get("lowest_degeneracy")), context.get("allowed_degeneracies")
    if element is None or term is None or lowest is None:
        return None
    if not isinstance(allowed, list) or not allowed or not all(_int(a) is not None for a in allowed):
        return None
    return (
        f"The lowest electronic level of this {element} atom has degeneracy {lowest}, but the {term} ground term "
        f"allows only {', '.join(str(a) for a in allowed)} for its lowest level."
    )


def _freq_list_incomplete(context: Mapping[str, Any]) -> str | None:
    n_modes, n_atoms, floor = _int(context.get("n_modes")), _int(context.get("n_atoms")), _int(context.get("minimum_modes"))
    if n_modes is None or n_atoms is None or floor is None:
        return None
    return (
        f"The frequency list carries {_count(n_modes, 'mode', 'modes')}, but a harmonic analysis of the "
        f"{n_atoms}-atom geometry it is attached to produces at least {floor}. The record was accepted and flagged."
    )


def _linear_count_for_bent(context: Mapping[str, Any]) -> str | None:
    n_modes, n_atoms, expected = _int(context.get("n_modes")), _int(context.get("n_atoms")), _int(context.get("bent_count"))
    if n_modes is None or n_atoms is None or expected is None:
        return None
    return (
        f"The frequency list carries {n_modes} modes, which is 3N-5 for this {n_atoms}-atom geometry (the count of a "
        f"linear molecule), but the geometry is not linear: a non-linear molecule of {n_atoms} atoms has "
        f"3N-6 = {expected} vibrations. The record was accepted and flagged."
    )


def _bent_count_for_linear(context: Mapping[str, Any]) -> str | None:
    n_modes, n_atoms, expected = _int(context.get("n_modes")), _int(context.get("n_atoms")), _int(context.get("linear_count"))
    if n_modes is None or n_atoms is None or expected is None:
        return None
    return (
        f"The frequency list carries {n_modes} modes, which is 3N-6 for this {n_atoms}-atom geometry (the count of a "
        f"bent molecule), but the geometry is linear: a linear molecule of {n_atoms} atoms has 3N-5 = {expected} "
        "vibrations, so one mode is missing, typically a degenerate bending pair reported once. The record was "
        "accepted and flagged."
    )


def _minimum_below_tau(context: Mapping[str, Any]) -> str | None:
    n_imag, tau, modes = _int(context.get("n_imag")), _tau(context), context.get("imaginary_modes_cm1")
    if n_imag is None or tau is None:
        return None
    if not isinstance(modes, list) or not all(isinstance(m, (int, float)) and not isinstance(m, bool) for m in modes):
        return None
    sizes = ", ".join(f"{m:.1f}" for m in modes)
    return (
        f"The frequency analysis of a structure declared a minimum reports {_count(n_imag, 'imaginary mode', 'imaginary modes')} "
        f"({sizes} cm-1), all below tau = {tau}, where the sign of the curvature is not determined at the protocol "
        "that produced them. The record was accepted with a warning."
    )


def _vdw_suggests_ts(context: Mapping[str, Any]) -> str | None:
    size, floor = _cm1(context.get("imaginary_frequency_cm1")), _cm1(context.get("ts_minimum_cm1"))
    if size is None or floor is None:
        return None
    return (
        f"The imaginary mode of a structure declared a van der Waals complex is {size}, at or above {floor}, "
        "which is too stiff to be intermolecular and suggests a transition state. The record was accepted and flagged."
    )


def _ts_extras_below_tau(context: Mapping[str, Any]) -> str | None:
    modes, tau = _modes(context), _tau(context)
    if modes is None or tau is None:
        return None
    return (
        f"The transition state has imaginary modes besides its reaction coordinate ({modes}), all below tau = {tau}, "
        "where the sign of the curvature is not determined at the protocol that produced them. The record was "
        "accepted with a warning."
    )


def _ts_extra_above_tau(context: Mapping[str, Any]) -> str | None:
    modes, tau = _modes(context), _tau(context)
    if modes is None or tau is None:
        return None
    return (
        f"The transition state has an imaginary mode at or above tau = {tau} besides its reaction coordinate "
        f"({modes}), so it is a higher-order saddle point. The record was accepted and flagged."
    )


def _ts_extras_not_assessable(context: Mapping[str, Any]) -> str | None:
    extras, tau = _int(context.get("extra_imaginary_modes")), _tau(context)
    if extras is None or tau is None:
        return None
    return (
        f"The transition state has {_count(extras, 'imaginary mode', 'imaginary modes')} besides its reaction "
        f"coordinate that cannot be assessed against tau = {tau} from what was deposited. The record was accepted "
        "with a warning."
    )


def _ts_coordinate_too_small(context: Mapping[str, Any]) -> str | None:
    size, floor = _cm1(context.get("reaction_coordinate_cm1")), _cm1(context.get("ts_minimum_cm1"))
    if size is None or floor is None:
        return None
    return (
        f"The reaction coordinate of the transition state is {size}, softer than the usual lower bound of {floor} for "
        "one, which can mean an under-converged geometry or a coarse integration grid."
    )


def _ordering_mixed_levels(context: Mapping[str, Any]) -> str | None:
    kind, levels = _token(context.get("energy_kind"), _ENERGY_KINDS), _int(context.get("n_levels"))
    if kind is None or levels is None:
        return None
    return (
        f"The '{kind}' energies of a transition-state energy ordering are taken at {levels} different levels of "
        "theory. An ordering is only meaningful between energies at one level."
    )


def _reasons(context: Mapping[str, Any]) -> list[str] | None:
    reasons = context.get("reasons")
    if not isinstance(reasons, list) or not reasons:
        return None
    tokens = [_token(r, _NOT_COMPARED_REASONS) for r in reasons]
    return None if any(t is None for t in tokens) else sorted({t for t in tokens if t})


_ZPE_SCALING_HINT = (
    " A 'zpe_scaling_unstated' energy cites a frequency ZPE but states no zpe_scale_factor: TCKDB stores the "
    "unscaled ZPE and never assumes the scale is one, so state the factor (to at least 4 decimals) and it is compared."
)
_PRECISION_HINT = (
    " A 'stated_precision_unknown' energy differs from the stored sum by more than printed precision but by no more "
    "than honest rounding of the stated number could explain: state energy_precision_kj_mol for it, and it is compared."
)


def _ordering_not_compared(context: Mapping[str, Any]) -> str | None:
    reasons = _reasons(context)
    if reasons is None:
        return None
    hint = _ZPE_SCALING_HINT if "zpe_scaling_unstated" in reasons else ""
    return (
        "Energies stated in a transition-state energy ordering could not be compared with the energies TCKDB stores "
        f"for the calculations they cite (reasons: {', '.join(reasons)}). They are stored as not compared, and the "
        f"ordering rests on the stated numbers for them.{hint}"
    )


def _state_sum_not_compared(context: Mapping[str, Any]) -> str | None:
    reasons = _reasons(context)
    if reasons is None:
        return None
    hint = (_PRECISION_HINT if "stated_precision_unknown" in reasons else "") + (
        _ZPE_SCALING_HINT if "zpe_scaling_unstated" in reasons else ""
    )
    return (
        "Network state energies could not be held against the sum of the energies stored for the calculations they "
        f"cite (reasons: {', '.join(reasons)}). They are stored as not compared, and each rests on the stated number "
        f"alone.{hint}"
    )


def _state_sources_partial(context: Mapping[str, Any]) -> str | None:
    total, cited = _int(context.get("participants")), _int(context.get("cited_participants"))
    if total is None or cited is None:
        return None
    return (
        f"The energy of a network state is a sum over {total} species but names a source calculation for {cited}. "
        "It is stored as given and reads back as partial_sources, and the sum is not compared. Cite every "
        "participant with source_calculation_keys."
    )


_ATOM_MAP_REMEDY = {
    "atom_map_field": " If you followed the intrinsic reaction coordinate you already have it: supply it as 'atom_map' (ADR 0011).",
    "pdep_bundle": (
        " The pressure-dependent network bundle cannot yet carry a map, so to record one for this micro reaction "
        "deposit it through the computed-reaction upload, which accepts 'atom_map' (ADR 0011)."
    ),
    "standalone_transition_state": (
        " To record one for this micro reaction, supply 'atom_map' together with 'geometry_key' (naming the "
        "saddle-point geometry) and a 'key' and 'geometry' on each reaction participant it maps (ADR 0011); the "
        "computed-reaction upload accepts the same map."
    ),
}


def _atom_map_absent(context: Mapping[str, Any]) -> str | None:
    path = context.get("deposit_path")
    remedy = _ATOM_MAP_REMEDY.get(path) if isinstance(path, str) else None
    return None if remedy is None else RENDERED["reaction_atom_map_absent"] + remedy


def _atom_map_participants(context: Mapping[str, Any]) -> str | None:
    unmapped = _participants(context.get("unmapped_participants"))
    if unmapped is None:
        return None
    return (
        f"The atom map does not cover every participant of this reaction: {unmapped} carry no mapping. The map is "
        "stored as given; the unmapped molecules' atoms cannot be followed across the reaction."
    )


def _atom_map_atoms(context: Mapping[str, Any]) -> str | None:
    gaps: list[str] = []
    partial = context.get("partial_participants")
    if isinstance(partial, list):
        for item in partial:
            if not isinstance(item, Mapping):
                return None
            where = _participants([item])
            mapped, total = _int(item.get("mapped_atoms")), _int(item.get("total_atoms"))
            if where is None or mapped is None or total is None:
                return None
            gaps.append(f"{where} maps {mapped} of its {total} atoms")
    unclaimed = context.get("unclaimed_transition_state_atoms")
    if isinstance(unclaimed, Mapping):
        for side in sorted(unclaimed):
            atoms = unclaimed[side]
            if _token(side, _SIDES) is None or not isinstance(atoms, list) or not all(_int(a) is not None for a in atoms):
                return None
            gaps.append(f"the {side} leg leaves transition-state atom(s) {atoms} unclaimed")
    if not gaps:
        return None
    return f"The atom map is partial: {'; '.join(gaps)}. A partial map says nothing about the atoms it omits."


def _composite_method_mismatch(context: Mapping[str, Any]) -> str | None:
    method = _token(context.get("log_method"))
    if method is None:
        return None
    return (
        f"The attached output log is a {method} run, which is not the composite method of this calculation. The "
        "method was kept as sent and the deposited energies were not compared with this log."
    )


def _composite_log_available(context: Mapping[str, Any]) -> str | None:
    method, e0, zpe = _token(context.get("log_method")), _hartree6(context.get("e0_hartree")), _hartree6(context.get("recipe_zpe_hartree"))
    if method is None or e0 is None or zpe is None:
        return None
    return (
        f"No composite energy was deposited, and the attached {method} output log states E0 = {e0} and a recipe ZPE "
        f"of {zpe}. Nothing was filled from the log; send these in composite_result if you want them recorded."
    )


def _composite_log_mismatch(context: Mapping[str, Any]) -> str | None:
    method, rows = _token(context.get("log_method")), context.get("disagreements")
    if method is None or not isinstance(rows, list) or not rows:
        return None
    parts = []
    for row in rows:
        if not isinstance(row, Mapping):
            return None
        quantity = _token(row.get("quantity"), _COMPOSITE_QUANTITIES)
        deposited, logged = _hartree6(row.get("deposited_hartree")), _hartree6(row.get("log_hartree"))
        if quantity is None or deposited is None or logged is None:
            return None
        parts.append(f"{quantity} {deposited} against the log's {logged}")
    return (
        f"The deposited composite energy disagrees with the output log's {method} summary block: {'; '.join(parts)}. "
        "The deposited values are kept unchanged and flagged for reviewer attention; nothing is filled from the log."
    )


def _composite_total_unverifiable(context: Mapping[str, Any]) -> str | None:
    reason = context.get("reason")
    if not isinstance(reason, str) or reason not in COMPOSITE_TOTAL_UNVERIFIABLE_REASONS:
        return None
    return (
        f"The deposited composite total could not be checked against its inputs: "
        f"{COMPOSITE_TOTAL_UNVERIFIABLE_REASONS[reason]}. It was stored as sent; TCKDB recomputes a total only to "
        "check it and never stores one."
    )


def _converged_opt(context: Mapping[str, Any]) -> str | None:
    energy, artifact = context.get("has_energy"), context.get("has_artifact")
    if not isinstance(energy, bool) or not isinstance(artifact, bool):
        return None
    if not energy:
        missing = (
            "reports no final energy of its own, and no artifact backs one either"
            if not artifact
            else "reports no final energy of its own"
        )
    else:
        missing = "reports a final energy but no artifact backs it"
    return (
        "The optimisation is recorded as converged, but no usable energy backs it: there is no same-owner single "
        f"point and the optimisation {missing}. Deposit a single point for this species or transition state, or "
        "supply the optimisation's own final energy together with an artifact backing it."
    )


def _edge_not_inferred(context: Mapping[str, Any]) -> str | None:
    role, expected, primary = (_token(context.get(k)) for k in ("dependency_role", "expected_parent_type", "primary_type"))
    if role is None or expected is None or primary is None:
        return None
    return (
        f"The calculation was stored but not linked to the primary calculation: role '{role}' needs a parent of type "
        f"'{expected}' and the primary calculation is type '{primary}'."
    )


def _software_corrected(context: Mapping[str, Any]) -> str | None:
    observed = _token(context.get("observed_software"))
    if observed is None:
        return None
    return (
        f"The declared software name disagreed with the program identified in the uploaded artifact ({observed}), "
        "so the calculation now points at the program the artifact names. The declared value is preserved on "
        "declared_software_banner."
    )


def _literature_year(context: Mapping[str, Any]) -> str | None:
    supplied, fetched = _int(context.get("supplied_year")), _int(context.get("fetched_year"))
    if supplied is None or fetched is None:
        return None
    return (
        f"The year supplied for the cited literature ({supplied}) differs from the one fetched for its identifier "
        f"({fetched}). The fetched year was stored."
    )


def _reversible_twin(context: Mapping[str, Any]) -> str | None:
    reversible = context.get("reversible")
    if not isinstance(reversible, bool):
        return None
    this, other = ("reversible", "irreversible") if reversible else ("irreversible", "reversible")
    return (
        f"This reaction is stored as {this}, and the same participants are also stored as {other}, as a separate "
        "reaction. Reversibility is part of a reaction's identity, so rates deposited against one are not found "
        "under the other."
    )


def _level_without_sources(context: Mapping[str, Any]) -> str | None:
    reason = context.get("reason")
    if reason == "none":
        why = "no single calculation at that level exists for a participant on this instance"
    elif reason == "ambiguous":
        why = "more than one calculation at that level exists for a participant, so none was chosen"
    else:
        return None
    return f"The declared energy level of theory was stored, but no source calculations were linked: {why}."


def _tunneling(context: Mapping[str, Any]) -> str | None:
    model = _token(context.get("tunneling_model"), frozenset({"wigner", "eckart", "sct", "other"}))
    if model is None:
        return None
    return (
        f"A computed rate was deposited with tunneling_model '{model}' but without typed evidence of the tunneling "
        "correction applied to it, so the correction is recorded as a reported attribute and cannot be replayed."
    )


def _zpe_source_absent(context: Mapping[str, Any]) -> str | None:
    convention = _token(context.get("correction_convention"))
    subject = context.get("subject")
    if convention is None or subject not in ("state_energy", "channel_barrier"):
        return None
    what = "network state energy" if subject == "state_energy" else "channel barrier"
    return (
        f"A {what} states '{convention}', which includes a vibrational term, but its source is a bare "
        "electronic-energy calculation and the upload carries no frequency calculation for it. It is stored as "
        "stated, with no source for its zero-point energy."
    )


def _charge(context: Mapping[str, Any]) -> str | None:
    declared, logged = _signed(context.get("declared")), _signed(context.get("logged"))
    if declared and logged:
        return (
            f"The declared charge ({declared}) disagrees with the charge stated by the uploaded "
            f"{_program(context)}output log ({logged}). The declared value was kept."
        )
    return None


def _hessian_disagrees(context: Mapping[str, Any]) -> str | None:
    stored, deck = context.get("stored_method"), context.get("deck_method")
    if stored in _HESSIAN_METHODS and deck in _HESSIAN_METHODS:
        return (
            f"The tolerance stored with this frequency calculation rests on the Hessian method {stored}, but an "
            f"input deck attached after the deposit states {deck}. The stored tolerance was not changed."
        )
    return None


def _multiplicity(context: Mapping[str, Any]) -> str | None:
    declared, logged = context.get("declared"), context.get("logged")
    if isinstance(declared, int) and isinstance(logged, int):
        return (
            f"The declared spin multiplicity ({declared}) disagrees with the multiplicity stated by the "
            f"uploaded {_program(context)}output log ({logged}). The declared value was kept."
        )
    return None


def _sp_mismatch(context: Mapping[str, Any]) -> str | None:
    recorded, log = _hartree(context.get("recorded_hartree")), _hartree(context.get("log_hartree"))
    if recorded and log:
        return (
            f"The single-point energy deposited ({recorded}) disagrees with the value re-derived from the "
            f"uploaded output log ({log}). The deposited value was kept."
        )
    return None


def _sp_filled(context: Mapping[str, Any]) -> str | None:
    log = _hartree(context.get("log_hartree"))
    if log:
        return f"No single-point energy was deposited; {log}, re-derived from the uploaded output log, was stored."
    return None


#: The codes whose sentence uses the producer's values. A renderer returns ``None`` when the context cannot supply
#: them (a row that predates its context), and the fixed sentence in :data:`RENDERED` stands.
_WITH_CONTEXT: dict[str, Any] = {
    "charge_mismatch": _charge,
    "multiplicity_mismatch": _multiplicity,
    "sp_energy_payload_log_mismatch": _sp_mismatch,
    "sp_energy_filled_from_log": _sp_filled,
    "hessian_method_disagrees_with_stored_tau_basis": _hessian_disagrees,
    "n_imag_suggests_transition_state": _vdw_suggests_ts,
    "minimum_imaginary_modes_below_tau": _minimum_below_tau,
    "transition_state_extra_imaginary_modes_below_tau": _ts_extras_below_tau,
    "transition_state_extra_imaginary_mode_above_tau": _ts_extra_above_tau,
    "transition_state_extra_imaginary_modes_not_assessable": _ts_extras_not_assessable,
    "transition_state_imaginary_frequency_too_small": _ts_coordinate_too_small,
    "freq_list_incomplete_for_geometry": _freq_list_incomplete,
    "freq_list_linear_mode_count_for_bent_geometry": _linear_count_for_bent,
    "freq_list_bent_mode_count_for_linear_geometry": _bent_count_for_linear,
    "transition_state_energy_ordering_mixed_levels": _ordering_mixed_levels,
    "transition_state_energy_ordering_not_compared": _ordering_not_compared,
    "network_state_energy_sum_not_compared": _state_sum_not_compared,
    "network_state_energy_sources_partial": _state_sources_partial,
    "network_energy_zpe_source_absent": _zpe_source_absent,
    "reaction_atom_map_absent": _atom_map_absent,
    "reaction_atom_map_participants_incomplete": _atom_map_participants,
    "reaction_atom_map_atoms_incomplete": _atom_map_atoms,
    "term_symbol_contradicts_multiplicity": _term_symbol_contradicts_multiplicity,
    "term_symbol_contradicts_ground_term": _term_symbol_contradicts_ground_term,
    "missing_atomic_electronic_levels": _missing_atomic_electronic_levels,
    "missing_atomic_spin_orbit_correction": _missing_atomic_spin_orbit_correction,
    "atomic_electronic_degeneracy_contradicts_term": _degeneracy,
    "composite_log_method_mismatch": _composite_method_mismatch,
    "composite_energy_log_available": _composite_log_available,
    "composite_energy_log_mismatch": _composite_log_mismatch,
    "composite_total_unverifiable": _composite_total_unverifiable,
    "converged_opt_no_usable_energy": _converged_opt,
    "dependency_edge_not_inferred": _edge_not_inferred,
    "software_release_identity_corrected_from_artifact": _software_corrected,
    "literature_year_mismatch": _literature_year,
    "reaction_reversible_twin": _reversible_twin,
    "energy_level_stored_without_source_calculations": _level_without_sources,
    "missing_tunneling_application_evidence": _tunneling,
    "software_release_version_is_composite": _version_is_composite,
    "species_entry_stereo_not_represented": _stereo_not_represented,
    "species_entry_stereo_unresolved": _stereo_unresolved,
}


def _with_context(code: str, context: Mapping[str, Any]) -> str | None:
    """The sentence with the producer's numbers in it, or ``None`` when the stored context cannot supply them."""
    render = _WITH_CONTEXT.get(code)
    return None if render is None else render(context)


def render_message(code: str, context: Mapping[str, Any] | None) -> str:
    """TCKDB's sentence for a warning: from the code and the structured context, never a depositor string.

    The one function both ends call. A producer builds ``UploadWarning.message`` with it
    (:meth:`tckdb_schemas.upload_warning.UploadWarning.from_code`), and a read renders a stored row with it, so a
    depositor and a later reader are told the same thing.
    """
    sentence = _with_context(code, context) if context else None
    if sentence is not None:
        return sentence
    return RENDERED.get(code) or "The deposit tripped a warning that has no public description yet."


#: A depositor key inside a payload path: ``['h']``, ``['ts-opt']``. Positional ``[0]`` indexes are TCKDB's own.
_KEY = re.compile(r"\[(?:'[^']*'|\"[^\"]*\")\]")


def normalise_field(field: str) -> str:
    """The payload path with every depositor key replaced by ``[*]``."""
    return _KEY.sub("[*]", field)


__all__ = [
    "COMPOSITE_TOTAL_UNVERIFIABLE_REASONS",
    "RENDERED",
    "STEREO_NOT_REPRESENTED_REASONS",
    "normalise_field",
    "render_message",
]
