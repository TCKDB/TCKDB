"""Collection point for the scientific check register.

Most entries live beside the check they describe. This module holds the entries
that cannot sit beside a Python check, plus the list of modules the collector
walks:

* PostgreSQL constraints and triggers have no Python object, so the register
  names the schema object and the drift guard verifies the name against live
  schema metadata.
* ``tckdb_schemas`` may not import the backend, so a wire-schema check cannot
  reference :class:`~app.scientific_checks.ScientificCheck` where it is
  defined.
"""

from __future__ import annotations

import sys
from types import ModuleType

from tckdb_schemas.bundle_source_rules import (
    W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH,
    W_SCF_STABILITY_SOURCE_CALCULATION_OWNER_MISMATCH,
    W_SCF_STABILITY_SOURCE_CONFORMER_UNANCHORED,
    W_SCF_STABILITY_SOURCE_CYCLE,
    W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH,
    W_TRANSITION_STATE_CALCULATION_SPECIES_GEOMETRY,
    W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
    assert_calculation_geometry_matches_conformer,
    assert_transition_state_calculations_unanchored,
    owner_mismatch_error,
    scf_source_cycle_error,
    scf_source_geometry_error,
    scf_source_unanchored_error,
)
from tckdb_schemas.composite_total import (
    COMPOSITE_TOTAL_MISMATCH,
    assert_composite_total_recomputes,
)
from tckdb_schemas.enthalpy_reference import (
    W_ENTHALPY_DECLARATION_ABSENT,
    W_ENTHALPY_DECLARATION_WITHOUT_CONTENT,
    W_ENTHALPY_QUANTITY_NOT_STORABLE_HERE,
    W_ENTHALPY_REFERENCE_KIND_UNRECOGNIZED,
    enthalpy_reference_error,
)
from tckdb_schemas.fragments import reaction_atom_map as wire_atom_map
from tckdb_schemas.fragments.calculation import (
    COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
    COMPOSITE_E0_INCONSISTENT,
    COMPOSITE_TERMS_DO_NOT_SUM,
    W_FREQ_N_IMAG_DISAGREES_WITH_MODES,
    FreqResultPayload,
    assert_composite_result_arithmetic,
)
from tckdb_schemas.fragments.kinetics_bath_gas import (
    BATH_GAS_SUM_TOLERANCE,
    W_KINETICS_BATH_GAS_AT_HIGH_PRESSURE_LIMIT,
    W_KINETICS_BATH_GAS_DUPLICATE_SPECIES,
    W_KINETICS_BATH_GAS_MOLE_FRACTION_SUM,
    check_bath_gas_mole_fraction_sum,
    check_kinetics_bath_gas,
)
from tckdb_schemas.frequency_completeness import (
    W_FREQ_LIST_EXCEEDS_GEOMETRY,
    evaluate_frequency_list_completeness,
)
from tckdb_schemas.hessian_method_detection import (
    W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK,
    check_recorded_method_agrees_with_deck,
)
from tckdb_schemas.kinetics_declarations import (
    W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
    kinetics_applicability_error,
    kinetics_protocol_origin_error,
)
from tckdb_schemas.network_channel_kind import (
    W_NETWORK_CHANNEL_KIND_MISMATCH,
    derive_channel_kind,
)
from tckdb_schemas.sp_energy_components import (
    SP_ENERGY_COMPONENT_DUPLICATE,
    SP_ENERGY_COMPONENT_TOTAL_MISMATCH,
    SP_ENERGY_COMPONENTS_DO_NOT_SUM,
    SUM_TOLERANCE_HARTREE,
    check_sp_energy_components,
)
from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_DEFAULT_CM1,
    TAU_ANALYTIC_TIGHT_CM1,
    TAU_FINITE_DIFFERENCE_ENERGY_CM1,
    TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
    TAU_PARAMETER_KEYS,
    TAU_PROTOCOL_NOT_RECORDED_CM1,
    TS_IMAGINARY_FREQUENCY_MIN_CM1,
    W_HESSIAN_METHOD_REFINED,
    W_MINIMUM_IMAGINARY_MODES_BELOW_TAU,
    W_N_IMAG_CONTRADICTS_MINIMUM,
    W_N_IMAG_HIGHER_ORDER_SADDLE,
    W_N_IMAG_SUGGESTS_TS,
    W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
    W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU,
    W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE,
    W_TS_IMAG_FREQ_TOO_SMALL,
    W_TS_NO_IMAGINARY_MODE,
    W_TS_REACTION_COORDINATE_AMBIGUOUS,
    W_TS_REACTION_COORDINATE_NOT_DESIGNATED,
    _refinement_findings,
    evaluate_species_entry_frequency,
    evaluate_transition_state_frequency,
    raise_for_blocking_findings,
    resolve_tau,
)
from tckdb_schemas.thermo_declarations import (
    W_THERMO_DECLARED_BY_RECORD_INCOHERENT,
    W_THERMO_DECLARED_BY_TARGET_INCOHERENT,
    _declared_by_record_error,
    thermo_target_error,
)

from app.api.routes import lookup as lookup_routes
from app.chemistry import species as chemistry_species
from app.chemistry import stereo as chemistry_stereo
from app.chemistry import units as chemistry_units
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    DatabaseConstraint,
    DesignPosition,
    ProvenanceThreshold,
    PythonCheck,
    ScientificCheck,
    collect_constraint_rejections,
    collect_registered_checks,
)
from app.scientific_checks import external_comparison as external_comparison_checks
from app.services import (
    atomic_electronic_warnings,
    calc_isotopes,
    calculation_levels,
    calculation_ownership,
    calculation_resolution,
    charge_multiplicity_reconciliation,
    composite_energy_reconciliation,
    composite_input_resolution,
    composite_result_resolution,
    energy_correction_resolution,
    frequency_geometry_linearity,
    hessian_method_deck_check,
    kinetics_declaration_resolution,
    network_energy_sources,
    observation_identity_attach,
    parameter_reconciliation,
    reaction_resolution,
    scan_coordinate_conformance,
    scf_stability_sources,
    sp_energy_reconciliation,
    species_resolution,
    statmech_declaration_resolution,
    statmech_resolution,
    structure_determination_resolution,
    thermo_declaration_resolution,
    transition_state_validation,
    upload_reconciliation,
    wavefunction_reliability_warnings,
)
from app.services import geometry_validation as geometry_validation_service
from app.services import reaction_atom_map as reaction_atom_map_service
from app.services.artifact_integrity import record_integrity_observation
from app.services.artifact_storage import load_artifact_bytes
from app.services.provenance_warnings import (
    W_NETWORK_WIDE_ENERGY_TRANSFER,
    W_REPORTED_NETWORK_SOLVE,
)
from app.services.relative_conformer_energy import compare as relative_conformer_energy_compare
from app.services.scientific_read import species_calculations_search
from app.services.trust.evaluator import (
    _detect_calculation_hard_fail as detect_calculation_hard_fail,
)
from app.workflows import network_pdep
from app.workflows.thermo import (
    W_THERMO_SOURCE_ROLE_TYPE_MISMATCH,
    assert_enthalpy_reference,
    assert_thermo_role_matches_calculation_type,
)

