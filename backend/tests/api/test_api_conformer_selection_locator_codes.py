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

from sqlalchemy import select

from app.db.models.kinetics import KineticsInterpretationAssignment
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
_UNKNOWN_GROUP = "unknown_conformer_group_ref"
_GROUP_OWNER_MISMATCH = "kinetics_interpretation_conformer_selection_owner_mismatch"

#: ``CH3``, the first reactant of the reaction the shared fixture declares.
_CH3 = {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}

_FIELD = "interpretation_assignments[0].conformer_selection"
_GROUP_FIELD = f"{_FIELD}.conformer_group_ref"


def _payload(
    statmechs,
    *,
    species_entry=None,
    selection_kind="lowest_energy",
    conformer_group_ref=None,
):
    """A correct rate whose first reactant also cites a conformer selection.

    Everything but the locator is valid: the statmechs are the right ones
    for the right participants and the interpretation set is complete. That
    is what makes a refusal here attributable to the locator rather than to
    one of the four guards above it.

    ``conformer_group_ref`` is added to the locator only when given, so
    every payload built without it is byte-identical to the one this
    helper produced before the field existed. The tests that assert the
    old behaviour is unchanged are therefore asserting it about the same
    request body, not about a near-miss of it.
    """
    locator = {
        "species_entry": species_entry or _CH3,
        "selection_kind": selection_kind,
    }
    if conformer_group_ref is not None:
        locator["conformer_group_ref"] = conformer_group_ref
    return _rate(
        _assignments(statmechs, **{"reactant:1": {"conformer_selection": locator}})
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

    The discriminating axis is the conformer group. The labels differ too
    and are reported in ``differs_by``, but the axis the refusal *names*
    is ``conformer_group_ref``, because that is the one the locator has a
    field for — the label has none, and naming it would send the depositor
    after something they cannot set. Before that field existed this
    refusal named the label and reported
    ``locator_can_express: false``, which meant no corrected request
    existed at all; ``test_the_new_field_is_optional_and_its_absence_still_refuses``
    holds the pair.
    """
    entries, statmechs = _seed_participants(db_session)
    groups = [
        make_conformer_group(db_session, entries["ch3"], label=label)
        for label in ("basin-A", "basin-B")
    ]
    for group in groups:
        attach_conformer_selection(db_session, conformer_group=group)

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
    assert context["discriminator"] == "conformer_group_ref", body
    assert context["discriminator_values"] == [
        group.public_ref for group in groups
    ], body
    # The label still varies and is still reported; it is simply not the
    # axis the advice names.
    assert context["differs_by"] == [
        "conformer_group_ref",
        "conformer_group_label",
    ], body
    assert context["locator_can_express"] is True, body


def test_two_unlabelled_groups_fall_back_to_the_group_ref(client, db_session) -> None:
    """The same ambiguity with nothing to print but the group's public ref.

    ``uq_conformer_group_species_entry_id`` does not declare
    ``postgresql_nulls_not_distinct``, so a species entry may own several
    groups with no label — and then the label separates nothing. Reporting
    the public refs keeps this out of the "differ by nothing" outcome,
    which would have called two real curation rows a duplicate.

    It is also why the locator's new field is a public ref and not a
    label: here there is no label to write in one.
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
    assert context["locator_can_express"] is True, body


def test_a_third_group_is_counted_and_listed(client, db_session) -> None:
    """``match_count`` is the real count, not the old ``limit(2)`` fetch.

    The lookup used to stop at two rows because it only needed to know
    whether the answer was exactly one. A refusal that says how many
    matched has to actually count them, and "2" for three matches is the
    shape of wrong that reads as right.
    """
    entries, statmechs = _seed_participants(db_session)
    groups = [
        make_conformer_group(db_session, entries["ch3"], label=label)
        for label in ("basin-A", "basin-B", "basin-C")
    ]
    for group in groups:
        attach_conformer_selection(db_session, conformer_group=group)

    resp = client.post(_KINETICS, json=_payload(statmechs))

    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _AMBIGUOUS, body
    assert body["context"]["match_count"] == 3, body
    # All three refs, in label order -- three values, not the first two,
    # and the depositor picks one of them for ``conformer_group_ref``.
    assert body["context"]["discriminator_values"] == [
        group.public_ref for group in groups
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


# ---------------------------------------------------------------------------
# The repair: naming the conformer group
# ---------------------------------------------------------------------------


def test_a_group_ref_picks_one_of_two_otherwise_identical_selections(
    client, db_session
) -> None:
    """The scenario the ambiguity refusal had no repair for.

    Two conformer groups under one species entry, each carrying a
    ``lowest_energy`` selection with no assignment scheme. Every other
    field of the locator is already pinned -- ``species_entry`` and
    ``selection_kind`` are what the ``WHERE`` clause filters on and
    ``assignment_scheme_ref`` is NULL on both rows -- so before
    ``conformer_group_ref`` existed this request had no accepting form at
    all, and the 422 said so with ``locator_can_express: false``.

    Asserted on both groups, not one: a filter that ignored the ref would
    still refuse (two matches), but a filter that always picked the first
    row would pass for one group and fail for the other.
    """
    entries, statmechs = _seed_participants(db_session)
    groups = [
        make_conformer_group(db_session, entries["ch3"], label=label)
        for label in ("basin-A", "basin-B")
    ]
    selections = [
        attach_conformer_selection(db_session, conformer_group=group)
        for group in groups
    ]

    for group, selection in zip(groups, selections, strict=True):
        resp = client.post(
            _KINETICS,
            json=_payload(statmechs, conformer_group_ref=group.public_ref),
        )

        assert resp.status_code == 201, resp.text[:800]
        # The row that was stored is the one the ref named, not merely
        # some row: a lookup that resolved to the other group would still
        # have answered 201, which is the pass this assertion refuses.
        stored = db_session.scalar(
            select(KineticsInterpretationAssignment.conformer_selection_id).where(
                KineticsInterpretationAssignment.kinetics_id == resp.json()["id"],
                KineticsInterpretationAssignment.subject_key == "reactant:1",
            )
        )
        assert stored == selection.id, (stored, selection.id, group.public_ref)


def test_a_group_ref_naming_a_group_of_another_species_entry_is_refused(
    client, db_session
) -> None:
    """The locator's two halves contradict each other.

    ``[OH]``'s group is a real group with a real ``lowest_energy``
    selection, so this is not "the ref names nothing" -- it is "the ref
    names something that is not this species entry's". 422 and the
    ownership code, because the world is consistent and the request is
    not; a 404 here would tell the depositor to deposit a row that already
    exists.
    """
    entries, statmechs = _seed_participants(db_session)
    attach_conformer_selection(
        db_session, conformer_group=make_conformer_group(db_session, entries["ch3"])
    )
    foreign = make_conformer_group(db_session, entries["oh"], label="basin-A")
    attach_conformer_selection(db_session, conformer_group=foreign)

    resp = client.post(
        _KINETICS, json=_payload(statmechs, conformer_group_ref=foreign.public_ref)
    )

    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _GROUP_OWNER_MISMATCH, body
    assert body["context"]["field"] == _GROUP_FIELD, body
    assert body["context"]["owner_kind"] == "species_entry", body
    # DR-0028 Requirement 2: the entry that does own it is not disclosed.
    rendered = str(body)
    for row_id in (entries["oh"].id, foreign.id):
        assert str(row_id) not in rendered, (row_id, rendered)


def test_a_group_ref_naming_nothing_is_a_404_that_echoes_it(
    client, db_session
) -> None:
    """A ref for a group that is not in this database at all.

    Its own code rather than ``unknown_conformer_selection``: the repair
    is to correct the ref the caller wrote, not to deposit a selection.
    The ref is echoed because the caller wrote it -- that is
    ``unknown_reference``'s disclosure rule for a public ref.
    """
    _entries, statmechs = _seed_participants(db_session)

    resp = client.post(
        _KINETICS,
        json=_payload(statmechs, conformer_group_ref="cg_notarealconformergroupref"),
    )

    assert resp.status_code == 404, resp.text[:800]
    body = resp.json()
    assert body["code"] == _UNKNOWN_GROUP, body
    assert body["context"]["field"] == _GROUP_FIELD, body
    assert body["context"]["kind"] == "conformer_group", body
    assert body["context"]["ref"] == "cg_notarealconformergroupref", body


def test_a_named_group_holding_no_such_selection_is_the_selection_404(
    client, db_session
) -> None:
    """The group is right and the selection is missing.

    Distinguished from the two refusals above on purpose: this one *is*
    repaired by depositing a selection, so it keeps
    ``unknown_conformer_selection`` -- and the 404 now echoes the group
    ref, so a depositor can see which group was searched.
    """
    entries, statmechs = _seed_participants(db_session)
    group = make_conformer_group(db_session, entries["ch3"], label="basin-A")

    resp = client.post(
        _KINETICS, json=_payload(statmechs, conformer_group_ref=group.public_ref)
    )

    assert resp.status_code == 404, resp.text[:800]
    body = resp.json()
    assert body["code"] == _UNKNOWN, body
    assert body["context"]["conformer_group_ref"] == group.public_ref, body
    assert body["context"]["selection_kind"] == "lowest_energy", body


def test_the_new_field_is_optional_and_its_absence_still_refuses(
    client, db_session
) -> None:
    """One request, both spellings, so the widening cannot be a tightening.

    The same database state answers 422 without the ref and 201 with it.
    Asserting only the 201 would pass against a build that made the field
    required; asserting only the 422 would pass against one that ignored
    the field entirely.
    """
    entries, statmechs = _seed_participants(db_session)
    groups = [
        make_conformer_group(db_session, entries["ch3"], label=label)
        for label in ("basin-A", "basin-B")
    ]
    for group in groups:
        attach_conformer_selection(db_session, conformer_group=group)

    without = client.post(_KINETICS, json=_payload(statmechs))
    with_ref = client.post(
        _KINETICS, json=_payload(statmechs, conformer_group_ref=groups[0].public_ref)
    )

    assert (without.status_code, without.json()["code"]) == (
        422,
        _AMBIGUOUS,
    ), without.text[:400]
    assert with_ref.status_code == 201, with_ref.text[:800]
    # And the refusal now names a field the payload can actually set,
    # which is the whole point of the widening.
    context = without.json()["context"]
    assert context["discriminator"] == "conformer_group_ref", context
    assert context["locator_can_express"] is True, context
    assert sorted(context["discriminator_values"]) == sorted(
        group.public_ref for group in groups
    ), context
