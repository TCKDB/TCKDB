// What each trust check asks, written for a reader of a record page: no table names, column names or paths. The check set
// is the guide's (`apiVocabulary.json`), and `apiVocabulary.parity.test.ts` fails when a check has no sentence here. A
// check with no sentence shows no description rather than the guide's developer wording.

/** One sentence per check name; a rubric that asks it differently has its own entry in `RUBRIC_CHECK_ASKS`. */
const CHECK_ASKS: Record<string, string> = {
    arrhenius_parameters_complete: "An Arrhenius-form rate should give A, its units, n and the activation energy.",
    arrhenius_units_present: "An Arrhenius-form rate should state the units of A.",
    artifacts_present: "At least one file from the calculation, such as its log or input, should be kept.",
    at_least_one_thermo_representation_present: "The record must carry thermochemistry in some form: scalar values, NASA-7, NASA-9, Wilhoit or tabulated points.",
    calculation_dependencies_present: "The links between the calculations that feed this record should be recorded.",
    calculation_dependencies_present_when_expected: "A calculation that builds on another step, such as a frequency, single-point, IRC or scan calculation, should say which calculation it came from.",
    calculation_has_owner: "The calculation must belong to exactly one species entry or transition state entry.",
    calculation_type_present: "The calculation must say what kind of calculation it is.",
    charge_present: "The charge of the transition state entry must be recorded.",
    chem_reaction_present: "The reaction entry the transition state belongs to should resolve to a reaction.",
    correction_scheme_literature_present: "A correction scheme tied to one program should cite the literature its parameters come from.",
    correction_scheme_software_release_present: "A correction scheme tied to one program should say which release of that program produced its parameters.",
    declared_saddle_target_qualifies: "When the entry names the structure it takes to be the saddle point, that structure should qualify as a first-order saddle point on the evidence alone, with no competing candidate. This is advisory and carries no weight. A warning means the named structure needs a closer look, not that the transition state is wrong. No warning does not confirm it either.",
    dipole_present: "A dipole moment should be given when this representation is used.",
    dipole_source_present_if_dipole_present: "A computed dipole moment should link the calculation it came from.",
    epsilon_present: "Lennard-Jones transport should give the well depth, epsilon over k.",
    external_symmetry_present: "The external symmetry number should be recorded.",
    extra_imaginary_modes_not_flagged: "No extra imaginary mode at or above the protocol's threshold has been flagged in the recorded structure checks. Advisory.",
    freq_source_present: "A computed record should link its frequency calculation when there is one.",
    frequency_scale_factor_present_if_applicable: "A record derived from frequencies should state the frequency scale factor used.",
    frequency_source_present: "The frequency calculations should be linked when there are any.",
    full_transport_source_present: "Computed transport should link the calculation behind the full set of transport values when there is one.",
    geometry_validation_not_failed_for_source_calculations: "No supporting calculation has a geometry check that raised a warning. Advisory.",
    geometry_validation_passed_or_warning: "The calculation's geometry check did not raise a warning. Advisory.",
    geometry_validation_present: "An optimization should carry a geometry check.",
    geometry_validation_present_for_source_calculations: "The main supporting calculations should carry a geometry check.",
    imaginary_frequency_count_recorded: "The frequency result that represents the entry should record how many imaginary modes it found. That is the result the named saddle structure points to, or else the newest one.",
    imaginary_frequency_value_present: "The frequency result that represents the entry should record the value of its imaginary mode, in reciprocal centimetres.",
    input_geometry_present: "At least one input geometry must be linked.",
    irc_evidence_present: "An IRC calculation should be linked when there is one.",
    is_linear_present: "Whether the molecule is linear should be recorded explicitly.",
    kinetics_model_present: "The kind of kinetics model must be stated.",
    level_of_theory_present: "The calculation must name its level of theory.",
    lj_pair_present_if_applicable: "Lennard-Jones transport should give both sigma and epsilon over k.",
    master_equation_or_fit_source_present_if_applicable: "A master-equation or fitting source counts when the record has one.",
    multiplicity_present: "The spin multiplicity of the transition state entry must be recorded.",
    multiplicity_valid: "The spin multiplicity must be at least 1.",
    nasa_coefficients_present: "A NASA polynomial should give its full set of coefficients.",
    no_frequency_result_has_undesignated_imaginary_modes: "No attached frequency result reports several imaginary modes without saying which one is the reaction coordinate. Advisory, and the badge does not certify the saddle point. A warning means that result needs a closer look, not that the transition state is wrong.",
    no_frequency_result_lacks_an_imaginary_mode: "No attached frequency result reports zero imaginary modes. Advisory, and the badge does not certify the saddle point.",
    opt_source_present: "A computed record should link its geometry optimization when there is one.",
    output_geometry_present: "A calculation that produces a geometry should record its output geometry.",
    parameters_parsed: "The settings the program ran with should have been read from the calculation files.",
    path_search_evidence_present: "The path search that found the transition state, such as NEB, GSM or a scan, should be linked when there is one.",
    point_group_present: "The point group should be recorded when it is known.",
    polarizability_present: "A polarizability should be given when this representation is used.",
    polarizability_source_present_if_polarizability_present: "A computed polarizability should link the calculation it came from.",
    product_energy_sources_present: "Energy calculations should be linked for every product.",
    quality_recorded: "A reviewer other than the depositor should have approved this calculation. The depositor's own quality claim does not count.",
    reactant_energy_sources_present: "Energy calculations should be linked for every reactant.",
    reaction_coordinate_designated_for_ts: "The frequency result that represents the entry must say which imaginary mode is the reaction coordinate. With one imaginary mode nothing needs saying. With more than one it does.",
    reaction_entry_present: "The record must be attached to a reaction entry.",
    result_block_present: "The calculation must carry the result that matches its type: single-point, optimization, frequency, IRC, scan or path search.",
    review_not_rejected_or_deprecated_if_applicable: "A curator has not rejected or deprecated the transition state entry.",
    rigid_rotor_kind_present: "The rigid rotor treatment should be recorded.",
    rotational_relaxation_present: "A rotational relaxation value should be given when there is one.",
    scalar_thermo_present: "Scalar thermochemistry should give H298 or S298.",
    scan_source_present_if_torsions_present: "A record with torsions should link the scan calculations behind them.",
    scf_stability_present_if_claimed: "A stability check of the SCF solution should be attached when the calculation claims one.",
    sigma_epsilon_pair_consistent: "Sigma and epsilon over k must either both be given or both be left out.",
    sigma_present: "Lennard-Jones transport should give sigma.",
    software_release_present: "The calculation should say which release of the program produced it.",
    source_calculation_artifacts_present: "At least one supporting calculation should keep one of its files.",
    source_calculation_has_non_hard_failed_evidence: "The supporting calculations should not carry a hard-failure signal.",
    source_calculation_lot_present: "Every supporting calculation should name its level of theory.",
    source_calculation_result_blocks_present: "The supporting calculations should carry the results their type calls for.",
    source_calculation_software_present: "Every supporting calculation should say which release of the program produced it.",
    source_calculation_workflow_tool_present: "The record, or at least one supporting calculation, should say which release of the workflow tool ran it.",
    source_calculations_present: "At least one calculation should support the record.",
    sp_or_composite_source_present: "A single-point or composite calculation may be linked as supporting evidence.",
    sp_or_composite_source_present_if_applicable: "A single-point or composite energy calculation should be linked when one applies.",
    species_entry_present: "The record must be attached to a species entry.",
    statmech_not_rejected_or_deprecated_if_applicable: "A curator has not rejected or deprecated the statistical mechanics record.",
    statmech_origin_is_computed: "This rubric is for computed statistical mechanics, so the record's origin should be computed.",
    statmech_present: "Statistical mechanics evidence should be linked.",
    statmech_treatment_present: "The kind of statistical mechanics treatment should be recorded.",
    supporting_calculations_present: "At least one calculation should support this transition state entry.",
    supporting_geometry_source_present: "Computed transport should link the calculation that supplied the geometry when there is one.",
    temperature_range_present: "Both ends of the temperature range should be given.",
    temperature_range_present_if_applicable: "A record that covers a range should give its temperature limits.",
    temperature_range_valid: "The temperature range should run from above 0 K up to at most 10000 K, with the minimum below the maximum.",
    thermo_model_present: "The thermochemistry should be in a recognised form: scalar values, NASA-7, NASA-9, Wilhoit or tabulated points.",
    thermo_not_rejected_or_deprecated_if_applicable: "A curator has not rejected or deprecated the thermochemistry record.",
    thermo_origin_is_computed: "This rubric is for computed thermochemistry, so the record's origin should be computed.",
    thermo_points_present: "Tabulated thermochemistry should include at least one point that carries a value.",
    torsion_definitions_present: "Each recorded torsion should define its torsion coordinate.",
    torsion_symmetry_recorded: "Each recorded torsion should give its symmetry number.",
    torsions_recorded_if_hindered_rotor_treatment: "A hindered rotor treatment should list the torsions it treats.",
    transition_state_entry_present: "The transition state entry being judged could be loaded.",
    transition_state_parent_present: "The entry must resolve to the transition state it is an entry for.",
    transport_model_present: "The transport record should give at least one structured transport property.",
    transport_not_rejected_or_deprecated_if_applicable: "A curator has not rejected or deprecated the transport record.",
    transport_origin_is_computed: "This rubric is for computed transport, so the record's origin should be computed.",
    transport_property_present: "At least one transport property must be given.",
    ts_energy_source_present: "Computed transition state theory kinetics should link the calculation of the transition state energy.",
    ts_frequency_present: "A frequency calculation should be among the supporting calculations.",
    ts_graph_or_smiles_present: "The transition state entry should carry a SMILES string or a molecular structure.",
    ts_optimization_present: "A transition state optimization should be among the supporting calculations.",
    ts_single_point_present: "A single-point calculation should be among the supporting calculations.",
    ts_status_not_rejected: "The transition state entry's status must not be rejected.",
    ts_status_recorded: "The transition state entry's status must be recorded.",
    tunneling_metadata_present_if_claimed: "A tunneling model that the record claims should be named.",
    uncertainty_present: "At least one uncertainty value should be given.",
    uses_projected_frequencies_recorded: "Whether projected frequencies were used should be recorded explicitly.",
    workflow_tool_release_present: "The record, or at least one supporting calculation, should say which release of the workflow tool ran it.",
}