# ---------------------------------------------------------------------------
# Stationary points — the imaginary-mode spectrum is the definition
# ---------------------------------------------------------------------------

#: The imaginary-mode tau protocol table, declared once and rendered into both the
#: register document and the τ entry below.
_TAU_VALUES: tuple[tuple[str, str], ...] = (
    (
        "analytic Hessian, tight grid, tight optimisation",
        f"{TAU_ANALYTIC_TIGHT_CM1:.0f}",
    ),
    (
        "analytic Hessian, default grid and tolerances",
        f"{TAU_ANALYTIC_DEFAULT_CM1:.0f}",
    ),
    (
        "finite-difference Hessian from analytic gradients",
        f"{TAU_FINITE_DIFFERENCE_GRADIENT_CM1:.0f}",
    ),
    (
        "finite-difference Hessian from energies",
        f"{TAU_FINITE_DIFFERENCE_ENERGY_CM1:.0f}",
    ),
    (
        "Hessian method not recorded and program or method family not in the assumed-default table",
        f"{TAU_PROTOCOL_NOT_RECORDED_CM1:.0f}",
    ),
)

_TAU_THRESHOLD = ProvenanceThreshold(
    name="tau",
    unit="cm-1",
    resolver=resolve_tau,
    parameter_keys=TAU_PARAMETER_KEYS,
    values=_TAU_VALUES,
    fallback=(
        f"Tau is taken from the method an attached output log reports, else the method the "
        f"input echoed in that log requests, else the recorded ``freq.hessian_method`` or the "
        f"method the calculation's input deck states, else the producing program's assumed "
        f"default for the level of theory's method family, else "
        f"{TAU_PROTOCOL_NOT_RECORDED_CM1:.0f} cm-1 with ``protocol_not_recorded`` as the "
        f"basis, which is the conservative row. A present but unrecognised "
        f"``freq.hessian_method`` keeps ``protocol_not_recorded`` and skips the assumption."
    ),
    rationale=(
        "The Hessian noise floor is flat in omega-squared, so whether a small imaginary "
        "frequency is real depends on how the Hessian was built."
    ),
)

CHECK_TRANSITION_STATE_REACTION_COORDINATE = ScientificCheck(
    group="Stationary points",
    sort_key=1,
    code=(
        W_TS_NO_IMAGINARY_MODE,
        W_TS_REACTION_COORDINATE_NOT_DESIGNATED,
        W_TS_REACTION_COORDINATE_AMBIGUOUS,
    ),
    asserts=(
        "A transition state has at least one imaginary vibrational mode, "
        "exactly one of them is designated the reaction coordinate, and no "
        "undeclared mode is stiff enough to make that designation meaningless."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(evaluate_transition_state_frequency),
        DesignPosition(
            where=(
                "``_check_ts_reaction_coordinate_designated`` "
                "(``backend/app/services/trust/rubrics.py``) reports at read time "
                "whether a record with more than one imaginary mode carries the designation, "
                "without re-judging the frequencies."
            )
        ),
    ),
    escape_hatch=(
        "Deposit no frequency evidence, or designate the reaction coordinate and give each "
        "other imaginary mode a disposition."
    ),
)

CHECK_MINIMUM_ZERO_IMAGINARY_MODES = ScientificCheck(
    group="Stationary points",
    sort_key=2,
    code=W_N_IMAG_CONTRADICTS_MINIMUM,
    asserts=(
        "A species entry declared a minimum has no imaginary vibrational "
        "mode at or above the tolerance of the protocol that produced it."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    thresholds=(_TAU_THRESHOLD,),
    enforced_by=(
        PythonCheck(evaluate_species_entry_frequency),
    ),
    escape_hatch=(
        "Declare ``species_entry_kind='vdw_complex'``, or deposit the structure as a "
        "transition state."
    ),
)

CHECK_VDW_COMPLEX_IMAGINARY_MODE = ScientificCheck(
    group="Stationary points",
    sort_key=3,
    code=(
        W_N_IMAG_CONTRADICTS_MINIMUM,
        W_N_IMAG_HIGHER_ORDER_SADDLE,
        W_N_IMAG_SUGGESTS_TS,
    ),
    asserts=(
        "An imaginary mode on a van der Waals complex is recorded with a warning rather than "
        "refused, and a mode too stiff to be intermolecular is also reported as suggesting a "
        "transition state."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(evaluate_species_entry_frequency),
    ),
    escape_hatch="None; the check only warns.",
)

CHECK_TRANSITION_STATE_EXTRA_IMAGINARY_MODES = ScientificCheck(
    group="Stationary points",
    sort_key=4,
    code=(
        W_MINIMUM_IMAGINARY_MODES_BELOW_TAU,
        W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU,
        W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
        W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE,
    ),
    asserts=(
        "An imaginary mode other than a transition state's reaction coordinate, or any "
        "imaginary mode on a declared minimum, is judged by magnitude against the tolerance "
        "of the protocol that produced it."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    thresholds=(_TAU_THRESHOLD,),
    enforced_by=(
        PythonCheck(evaluate_transition_state_frequency),
        PythonCheck(evaluate_species_entry_frequency),
    ),
    escape_hatch="None; the check never refuses.",
    divergence=(
        "The disposition of each extra imaginary mode is declared by the depositor, and "
        "magnitude alone cannot tell a spurious mode from a real one that is not the reaction "
        "coordinate."
    ),
)

CHECK_TRANSITION_STATE_SOFT_REACTION_COORDINATE = ScientificCheck(
    group="Stationary points",
    sort_key=5,
    code=W_TS_IMAG_FREQ_TOO_SMALL,
    asserts=(
        "A transition state's reaction coordinate should exceed roughly "
        f"{TS_IMAGINARY_FREQUENCY_MIN_CM1:.0f} cm-1 in magnitude."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    thresholds=(
        ConstantThreshold(
            name="TS_IMAGINARY_FREQUENCY_MIN_CM1",
            value=TS_IMAGINARY_FREQUENCY_MIN_CM1,
            unit="cm-1",
            rationale=(
                "Reaction coordinates for hydrogen transfers run to thousands of cm-1, while "
                "genuinely flat barriers fall well under 100."
            ),
        ),
        _TAU_THRESHOLD,
    ),
    enforced_by=(
        PythonCheck(evaluate_transition_state_frequency),
    ),
    escape_hatch="None; the check never refuses.",
)

CHECK_FREQ_N_IMAG_AGREES_WITH_ITS_MODES = ScientificCheck(
    group="Stationary points",
    sort_key=7,
    code=W_FREQ_N_IMAG_DISAGREES_WITH_MODES,
    asserts=(
        "A frequency result that deposits both a scalar imaginary-mode count "
        "and a frequency list agrees with itself about how many imaginary "
        "modes it has."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(FreqResultPayload.validate_modes_consistency),
    ),
    escape_hatch="Omit ``modes``.",
)

CHECK_FREQ_LIST_WITHIN_GEOMETRY_DEGREES_OF_FREEDOM = ScientificCheck(
    group="Stationary points",
    sort_key=8,
    code=W_FREQ_LIST_EXCEEDS_GEOMETRY,
    asserts=(
        "A frequency list carries no more modes than the geometry it is "
        "attached to has degrees of freedom: at most ``3N`` for ``N`` atoms, "
        "the six rigid-body modes included."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(evaluate_frequency_list_completeness),
        PythonCheck(raise_for_blocking_findings),
    ),
    escape_hatch="None.",
)

CHECK_RECORDED_HESSIAN_METHOD_AGREES_WITH_THE_INPUT_DECK = ScientificCheck(
    group="Vibrational frequency provenance",
    sort_key=1,
    code=W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK,
    asserts=(
        "A calculation that records how its frequency job built the Hessian "
        "(``freq.hessian_method``) and also attaches the input deck that ran "
        "it, or an output log that echoes that input, states the same method "
        "in both."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(check_recorded_method_agrees_with_deck),
    ),
    escape_hatch="Correct whichever of the two is wrong, or omit the recorded ``freq.hessian_method``.",
)

# ---------------------------------------------------------------------------
# Atom mapping across a reaction
# ---------------------------------------------------------------------------

CHECK_ATOM_MAP_ELEMENT_CONSERVED = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=1,
    code=wire_atom_map.W_ATOM_MAP_ELEMENT_NOT_CONSERVED,
    asserts=(
        "An atom does not change element on the way across a reaction: carbon "
        "does not map onto nitrogen."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="ck_reaction_atom_map_pair_element_matches",
            kind="check",
                        definition="element = ts_element",
            rejection_code=wire_atom_map.W_ATOM_MAP_ELEMENT_NOT_CONSERVED,
            rejection_detail=(
                "An atom map pairs two atoms of different elements. An atom "
                "does not change element on the way across a reaction."
            ),
        ),
    ),
    escape_hatch="None.",
)

CHECK_ATOM_MAP_IS_A_BIJECTION = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=2,
    code=wire_atom_map.W_ATOM_MAP_NOT_A_BIJECTION,
    asserts=(
        "One saddle-point atom is claimed by exactly one atom of each leg, and "
        "one participant atom maps to exactly one saddle-point atom."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="uq_reaction_atom_map_pair_ts_atom_index",
            kind="unique",
            definition="(atom_map_id, side, ts_atom_index)",
            rejection_code=wire_atom_map.W_ATOM_MAP_NOT_A_BIJECTION,
            rejection_detail=(
                "Two atoms of one leg claim the same saddle-point atom. A map "
                "is a bijection or it is not a map."
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="uq_reaction_atom_map_pair_atom_map_id",
            kind="unique",
            definition="(atom_map_id, structure_participant_id, atom_index)",
            rejection_code=wire_atom_map.W_ATOM_MAP_NOT_A_BIJECTION,
            rejection_detail=(
                "One participant atom is mapped more than once. A map is a "
                "bijection or it is not a map."
            ),
        ),
    ),
    escape_hatch="None.",
)

