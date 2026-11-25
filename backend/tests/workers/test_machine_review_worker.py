"""The machine-review worker over the job service, driven by the scripted fake agent.

The worker takes an injected session factory. Here it is bound to the test's own
connection in savepoint mode, so rows seeded through ``db_session`` are visible
to it and nothing is committed past the test.
"""

from __future__ import annotations

import json
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


# --- local provider through a stub server ---------------------------------------


def _local_settings(base_url: str, **extra):
    from app.api.config import Settings

    return Settings(
        ai_review_assistant_mode="local",
        llm_precheck_model="gguf/stub-model",
        llm_precheck_base_url=base_url,
        llm_precheck_timeout_seconds=extra.pop("llm_precheck_timeout_seconds", 5),
        **extra,
    )


def _local_job(db_session, settings_obj, username="local-admin"):
    admin = make_user(db_session, username, AppUserRole.admin)
    submission = make_submission(db_session, admin.id)
    job = jobs.create_job(
        db_session,
        submission_id=submission.id,
        requester=admin,
        provider_key=MachineReviewProviderKey.local,
        settings_obj=settings_obj,
    )
    return submission, job


def _pass_reply(content: str | None = None) -> dict:
    import json

    answer = content or json.dumps(
        {
            "format": {"name": "machine_review", "version": 2},
            "status": "machine_screened_pass",
            "curator_priority": None,
            "summary": "Nothing needs a curator.",
            "findings": [],
            "retrieval_sources": [],
        }
    )
    return {"choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": answer}}]}


@pytest.fixture
def local_stub():
    from tests.services._llm_stub_server import StubServer

    servers = []

    def make(*script):
        server = StubServer(list(script)).start()
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


def _local_cycle(factory, settings_obj):
    return worker.process_one_cycle(factory, settings_obj, heartbeat=False)


def test_a_local_job_against_the_stub_succeeds_with_one_audit_event(db_session, factory, local_stub):
    from tests.services._llm_stub_server import StubResponse

    server = local_stub(StubResponse(body=_pass_reply()))
    settings_obj = _local_settings(server.base_url)
    submission, job = _local_job(db_session, settings_obj)

    assert _local_cycle(factory, settings_obj) is True

    job = _reload(db_session, job)
    assert job.status is S.succeeded
    assert len(server.requests) == 2
    audit = db_session.get(SubmissionAuditEvent, job.audit_event_id)
    assert audit.details_json["model"] == "gguf/stub-model"
    assert len(_audit_events(db_session, submission)) == 1
    assert job.budget_json["thinking_budget_tokens"] == 1024
    assert job.budget_json["max_steps"] == 6
    assert job.budget_json["max_tool_calls"] == 10
    assert job.budget_json["run_wall_seconds"] == 900


def test_a_refused_port_fails_as_provider_unavailable_with_a_failed_audit_event(db_session, factory, monkeypatch):
    from app.services.machine_review.providers import factory as provider_factory
    from tests.services._llm_stub_server import refused_base_url

    monkeypatch.setattr(provider_factory, "_LOCAL_CONNECT_TIMEOUT_SECONDS", 2.0)

    settings_obj = _local_settings(refused_base_url())
    submission, job = _local_job(db_session, settings_obj)

    _local_cycle(factory, settings_obj)

    job = _reload(db_session, job)
    assert job.status is S.failed and job.failure_code is MachineReviewJobFailure.provider_unavailable
    assert db_session.get(SubmissionAuditEvent, job.audit_event_id).details_json["status"] == "machine_review_failed"
    assert len(_audit_events(db_session, submission)) == 1


def test_a_slow_server_fails_as_provider_timeout(db_session, factory, local_stub):
    from tests.services._llm_stub_server import StubResponse

    server = local_stub(StubResponse(body=_pass_reply(), delay=2.5))
    settings_obj = _local_settings(server.base_url, llm_precheck_timeout_seconds=1)
    _, job = _local_job(db_session, settings_obj)

    _local_cycle(factory, settings_obj)

    job = _reload(db_session, job)
    assert job.status is S.failed and job.failure_code is MachineReviewJobFailure.provider_timeout
    assert len(server.requests) == 1


