"""Machine-readable codes TCKDB reports when it refuses a deposit.

**Generated. Do not edit by hand.** Regenerate with
``conda run -n tckdb_env python backend/scripts/generate_client_rejection_codes.py``;
``backend/tests/api/test_client_rejection_codes_generated.py`` fails if
this file and the server's code catalogue disagree.

Every member is a refusal TCKDB can report with an HTTP 4xx: a deposit
that contradicts chemistry, a query that names a filter the endpoint does
not have, a calculation the depositor does not own, a handle of the wrong
type. They are not all scientific, and that is deliberate -- the subset
that *is* a position about chemistry is documented for humans at
``docs/guides/scientific_check_register.md``, which is where to look for
what such a code asserts, why it refuses rather than warns, and what the
escape hatch is for legitimate chemistry it would otherwise reject. A
code absent from that document is still a real refusal; it simply is not
a claim a referee could argue with.

Deliberately absent: warning codes, which arrive alongside an *accepted*
upload, and trust labels, which are applied at read time and refuse
nothing -- both would be misread as failures under this name. Also absent
are the ``http_<status>`` fallbacks and the generic ``validation_error``
family, which carry nothing the status line does not, and 5xx codes,
which refuse nothing the caller did.

Using it::

    from tckdb_client import RejectionCode, rejection_code

    try:
        client.upload_reaction(payload)
    except TCKDBHTTPError as exc:
        match rejection_code(exc.code):
            case RejectionCode.REACTION_MASS_BALANCE_FAILED:
                raise                      # the deposit is wrong; do not retry
            case RejectionCode.SPECIES_GEOMETRY_COMPOSITION_MISMATCH:
                payload = repair(payload)  # recoverable
            case None:
                raise                      # a code this client does not know

Use :func:`rejection_code` rather than ``RejectionCode(exc.code)``. A
server is routinely newer than the client pinned against it, and a code
added since this file was generated must not turn a handled refusal into
an unhandled ``ValueError``.

:data:`REJECTION_STATUSES` gives the HTTP status each member arrives at,
which is the retry advice: 422 means nothing was written and a corrected
payload may be resent, 409 means the write reached the database, 404
means the record named is not there, 426 means upgrade this package, and
429 is the one status where retrying the same request unchanged is the
right thing to do.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CONFLICT_REJECTION_CODES",
    "REJECTION_STATUSES",
    "RejectionCode",
    "VALIDATION_REJECTION_CODES",
    "rejection_code",
]


class RejectionCode(str, Enum):
    """A code TCKDB reports in the ``code`` field of a refusal body.

    ``str`` subclass so a member compares equal to the wire string and
    can be used wherever the raw code was.
    """

    APPLIED_ENERGY_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH = "applied_energy_correction_source_calculation_owner_mismatch"
    APPLIED_ENERGY_CORRECTION_SOURCE_KEY_UNDECLARED = "applied_energy_correction_source_key_undeclared"
    ARRHENIUS_A_UNITS_MOLECULARITY_MISMATCH = "arrhenius_a_units_molecularity_mismatch"
    ATOM_MAP_ATOMS_UNACCOUNTED_FOR = "atom_map_atoms_unaccounted_for"
    ATOM_MAP_CONTRADICTS_IRC_MAPPING = "atom_map_contradicts_irc_mapping"
    ATOM_MAP_ELEMENT_NOT_CONSERVED = "atom_map_element_not_conserved"
    ATOM_MAP_GEOMETRY_UNPARSEABLE = "atom_map_geometry_unparseable"
    ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE = "atom_map_indices_not_geometry_relative"
    ATOM_MAP_INFERRED_REQUIRES_NOTE = "atom_map_inferred_requires_note"
    ATOM_MAP_NOT_A_BIJECTION = "atom_map_not_a_bijection"
    ATOM_MAP_PARTICIPANT_NOT_DECLARED = "atom_map_participant_not_declared"
    ATOM_MAP_WITHOUT_TRANSITION_STATE = "atom_map_without_transition_state"
    CALCULATION_GEOMETRY_COMPOSITION_MISMATCH = "calculation_geometry_composition_mismatch"
    CALCULATION_HANDLE_CONFLICT = "calculation_handle_conflict"
    CALCULATION_KEY_UNDECLARED = "calculation_key_undeclared"
    CANONICAL_PARAMETER_VALUE_REQUIRES_KEY = "canonical_parameter_value_requires_key"
    CLIENT_SORT_NOT_SUPPORTED = "client_sort_not_supported"
    COMPOSED_SEARCH_CANDIDATE_LIMIT_EXCEEDED = "composed_search_candidate_limit_exceeded"
    COMPOSED_SEARCH_INVALID_PAGE = "composed_search_invalid_page"
    COMPOSED_SEARCH_PAGINATION_CHANGED = "composed_search_pagination_changed"
    COMPOSED_SEARCH_PAGINATION_STALLED = "composed_search_pagination_stalled"
    CURATION_POLICY_VERSION_CONFLICT = "curation_policy_version_conflict"
    CURATOR_TASK_NOT_FOUND = "curator_task_not_found"
    CURSOR_OFFSET_CONFLICT = "cursor_offset_conflict"
    CURSOR_QUERY_MISMATCH = "cursor_query_mismatch"
    DOI_ALREADY_RECORDED = "doi_already_recorded"
    ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE = "energy_transfer_scope_columns_disagree"
    EXPORT_ALL_CAP_EXCEEDED = "export_all_cap_exceeded"
    EXPORT_SEED_EMPTY = "export_seed_empty"
    EXPORT_SEED_UNRESOLVED = "export_seed_unresolved"
    FREQ_LIST_EXCEEDS_GEOMETRY_DEGREES_OF_FREEDOM = "freq_list_exceeds_geometry_degrees_of_freedom"
    FREQ_N_IMAG_DISAGREES_WITH_MODES = "freq_n_imag_disagrees_with_modes"
    GEOMETRY_TOO_LARGE = "geometry_too_large"
    HANDLE_NOT_FOUND = "handle_not_found"
    HANDLE_TYPE_MISMATCH = "handle_type_mismatch"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INCLUDE_NOT_IMPLEMENTED_YET = "include_not_implemented_yet"
    INVALID_CURSOR = "invalid_cursor"
    INVALID_HANDLE = "invalid_handle"
    INVALID_IDEMPOTENCY_KEY = "invalid_idempotency_key"
    INVALID_PAGINATION = "invalid_pagination"
    INVALID_RANGE = "invalid_range"
    INVALID_STRUCTURE_QUERY = "invalid_structure_query"
    INVALID_TEMPERATURE_RANGE = "invalid_temperature_range"
    IRC_RESULT_NOT_FOUND = "irc_result_not_found"
    KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH = "kinetics_interpretation_conformer_selection_owner_mismatch"
    KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH = "kinetics_interpretation_statmech_owner_mismatch"
    LEVEL_OF_THEORY_HANDLE_CONFLICT = "level_of_theory_handle_conflict"
    LIMIT_TOO_LARGE = "limit_too_large"
    LOWEST_ENERGY_UNAVAILABLE = "lowest_energy_unavailable"
    MANIFEST_ALREADY_FROZEN = "manifest_already_frozen"
    MANIFEST_NOT_FROZEN = "manifest_not_frozen"
    MISSING_FILTER = "missing_filter"
    MISSING_IDENTIFIER = "missing_identifier"
    MISSING_REACTION_SEARCH_FILTER = "missing_reaction_search_filter"
    MISSING_STRUCTURE_QUERY = "missing_structure_query"
    ML_EXPORT_ALL_CAP_EXCEEDED = "ml_export_all_cap_exceeded"
    ML_EXPORT_LOT_UNRESOLVED = "ml_export_lot_unresolved"
    ML_EXPORT_SEED_EMPTY = "ml_export_seed_empty"
    ML_EXPORT_SEED_UNRESOLVED = "ml_export_seed_unresolved"
    MULTIPLE_STRUCTURE_QUERIES = "multiple_structure_queries"
    N_IMAG_CONTRADICTS_MINIMUM = "n_imag_contradicts_minimum"
    NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE = "network_solve_reported_requires_literature"
    NON_FINITE_VALUE = "non_finite_value"
    OFFSET_TOO_LARGE = "offset_too_large"
    OWNER_MISSING = "owner_missing"
    PARAMETER_VALUE_REQUIRES_KEY = "parameter_value_requires_key"
    PATH_SEARCH_RESULT_NOT_FOUND = "path_search_result_not_found"
    POST_SEARCH_FIELDS_MUST_BE_IN_BODY = "post_search_fields_must_be_in_body"
    PRESSURE_ALIAS_CONFLICT = "pressure_alias_conflict"
    QUERY_TOO_EXPENSIVE = "query_too_expensive"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    RATIONALE_REQUIRED = "rationale_required"
    REACTION_CHARGE_NOT_CONSERVED = "reaction_charge_not_conserved"
    REACTION_ENTRY_HANDLE_CONFLICT = "reaction_entry_handle_conflict"
    REACTION_HANDLE_CONFLICT = "reaction_handle_conflict"
    REACTION_MASS_BALANCE_FAILED = "reaction_mass_balance_failed"
    RECORD_HAS_NO_SUBJECT = "record_has_no_subject"
    RECORD_NOT_APPROVED = "record_not_approved"
    RECORD_REF_NOT_SELECTABLE = "record_ref_not_selectable"
    RECORD_SUBJECT_MISMATCH = "record_subject_mismatch"
    RECORD_TYPE_NOT_SELECTABLE = "record_type_not_selectable"
    REFERENCE_CONFLICT = "reference_conflict"
    RELEASE_NOT_DRAFT = "release_not_draft"
    RELEASE_NOT_PUBLISHED = "release_not_published"
    RELEASE_SCOPING_NOT_IMPLEMENTED = "release_scoping_not_implemented"
    RELEASE_SELECTS_NOTHING = "release_selects_nothing"
    RELEASE_TAG_TAKEN = "release_tag_taken"
    SCAN_RESULT_NOT_FOUND = "scan_result_not_found"
    SELECTION_ALREADY_STANDS = "selection_already_stands"
    SELECTION_ALREADY_SUPERSEDED = "selection_already_superseded"
    SELECTION_NO_LONGER_APPROVED = "selection_no_longer_approved"
    SMILES_TOO_LONG = "smiles_too_long"
    SPECIES_ENTRY_HANDLE_CONFLICT = "species_entry_handle_conflict"
    SPECIES_GEOMETRY_COMPOSITION_MISMATCH = "species_geometry_composition_mismatch"
    SPECIES_GEOMETRY_ISOTOPE_MISMATCH = "species_geometry_isotope_mismatch"
    SPECIES_HANDLE_CONFLICT = "species_handle_conflict"
    SPECIES_KIND_CONFLICT = "species_kind_conflict"
    SPECIES_SMILES_CHARGE_MISMATCH = "species_smiles_charge_mismatch"
    STATE_CONFLICT = "state_conflict"
    STATMECH_CALCULATION_KEY_UNDECLARED = "statmech_calculation_key_undeclared"
    STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH = "statmech_source_calculation_owner_mismatch"
    STATMECH_SOURCE_ROLE_TYPE_MISMATCH = "statmech_source_role_type_mismatch"
    STATMECH_SUBJECT_NOT_EXACTLY_ONE = "statmech_subject_not_exactly_one"
    STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH = "statmech_torsion_scan_calculation_owner_mismatch"
    STORED_SPECIES_SMILES_UNPARSEABLE = "stored_species_smiles_unparseable"
    SUBJECT_TYPE_MISMATCH = "subject_type_mismatch"
    SUPERSEDES_SAME_RECORD = "supersedes_same_record"
    TCKDB_CLIENT_VERSION_INVALID = "tckdb_client_version_invalid"
    TCKDB_CLIENT_VERSION_MISSING = "tckdb_client_version_missing"
    TCKDB_CLIENT_VERSION_UNSUPPORTED = "tckdb_client_version_unsupported"
    THERMO_SOURCE_CALCULATION_OWNER_MISMATCH = "thermo_source_calculation_owner_mismatch"
    THERMO_SOURCE_ROLE_TYPE_MISMATCH = "thermo_source_role_type_mismatch"
    THERMO_STATMECH_OWNER_MISMATCH = "thermo_statmech_owner_mismatch"
    TRANSITION_STATE_CHARGE_MISMATCH = "transition_state_charge_mismatch"
    TRANSITION_STATE_COMPOSITION_MISMATCH = "transition_state_composition_mismatch"
    TRANSITION_STATE_IRC_MAPPING_ELEMENT_MISMATCH = "transition_state_irc_mapping_element_mismatch"
    TRANSITION_STATE_NO_IMAGINARY_MODE = "transition_state_no_imaginary_mode"
    TRANSITION_STATE_REACTION_COORDINATE_AMBIGUOUS = "transition_state_reaction_coordinate_ambiguous"
    TRANSITION_STATE_REACTION_COORDINATE_NOT_DESIGNATED = "transition_state_reaction_coordinate_not_designated"
    UNIQUE_CONFLICT = "unique_conflict"
    UNKNOWN_CURATION_POLICY = "unknown_curation_policy"
    UNKNOWN_INCLUDE_TOKEN = "unknown_include_token"
    UNKNOWN_RECORD = "unknown_record"
    UNKNOWN_RECORD_TYPE = "unknown_record_type"
    UNKNOWN_RELEASE = "unknown_release"
    UNKNOWN_RELEASE_ARTIFACT = "unknown_release_artifact"
    UNKNOWN_SELECTION = "unknown_selection"
    UNSAFE_LOWEST_ENERGY_COMPARISON = "unsafe_lowest_energy_comparison"
    UNSUPPORTED_DIRECTION = "unsupported_direction"
    UNSUPPORTED_FILTER = "unsupported_filter"
    UNSUPPORTED_RANKING_FOR_CALCULATION_TYPE = "unsupported_ranking_for_calculation_type"
    UNSUPPORTED_REACTION_MOLECULARITY = "unsupported_reaction_molecularity"
    UNSUPPORTED_RELEASE_RECORD_TYPE = "unsupported_release_record_type"
    WITHDRAW_REASON_REQUIRED = "withdraw_reason_required"


#: Codes carried by an HTTP 422: the payload was refused before
#: anything was written, so nothing was stored and a corrected
#: payload may be sent again under the same idempotency key.
VALIDATION_REJECTION_CODES: frozenset[RejectionCode] = frozenset(
    {
        RejectionCode.APPLIED_ENERGY_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH,
        RejectionCode.APPLIED_ENERGY_CORRECTION_SOURCE_KEY_UNDECLARED,
        RejectionCode.ARRHENIUS_A_UNITS_MOLECULARITY_MISMATCH,
        RejectionCode.ATOM_MAP_ATOMS_UNACCOUNTED_FOR,
        RejectionCode.ATOM_MAP_CONTRADICTS_IRC_MAPPING,
        RejectionCode.ATOM_MAP_ELEMENT_NOT_CONSERVED,
        RejectionCode.ATOM_MAP_GEOMETRY_UNPARSEABLE,
        RejectionCode.ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
        RejectionCode.ATOM_MAP_INFERRED_REQUIRES_NOTE,
        RejectionCode.ATOM_MAP_NOT_A_BIJECTION,
        RejectionCode.ATOM_MAP_PARTICIPANT_NOT_DECLARED,
        RejectionCode.ATOM_MAP_WITHOUT_TRANSITION_STATE,
        RejectionCode.CALCULATION_GEOMETRY_COMPOSITION_MISMATCH,
        RejectionCode.CALCULATION_HANDLE_CONFLICT,
        RejectionCode.CALCULATION_KEY_UNDECLARED,
        RejectionCode.CANONICAL_PARAMETER_VALUE_REQUIRES_KEY,
        RejectionCode.CLIENT_SORT_NOT_SUPPORTED,
        RejectionCode.COMPOSED_SEARCH_CANDIDATE_LIMIT_EXCEEDED,
        RejectionCode.COMPOSED_SEARCH_INVALID_PAGE,
        RejectionCode.COMPOSED_SEARCH_PAGINATION_CHANGED,
        RejectionCode.COMPOSED_SEARCH_PAGINATION_STALLED,
        RejectionCode.CURSOR_OFFSET_CONFLICT,
        RejectionCode.CURSOR_QUERY_MISMATCH,
        RejectionCode.DOI_ALREADY_RECORDED,
        RejectionCode.EXPORT_ALL_CAP_EXCEEDED,
        RejectionCode.EXPORT_SEED_EMPTY,
        RejectionCode.EXPORT_SEED_UNRESOLVED,
        RejectionCode.FREQ_LIST_EXCEEDS_GEOMETRY_DEGREES_OF_FREEDOM,
        RejectionCode.FREQ_N_IMAG_DISAGREES_WITH_MODES,
        RejectionCode.GEOMETRY_TOO_LARGE,
        RejectionCode.HANDLE_TYPE_MISMATCH,
        RejectionCode.INCLUDE_NOT_IMPLEMENTED_YET,
        RejectionCode.INVALID_CURSOR,
        RejectionCode.INVALID_HANDLE,
        RejectionCode.INVALID_PAGINATION,
        RejectionCode.INVALID_RANGE,
        RejectionCode.INVALID_STRUCTURE_QUERY,
        RejectionCode.INVALID_TEMPERATURE_RANGE,
        RejectionCode.KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH,
        RejectionCode.KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH,
        RejectionCode.LEVEL_OF_THEORY_HANDLE_CONFLICT,
        RejectionCode.LIMIT_TOO_LARGE,
        RejectionCode.LOWEST_ENERGY_UNAVAILABLE,
        RejectionCode.MANIFEST_ALREADY_FROZEN,
        RejectionCode.MISSING_FILTER,
        RejectionCode.MISSING_IDENTIFIER,
        RejectionCode.MISSING_REACTION_SEARCH_FILTER,
        RejectionCode.MISSING_STRUCTURE_QUERY,
        RejectionCode.ML_EXPORT_ALL_CAP_EXCEEDED,
        RejectionCode.ML_EXPORT_LOT_UNRESOLVED,
        RejectionCode.ML_EXPORT_SEED_EMPTY,
        RejectionCode.ML_EXPORT_SEED_UNRESOLVED,
        RejectionCode.MULTIPLE_STRUCTURE_QUERIES,
        RejectionCode.N_IMAG_CONTRADICTS_MINIMUM,
        RejectionCode.NON_FINITE_VALUE,
        RejectionCode.OFFSET_TOO_LARGE,
        RejectionCode.PARAMETER_VALUE_REQUIRES_KEY,
        RejectionCode.POST_SEARCH_FIELDS_MUST_BE_IN_BODY,
        RejectionCode.PRESSURE_ALIAS_CONFLICT,
        RejectionCode.QUERY_TOO_EXPENSIVE,
        RejectionCode.RATIONALE_REQUIRED,
        RejectionCode.REACTION_CHARGE_NOT_CONSERVED,
        RejectionCode.REACTION_ENTRY_HANDLE_CONFLICT,
        RejectionCode.REACTION_HANDLE_CONFLICT,
        RejectionCode.REACTION_MASS_BALANCE_FAILED,
        RejectionCode.RECORD_HAS_NO_SUBJECT,
        RejectionCode.RECORD_NOT_APPROVED,
        RejectionCode.RECORD_REF_NOT_SELECTABLE,
        RejectionCode.RECORD_SUBJECT_MISMATCH,
        RejectionCode.RECORD_TYPE_NOT_SELECTABLE,
        RejectionCode.RELEASE_NOT_DRAFT,
        RejectionCode.RELEASE_NOT_PUBLISHED,
        RejectionCode.RELEASE_SCOPING_NOT_IMPLEMENTED,
        RejectionCode.RELEASE_SELECTS_NOTHING,
        RejectionCode.SELECTION_ALREADY_STANDS,
        RejectionCode.SELECTION_ALREADY_SUPERSEDED,
        RejectionCode.SELECTION_NO_LONGER_APPROVED,
        RejectionCode.SMILES_TOO_LONG,
        RejectionCode.SPECIES_ENTRY_HANDLE_CONFLICT,
        RejectionCode.SPECIES_GEOMETRY_COMPOSITION_MISMATCH,
        RejectionCode.SPECIES_GEOMETRY_ISOTOPE_MISMATCH,
        RejectionCode.SPECIES_HANDLE_CONFLICT,
        RejectionCode.SPECIES_KIND_CONFLICT,
        RejectionCode.SPECIES_SMILES_CHARGE_MISMATCH,
        RejectionCode.STATMECH_CALCULATION_KEY_UNDECLARED,
        RejectionCode.STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
        RejectionCode.STATMECH_SOURCE_ROLE_TYPE_MISMATCH,
        RejectionCode.STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH,
        RejectionCode.STORED_SPECIES_SMILES_UNPARSEABLE,
        RejectionCode.SUBJECT_TYPE_MISMATCH,
        RejectionCode.SUPERSEDES_SAME_RECORD,
        RejectionCode.THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
        RejectionCode.THERMO_SOURCE_ROLE_TYPE_MISMATCH,
        RejectionCode.THERMO_STATMECH_OWNER_MISMATCH,
        RejectionCode.TRANSITION_STATE_CHARGE_MISMATCH,
        RejectionCode.TRANSITION_STATE_COMPOSITION_MISMATCH,
        RejectionCode.TRANSITION_STATE_IRC_MAPPING_ELEMENT_MISMATCH,
        RejectionCode.TRANSITION_STATE_NO_IMAGINARY_MODE,
        RejectionCode.TRANSITION_STATE_REACTION_COORDINATE_AMBIGUOUS,
        RejectionCode.TRANSITION_STATE_REACTION_COORDINATE_NOT_DESIGNATED,
        RejectionCode.UNKNOWN_INCLUDE_TOKEN,
        RejectionCode.UNKNOWN_RECORD,
        RejectionCode.UNKNOWN_RECORD_TYPE,
        RejectionCode.UNSAFE_LOWEST_ENERGY_COMPARISON,
        RejectionCode.UNSUPPORTED_DIRECTION,
        RejectionCode.UNSUPPORTED_FILTER,
        RejectionCode.UNSUPPORTED_RANKING_FOR_CALCULATION_TYPE,
        RejectionCode.UNSUPPORTED_REACTION_MOLECULARITY,
        RejectionCode.UNSUPPORTED_RELEASE_RECORD_TYPE,
        RejectionCode.WITHDRAW_REASON_REQUIRED,
    }
)

#: Codes carried by an HTTP 409: the write reached the database
#: and a position it holds refused it, or an idempotency key was
#: reused. A code may appear in both sets -- the same claim can be
#: enforced at the wire boundary and again in the schema, and which
#: one fires depends on the write path, not on what the depositor
#: did wrong. Members in neither set are carried by some other 4xx
#: (404, 426, 429); read the HTTP status for those.
CONFLICT_REJECTION_CODES: frozenset[RejectionCode] = frozenset(
    {
        RejectionCode.ATOM_MAP_ELEMENT_NOT_CONSERVED,
        RejectionCode.ATOM_MAP_NOT_A_BIJECTION,
        RejectionCode.CURATION_POLICY_VERSION_CONFLICT,
        RejectionCode.ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE,
        RejectionCode.IDEMPOTENCY_CONFLICT,
        RejectionCode.NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE,
        RejectionCode.REFERENCE_CONFLICT,
        RejectionCode.RELEASE_TAG_TAKEN,
        RejectionCode.STATE_CONFLICT,
        RejectionCode.STATMECH_SUBJECT_NOT_EXACTLY_ONE,
        RejectionCode.UNIQUE_CONFLICT,
    }
)

#: The HTTP status(es) each code arrives at. Every member appears
#: here, including the ones carried by a status with no named set
#: above -- a 404 that names a missing record, a 426 that asks the
#: client to upgrade, a 429 that asks it to wait. A member with no
#: status would be a code a consumer is told about with no
#: indication of what to do next, which is what
#: ``tests/test_rejection_codes.py`` refuses.
#:
#: A frozenset rather than an int because a claim enforced at the
#: wire boundary and again in the schema reports the same code from
#: both, at two different statuses.
REJECTION_STATUSES: dict[RejectionCode, frozenset[int]] = {
    RejectionCode.APPLIED_ENERGY_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH: frozenset({422}),
    RejectionCode.APPLIED_ENERGY_CORRECTION_SOURCE_KEY_UNDECLARED: frozenset({422}),
    RejectionCode.ARRHENIUS_A_UNITS_MOLECULARITY_MISMATCH: frozenset({422}),
    RejectionCode.ATOM_MAP_ATOMS_UNACCOUNTED_FOR: frozenset({422}),
    RejectionCode.ATOM_MAP_CONTRADICTS_IRC_MAPPING: frozenset({422}),
    RejectionCode.ATOM_MAP_ELEMENT_NOT_CONSERVED: frozenset({409, 422}),
    RejectionCode.ATOM_MAP_GEOMETRY_UNPARSEABLE: frozenset({422}),
    RejectionCode.ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE: frozenset({422}),
    RejectionCode.ATOM_MAP_INFERRED_REQUIRES_NOTE: frozenset({422}),
    RejectionCode.ATOM_MAP_NOT_A_BIJECTION: frozenset({409, 422}),
    RejectionCode.ATOM_MAP_PARTICIPANT_NOT_DECLARED: frozenset({422}),
    RejectionCode.ATOM_MAP_WITHOUT_TRANSITION_STATE: frozenset({422}),
    RejectionCode.CALCULATION_GEOMETRY_COMPOSITION_MISMATCH: frozenset({422}),
    RejectionCode.CALCULATION_HANDLE_CONFLICT: frozenset({422}),
    RejectionCode.CALCULATION_KEY_UNDECLARED: frozenset({422}),
    RejectionCode.CANONICAL_PARAMETER_VALUE_REQUIRES_KEY: frozenset({422}),
    RejectionCode.CLIENT_SORT_NOT_SUPPORTED: frozenset({422}),
    RejectionCode.COMPOSED_SEARCH_CANDIDATE_LIMIT_EXCEEDED: frozenset({422}),
    RejectionCode.COMPOSED_SEARCH_INVALID_PAGE: frozenset({422}),
    RejectionCode.COMPOSED_SEARCH_PAGINATION_CHANGED: frozenset({422}),
    RejectionCode.COMPOSED_SEARCH_PAGINATION_STALLED: frozenset({422}),
    RejectionCode.CURATION_POLICY_VERSION_CONFLICT: frozenset({409}),
    RejectionCode.CURATOR_TASK_NOT_FOUND: frozenset({404}),
    RejectionCode.CURSOR_OFFSET_CONFLICT: frozenset({422}),
    RejectionCode.CURSOR_QUERY_MISMATCH: frozenset({422}),
    RejectionCode.DOI_ALREADY_RECORDED: frozenset({422}),
    RejectionCode.ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE: frozenset({409}),
    RejectionCode.EXPORT_ALL_CAP_EXCEEDED: frozenset({422}),
    RejectionCode.EXPORT_SEED_EMPTY: frozenset({422}),
    RejectionCode.EXPORT_SEED_UNRESOLVED: frozenset({422}),
    RejectionCode.FREQ_LIST_EXCEEDS_GEOMETRY_DEGREES_OF_FREEDOM: frozenset({422}),
    RejectionCode.FREQ_N_IMAG_DISAGREES_WITH_MODES: frozenset({422}),
    RejectionCode.GEOMETRY_TOO_LARGE: frozenset({422}),
    RejectionCode.HANDLE_NOT_FOUND: frozenset({404}),
    RejectionCode.HANDLE_TYPE_MISMATCH: frozenset({422}),
    RejectionCode.IDEMPOTENCY_CONFLICT: frozenset({409}),
    RejectionCode.INCLUDE_NOT_IMPLEMENTED_YET: frozenset({422}),
    RejectionCode.INVALID_CURSOR: frozenset({422}),
    RejectionCode.INVALID_HANDLE: frozenset({422}),
    RejectionCode.INVALID_IDEMPOTENCY_KEY: frozenset({400}),
    RejectionCode.INVALID_PAGINATION: frozenset({422}),
    RejectionCode.INVALID_RANGE: frozenset({422}),
    RejectionCode.INVALID_STRUCTURE_QUERY: frozenset({422}),
    RejectionCode.INVALID_TEMPERATURE_RANGE: frozenset({422}),
    RejectionCode.IRC_RESULT_NOT_FOUND: frozenset({404}),
    RejectionCode.KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH: frozenset({422}),
    RejectionCode.KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH: frozenset({422}),
    RejectionCode.LEVEL_OF_THEORY_HANDLE_CONFLICT: frozenset({422}),
    RejectionCode.LIMIT_TOO_LARGE: frozenset({422}),
    RejectionCode.LOWEST_ENERGY_UNAVAILABLE: frozenset({422}),
    RejectionCode.MANIFEST_ALREADY_FROZEN: frozenset({422}),
    RejectionCode.MANIFEST_NOT_FROZEN: frozenset({404}),
    RejectionCode.MISSING_FILTER: frozenset({422}),
    RejectionCode.MISSING_IDENTIFIER: frozenset({422}),
    RejectionCode.MISSING_REACTION_SEARCH_FILTER: frozenset({422}),
    RejectionCode.MISSING_STRUCTURE_QUERY: frozenset({422}),
    RejectionCode.ML_EXPORT_ALL_CAP_EXCEEDED: frozenset({422}),
    RejectionCode.ML_EXPORT_LOT_UNRESOLVED: frozenset({422}),
    RejectionCode.ML_EXPORT_SEED_EMPTY: frozenset({422}),
    RejectionCode.ML_EXPORT_SEED_UNRESOLVED: frozenset({422}),
    RejectionCode.MULTIPLE_STRUCTURE_QUERIES: frozenset({422}),
    RejectionCode.N_IMAG_CONTRADICTS_MINIMUM: frozenset({422}),
    RejectionCode.NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE: frozenset({409}),
    RejectionCode.NON_FINITE_VALUE: frozenset({422}),
    RejectionCode.OFFSET_TOO_LARGE: frozenset({422}),
    RejectionCode.OWNER_MISSING: frozenset({404}),
    RejectionCode.PARAMETER_VALUE_REQUIRES_KEY: frozenset({422}),
    RejectionCode.PATH_SEARCH_RESULT_NOT_FOUND: frozenset({404}),
    RejectionCode.POST_SEARCH_FIELDS_MUST_BE_IN_BODY: frozenset({422}),
    RejectionCode.PRESSURE_ALIAS_CONFLICT: frozenset({422}),
    RejectionCode.QUERY_TOO_EXPENSIVE: frozenset({422}),
    RejectionCode.RATE_LIMIT_EXCEEDED: frozenset({429}),
    RejectionCode.RATIONALE_REQUIRED: frozenset({422}),
    RejectionCode.REACTION_CHARGE_NOT_CONSERVED: frozenset({422}),
    RejectionCode.REACTION_ENTRY_HANDLE_CONFLICT: frozenset({422}),
    RejectionCode.REACTION_HANDLE_CONFLICT: frozenset({422}),
    RejectionCode.REACTION_MASS_BALANCE_FAILED: frozenset({422}),
    RejectionCode.RECORD_HAS_NO_SUBJECT: frozenset({422}),
    RejectionCode.RECORD_NOT_APPROVED: frozenset({422}),
    RejectionCode.RECORD_REF_NOT_SELECTABLE: frozenset({422}),
    RejectionCode.RECORD_SUBJECT_MISMATCH: frozenset({422}),
    RejectionCode.RECORD_TYPE_NOT_SELECTABLE: frozenset({422}),
    RejectionCode.REFERENCE_CONFLICT: frozenset({409}),
    RejectionCode.RELEASE_NOT_DRAFT: frozenset({422}),
    RejectionCode.RELEASE_NOT_PUBLISHED: frozenset({422}),
    RejectionCode.RELEASE_SCOPING_NOT_IMPLEMENTED: frozenset({422}),
    RejectionCode.RELEASE_SELECTS_NOTHING: frozenset({422}),
    RejectionCode.RELEASE_TAG_TAKEN: frozenset({409}),
    RejectionCode.SCAN_RESULT_NOT_FOUND: frozenset({404}),
    RejectionCode.SELECTION_ALREADY_STANDS: frozenset({422}),
    RejectionCode.SELECTION_ALREADY_SUPERSEDED: frozenset({422}),
    RejectionCode.SELECTION_NO_LONGER_APPROVED: frozenset({422}),
    RejectionCode.SMILES_TOO_LONG: frozenset({422}),
    RejectionCode.SPECIES_ENTRY_HANDLE_CONFLICT: frozenset({422}),
    RejectionCode.SPECIES_GEOMETRY_COMPOSITION_MISMATCH: frozenset({422}),
    RejectionCode.SPECIES_GEOMETRY_ISOTOPE_MISMATCH: frozenset({422}),
    RejectionCode.SPECIES_HANDLE_CONFLICT: frozenset({422}),
    RejectionCode.SPECIES_KIND_CONFLICT: frozenset({422}),
    RejectionCode.SPECIES_SMILES_CHARGE_MISMATCH: frozenset({422}),
    RejectionCode.STATE_CONFLICT: frozenset({409}),
    RejectionCode.STATMECH_CALCULATION_KEY_UNDECLARED: frozenset({422}),
    RejectionCode.STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH: frozenset({422}),
    RejectionCode.STATMECH_SOURCE_ROLE_TYPE_MISMATCH: frozenset({422}),
    RejectionCode.STATMECH_SUBJECT_NOT_EXACTLY_ONE: frozenset({409}),
    RejectionCode.STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH: frozenset({422}),
    RejectionCode.STORED_SPECIES_SMILES_UNPARSEABLE: frozenset({422}),
    RejectionCode.SUBJECT_TYPE_MISMATCH: frozenset({422}),
    RejectionCode.SUPERSEDES_SAME_RECORD: frozenset({422}),
    RejectionCode.TCKDB_CLIENT_VERSION_INVALID: frozenset({426}),
    RejectionCode.TCKDB_CLIENT_VERSION_MISSING: frozenset({426}),
    RejectionCode.TCKDB_CLIENT_VERSION_UNSUPPORTED: frozenset({426}),
    RejectionCode.THERMO_SOURCE_CALCULATION_OWNER_MISMATCH: frozenset({422}),
    RejectionCode.THERMO_SOURCE_ROLE_TYPE_MISMATCH: frozenset({422}),
    RejectionCode.THERMO_STATMECH_OWNER_MISMATCH: frozenset({422}),
    RejectionCode.TRANSITION_STATE_CHARGE_MISMATCH: frozenset({422}),
    RejectionCode.TRANSITION_STATE_COMPOSITION_MISMATCH: frozenset({422}),
    RejectionCode.TRANSITION_STATE_IRC_MAPPING_ELEMENT_MISMATCH: frozenset({422}),
    RejectionCode.TRANSITION_STATE_NO_IMAGINARY_MODE: frozenset({422}),
    RejectionCode.TRANSITION_STATE_REACTION_COORDINATE_AMBIGUOUS: frozenset({422}),
    RejectionCode.TRANSITION_STATE_REACTION_COORDINATE_NOT_DESIGNATED: frozenset({422}),
    RejectionCode.UNIQUE_CONFLICT: frozenset({409}),
    RejectionCode.UNKNOWN_CURATION_POLICY: frozenset({404}),
    RejectionCode.UNKNOWN_INCLUDE_TOKEN: frozenset({422}),
    RejectionCode.UNKNOWN_RECORD: frozenset({422}),
    RejectionCode.UNKNOWN_RECORD_TYPE: frozenset({422}),
    RejectionCode.UNKNOWN_RELEASE: frozenset({404}),
    RejectionCode.UNKNOWN_RELEASE_ARTIFACT: frozenset({404}),
    RejectionCode.UNKNOWN_SELECTION: frozenset({404}),
    RejectionCode.UNSAFE_LOWEST_ENERGY_COMPARISON: frozenset({422}),
    RejectionCode.UNSUPPORTED_DIRECTION: frozenset({422}),
    RejectionCode.UNSUPPORTED_FILTER: frozenset({422}),
    RejectionCode.UNSUPPORTED_RANKING_FOR_CALCULATION_TYPE: frozenset({422}),
    RejectionCode.UNSUPPORTED_REACTION_MOLECULARITY: frozenset({422}),
    RejectionCode.UNSUPPORTED_RELEASE_RECORD_TYPE: frozenset({422}),
    RejectionCode.WITHDRAW_REASON_REQUIRED: frozenset({422}),
}


def rejection_code(value: object) -> RejectionCode | None:
    """Return the member for *value*, or ``None`` if this client cannot name it.

    ``None`` covers three genuinely different situations, and a caller
    should treat all three the same way -- as a refusal it does not have
    a specific branch for: the server is newer than this client and sent
    a code added since; the refusal was not a scientific one (a rate
    limit, an authentication failure, a generic conflict); or there was
    no code at all.
    """
    if not isinstance(value, str):
        return None
    try:
        return RejectionCode(value)
    except ValueError:
        return None