CHECK_ATOM_MAP_INDICES_ARE_GEOMETRY_RELATIVE = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=3,
    code=(
        wire_atom_map.W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
        wire_atom_map.W_ATOM_MAP_PARTICIPANT_NOT_DECLARED,
        wire_atom_map.W_ATOM_MAP_WITHOUT_TRANSITION_STATE,
    ),
    asserts=(
        "Every atom index in a map names an atom of a geometry the participant owns, and a "
        "map names only participants of a reaction that has a saddle point."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="fk_reaction_atom_map_pair_geometry_id_geometry_atom",
            kind="foreign_key",
            definition=(
                "(geometry_id, atom_index, element) -> "
                "geometry_atom(geometry_id, atom_index, element)"
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom",
            kind="foreign_key",
            definition=(
                "(transition_state_geometry_id, ts_atom_index, ts_element) -> "
                "geometry_atom(geometry_id, atom_index, element)"
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="fk_reaction_atom_map_pair_structure_participant",
            kind="foreign_key",
            definition=(
                "(structure_participant_id, side) -> "
                "reaction_entry_structure_participant(id, role)"
            ),
        ),
    ),
    escape_hatch="None.",
)

CHECK_ATOM_MAP_ACCOUNTS_FOR_EVERY_ATOM = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=4,
    code=wire_atom_map.W_ATOM_MAP_ATOMS_UNACCOUNTED_FOR,
    asserts=(
        "When a map covers every declared participant of an atom-balanced "
        "reaction, both legs claim the same saddle-point atoms and no "
        "saddle-point atom is left unclaimed."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map),
    ),
    escape_hatch=(
        "Leave the map incomplete, or deposit an unbalanced reaction, which drops the rule to "
        "a warning."
    ),
)

CHECK_ATOM_MAP_PROVENANCE_IS_DECLARED = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=8,
    code=wire_atom_map.W_ATOM_MAP_INFERRED_REQUIRES_NOTE,
    asserts=(
        "An atom map records whether a human asserted it or an algorithm produced it, an "
        "inferred map names the algorithm, and neither attribution can be changed afterwards."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(wire_atom_map.ReactionAtomMapIn.validate_inferred_names_its_algorithm),
        DatabaseConstraint(
            table="reaction_atom_map",
            name="ck_reaction_atom_map_inferred_requires_note",
            kind="check",
            definition=(
                "source <> 'inferred' OR (note IS NOT NULL AND btrim(note) <> '')"
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map",
            name="trg_reaction_atom_map_source_immutable",
            kind="trigger",
            definition=(
                "BEFORE UPDATE FOR EACH ROW: refuse any change to the source column"
            ),
        ),
    ),
    escape_hatch=(
        "Edit the mutable ``note`` and ``equivalent_map_count`` fields, which do not touch "
        "the attribution."
    ),
)

# ---------------------------------------------------------------------------
# Pressure-dependent networks
# ---------------------------------------------------------------------------

#: 409 codes for the PDep constraints. These are refusal codes, not
#: :mod:`app.services.provenance_warnings` tokens.
R_ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE = "energy_transfer_scope_columns_disagree"
R_NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE = (
    "network_solve_reported_requires_literature"
)

CHECK_NETWORK_SOLVE_KIND_EVIDENCE = ScientificCheck(
    group="Pressure-dependent networks",
    sort_key=1,
    code=W_REPORTED_NETWORK_SOLVE,
    asserts=(
        "A phenomenological k(T,P) declares whether it was computed or reported: a computed "
        "solve carries master-equation evidence and a reported one cites its publication."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        DatabaseConstraint(
            table="network_solve",
            name="ck_network_solve_reported_requires_literature",
            kind="check",
            definition="kind <> 'reported' OR literature_id IS NOT NULL",
            rejection_code=R_NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE,
            rejection_detail=(
                "A network solve declared as 'reported' cites no literature. "
                "Transcribed rates must name the publication they came from, "
                "because nothing else in the deposit accounts for them."
            ),
        ),
        DatabaseConstraint(
            table="network_solve",
            name="ct_network_solve_computed_evidence",
            kind="trigger",
            definition=(
                "deferred constraint trigger, at COMMIT: a computed solve "
                "must hold at least one state energy; at least one "
                "energy-transfer model if its network declares a well; at "
                "least one channel barrier if its network declares a "
                "saddle-point path"
            ),
        ),
    ),
    escape_hatch="Declare ``kind='reported'`` and cite the literature.",
    divergence=(
        "The database guarantees a computed solve carries master-equation evidence, but "
        "state-energy, energy-transfer and barrier coverage are checked only by the upload "
        "validator."
    ),
)

