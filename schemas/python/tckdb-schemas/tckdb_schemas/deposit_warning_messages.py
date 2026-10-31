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
        "The frequency analysis reports an imaginary mode for a structure declared a van der Waals complex, "
        "or the declared stationary point kind disagrees with the frequency analysis. A van der Waals complex "
        "is formally a minimum, so zero is expected; its intermolecular modes sit low enough that Hessian "
        "grid noise is comparable to the true curvature, so the record was accepted and flagged rather than "
        "refused. Re-run the frequency job on a tighter integration grid if the mode matters downstream."
    ),
    "n_imag_higher_order_saddle": (
        "The frequency analysis reports more than one imaginary mode for a structure declared a van der Waals "
        "complex. A van der Waals complex is formally a minimum, so zero is expected; its intermolecular "
        "modes sit low enough that Hessian grid noise is comparable to the true curvature, so the record was "
        "accepted and flagged rather than refused. Re-run the frequency job on a tighter integration grid if "
        "the mode matters downstream."
    ),
    "n_imag_suggests_transition_state": (
        "The imaginary mode of a structure declared a van der Waals complex is too stiff to be "
        "intermolecular, so it is unlikely to be Hessian grid noise and looks instead like a genuine reaction "
        "coordinate. The record was accepted and flagged. If this really is a saddle point, deposit it as a "
        "transition state."
    ),
    "minimum_imaginary_modes_below_tau": (
        "Every imaginary mode of a structure declared a minimum is below tau, where the sign of the curvature "
        "is not determined at the protocol that produced it. The record was accepted, with tau recorded "
        "alongside it. Remediation, in order: re-run the frequency job on a tighter integration grid (most "
        "modes between -10 and -40 cm-1 vanish); re-optimise more tightly and repeat; if they persist, "
        "displace along each extra imaginary mode by 0.1-0.3 A and re-optimise -- if the energy drops and the "
        "extra modes disappear, the original was a genuine higher-order saddle. If the mode is a torsion, "
        "scan it and treat it as a hindered rotor. Passing IRC evidence makes a soft extra mode moot and is "
        "the strongest single thing you can supply."
    ),
    "transition_state_extra_imaginary_modes_below_tau": (
        "The transition state has imaginary modes besides its reaction coordinate, all below tau, where the "
        "sign of the curvature is not determined at the protocol that produced them. The record was accepted "
        "without a structural flag. Remediation, in order: re-run the frequency job on a tighter integration "
        "grid (most modes between -10 and -40 cm-1 vanish); re-optimise more tightly and repeat; if they "
        "persist, displace along each extra imaginary mode by 0.1-0.3 A and re-optimise -- if the energy "
        "drops and the extra modes disappear, the original was a genuine higher-order saddle. If the mode is "
        "a torsion, scan it and treat it as a hindered rotor. Passing IRC evidence makes a soft extra mode "
        "moot and is the strongest single thing you can supply."
    ),
    "transition_state_extra_imaginary_mode_above_tau": (
        "The transition state has an imaginary mode at or above tau besides its reaction coordinate, so its "
        "negative curvature is real rather than numerical noise: it is a genuine higher-order saddle. It was "
        "accepted because that can be correct chemistry -- a transition state may sit at a torsional maximum "
        "and still be the right reactive bottleneck -- and flagged because a consumer treating it as a plain "
        "first-order saddle would be wrong. Remediation, in order: re-run the frequency job on a tighter "
        "integration grid (most modes between -10 and -40 cm-1 vanish); re-optimise more tightly and repeat; "
        "if they persist, displace along each extra imaginary mode by 0.1-0.3 A and re-optimise -- if the "
        "energy drops and the extra modes disappear, the original was a genuine higher-order saddle. If the "
        "mode is a torsion, scan it and treat it as a hindered rotor. Passing IRC evidence makes a soft extra "
        "mode moot and is the strongest single thing you can supply."
    ),
    "transition_state_extra_imaginary_modes_not_assessable": (
        "A reaction coordinate is designated for the transition state and other imaginary modes are reported, "
        "but no frequency list was deposited, so they cannot be judged against tau. The record was accepted "
        "and flagged. Deposit the complete signed, unrounded frequency list so the extra modes can be "
        "assessed."
    ),
    "transition_state_imaginary_frequency_too_small": (
        "The reaction coordinate of the transition state is softer than the usual lower bound for one. A "
        "reaction coordinate this soft is often an under-converged geometry or a coarse integration grid. It "
        "can also be real -- flat and variational barriers genuinely produce one -- which is why this is a "
        "quality expectation and not a refusal."
    ),
    "hessian_method_disagrees_with_stored_tau_basis": (
        "An input deck attached after the deposit states a different Hessian method from the one the tolerance "
        "stored with this frequency calculation rests on. The stored tolerance was not changed."
    ),
    "freq_list_incomplete_for_geometry": (
        "The frequency list carries fewer modes than the geometry it is attached to has vibrations. The "
        "complete signed unrounded list is required, never filtered: a partial list agrees with an "
        "independently recovered spectrum on every mode it contains and is silent about the ones it omits, "
        "which reads as agreement. The record was accepted and flagged. Deposit every mode the frequency job "
        "printed, including the reaction coordinate and any mode removed from the partition function; if the "
        "job genuinely computed a partial Hessian, say so in the calculation's parameters so the short list "
        "is explicable."
    ),
    "freq_list_linear_mode_count_for_bent_geometry": (
        "The frequency list has the mode count of a linear molecule (3N-5), but its geometry is not linear, "
        "so one mode is either spurious or a rigid-body translation/rotation left in the list. The record was "
        "accepted and flagged, not refused: the boundary between linear and bent is a tolerance rather than a "
        "definition, and near-linear geometries are deliberately left unreported. Deposit every mode the job "
        "printed, or the geometry the job actually ran on."
    ),
    "freq_list_bent_mode_count_for_linear_geometry": (
        "The frequency list has the mode count of a bent molecule (3N-6), but its geometry is linear, so one "
        "mode is missing. The usual cause is a degenerate bending pair reported once: a parser or script that "
        "de-duplicates equal frequencies drops one component. A genuinely partial or frozen-atom Hessian "
        "would also produce a short list. The record was accepted and flagged, not refused: the boundary "
        "between linear and bent is a tolerance rather than a definition. Deposit every mode the job printed, "
        "including both components of each degenerate pair."
    ),
    "freq_parser_extracted_but_modes_missing": (
        "A frequency result was emitted by an ESS parser but carries no per-mode frequencies. Per-mode rows "
        "are required for downstream statmech recomputation: re-run the parser or attach the mode list before "
        "promoting the record."
    ),
    # -- transition-state evidence and atom maps ----------------------------------------------------------------
    "transition_state_missing_irc_evidence": (
        "The transition state was deposited without passed intrinsic-reaction-coordinate evidence. The saddle "
        "point is stored, but nothing in the deposit shows that it connects the declared reactants and "
        "products. Energy-ordering and imaginary-mode evidence say something else, and do not stand in for "
        "it."
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
        "reactants is which atom of the saddle point and of the products. The rate constant and the saddle "
        "point are stored, but the bonds that break and form cannot be read back, reaction-path degeneracy "
        "cannot be derived, a kinetic isotope effect cannot be followed atom by atom, and the reaction cannot "
        "be exported in the atom-mapped form reaction machine learning consumes. TCKDB will not infer one: "
        "several chemically distinct maps are usually consistent with the same reactants and products, and "
        "choosing one by algorithm would manufacture provenance."
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
        "The declared charge disagrees with the charge stated by the uploaded output log. The declared value "
        "was kept and flagged for reviewer attention."
    ),
    "multiplicity_mismatch": (
        "The declared spin multiplicity disagrees with the multiplicity stated by the uploaded output log. "
        "The declared value was kept and flagged for reviewer attention."
    ),
    "sp_energy_payload_log_mismatch": (
        "The single-point energy deposited disagrees with the value re-derived from the uploaded output log. "
        "The deposited value was kept and flagged for reviewer attention."
    ),
    "sp_energy_filled_from_log": (
        "No single-point energy was deposited; the value re-derived from the uploaded output log was stored."
    ),
    "software_release_identity_corrected_from_artifact": (
        "The declared software name disagreed with the program identified in the uploaded artifact, so the "
        "calculation now points at the program the artifact names."
    ),
    "software_release_version_filled_from_artifact": (
        "The software version the deposit lacked was filled in from the uploaded artifact: the calculation "
        "now cites the release the artifact states, and the banner is kept on observed_software_banner."
    ),
    "hessian_not_stored_geometry_mismatch": (
        "A Hessian was found in an uploaded artifact but not stored: its geometry does not match the "
        "calculation's. The artifact itself is stored; only the Hessian is not."
    ),
    "hessian_not_stored_no_unique_input_geometry": (
        "A Hessian was found in an uploaded artifact but not stored: the calculation has no single input "
        "geometry to bind it to. The artifact itself is stored; only the Hessian is not."
    ),
    "hessian_not_stored_unparseable": (
        "A Hessian was found in an uploaded artifact but not stored: it could not be read. The artifact "
        "itself is stored; only the Hessian is not."
    ),
    "hessian_not_stored_failed": (
        "A Hessian was found in an uploaded artifact but storing it failed. The artifact itself is stored; "
        "only the Hessian is not."
    ),
    "hessian_not_stored_not_frequency_calculation": (
        "A Hessian was found in an uploaded artifact but not stored: a Hessian is stored only from a `freq` "
        "calculation, whose input geometry is the frame the matrix was computed at. The artifact itself is "
        "stored; only the Hessian is not."
    ),
    # -- calculations, composites and links ---------------------------------------------------------------------
    "converged_opt_no_usable_energy": (
        "The optimisation is recorded as converged, which is itself a claim about the energy, but no usable "
        "energy backs it: there is no same-owner single point (any level of theory would satisfy this) and "
        "its own energy is not stated with supporting evidence. Deposit a single-point calculation for this "
        "species or transition state, or supply the optimisation's own final energy together with an artifact "
        "backing it."
    ),
    "dependency_edge_not_inferred": (
        "The calculation was stored but not linked to the primary calculation, because the link its type implies "
        "is not allowed for the primary calculation's type."
    ),
    "calculation_conformer_anchor_unresolved": (
        "The calculation named neither a conformer_key nor a geometry_key, so it is tied to no conformer basin and "
        "basin-scoped reads will not count it. Set conformer_key to one of the conformer keys of this species to anchor it."
    ),
    "scf_stability_source_conformer_unverified": (
        "Whether the stability verdict's source calculation and its carrying calculation are on the same "
        "conformer cannot be checked, because neither is tied to a conformer (no conformer_key, and no "
        "geometry_key naming a conformer's geometry). The link is stored as stated; give both calculations a "
        "conformer_key to have it checked."
    ),
    "scf_stability_source_level_mismatch": (
        "The job that measured a stability verdict ran at a different level of theory than the calculation "
        "that carries the verdict. The verdict describes the measuring job's wavefunction, not the carrying "
        "calculation's."
    ),
    "named_composite_deposited_as_opt": (
        "An optimisation was deposited at the level of a named composite method, which is one program run and "
        "not an optimisation. It was stored as sent; send it as type `composite` with a composite_result "
        "block (assembly `program_run` and the energies the program printed) and, if the run's own "
        "optimisation or frequency job is also deposited, send those separately at their own levels. This "
        "will be refused once producers can send that shape."
    ),
    "named_composite_deposited_as_sp": (
        "A single point was deposited at the level of a named composite method, which is one program run and "
        "not a single point. It was stored as sent; send it as type `composite` with a composite_result block "
        "(assembly `program_run` and the energies the program printed) and, if the run's own optimisation or "
        "frequency job is also deposited, send those separately at their own levels. This will be refused "
        "once producers can send that shape."
    ),
    "composite_role_on_non_composite_calculation": (
        "A composite role was given to a calculation that is not a composite calculation. The role names a "
        "composite energy, which is recorded by a calculation of type `composite`. The link was stored as "
        "sent; it will be refused once producers can send that shape."
    ),
    "composite_frequency_level_differs_from_recipe": (
        "The frequency calculation linked to a composite is at a different level of theory than its recipe "
        "prescribes, and the composite method scales its zero-point energy for the recipe's level. Link the "
        "frequency calculation at the recipe's own level, or expect the record's frequencies and its energy "
        "to describe different levels."
    ),
    "composite_input_geometry_undeclared": (
        "An input of the composite declares no geometry, so TCKDB cannot check that every input of this "
        "composite was computed at one geometry. Declare the single point's input geometry (or the "
        "optimisation's final one) to make that checkable."
    ),
    "composite_total_unverifiable": "The deposited composite total could not be checked against its inputs.",
    "composite_log_method_mismatch": (
        "The method named in the attached output log differs from the composite method of the calculation. "
        "The method was kept as sent and the deposited energies were not compared with this log. Attach the "
        "log of the run that produced this calculation, or correct the level of theory."
    ),
    "composite_energy_log_available": (
        "The attached output log carries a composite energy summary that the deposit did not state. Nothing "
        "was filled from the log; send its values in composite_result (e0_hartree, recipe_zpe_hartree, and "
        "the ZPE-free electronic_energy_hartree) if you want them recorded."
    ),
    "composite_energy_log_mismatch": (
        "The composite energy deposited disagrees with the composite energy in the attached output log. The "
        "deposited values are kept unchanged and flagged for reviewer attention; nothing is filled from the "
        "log."
    ),
    # -- statmech, thermo, kinetics, corrections ----------------------------------------------------------------
    "statmech_frequency_scale_factor_software_mismatch": (
        "The frequency scale factor was derived for a different program than the one that ran the frequency "
        "calculation it is applied to. A harmonic scale factor is specific to the code it was fit against, so "
        "applying it across a different code is a deliberate choice, not an error: confirm this is "
        "intentional and documented, or attach a scale factor derived for the program that ran the "
        "frequencies."
    ),
    "missing_statmech_source_calculations": (
        "A computed statmech record was deposited without source calculations, so the partition-function "
        "interpretation cannot be traced back to the calculations it was derived from."
    ),
    "missing_statmech_frequency_source": (
        "A computed statmech record describes a species with rotational structure, so its partition function "
        "uses vibrational modes, but no source calculation with role `freq` was linked. The frequencies "
        "behind this record are untraceable."
    ),
    "missing_kinetics_interpretation_assignments": (
        "A computed rate was deposited without statmech interpretation assignments, so it does not record "
        "which partition functions in this database it was built from."
    ),
    "missing_kinetics_transition_state_interpretation": (
        "The interpretation set names reactant and product partition functions but no transition state, so "
        "the rate's activated complex is unaccounted for. This is expected for a rate fitted from a "
        "master-equation solve; otherwise supply the role `transition_state` assignment."
    ),
    "missing_tunneling_application_evidence": (
        "A computed rate declares a tunneling model but carries no typed tunneling-application evidence, so "
        "the correction is recorded as a reported attribute and cannot be replayed."
    ),
    "energy_level_stored_without_source_calculations": (
        "The declared energy level of theory was stored, but no source calculations were linked to the record."
    ),
    "composite_delta_prefer_scheme_terms": (
        "The correction was applied with the role of a composite delta. Focal-point deltas (core-valence, "
        "higher-order triples, relativistic, DBOC) will become composite-scheme terms, which can name every "
        "calculation a delta is built from and sit on the energy itself instead of on the species entry. It "
        "was stored as sent; prefer describing the recipe as a composite scheme once that shape is available "
        "to you."
    ),
    "missing_energy_correction_scheme_software": (
        "No software release was supplied for this energy correction scheme. Atom-energy and bond-additivity "
        "corrections are the output of a program's own build-level numerics, so a scheme with no program "
        "recorded at all cannot be distinguished from a different program's values at the same level of "
        "theory. (A program with no build stated -- e.g. just `Gaussian`, no version -- is a complete, honest "
        "deposit and does not trigger this warning; only a fully absent software release does.)"
    ),
    "ambiguous_energy_correction_scheme_without_literature": (
        "This energy correction scheme and another share a kind, level of theory, and recorded software, and "
        "neither carries a citation, so the archive cannot say whether they are the same correction deposited "
        "twice or two genuinely different sets of numbers."
    ),
    "level_of_theory_method_names_correction_table": (
        "The method of a level of theory is a method followed by a label that selects an energy-correction "
        "table (a correction-set name or a year), not a method. The calculation ran the method without the "
        "label. It was stored as sent, as a separate level of theory from the method alone. Send the method "
        "alone and name the table on the energy correction scheme instead."
    ),
    "software_release_version_is_composite": (
        "A software version was a parsed banner: it carried the program name, and possibly a revision label, "
        "with it. It was normalised to a version and, when present, a revision (the verbatim string is kept "
        "in the release notes)."
    ),
    "software_release_name_looks_wrong": (
        "The software name and the program named in the software version disagree. This looks like a stale or "
        "default name that rode along with a version observed from a different program; the version is the "
        "more likely observed fact. Both fields were left exactly as sent; verify which of name and version "
        "is correct before trusting this record's software identity."
    ),
    # -- provenance gaps ----------------------------------------------------------------------------------------
    "missing_literature_provenance": (
        "No literature provenance was supplied. A record that is not computed, and a new energy correction scheme, "
        "should carry a literature reference so the source of the data can be audited."
    ),
    "missing_software_release_provenance": (
        "No software release was supplied. A computed record should identify which electronic-structure or "
        "post-processing software produced it."
    ),
    "missing_workflow_tool_provenance": (
        "No workflow tool release was supplied. A computed record should identify the orchestration tool that "
        "produced it."
    ),
    "missing_level_of_theory_provenance": (
        "No level-of-theory provenance was supplied. Computed kinetics should declare the electronic-energy level of "
        "theory so source single-point calculations can be anchored to it."
    ),
    "missing_frequency_scale_factor_provenance": (
        "No frequency scale factor provenance was supplied. Computed statmech should record the scaling applied to "
        "harmonic frequencies; use value=1.0 for explicitly unscaled results."
    ),
    "literature_title_mismatch": (
        "The title supplied for the cited literature differs from the one fetched for its identifier. The "
        "fetched title was stored; verify the identifier is correct for this citation."
    ),
    "literature_year_mismatch": (
        "The year supplied for the cited literature differs from the one fetched for its identifier. The "
        "fetched year was stored; verify the identifier is correct for this citation."
    ),
    # -- networks -----------------------------------------------------------------------------------------------
    "network_state_energy_sum_not_compared": (
        "A network state energy that cites source calculations could not be compared with the sum of the "
        "energies TCKDB stores for them. It is stored as not compared with its reason, and rests on the "
        "stated number alone."
    ),
    "network_state_energy_sources_partial": (
        "The source calculations of a network state energy cover only some of the state's participants. It is "
        "stored as given and reads back as partial_sources, and the sum is not compared. Cite every "
        "participant with source_calculation_keys."
    ),
    "network_energy_zpe_source_absent": (
        "A composed network energy states a convention that includes a vibrational term, but its source is a "
        "bare electronic-energy calculation and the deposit has no frequency calculation to supply its "
        "zero-point energy. It is stored as stated, with no source for its zero-point energy. Cite the freq "
        "(zpe_calculation_key), include it in the upload, or cite a composite that holds the E0."
    ),
    "reported_network_solve": (
        "These k(T,P) were transcribed from a publication, not derived here. The master-equation inputs "
        "behind them -- state energies, channel barriers and the collisional energy-transfer model -- are not "
        "in this database, so the rates cannot be re-derived, the fit cannot be checked, and the network "
        "cannot be re-solved outside the reported temperature and pressure range. The values are as "
        "trustworthy as the cited source; what is missing is the derivation. A computed solve is preferred "
        "where the underlying master-equation run is available."
    ),
    "network_wide_energy_transfer_scope": (
        "Collisional energy transfer was declared once for the whole network and applied to every "
        "collisionally stabilised well and to the bath gas as a whole. <DeltaE>down is a property of a (well, "
        "collider) pair, so this record does not resolve how it varies between wells; that variation was not "
        "determined by the run. Per-well declarations are preferred where the calculation supplies them."
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


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _cm1(value: Any) -> str | None:
    return f"{value:.1f} cm-1" if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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


def _program(context: Mapping[str, Any]) -> str:
    software = _token(context.get("software"))
    return f"{software} " if software else ""


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ---------------------------------------------------------------------------
# tau: why the tolerance is what it is
# ---------------------------------------------------------------------------

_ASSUMED = (
    "'freq.hessian_method' is not recorded, so this is an assumption, not a recorded statement: the producing "
    "program's documented default second-derivative method ({basis}) is assumed and given the same tau as the "
    "equivalent recorded basis. A later upload that states the Hessian method explicitly supersedes this assumption"
)

#: What each ``tau_basis`` token means. TCKDB's own wording for the reason the tolerance was chosen.
_TAU_WHY: dict[str, str] = {
    "protocol_not_recorded": (
        "no Hessian method is recorded for this calculation, so it is not known whether the second derivatives were "
        "analytic or finite-difference; the conservative value is used rather than assuming the better case. Record "
        "Gaussian 'Freq=Numer' or ORCA 'NumFreq'/'AnFreq' in the calculation parameters to tighten it"
    ),
    "analytic_tight": "analytic second derivatives on a tight integration grid with a tight optimisation",
    "analytic_default": "analytic second derivatives, but not on a tight grid with a tight optimisation",
    "finite_difference_gradient": "the Hessian was built by finite differences of analytic gradients",
    "finite_difference_energy": (
        "the Hessian was built by finite differences of energies, which inherits SCF noise divided by the square of "
        "the displacement step"
    ),
    "assumed_analytic_default": _ASSUMED.format(basis="assumed_analytic_default"),
    "assumed_finite_difference_gradient": _ASSUMED.format(basis="assumed_finite_difference_gradient"),
    "assumed_finite_difference_energy": _ASSUMED.format(basis="assumed_finite_difference_energy"),
}

_REMEDIATION = (
    "Remediation, in order: re-run the frequency job on a tighter integration grid (most modes between -10 and "
    "-40 cm-1 vanish); re-optimise more tightly and repeat; if they persist, displace along each extra imaginary mode "
    "by 0.1-0.3 A and re-optimise -- if the energy drops and the extra modes disappear, the original was a genuine "
    "higher-order saddle. If the mode is a torsion, scan it and treat it as a hindered rotor. Passing IRC evidence "
    "makes a soft extra mode moot and is the strongest single thing you can supply."
)


def _tau(context: Mapping[str, Any]) -> str | None:
    value = _num(context.get("tau_cm1"))
    return None if value is None else f"{value:.0f} cm-1"


def _tau_basis(context: Mapping[str, Any]) -> str | None:
    basis = context.get("tau_basis")
    return basis if isinstance(basis, str) and basis in _TAU_WHY else None


def _tau_why(context: Mapping[str, Any]) -> str | None:
    """``Why tau = 30 cm-1: ...`` for the stored tau and its basis, or ``None`` when the row cannot say."""
    tau, basis = _tau(context), _tau_basis(context)
    return None if tau is None or basis is None else f"Why tau = {tau}: {_TAU_WHY[basis]}."


def _protocol(context: Mapping[str, Any], *, plural: bool) -> str:
    """The protocol, said as the basis allows: recorded, assumed from the program's default, or not recorded."""
    basis = _tau_basis(context) or ""
    them = "them" if plural else "it"
    if basis.startswith("assumed_"):
        return f"the protocol assumed for {them}"
    if basis == "protocol_not_recorded":
        return "a protocol that is not recorded"
    return f"the protocol that produced {them}"


# ---------------------------------------------------------------------------
# stationary points and frequency lists
# ---------------------------------------------------------------------------

_VDW_TAIL = (
    "A van der Waals complex is formally a minimum, so zero is expected; its intermolecular modes sit low enough that "
    "Hessian grid noise is comparable to the true curvature, so the record is accepted and flagged rather than "
    "refused. Re-run the frequency job on a tighter integration grid if the mode matters downstream."
)

#: Where a deduction came from (``Deduction.source``), by category, and what each says about the declared value.
_EVIDENCE = frozenset(
    {
        "frequency_analysis",
        "excited_state_job_type",
        "excited_state_method",
        "method_default",
        "multiplicity",
    }
)


def _deduction(context: Mapping[str, Any], declared_what: str) -> str | None:
    deduced, evidence = _token(context.get("deduced")), _token(context.get("evidence"), _EVIDENCE)
    if deduced is None or evidence is None:
        return None
    by = {
        "frequency_analysis": "the frequency analysis",
        "excited_state_job_type": "the excited-state (TD-DFT) job type of the calculation",
        "excited_state_method": "the excited-state method of the calculation",
        "method_default": (
            "the calculation's single-reference method, which targets the ground state by default (a heuristic, "
            "not a measurement)"
        ),
        "multiplicity": "the spin multiplicity (and the point group or linearity, when stated)",
    }[evidence]
    return f"The declared {declared_what} disagrees with the value that {by} indicates ({deduced})."


def _n_imag_contradicts_minimum(context: Mapping[str, Any]) -> str | None:
    n_imag = _int(context.get("n_imag"))
    if n_imag is not None:
        return (
            "The frequency analysis reports 1 imaginary mode for a structure declared a van der Waals complex. "
            + _VDW_TAIL
        )
    return _deduction(context, "stationary point kind")


def _n_imag_higher_order(context: Mapping[str, Any]) -> str | None:
    n_imag = _int(context.get("n_imag"))
    if n_imag is None:
        return None
    return (
        f"The frequency analysis reports {n_imag} imaginary modes for a structure declared a van der Waals complex. "
        + _VDW_TAIL
    )


def _vdw_suggests_ts(context: Mapping[str, Any]) -> str | None:
    size, floor = _cm1(context.get("imaginary_frequency_cm1")), _cm1(context.get("ts_minimum_cm1"))
    if size is None or floor is None:
        return None
    return (
        f"The imaginary mode of a structure declared a van der Waals complex is {size}, at or above {floor}. That is "
        "far too stiff to be an intermolecular mode, so it is unlikely to be Hessian grid noise and looks instead "
        "like a genuine reaction coordinate. The record was accepted and flagged. If this really is a saddle point, "
        "deposit it as a transition state."
    )


def _electronic_state(context: Mapping[str, Any]) -> str | None:
    return _deduction(context, "electronic state")


def _term_symbol_mismatch(context: Mapping[str, Any]) -> str | None:
    return _deduction(context, "term symbol")


def _minimum_below_tau(context: Mapping[str, Any]) -> str | None:
    n_imag, tau, modes, why = _int(context.get("n_imag")), _tau(context), context.get("imaginary_modes_cm1"), _tau_why(context)
    if n_imag is None or tau is None or why is None:
        return None
    if not isinstance(modes, list) or not all(_num(m) is not None for m in modes):
        return None
    sizes = ", ".join(f"{m:.1f}" for m in modes)
    return (
        f"The frequency analysis of a structure declared a minimum reports "
        f"{_count(n_imag, 'imaginary mode', 'imaginary modes')} ({sizes} cm-1), all below tau = {tau}, where the "
        f"sign of the curvature is not determined at {_protocol(context, plural=True)}. The record is accepted, with "
        f"tau recorded alongside it. {why} {_REMEDIATION}"
    )


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


def _ts_extras_below_tau(context: Mapping[str, Any]) -> str | None:
    modes, tau, why = _modes(context), _tau(context), _tau_why(context)
    if modes is None or tau is None or why is None:
        return None
    return (
        f"The transition state has imaginary modes besides its reaction coordinate ({modes}), all below tau = {tau}, "
        f"where the sign of the curvature is not determined at {_protocol(context, plural=True)}. The record is "
        f"accepted without a structural flag. {why} {_REMEDIATION}"
    )


def _ts_extra_above_tau(context: Mapping[str, Any]) -> str | None:
    modes, tau, why = _modes(context), _tau(context), _tau_why(context)
    if modes is None or tau is None or why is None:
        return None
    return (
        f"The transition state has an imaginary mode at or above tau = {tau} besides its reaction coordinate "
        f"({modes}), so at {_protocol(context, plural=False)} the negative curvature is real rather than numerical "
        "noise. The structure is a genuine higher-order saddle. It is accepted because that can be correct "
        "chemistry -- a transition state may sit at a torsional maximum and still be the right reactive bottleneck "
        "-- and flagged because a consumer treating it as a plain first-order saddle would be wrong. "
        f"{why} {_REMEDIATION}"
    )


def _ts_extras_not_assessable(context: Mapping[str, Any]) -> str | None:
    extras, tau, why = _int(context.get("extra_imaginary_mode_count")), _tau(context), _tau_why(context)
    if extras is None or tau is None or why is None:
        return None
    return (
        f"A reaction coordinate is designated for the transition state and {_count(extras, 'other imaginary mode', 'other imaginary modes')} "
        f"{'is' if extras == 1 else 'are'} reported, but no frequency list was deposited, so "
        f"{'it' if extras == 1 else 'they'} cannot be judged against tau = {tau}. {why} The record is accepted and "
        "flagged. Deposit the complete signed, unrounded frequency list so the extra modes can be assessed."
    )


def _ts_coordinate_too_small(context: Mapping[str, Any]) -> str | None:
    size, floor, why = _cm1(context.get("reaction_coordinate_cm1")), _cm1(context.get("ts_minimum_cm1")), _tau_why(context)
    if size is None or floor is None or why is None:
        return None
    return (
        f"The reaction coordinate of the transition state is {size}, below {floor}. A reaction coordinate this soft "
        "is often an under-converged geometry or a coarse integration grid. It can also be real -- flat and "
        "variational barriers genuinely produce one -- which is why this is a quality expectation and not a "
        f"refusal. For reference, the noise floor for this calculation's extra modes: {why}"
    )


def _freq_list_incomplete(context: Mapping[str, Any]) -> str | None:
    n_modes, n_atoms, floor = _int(context.get("n_modes")), _int(context.get("n_atoms")), _int(context.get("minimum_modes"))
    if n_modes is None or n_atoms is None or floor is None:
        return None
    return (
        f"The frequency list carries {_count(n_modes, 'mode', 'modes')}, but a harmonic analysis of the {n_atoms}-atom "
        f"geometry it is attached to produces at least {floor}. The complete signed unrounded list is required, "
        "never filtered: a partial list agrees with an independently recovered spectrum on every mode it contains "
        "and is silent about the ones it omits, which reads as agreement. The record is accepted and flagged. "
        "Deposit every mode the frequency job printed, including the reaction coordinate and any mode removed from "
        "the partition function; if the job genuinely computed a partial Hessian, say so in the calculation's "
        "parameters so the short list is explicable."
    )


def _ratio(context: Mapping[str, Any], threshold_key: str, *, linear: bool) -> str | None:
    ratio, threshold = _num(context.get("transverse_ratio")), _num(context.get(threshold_key))
    if ratio is None or threshold is None:
        return None
    if linear:
        return (
            f"This geometry is linear: its atoms spread across their long axis by only {ratio:.2g} of their spread "
            f"along it, against a threshold of {threshold:g} for calling a geometry linear (exactly collinear is 0)."
        )
    return (
        f"This geometry is not linear: its atoms spread across their long axis by {ratio:.2g} of their spread along "
        f"it, against a threshold of {threshold:g} for calling a geometry bent (exactly collinear is 0)."
    )


_NEAR_LINEAR = (
    "The record is accepted and flagged, not refused: the boundary between linear and bent is a tolerance rather "
    "than a definition, and near-linear geometries are deliberately left unreported."
)


def _linear_count_for_bent(context: Mapping[str, Any]) -> str | None:
    n_modes, n_atoms, expected = _int(context.get("n_modes")), _int(context.get("n_atoms")), _int(context.get("bent_count"))
    measure = _ratio(context, "bent_min_transverse_ratio", linear=False)
    if n_modes is None or n_atoms is None or expected is None or measure is None:
        return None
    return (
        f"The frequency list carries {n_modes} modes, which is 3N-5 for this {n_atoms}-atom geometry (the count of a "
        f"linear molecule). {measure} A non-linear {n_atoms}-atom molecule has 3N-6 = {expected} vibrations, so one "
        "mode here is either spurious or a rigid-body translation/rotation left in the list -- the usual cause "
        "being a frequency job, or a script reading one, that treated the molecule as linear. "
        f"{_NEAR_LINEAR} Deposit every mode the job printed, or the geometry the job actually ran on."
    )


def _bent_count_for_linear(context: Mapping[str, Any]) -> str | None:
    n_modes, n_atoms, expected = _int(context.get("n_modes")), _int(context.get("n_atoms")), _int(context.get("linear_count"))
    measure = _ratio(context, "collinear_max_transverse_ratio", linear=True)
    if n_modes is None or n_atoms is None or expected is None or measure is None:
        return None
    return (
        f"The frequency list carries {n_modes} modes, which is 3N-6 for this {n_atoms}-atom geometry (the vibrational "
        f"count of a bent molecule). {measure} A linear {n_atoms}-atom molecule has 3N-5 = {expected} vibrations, so "
        "one mode is missing from this list. The usual cause is a degenerate bending pair reported once: a linear "
        "molecule's bends are doubly degenerate -- CO2's four vibrations are two stretches and one bend counted "
        "twice -- so a parser or script that de-duplicates equal frequencies drops one component and lands exactly "
        "here. A genuinely partial or frozen-atom Hessian would also produce a short list, honestly. "
        f"{_NEAR_LINEAR} Deposit every mode the job printed, including both components of each degenerate pair."
    )


# ---------------------------------------------------------------------------
# transition-state evidence, atom maps
# ---------------------------------------------------------------------------

#: The sides of a reaction a participant is on (``ReactionRole`` tokens the atom map uses).
_SIDES = frozenset({"reactant", "product"})

#: The energy kinds of a transition-state energy ordering.
_ENERGY_KINDS = frozenset({"electronic", "e0"})

#: Why a stated energy could not be held against a stored one, for a transition-state energy ordering and for a network
#: state energy. TCKDB's own stable tokens (``NOT_COMPARED_*`` in the services that emit them; a test pins the two).
NOT_COMPARED_REASONS = frozenset(
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
    tokens = [_token(r, NOT_COMPARED_REASONS) for r in reasons]
    return None if any(t is None for t in tokens) else sorted({t for t in tokens if t})


_ZPE_SCALING_HINT = (
    " A 'zpe_scaling_unstated' energy cites a frequency ZPE but states no zpe_scale_factor: TCKDB stores the "
    "unscaled ZPE and never assumes the scale is one, so state the factor (to at least 4 decimals) and it is compared."
)
_PRECISION_HINT = (
    " A 'stated_precision_unknown' energy differs from the stored sum by more than printed precision but by no more "
    "than honest rounding of the stated number could explain: state energy_precision_kj_mol (the rounding unit) for "
    "it, or state unrounded kJ/mol derived from hartree with 2625.499639, and it is compared."
)
_ORDERING_SCALING_HINT = (
    " An E0 that is not the stored electronic energy plus the stored (unscaled) zero-point energy was not refused, "
    "because a scaled zero-point energy cannot be told from a wrong number; if you scaled it, state zpe_scale_factor "
    "on that energy and it will be checked."
)


def _ordering_not_compared(context: Mapping[str, Any]) -> str | None:
    reasons = _reasons(context)
    if reasons is None:
        return None
    hint = _ORDERING_SCALING_HINT if "zpe_scaling_unstated" in reasons else ""
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
        f"cite (reasons: {', '.join(reasons)}). They are stored as not compared with their reason, and each rests on "
        f"the stated number alone.{hint}"
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
    "atom_map_field": " If you followed the intrinsic reaction coordinate, you already have this: supply it as 'atom_map' (ADR 0011).",
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


# ---------------------------------------------------------------------------
# atoms and identity
# ---------------------------------------------------------------------------


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
    j = _num(context.get("ground_j"))
    if element is None or term is None or j is None:
        return None
    return (
        f"The declared term symbol is not the NIST ground level of neutral {element} ({term}, ground J={j:g}) "
        "for a ground-state entry."
    )


def _levels_text(context: Mapping[str, Any]) -> str | None:
    levels = context.get("ground_levels")
    if not isinstance(levels, list) or not levels:
        return None
    parts = []
    for level in levels:
        if not isinstance(level, Mapping):
            return None
        j, g, e = _num(level.get("j")), _int(level.get("degeneracy")), _num(level.get("energy_cm1"))
        if j is None or g is None or e is None:
            return None
        parts.append(f"J={j:g} (g={g}) at {e:g} cm^-1")
    return ", ".join(parts)


def _missing_atomic_electronic_levels(context: Mapping[str, Any]) -> str | None:
    element, term = _token(context.get("element")), _token(context.get("ground_term"))
    multiplicity, j_desc = _int(context.get("multiplicity")), _levels_text(context)
    q_el, error = _num(context.get("electronic_partition_function")), _num(context.get("free_energy_error_kj_mol"))
    if element is None or term is None or multiplicity is None or j_desc is None or q_el is None or error is None:
        return None
    return (
        f"{element} atom has a {term} ground term with fine structure ({j_desc}) but this statmech carries no "
        f"electronic levels, so its electronic partition function is only the spin degeneracy {multiplicity}. Its "
        f"entropy is low and its free energy too high by about {error:.2f} kJ/mol at 298.15 K (q_el = {q_el:.2f} "
        f"against {multiplicity})."
    )


def _missing_atomic_spin_orbit_correction(context: Mapping[str, Any]) -> str | None:
    element, term = _token(context.get("element")), _token(context.get("ground_term"))
    shift = _num(context.get("spin_orbit_shift_kj_mol"))
    if element is None or term is None or shift is None:
        return None
    return (
        f"{element} atom has a {term} ground term whose spin-orbit splitting lowers the ground level, but no "
        f"soc_total energy correction was supplied, so its energy is the spin-orbit-free value, which lies "
        f"{abs(shift):.2f} kJ/mol above the ground level."
    )


def _degeneracy(context: Mapping[str, Any]) -> str | None:
    element, term = _token(context.get("element")), _token(context.get("ground_term"))
    lowest, allowed, levels = _int(context.get("lowest_degeneracy")), context.get("allowed_degeneracies"), context.get("level_degeneracies")
    if element is None or term is None or lowest is None:
        return None
    for seq in (allowed, levels):
        if not isinstance(seq, list) or not seq or not all(_int(a) is not None for a in seq):
            return None
    return (
        f"The lowest electronic level of this {element} atom has degeneracy {lowest}, but the {term} ground term has "
        f"levels of degeneracy {', '.join(str(g) for g in levels)} in energy order, so the lowest level can only be "
        f"{', '.join(str(a) for a in allowed)}."
    )


# ---------------------------------------------------------------------------
# composites
# ---------------------------------------------------------------------------


def _composite_method_mismatch(context: Mapping[str, Any]) -> str | None:
    method = _token(context.get("log_method"))
    if method is None:
        return None
    return (
        f"The attached output log is a {method} run, which is not the composite method of this calculation. The "
        "method was kept as sent and the deposited energies were not compared with this log. Attach the log of the "
        "run that produced this calculation, or correct the level of theory."
    )


def _composite_log_available(context: Mapping[str, Any]) -> str | None:
    method, e0, zpe = _token(context.get("log_method")), _hartree6(context.get("e0_hartree")), _hartree6(context.get("recipe_zpe_hartree"))
    if method is None or e0 is None or zpe is None:
        return None
    return (
        f"No composite energy was deposited, and the attached {method} output log states E0 = {e0} and a recipe ZPE "
        f"of {zpe}. Nothing was filled from the log; send these in composite_result (e0_hartree, recipe_zpe_hartree, "
        "and the ZPE-free electronic_energy_hartree) if you want them recorded."
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
        if quantity == "electronic_energy_hartree":
            parts.append(f"{quantity} {deposited} against the log-implied {logged} (the log's 0 K energy minus its ZPE)")
        else:
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


_COMPOSITE_REFUSED_LATER = "This will be refused once producers can send that shape."


def _named_composite(context: Mapping[str, Any], kind: str) -> str | None:
    if context.get("calculation_type") != kind:
        return None
    return (
        f"This {kind} calculation is at the level of theory of a named composite method: one program run that "
        f"produces a final energy from several internal steps. A {kind} calculation is not what ran. It was stored "
        "as sent; send it as type 'composite' with a composite_result block (assembly 'program_run' and the energies "
        "the program printed) and, if the run's own optimisation or frequency job is also deposited, send those "
        f"separately at their own levels. {_COMPOSITE_REFUSED_LATER}"
    )


def _named_composite_opt(context: Mapping[str, Any]) -> str | None:
    return _named_composite(context, "opt")


def _named_composite_sp(context: Mapping[str, Any]) -> str | None:
    return _named_composite(context, "sp")


def _composite_role(context: Mapping[str, Any]) -> str | None:
    kind = _token(context.get("calculation_type"))
    if kind is None:
        return None
    return (
        f"A calculation of type '{kind}' is linked with the role 'composite'. The role names a composite energy, "
        "which is recorded by a calculation of type 'composite'. The link was stored as sent; it will be refused "
        "once producers can send that shape."
    )


# ---------------------------------------------------------------------------
# calculations, software, literature
# ---------------------------------------------------------------------------


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
        "The optimisation is recorded as converged, which is itself a claim about the energy -- an optimiser declares "
        "convergence when the energy change between steps falls below a threshold. No single-point calculation was "
        "found for the same owner (any level of theory would satisfy this; none was required at the optimisation's "
        f"own level), and the optimisation {missing}. Deposit a single-point calculation for this species or "
        "transition state, or supply the optimisation's own final energy together with an artifact backing it."
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
        f"The declared software name disagreed with the program identified in the uploaded artifact ({observed}). "
        f"The artifact is the observed fact, so this calculation's software_release now points at {observed}. The "
        "originally declared value is preserved on declared_software_banner."
    )


def _software_version_filled(context: Mapping[str, Any]) -> str | None:
    version = _token(context.get("version"))
    if version is None:
        return None
    return (
        f"The declared release gave no version; the uploaded output log states version {version}. This calculation "
        "now cites that release. The banner is kept on observed_software_banner."
    )


def _literature_year(context: Mapping[str, Any]) -> str | None:
    supplied, fetched = _int(context.get("supplied_year")), _int(context.get("fetched_year"))
    if supplied is None or fetched is None:
        return None
    return (
        f"The year supplied for the cited literature ({supplied}) differs from the one fetched for its identifier "
        f"({fetched}). The fetched year was stored; verify the identifier is correct for this citation."
    )


def _reversible_twin(context: Mapping[str, Any]) -> str | None:
    reversible = context.get("reversible")
    if not isinstance(reversible, bool):
        return None
    this, other = ("reversible", "irreversible") if reversible else ("irreversible", "reversible")
    return (
        f"This reaction is stored as {this}, and the same participants are also stored as {other}, as a separate "
        "reaction. They are two reactions in TCKDB because reversibility is part of a reaction's identity; rates "
        "deposited against one are not found under the other."
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
    remedy = (
        " Cite the freq (zpe_calculation_key), include it in the upload, or cite a composite that holds the E0."
        if subject == "state_energy"
        else ""
    )
    return (
        f"A {what} states '{convention}', which includes a vibrational term, but its source is a bare "
        "electronic-energy calculation and the upload carries no frequency calculation for it. It is stored as "
        f"stated, with no source for its zero-point energy.{remedy}"
    )


def _scheme_software(context: Mapping[str, Any]) -> str | None:
    kind = _token(context.get("scheme_kind"))
    if kind is None:
        return None
    return (
        f"No software release was supplied for this {kind} scheme. Atom-energy and bond-additivity corrections are "
        "the output of a program's own build-level numerics, so a scheme with no program recorded at all cannot be "
        "distinguished from a different program's values at the same level of theory. (A program with no build "
        "stated -- e.g. just 'Gaussian', no version -- is a complete, honest deposit and does not trigger this "
        "warning; only a fully absent software release does.)"
    )


def _method_table(context: Mapping[str, Any]) -> str | None:
    stem = _token(context.get("method_stem"))
    if stem is None:
        return None
    return (
        f"The method of a level of theory is the method {stem} followed by a label that selects an energy-correction "
        f"table (a correction-set name or a year), not a method. The calculation ran {stem}. It was stored as sent, "
        f"as a separate level of theory from {stem}. Send method={stem} and name the table on the energy correction "
        "scheme instead."
    )


_VERSION_ACTIONS = {
    "split_banner_with_revision": (
        "A software version was a parsed banner carrying the program name and a revision label. It was normalised "
        "to a version and a revision (the leading package name was stripped and the trailing revision label was "
        "split out)."
    ),
    "split_banner_name_only": (
        "A software version carried the software's own name with it. It was normalised to the version alone (the "
        "leading package name was stripped)."
    ),
    "revision_already_supplied": (
        "A software version looks like a parsed software banner embedding its own revision label, but a revision "
        "was also supplied. Both fields were left exactly as sent rather than choosing between them."
    ),
}


def _version_is_composite(context: Mapping[str, Any]) -> str | None:
    action = context.get("action")
    return _VERSION_ACTIONS.get(action) if isinstance(action, str) else None


#: Why a Hessian found in an uploaded artifact was not stored (``reason`` in the context).
_HESSIAN_NOT_STORED = {
    "hessian_not_stored_unparseable": {
        "sent_as_hessian": (
            "An artifact sent as a Hessian contained no complete force-constant matrix that could be read, so no "
            "Hessian was found to store."
        ),
        "log_block_unreadable": (
            "A Hessian was found in an uploaded artifact but not stored: the log has a force-constant block but no "
            "complete, trustworthy matrix could be read from it (it may be truncated)."
        ),
    },
    "hessian_not_stored_failed": {
        "read_failed": "A Hessian was found in an uploaded artifact but not stored: reading it failed unexpectedly.",
        "store_failed": "A Hessian was found in an uploaded artifact but not stored: storing it failed unexpectedly.",
    },
    "hessian_not_stored_no_unique_input_geometry": {
        "geometry_not_found": (
            "A Hessian was found in an uploaded artifact but not stored: the calculation's input geometry could not "
            "be found."
        ),
    },
}
_HESSIAN_TAIL = " The artifact itself is stored; only the Hessian is not."


def _hessian_not_stored(code: str):
    def render(context: Mapping[str, Any]) -> str | None:
        reason = context.get("reason")
        if code == "hessian_not_stored_not_frequency_calculation":
            kind = _token(context.get("calculation_type"))
            if kind is None:
                return None
            return (
                f"A Hessian was found in an uploaded artifact but not stored: this is a '{kind}' calculation, and a "
                "Hessian is stored only from a 'freq' calculation, whose input geometry is the frame the matrix was "
                f"computed at.{_HESSIAN_TAIL}"
            )
        if code == "hessian_not_stored_no_unique_input_geometry" and reason == "input_geometry_count":
            count = _int(context.get("input_geometries"))
            if count is None:
                return None
            return (
                f"A Hessian was found in an uploaded artifact but not stored: the calculation has {count} input "
                f"geometries and exactly one is needed to bind the matrix to the structure it was computed at.{_HESSIAN_TAIL}"
            )
        if code == "hessian_not_stored_geometry_mismatch":
            if reason == "atom_count":
                matrix, geometry = _int(context.get("matrix_atoms")), _int(context.get("geometry_atoms"))
                if matrix is None or geometry is None:
                    return None
                return (
                    f"A Hessian was found in an uploaded artifact but not stored: the matrix is for {matrix} atoms "
                    f"but the calculation's input geometry has {geometry}.{_HESSIAN_TAIL}"
                )
            if reason == "frame":
                return (
                    "A Hessian was found in an uploaded artifact but not stored: the atomic frame recorded in the "
                    "file does not match the calculation's input geometry (different atoms, order or "
                    f"orientation).{_HESSIAN_TAIL}"
                )
            if reason == "species_contradiction":
                refusal = _token(context.get("refusal_code"))
                if refusal is None:
                    return None
                return (
                    "A Hessian was found in an uploaded artifact but not stored: the input geometry it would be "
                    f"bound to contradicts the calculation's species ({refusal}), so it was not trusted with a "
                    f"force-constant matrix.{_HESSIAN_TAIL}"
                )
            return None
        sentence = _HESSIAN_NOT_STORED.get(code, {}).get(reason) if isinstance(reason, str) else None
        return None if sentence is None else sentence + _HESSIAN_TAIL

    return render


# ---------------------------------------------------------------------------
# stereo
# ---------------------------------------------------------------------------

#: Why part of an entry's stereochemistry cannot be stated yet, by the reason token the stereo descriptor reports
#: (``R_*`` in ``app.chemistry.stereo``; a test pins the two).
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

_DESCRIPTOR = re.compile(r"[A-Za-z0-9?,*+\-]{1,60}")


def _descriptor(context: Mapping[str, Any]) -> str:
    value = context.get("descriptor")
    return f" (descriptor '{value}')" if isinstance(value, str) and _DESCRIPTOR.fullmatch(value) else ""


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
        f"Part of this species entry's stereochemistry cannot be represented yet{_descriptor(context)}: "
        f"{'; '.join(t for t in texts if t)}. Real stereoisomers may therefore share this entry, and a later "
        f"deposit of the other form can land in it.{also}"
    )


def _stereo_unresolved(context: Mapping[str, Any]) -> str | None:
    resolution = context.get("resolution")
    if resolution not in ("partial", "unresolved"):
        return None
    return (
        f"The stereochemistry of this species entry is {resolution}{_descriptor(context)}: at least one stereo "
        "element (R/S centre, E/Z double bond, Ra/Sa allene axis or E/Z cumulene) is unknown, because no geometry "
        "resolved it, the SMILES carries no readable mark for it and no stereo_label was supplied. The entry was "
        "stored as its own entry; a later deposit that specifies the configuration will not merge into it."
    )


# ---------------------------------------------------------------------------
# values stated beside the log
# ---------------------------------------------------------------------------

#: TCKDB's own Hessian-method tokens (``HessianMethod``): the only strings a context may put in a sentence.
_HESSIAN_METHODS = frozenset({"analytic", "finite_difference_gradient", "finite_difference_energy"})


def _charge(context: Mapping[str, Any]) -> str | None:
    declared, logged = _signed(context.get("declared")), _signed(context.get("logged"))
    if declared and logged:
        return (
            f"The declared charge ({declared}) disagrees with the charge stated by the uploaded "
            f"{_program(context)}output log ({logged}). The declared value was kept and flagged for reviewer attention."
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
            f"uploaded {_program(context)}output log ({logged}). The declared value was kept and flagged for "
            "reviewer attention."
        )
    return None


def _sp_mismatch(context: Mapping[str, Any]) -> str | None:
    recorded, log = _hartree(context.get("recorded_hartree")), _hartree(context.get("log_hartree"))
    delta = _num(context.get("delta_hartree"))
    if recorded and log:
        gap = f" (delta {delta:+.2e} Ha)" if delta is not None else ""
        return (
            f"The single-point energy deposited ({recorded}) disagrees with the value re-derived from the "
            f"uploaded output log ({log}){gap}. The deposited value was kept and flagged for reviewer attention."
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
    "n_imag_contradicts_minimum": _n_imag_contradicts_minimum,
    "n_imag_higher_order_saddle": _n_imag_higher_order,
    "n_imag_suggests_transition_state": _vdw_suggests_ts,
    "electronic_state_contradicts_method": _electronic_state,
    "term_symbol_mismatch": _term_symbol_mismatch,
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
    "named_composite_deposited_as_opt": _named_composite_opt,
    "named_composite_deposited_as_sp": _named_composite_sp,
    "composite_role_on_non_composite_calculation": _composite_role,
    "converged_opt_no_usable_energy": _converged_opt,
    "dependency_edge_not_inferred": _edge_not_inferred,
    "software_release_identity_corrected_from_artifact": _software_corrected,
    "software_release_version_filled_from_artifact": _software_version_filled,
    "literature_year_mismatch": _literature_year,
    "reaction_reversible_twin": _reversible_twin,
    "energy_level_stored_without_source_calculations": _level_without_sources,
    "missing_tunneling_application_evidence": _tunneling,
    "missing_energy_correction_scheme_software": _scheme_software,
    "level_of_theory_method_names_correction_table": _method_table,
    "software_release_version_is_composite": _version_is_composite,
    "species_entry_stereo_not_represented": _stereo_not_represented,
    "species_entry_stereo_unresolved": _stereo_unresolved,
    "hessian_not_stored_unparseable": _hessian_not_stored("hessian_not_stored_unparseable"),
    "hessian_not_stored_failed": _hessian_not_stored("hessian_not_stored_failed"),
    "hessian_not_stored_no_unique_input_geometry": _hessian_not_stored("hessian_not_stored_no_unique_input_geometry"),
    "hessian_not_stored_geometry_mismatch": _hessian_not_stored("hessian_not_stored_geometry_mismatch"),
    "hessian_not_stored_not_frequency_calculation": _hessian_not_stored("hessian_not_stored_not_frequency_calculation"),
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


# ---------------------------------------------------------------------------
# what a context may hold
# ---------------------------------------------------------------------------

#: The context keys that may hold a string outside ``depositor_supplied``: TCKDB tokens, parsed program keys, public
#: refs, fetched metadata. Every other string in a context belongs under ``depositor_supplied``. A producer that
#: moves a label to an unmarked key is caught by :func:`context_problems`, which the test session applies to every
#: warning any producer builds.
UNMARKED_STRING_KEYS = frozenset(
    {
        "application_role",
        "calculation_type",
        "confidence",
        "correction_convention",
        "deck_method",
        "deduced",
        "dependency_role",
        "deposit_path",
        "descriptor",
        "disposition",
        "element",
        "energy_kind",
        "evidence",
        "expected_parent_type",
        "fetched_title",
        "ground_term",
        "log_method",
        "method_stem",
        "observed_software",
        "primary_type",
        "quantity",
        "action",
        "reason",
        "reasons",
        "record_kind",
        "refusal_code",
        "release_ref",
        "resolution",
        "scheme_kind",
        "sibling_ref",
        "side",
        "software",
        "stored_basis",
        "stored_method",
        "subject",
        "tau_basis",
        "tunneling_model",
        "twin_ref",
        "version",
    }
)

_PUBLIC_REF = re.compile(r"[a-z]{2,6}_[a-z0-9]{8,40}")
_ELEMENT = re.compile(r"[A-Z][a-z]?")
#: Keys whose string must also look like what they are, so a label moved there is not waved through by its name.
_KEY_SHAPES: dict[str, re.Pattern[str]] = {
    "sibling_ref": _PUBLIC_REF,
    "twin_ref": _PUBLIC_REF,
    "release_ref": _PUBLIC_REF,
    "element": _ELEMENT,
}

_DEPOSITOR_KEY = "depositor_supplied"


def _depositor_supplied_problems(value: Any) -> list[str]:
    if not isinstance(value, dict) or not value:
        return ["depositor_supplied is not a non-empty dict"]
    problems = []
    for name, item in value.items():
        if not isinstance(name, str):
            problems.append(f"depositor_supplied key {name!r} is not a string")
        elif isinstance(item, str):
            pass
        elif isinstance(item, list) and item and all(isinstance(i, str) for i in item):
            pass
        elif isinstance(item, list) and item and all(
            isinstance(i, dict) and i and all(isinstance(k, str) and isinstance(v, str) for k, v in i.items())
            for i in item
        ):
            pass
        else:
            problems.append(
                f"depositor_supplied[{name!r}] is not a string, a non-empty list of strings or a non-empty list of "
                "records of strings"
            )
    return problems


def _walk_unmarked(value: Any, key: str | None, path: str, problems: list[str]) -> None:
    if isinstance(value, str):
        if key not in UNMARKED_STRING_KEYS:
            problems.append(f"{path} holds a string outside depositor_supplied")
        elif key in _KEY_SHAPES and not _KEY_SHAPES[key].fullmatch(value):
            problems.append(f"{path} does not look like a {key}")
    elif isinstance(value, Mapping):
        for name, item in value.items():
            _walk_unmarked(item, name if isinstance(name, str) else None, f"{path}.{name}", problems)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_unmarked(item, key, f"{path}[]", problems)


def context_problems(code: str, context: Mapping[str, Any] | None) -> list[str]:
    """What is wrong with a warning's context: an unknown code, a string outside the marked key, a malformed marked key.

    ``depositor_supplied`` is the one place depositor-typed text may go: a dict of named strings, lists of strings
    and lists of records of strings. Every other string must sit under a key in :data:`UNMARKED_STRING_KEYS`.
    """
    problems: list[str] = []
    if code not in RENDERED:
        problems.append(f"{code} has no sentence in RENDERED")
    for name, value in (context or {}).items():
        if name == _DEPOSITOR_KEY:
            problems.extend(_depositor_supplied_problems(value))
        else:
            _walk_unmarked(value, name, name, problems)
    return problems


def clean_depositor_supplied(context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The context with the entries of ``depositor_supplied`` that hold nothing dropped, and the key itself if empty."""
    if not context:
        return None
    cleaned = dict(context)
    supplied = cleaned.get(_DEPOSITOR_KEY)
    if isinstance(supplied, dict):
        kept = {k: v for k, v in supplied.items() if v is not None and v != [] and v != ""}
        if kept:
            cleaned[_DEPOSITOR_KEY] = kept
        else:
            del cleaned[_DEPOSITOR_KEY]
    return cleaned or None


# ---------------------------------------------------------------------------
# the payload path
# ---------------------------------------------------------------------------


def normalise_field(field: str) -> str:
    """The payload path with every depositor key replaced by ``[*]``; plain integer indexes (TCKDB's ordinals) stay.

    A key is whatever stands between ``[`` and its closing ``]`` that is not a plain integer: quoted
    (``['h']``, ``["a b"]``, ``['o'k']`` with an embedded quote, a backslash escape) or unquoted
    (``transition_states[ts1]``). A quoted key ends at the first closing quote that is followed by ``]``.
    """
    out: list[str] = []
    i, n = 0, len(field)
    while i < n:
        ch = field[i]
        if ch != "[":
            out.append(ch)
            i += 1
            continue
        end = field.find("]", i + 1)
        if end != -1 and field[i + 1 : end].isdigit():
            out.append(field[i : end + 1])
            i = end + 1
            continue
        quote = field[i + 1 : i + 2]
        close = -1
        if quote in ("'", '"'):
            j = i + 2
            while j < n:
                if field[j] == "\\":
                    j += 2
                    continue
                if field[j] == quote and field[j + 1 : j + 2] == "]":
                    close = j + 1
                    break
                j += 1
        else:
            close = end
        out.append("[*]")
        i = n if close == -1 else close + 1
    return "".join(out)


__all__ = [
    "COMPOSITE_TOTAL_UNVERIFIABLE_REASONS",
    "NOT_COMPARED_REASONS",
    "RENDERED",
    "STEREO_NOT_REPRESENTED_REASONS",
    "UNMARKED_STRING_KEYS",
    "clean_depositor_supplied",
    "context_problems",
    "normalise_field",
    "render_message",
]