/** The rubrics that ask a check in their own words, keyed `rubric:check`. */
const RUBRIC_CHECK_ASKS: Record<string, string> = {
    "computed_transition_state:calculation_dependencies_present": "At least one link between the supporting calculations should be recorded.",
    "computed_transition_state:geometry_validation_present_for_source_calculations": "At least one optimization, IRC or path search supporting the entry should carry a geometry check.",
    "computed_transition_state:irc_evidence_present": "An IRC calculation exists among the supporting calculations. This only checks that one exists, not that anyone deposited a structured pass or fail for it.",
    "computed_transition_state:source_calculation_artifacts_present": "At least one supporting calculation should keep one of its files.",
    "computed_transition_state:source_calculation_has_non_hard_failed_evidence": "At least one supporting calculation must not carry a hard-failure signal.",
    "computed_transition_state:source_calculation_lot_present": "Every supporting calculation must name its level of theory.",
    "computed_transition_state:source_calculation_software_present": "Every supporting calculation should say which release of the program produced it.",
    "computed_transition_state:source_calculation_workflow_tool_present": "At least one supporting calculation should say which release of the workflow tool ran it.",
    "computed_transition_state:path_search_evidence_present": "The path search that found the transition state, such as NEB, GSM or a scan, should be linked when there is one.",
    "computed_ts_statmech:transition_state_entry_present": "The statistical mechanics record must be attached to a transition state entry.",
    "computed_kinetics:geometry_validation_not_failed_for_source_calculations": "No supporting calculation has a geometry check that raised a warning. Advisory.",
    "computed_kinetics:source_calculations_present": "At least one calculation should support the computed kinetics.",
    "computed_statmech:source_calculations_present": "At least one calculation should support the computed statistical mechanics.",
    "computed_ts_statmech:source_calculations_present": "At least one calculation should support the computed statistical mechanics.",
    "computed_thermo:source_calculations_present": "At least one calculation should support the computed thermochemistry.",
    "computed_transport:source_calculations_present": "At least one calculation should support the computed transport.",
    "computed_kinetics:workflow_tool_release_present": "The record, or at least one supporting calculation, should say which release of the workflow tool ran it.",
}