CHECK_ENERGY_TRANSFER_SCOPE = ScientificCheck(
    group="Pressure-dependent networks",
    sort_key=2,
    code=W_NETWORK_WIDE_ENERGY_TRANSFER,
    asserts=(
        "A collisional energy-transfer model records whether its ⟨ΔE⟩down was "
        "determined per (well, collider) pair or declared once for the whole "
        "network."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        DatabaseConstraint(
            table="network_solve_energy_transfer",
            name="ck_network_solve_energy_transfer_scope_columns_agree",
            kind="check",
            definition=(
                "(scope='per_well' AND state_id IS NOT NULL AND "
                "collider_species_entry_id IS NOT NULL) OR "
                "(scope='network_wide' AND state_id IS NULL AND "
                "collider_species_entry_id IS NULL)"
            ),
            rejection_code=R_ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE,
            rejection_detail=(
                "A collisional energy-transfer model declares a scope its own "
                "columns contradict: a 'per_well' model must name the well and "
                "the collider it was determined for, and a 'network_wide' one "
                "must name neither."
            ),
        ),
    ),
    escape_hatch="Declare ``scope='network_wide'``.",
)

# ---------------------------------------------------------------------------
# Statistical mechanics
# ---------------------------------------------------------------------------

#: The 409 code for the statmech subject constraint.
R_STATMECH_SUBJECT_NOT_EXACTLY_ONE = "statmech_subject_not_exactly_one"

