"""Register entries for scientific codes the catalogue and the warning map carry.

Each entry holds the real function that enforces the check, so renaming or deleting one breaks the
import of this module. The drift guard in ``tests/db/test_scientific_check_register.py`` checks every code
against the source of the module that defines the enforcing function.

Codes that are the same check as an existing entry sit in that entry's ``code`` tuple (entries 10, 12, 29
and the Arrhenius ``A`` units entry). Groups are appended to ``numbering.READING_ORDER``, so no published
entry is renumbered. Every code not claimed by the register is in ``not_scientific.NOT_SCIENTIFIC`` with a
reason; a test fails when a code is in neither, or in both.
"""

from __future__ import annotations

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
from tckdb_schemas.fragments.calculation import (
    COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
    COMPOSITE_E0_INCONSISTENT,
    COMPOSITE_TERMS_DO_NOT_SUM,
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
from tckdb_schemas.stationary_point import W_HESSIAN_METHOD_REFINED, _refinement_findings
from tckdb_schemas.thermo_declarations import (
    W_THERMO_DECLARED_BY_RECORD_INCOHERENT,
    W_THERMO_DECLARED_BY_TARGET_INCOHERENT,
    _declared_by_record_error,
    thermo_target_error,
)

from app.api.routes import lookup as lookup_routes
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    DatabaseConstraint,
    PythonCheck,
    ScientificCheck,
)
from app.services import (
    atomic_electronic_warnings,
    calculation_levels,
    calculation_ownership,
    calculation_resolution,
    composite_energy_reconciliation,
    composite_input_resolution,
    composite_result_resolution,
    energy_correction_resolution,
    hessian_method_deck_check,
    kinetics_declaration_resolution,
    network_energy_sources,
    observation_identity_attach,
    parameter_reconciliation,
    scf_stability_sources,
    sp_energy_reconciliation,
    statmech_declaration_resolution,
    statmech_resolution,
    structure_determination_resolution,
    thermo_declaration_resolution,
    transition_state_validation,
    upload_reconciliation,
)
from app.services.relative_conformer_energy import compare as relative_conformer_energy_compare
from app.services.scientific_read import species_calculations_search
from app.workflows import network_pdep
from app.workflows.thermo import (
    W_THERMO_SOURCE_ROLE_TYPE_MISMATCH,
    assert_enthalpy_reference,
    assert_thermo_role_matches_calculation_type,
)

