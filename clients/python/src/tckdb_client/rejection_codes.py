"""Machine-readable codes TCKDB reports when it refuses a deposit.

**Generated. Do not edit by hand.** Regenerate with
``conda run -n tckdb_env python backend/scripts/generate_client_rejection_codes.py``;
``backend/tests/api/test_client_rejection_codes_generated.py`` fails if
this file and the server's scientific check register disagree.

Every member here is a position TCKDB takes about chemistry -- a claim a
referee could argue with -- and each one is proved to arrive in a real
HTTP response body by a test on the server side. The register that
generates this file is rendered for humans at
``docs/guides/scientific_check_register.md``, which is where to look for
what a code asserts, why it refuses rather than warns, and what the
escape hatch is for legitimate chemistry it would otherwise reject.

Deliberately absent: warning codes, which arrive alongside an *accepted*
upload, and trust labels, which are applied at read time and refuse
nothing. Both would be misread as failures under this name.

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
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CONFLICT_REJECTION_CODES",
    "RejectionCode",
    "VALIDATION_REJECTION_CODES",
    "rejection_code",
]


class RejectionCode(str, Enum):
    """A code TCKDB reports in the ``code`` field of a refusal body.

    ``str`` subclass so a member compares equal to the wire string and
    can be used wherever the raw code was.
    """

    ARRHENIUS_A_UNITS_MOLECULARITY_MISMATCH = "arrhenius_a_units_molecularity_mismatch"
    ATOM_MAP_ATOMS_UNACCOUNTED_FOR = "atom_map_atoms_unaccounted_for"
    ATOM_MAP_CONTRADICTS_IRC_MAPPING = "atom_map_contradicts_irc_mapping"
    ATOM_MAP_ELEMENT_NOT_CONSERVED = "atom_map_element_not_conserved"
    ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE = "atom_map_indices_not_geometry_relative"
    ATOM_MAP_INFERRED_REQUIRES_NOTE = "atom_map_inferred_requires_note"
    ATOM_MAP_NOT_A_BIJECTION = "atom_map_not_a_bijection"
    ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE = "energy_transfer_scope_columns_disagree"
    FREQ_N_IMAG_DISAGREES_WITH_MODES = "freq_n_imag_disagrees_with_modes"
    N_IMAG_CONTRADICTS_MINIMUM = "n_imag_contradicts_minimum"
    NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE = "network_solve_reported_requires_literature"
    REACTION_CHARGE_NOT_CONSERVED = "reaction_charge_not_conserved"
    REACTION_MASS_BALANCE_FAILED = "reaction_mass_balance_failed"
    SPECIES_GEOMETRY_COMPOSITION_MISMATCH = "species_geometry_composition_mismatch"
    SPECIES_GEOMETRY_ISOTOPE_MISMATCH = "species_geometry_isotope_mismatch"
    SPECIES_KIND_CONFLICT = "species_kind_conflict"
    SPECIES_SMILES_CHARGE_MISMATCH = "species_smiles_charge_mismatch"
    STATMECH_SUBJECT_NOT_EXACTLY_ONE = "statmech_subject_not_exactly_one"
    TRANSITION_STATE_CHARGE_MISMATCH = "transition_state_charge_mismatch"
    TRANSITION_STATE_COMPOSITION_MISMATCH = "transition_state_composition_mismatch"
    TRANSITION_STATE_IRC_MAPPING_ELEMENT_MISMATCH = "transition_state_irc_mapping_element_mismatch"
    TRANSITION_STATE_NO_IMAGINARY_MODE = "transition_state_no_imaginary_mode"
    TRANSITION_STATE_REACTION_COORDINATE_AMBIGUOUS = "transition_state_reaction_coordinate_ambiguous"
    TRANSITION_STATE_REACTION_COORDINATE_NOT_DESIGNATED = "transition_state_reaction_coordinate_not_designated"


#: Codes carried by an HTTP 422: the payload was refused before
#: anything was written, so nothing was stored and a corrected
#: payload may be sent again under the same idempotency key.
VALIDATION_REJECTION_CODES: frozenset[RejectionCode] = frozenset(
    {
        RejectionCode.ARRHENIUS_A_UNITS_MOLECULARITY_MISMATCH,
        RejectionCode.ATOM_MAP_ATOMS_UNACCOUNTED_FOR,
        RejectionCode.ATOM_MAP_CONTRADICTS_IRC_MAPPING,
        RejectionCode.ATOM_MAP_ELEMENT_NOT_CONSERVED,
        RejectionCode.ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
        RejectionCode.ATOM_MAP_INFERRED_REQUIRES_NOTE,
        RejectionCode.ATOM_MAP_NOT_A_BIJECTION,
        RejectionCode.FREQ_N_IMAG_DISAGREES_WITH_MODES,
        RejectionCode.N_IMAG_CONTRADICTS_MINIMUM,
        RejectionCode.REACTION_CHARGE_NOT_CONSERVED,
        RejectionCode.REACTION_MASS_BALANCE_FAILED,
        RejectionCode.SPECIES_GEOMETRY_COMPOSITION_MISMATCH,
        RejectionCode.SPECIES_GEOMETRY_ISOTOPE_MISMATCH,
        RejectionCode.SPECIES_KIND_CONFLICT,
        RejectionCode.SPECIES_SMILES_CHARGE_MISMATCH,
        RejectionCode.TRANSITION_STATE_CHARGE_MISMATCH,
        RejectionCode.TRANSITION_STATE_COMPOSITION_MISMATCH,
        RejectionCode.TRANSITION_STATE_IRC_MAPPING_ELEMENT_MISMATCH,
        RejectionCode.TRANSITION_STATE_NO_IMAGINARY_MODE,
        RejectionCode.TRANSITION_STATE_REACTION_COORDINATE_AMBIGUOUS,
        RejectionCode.TRANSITION_STATE_REACTION_COORDINATE_NOT_DESIGNATED,
    }
)

#: Codes carried by an HTTP 409: a position PostgreSQL holds
#: refused the write. A code may appear in both sets -- the same
#: claim can be enforced at the wire boundary and again in the
#: schema, and which one fires depends on the write path, not on
#: what the depositor did wrong.
CONFLICT_REJECTION_CODES: frozenset[RejectionCode] = frozenset(
    {
        RejectionCode.ATOM_MAP_ELEMENT_NOT_CONSERVED,
        RejectionCode.ATOM_MAP_NOT_A_BIJECTION,
        RejectionCode.ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE,
        RejectionCode.NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE,
        RejectionCode.STATMECH_SUBJECT_NOT_EXACTLY_ONE,
    }
)


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
