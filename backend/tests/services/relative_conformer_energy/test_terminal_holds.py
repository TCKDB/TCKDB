"""A rejected or deprecated record is terminal, which is not a floor: with no floor set, say so, not "below the floor"."""

from __future__ import annotations

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.services.relative_conformer_energy._support import basin, compute, member, request
from tests.services.relative_conformer_energy.test_service import conformer, req, run
from tests.services.scientific_read._factories import set_review
from tests.services.structure_selection._support import make_world

R = RecordReviewStatus


def test_pure_a_terminal_hold_is_not_called_a_floor():
    r = compute(request(), ["cg_a", "cg_b"], basin("cg_a"), terminal_excluded=frozenset({"cg_b"}))
    assert member(r, "cg_b").absent_reason == "determination_terminal_review_status"


def test_a_rejected_observation_with_no_floor_set_is_terminal_not_below_a_floor(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400)
    dead, dead_obs = conformer(world, -76.410)
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=dead.id, status=R.approved)
    set_review(db_session, record_type=SubmissionRecordType.conformer_observation, record_id=dead_obs.id, status=R.rejected)
    run_ = run(db_session, world, req(world, ref))
    b = next(m for m in run_.result.members if m.conformer_ref == dead.public_ref)
    assert b.absent_reason == "determination_terminal_review_status"
    assert run_.population.floor_excluded_refs == frozenset()


def test_a_deprecated_observation_is_terminal_too(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400)
    old, old_obs = conformer(world, -76.410)
    set_review(db_session, record_type=SubmissionRecordType.conformer_observation, record_id=old_obs.id, status=R.deprecated)
    run_ = run(db_session, world, req(world, ref))
    b = next(m for m in run_.result.members if m.conformer_ref == old.public_ref)
    assert b.absent_reason == "determination_terminal_review_status"


def test_a_rejected_species_entry_makes_every_member_terminal_not_below_a_floor(db_session):
    world = make_world(db_session, entry_status=R.rejected)
    ref, _ = conformer(world, -76.400)
    conformer(world, -76.410)
    run_ = run(db_session, world, req(world, ref))
    assert {m.absent_reason for m in run_.result.members} == {"determination_terminal_review_status"}
    assert run_.population.determination_refs == ()
