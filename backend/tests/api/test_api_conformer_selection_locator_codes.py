"""Two opposite repairs that used to share one sentence, on the wire.

What was wrong
--------------
``/uploads/kinetics`` lets an interpretation assignment cite a stored
conformer selection by *describing* it —
``{species_entry, selection_kind, assignment_scheme_ref}`` — and expects
the description to pick out exactly one row. Both ways that can fail
raised one bare ``ValueError``:

    conformer_selection content locator must resolve exactly one selection.

Measured before the change, both arrived byte-identically as
``422 {"code": "validation_error", "context": {}}``. They are opposite
repairs. **Zero** matched means the row is not there: deposit it, or fix
the species entry named. **Several** matched means every row the
description could mean exists and the description was under-determined. A
client got one string and could not tell which.

Now: zero is ``404 unknown_conformer_selection``, several is
``422 ambiguous_conformer_selection_locator``.

Why these are on the wire
-------------------------
The classification itself is unit-tested in
``tests/services/test_conformer_selection_locator.py``, which is the only
place two of its three outcomes can be reached at all. Only a request can
establish that the envelope carries the code and the context out to a
client, and that the split did not come at the cost of the accepting path
— so the exactly-one case is asserted here too. A test that only checks an
error arrived passes against a resolver that refuses everything.

Assertions are on ``(status, code)`` and on ``context`` keys, never on
substrings of ``detail``.
"""

from __future__ import annotations

from tests.api.test_api_statmech_citation_ownership import (
    _KINETICS,
    _assignments,
    _rate,
    _seed_participants,
)
from tests.services.scientific_read._factories import (
    attach_conformer_selection,
    make_conformer_group,
)

_UNKNOWN = "unknown_conformer_selection"
_AMBIGUOUS = "ambiguous_conformer_selection_locator"

#: ``CH3``, the first reactant of the reaction the shared fixture declares.
_CH3 = {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}


def _payload(statmechs, *, species_entry=None, selection_kind="lowest_energy"):
    """A correct rate whose first reactant also cites a conformer selection.

    Everything but the locator is valid: the statmechs are the right ones
    for the right participants and the interpretation set is complete. That
    is what makes a refusal here attributable to the locator rather than to
    one of the four guards above it.
    """
    return _rate(
        _assignments(
            statmechs,
            **{
                "reactant:1": {
                    "conformer_selection": {
                        "species_entry": species_entry or _CH3,
                        "selection_kind": selection_kind,
                    }
                }
            },
        )
    )


# ---------------------------------------------------------------------------
# Zero matched -> 404
# ---------------------------------------------------------------------------


def test_a_locator_matching_nothing_is_a_404_with_its_own_code(
    client, db_session
) -> None:
    """No conformer selection exists for CH3 at all."""
    _entries, statmechs = _seed_participants(db_session)

    resp = client.post(_KINETICS, json=_payload(statmechs))

    assert resp.status_code == 404, resp.text[:800]
    body = resp.json()
    assert body["code"] == _UNKNOWN, body
    assert body["context"]["field"] == (
        "interpretation_assignments[0].conformer_selection"
    ), body
    assert body["context"]["kind"] == "conformer_selection", body
    assert body["context"]["selection_kind"] == "lowest_energy", body
    # The locator named no scheme, so there is no scheme to echo.
    assert "assignment_scheme_ref" not in body["context"], body


def test_a_selection_of_the_wrong_kind_still_matches_nothing(
    client, db_session
) -> None:
    """The species entry has a selection; the locator asks for another kind.

    The distinction that matters: this is not "no conformer data for this
    species", it is "nothing answering this description", and the same 404
    covers both because the same deposit repairs both.
    """
    entries, statmechs = _seed_participants(db_session)
    attach_conformer_selection(
        db_session, conformer_group=make_conformer_group(db_session, entries["ch3"])
    )

    resp = client.post(
        _KINETICS, json=_payload(statmechs, selection_kind="curator_pick")
    )

    assert resp.status_code == 404, resp.text[:800]
    body = resp.json()
    assert body["code"] == _UNKNOWN, body
    assert body["context"]["selection_kind"] == "curator_pick", body


# ---------------------------------------------------------------------------
# Several matched -> 422, reporting what differs
# ---------------------------------------------------------------------------


