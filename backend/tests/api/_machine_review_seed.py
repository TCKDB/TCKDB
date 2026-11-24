"""Shared seeding for the machine-review read-surface tests.

Everything goes through the real path: the job route queues a review, one
worker cycle runs it with a substituted provider and writes the audit event, and
the admin build route writes the curator tasks. Nothing here hand-writes an
audit row for a review that is meant to be a real one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.api.config import settings as app_settings
from app.db.models.common import SubmissionKind, SubmissionRecordType
from app.db.models.submission import Submission
from app.services.machine_review.providers.fake import FakeMachineReviewProvider
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_FORMAT,
    CuratorPriority,
    MachineReviewCategory,
    MachineReviewProviderFinding,
    MachineReviewProviderResult,
    MachineReviewSeverity,
    MachineReviewStatus,
)
from app.services.submission import create_submission, link_record
from app.workers import machine_review_worker

JOBS_URL = "/api/v1/machine-review/jobs"
BUILD_URL = "/api/v1/admin/machine-review/curator-tasks/build-for-submission"
TASKS_URL = "/api/v1/admin/machine-review/curator-tasks"
INSPECT_URL = "/api/v1/admin/submissions/{}/machine-review-inspection"


def new_submission(db_session: Session, user_id: int, *, title: str = "machine review read") -> Submission:
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title=title,
        summary="compact",
    )
    db_session.flush()
    return submission


def link(db_session: Session, submission: Submission, record_type: SubmissionRecordType, record_id: int) -> None:
    link_record(
        db_session,
        submission=submission,
        record_type=record_type,
        record_id=record_id,
        role="primary",
    )
    db_session.flush()


def finding(
    record_type: str | None,
    record_id: int | None,
    *,
    severity: MachineReviewSeverity = MachineReviewSeverity.warning,
    category: MachineReviewCategory = MachineReviewCategory.provenance,
    message: str = "Missing source artifact summary.",
    evidence_keys: tuple[str, ...] = ("missing_checks.source_artifact_present",),
    recommended_action: str | None = "Ask the depositor for the source artifact.",
    record_ref: str | None = None,
) -> MachineReviewProviderFinding:
    return MachineReviewProviderFinding(
        severity=severity,
        category=category,
        record_type=record_type,
        record_ref=record_ref if record_ref is not None else (None if record_id is None else str(record_id)),
        message=message,
        evidence_keys=evidence_keys,
        recommended_action=recommended_action,
    )


def result(
    findings: tuple[MachineReviewProviderFinding, ...] = (),
    *,
    status: MachineReviewStatus = MachineReviewStatus.machine_screened_warning,
    summary: str = "Reviewer summary text.",
    priority: CuratorPriority | None = CuratorPriority.medium,
) -> MachineReviewProviderResult:
    return MachineReviewProviderResult(
        format=MACHINE_REVIEW_FORMAT,
        status=status,
        curator_priority=priority,
        summary=summary,
        findings=findings,
        model="fake_test/machine-review",
        provider="FakeMachineReviewProvider",
    )


def use_provider(monkeypatch, provider) -> None:
    monkeypatch.setattr(
        "app.services.machine_review.run.build_machine_review_provider",
        lambda settings_obj: provider,
    )


class _RaisingProvider:
    """Stands in for a transport that cannot reach the model."""

    def review_submission(self, context):
        raise RuntimeError("upstream endpoint unreachable")


def run_review(
    client,
    monkeypatch,
    submission_id: int,
    fixed: MachineReviewProviderResult | None = None,
    *,
    expect: str = "succeeded",
) -> dict:
    """Queue a review through the job route and run it in one worker cycle.

    ``fixed`` is the provider's answer; ``expect="failed"`` makes the provider raise.
    Returns the job as the job route reads it after the cycle.
    """
    monkeypatch.setattr(app_settings, "ai_review_assistant_mode", "test")
    monkeypatch.setattr(app_settings, "deployment_mode", "local")
    session = client._db_session
    provider = _RaisingProvider() if expect == "failed" else FakeMachineReviewProvider(fixed_result=fixed)
    queued = client.post(JOBS_URL, json={"submission_id": submission_id, "provider_key": "fake"})
    assert queued.status_code == 202, queued.text
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False, join_transaction_mode="create_savepoint")
    assert machine_review_worker.process_one_cycle(factory, app_settings, heartbeat=False, provider=provider)
    # A real request gets a fresh session; the harness shares one, whose loaded
    # ``submission.audit_events`` would not see the event just written.
    session.expire_all()
    job = client.get(f"{JOBS_URL}/{queued.json()['id']}")
    assert job.status_code == 200, job.text
    assert job.json()["status"] == expect, job.text
    if expect == "succeeded":
        assert job.json()["audit_event_id"] is not None
    return job.json()


def build_tasks(client, submission_id: int) -> dict:
    resp = client.post(f"{BUILD_URL}/{submission_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()
