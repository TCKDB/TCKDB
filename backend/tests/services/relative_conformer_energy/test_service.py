"""Relative conformer energies against real rows: the population filter, bounds, refusals and the snapshot."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.common import RecordReviewStatus, StructureDeterminationTargetKind, SubmissionRecordType
from app.services.read_snapshot import SnapshotNotConsistentError
from app.services.relative_conformer_energy import (
    Certificate,
    EnergyKind,
    LevelSelector,
    RelativeEnergyOutcome,
    RelativeEnergyRequest,
    run_relative_energies,
)
from tests.services.scientific_read._factories import (
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    set_review,
)
from tests.services.structure_selection._support import curated, make_world, reset_current_read_profile
from tests.services.structure_selection.test_service import build_basin

Out = RelativeEnergyOutcome


def level_of(world) -> LevelSelector:
    return LevelSelector(level_of_theory_ref=world.lot.public_ref)


def req(world, reference, **changes) -> RelativeEnergyRequest:
    base = {
        "reference_conformer_ref": reference.public_ref,
        "energy_kind": EnergyKind.electronic_energy,
        "level": level_of(world),
        "certificate": Certificate.local_minimum_any_surface,
    }
    base.update(changes)
    return RelativeEnergyRequest(**base)


def run(session, world, request, *, require_snapshot=False):
    world.settle()
    return run_relative_energies(session, entry_id=world.entry.id, request=request, require_snapshot=require_snapshot)


def conformer(world, energy, *, approve=False):
    """A conformer group with one observation and (when ``energy`` is given) one basin determination."""
    group = make_conformer_group(world.session, world.entry)
    obs = make_conformer_observation(world.session, conformer_group=group)
    if energy is not None:
        build_basin(world, energy=energy, observation=obs, target=StructureDeterminationTargetKind.conformer_basin)
    if approve:
        for record_type, row in ((SubmissionRecordType.conformer_group, group), (SubmissionRecordType.conformer_observation, obs)):
            set_review(world.session, record_type=record_type, record_id=row.id, status=RecordReviewStatus.approved)
    world.session.flush()
    return group, obs


def test_three_conformers_one_value_each_way_and_one_absent(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.400)
    low, _ = conformer(world, -76.401)
    bare, _ = conformer(world, None)
    run_ = run(db_session, world, req(world, ref))
    by = {m.conformer_ref: m for m in run_.result.members}
    assert run_.result.outcome is Out.relative_energies
    assert by[low.public_ref].relative_energy_hartree == pytest.approx(-0.001) and by[low.public_ref].relative_energy_kj_mol < 0
    assert by[bare.public_ref].absent_reason == "no_structure_determination" and by[bare.public_ref].value_hartree is None
    assert by[ref.public_ref].status.value == "reference"
    assert [m.conformer_ref for m in run_.result.members] == sorted(by)


def test_an_unknown_reference_is_a_not_found(db_session):
    world = make_world(db_session)
    conformer(world, -76.4)
    other = make_world(db_session)
    foreign, _ = conformer(other, -76.4)
    world.settle()
    with pytest.raises(NotFoundError):
        run_relative_energies(
            db_session, entry_id=world.entry.id, request=req(world, foreign), require_snapshot=False
        )


# -- disclosure ------------------------------------------------------------------------------------------------


def test_a_hidden_conformer_group_is_not_in_the_world_under_the_curated_profile(db_session):
    world = make_world(db_session)
    shown, shown_obs = conformer(world, -76.400, approve=True)
    # The hidden group's observation is approved (so the structure scan alone would admit its determination), but the
    # group itself has no review and the curated profile hides it.
    hidden, hidden_obs = conformer(world, -76.410)
    set_review(
        db_session, record_type=SubmissionRecordType.conformer_observation, record_id=hidden_obs.id, status=RecordReviewStatus.approved
    )
    token = curated()
    try:
        run_ = run(db_session, world, req(world, shown))
    finally:
        reset_current_read_profile(token)
    assert [m.conformer_ref for m in run_.result.members] == [shown.public_ref]
    assert run_.result.coverage["requested_members"] == 1
    everything = json.dumps({"manifest": run_.manifest, "result": run_.result.to_dict()}, default=str)
    assert hidden.public_ref not in everything and hidden_obs.public_ref not in everything
    hidden_dets = [d.determination_ref for d in run_.assessment.determinations if d.conformer_group_ref != shown.public_ref]
    assert hidden_dets == []
    # And a hidden group cannot be named as the reference or a member: the same answer as an unknown one.
    token = curated()
    try:
        with pytest.raises(NotFoundError):
            run(db_session, world, req(world, hidden))
    finally:
        reset_current_read_profile(token)


# -- refusals and bounds ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [EnergyKind.enthalpy, EnergyKind.gibbs_energy])
def test_enthalpy_and_gibbs_are_refused_before_any_query(db_session, kind):
    world = make_world(db_session)
    group, _ = conformer(world, -76.4)
    world.settle()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    request = req(world, group, energy_kind=kind, temperature_k=298.15)
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        with pytest.raises(CodedValueError) as raised:
            run_relative_energies(db_session, entry_id=world.entry.id, request=request, require_snapshot=False)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert raised.value.code == "relative_conformer_energy_unsupported"
    assert raised.value.context["reason"] == "deferred_energy_kind"
    assert statements == []


def test_a_request_for_no_certificate_is_refused_with_a_code(db_session):
    world = make_world(db_session)
    group, _ = conformer(world, -76.4)
    world.settle()
    with pytest.raises(CodedValueError) as raised:
        run_relative_energies(
            db_session, entry_id=world.entry.id, request=req(world, group, certificate=Certificate.none), require_snapshot=False
        )
    assert raised.value.code == "relative_conformer_energy_unsupported" and raised.value.context["reason"] == "deferred_certificate"


def test_the_population_bound_is_the_largest_answered_and_one_more_is_refused(db_session):
    world = make_world(db_session)
    groups = [make_conformer_group(db_session, world.entry) for _ in range(501)]
    world.settle()
    with pytest.raises(CodedValueError) as raised:
        run_relative_energies(
            db_session, entry_id=world.entry.id, request=req(world, groups[0]), require_snapshot=False
        )
    assert raised.value.code == "relative_conformer_energy_population_too_large"
    assert raised.value.context["visible"] == 501 and raised.value.context["limit"] == 500
    # Naming 500 of them is at the bound and is answered (every one absent: none has a determination).
    exact = run_relative_energies(
        db_session,
        entry_id=world.entry.id,
        request=req(world, groups[0], member_refs=tuple(g.public_ref for g in groups[:500])),
        require_snapshot=False,
    )
    assert len(exact.result.members) == 500 and exact.result.outcome is Out.reference_unavailable


def test_the_read_needs_a_snapshot_even_when_there_is_nothing_to_assess(db_session):
    # No determination at all: the structure assessment (which also demands a snapshot) is never called, so only this
    # operation's own snapshot call can refuse a read that is not one.
    world = make_world(db_session)
    group, _ = conformer(world, None)
    world.settle()
    with pytest.raises(SnapshotNotConsistentError):
        run_relative_energies(db_session, entry_id=world.entry.id, request=req(world, group), require_snapshot=True)
    answered = run_relative_energies(db_session, entry_id=world.entry.id, request=req(world, group), require_snapshot=False)
    assert answered.result.outcome is Out.reference_unavailable
    assert answered.assessment.snapshot_isolation in {"read committed", "repeatable read", "serializable"}


def test_another_level_of_the_same_world_is_not_a_value(db_session):
    world = make_world(db_session)
    ref, _ = conformer(world, -76.4)
    other = make_lot(db_session, method="ccsd(t)", basis="cc-pvqz")
    run_ = run(
        db_session, world, req(world, ref, level=LevelSelector(level_of_theory_ref=other.public_ref))
    )
    assert run_.result.outcome is Out.reference_unavailable
    assert run_.result.reference.absent_reason == "no_value_at_requested_level"
