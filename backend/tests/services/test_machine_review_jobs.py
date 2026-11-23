"""Machine-review job service: creation guards, events, claim, reaper, cancel and fence."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.api.config import Settings
from app.db.models.common import (
    AppUserRole,
    MachineReviewAvailability,
    MachineReviewExecutor,
    MachineReviewJobEventKind,
    MachineReviewJobFailure,
    MachineReviewJobStatus,
    MachineReviewProviderKey,
)
from app.db.models.machine_review_job import MachineReviewJob, MachineReviewJobEvent
from app.services.machine_review import jobs
from app.services.machine_review.switches import write_machine_review_switches
from tests.services._machine_review_job_support import (
    CLOUD_SETTINGS,
    FAKE_SETTINGS,
    OFF_SETTINGS,
    insert_job,
    make_submission,
    make_user,
)

FAKE = MachineReviewProviderKey.fake


@pytest.fixture
def ctx(db_session):
    admin = make_user(db_session, "job-admin", AppUserRole.admin)
    curator = make_user(db_session, "job-curator", AppUserRole.curator)
    plain = make_user(db_session, "job-plain", AppUserRole.user)
    submission = make_submission(db_session, plain.id)
    return db_session, admin, curator, plain, submission


def _count(session, submission_id):
    return session.scalar(
        select(func.count()).select_from(MachineReviewJob).where(MachineReviewJob.submission_id == submission_id)
    )


def _create(session, submission, user, key=FAKE, settings_obj=FAKE_SETTINGS):
    return jobs.create_job(
        session, submission_id=submission.id, requester=user, provider_key=key, settings_obj=settings_obj
    )


def test_second_open_job_for_a_submission_raises_and_leaves_one_row(ctx):
    session, admin, _, _, submission = ctx
    first = _create(session, submission, admin)

    with pytest.raises(jobs.MachineReviewJobConflict) as raised:
        _create(session, submission, admin)

    assert raised.value.open_job.id == first.id
    assert _count(session, submission.id) == 1


def test_a_new_job_is_allowed_once_the_first_is_terminal(ctx):
    session, admin, _, _, submission = ctx
    first = _create(session, submission, admin)
    jobs.request_cancel(session, first.id)

    second = _create(session, submission, admin)

    assert second.id != first.id
    assert _count(session, submission.id) == 2


def test_missing_submission_is_not_found(ctx):
    from app.api.errors import NotFoundError

    session, admin, *_ = ctx
    with pytest.raises(NotFoundError):
        jobs.create_job(session, submission_id=-1, requester=admin, provider_key=FAKE, settings_obj=FAKE_SETTINGS)


def _reason(session, submission, user, **kw):
    with pytest.raises(jobs.MachineReviewUnavailable) as raised:
        _create(session, submission, user, **kw)
    return raised.value.reason


def test_mode_off_refuses_everyone_and_fake_mode_twin_accepts(ctx):
    session, admin, curator, *_, submission = ctx
    assert _reason(session, submission, admin, settings_obj=OFF_SETTINGS) == "mode_off"
    assert _reason(session, submission, curator, settings_obj=OFF_SETTINGS) == "mode_off"
    assert _create(session, submission, admin).status is MachineReviewJobStatus.queued


def test_machine_review_switch_off_refuses_admin_and_curator(ctx):
    session, admin, curator, _, submission = ctx
    write_machine_review_switches(session, actor=admin, machine_review_enabled=False)
    assert _reason(session, submission, admin) == "machine_review_disabled"
    assert _reason(session, submission, curator) == "machine_review_disabled"
    write_machine_review_switches(session, actor=admin, machine_review_enabled=True)
    assert _create(session, submission, curator).requested_by == curator.id


def test_curator_switch_off_refuses_curator_but_not_admin(ctx):
    session, admin, curator, _, submission = ctx
    write_machine_review_switches(session, actor=admin, curator_requests_enabled=False)
    assert _reason(session, submission, curator) == "curator_requests_disabled"
    assert _create(session, submission, admin).requested_by == admin.id


def test_plain_user_is_refused_and_curator_twin_accepted(ctx):
    session, _, curator, plain, submission = ctx
    assert _reason(session, submission, plain) == "not_allowed_for_user"
    assert _create(session, submission, curator).status is MachineReviewJobStatus.queued


@pytest.mark.parametrize("key", [MachineReviewProviderKey.claude_code, MachineReviewProviderKey.codex])
def test_remote_keys_are_refused_until_a_runner_exists(ctx, key):
    session, admin, _, _, submission = ctx
    assert _reason(session, submission, admin, key=key) == "remote_runner_unavailable"
    assert _count(session, submission.id) == 0


def test_provider_key_must_match_the_mode(ctx):
    session, admin, _, _, submission = ctx
    assert _reason(session, submission, admin, key=MachineReviewProviderKey.cloud) == "provider_not_configured"
    assert _reason(session, submission, admin, settings_obj=CLOUD_SETTINGS) == "provider_not_configured"
    hosted_fake = Settings(ai_review_assistant_mode="test", deployment_mode="hosted_public")
    assert _reason(session, submission, admin, settings_obj=hosted_fake) == "provider_not_configured"
    job = _create(session, submission, admin, key=MachineReviewProviderKey.cloud, settings_obj=CLOUD_SETTINGS)
    assert job.provider_key is MachineReviewProviderKey.cloud


def test_event_seq_runs_one_to_n(ctx):
    session, admin, _, _, submission = ctx
    job = _create(session, submission, admin)
    for _ in range(4):
        jobs.append_event(session, job, MachineReviewJobEventKind.model_turn, outcome="ok")
    seqs = list(
        session.scalars(
            select(MachineReviewJobEvent.seq).where(MachineReviewJobEvent.job_id == job.id).order_by(MachineReviewJobEvent.seq)
        )
    )
    assert seqs == [1, 2, 3, 4]


@pytest.mark.parametrize("store", [False, True])
def test_detail_is_stored_only_with_full_context_storage(ctx, store):
    session, admin, _, _, submission = ctx
    job = _create(session, submission, admin)
    settings_obj = Settings(llm_precheck_store_full_context=store)
    jobs.append_event(
        session, job, MachineReviewJobEventKind.model_turn, detail={"prompt": "x"}, settings_obj=settings_obj
    )
    stored = session.scalar(select(MachineReviewJobEvent.detail_json).where(MachineReviewJobEvent.job_id == job.id))
    assert stored == ({"prompt": "x"} if store else None)


def test_claim_marks_running_and_stamps_the_recipe(ctx):
    session, admin, _, _, submission = ctx
    job = _create(session, submission, admin)

    claimed = jobs.claim_next_job(session, settings_obj=FAKE_SETTINGS)

    assert claimed.id == job.id
    assert claimed.status is MachineReviewJobStatus.running
    assert (claimed.attempts, claimed.prompt_name, claimed.lease_expires_at is not None) == (1, "machine_review", True)
    assert claimed.model == "fake_test/machine-review"
    kinds = list(session.scalars(select(MachineReviewJobEvent.kind).where(MachineReviewJobEvent.job_id == job.id)))
    assert kinds == [MachineReviewJobEventKind.claimed]
    assert jobs.claim_next_job(session, settings_obj=FAKE_SETTINGS) is None


def test_remote_runner_job_is_not_claimed_in_process(ctx):
    session, admin, _, _, submission = ctx
    remote = insert_job(
        session, submission.id, admin.id, provider_key=MachineReviewProviderKey.codex,
        executor=MachineReviewExecutor.remote_runner,
    )
    assert jobs.claim_next_job(session, settings_obj=FAKE_SETTINGS) is None
    assert jobs.claim_next_job(session, executor=MachineReviewExecutor.remote_runner).id == remote.id


def test_cancel_queued_running_and_terminal(ctx):
    session, admin, _, _, submission = ctx
    queued = _create(session, submission, admin)
    assert jobs.request_cancel(session, queued.id).status is MachineReviewJobStatus.cancelled

    running = _create(session, submission, admin)
    jobs.claim_next_job(session, settings_obj=FAKE_SETTINGS)
    after = jobs.request_cancel(session, running.id)
    assert after.status is MachineReviewJobStatus.running and after.cancel_requested
    assert jobs.is_cancel_requested(session, running.id)

    with pytest.raises(jobs.MachineReviewJobNotCancellable):
        jobs.request_cancel(session, queued.id)


def test_reaper_requeues_once_then_fails_worker_lost(ctx):
    session, admin, _, _, submission = ctx
    job = _create(session, submission, admin)
    t0 = job.created_at
    claimed = jobs.claim_next_job(session, now=t0, settings_obj=FAKE_SETTINGS)
    late = t0 + jobs.LEASE_DURATION + timedelta(seconds=1)

    assert jobs.reap_expired(session, late) == 1
    session.refresh(claimed)
    assert (claimed.status, claimed.attempts) == (MachineReviewJobStatus.queued, 1)
    retry = session.scalars(
        select(MachineReviewJobEvent).where(MachineReviewJobEvent.kind == MachineReviewJobEventKind.retry)
    ).one()
    assert retry.status_code == "lease_expired"

    jobs.claim_next_job(session, now=late, settings_obj=FAKE_SETTINGS)
    assert jobs.reap_expired(session, late + jobs.LEASE_DURATION + timedelta(seconds=1)) == 1
    session.refresh(claimed)
    assert (claimed.status, claimed.failure_code, claimed.audit_event_id, claimed.attempts) == (
        MachineReviewJobStatus.failed, MachineReviewJobFailure.worker_lost, None, 2,
    )


def test_reaper_cancels_an_expired_job_with_a_cancel_request(ctx):
    session, admin, _, _, submission = ctx
    job = _create(session, submission, admin)
    t0 = job.created_at
    jobs.claim_next_job(session, now=t0, settings_obj=FAKE_SETTINGS)
    jobs.request_cancel(session, job.id)

    assert jobs.reap_expired(session, t0 + jobs.LEASE_DURATION + timedelta(seconds=1)) == 1
    session.refresh(job)
    assert (job.status, job.failure_code) == (MachineReviewJobStatus.cancelled, None)
    assert job.finished_at is not None
    assert jobs.claim_next_job(session, settings_obj=FAKE_SETTINGS) is None
    last = session.scalars(
        select(MachineReviewJobEvent).where(MachineReviewJobEvent.job_id == job.id).order_by(MachineReviewJobEvent.seq.desc())
    ).first()
    assert (last.kind, last.status_code) == (MachineReviewJobEventKind.finished, "cancelled")


def test_heartbeat_extends_the_lease_only_for_the_current_attempt(ctx):
    session, admin, _, _, submission = ctx
    job = _create(session, submission, admin)
    t0 = job.created_at
    jobs.claim_next_job(session, now=t0, settings_obj=FAKE_SETTINGS)
    t1 = t0 + timedelta(minutes=4)

    assert jobs.heartbeat(session, job.id, job.attempts, t1) is True
    assert job.lease_expires_at == t1 + jobs.LEASE_DURATION

    assert jobs.heartbeat(session, job.id, job.attempts + 1, t1 + timedelta(minutes=1)) is False
    assert job.lease_expires_at == t1 + jobs.LEASE_DURATION

    jobs.finish_job(session, job.id, job.attempts, status=MachineReviewJobStatus.cancelled)
    assert jobs.heartbeat(session, job.id, job.attempts, t1) is False


def test_unknown_unavailable_reason_is_refused():
    with pytest.raises(ValueError):
        jobs.MachineReviewUnavailable("nope")


def test_reaper_leaves_a_live_lease_alone(ctx):
    session, admin, *_, submission = ctx
    job = _create(session, submission, admin)
    jobs.claim_next_job(session, now=job.created_at, settings_obj=FAKE_SETTINGS)
    assert jobs.reap_expired(session, job.created_at + timedelta(seconds=30)) == 0
    assert job.status is MachineReviewJobStatus.running


def test_finish_job_fence_rejects_a_stale_attempt(ctx):
    session, admin, *_, submission = ctx
    job = _create(session, submission, admin)
    jobs.claim_next_job(session, settings_obj=FAKE_SETTINGS)

    stale = jobs.finish_job(session, job.id, job.attempts + 1, status=MachineReviewJobStatus.cancelled)
    assert stale is False
    assert job.status is MachineReviewJobStatus.running
    events = session.scalar(select(func.count()).select_from(MachineReviewJobEvent).where(MachineReviewJobEvent.job_id == job.id))
    assert events == 1  # only 'claimed'

    assert jobs.finish_job(session, job.id, job.attempts, status=MachineReviewJobStatus.cancelled) is True
    assert jobs.finish_job(session, job.id, job.attempts, status=MachineReviewJobStatus.cancelled) is False


def test_failure_detail_is_clipped(ctx):
    session, admin, *_, submission = ctx
    job = _create(session, submission, admin)
    jobs.claim_next_job(session, settings_obj=FAKE_SETTINGS)
    jobs.finish_job(
        session, job.id, job.attempts, status=MachineReviewJobStatus.failed,
        failure_code=MachineReviewJobFailure.internal_error, failure_detail="x" * 5000,
    )
    assert len(job.failure_detail) == 2000


# ---------------------------------------------------------------------------
# Availability and the refusal vocabulary
# ---------------------------------------------------------------------------

Avail = MachineReviewAvailability
_ALL_KEYS = list(MachineReviewProviderKey)


def _availability(session, user, key, settings_obj=FAKE_SETTINGS):
    return jobs.provider_availability(session, user, key, settings_obj)


def test_curator_never_gets_a_remote_key_but_gets_fake(ctx):
    session, admin, curator, *_ = ctx
    for key in (MachineReviewProviderKey.claude_code, MachineReviewProviderKey.codex):
        assert _availability(session, curator, key) is Avail.not_allowed_for_user
        assert _availability(session, admin, key) is Avail.remote_runner_unavailable
    assert _availability(session, curator, FAKE) is Avail.ok
    assert _availability(session, admin, FAKE) is Avail.ok


def test_availability_follows_the_switches_and_mode(ctx):
    session, admin, curator, plain, _ = ctx
    assert _availability(session, plain, FAKE) is Avail.not_allowed_for_user
    assert _availability(session, admin, FAKE, OFF_SETTINGS) is Avail.mode_off
    assert _availability(session, admin, MachineReviewProviderKey.cloud) is Avail.provider_not_configured
    write_machine_review_switches(session, actor=admin, curator_requests_enabled=False)
    assert _availability(session, curator, FAKE) is Avail.curator_requests_disabled
    assert _availability(session, admin, FAKE) is Avail.ok
    write_machine_review_switches(session, actor=admin, machine_review_enabled=False)
    assert _availability(session, admin, FAKE) is Avail.machine_review_disabled


def test_list_provider_availability_names_every_key(ctx):
    session, _, curator, *_ = ctx
    listed = jobs.list_provider_availability(session, curator, FAKE_SETTINGS)
    assert list(listed) == _ALL_KEYS
    assert listed[FAKE] is Avail.ok


@pytest.mark.parametrize("role", ["admin", "curator", "plain"])
@pytest.mark.parametrize("switches", [(True, True), (True, False), (False, True)])
@pytest.mark.parametrize("which", ["test", "off", "cloud"])
def test_create_job_refuses_exactly_what_availability_reports(ctx, role, switches, which):
    session, admin, curator, plain, submission = ctx
    user = {"admin": admin, "curator": curator, "plain": plain}[role]
    settings_obj = {"test": FAKE_SETTINGS, "off": OFF_SETTINGS, "cloud": CLOUD_SETTINGS}[which]
    write_machine_review_switches(
        session, actor=admin, machine_review_enabled=switches[0], curator_requests_enabled=switches[1]
    )
    for key in _ALL_KEYS:
        expected = _availability(session, user, key, settings_obj)
        if expected is Avail.ok:
            job = _create(session, submission, user, key, settings_obj)
            jobs.request_cancel(session, job.id)
        else:
            assert _reason(session, submission, user, key=key, settings_obj=settings_obj) == expected.value


_STATUS_AND_CODE = {
    "mode_off": (409, "machine_review_mode_off"),
    "machine_review_disabled": (409, "machine_review_disabled"),
    "curator_requests_disabled": (403, "machine_review_curator_requests_disabled"),
    "not_allowed_for_user": (403, "machine_review_not_allowed_for_user"),
    "provider_not_configured": (409, "machine_review_provider_not_configured"),
    "remote_runner_unavailable": (409, "machine_review_remote_runner_unavailable"),
}


@pytest.mark.parametrize("reason", sorted(_STATUS_AND_CODE))
def test_each_refusal_carries_its_status_and_code(reason):
    error = jobs.MachineReviewUnavailable(reason)
    assert (error.status, error.code) == _STATUS_AND_CODE[reason]
    assert error.reason == reason


def test_reasons_are_the_availability_vocabulary_without_ok():
    assert set(jobs.MachineReviewUnavailable.REASONS) == {a.value for a in Avail} - {"ok"}


def test_conflict_and_not_cancellable_carry_status_code_and_an_id_free_context(ctx):
    session, admin, _, _, submission = ctx
    first = _create(session, submission, admin)
    with pytest.raises(jobs.MachineReviewJobConflict) as raised:
        _create(session, submission, admin)
    assert (raised.value.status, raised.value.code) == (409, "machine_review_job_conflict")
    open_job = raised.value.context["open_job"]
    assert set(open_job) == {"status", "provider_key", "step_count", "cancel_requested", "created_at", "started_at"}
    assert open_job["created_at"] == first.created_at.isoformat()
    jobs.request_cancel(session, first.id)
    with pytest.raises(jobs.MachineReviewJobNotCancellable) as raised:
        jobs.request_cancel(session, first.id)
    assert (raised.value.status, raised.value.code) == (409, "machine_review_job_not_cancellable")


def test_get_list_and_events_read_back_in_order(ctx):
    session, admin, _, _, submission = ctx
    first = _create(session, submission, admin)
    jobs.request_cancel(session, first.id)
    second = _create(session, submission, admin)
    for _ in range(3):
        jobs.append_event(session, second, MachineReviewJobEventKind.model_turn, outcome="ok")

    assert jobs.get_job(session, second.id).id == second.id
    rows, total = jobs.list_jobs(session, submission.id, offset=0, limit=10)
    assert [j.id for j in rows] == [second.id, first.id] and total == 2
    assert [e.seq for e in jobs.list_events(session, second.id, after_seq=1)] == [2, 3]
    from app.api.errors import NotFoundError

    with pytest.raises(NotFoundError) as missing:
        jobs.get_job(session, -1)
    assert missing.value.code == "machine_review_job_not_found"
