// What each trust token means, written for a reader of a record page. The API vocabulary guide words these for people who
// build against the API; this map is the site's own wording, with no JSON paths, query parameters or raw tokens. The token
// set is the guide's, and `apiVocabulary.parity.test.ts` fails when the two differ.

export const READER_REVIEW_STATUS: Record<string, string> = {
    not_reviewed: "Nobody has looked at this record yet. That says nothing about the science.",
    under_review: "A curator is working on this record now.",
    approved: "A curator reviewed this record and accepts it.",
    rejected: "A curator reviewed this record and does not accept it.",
    deprecated: "This record should not be used. It is kept so that existing citations still resolve.",
}

export const READER_TRUST_STATUS: Record<string, string> = {
    well_supported: "At least 90% of the weighted checks passed, and every required check passed.",
    mostly_supported: "At least 75% of the weighted checks passed.",
    partial: "At least 50% of the weighted checks passed.",
    sparse: "At least 25% of the weighted checks passed.",
    unsupported: "Fewer than 25% of the weighted checks passed. The record is incomplete, which is not the same as wrong.",
    hard_failed: "A structural failure was found, and it sets this badge whatever else passed. The reason is given above.",
}

export const READER_HARD_FAIL_REASON: Record<string, string> = {
    calculation_missing: "The calculation this verdict is about could not be loaded.",
    calculation_rejected: "A curator marked the calculation unusable.",
    kinetics_missing: "The kinetics record could not be loaded.",
    statmech_missing: "The statistical mechanics record could not be loaded.",
    thermo_missing: "The thermochemistry record could not be loaded.",
    transport_missing: "The transport record could not be loaded.",
    species_entry_missing: "The record does not point at the species entry it describes, so there is nothing to attribute it to.",
    no_thermo_representation_present: "The thermochemistry record carries no thermochemistry at all.",
    no_transport_property_present: "The transport record carries no transport property at all.",
    invalid_lj_pair: "The Lennard-Jones parameters are not a usable pair.",
    invalid_temperature_range: "The temperature range is impossible: not above zero, or its minimum is above its maximum.",
    invalid_external_symmetry: "The external symmetry number is below 1.",
    invalid_torsion_dimension: "A hindered rotor torsion declares a dimension below 1.",
    geometry_validation_failed: "An older review recorded a failed geometry check as a hard failure. Current reviews report it as a warning.",
    artifact_integrity_failed: "The stored bytes behind one of this calculation's files do not match their checksum, or are gone. This is about TCKDB's custody of the file, not about the science.",
    missing_required_identity: "The kinetics record does not identify a complete reaction: no reaction entry, or a side with no participants.",
    source_calculation_hard_failed_for_required_role: "A calculation this record cannot do without, such as a reactant or product energy, the transition state energy or the frequencies, has itself hard failed.",
    transition_state_entry_missing: "The transition state entry could not be loaded.",
    transition_state_parent_missing: "The entry does not point at the transition state it is an entry for.",
    reaction_entry_missing: "The transition state names no reaction entry, so it belongs to no reaction.",
    ts_entry_status_rejected: "The transition state entry was rejected.",
    multiplicity_invalid: "The spin multiplicity is below 1.",
    all_source_calculations_hard_failed: "Every calculation supporting this transition state entry has itself hard failed, so nothing supports it.",
    geometry_validation_failed_for_source_calculation: "An older review recorded a failed geometry check on a supporting calculation as a hard failure. Current reviews report it as a warning.",
    frequency_source_has_zero_imaginary_modes_for_validated_ts: "An older review recorded a transition state frequency calculation with no imaginary mode as a hard failure. Current reviews report it as a warning.",
    frequency_source_reaction_coordinate_not_designated_for_validated_ts: "An older review recorded a missing reaction coordinate designation as a hard failure. Current reviews report it as a missing check.",
}
