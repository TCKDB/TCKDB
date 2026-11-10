"""The caller's own floor and the read profile, against real rows."""

from __future__ import annotations

import json

import pytest

from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus, StructureDeterminationTargetKind, SubmissionRecordType
from tests.services.relative_conformer_energy.test_service import conformer, req, run
from tests.services.scientific_read._factories import make_conformer_group, make_conformer_observation, set_review
from tests.services.structure_selection._support import curated, make_world, reset_current_read_profile
from tests.services.structure_selection.test_service import build_basin

R = RecordReviewStatus


def _review(world, record_type, row, status):
    set_review(world.session, record_type=record_type, record_id=row.id, status=status)


def test_a_determination_held_out_by_the_callers_floor_is_not_reported_as_missing(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400, approve=True)
    held, held_obs = conformer(world, -76.410)
    _review(world, SubmissionRecordType.conformer_group, held, R.approved)
    _review(world, SubmissionRecordType.conformer_observation, held_obs, R.under_review)
    run_ = run(db_session, world, req(world, ref, min_review_status=R.approved))
    b = next(m for m in run_.result.members if m.conformer_ref == held.public_ref)
    assert b.status.value == "absent" and b.absent_reason == "determination_below_review_floor"
    assert b.value_hartree is None
    assert len(run_.population.determination_refs) == 1  # only the reference's own is assessed


def test_a_species_entry_below_the_floor_does_not_hold_its_members_out(db_session):
    world = make_world(db_session, entry_status=R.under_review)
    ref, _ = conformer(world, -76.400, approve=True)
    conformer(world, -76.410, approve=True)
    run_ = run(db_session, world, req(world, ref, min_review_status=R.approved))
    assert "determination_below_review_floor" not in {m.absent_reason for m in run_.result.members}
    assert len(run_.population.determination_refs) == 2


def test_a_terminal_group_is_reported_terminal_not_below_a_floor(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400)
    dead, _ = conformer(world, -76.410)
    _review(world, SubmissionRecordType.conformer_group, dead, R.rejected)
    run_ = run(db_session, world, req(world, ref))
    b = next(m for m in run_.result.members if m.conformer_ref == dead.public_ref)
    assert b.absent_reason == "group_terminal_review_status"


def test_an_unreviewed_observation_inside_a_visible_group_is_in_the_world_under_curated(db_session):
    """ADR 0023, Q2: an observation inside a group is evidence, used and badged unless it is rejected or deprecated.

    Kills: the earlier rule, which held the observation out of the compared world because it was below ``approved``.
    """
    world = make_world(db_session)
    shown, _ = conformer(world, -76.400, approve=True)
    unreviewed_obs = make_conformer_observation(db_session, conformer_group=shown)  # no review row: not_reviewed
    build_basin(world, energy=-76.500, observation=unreviewed_obs, target=StructureDeterminationTargetKind.conformer_basin)
    token = curated()
    try:
        run_ = run(db_session, world, req(world, shown))
        floored = run(db_session, world, req(world, shown, min_review_status=R.approved))
    finally:
        reset_current_read_profile(token)
    m = run_.result.members[0]
    assert len(m.determination_refs) == 2  # both observations of the approved group are used
    assert {-76.400, -76.500} <= {m.value_hartree, *(a.value_hartree for a in m.alternates)}
    assert unreviewed_obs.public_ref in json.dumps(run_.manifest, default=str)
    # The caller's own floor still holds the unreviewed observation out.
    assert len(floored.result.members[0].determination_refs) == 1
    assert run_.population.floor_excluded_refs == frozenset()


def test_a_withheld_observation_inside_a_visible_group_is_not_in_the_world(db_session):
    world = make_world(db_session)
    shown, _ = conformer(world, -76.400, approve=True)
    # A second observation of the same, visible group has a different value and is rejected: curated withholds it.
    hidden_obs = make_conformer_observation(db_session, conformer_group=shown)
    _review(world, SubmissionRecordType.conformer_observation, hidden_obs, R.rejected)
    build_basin(world, energy=-76.500, observation=hidden_obs, target=StructureDeterminationTargetKind.conformer_basin)
    token = curated()
    try:
        run_ = run(db_session, world, req(world, shown))
    finally:
        reset_current_read_profile(token)
    m = run_.result.members[0]
    assert m.value_hartree == -76.400 and m.alternates == () and len(m.determination_refs) == 1
    assert hidden_obs.public_ref not in json.dumps(run_.manifest, default=str)


def test_a_withheld_determination_is_not_disclosed_even_as_a_floor_reason(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400, approve=True)
    group = make_conformer_group(db_session, world.entry)
    hidden_obs = make_conformer_observation(db_session, conformer_group=group)
    _review(world, SubmissionRecordType.conformer_observation, hidden_obs, R.rejected)
    build_basin(world, energy=-76.500, observation=hidden_obs, target=StructureDeterminationTargetKind.conformer_basin)
    _review(world, SubmissionRecordType.conformer_group, group, R.approved)
    token = curated()
    try:
        run_ = run(db_session, world, req(world, ref))
    finally:
        reset_current_read_profile(token)
    b = next(m for m in run_.result.members if m.conformer_ref == group.public_ref)
    # The curated profile withholds the observation, so there is nothing to say about it: not even that a floor held it out.
    assert b.absent_reason == "no_structure_determination"
    assert run_.population.floor_excluded_refs == frozenset()


def _groups(world, shown_count, hidden_count):
    shown = [make_conformer_group(world.session, world.entry) for _ in range(shown_count)]
    for _ in range(hidden_count):
        make_conformer_group(world.session, world.entry)
    for g in shown:
        _review(world, SubmissionRecordType.conformer_group, g, R.approved)
    world.session.flush()
    return shown


def test_hidden_groups_never_count_toward_the_bound(db_session):
    world = make_world(db_session)
    shown = _groups(world, 3, 498)  # 501 groups exist; the curated profile shows 3
    token = curated()
    try:
        run_ = run(db_session, world, req(world, shown[0]))
    finally:
        reset_current_read_profile(token)
    assert len(run_.result.members) == 3


def test_hidden_groups_are_not_in_the_visible_count_a_refusal_reports(db_session):
    world = make_world(db_session)
    shown = _groups(world, 501, 5)
    token = curated()
    try:
        with pytest.raises(CodedValueError) as raised:
            run(db_session, world, req(world, shown[0]))
    finally:
        reset_current_read_profile(token)
    assert raised.value.context["visible"] == 501