# ---------------------------------------------------------------------------
# Stationary points (entry 26 is declared here: it needs the thermo and statmech
# modules, which import the module that declares the other entries of its group)
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
        "A calculation cited as the source of a stated energy should be of a "
        "type that carries that kind of energy: an electronic energy from an "
        "``sp``, ``opt`` or ``composite``, an E0 from a ``freq`` or "
        "``composite``, never from an IRC point, scan, path search or "
        "conformer search; a network ``well_freq`` or ``barrier_freq`` "
        "source must be a ``freq``; and a thermo or statmech source link "
        "gives a calculation a role its type can take."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. An energy taken from an IRC point, or from a bare "
        "single point labelled as an E0, is not the quantity it is labelled "
        "as, and an ordering or a master equation built on it compares unlike "
        "things; the master equation reads the modes of its frequency roles, "
        "which a ``composite`` (a ZPE only) does not store. The table of types "
        "is one table shared by the transition-state and network rules "
        "(``app.services.energy_source_types``), so the two cannot "
        "disagree about what carries an energy; it is a position a referee "
        "could dispute, which is why it is registered. A network energy on a "
        "composed convention (``electronic_plus_zpe`` and the like) is held "
        "only to the floor of stationary-point types, because its one source "
        "slot cites the electronic part and the frequency goes in the "
        "frequency role: tightening it would refuse every producer and "
        "fixture in the repository. The thermo and statmech role tables "
        "(``opt``, ``freq``, ``sp`` and, for statmech, ``composite``) are "
        "the same rule for the products' own links."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            transition_state_validation._assert_energy_sources_are_comparable,
            note=(
                "The transition-state ``energy_ordering`` evidence, at the "
                "shared evidence seam, so all three deposit paths refuse alike."
            ),
        ),
        PythonCheck(
            network_energy_sources.assert_network_energy_source_type,
            note="Network state and barrier energy sources, read off the persisted row.",
        ),
        PythonCheck(
            network_energy_sources.assert_network_source_role_type,
            note=(
                "The ``well_energy`` / ``barrier_energy`` and the frequency "
                "roles of the solve's ``source_calculations``."
            ),
        ),
        PythonCheck(assert_thermo_role_matches_calculation_type),
        PythonCheck(statmech_resolution.assert_statmech_role_compatible),
    ),
    escape_hatch=(
        "Cite the calculation that carries the energy: the ``sp``, ``opt`` or "
        "``composite`` for an electronic energy, the ``freq`` (or a "
        "``composite``) for an E0, and put the ``freq`` in the "
        "``well_freq`` / ``barrier_freq`` role."
    ),
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
    tier_rationale=(
        "Definitional. A mole-fraction set that does not sum to one, or that names one species "
        "twice, is not a composition, and no correct measurement or calculation can have been "
        "obtained in it. The high-pressure limit is the rate with collisional energy transfer "
        "removed, so a bath gas attached to it contradicts the pressure context the same record "
        "states. A network solve's bath gas is held to the same sum, with the same tolerance. All "
        "three are contradictions inside one payload, not expectations about a "
        "result. Nothing is refused for an absent bath gas: it reads as 'not stated' and is "
        "never inferred, which is why the check is a block and not a demand."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            check_kinetics_bath_gas,
            note=(
                "Shared by the kinetics upload and the computed-reaction bundle, so the two routes "
                "cannot disagree about what a valid bath gas is."
            ),
        ),
        PythonCheck(
            check_bath_gas_mole_fraction_sum,
            note="The sum rule alone, which the network solve's bath gas also calls.",
        ),
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
                "Tight on purpose: fractions are stated, not measured, so a sum that is off by more "
                "than rounding means a component is missing or doubled."
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
    tier_rationale=(
        "Definitional. Whether a channel is an association or a dissociation follows from whether "
        "each end is a well or a set of separated fragments, by one rule applied to every pair "
        "(``derive_channel_kind``). A stated kind is only ever a claim to check, and one that "
        "disagrees contradicts the states the same payload declares. No correct network can "
        "contain it, and storing it would let two readers disagree about the direction of "
        "the same channel."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            derive_channel_kind,
            note="The one place the rule lives; the upload validator and the persisting workflow both call it.",
        ),
        PythonCheck(
            NetworkPDepUploadRequest.validate_channel_kind_from_states,
            note="Fills an omitted kind and refuses a stated one that differs; the workflow repeats the comparison.",
        ),
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
    tier_rationale=(
        "Definitional. A well or a separated-fragment set is identified by what it is made of; two "
        "states with the same composition are one state declared twice, and a state naming one entry "
        "twice states a stoichiometry the wrong way. Both are refused before any row is written. Stereoisomers are different species entries, so a "
        "network that really holds two isomers is not refused."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            network_pdep.refuse_duplicate_state_compositions,
            note="Runs before any state row is written and names the depositor's local state keys.",
        ),
    ),
    escape_hatch=(
        "Declare each state once. If two states are meant to differ, say how (stereo marks, a stereo "
        "label or a geometry on the species) so they resolve to different entries."
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
        "One record has one energy level and the declared level matches it: the linked single points or "
        "composites share one level, which the declaration names (the optimisations' when none is linked); "
        "for kinetics, each linked energy, modulo spin treatment per participant."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "A contract between statements of one record, not an expectation about a result. A "
        "depositor may legitimately run the optimisation and the single point at different levels "
        "(a legitimate shape), which is why the record must say which level its energy "
        "stands at, once: linked energies at two levels give no single answer (``*_energy_level_"
        "ambiguous``), a declared level that contradicts the linked energies is a false label "
        "(``*_energy_level_contradiction``), and a declared level that differs from the "
        "optimisation's with no energy calculation at it linked has nothing behind it "
        "(``*_energy_level_requires_sp``, the declared-level branch; its coverage branch is entry 57). "
        "No re-run decides which statement was meant. Kinetics is compared per participant and "
        "modulo spin treatment, so a restricted and an unrestricted calculation of closed-shell "
        "species do not contradict each other."
    ),
    adr="0008, 0021",
    enforced_by=(
        PythonCheck(
            calculation_levels.assert_role_consistency,
            note="Thermo and statmech, on every upload shape; the codes arrive as arguments, one set per product.",
        ),
        PythonCheck(
            calculation_levels.assert_kinetics_energy_level_consistency,
            note="Kinetics, by ``kinetics_energy_level_violation``.",
        ),
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
    tier_rationale=(
        "Definitional. A single-point energy belongs to the structure it was computed at; paired with "
        "another conformer's optimisation it is a different molecule's energy, two energies on one "
        "optimisation (or a single point and a composite) are two answers to one question, and an "
        "ensemble that gives some conformers a refined energy and not others is the 'forgot the single "
        "point' deposit. A stability analysis describes one wavefunction at one geometry. For thermo and "
        "statmech silence is kept where either side declares no geometry (absence of evidence is not "
        "evidence of a mismatch, and with at most one optimisation an energy with no geometry is "
        "assumed to belong to it). The stability half is stricter: a verdict and its source that are "
        "tied to different conformers are refused here, exactly one of them unanchored is refused "
        "(``scf_stability_source_conformer_unanchored``), and only when neither is tied to any "
        "conformer is it the warning of entry 80."
    ),
    adr="0008, 0021",
    enforced_by=(
        PythonCheck(
            calculation_levels.assert_role_consistency,
            note="Rules R2' (distinctness), R3' (geometry) and Coverage, run once per product with that product's codes.",
        ),
        PythonCheck(
            scf_source_geometry_error,
            note="Builds the stability-source refusal for both the request schema and the workflow (ADR 0017).",
        ),
        PythonCheck(scf_source_unanchored_error, note="Exactly one of verdict and source is tied to a conformer."),
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
        "A calculation is evidence for one conformer and ran on one structure: its conformer key and "
        "its geometry key name the same conformer, and the geometry it states it ran on is one of "
        "its input geometries; a transition-state calculation did not run on a species minimum's geometry."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "A contract between two statements of one calculation. Neither key overrides the other and "
        "neither can be chosen for the depositor, so storing both would attribute the same numbers "
        "to two conformers. A freq or single point runs on one structure; a stated geometry outside "
        "its own input list is a contradiction, not an unusual result."
    ),
    adr="0008, 0017",
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
    asserts=(
        "The numbers a composite energy states agree with each other and with its recipe: E0 is the "
        "electronic energy plus the recipe's zero-point energy, the stated terms sum to the "
        "electronic energy, and an assembled composite's total is what its scheme gives for the "
        "energies of its inputs."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, and a comparison only: TCKDB forms the sums to compare them with the numbers "
        "sent and stores none of them. A total that does not follow from the "
        "inputs it cites contradicts its own recipe; an E0 that is not electronic plus zero-point "
        "contradicts what the three quantities are. The tolerance scales with the number of rounded "
        "quantities in the comparison, so printed precision never trips it."
    ),
    adr="0008, 0021",
    enforced_by=(
        PythonCheck(
            assert_composite_result_arithmetic,
            note="Run by the wire model on parse and again by the backend on persist, so the tolerance cannot differ.",
        ),
        PythonCheck(assert_composite_total_recomputes),
    ),
    escape_hatch="Send the values the program printed, or omit the number you did not read.",
    thresholds=(
        ConstantThreshold(
            name="composite_arithmetic_tolerance",
            value=COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
            unit="hartree (floor; 5e-7 per rounded quantity above two)",
            rationale=(
                "An equation among n numbers printed to six decimals can disagree by n half-units of the "
                "last place without anything being wrong; the floor protects short equations."
            ),
        ),
        ConstantThreshold(
            name="composite_total_recomputation_tolerance",
            value=COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
            unit="hartree (floor; 5e-7 times 1 + the summed sensitivity weights)",
            rationale=(
                "The recomputed total is max(1e-6, 5e-7 * (1 + sum_i |d total / d x_i|)) hartree over every "
                "stored number the recomputation consumed: a larger-basis energy enters an inverse-power "
                "extrapolation with a weight above 1, so a total computed from inputs printed to six "
                "decimals stays inside the tolerance it earns. The deposited total counts as one number "
                "of weight 1."
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
    tier_rationale=(
        "Definitional. A recipe that sums energies taken at different geometries describes no single "
        "molecule, and a slot filled by a calculation at another level is a different recipe from "
        "the one the scheme names. Spellings of one level are followed through merges, so only a "
        "genuinely different level refuses. An input that declares no geometry is a warning, not "
        "this refusal: absence of a geometry does not show a mismatch."
    ),
    adr="0008, 0021",
    enforced_by=(
        PythonCheck(composite_input_resolution._finalize_one, note="Also runs the one-geometry rule (``_check_geometries``)."),
    ),
    escape_hatch="Cite the calculation at the slot's level, all computed at the one geometry.",
)

CHECK_SP_ENERGY_COMPONENTS_ADD_UP = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=3,
    code=(SP_ENERGY_COMPONENT_TOTAL_MISMATCH, SP_ENERGY_COMPONENTS_DO_NOT_SUM, SP_ENERGY_COMPONENT_DUPLICATE),
    asserts=(
        "The parts of a single point's electronic energy do not contradict the energy they are parts "
        "of: each component is stated once, a stated total is the energy, and reference plus correlation "
        "(plus triples, where the program prints it separately) is the energy."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A composite level consumes these parts, so a part from a different run than the "
        "energy poisons every number built on it. A component stated twice has two values and no "
        "way to say which is the energy's part (the same shape as a bath-gas species named twice, entry 53). "
        "Either convention for the triples is accepted and "
        "both sums are reported when neither matches, so a producer using ORCA's or Molpro's printing "
        "is not refused. TCKDB forms the sums only to compare them and never fills an absent part."
    ),
    adr="0008, 0021",
    enforced_by=(PythonCheck(check_sp_energy_components),),
    escape_hatch="Send the parts the program printed with its energy, or omit the components.",
    thresholds=(
        ConstantThreshold(
            name="sp_energy_component_sum_tolerance",
            value=SUM_TOLERANCE_HARTREE,
            unit="hartree",
            rationale=(
                "The tolerance the single-point log reconciliation uses: tight enough to catch a part "
                "from a different run, loose enough for the digits a program prints."
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
        "A frequency calculation linked beside a composite runs at the level of theory the composite's "
        "scheme prescribes for frequencies, because the recipe's zero-point energy is scaled for that level; "
        "and the composite role is given only to a calculation of type composite."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "An expectation, so a warning. A depositor may legitimately link the frequencies they have, at "
        "another level, and say so; the record's frequencies and its energy then describe different levels, "
        "which is worth telling the depositor and the reader but cannot be shown to be wrong. The composite "
        "role on a calculation of another type is the legacy shape from before the composite type existed: "
        "a warning now, to be refused once producers can send the new shape."
    ),
    adr="0008, 0021",
    enforced_by=(PythonCheck(calculation_levels.collect_composite_link_warnings),),
    escape_hatch="None needed: the link is stored as stated. Link the frequency calculation at the recipe's level.",
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
    tier_rationale=(
        "The legacy shape from before the composite type existed, so a warning (refused "
        "once producers can send the new shape). A composite's number is a recipe's, not an "
        "optimisation's or a single point's, and storing it under the wrong type lets every reader take "
        "it for one; the deposit is kept as sent because the energy it carries is still true."
    ),
    adr="0008, 0021",
    enforced_by=(
        PythonCheck(composite_result_resolution.collect_named_composite_deposit_warnings),
    ),
    escape_hatch="None needed: the deposit is accepted. Send it as type composite with a composite_result block.",
)

CHECK_COMPOSITE_INPUTS_AND_TOTAL_CAN_BE_CHECKED = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=6,
    code=(
        composite_input_resolution.W_COMPOSITE_INPUT_GEOMETRY_UNDECLARED,
        composite_input_resolution.W_COMPOSITE_TOTAL_UNVERIFIABLE,
    ),
    asserts=(
        "An assembled composite's inputs each declare the geometry their energy is at, and the "
        "deposited total can be recomputed from them, so the checks of entries 59 and 60 can run."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "The absence sibling of entries 59 and 60. An input with no declared geometry cannot show that "
        "all inputs share one, and a total whose energies, components or correlation convention are not "
        "stated cannot be recomputed; an incomplete record is still a true record, so these warn (ADR 0008) "
        "and the deposit is kept."
    ),
    adr="0008, 0021",
    enforced_by=(
        PythonCheck(composite_input_resolution._unverifiable_warning, note="The geometry-undeclared warning is raised in ``_check_geometries``."),
    ),
    escape_hatch="None needed: the deposit is accepted. Declare each input's geometry and state the energies used.",
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
        calculation_ownership.W_TS_VALIDATION_SOURCE_CALCULATION_OWNER_MISMATCH,
    ),
    asserts=(
        "What a record cites as evidence belongs to the record's own subject (its species, transition state "
        "or participant). A source of another subject's is that subject's evidence, not weaker evidence for "
        "this one."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, and the same claim wherever it is made: a number is attributed to a subject by the "
        "calculations behind it, so a source of another molecule's, another saddle point's or another "
        "reaction's is not weak evidence for this record but a different record's evidence, and borrowing "
        "it would put one subject's provenance on another's number. The refusals are separate codes "
        "because the repair differs (the wrong job, the wrong role and the wrong rotor scan are different "
        "fields), not because the rule does. A bimolecular network state accepts a calculation of any one "
        "of its participants."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            calculation_ownership.assert_owned_by,
            note=(
                "The shared seam; every source-calculation and statmech owner code above "
                "arrives as its argument, through ``assert_calculation_owned_by`` and ``assert_statmech_owned_by``."
            ),
        ),
        PythonCheck(
            owner_mismatch_error,
            note="Transport and stability source links, built once for the request schema and the workflow (ADR 0017).",
        ),
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
    tier_rationale=(
        "A contract between two statements of one record. A declaration is a claim, stored as made, and "
        "is never reconciled with the columns: an 'experimental' method on a computed record, or a "
        "fixed-mixture collider whose species differ from the bath gas, cannot both be true of the "
        "same rate. A column that is not stated is not a contradiction; the declaration is then the "
        "only statement."
    ),
    adr="0008",
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
        "A determination, statistical-model, structure, protocol or interpretation declaration describes the "
        "record that carries it. Its reaction, direction, source, subject, cited calculations, statmech "
        "record and conformer selection are that record's own."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "A contract about what the record says, and the claim in this group closest to referential integrity: "
        "the same subject rule as entry 65, made about a declaration instead of a link. It is registered "
        "because the declarations are the evidence a later method-aware comparison reads, and a determination "
        "of another reaction, or a model of another species, would be read as evidence for this one. "
        "``context.reason`` names which relation failed. A declaration that restates an existing key with "
        "different content is refused here too, because a determination is immutable."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(kinetics_declaration_resolution._mismatch, note="Also the kinetics protocol-calculation owner refusal."),
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
    tier_rationale=(
        "A contract on a curator action, not a claim about a result. An observation carries no "
        "stereochemical or excited-state resolution of its own, so attaching it to an excited-state or "
        "non-minimum entry would present it as that state's, and a hint naming another molecular skeleton "
        "than the target's species would present it as another molecule's. Only the connectivity block is "
        "compared, so a stereochemistry-only difference is accepted. The ground-state-minimum test is the "
        "one the automatic resolver uses."
    ),
    adr="0008",
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
    tier_rationale=(
        "Definitional. A Petersson bond additivity correction is a sum over bonds; a total with nothing "
        "behind it cannot be shown to be one rather than an empty sum or an arbitrary number. A species "
        "with no bonds (an atom) has nothing to sum. A Melius total does not decompose into bond components "
        "and is not judged."
    ),
    adr="0008",
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
    tier_rationale=(
        "A harmonic scale factor is specific to the code and level it was fit against, but applying one "
        "across codes is a deliberate and sometimes documented choice (re-scaling frequencies computed "
        "elsewhere), so it could fire on a correct deposit and cannot block. Both softwares must be known "
        "and differ; 'nothing to compare' is a distinct state and never counts as agreement."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(statmech_resolution.collect_frequency_scale_factor_software_mismatch_warnings),
    ),
    escape_hatch="None needed: the deposit is accepted. Confirm the choice, or attach a factor derived for that program.",
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
        "A thermo record that carries an enthalpy states what the enthalpy is relative to, the only "
        "accepted reference is the standard enthalpy of formation at 298.15 K, and a declaration is "
        "made only for a record that carries enthalpy content."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "A contract, and the most consequential one for downstream arithmetic. An enthalpy is a number "
        "that means something only against a chosen zero; TCKDB used to store "
        "the number and not the zero. Combining enthalpies of different references (a reaction enthalpy, "
        "a Hess cycle) gives nonsense that looks like a result, so an undeclared enthalpy is refused "
        "rather than defaulted, and a producer that does not know its own convention must refuse to "
        "emit it. A near-miss spelling of the one value gets its own refusal because it is a typo, not "
        "a different quantity."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(enthalpy_reference_error, note="The rule itself, shared with the client builder and the bundle export."),
        PythonCheck(assert_enthalpy_reference, note="Raises the refusal for the thermo upload."),
    ),
    escape_hatch=(
        "Declare ``formation_298k`` when the source states that convention. Sensible increments and "
        "absolute enthalpies belong to the molecular property observation route."
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
    tier_rationale=(
        "Held by the shape of the schema. A calculation that is an input to itself has no order in which "
        "it could have been run, so a record violating it cannot be represented. It is a trigger and "
        "not a Python check so that no write path can bypass it; the lock trigger beside it serialises "
        "concurrent inserts that would together close a cycle."
    ),
    adr="0008",
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
    tier_rationale=(
        "Definitional. A verdict that names, as its measuring job, a calculation whose own verdict names "
        "the first has no measuring job at all. It is the bundle-level sibling of the dependency graph rule "
        "(entry 72), refused before anything is stored."
    ),
    adr="0008",
    enforced_by=(PythonCheck(scf_source_cycle_error, note="The same refusal from the request schema and the workflow (ADR 0017)."),),
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
    tier_rationale=(
        "Definitional, on the read side. Absolute energies from different levels differ by far more than "
        "the quantity a lowest-energy selection reports, so the answer would be an artefact of the levels; "
        "a protocol compared with itself compares nothing. The refusal names what to narrow rather than "
        "choosing for the reader."
    ),
    adr="0008",
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
    tier_rationale=(
        "An absence, so a warning: a converged geometry is a claim about the energy, but an incomplete record "
        "is still a true one and trust and reproducibility grade it. The sibling of entry 19, which asks "
        "for evidence that a saddle point connects its endpoints."
    ),
    adr="0008",
    enforced_by=(PythonCheck(calculation_resolution.collect_converged_opt_energy_warnings),),
    escape_hatch="None needed: the deposit is accepted. Deposit a single point, or the optimisation's final energy with its artifact.",
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
        "What a depositor typed about a calculation's settings and level of theory agrees with what the "
        "uploaded input deck and output log say, the deck agrees with the log, a later log agrees with "
        "an earlier one, and a value a log reports agrees with the value it was asked for."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "An expectation about evidence, so it warns. A deck that differs from a log is common and "
        "legitimate (the deck may not be the exact input that ran), a log legitimately reports a value "
        "the program chose rather than the one requested, and a parser reads only a subset of a log; any "
        "of these could fire on a correct deposit. It is compared only where both sides state a "
        "canonical value for a setting that can change the result, only for the program the "
        "calculation declares, and absence is never a disagreement. Nothing is refused or corrected, "
        "and the precedence (reported, echoed, deck, typed) decides which value is shown."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(parameter_reconciliation.declared_parameter_warnings),
        PythonCheck(parameter_reconciliation.merge_artifact_observations),
        PythonCheck(parameter_reconciliation.level_of_theory_warnings),
        PythonCheck(parameter_reconciliation.level_of_theory_qualifier_warnings),
        PythonCheck(parameter_reconciliation.reported_setting_warnings),
    ),
    escape_hatch="None needed: the deposit is accepted. Correct the typed value, or attach the right deck.",
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
        "The electronic energy a tool states for a single point, and the E0, zero-point and electronic "
        "energies it states for a composite, agree with the output log attached to the same "
        "calculation, and the log is the composite method the level of theory names."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "The log is evidence, not authority: a tool may store a value at reduced precision, a parser "
        "may read the wrong line, and a flagged mismatch is for a reviewer, not a reject. TCKDB keeps the "
        "number exactly as sent and never overwrites it. A log that is a different composite method "
        "from the level of theory is flagged and the energies are then not compared, because they "
        "answer different questions. Where nothing can be read from the log the deposit stands with a "
        "recorded reason, so an unparseable log is never a mismatch."
    ),
    adr="0008, 0021",
    enforced_by=(
        PythonCheck(sp_energy_reconciliation.reconcile_sp_energy),
        PythonCheck(composite_energy_reconciliation.reconcile_composite_energy),
    ),
    escape_hatch="None needed: the deposit is accepted. Send the value the log prints, or omit it and let the log speak.",
    thresholds=(
        ConstantThreshold(
            name="composite_log_tolerance",
            value=COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
            unit="hartree (max(1e-6, 5e-7 * n), n the rounded quantities compared)",
            rationale=(
                "Each log number was printed to six decimals and so was a deposited one copied from it: "
                "E0 against its 0 K line and the ZPE against E(ZPE) use n = 2, the electronic energy "
                "against E0 - E(ZPE) uses n = 3."
            ),
        ),
        ConstantThreshold(
            name="sp_energy_log_tolerance",
            value=sp_energy_reconciliation.SP_ENERGY_ABS_TOL_HARTREE,
            unit="hartree",
            rationale=(
                "Tight because a mismatch is only a reviewer flag: a wrong line, a wrong method or a unit "
                "slip diverges by far more, while a tool storing reduced precision stays inside it."
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
    tier_rationale=(
        "The sibling of the refusal in entry 50, for the case that refusal cannot reach. A deck or log "
        "attached through the artifact route is read after the deposit was judged, so the tolerance was "
        "already decided without it, and stored rows are append-only: nothing is relabelled. The "
        "contradiction is real but the record it concerns is already accepted, so the only available "
        "consequence is an annotation. Agreement, a deck with no frequency directive and a tolerance "
        "basis resting on no method warn nothing."
    ),
    adr="0008, 0012",
    enforced_by=(PythonCheck(hessian_method_deck_check.try_hessian_method_disagreement_from_input_upload),),
    escape_hatch="None needed: the record is annotated, not refused.",
)

CHECK_ATOMIC_ELECTRONIC_LEVELS_AGREE_WITH_THE_GROUND_TERM = ScientificCheck(
    group="Declared against the uploaded evidence",
    sort_key=4,
    code=(
        atomic_electronic_warnings.W_ATOMIC_ELECTRONIC_DEGENERACY_CONTRADICTS_TERM,
        upload_reconciliation.W_TERM_SYMBOL_CONTRADICTS_GROUND_TERM,
    ),
    asserts=(
        "What a neutral ground-state atom declares agrees with the NIST ground term of its element: its "
        "term symbol is the ground term, and the degeneracy of the lowest electronic level it deposits is "
        "one that term allows (the J-resolved ground level, or the lowest levels lumped up to the unsplit term)."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "An atom's entropy and free energy depend on its electronic partition function, so a lowest "
        "degeneracy no ground term gives changes S(298 K) by several J/mol/K. It warns and does not "
        "block because the degeneracy is compared with a reference table (an expectation about a "
        "result), an atom in a non-ground configuration is a legitimate deposit, and the deposit is "
        "kept as sent. Absence of levels is a separate, unregistered absence warning."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(atomic_electronic_warnings._degeneracy_warnings),
        PythonCheck(upload_reconciliation.term_symbol_warnings, note="The ground-term half; the multiplicity half is entry 12's."),
    ),
    escape_hatch="None needed: the deposit is accepted. Deposit the levels that match the declared term.",
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
    tier_rationale=(
        "An expectation and an absence, so a warning. A producer may legitimately run the stability "
        "check at a cheaper level and say so, which is worth telling the depositor but not a reason to "
        "refuse the record. When neither calculation is tied to any conformer the same-conformer rule "
        "cannot be checked, and an incomplete record is still a true record; exactly one side "
        "unanchored is a refusal, and a different conformer is entry 57's refusal."
    ),
    adr="0008",
    enforced_by=(PythonCheck(scf_stability_sources.link_scf_stability_sources),),
    escape_hatch="None needed: the link is stored as stated.",
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
    tier_rationale=(
        "Definitional. A CHEMKIN NASA-7 polynomial is the equilibrium-ensemble thermochemistry of a gas-phase "
        "species, so a depositor cannot make the format declare a single conformer, a liquid, or a record "
        "with no polynomial. The depositor's own declarations are unrestricted: the rule applies only where "
        "the format is the declarer."
    ),
    adr="0008",
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
    tier_rationale=(
        "Not a contradiction with the deck (the sibling of entry 50): NumFreq differentiates gradients, and "
        "for a method with no analytic gradient those gradients are themselves numerical, so the recorded "
        "label and the deck agree about the program's procedure and differ only in what the label means. The "
        "tolerance stored with the record is the looser one for energies; the deposit is kept and the "
        "refinement is stated."
    ),
    adr="0008, 0012",
    enforced_by=(PythonCheck(_refinement_findings),),
    escape_hatch="None needed: the deposit is accepted. Record finite_difference_energy to state it directly.",
)

__all__: list[str] = []