def test_two_labelled_groups_make_the_locator_ambiguous(client, db_session) -> None:
    """One species entry, two conformer groups, one selection each.

    ``conformer_group`` is unique on ``(species_entry_id, label)`` and the
    locator joins on ``species_entry_id`` alone, which is what makes this
    reachable: two distinct basins, each with a ``lowest_energy`` selection
    under no assignment scheme.

    The discriminating axis is the group *label*, which the locator has no
    field for — so ``locator_can_express`` is ``False`` and the refusal
    says the difference cannot be expressed rather than telling the
    depositor to add something that does not exist.
    """
    entries, statmechs = _seed_participants(db_session)
    for label in ("basin-A", "basin-B"):
        attach_conformer_selection(
            db_session,
            conformer_group=make_conformer_group(
                db_session, entries["ch3"], label=label
            ),
        )

    resp = client.post(_KINETICS, json=_payload(statmechs))

    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _AMBIGUOUS, body
    context = body["context"]
    assert context["field"] == (
        "interpretation_assignments[0].conformer_selection"
    ), body
    assert context["kind"] == "conformer_selection", body
    assert context["match_count"] == 2, body
    assert context["discriminator"] == "conformer_group_label", body
    assert context["discriminator_values"] == ["basin-A", "basin-B"], body
    assert context["differs_by"] == [
        "conformer_group_label",
        "conformer_group_ref",
    ], body
    assert context["locator_can_express"] is False, body


def test_two_unlabelled_groups_fall_back_to_the_group_ref(client, db_session) -> None:
    """The same ambiguity with nothing to print but the group's public ref.

    ``uq_conformer_group_species_entry_id`` does not declare
    ``postgresql_nulls_not_distinct``, so a species entry may own several
    groups with no label — and then the label separates nothing. Reporting
    the public refs keeps this out of the "differ by nothing" outcome,
    which would have called two real curation rows a duplicate.
    """
    entries, statmechs = _seed_participants(db_session)
    groups = [
        make_conformer_group(db_session, entries["ch3"], label=None) for _ in range(2)
    ]
    for group in groups:
        attach_conformer_selection(db_session, conformer_group=group)

    resp = client.post(_KINETICS, json=_payload(statmechs))

    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _AMBIGUOUS, body
    context = body["context"]
    assert context["match_count"] == 2, body
    assert context["differs_by"] == ["conformer_group_ref"], body
    assert context["discriminator"] == "conformer_group_ref", body
    assert sorted(context["discriminator_values"]) == sorted(
        group.public_ref for group in groups
    ), body
    assert context["locator_can_express"] is False, body


def test_a_third_group_is_counted_and_listed(client, db_session) -> None:
    """``match_count`` is the real count, not the old ``limit(2)`` fetch.

    The lookup used to stop at two rows because it only needed to know
    whether the answer was exactly one. A refusal that says how many
    matched has to actually count them, and "2" for three matches is the
    shape of wrong that reads as right.
    """
    entries, statmechs = _seed_participants(db_session)
    for label in ("basin-A", "basin-B", "basin-C"):
        attach_conformer_selection(
            db_session,
            conformer_group=make_conformer_group(
                db_session, entries["ch3"], label=label
            ),
        )

    resp = client.post(_KINETICS, json=_payload(statmechs))

    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _AMBIGUOUS, body
    assert body["context"]["match_count"] == 3, body
    assert body["context"]["discriminator_values"] == [
        "basin-A",
        "basin-B",
        "basin-C",
    ], body


# ---------------------------------------------------------------------------
# Exactly one -> still accepted
# ---------------------------------------------------------------------------


def test_a_locator_matching_exactly_one_selection_is_still_accepted(
    client, db_session
) -> None:
    """The half that fails against a resolver which refuses everything.

    Same route, same field, same locator shape as both refusals above --
    the only difference is how many selections the database holds.
    """
    entries, statmechs = _seed_participants(db_session)
    attach_conformer_selection(
        db_session, conformer_group=make_conformer_group(db_session, entries["ch3"])
    )

    resp = client.post(_KINETICS, json=_payload(statmechs))

    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# The two are distinguishable, which is the whole point
# ---------------------------------------------------------------------------


def test_the_two_halves_no_longer_share_a_status_or_a_code(
    client, db_session
) -> None:
    """One assertion over both, so collapsing them cannot pass.

    Before this change both bodies were byte-identical:
    ``422 {"code": "validation_error", "detail": "conformer_selection
    content locator must resolve exactly one selection.", "context": {}}``.
    Asserting each half separately would still pass if a later edit gave
    them one code again, provided each assertion were updated on its own;
    comparing the pair is what refuses that.
    """
    entries, statmechs = _seed_participants(db_session)

    zero = client.post(_KINETICS, json=_payload(statmechs))

    for label in ("basin-A", "basin-B"):
        attach_conformer_selection(
            db_session,
            conformer_group=make_conformer_group(
                db_session, entries["ch3"], label=label
            ),
        )
    several = client.post(_KINETICS, json=_payload(statmechs))

    assert (zero.status_code, zero.json()["code"]) == (404, _UNKNOWN), zero.text[:400]
    assert (several.status_code, several.json()["code"]) == (
        422,
        _AMBIGUOUS,
    ), several.text[:400]
    assert zero.status_code != several.status_code
    assert zero.json()["code"] != several.json()["code"]
    assert zero.json()["context"] != several.json()["context"]
