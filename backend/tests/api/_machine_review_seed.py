"""Shared seeding for the machine-review read-surface tests.

Everything goes through the real path: a provider is substituted at the factory
the runner calls, then the admin run route writes the audit event and the admin
build route writes the curator tasks. Nothing here hand-writes an audit row for
a review that is meant to be a real one.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

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

RUN_URL = "/api/v1/admin/machine-review/run-for-submission"
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
        record_ref=record_ref,
        record_id=record_id,
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
        used_rag=False,
    )


def use_provider(monkeypatch, provider) -> None:
    monkeypatch.setattr(
        "app.services.machine_review.run.build_machine_review_provider",
        lambda settings_obj: provider,
    )


def run_review(client, monkeypatch, submission_id: int, fixed: MachineReviewProviderResult) -> dict:
    """POST the real admin run route with a fixed provider result (caller is admin)."""
    use_provider(monkeypatch, FakeMachineReviewProvider(fixed_result=fixed))
    resp = client.post(f"{RUN_URL}/{submission_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["audit_event_recorded"] is True
    # A real request gets a fresh session; the harness shares one, whose loaded
    # ``submission.audit_events`` would not see the event just written.
    client._db_session.expire_all()
    return resp.json()


def build_tasks(client, submission_id: int) -> dict:
    resp = client.post(f"{BUILD_URL}/{submission_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()