const KNOWN_RUBRICS = new Set(["computed_calculation", "computed_kinetics", "computed_statmech", "computed_thermo", "computed_transition_state", "computed_transport", "computed_ts_statmech"])

/** The reader's sentence for a check under a rubric, or null when none is written. */
export function readerCheckAsks(rubric: string | null | undefined, check: string): string | null {
    if (!rubric) return null
    const own = `${rubric}:${check}`
    if (Object.hasOwn(RUBRIC_CHECK_ASKS, own)) return RUBRIC_CHECK_ASKS[own]
    if (!KNOWN_RUBRICS.has(rubric)) return null
    return Object.hasOwn(CHECK_ASKS, check) ? CHECK_ASKS[check] : null
}

/** Every check name that has a default sentence, for the parity test. */
export const READER_CHECK_NAMES = Object.keys(CHECK_ASKS)

const CHECK_WORDS: Record<string, string> = {
    irc: "IRC", freq: "frequency", lot: "level of theory", sp: "single-point", opt: "optimization", ts: "transition state",
    nasa: "NASA", scf: "SCF", lj: "Lennard-Jones", smiles: "SMILES", calc: "calculation", dipole: "dipole",
    neb: "NEB", gsm: "GSM", arrhenius: "Arrhenius", tckdb: "TCKDB", statmech: "statistical mechanics",
}

/** A check or rubric token as a label: abbreviations spelled in the site's way, first letter capitalised. */
export function readerTokenLabel(token: string): string {
    const words = token.split("_").map((word) => (Object.hasOwn(CHECK_WORDS, word) ? CHECK_WORDS[word] : word))
    const text = words.join(" ")
    return text.charAt(0).toUpperCase() + text.slice(1)
}