def test_garbled_local_content_fails_as_invalid_output_with_a_parse_failed_event(db_session, factory, local_stub):
    from tests.services._llm_stub_server import StubResponse

    server = local_stub(StubResponse(body=_pass_reply("this is not json")))
    settings_obj = _local_settings(server.base_url)
    _, job = _local_job(db_session, settings_obj)

    _local_cycle(factory, settings_obj)

    job = _reload(db_session, job)
    assert job.status is S.failed and job.failure_code is MachineReviewJobFailure.invalid_output
    assert _kinds(db_session, job).count(K.parse_failed) == 1


def _cite_reply(record_ref: str) -> dict:
    import json

    answer = json.dumps(
        {
            "format": {"name": "machine_review", "version": 2},
            "status": "machine_screened_warning",
            "curator_priority": "medium",
            "summary": "One advisory finding.",
            "findings": [
                {
                    "severity": "warning",
                    "category": "schema_gap",
                    "record_type": "calculation",
                    "record_ref": record_ref,
                    "message": "A quantity has no field to hold it.",
                    "evidence_keys": ["schema_gap.unmapped_quantity"],
                    "recommended_action": "Decide whether to model it.",
                }
            ],
            "retrieval_sources": [],
        }
    )
    return _pass_reply(answer)


def _capped_cite_job(db_session, local_stub, **settings_extra):
    from tests.services._llm_stub_server import StubResponse

    server = local_stub(StubResponse(body=_cite_reply("777")))
    settings_obj = _local_settings(server.base_url, **settings_extra)
    submission, job = _local_job(db_session, settings_obj)
    link_record(
        db_session, submission=submission, record_type=SubmissionRecordType.calculation, record_id=777, role="primary"
    )
    db_session.flush()
    return submission, job, settings_obj


def test_a_finding_citing_a_ref_the_cap_dropped_fails_as_invalid_output(db_session, factory, local_stub):
    submission, job, settings_obj = _capped_cite_job(db_session, local_stub, llm_precheck_max_input_tokens=1)

    _local_cycle(factory, settings_obj)

    job = _reload(db_session, job)
    assert job.status is S.failed and job.failure_code is MachineReviewJobFailure.invalid_output
    assert _kinds(db_session, job).count(K.parse_failed) == 1
    assert "not offered" in job.failure_detail


def test_the_same_finding_citing_a_kept_ref_succeeds(db_session, factory, local_stub):
    submission, job, settings_obj = _capped_cite_job(db_session, local_stub, llm_precheck_max_input_tokens=16000)

    _local_cycle(factory, settings_obj)

    job = _reload(db_session, job)
    assert job.status is S.succeeded and job.failure_code is None
    assert K.parse_failed not in _kinds(db_session, job)


# --- the agent loop through a stub server ----------------------------------------


def _tool_reply(record_type: str, record_ref: str, *, usage=(100, 20)) -> dict:
    import json

    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "REASONING-SENTINEL",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_record",
                                "arguments": json.dumps({"record_type": record_type, "record_ref": record_ref}),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }


def _with_usage(reply: dict, usage=(100, 20)) -> dict:
    return {**reply, "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


def _events(session, job):
    session.expire_all()
    return list(
        session.scalars(
            select(MachineReviewJobEvent).where(MachineReviewJobEvent.job_id == job.id).order_by(MachineReviewJobEvent.seq)
        )
    )


def _tool_steps(session, job):
    return [(e.kind, e.status_code) for e in _events(session, job) if e.kind in (K.tool_call, K.tool_result)]


import itertools

_AGENT_USERS = itertools.count()


def _agent_job(db_session, local_stub, script, **settings_extra):
    from tests.services._llm_stub_server import StubResponse

    server = local_stub(*[StubResponse(**step) if isinstance(step, dict) else step for step in script])
    settings_obj = _local_settings(server.base_url, **settings_extra)
    submission, job = _local_job(db_session, settings_obj, username=f"agent-admin-{next(_AGENT_USERS)}")
    link_record(
        db_session, submission=submission, record_type=SubmissionRecordType.calculation, record_id=777, role="primary"
    )
    db_session.flush()
    return server, submission, job, settings_obj


def test_a_lookup_of_an_unoffered_ref_is_refused_and_the_offered_twin_is_not(db_session, factory, local_stub):
    results = {}
    for label, ref in (("unoffered", "999"), ("offered", "777")):
        server, submission, job, settings_obj = _agent_job(
            db_session,
            local_stub,
            [
                {"body": _tool_reply("calculation", ref)},
                {"body": _with_usage(_pass_reply("done thinking"))},
                {"body": _with_usage(_pass_reply())},
            ],
        )
        _local_cycle(factory, settings_obj)
        job = _reload(db_session, job)
        assert job.status is S.succeeded
        results[label] = (_tool_steps(db_session, job), len(server.requests), job)
    assert results["unoffered"][0] == [(K.tool_call, "ok"), (K.tool_result, "refused")]
    assert results["offered"][0] == [(K.tool_call, "ok"), (K.tool_result, "not_available")]
    server_count, job = results["unoffered"][1], results["unoffered"][2]
    assert server_count == 3
    kinds = _kinds(db_session, job)
    assert kinds[:2] == [K.claimed, K.context_built] and kinds[-2:] == [K.final_output, K.finished]
    assert kinds.count(K.model_turn) == 3
    seqs = [e.seq for e in _events(db_session, job)]
    assert seqs == list(range(1, len(seqs) + 1))
    assert job.step_count == 3
    assert (job.tokens_in, job.tokens_out) == (300, 60)


def test_a_linked_record_with_a_summary_is_returned_ok(db_session, factory, local_stub):
    from tests.services._llm_stub_server import StubResponse
    from tests.services.scientific_read._factories import make_species, make_species_entry, make_thermo_scalar

    admin = make_user(db_session, "agent-ok-admin", AppUserRole.admin)
    thermo = make_thermo_scalar(db_session, species_entry=make_species_entry(db_session, make_species(db_session)), h298_kj_mol=-1.0)
    server = local_stub(
        StubResponse(body=_tool_reply("thermo", str(thermo.id))),
        StubResponse(body=_pass_reply("x")),
        StubResponse(body=_pass_reply()),
    )
    settings_obj = _local_settings(server.base_url)
    submission = make_submission(db_session, admin.id)
    job = jobs.create_job(
        db_session, submission_id=submission.id, requester=admin, provider_key=MachineReviewProviderKey.local,
        settings_obj=settings_obj,
    )
    link_record(db_session, submission=submission, record_type=SubmissionRecordType.thermo, record_id=thermo.id, role="primary")
    db_session.flush()
    _local_cycle(factory, settings_obj)
    job = _reload(db_session, job)
    assert job.status is S.succeeded
    assert _tool_steps(db_session, job) == [(K.tool_call, "ok"), (K.tool_result, "ok")]


def test_event_detail_is_stored_only_when_full_context_storage_is_on(db_session, factory, local_stub):
    stored = {}
    for label, flag in (("off", False), ("on", True)):
        server, _, job, settings_obj = _agent_job(
            db_session,
            local_stub,
            [{"body": _tool_reply("calculation", "777")}, {"body": _pass_reply("x")}, {"body": _pass_reply()}],
            llm_precheck_store_full_context=flag,
        )
        _local_cycle(factory, settings_obj)
        job = _reload(db_session, job)
        stored[label] = [(e.kind, e.detail_json) for e in _events(db_session, job)]
    assert all(detail is None for _, detail in stored["off"])
    assert all(detail is not None for kind, detail in stored["on"] if kind is K.model_turn)


def test_an_exhausted_wall_budget_fails_without_a_review_and_the_twin_records_one(db_session, factory, local_stub):
    outcome = {}
    for label, wall in (("tight", 1), ("generous", 900)):
        server, submission, job, settings_obj = _agent_job(
            db_session,
            local_stub,
            [{"body": _tool_reply("calculation", "777"), "delay": 1.3}, {"body": _pass_reply("x")}, {"body": _pass_reply()}],
            llm_precheck_run_wall_seconds=wall,
        )
        _local_cycle(factory, settings_obj)
        job = _reload(db_session, job)
        outcome[label] = (job.status, job.failure_code, job.audit_event_id is None, len(_audit_events(db_session, submission)))
    assert outcome["tight"] == (S.failed, MachineReviewJobFailure.budget_exhausted, True, 0)
    assert outcome["generous"] == (S.succeeded, None, False, 1)


def test_a_server_that_rejects_tools_gets_one_single_shot_request_instead(db_session, factory, local_stub):
    from tests.services._llm_stub_server import StubResponse

    server, submission, job, settings_obj = _agent_job(
        db_session,
        local_stub,
        [
            StubResponse(body={"error": {"message": "tools param requires --jinja flag"}}, status=500),
            StubResponse(body=_pass_reply()),
        ],
    )
    _local_cycle(factory, settings_obj)
    job = _reload(db_session, job)
    assert job.status is S.succeeded
    assert len(server.requests) == 2 and "tools" not in server.requests[1].body
    events = _events(db_session, job)
    assert [e.status_code for e in events if e.kind is K.model_turn] == ["tools_unsupported"]
    assert not any(e.kind is K.tool_call for e in events)
    assert len(_audit_events(db_session, submission)) == 1


def _tool_reply_named(name: str) -> dict:
    reply = _tool_reply("calculation", "777")
    reply["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = name
    return reply


@pytest.mark.parametrize("name", ["MODELTEXT_rm", "MODELTEXT_" + "x" * 100])
def test_an_invented_tool_name_is_never_stored_as_a_tool_name(db_session, factory, local_stub, name):
    server, submission, job, settings_obj = _agent_job(
        db_session,
        local_stub,
        [{"body": _tool_reply_named(name)}, {"body": _pass_reply("x")}, {"body": _pass_reply()}],
    )
    _local_cycle(factory, settings_obj)
    job = _reload(db_session, job)
    assert job.status is S.succeeded
    events = _events(db_session, job)
    steps = [(e.kind, e.tool_name, e.status_code) for e in events if e.kind in (K.tool_call, K.tool_result)]
    assert steps == [(K.tool_call, None, "ok"), (K.tool_result, None, "unknown_tool")]
    assert not any(e.tool_name and "MODELTEXT" in e.tool_name for e in events)


def test_a_known_tool_keeps_its_name_on_the_events(db_session, factory, local_stub):
    server, submission, job, settings_obj = _agent_job(
        db_session, local_stub, [{"body": _tool_reply("calculation", "777")}, {"body": _pass_reply("x")}, {"body": _pass_reply()}]
    )
    _local_cycle(factory, settings_obj)
    names = [e.tool_name for e in _events(db_session, _reload(db_session, job)) if e.kind in (K.tool_call, K.tool_result)]
    assert names == ["get_record", "get_record"]


def test_a_budget_exhausted_job_keeps_the_tokens_it_spent_and_the_event_agrees(db_session, factory, local_stub):
    server, submission, job, settings_obj = _agent_job(
        db_session,
        local_stub,
        [{"body": _tool_reply("calculation", "777")}, {"body": _pass_reply("x"), "delay": 1.5}, {"body": _pass_reply()}],
        llm_precheck_run_wall_seconds=1,
    )
    _local_cycle(factory, settings_obj)
    job = _reload(db_session, job)
    assert (job.status, job.failure_code) == (S.failed, MachineReviewJobFailure.budget_exhausted)
    assert (job.tokens_in, job.tokens_out) == (100, 20)
    turns = [e.status_code for e in _events(db_session, job) if e.kind is K.model_turn]
    assert turns == ["ok", "budget_exhausted"]


def test_cancel_during_the_tool_phase_cancels_with_no_further_requests_and_no_audit_event(
    db_session, factory, local_stub, monkeypatch
):
    from app.db.models.app_user import AppUser
    from app.services.machine_review.agent.tools import ToolRegistry

    server, submission, job, settings_obj = _agent_job(
        db_session, local_stub, [{"body": _tool_reply("calculation", "777")}, {"body": _pass_reply()}]
    )
    actor = db_session.get(AppUser, submission.created_by)
    real = ToolRegistry.execute

    def execute(self, name, arguments_text):
        with factory() as other:
            jobs.request_cancel(other, job.id, actor=actor)
            other.commit()
        return real(self, name, arguments_text)

    monkeypatch.setattr(ToolRegistry, "execute", execute)
    _local_cycle(factory, settings_obj)
    job = _reload(db_session, job)
    assert job.status is S.cancelled and job.audit_event_id is None
    assert len(server.requests) == 1
    assert _audit_events(db_session, submission) == []


# --- guidance search and the retrieval_sources check ---------------------------------------------

WARNING_CODE = "n_imag_contradicts_minimum"
WARNING_KEY = f"warning:{WARNING_CODE}"


def _search_reply(query: str) -> dict:
    reply = _tool_reply("calculation", "777")
    function = reply["choices"][0]["message"]["tool_calls"][0]["function"]
    function["name"], function["arguments"] = "search_guidance", json.dumps({"query": query})
    return reply


def _sources_reply(*keys: str) -> dict:
    return _pass_reply(
        json.dumps(
            {
                "format": {"name": "machine_review", "version": 2},
                "status": "machine_screened_pass",
                "curator_priority": None,
                "summary": "Nothing needs a curator.",
                "findings": [],
                "retrieval_sources": list(keys),
            }
        )
    )


def _warned_job(db_session, local_stub, script, *, with_warning=True):
    from app.db.models.deposit_warning import DepositWarning
    from app.services.deposit_warnings import content_hash
    from tests.services._llm_stub_server import StubResponse
    from tests.services.scientific_read._factories import make_species, make_species_entry, make_thermo_scalar

    server = local_stub(*[StubResponse(body=body) for body in script])
    settings_obj = _local_settings(server.base_url)
    submission, job = _local_job(db_session, settings_obj, username=f"guide-admin-{next(_AGENT_USERS)}")
    thermo = make_thermo_scalar(
        db_session, species_entry=make_species_entry(db_session, make_species(db_session)), h298_kj_mol=-1.0
    )
    if with_warning:
        db_session.add(
            DepositWarning(
                submission_id=submission.id,
                record_type=SubmissionRecordType.thermo,
                record_id=thermo.id,
                written_by_user_id=submission.created_by,
                code=WARNING_CODE,
                field="f",
                message="DEPOSITOR-TEXT",
                content_hash=content_hash(WARNING_CODE, "f", "DEPOSITOR-TEXT", None),
                check_version=1,
            )
        )
    link_record(
        db_session, submission=submission, record_type=SubmissionRecordType.thermo, record_id=thermo.id, role="primary"
    )
    db_session.flush()
    return server, submission, job, settings_obj


def _outcome(db_session, factory, job, submission, settings_obj):
    _local_cycle(factory, settings_obj)
    job = _reload(db_session, job)
    return job.status, job.failure_code, len(_failed_audit_events(db_session, submission)), job


def test_citing_a_key_this_job_searched_succeeds_and_is_stored(db_session, factory, local_stub):
    server, submission, job, settings_obj = _warned_job(
        db_session, local_stub, [_search_reply(WARNING_CODE), _pass_reply("x"), _sources_reply(WARNING_KEY)]
    )
    status, code, failed, job = _outcome(db_session, factory, job, submission, settings_obj)
    assert (status, code, failed) == (S.succeeded, None, 0)
    audit = db_session.get(SubmissionAuditEvent, job.audit_event_id)
    assert audit.details_json["retrieval_sources"] == [WARNING_KEY]
    assert _tool_steps(db_session, job) == [(K.tool_call, "ok"), (K.tool_result, "ok")]
    assert {e.tool_name for e in _events(db_session, job) if e.kind in (K.tool_call, K.tool_result)} == {
        "search_guidance"
    }
    assert any(t["function"]["name"] == "search_guidance" for t in server.requests[0].body["tools"])


def test_citing_a_real_key_the_job_did_not_receive_fails_as_invalid_output(db_session, factory, local_stub):
    from app.services.machine_review.guidance_corpus import default_guidance_index

    index = default_guidance_index()
    shown = {e.source_key for e in index.search(WARNING_CODE, limit=5)}
    other = next(e.source_key for e in index.entries if e.source_key not in shown)
    server, submission, job, settings_obj = _warned_job(
        db_session, local_stub, [_search_reply(WARNING_CODE), _pass_reply("x"), _sources_reply(other)]
    )
    status, code, failed, job = _outcome(db_session, factory, job, submission, settings_obj)
    assert (status, code, failed) == (S.failed, MachineReviewJobFailure.invalid_output, 1)
    assert K.parse_failed in _kinds(db_session, job)


def test_the_returned_set_is_per_job_so_a_key_a_neighbour_searched_is_not_citable(db_session, factory, local_stub):
    results = {}
    for label, script in (
        ("searched", [_search_reply(WARNING_CODE), _pass_reply("x"), _sources_reply(WARNING_KEY)]),
        ("not_searched", [_pass_reply("x"), _sources_reply(WARNING_KEY)]),
    ):
        server, submission, job, settings_obj = _warned_job(db_session, local_stub, script, with_warning=False)
        results[label] = _outcome(db_session, factory, job, submission, settings_obj)[:3]
    assert results["searched"] == (S.succeeded, None, 0)
    assert results["not_searched"] == (S.failed, MachineReviewJobFailure.invalid_output, 1)


def _tools_rejected():
    return {"error": {"message": "tools param requires --jinja flag"}}


def test_the_no_tools_fallback_shows_the_warning_guidance_and_it_is_citable(db_session, factory, local_stub):
    from tests.services._llm_stub_server import StubResponse

    server, submission, job, settings_obj = _warned_job(db_session, local_stub, [{}])
    server.script[:] = [StubResponse(body=_tools_rejected(), status=500), StubResponse(body=_sources_reply(WARNING_KEY))]
    status, code, failed, job = _outcome(db_session, factory, job, submission, settings_obj)
    assert (status, code, failed) == (S.succeeded, None, 0)
    assert len(server.requests) == 2 and "tools" not in server.requests[1].body
    assert WARNING_KEY in server.requests[1].body["messages"][-1]["content"]


def test_the_no_tools_fallback_without_a_warning_has_no_guidance_and_the_key_is_not_citable(
    db_session, factory, local_stub
):
    from tests.services._llm_stub_server import StubResponse

    server, submission, job, settings_obj = _warned_job(db_session, local_stub, [{}], with_warning=False)
    server.script[:] = [StubResponse(body=_tools_rejected(), status=500), StubResponse(body=_sources_reply(WARNING_KEY))]
    status, code, failed, job = _outcome(db_session, factory, job, submission, settings_obj)
    assert (status, code, failed) == (S.failed, MachineReviewJobFailure.invalid_output, 1)
    assert WARNING_KEY not in json.dumps(server.requests[1].body["messages"])