CHECK_STATMECH_HAS_EXACTLY_ONE_SUBJECT = ScientificCheck(
    group="Statistical mechanics",
    sort_key=1,
    code=R_STATMECH_SUBJECT_NOT_EXACTLY_ONE,
    asserts=(
        "A partition function belongs to exactly one subject — a species entry "
        "or a transition-state entry, never both and never neither."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.database_constraint,
    emitted=True,
    enforced_by=(
        DatabaseConstraint(
            table="statmech",
            name="ck_statmech_statmech_exactly_one_subject",
            kind="check",
            definition="(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)",
            rejection_code=R_STATMECH_SUBJECT_NOT_EXACTLY_ONE,
            rejection_detail=(
                "A partition function names both a species entry and a "
                "transition-state entry, or neither. It belongs to exactly one "
                "subject."
            ),
        ),
    ),
    escape_hatch=None,
)

# ---------------------------------------------------------------------------
# Custody of the evidence
# ---------------------------------------------------------------------------

CHECK_STORED_ARTIFACT_BYTES_MATCH_THEIR_DIGEST = ScientificCheck(
    group="Custody of the evidence",
    sort_key=1,
    code="artifact_integrity_failed",
    asserts=(
        "The bytes TCKDB serves for a stored artifact are the bytes it stored, and a record "
        "whose evidence is known not to be is labelled at read time."
    ),
    tier=CheckTier.label,
    channel=CodeChannel.trust_label,
    emitted=True,
    enforced_by=(
        PythonCheck(load_artifact_bytes),
        PythonCheck(record_integrity_observation),
        PythonCheck(detect_calculation_hard_fail),
        DatabaseConstraint(
            table="artifact_integrity_event",
            name="ck_artifact_integrity_event_observed_digest_present_iff_read",
            kind="check",
            definition=(
                "(finding = 'object_missing' AND observed_sha256 IS NULL) OR "
                "(finding <> 'object_missing' AND observed_sha256 IS NOT NULL)"
            ),
        ),
        DatabaseConstraint(
            table="artifact_integrity_event",
            name="ck_artifact_integrity_event_verified_requires_matching_digest",
            kind="check",
            definition="finding <> 'verified' OR observed_sha256 = sha256",
        ),
    ),
    escape_hatch=(
        "Restore the object, or re-upload the original bytes under the same digest so a later "
        "verification supersedes the break."
    ),
)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

CHECK_REPRODUCIBILITY_IS_ITS_OWN_JUDGEMENT = ScientificCheck(
    group="Reproducibility",
    sort_key=1,
    code=None,
    asserts=(
        "Whether a record's preserved evidence is sufficient to understand, audit or repeat "
        "it is assessed separately from its trust and from curator approval."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.none,
    emitted=False,
    enforced_by=(
        DesignPosition(
            where=(
                "``reproducibility_assessment`` rows carry ``described`` / "
                "``auditable`` / ``rerunnable`` under a versioned rubric, "
                "append-only, independent of ``record_review`` and of the "
                "trust evaluator (``app/services/trust/``)"
            )
        ),
    ),
    escape_hatch=None,
)

# ---------------------------------------------------------------------------
# Stationary points (declared here because it imports the thermo and statmech modules)
# ---------------------------------------------------------------------------

CHECK_ENERGY_SOURCE_TYPE = ScientificCheck(
    group="Stationary points",
    sort_key=13,
    code=(
        transition_state_validation.E_TS_ENERGY_ORDERING_SOURCE_TYPE_MISMATCH,
        network_energy_sources.W_NETWORK_ENERGY_SOURCE_TYPE_MISMATCH,
        W_THERMO_SOURCE_ROLE_TYPE_MISMATCH,
        statmech_resolution.W_STATMECH_SOURCE_ROLE_TYPE_MISMATCH,
    ),
    asserts=(
        "A calculation cited as the source of a stated energy is of a type that carries that "
        "kind of energy."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(transition_state_validation._assert_energy_sources_are_comparable),
        PythonCheck(network_energy_sources.assert_network_energy_source_type),
        PythonCheck(network_energy_sources.assert_network_source_role_type),
        PythonCheck(assert_thermo_role_matches_calculation_type),
        PythonCheck(statmech_resolution.assert_statmech_role_compatible),
    ),
    escape_hatch="Cite a calculation of a type that carries the energy.",
)

# ---------------------------------------------------------------------------
# Bath gas of a rate
# ---------------------------------------------------------------------------

CHECK_KINETICS_BATH_GAS_IS_A_COMPOSITION = ScientificCheck(
    group="Bath gas of a rate",
    sort_key=1,
    code=(
        W_KINETICS_BATH_GAS_MOLE_FRACTION_SUM,
        W_KINETICS_BATH_GAS_DUPLICATE_SPECIES,
        W_KINETICS_BATH_GAS_AT_HIGH_PRESSURE_LIMIT,
    ),
    asserts=(
        "A bath gas a rate or a network solve states is a composition: its mole fractions sum to one, "
        "and for a rate each species appears once and no bath gas is given at the high-pressure limit, "
        "where there is no collider."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(check_kinetics_bath_gas),
        PythonCheck(check_bath_gas_mole_fraction_sum),
    ),
    escape_hatch=(
        "Omit the bath gas (it then reads as not stated), merge repeated components into one with "
        "their total mole fraction, or state the pressure context the rate was obtained in."
    ),
    thresholds=(
        ConstantThreshold(
            name="bath_gas_mole_fraction_sum_tolerance",
            value=BATH_GAS_SUM_TOLERANCE,
            unit="absolute, on the sum",
            rationale=(
                "Fractions are stated, not measured, so a sum off by more than rounding means a "
                "component is missing or doubled."
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# Network composition and sources
# ---------------------------------------------------------------------------

CHECK_NETWORK_CHANNEL_KIND_FOLLOWS_ITS_STATES = ScientificCheck(
    group="Network composition and sources",
    sort_key=1,
    code=W_NETWORK_CHANNEL_KIND_MISMATCH,
    asserts=(
        "The kind of a network channel (isomerization, association, dissociation, exchange) is the "
        "one its source and sink states imply, so a stated kind that disagrees is refused."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(derive_channel_kind),
        PythonCheck(NetworkPDepUploadRequest.validate_channel_kind_from_states),
    ),
    escape_hatch="Omit the kind and it is derived from the states.",
)

CHECK_NETWORK_STATES_ARE_DISTINCT_COMPOSITIONS = ScientificCheck(
    group="Network composition and sources",
    sort_key=2,
    code=(
        network_pdep.W_NETWORK_STATE_DUPLICATE_COMPOSITION,
        network_pdep.W_NETWORK_STATE_DUPLICATE_PARTICIPANT,
    ),
    asserts=(
        "A network state is the multiset of its participants' species entries, a state lists a "
        "species entry once (with a stoichiometry), and a network holds one state per composition."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(network_pdep.refuse_duplicate_state_compositions),
    ),
    escape_hatch=(
        "Declare each state once, or make two states differ by stereo marks, a stereo label or a "
        "geometry on the species so they resolve to different entries."
    ),
)

# ---------------------------------------------------------------------------
# Levels and geometries of linked calculations
# ---------------------------------------------------------------------------

CHECK_DECLARED_ENERGY_LEVEL_IS_HONEST = ScientificCheck(
    group="Levels and geometries of linked calculations",
    sort_key=1,
    code=(
        calculation_levels.W_THERMO_ENERGY_LEVEL_CONTRADICTION,
        calculation_levels.W_STATMECH_ENERGY_LEVEL_CONTRADICTION,
        calculation_levels.W_KINETICS_ENERGY_LEVEL_CONTRADICTION,
        calculation_levels.W_THERMO_ENERGY_LEVEL_AMBIGUOUS,
        calculation_levels.W_STATMECH_ENERGY_LEVEL_AMBIGUOUS,
        calculation_levels.W_THERMO_ENERGY_LEVEL_REQUIRES_SP,
        calculation_levels.W_STATMECH_ENERGY_LEVEL_REQUIRES_SP,
    ),
    asserts=(
        "One record has one energy level, and the declared level matches the level of the "
        "energies linked to it (the optimisations' level when no energy is linked)."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(calculation_levels.assert_role_consistency),
        PythonCheck(calculation_levels.assert_kinetics_energy_level_consistency),
    ),
    escape_hatch=(
        "Omit the declaration (the level is then derived from the links at read time), link every "
        "energy at one level, or declare the level the linked calculations ran at."
    ),
)

CHECK_ENERGIES_AND_OPTIMISATIONS_PAIR_ONE_TO_ONE_BY_GEOMETRY = ScientificCheck(
    group="Levels and geometries of linked calculations",
    sort_key=2,
    code=(
        calculation_levels.W_THERMO_SP_GEOMETRY_MISMATCH,
        calculation_levels.W_STATMECH_SP_GEOMETRY_MISMATCH,
        calculation_levels.W_THERMO_ROLE_DUPLICATE,
        calculation_levels.W_STATMECH_ROLE_DUPLICATE,
        calculation_levels.W_THERMO_ENERGY_SP_AND_COMPOSITE_LINKED,
        calculation_levels.W_STATMECH_ENERGY_SP_AND_COMPOSITE_LINKED,
        calculation_levels.W_THERMO_ENERGY_LEVEL_REQUIRES_SP,
        calculation_levels.W_STATMECH_ENERGY_LEVEL_REQUIRES_SP,
        W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH,
        W_SCF_STABILITY_SOURCE_CONFORMER_UNANCHORED,
    ),
    asserts=(
        "Energy calculations and optimisations pair one-to-one by geometry: each energy sits on a linked "
        "optimisation's geometry, each optimisation has one energy, and an SCF stability source is on the "
        "verdict's conformer."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(calculation_levels.assert_role_consistency),
        PythonCheck(scf_source_geometry_error),
        PythonCheck(scf_source_unanchored_error),
    ),
    escape_hatch="Link the optimisation whose geometry the energy was computed at, one energy per optimisation.",
)

CHECK_CALCULATION_GEOMETRY_STATEMENTS_AGREE = ScientificCheck(
    group="Levels and geometries of linked calculations",
    sort_key=3,
    code=(
        W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH,
        calculation_resolution.W_CALCULATION_INPUT_GEOMETRY_MISMATCH,
        W_TRANSITION_STATE_CALCULATION_SPECIES_GEOMETRY,
    ),
    asserts=(
        "A calculation names one conformer and one input geometry: its conformer key and "
        "geometry key agree, and a transition-state calculation does not run on a species "
        "minimum's geometry."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(assert_calculation_geometry_matches_conformer),
        PythonCheck(calculation_resolution.attach_calculation_input_geometries),
        PythonCheck(assert_transition_state_calculations_unanchored),
    ),
    escape_hatch="State one conformer, or omit the redundant key; list the geometry the calculation ran on.",
)

# ---------------------------------------------------------------------------
# Composite and single-point arithmetic
# ---------------------------------------------------------------------------

CHECK_COMPOSITE_STATED_NUMBERS_AGREE = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=1,
    code=(COMPOSITE_E0_INCONSISTENT, COMPOSITE_TERMS_DO_NOT_SUM, COMPOSITE_TOTAL_MISMATCH),
    asserts="The numbers a composite energy states agree with each other and with its recipe.",
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(assert_composite_result_arithmetic),
        PythonCheck(assert_composite_total_recomputes),
    ),
    escape_hatch="Send the values the program printed, or omit the number you did not read.",
    thresholds=(
        ConstantThreshold(
            name="composite_arithmetic_tolerance",
            value=COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
            unit="hartree (floor; 5e-7 per rounded quantity above two)",
            rationale=(
                "An equation among n numbers printed to six decimals can differ by n half-units of "
                "the last place without anything being wrong."
            ),
        ),
        ConstantThreshold(
            name="composite_total_recomputation_tolerance",
            value=COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
            unit="hartree (floor; 5e-7 times 1 + the summed sensitivity weights)",
            rationale=(
                "The tolerance scales with how strongly each stored input is weighted in the "
                "recomputed total, so inputs printed to six decimals stay inside it."
            ),
        ),
    ),
)

CHECK_COMPOSITE_INPUTS_ARE_AT_THE_SLOT_LEVEL_AND_ONE_GEOMETRY = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=2,
    code=(
        composite_input_resolution.COMPOSITE_INPUT_LEVEL_MISMATCH,
        composite_input_resolution.COMPOSITE_INPUT_GEOMETRY_MISMATCH,
    ),
    asserts=(
        "Each input of an assembled composite ran at the level of theory its slot in the scheme names, "
        "and the inputs and the composite itself declare one geometry between them."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(composite_input_resolution._finalize_one),
    ),
    escape_hatch="Cite the calculation at the slot's level, all computed at the one geometry.",
)

CHECK_SP_ENERGY_COMPONENTS_ADD_UP = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=3,
    code=(SP_ENERGY_COMPONENT_TOTAL_MISMATCH, SP_ENERGY_COMPONENTS_DO_NOT_SUM, SP_ENERGY_COMPONENT_DUPLICATE),
    asserts="The parts of a single point's electronic energy agree with the energy they are parts of.",
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(PythonCheck(check_sp_energy_components),),
    escape_hatch="Send the parts the program printed with its energy, or omit the components.",
    thresholds=(
        ConstantThreshold(
            name="sp_energy_component_sum_tolerance",
            value=SUM_TOLERANCE_HARTREE,
            unit="hartree",
            rationale=(
                "Tight enough to catch a part from a different run, loose enough for the digits a "
                "program prints."
            ),
        ),
    ),
)

CHECK_COMPOSITE_FREQUENCY_IS_AT_THE_RECIPE_LEVEL = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=4,
    code=(
        calculation_levels.W_COMPOSITE_FREQUENCY_LEVEL_DIFFERS,
        calculation_levels.W_COMPOSITE_ROLE_ON_NON_COMPOSITE,
    ),
    asserts=(
        "A frequency calculation linked beside a composite runs at the level of theory the "
        "composite's scheme prescribes for frequencies, and the composite role is given only "
        "to a calculation of type composite."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(PythonCheck(calculation_levels.collect_composite_link_warnings),),
    escape_hatch="The link is stored as stated; link the frequency calculation at the recipe's level to clear the warning.",
)

CHECK_NAMED_COMPOSITE_IS_A_COMPOSITE_CALCULATION = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=5,
    code=(
        composite_result_resolution.W_NAMED_COMPOSITE_DEPOSITED_AS_OPT,
        composite_result_resolution.W_NAMED_COMPOSITE_DEPOSITED_AS_SP,
    ),
    asserts=(
        "A named composite method (CBS-QB3, G4, W1U) is one program run and is deposited as a "
        "calculation of type composite, not as an optimisation or a single point at its level."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(composite_result_resolution.collect_named_composite_deposit_warnings),
    ),
    escape_hatch="The deposit is accepted; send it as type composite with a composite_result block to clear the warning.",
)

CHECK_COMPOSITE_INPUTS_AND_TOTAL_CAN_BE_CHECKED = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=6,
    code=(
        composite_input_resolution.W_COMPOSITE_INPUT_GEOMETRY_UNDECLARED,
        composite_input_resolution.W_COMPOSITE_TOTAL_UNVERIFIABLE,
    ),
    asserts=(
        "An assembled composite's inputs each declare their geometry, and the deposited total "
        "can be recomputed from them."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(composite_input_resolution._unverifiable_warning),
    ),
    escape_hatch="The deposit is accepted; declare each input's geometry and state the energies used to clear the warning.",
)

