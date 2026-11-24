"""The machine-review worker over the job service, driven by the scripted fake agent.

The worker takes an injected session factory. Here it is bound to the test's own
connection in savepoint mode, so rows seeded through ``db_session`` are visible
to it and nothing is committed past the test.
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models.common import (
    AppUserRole,
    MachineReviewExecutor,
    MachineReviewJobFailure,
    MachineReviewProviderKey,
    SubmissionAuditEventKind,
    SubmissionRecordType,
)
from app.db.models.common import (
    MachineReviewJobEventKind as K,
)
from app.db.models.common import (
    MachineReviewJobStatus as S,
)
from app.db.models.machine_review_job import MachineReviewJob, MachineReviewJobEvent
from app.db.models.submission import SubmissionAuditEvent
from app.services.machine_review import jobs
from app.services.machine_review.providers.fake import (
    ScriptedFakeMachineReviewAgent,
    make_pass_result,
    make_warning_result,
)
from app.services.machine_review.switches import write_machine_review_switches
from app.services.submission import link_record
from app.workers import machine_review_worker as worker
from tests.services._machine_review_job_support import (
    FAKE_SETTINGS,
    insert_job,
    make_submission,
    make_user,
)


@pytest.fixture
def factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
def ctx(db_session):
    admin = make_user(db_session, "worker-admin", AppUserRole.admin)
    submission = make_submission(db_session, admin.id)
    job = jobs.create_job(
        db_session,
        submission_id=submission.id,
        requester=admin,
        provider_key=MachineReviewProviderKey.fake,
        settings_obj=FAKE_SETTINGS,
    )
    return db_session, admin, submission, job


def _cycle(factory, **kw) -> bool:
    return worker.process_one_cycle(factory, FAKE_SETTINGS, heartbeat=False, **kw)


def _kinds(session, job):
    return list(
        session.scalars(
            select(MachineReviewJobEvent.kind).where(MachineReviewJobEvent.job_id == job.id).order_by(MachineReviewJobEvent.seq)
        )
    )


def _audit_events(session, submission):
    return list(
        session.scalars(
            select(SubmissionAuditEvent).where(
                SubmissionAuditEvent.submission_id == submission.id,
                SubmissionAuditEvent.event_kind == SubmissionAuditEventKind.llm_precheck_recorded,
            )
        )
    )


def _reload(session, job):
    session.expire_all()
    return session.get(MachineReviewJob, job.id)


def test_a_queued_job_runs_to_succeeded_with_the_scripted_events(ctx, factory):
    session, _, submission, job = ctx
    assert _cycle(factory) is True

    job = _reload(session, job)
    assert job.status is S.succeeded
    assert _kinds(session, job) == [K.claimed, K.context_built, K.model_turn, K.model_turn, K.model_turn, K.final_output, K.finished]
    seqs = list(session.scalars(select(MachineReviewJobEvent.seq).where(MachineReviewJobEvent.job_id == job.id).order_by(MachineReviewJobEvent.seq)))
    assert seqs == list(range(1, 8))
    assert job.step_count == 3
    assert job.context_hash and len(job.context_hash) == 64
    audit = session.get(SubmissionAuditEvent, job.audit_event_id)
    assert audit.event_kind is SubmissionAuditEventKind.llm_precheck_recorded
    assert audit.details_json["model"] == "fake_test/machine-review"
    assert len(_audit_events(session, submission)) == 1
    assert _cycle(factory) is False


def test_an_empty_queue_does_nothing(db_session, factory):
    assert _cycle(factory) is False


def test_cancel_between_steps_cancels_without_an_audit_event(ctx, factory):
    session, admin, submission, job = ctx

    class CancelAfterFirstStep(ScriptedFakeMachineReviewAgent):
        def run(self, context, *, on_step, cancelled):
            def step(i):
                on_step(i)
                if i == 1:
                    with factory() as other:
                        jobs.request_cancel(other, job.id, actor=admin)
                        other.commit()

            return super().run(context, on_step=step, cancelled=cancelled)

    _cycle(factory, provider=CancelAfterFirstStep(steps=3))

    job = _reload(session, job)
    assert job.status is S.cancelled and job.audit_event_id is None and job.failure_code is None
    assert _audit_events(session, submission) == []
    assert _kinds(session, job).count(K.model_turn) == 1
    assert _kinds(session, job)[-1] is K.finished


def test_switch_off_at_claim_cancels_the_job(ctx, factory):
    session, admin, submission, job = ctx
    write_machine_review_switches(session, actor=admin, machine_review_enabled=False)

    assert _cycle(factory) is True

    job = _reload(session, job)
    assert (job.status, job.failure_code, job.audit_event_id) == (S.cancelled, MachineReviewJobFailure.machine_review_disabled, None)
    assert _kinds(session, job) == [K.claimed, K.finished]
    assert _audit_events(session, submission) == []


def test_switch_on_twin_succeeds(ctx, factory):
    session, admin, _, job = ctx
    write_machine_review_switches(session, actor=admin, machine_review_enabled=True)
    _cycle(factory)
    assert _reload(session, job).status is S.succeeded


def _queued_job_by(session, role, name):
    user = make_user(session, name, role)
    submission = make_submission(session, user.id)
    job = jobs.create_job(
        session,
        submission_id=submission.id,
        requester=user,
        provider_key=MachineReviewProviderKey.fake,
        settings_obj=FAKE_SETTINGS,
    )
    return user, submission, job


def test_curator_switch_off_at_claim_cancels_a_curator_job(db_session, factory):
    admin = make_user(db_session, "switch-admin", AppUserRole.admin)
    _, submission, job = _queued_job_by(db_session, AppUserRole.curator, "claim-curator")
    write_machine_review_switches(db_session, actor=admin, curator_requests_enabled=False)

    assert _cycle(factory) is True

    job = _reload(db_session, job)
    assert (job.status, job.failure_code, job.audit_event_id) == (S.cancelled, MachineReviewJobFailure.machine_review_disabled, None)
    assert _kinds(db_session, job) == [K.claimed, K.finished]
    assert _audit_events(db_session, submission) == []


def test_curator_switch_off_still_runs_an_admin_job(db_session, factory):
    admin = make_user(db_session, "switch-admin", AppUserRole.admin)
    _, _, job = _queued_job_by(db_session, AppUserRole.admin, "claim-admin")
    write_machine_review_switches(db_session, actor=admin, curator_requests_enabled=False)

    assert _cycle(factory) is True

    assert _reload(db_session, job).status is S.succeeded


def test_curator_switch_on_runs_a_curator_job(db_session, factory):
    admin = make_user(db_session, "switch-admin", AppUserRole.admin)
    _, _, job = _queued_job_by(db_session, AppUserRole.curator, "claim-curator")
    write_machine_review_switches(db_session, actor=admin, curator_requests_enabled=True)

    assert _cycle(factory) is True

    assert _reload(db_session, job).status is S.succeeded


def test_a_job_whose_requester_was_demoted_is_cancelled_at_claim(db_session, factory):
    user, submission, job = _queued_job_by(db_session, AppUserRole.curator, "demoted-curator")
    user.role = AppUserRole.user
    db_session.flush()

    assert _cycle(factory) is True

    job = _reload(db_session, job)
    assert (job.status, job.failure_code, job.audit_event_id) == (S.cancelled, MachineReviewJobFailure.machine_review_disabled, None)
    assert _audit_events(db_session, submission) == []


def test_a_job_whose_requester_is_inactive_is_cancelled_at_claim(db_session, factory):
    user, submission, job = _queued_job_by(db_session, AppUserRole.curator, "inactive-curator")
    user.is_active = False
    db_session.flush()

    assert _cycle(factory) is True

    job = _reload(db_session, job)
    assert (job.status, job.failure_code, job.audit_event_id) == (S.cancelled, MachineReviewJobFailure.machine_review_disabled, None)
    assert _audit_events(db_session, submission) == []


def test_an_active_curator_job_with_the_switch_on_runs_twin(db_session, factory):
    _, _, job = _queued_job_by(db_session, AppUserRole.curator, "active-curator")

    assert _cycle(factory) is True

    assert _reload(db_session, job).status is S.succeeded


def test_a_raising_provider_fails_with_internal_error_and_a_failed_audit_event(ctx, factory):
    session, _, submission, job = ctx

    class Raising:
        def review_submission(self, context):
            raise RuntimeError("upstream unreachable")

    _cycle(factory, provider=Raising())

    job = _reload(session, job)
    assert job.status is S.failed and job.failure_code is MachineReviewJobFailure.internal_error
    assert "upstream unreachable" in job.failure_detail
    audit = session.get(SubmissionAuditEvent, job.audit_event_id)
    assert audit.details_json["status"] == "machine_review_failed"
    assert len(_audit_events(session, submission)) == 1


def test_unparseable_output_fails_with_invalid_output(ctx, factory):
    session, _, _, job = ctx

    class Garbled(ScriptedFakeMachineReviewAgent):
        def run(self, context, *, on_step, cancelled):
            return "not json"

    _cycle(factory, provider=Garbled())

    job = _reload(session, job)
    assert (job.status, job.failure_code) == (S.failed, MachineReviewJobFailure.invalid_output)
    assert K.parse_failed in _kinds(session, job)


def test_a_provider_key_that_does_not_match_the_mode_is_a_config_error(db_session, factory):
    admin = make_user(db_session, "worker-admin2", AppUserRole.admin)
    submission = make_submission(db_session, admin.id)
    job = insert_job(db_session, submission.id, admin.id, provider_key=MachineReviewProviderKey.cloud)

    _cycle(factory)

    job = _reload(db_session, job)
    assert (job.status, job.failure_code) == (S.failed, MachineReviewJobFailure.config_error)


def test_a_remote_runner_job_is_not_claimed(db_session, factory):
    admin = make_user(db_session, "worker-admin3", AppUserRole.admin)
    submission = make_submission(db_session, admin.id)
    job = insert_job(
        db_session, submission.id, admin.id, provider_key=MachineReviewProviderKey.codex,
        executor=MachineReviewExecutor.remote_runner,
    )
    assert _cycle(factory) is False
    assert _reload(db_session, job).status is S.queued


def test_a_worker_that_lost_its_lease_writes_no_audit_event(ctx, factory):
    session, _, submission, job = ctx

    class LeaseStolen(ScriptedFakeMachineReviewAgent):
        def run(self, context, *, on_step, cancelled):
            text = super().run(context, on_step=on_step, cancelled=cancelled)
            with factory() as other:
                late = jobs._utcnow() + jobs.LEASE_DURATION + timedelta(seconds=5)
                jobs.reap_expired(other, late)
                jobs.claim_next_job(other, now=late, settings_obj=FAKE_SETTINGS)
                other.commit()
            return text

    _cycle(factory, provider=LeaseStolen(steps=1))

    job = _reload(session, job)
    assert (job.status, job.attempts) == (S.running, 2)
    assert _audit_events(session, submission) == []


def test_lease_expiry_requeues_once_then_fails_worker_lost(ctx, factory):
    session, _, submission, job = ctx
    t0 = job.created_at
    with factory() as s:
        jobs.claim_next_job(s, now=t0, settings_obj=FAKE_SETTINGS)
        s.commit()
    late = t0 + jobs.LEASE_DURATION + timedelta(seconds=1)
    with factory() as s:
        jobs.reap_expired(s, late)
        s.commit()
    job = _reload(session, job)
    assert (job.status, job.attempts) == (S.queued, 1)
    assert K.retry in _kinds(session, job)

    with factory() as s:
        jobs.claim_next_job(s, now=late, settings_obj=FAKE_SETTINGS)
        s.commit()
    with factory() as s:
        jobs.reap_expired(s, late + jobs.LEASE_DURATION + timedelta(seconds=1))
        s.commit()
    job = _reload(session, job)
    assert (job.status, job.failure_code, job.audit_event_id) == (S.failed, MachineReviewJobFailure.worker_lost, None)
    assert _audit_events(session, submission) == []


def test_the_worker_thread_runs_cycles_until_stopped():
    stop = threading.Event()
    calls = []

    def cycle():
        calls.append(1)
        if len(calls) >= 3:
            stop.set()
        return True

    thread = worker.run_worker_thread(stop, cycle=cycle, poll_interval=0.01)
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert len(calls) == 3


def test_cancel_flag_stops_a_single_shot_provider_before_it_is_called(ctx, factory):
    session, _, submission, job = ctx
    job.cancel_requested = True
    session.flush()
    calls = []

    class SingleShot:
        def review_submission(self, context):
            calls.append(context)
            raise AssertionError("must not be called")

    _cycle(factory, provider=SingleShot())

    job = _reload(session, job)
    assert calls == []
    assert job.status is S.cancelled and job.audit_event_id is None and job.failure_code is None
    assert _audit_events(session, submission) == []
    assert _kinds(session, job)[-1] is K.finished


def test_cancel_arriving_while_a_single_shot_provider_runs_records_no_review(ctx, factory):
    session, admin, submission, job = ctx

    class FlipsCancelWhileRunning:
        def review_submission(self, context):
            with factory() as other:
                jobs.request_cancel(other, job.id, actor=admin)
                other.commit()
            return make_pass_result()

    before = len(_audit_events(session, submission))
    _cycle(factory, provider=FlipsCancelWhileRunning())

    job = _reload(session, job)
    assert job.status is S.cancelled and job.audit_event_id is None and job.failure_code is None
    assert len(_audit_events(session, submission)) == before


def test_a_refused_fake_job_records_the_job_model_not_the_configured_one(db_session, factory):
    admin = make_user(db_session, "worker-admin4", AppUserRole.admin)
    submission = make_submission(db_session, admin.id)
    job = insert_job(db_session, submission.id, admin.id, provider_key=MachineReviewProviderKey.fake)
    settings = FAKE_SETTINGS.model_copy(update={"ai_review_assistant_mode": "cloud", "llm_precheck_model": "cloud/never-asked"})

    worker.process_one_cycle(factory, settings, heartbeat=False)

    job = _reload(db_session, job)
    assert job.failure_code is MachineReviewJobFailure.config_error
    assert job.model == "fake_test/machine-review"
    events = _audit_events(db_session, submission)
    assert len(events) == 1
    assert events[0].details_json["model"] == "fake_test/machine-review"
    assert "cloud/never-asked" not in str(events[0].details_json)


def _scripted_with(**kw):
    return ScriptedFakeMachineReviewAgent(steps=1, result=make_warning_result(**kw))


def _failed_audit_events(session, submission):
    return [e for e in _audit_events(session, submission) if e.details_json["status"] == "machine_review_failed"]


def test_a_finding_citing_an_unlinked_record_fails_the_job_as_invalid_output(ctx, factory):
    session, _, submission, job = ctx

    _cycle(factory, provider=_scripted_with(record_type="calculation", record_ref="777"))

    job = _reload(session, job)
    assert (job.status, job.failure_code) == (S.failed, MachineReviewJobFailure.invalid_output)
    assert _kinds(session, job).count(K.parse_failed) == 1
    assert len(_failed_audit_events(session, submission)) == 1
    assert "not offered" in job.failure_detail


def test_the_same_finding_citing_a_linked_record_succeeds(ctx, factory):
    session, _, submission, job = ctx
    link_record(
        session, submission=submission, record_type=SubmissionRecordType.calculation, record_id=777, role="primary"
    )
    session.flush()

    _cycle(factory, provider=_scripted_with(record_type="calculation", record_ref="777"))

    job = _reload(session, job)
    assert job.status is S.succeeded and job.failure_code is None
    assert K.parse_failed not in _kinds(session, job)
    assert len(_audit_events(session, submission)) == 1
    assert _failed_audit_events(session, submission) == []


class _SingleShotCitingCalculation777:
    def review_submission(self, context):
        return make_warning_result(record_type="calculation", record_ref="777")


def test_single_shot_provider_citing_an_unlinked_record_fails_as_invalid_output(ctx, factory):
    session, _, submission, job = ctx

    _cycle(factory, provider=_SingleShotCitingCalculation777())

    job = _reload(session, job)
    assert (job.status, job.failure_code) == (S.failed, MachineReviewJobFailure.invalid_output)
    assert _kinds(session, job).count(K.parse_failed) == 1
    assert len(_failed_audit_events(session, submission)) == 1


def test_single_shot_provider_citing_a_linked_record_succeeds(ctx, factory):
    session, _, submission, job = ctx
    link_record(
        session, submission=submission, record_type=SubmissionRecordType.calculation, record_id=777, role="primary"
    )
    session.flush()

    _cycle(factory, provider=_SingleShotCitingCalculation777())

    assert _reload(session, job).status is S.succeeded
    assert _failed_audit_events(session, submission) == []


def _scripted_with_sources(sources):
    result = make_pass_result().model_copy(update={"retrieval_sources": sources})
    return ScriptedFakeMachineReviewAgent(steps=1, result=result)


def test_naming_a_retrieval_source_nothing_returned_fails_and_naming_none_succeeds(db_session, factory):
    admin = make_user(db_session, "worker-admin5", AppUserRole.admin)
    outcomes = {}
    for label, sources in (("named", ("guidance.t1",)), ("none", ())):
        submission = make_submission(db_session, admin.id)
        job = jobs.create_job(
            db_session,
            submission_id=submission.id,
            requester=admin,
            provider_key=MachineReviewProviderKey.fake,
            settings_obj=FAKE_SETTINGS,
        )
        _cycle(factory, provider=_scripted_with_sources(sources))
        job = _reload(db_session, job)
        outcomes[label] = (job.status, job.failure_code, len(_failed_audit_events(db_session, submission)))

    assert outcomes["named"] == (S.failed, MachineReviewJobFailure.invalid_output, 1)
    assert outcomes["none"] == (S.succeeded, None, 0)


def test_identity_on_a_stored_review_comes_from_the_job_and_not_from_the_model(ctx, factory):
    session, _, _, job = ctx
    claimed = make_pass_result().model_copy(update={"model": "claimed/model", "provider": "ClaimedProvider"})

    _cycle(factory, provider=ScriptedFakeMachineReviewAgent(steps=1, result=claimed))

    job = _reload(session, job)
    details = session.get(SubmissionAuditEvent, job.audit_event_id).details_json
    assert details["model"] == job.model == "fake_test/machine-review"
    assert details["provider"] == "ScriptedFakeMachineReviewAgent"
