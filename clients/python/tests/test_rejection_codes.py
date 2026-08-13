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
    REJECTION_STATUSES,
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


def test_every_member_says_what_status_it_arrives_at() -> None:
    """No member without an indication of what it means for retrying.

    This used to read "every member is in ``VALIDATION`` or ``CONFLICT``",
    which was the same property while 422 and 409 were the only statuses
    a member could arrive at. They no longer are: the enum now carries the
    404 that names a missing record, the 426 that asks the client to
    upgrade, and the 429 that asks it to wait, and those are exactly the
    codes whose retry advice a caller most needs. The property being
    protected was never "in one of two sets" -- it was "not told about a
    code with nothing to do about it" -- so it is asserted against the
    status map, which covers every member.

    The two named sets are then held to being *exactly* their statuses, so
    neither can quietly gain or lose a member.
    """
    orphans = sorted(
        member.value for member in RejectionCode if member not in REJECTION_STATUSES
    )
    assert not orphans, orphans

    assert VALIDATION_REJECTION_CODES == {
        member for member, statuses in REJECTION_STATUSES.items() if 422 in statuses
    }
    assert CONFLICT_REJECTION_CODES == {
        member for member, statuses in REJECTION_STATUSES.items() if 409 in statuses
    }
    assert all(statuses for statuses in REJECTION_STATUSES.values()), (
        "a member mapped to no status at all is the orphan this test exists "
        "to refuse, wearing an empty frozenset"
    )


def test_a_claim_enforced_twice_still_reports_from_both_sites() -> None:
    """The two sets must overlap, and the overlap must be dual enforcement.

    Not a partition: the atom map's element conservation and bijectivity
    are stated once at the wire boundary and again as a check constraint,
    so the same code arrives as a 422 or a 409 depending on the write
    path. Losing the overlap silently would mean a dual-enforcement claim
    had stopped reporting one code from both sites -- which happened while
    the code catalogue was being written, and this assertion is what would
    have caught it.
    """
    both = VALIDATION_REJECTION_CODES & CONFLICT_REJECTION_CODES
    assert both, (
        "No code is enforced at both the wire boundary and in the schema. That "
        "may be legitimate, but it used to be true of the atom-map checks and "
        "losing it silently would mean a dual-enforcement claim stopped "
        "reporting one code from both sites."
    )
    for member in both:
        assert REJECTION_STATUSES[member] >= {409, 422}


def test_the_enum_is_not_empty_and_covers_the_conservation_laws() -> None:
    """Guard the guard, and pin the entries a client is most likely to branch on."""
    assert len(RejectionCode) >= 10
    assert RejectionCode.REACTION_MASS_BALANCE_FAILED in VALIDATION_REJECTION_CODES
    assert RejectionCode.REACTION_CHARGE_NOT_CONSERVED in VALIDATION_REJECTION_CODES
    assert RejectionCode.ATOM_MAP_ELEMENT_NOT_CONSERVED in CONFLICT_REJECTION_CODES
