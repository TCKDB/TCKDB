"""Client-side properties of the generated rejection-code enum.

The staleness gate lives on the server, where the register can be
imported. These are the properties a *consumer* depends on and that the
server-side gate cannot see, because the client package is what has to
keep working when it is pinned against a newer server.
"""

from __future__ import annotations

import pytest

from tckdb_client import (
    CONFLICT_REJECTION_CODES,
    RejectionCode,
    VALIDATION_REJECTION_CODES,
    rejection_code,
)


def test_a_member_is_the_wire_string() -> None:
    """A caller may compare against the raw ``code`` field without converting."""
    assert RejectionCode.REACTION_MASS_BALANCE_FAILED == "reaction_mass_balance_failed"
    assert str(RejectionCode.REACTION_MASS_BALANCE_FAILED.value) == (
        "reaction_mass_balance_failed"
    )


def test_member_names_are_the_upper_cased_codes() -> None:
    """The naming rule is the whole ergonomic claim, so it is checked.

    ``RejectionCode.REACTION_MASS_BALANCE_FAILED`` has to be guessable
    from the string a server sent, or a consumer goes back to pasting
    literals -- which is the failure this file exists to remove.
    """
    for member in RejectionCode:
        assert member.name == member.value.upper()


def test_an_unknown_code_is_none_rather_than_an_exception() -> None:
    """A newer server must not break an older client.

    This is the property that makes the enum safe to use in a ``match``
    at all. Servers are routinely ahead of the clients pinned against
    them, so a code added after this file was generated has to degrade to
    "a refusal I have no branch for" -- not to a ``ValueError`` raised
    inside the caller's own error handler.
    """
    assert rejection_code("some_code_added_next_year") is None
    with pytest.raises(ValueError):
        RejectionCode("some_code_added_next_year")


@pytest.mark.parametrize("value", [None, 42, b"reaction_mass_balance_failed", {}])
def test_a_non_string_code_is_none(value: object) -> None:
    """``exc.code`` is ``str | None``; a missing code is not a crash."""
    assert rejection_code(value) is None


def test_a_known_code_round_trips() -> None:
    assert rejection_code("reaction_mass_balance_failed") is (
        RejectionCode.REACTION_MASS_BALANCE_FAILED
    )


def test_the_status_sets_partition_nothing_and_cover_everything() -> None:
    """Every member is reachable through at least one status.

    Not a partition: a claim enforced both at the wire boundary and by a
    check constraint reports the same code from both, so the two sets
    legitimately overlap. What must not happen is a member in neither --
    a code a client is told about with no indication of what it means for
    retrying.
    """
    covered = VALIDATION_REJECTION_CODES | CONFLICT_REJECTION_CODES
    orphans = sorted(member.value for member in RejectionCode if member not in covered)
    assert not orphans, orphans
    assert VALIDATION_REJECTION_CODES & CONFLICT_REJECTION_CODES, (
        "No code is enforced at both the wire boundary and in the schema. That "
        "may be legitimate, but it used to be true of the atom-map checks and "
        "losing it silently would mean a dual-enforcement claim stopped "
        "reporting one code from both sites."
    )


def test_the_enum_is_not_empty_and_covers_the_conservation_laws() -> None:
    """Guard the guard, and pin the entries a client is most likely to branch on."""
    assert len(RejectionCode) >= 10
    assert RejectionCode.REACTION_MASS_BALANCE_FAILED in VALIDATION_REJECTION_CODES
    assert RejectionCode.REACTION_CHARGE_NOT_CONSERVED in VALIDATION_REJECTION_CODES
    assert RejectionCode.ATOM_MAP_ELEMENT_NOT_CONSERVED in CONFLICT_REJECTION_CODES
