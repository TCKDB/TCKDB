"""Re-running machine reviews whose latest stored review is in the previous format."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.db.models.common import (
    AppUserRole,
    MachineReviewJobStatus,
    MachineReviewProviderKey,
    SubmissionActorKind,
    SubmissionAuditEventKind,
)
from app.db.models.machine_review_job import MachineReviewJob
from app.db.models.submission import SubmissionAuditEvent
from app.services.machine_review.rerun import find_submissions_with_previous_format_review, queue_reruns
from tests.services._machine_review_job_support import FAKE_SETTINGS, insert_job, make_submission, make_user

_T0 = datetime(2026, 1, 1, 12, 0, 0)


def _event(session, submission, *, version: int, minutes: int = 0, actor=SubmissionActorKind.llm):
    session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=actor,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json={
                "format": {"name": "machine_review", "version": version},
                "status": "machine_screened_pass",
                "summary": "advisory",
                "findings": [],
            },
            created_at=_T0 + timedelta(minutes=minutes),
        )
    )
    session.flush()


def _job_count(session, submission) -> int:
    return session.scalar(
        select(func.count()).select_from(MachineReviewJob).where(MachineReviewJob.submission_id == submission.id)
    )


def _seed(db_session):
    admin = make_user(db_session, "rerun-admin", AppUserRole.admin)
    a, b, c, d, e = (make_submission(db_session, admin.id) for _ in range(5))
    _event(db_session, a, version=1)
    _event(db_session, b, version=2)
    _event(db_session, c, version=1, minutes=0)
    _event(db_session, c, version=2, minutes=5)
    _event(db_session, d, version=1)
    insert_job(db_session, d.id, admin.id, status=MachineReviewJobStatus.queued)
    _event(db_session, e, version=1, actor=SubmissionActorKind.curator)  # not a machine review
    return admin, a, b, c, d, e


def test_find_returns_exactly_the_submissions_whose_latest_review_is_the_previous_format(db_session):
    _, a, b, c, d, e = _seed(db_session)

    found = {s.id for s in find_submissions_with_previous_format_review(db_session)}

    assert found == {a.id, d.id}


def test_queue_creates_one_job_per_stale_submission_and_skips_one_with_an_open_job(db_session):
    admin, a, b, c, d, _ = _seed(db_session)
    submissions = [s for s in find_submissions_with_previous_format_review(db_session) if s.id in {a.id, d.id}]

    report = queue_reruns(
        db_session,
        submissions,
        requester=admin,
        provider_key=MachineReviewProviderKey.fake,
        settings_obj=FAKE_SETTINGS,
    )

    assert report.queued == [a.public_ref]
    assert report.already_open == [d.public_ref]
    assert report.refused == {}
    assert (
        _job_count(db_session, a),
        _job_count(db_session, b),
        _job_count(db_session, c),
        _job_count(db_session, d),
    ) == (1, 0, 0, 1)


def test_finding_alone_writes_no_job(db_session):
    _seed(db_session)
    before = db_session.scalar(select(func.count()).select_from(MachineReviewJob))

    find_submissions_with_previous_format_review(db_session)

    assert db_session.scalar(select(func.count()).select_from(MachineReviewJob)) == before


def test_a_requester_who_may_not_request_is_refused_with_the_reason(db_session):
    _, a, *_ = _seed(db_session)
    plain = make_user(db_session, "rerun-plain", AppUserRole.user)

    report = queue_reruns(
        db_session, [a], requester=plain, provider_key=MachineReviewProviderKey.fake, settings_obj=FAKE_SETTINGS
    )

    assert report.queued == [] and report.refused == {a.public_ref: "not_allowed_for_user"}
    assert _job_count(db_session, a) == 0


def test_script_help_runs_without_a_database_environment():
    script = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "rerun_machine_reviews.py"

    done = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True, env={})

    assert done.returncode == 0, done.stderr
    assert "--commit" in done.stdout and "--requested-by" in done.stdout


def _run_script(db_session, monkeypatch, capsys, *argv):
    from app.api.config import settings

    monkeypatch.setattr(settings, "ai_review_assistant_mode", "test")
    return _run_script_keep_mode(db_session, monkeypatch, capsys, *argv)


def _run_script_keep_mode(db_session, monkeypatch, capsys, *argv):
    from sqlalchemy.orm import sessionmaker

    from app.api import deps
    from app.api.config import settings
    from scripts.ops import rerun_machine_reviews as script

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(deps, "SessionLocal", factory)
    monkeypatch.setattr(settings, "deployment_mode", "local")
    code = script.main(list(argv))
    return code, capsys.readouterr()


def test_script_plan_writes_nothing_and_commit_queues_one_job_naming_no_primary_key(db_session, monkeypatch, capsys):
    admin, a, b, c, d, _ = _seed(db_session)

    code, out = _run_script(db_session, monkeypatch, capsys, "--requested-by", admin.username)
    assert code == 0 and a.public_ref in out.out and b.public_ref not in out.out
    assert _job_count(db_session, a) == 0

    code, out = _run_script(
        db_session, monkeypatch, capsys, "--requested-by", admin.username, "--commit", "--i-know-this-is-deployed"
    )
    assert code == 0
    assert _job_count(db_session, a) == 1 and _job_count(db_session, b) == 0 and _job_count(db_session, d) == 1
    assert f"skipped (a job is already queued or running): {d.public_ref}" in out.out
    assert f" {a.id}" not in out.out.replace(a.public_ref, "")


def test_script_refuses_a_requester_who_is_not_an_admin_with_exit_2(db_session, monkeypatch, capsys):
    _, a, *_ = _seed(db_session)
    curator = make_user(db_session, "rerun-curator", AppUserRole.curator)

    code, out = _run_script(
        db_session, monkeypatch, capsys, "--requested-by", curator.username, "--commit", "--i-know-this-is-deployed"
    )

    assert code == 2
    assert _job_count(db_session, a) == 0


def test_script_plan_lists_submissions_when_the_assistant_mode_is_off(db_session, monkeypatch, capsys):
    from app.api.config import settings

    admin, a, *_ = _seed(db_session)

    def off(session, monkeypatch, capsys, *argv):
        monkeypatch.setattr(settings, "ai_review_assistant_mode", "off")
        return _run_script_keep_mode(session, monkeypatch, capsys, *argv)

    code, out = off(db_session, monkeypatch, capsys, "--requested-by", admin.username)
    assert code == 0 and a.public_ref in out.out
    assert "--commit will need" in out.out

    code, _ = off(
        db_session, monkeypatch, capsys, "--requested-by", admin.username, "--commit", "--i-know-this-is-deployed"
    )
    assert code == 2
    assert _job_count(db_session, a) == 0


def test_process_drains_jobs_left_queued_by_an_earlier_run(db_session, monkeypatch, capsys):
    from app.workers import machine_review_worker

    admin, a, *_ = _seed(db_session)
    commit = ("--requested-by", admin.username, "--commit", "--i-know-this-is-deployed")
    _run_script(db_session, monkeypatch, capsys, *commit)
    assert _job_count(db_session, a) == 1

    cycles = iter([True, True, False])
    monkeypatch.setattr(machine_review_worker, "process_one_cycle", lambda: next(cycles))
    code, out = _run_script(db_session, monkeypatch, capsys, *commit, "--process")

    assert code == 0
    assert "Queued 0 job(s)." in out.out
    assert "Ran 2 job(s) in this process." in out.out
