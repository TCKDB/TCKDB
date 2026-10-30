"""What a deposit warning says when it is read, in TCKDB's own words.

The text stored with a warning is what the producer said at deposit, and it often quotes the depositor: a transition
state's label, a local calculation key, a species key, a file name. Those are the depositor's words, not TCKDB's, and a
public read must not present them as TCKDB's (on the website, the API, the MCP server or an export). So a public
read never takes the stored text. It **renders** it from

* the warning's ``code`` (a fixed sentence below, with no interpolation of depositor strings), and
* the structured ``context`` where the producer stated values (numbers, parsed program names), and
* nothing else: no public ref, no label, no key.

The stored ``message`` and ``field`` stay exactly as they were (the rows are append-only and are the record of what was
said at deposit). They are shown, as ``depositor_message`` and ``depositor_field``, only to the deposit's owner and to
curators and admins (the access rule ``GET /jobs/{id}`` uses).

The public ``field`` is the stored one with every depositor key replaced (``species['h'].thermo`` becomes
``species[*].thermo``); positional indexes (``kinetics[0]``) are kept, because they are TCKDB's own ordinals.

:data:`RENDERED` has an entry for every warning code a producer can emit. ``tests/api/test_deposit_warning_reads.py``
sweeps the producers and fails when a code has none, so a new code cannot reach a reader as its stored text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: One fixed sentence per code. A code that needs the producer's numbers is in :func:`_with_context` as well, and
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


def _program(context: Mapping[str, Any]) -> str:
    software = context.get("software")
    return f"{software} " if isinstance(software, str) and software else ""


def _with_context(code: str, context: Mapping[str, Any]) -> str | None:
    """The sentence with the producer's numbers in it, or ``None`` when the stored context cannot supply them."""
    if code == "charge_mismatch":
        declared, logged = _signed(context.get("declared")), _signed(context.get("logged"))
        if declared and logged:
            return (
                f"The declared charge ({declared}) disagrees with the charge stated by the uploaded "
                f"{_program(context)}output log ({logged}). The declared value was kept."
            )
    if code == "multiplicity_mismatch":
        declared, logged = context.get("declared"), context.get("logged")
        if isinstance(declared, int) and isinstance(logged, int):
            return (
                f"The declared spin multiplicity ({declared}) disagrees with the multiplicity stated by the "
                f"uploaded {_program(context)}output log ({logged}). The declared value was kept."
            )
    if code == "sp_energy_payload_log_mismatch":
        recorded, log = _hartree(context.get("recorded_hartree")), _hartree(context.get("log_hartree"))
        if recorded and log:
            return (
                f"The single-point energy deposited ({recorded}) disagrees with the value re-derived from the "
                f"uploaded output log ({log}). The deposited value was kept."
            )
    if code == "sp_energy_filled_from_log":
        log = _hartree(context.get("log_hartree"))
        if log:
            return f"No single-point energy was deposited; {log}, re-derived from the uploaded output log, was stored."
    return None


def render_message(code: str, context: Mapping[str, Any] | None) -> str:
    """The public sentence for a stored warning. Never the stored text, never a depositor string."""
    sentence = _with_context(code, context) if context else None
    if sentence is not None:
        return sentence
    return RENDERED.get(code) or "The deposit tripped a warning that has no public description yet."


#: A depositor key inside a payload path: ``['h']``, ``['ts-opt']``. Positional ``[0]`` indexes are TCKDB's own.
_KEY = re.compile(r"\[(?:'[^']*'|\"[^\"]*\")\]")


def normalise_field(field: str) -> str:
    """The payload path with every depositor key replaced by ``[*]``."""
    return _KEY.sub("[*]", field)


__all__ = ["RENDERED", "normalise_field", "render_message"]
