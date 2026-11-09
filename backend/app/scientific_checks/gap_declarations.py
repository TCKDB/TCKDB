"""Register entries for scientific codes that had a catalogue entry and no register entry.

Why this module exists
----------------------
``app.api.code_catalogue`` lists every code the API can put in an error body, and
``app.services.deposit_warnings.REGISTER_ENTRY`` maps each stored warning to the register
entry that declares it. Comparing the two on 2026-11-09 found scientific codes in both with
no entry behind them: refusals and warnings that encode a position a referee could dispute
(a bath gas that does not sum to one, a declared energy level that contradicts the
calculations linked to it, a deposited composite energy that disagrees with its own log), and
that the register therefore did not claim.

The entries are declared together here, not beside each check, for one reason: they were
found by a single audit and are reviewed as one set, with their tiers, in one place. A check
that moves its module does not move its entry. Each entry still holds the real function
object, so renaming or deleting a check breaks the import of this module, and the drift
guard in ``backend/tests/db/test_scientific_check_register.py`` verifies every code against
the source of the module that defines the enforcing function.

Codes that were the same check as an existing entry were added to that entry instead (see
the ``code`` tuples of entries 12, 20, 33 and the Arrhenius ``A`` units entry), and each
group below is appended to ``numbering.READING_ORDER`` so no published entry is renumbered.

What is deliberately not here
-----------------------------
Codes that are plumbing (ownership, key resolution, pagination, auth, limits, releases,
curator workflow) are not scientific checks and stay in the catalogue alone. Two scientific
looking codes were left out on purpose and are listed in the pull request that added this
module: ``geometry_isotope_symbol_conflict`` (an earlier decision, recorded in
``tests/db/test_scientific_check_register.py``, keeps it out because it refuses an
input-format contradiction) and ``observation_identity_target_not_ground_state_minimum``
(raised as a ``message_prefix`` ``ValueError``, which the typed-path guard cannot accept).
"""

from __future__ import annotations

from tckdb_schemas.bundle_source_rules import (
    W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH,
    W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH,
    assert_calculation_geometry_matches_conformer,
    scf_source_geometry_error,
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
    SP_ENERGY_COMPONENT_TOTAL_MISMATCH,
    SP_ENERGY_COMPONENTS_DO_NOT_SUM,
    SUM_TOLERANCE_HARTREE,
    check_sp_energy_components,
)

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
    calculation_resolution,
    composite_energy_reconciliation,
    composite_input_resolution,
    hessian_method_deck_check,
    kinetics_declaration_resolution,
    network_energy_sources,
    parameter_reconciliation,
    scf_stability_sources,
    sp_energy_reconciliation,
    statmech_declaration_resolution,
    structure_determination_resolution,
)
from app.workflows import network_pdep
from app.workflows.thermo import assert_enthalpy_reference

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
        "A bath gas a rate states is a composition: its mole fractions sum to one, each "
        "species appears once, and it is not given for a rate at the high-pressure limit, "
        "where there is no collider."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A mole-fraction set that does not sum to one, or that names one species "
        "twice, is not a composition, and no correct measurement or calculation can have been "
        "obtained in it. The high-pressure limit is the rate with collisional energy transfer "
        "removed, so a bath gas attached to it contradicts the pressure context the same record "
        "states. All three are contradictions inside one payload, not expectations about a "
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
        "twice states a stoichiometry the wrong way. Both used to reach the database and surface as "
        "an HTTP 500 from the uniqueness constraint. Stereoisomers are different species entries, so a "
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

CHECK_NETWORK_ENERGY_SOURCE_IS_OF_THE_SUBJECT = ScientificCheck(
    group="Network composition and sources",
    sort_key=3,
    code=network_energy_sources.W_NETWORK_ENERGY_SOURCE_SUBJECT_MISMATCH,
    asserts=(
        "A calculation cited as the source of a network energy belongs to the subject the energy "
        "is stated for: a participant of the state, or the saddle point of the barrier."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. An energy taken from another molecule's calculation is not the energy of the "
        "state it is stated for, whatever its type. This is the sibling of the source-type rule "
        "(entry 26, which asks whether the calculation's type can carry the energy at all): that rule "
        "asks what kind of calculation it is, this one asks whose. A bimolecular state accepts a "
        "calculation of any one of its participants, and an empty participant set accepts none."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(network_energy_sources.assert_state_energy_source_owner),
        PythonCheck(network_energy_sources.assert_barrier_source_owner),
        PythonCheck(network_energy_sources.assert_network_role_source_owner),
        PythonCheck(network_energy_sources.assert_state_participant_source_owner),
    ),
    escape_hatch="Cite the calculation of the species (or saddle point) the energy is for.",
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
    ),
    asserts=(
        "The level of theory a record declares its energy stands at is the level of the energy "
        "calculations linked to that record: the single point or composite for thermo and statmech "
        "(the optimisation when none is linked), the reactant, product and transition-state energies "
        "for kinetics, compared modulo spin treatment per participant."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "A contract between two statements of one record, not an expectation about a result. A "
        "depositor may legitimately run the optimisation and the single point at different levels "
        "(owner decision, 2026-09), which is exactly why the declaration must be honest: it is the "
        "label every reader uses to say which level the energy is at, and one that contradicts the "
        "links beside it is a false label that no re-run decides. Kinetics is compared per "
        "participant and modulo spin treatment, so a restricted and an unrestricted calculation of "
        "closed-shell species do not contradict each other."
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
        "Omit the declaration (the level is then derived from the links at read time), or declare "
        "the level the linked calculations ran at."
    ),
)