# ---------------------------------------------------------------------------
# Subject of a record
# ---------------------------------------------------------------------------

CHECK_A_CITED_THING_IS_OF_THE_RECORDS_OWN_SUBJECT = ScientificCheck(
    group="Subject of a record",
    sort_key=1,
    code=(
        network_energy_sources.W_NETWORK_ENERGY_SOURCE_SUBJECT_MISMATCH,
        calculation_ownership.W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
        calculation_ownership.W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
        W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
        W_SCF_STABILITY_SOURCE_CALCULATION_OWNER_MISMATCH,
        calculation_ownership.W_COMPOSITE_INPUT_OWNER_MISMATCH,
        calculation_ownership.W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH,
        calculation_ownership.W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH,
        calculation_ownership.W_THERMO_STATMECH_OWNER_MISMATCH,
    ),
    asserts="What a record cites as evidence belongs to the record's own subject.",
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(calculation_ownership.assert_owned_by),
        PythonCheck(owner_mismatch_error),
        PythonCheck(network_energy_sources.assert_state_energy_source_owner),
        PythonCheck(network_energy_sources.assert_barrier_source_owner),
        PythonCheck(network_energy_sources.assert_network_role_source_owner),
        PythonCheck(network_energy_sources.assert_state_participant_source_owner),
    ),
    escape_hatch="Cite the calculation or statmech record of the subject the record is about.",
)

# ---------------------------------------------------------------------------
# Declarations against the record
# ---------------------------------------------------------------------------

CHECK_KINETICS_DECLARATION_DOES_NOT_CONTRADICT_THE_RECORD = ScientificCheck(
    group="Declarations against the record",
    sort_key=1,
    code=W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
    asserts=(
        "A kinetics applicability or protocol declaration does not contradict the columns the record "
        "already states (direction, pressure context, bath gas, scientific origin): the stored columns "
        "stay authoritative."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(kinetics_applicability_error),
        PythonCheck(kinetics_protocol_origin_error),
        PythonCheck(kinetics_declaration_resolution.assert_kinetics_declaration_columns),
    ),
    escape_hatch="Correct whichever statement is wrong, or omit the declaration.",
)

CHECK_DECLARATION_IS_ABOUT_THE_RECORD_THAT_CARRIES_IT = ScientificCheck(
    group="Declarations against the record",
    sort_key=2,
    code=(
        kinetics_declaration_resolution.W_KINETICS_DETERMINATION_MISMATCH,
        statmech_declaration_resolution.W_STATMECH_MODEL_MISMATCH,
        structure_determination_resolution.W_STRUCTURE_DETERMINATION_MISMATCH,
        kinetics_declaration_resolution.W_KINETICS_PROTOCOL_CALCULATION_OWNER_MISMATCH,
        thermo_declaration_resolution.W_THERMO_PROTOCOL_CALCULATION_OWNER_MISMATCH,
        calculation_ownership.W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH,
        calculation_ownership.W_KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH,
    ),
    asserts=(
        "A determination, statistical-model, structure, protocol or interpretation "
        "declaration describes the record that carries it."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(kinetics_declaration_resolution._mismatch),
        PythonCheck(thermo_declaration_resolution._resolve_protocol),
        PythonCheck(statmech_declaration_resolution._mismatch),
        PythonCheck(structure_determination_resolution._mismatch),
        PythonCheck(calculation_ownership.assert_statmech_owned_by),
    ),
    escape_hatch="Declare it on the record it describes, with the calculations and sources of that record.",
)

# ---------------------------------------------------------------------------
# Observation identity
# ---------------------------------------------------------------------------

CHECK_OBSERVATION_IS_ATTACHED_TO_ITS_OWN_GROUND_STATE = ScientificCheck(
    group="Observation identity",
    sort_key=1,
    code=(
        observation_identity_attach.W_OBSERVATION_IDENTITY_TARGET_NOT_GROUND_STATE_MINIMUM,
        observation_identity_attach.W_OBSERVATION_IDENTITY_HINT_CONFLICT,
    ),
    asserts=(
        "A curator attaches an observation of unresolved identity only to a ground-state, minimum-energy "
        "entry of a species whose connectivity agrees with the observation's own identity hint."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(observation_identity_attach._assert_ground_state_minimum_entry),
        PythonCheck(observation_identity_attach._assert_no_identity_hint_conflict),
    ),
    escape_hatch="Attach it to the ground-state minimum entry of the species it was measured on.",
)

# ---------------------------------------------------------------------------
# Corrections and scale factors
# ---------------------------------------------------------------------------

CHECK_A_BOND_ADDITIVITY_TOTAL_HAS_ITS_COMPONENTS = ScientificCheck(
    group="Corrections and scale factors",
    sort_key=1,
    code=energy_correction_resolution.W_BAC_TOTAL_REQUIRES_COMPONENTS,
    asserts=(
        "A Petersson bond-additivity correction applied as a total carries at least one bond component, "
        "unless its target species has no bonds; a transition state has no bond assignment and so cannot "
        "take a componentless total."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(PythonCheck(energy_correction_resolution.assert_bac_total_has_required_components),),
    escape_hatch="Send the bond components, or apply the correction under another role.",
)

CHECK_A_FREQUENCY_SCALE_FACTOR_IS_OF_THE_PROGRAM_THAT_RAN = ScientificCheck(
    group="Corrections and scale factors",
    sort_key=2,
    code=statmech_resolution.W_STATMECH_FSF_SOFTWARE_MISMATCH,
    asserts=(
        "The frequency scale factor a statmech record applies was derived for the program that ran the "
        "frequency calculation it is applied to."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(statmech_resolution.collect_frequency_scale_factor_software_mismatch_warnings),
    ),
    escape_hatch="The deposit is accepted; confirm the choice, or attach a factor derived for that program.",
)

# ---------------------------------------------------------------------------
# Enthalpy reference
# ---------------------------------------------------------------------------

CHECK_ENTHALPY_STATES_ITS_REFERENCE = ScientificCheck(
    group="Enthalpy reference",
    sort_key=1,
    code=(
        W_ENTHALPY_DECLARATION_ABSENT,
        W_ENTHALPY_REFERENCE_KIND_UNRECOGNIZED,
        W_ENTHALPY_DECLARATION_WITHOUT_CONTENT,
        W_ENTHALPY_QUANTITY_NOT_STORABLE_HERE,
    ),
    asserts=(
        "A thermo record that carries an enthalpy declares that it is the standard enthalpy "
        "of formation at 298.15 K, the only accepted reference, and a record without enthalpy "
        "content declares none."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(enthalpy_reference_error),
        PythonCheck(assert_enthalpy_reference),
    ),
    escape_hatch=(
        "Declare ``formation_298k`` when the source states that convention, or deposit increments and "
        "absolute enthalpies through the molecular property observation route."
    ),
)

# ---------------------------------------------------------------------------
# Calculation graph
# ---------------------------------------------------------------------------

CHECK_CALCULATION_DEPENDENCIES_ARE_ACYCLIC = ScientificCheck(
    group="Calculation graph",
    sort_key=1,
    code="calculation_dependency_cycle",
    asserts=(
        "The graph of which calculation depends on which is acyclic: no calculation depends on itself "
        "through a chain of others."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.database_constraint,
    enforced_by=(
        DatabaseConstraint(
            table="calculation_dependency",
            name="trg_calculation_dependency_acyclic",
            kind="trigger",
            definition=(
                "BEFORE INSERT OR UPDATE FOR EACH ROW: refuse an edge whose parent is already reachable "
                "from its child"
            ),
        ),
        DatabaseConstraint(
            table="calculation_dependency",
            name="ck_calculation_dependency_not_self",
            kind="check",
            definition="parent_calculation_id <> child_calculation_id",
        ),
    ),
    escape_hatch="Drop or reverse the dependency that points back.",
)

CHECK_STABILITY_VERDICTS_DO_NOT_CITE_EACH_OTHER_IN_A_RING = ScientificCheck(
    group="Calculation graph",
    sort_key=2,
    code=W_SCF_STABILITY_SOURCE_CYCLE,
    asserts=(
        "SCF stability verdicts do not cite one another as the job that measured them in a ring: some job "
        "measured each."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(PythonCheck(scf_source_cycle_error),),
    escape_hatch="Name, as the measuring job, a calculation that is not itself measured by the first.",
)

# ---------------------------------------------------------------------------
# Energy comparison
# ---------------------------------------------------------------------------

CHECK_ENERGIES_AT_DIFFERENT_LEVELS_ARE_NOT_COMPARED = ScientificCheck(
    group="Energy comparison",
    sort_key=1,
    code=(
        "lowest_energy_across_levels",
        "unsafe_lowest_energy_comparison",
        "relative_conformer_energy_compare_identical_protocols",
    ),
    asserts=(
        "A lowest-energy selection compares energies of one level and one species entry, and a "
        "relative-energy comparison names two different protocols."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(lookup_routes._refuse_mixed_levels),
        PythonCheck(species_calculations_search._validate_ranking),
        PythonCheck(relative_conformer_energy_compare.identical_protocols),
    ),
    escape_hatch="Filter to one level (method and basis) and one species entry, or use a select endpoint.",
)

# ---------------------------------------------------------------------------
# Evidence a result rests on
# ---------------------------------------------------------------------------

CHECK_A_CONVERGED_OPTIMISATION_HAS_AN_ENERGY_BEHIND_IT = ScientificCheck(
    group="Evidence a result rests on",
    sort_key=1,
    code=calculation_resolution.W_CONVERGED_OPT_NO_USABLE_ENERGY,
    asserts=(
        "An optimisation recorded as converged has an energy behind it: a single point of the same owner "
        "at any level, or its own final energy stated with an artifact."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(PythonCheck(calculation_resolution.collect_converged_opt_energy_warnings),),
    escape_hatch="The deposit is accepted; deposit a single point, or the optimisation's final energy with its artifact.",
)

# ---------------------------------------------------------------------------
# Declared against the uploaded evidence (warnings)
# ---------------------------------------------------------------------------

CHECK_TYPED_SETTINGS_AGREE_WITH_THE_ARTIFACTS = ScientificCheck(
    group="Declared against the uploaded evidence",
    sort_key=1,
    code=(
        parameter_reconciliation.W_PARAMETER_DISAGREES_WITH_ARTIFACT,
        parameter_reconciliation.W_INPUT_DECK_DISAGREES_WITH_OUTPUT_LOG,
        parameter_reconciliation.W_OUTPUT_LOG_DISAGREES_WITH_EARLIER_OUTPUT_LOG,
        parameter_reconciliation.W_REPORTED_SETTING_DIFFERS_FROM_REQUESTED,
        parameter_reconciliation.W_LEVEL_OF_THEORY_DISAGREES_WITH_ARTIFACT,
    ),
    asserts=(
        "What a depositor typed about a calculation's settings and level of theory agrees "
        "with the uploaded input deck and output log, the deck agrees with the log, a later "
        "log agrees with an earlier one, and a setting a log reports agrees with the value "
        "requested."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(parameter_reconciliation.declared_parameter_warnings),
        PythonCheck(parameter_reconciliation.merge_artifact_observations),
        PythonCheck(parameter_reconciliation.level_of_theory_warnings),
        PythonCheck(parameter_reconciliation.level_of_theory_qualifier_warnings),
        PythonCheck(parameter_reconciliation.reported_setting_warnings),
    ),
    escape_hatch="The deposit is accepted; correct the typed value, or attach the right deck.",
)

CHECK_STATED_ENERGY_AGREES_WITH_THE_LOG = ScientificCheck(
    group="Declared against the uploaded evidence",
    sort_key=2,
    code=(
        sp_energy_reconciliation.W_SP_ENERGY_MISMATCH,
        composite_energy_reconciliation.W_COMPOSITE_ENERGY_LOG_MISMATCH,
        composite_energy_reconciliation.W_COMPOSITE_LOG_METHOD_MISMATCH,
    ),
    asserts=(
        "The electronic, E0 and zero-point energies a tool states agree with the output log "
        "attached to the same calculation, and the log is the composite method the level of "
        "theory names."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(sp_energy_reconciliation.reconcile_sp_energy),
        PythonCheck(composite_energy_reconciliation.reconcile_composite_energy),
    ),
    escape_hatch="The deposit is accepted; send the value the log prints, or omit it and let the log speak.",
    thresholds=(
        ConstantThreshold(
            name="composite_log_tolerance",
            value=COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
            unit="hartree (max(1e-6, 5e-7 * n), n the rounded quantities compared)",
            rationale=(
                "Each log number is printed to six decimals, so a copy can differ by half-units of "
                "the last place of each term."
            ),
        ),
        ConstantThreshold(
            name="sp_energy_log_tolerance",
            value=sp_energy_reconciliation.SP_ENERGY_ABS_TOL_HARTREE,
            unit="hartree",
            rationale=(
                "Tight enough that a wrong line, wrong method or unit slip lies far outside it, loose "
                "enough for reduced stored precision."
            ),
        ),
    ),
)

CHECK_HESSIAN_METHOD_OF_A_LATE_ARTIFACT_AGREES_WITH_THE_STORED_BASIS = ScientificCheck(
    group="Declared against the uploaded evidence",
    sort_key=3,
    code=hessian_method_deck_check.W_HESSIAN_METHOD_DISAGREES_WITH_STORED_TAU_BASIS,
    asserts=(
        "The Hessian method a deck or log attached after the deposit states is the method the "
        "calculation's stored imaginary-mode tolerance rests on."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(PythonCheck(hessian_method_deck_check.try_hessian_method_disagreement_from_input_upload),),
    escape_hatch="None; the record is annotated, not refused.",
)

CHECK_ATOMIC_ELECTRONIC_LEVELS_AGREE_WITH_THE_GROUND_TERM = ScientificCheck(
    group="Declared against the uploaded evidence",
    sort_key=4,
    code=(
        atomic_electronic_warnings.W_ATOMIC_ELECTRONIC_DEGENERACY_CONTRADICTS_TERM,
        upload_reconciliation.W_TERM_SYMBOL_CONTRADICTS_GROUND_TERM,
    ),
    asserts=(
        "A neutral ground-state atom's term symbol and lowest-level degeneracy agree with the "
        "NIST ground term of its element."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(atomic_electronic_warnings._degeneracy_warnings),
        PythonCheck(upload_reconciliation.term_symbol_warnings),
    ),
    escape_hatch="The deposit is accepted; deposit the levels that match the declared term.",
)

CHECK_SCF_STABILITY_SOURCE_IS_COMPARABLE = ScientificCheck(
    group="Declared against the uploaded evidence",
    sort_key=5,
    code=(
        scf_stability_sources.W_SCF_STABILITY_SOURCE_LEVEL_MISMATCH,
        scf_stability_sources.W_SCF_STABILITY_SOURCE_CONFORMER_UNVERIFIED,
    ),
    asserts=(
        "The job named as having measured an SCF stability verdict ran at the level of theory of the "
        "calculation carrying the verdict, and the two can be shown to be on one conformer."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(PythonCheck(scf_stability_sources.link_scf_stability_sources),),
    escape_hatch="None; the link is stored as stated.",
)

# ---------------------------------------------------------------------------
# Thermo declarations
# ---------------------------------------------------------------------------

CHECK_A_FORMAT_DEFINED_THERMO_DECLARATION_DESCRIBES_WHAT_THE_FORMAT_DEFINES = ScientificCheck(
    group="Thermo declarations",
    sort_key=1,
    code=(W_THERMO_DECLARED_BY_TARGET_INCOHERENT, W_THERMO_DECLARED_BY_RECORD_INCOHERENT),
    asserts=(
        "A thermo declaration the file format defines describes what the format defines: a target declared "
        "by the CHEMKIN format is an equilibrium ensemble, on a gas-phase record that carries a NASA-7 block."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(thermo_target_error),
        PythonCheck(_declared_by_record_error),
    ),
    escape_hatch="Declare the target as depositor-defined, or send a gas-phase record with its NASA-7 block.",
)

# ---------------------------------------------------------------------------
# Hessian method of a stored tolerance
# ---------------------------------------------------------------------------

CHECK_A_RECORDED_HESSIAN_METHOD_IS_REFINED_WHEN_IT_CANNOT_HOLD = ScientificCheck(
    group="Hessian method of a stored tolerance",
    sort_key=1,
    code=W_HESSIAN_METHOD_REFINED,
    asserts=(
        "A recorded finite difference of gradients on an ORCA NumFreq deck, for a method with no analytic "
        "gradient, is stored as finite differences of energies, and the record says so."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(PythonCheck(_refinement_findings),),
    escape_hatch="The deposit is accepted; record finite_difference_energy to state it directly.",
)

# ---------------------------------------------------------------------------
# One-electron participants
# ---------------------------------------------------------------------------

CHECK_A_ONE_ELECTRON_HARTREE_FOCK_ENERGY_STANDS_FOR_THE_CORRELATED_LEVEL = ScientificCheck(
    group="One-electron participants",
    sort_key=1,
    code=calculation_levels.W_ONE_ELECTRON_HF_SUBSTITUTION,
    asserts=(
        "A kinetics record's declared correlated energy level is accepted for a one-electron "
        "participant's Hartree-Fock energy in the same basis, because with one electron every "
        "correlation term is zero."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(calculation_levels.kinetics_one_electron_hf_substitutions),
        PythonCheck(calculation_levels.is_one_electron_hf_substitute),
    ),
    escape_hatch="The deposit is accepted; link a correlated energy to state a level without the substitution.",
)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

#: Every module holding :class:`ScientificCheck` declarations. A guard test
#: fails if a file that constructs one is not listed here.
DECLARING_MODULES: tuple[ModuleType, ...] = (
    calc_isotopes,
    chemistry_species,
    chemistry_stereo,
    chemistry_units,
    charge_multiplicity_reconciliation,
    external_comparison_checks,
    frequency_geometry_linearity,
    geometry_validation_service,
    reaction_atom_map_service,
    reaction_resolution,
    scan_coordinate_conformance,
    species_resolution,
    transition_state_validation,
    wavefunction_reliability_warnings,
    sys.modules[__name__],
)


def register() -> list[ScientificCheck]:
    """Return the whole scientific check register, deterministically ordered."""
    return collect_registered_checks(DECLARING_MODULES)


def constraint_rejections() -> dict[str, tuple[str, str]]:
    """Constraint name to the ``(code, detail)`` a 409 body should carry.

    Consumed by ``app.api.errors._integrity_error_handler``.
    """
    return collect_constraint_rejections(register())


__all__ = ["DECLARING_MODULES", "constraint_rejections", "register"]