CHECK_ENERGY_CALCULATION_IS_ON_THE_LINKED_GEOMETRY = ScientificCheck(
    group="Levels and geometries of linked calculations",
    sort_key=2,
    code=(
        calculation_levels.W_THERMO_SP_GEOMETRY_MISMATCH,
        calculation_levels.W_STATMECH_SP_GEOMETRY_MISMATCH,
        W_SCF_STABILITY_SOURCE_GEOMETRY_MISMATCH,
    ),
    asserts=(
        "An energy calculation linked to a thermo or statmech record sits on the output geometry of "
        "a linked optimisation, and the job that measured an SCF stability verdict ran on the same "
        "conformer as the calculation carrying the verdict."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A single-point energy belongs to the structure it was computed at; paired "
        "with another conformer's optimisation it is a different molecule's energy, and a stability "
        "analysis describes one wavefunction at one geometry. Silence is kept where either side "
        "declares no geometry: absence of evidence is not evidence of a mismatch."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            calculation_levels.assert_role_consistency,
            note="Rule R3', run once per product with that product's code.",
        ),
        PythonCheck(
            scf_source_geometry_error,
            note="Builds the stability-source refusal for both the request schema and the workflow (ADR 0017).",
        ),
    ),
    escape_hatch="Link the optimisation whose geometry the energy was computed at.",
)

CHECK_CALCULATION_GEOMETRY_STATEMENTS_AGREE = ScientificCheck(
    group="Levels and geometries of linked calculations",
    sort_key=3,
    code=(
        W_CALCULATION_GEOMETRY_CONFORMER_MISMATCH,
        calculation_resolution.W_CALCULATION_INPUT_GEOMETRY_MISMATCH,
    ),
    asserts=(
        "A calculation is evidence for one conformer and ran on one structure: its conformer key and "
        "its geometry key name the same conformer, and the geometry it states it ran on is one of "
        "its input geometries."
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
        "sent and stores none of them (owner decision 5). A total that does not follow from the "
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
        PythonCheck(composite_input_resolution._finalize_one),
        PythonCheck(composite_input_resolution._check_geometries),
    ),
    escape_hatch="Cite the calculation at the slot's level, all computed at the one geometry.",
)

CHECK_SP_ENERGY_COMPONENTS_ADD_UP = ScientificCheck(
    group="Composite and single-point arithmetic",
    sort_key=3,
    code=(SP_ENERGY_COMPONENT_TOTAL_MISMATCH, SP_ENERGY_COMPONENTS_DO_NOT_SUM),
    asserts=(
        "The parts of a single point's electronic energy do not contradict the energy they are parts "
        "of: a stated total is the energy, and reference plus correlation (plus triples, where the "
        "program prints it separately) is the energy."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A composite level consumes these parts, so a part from a different run than the "
        "energy poisons every number built on it. Either convention for the triples is accepted and "
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
    ),
    asserts=(
        "A determination, statistical-model or structure declaration describes the record that carries "
        "it: a kinetics determination is of the record's own reaction, direction and source, a model "
        "declaration sits on its own subject, and a structure determination is pinned to calculations "
        "of its own owner."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "A contract about what the record says, and the weakest scientific claim in this group: it is "
        "closer to referential integrity than to chemistry. It is registered because the declarations "
        "are the evidence a later method-aware comparison rests on, and a determination of another "
        "reaction, or a model of another species, would be read as evidence for this one. The "
        "``context.reason`` names which relation failed. A declaration that restates an existing key "
        "with different content is also refused here, because a determination is immutable."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(kinetics_declaration_resolution.resolve_kinetics_declarations),
        PythonCheck(statmech_declaration_resolution.persist_statmech_model_declaration),
        PythonCheck(structure_determination_resolution.persist_structure_determinations),
    ),
    escape_hatch="Declare it on the record it describes, with the calculations and sources of that record.",
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
        "that means something only against a chosen zero; until the decision of 2026-09-23 TCKDB stored "
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
    ),
    escape_hatch="Drop or reverse the dependency that points back.",
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
    code=atomic_electronic_warnings.W_ATOMIC_ELECTRONIC_DEGENERACY_CONTRADICTS_TERM,
    asserts=(
        "The degeneracy of the lowest electronic level a single atom deposits is one the NIST ground "
        "term allows: the J-resolved ground level, or the lowest levels lumped up to the unsplit term."
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
    enforced_by=(PythonCheck(atomic_electronic_warnings._degeneracy_warnings),),
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
        "unanchored is a refusal, and a different conformer is entry 58's refusal."
    ),
    adr="0008",
    enforced_by=(PythonCheck(scf_stability_sources.link_scf_stability_sources),),
    escape_hatch="None needed: the link is stored as stated.",
)

__all__: list[str] = []
