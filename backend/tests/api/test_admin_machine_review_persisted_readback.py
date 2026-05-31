"""Persisted ingestion/readback tests for native v2 machine-review results.

Unlike the pure-adapter unit tests (which feed in-memory dicts to adapter
functions), these prove the *full persisted path*: a v2 provider result is
serialized, written as a real ``submission_audit_event`` row, read back from the
database, projected through the admin inspection endpoint, and used to
create/read curator tasks via the admin API.

```text
FakeMachineReviewProvider / make_* result
  -> machine_review_v2_result_to_details_json
  -> record_machine_review_v2_audit_event   (real submission_audit_event row)
  -> DB readback
  -> GET .../machine-review-inspection       (record summaries)
  -> POST .../curator-tasks/build-for-submission
  -> GET  .../curator-tasks                  (the persisted task)
```

Everything stays admin-only and private: no public ``trust.machine_review``, no
mutation of ``submission.status`` / ``RecordReviewStatus`` / scientific records,
no automatic task creation. Follows the existing admin-route testing pattern
(mirroring ``test_admin_machine_review_inspection.py`` /
``test_admin_machine_review_curator_tasks.py``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.machine_review.providers import (
    FakeMachineReviewProvider,
    MachineReviewContext,
    machine_review_v2_result_to_details_json,
)
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewCategory,
    MachineReviewProviderFindingV2,
    MachineReviewProviderResultV2,
    MachineReviewSeverity,
    MachineReviewStatus,
)
from app.services.submission import (
    create_submission,
    link_record,
    record_llm_precheck_audit_event,
    record_machine_review_v2_audit_event,
)
from app.services.trust import build_trust_fragment
from app.services.trust.models import EvidenceBadge, EvidenceEvaluation

_INSPECT_BASE = "/api/v1/admin/submissions"
_TASKS_BASE = "/api/v1/admin/machine-review/curator-tasks"


def _inspect_url(submission_id: int) -> str:
    return f"{_INSPECT_BASE}/{submission_id}/machine-review-inspection"


def _build_url(submission_id: int) -> str:
    return f"{_TASKS_BASE}/build-for-submission/{submission_id}"


# --------------------------------------------------------------------------- #
# Fixtures / seeding
# --------------------------------------------------------------------------- #


@pytest.fixture
def anon_client(db_session: Session):
    """A client with DB overrides but no auth override -> real auth runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def _new_submission(db_session: Session, user_id: int) -> Submission:
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.kinetics,
        title="Persisted MR readback",
        summary="compact",
    )
    db_session.flush()
    return submission


def _link_kinetics(db_session: Session, submission: Submission, record_id: int) -> None:
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.kinetics,
        record_id=record_id,
        role="primary",
    )


def _v2_result(
    *,
    status: MachineReviewStatus,
    findings: tuple[MachineReviewProviderFindingV2, ...],
    summary: str = "advisory",
) -> MachineReviewProviderResultV2:
    """Build a native v2 provider result."""
    return MachineReviewProviderResultV2(
        schema_version=MACHINE_REVIEW_V2_SCHEMA_VERSION,
        status=status,
        curator_priority=None,
        summary=summary,
        findings=findings,
        model="vendor/model-name",
        provider="VendorProvider",
        used_rag=False,
    )


def _kinetics_finding(
    *,
    record_ref: str | None,
    severity: MachineReviewSeverity,
    category: MachineReviewCategory,
    recommended_action: str,
    record_type: str | None = "kinetics",
) -> MachineReviewProviderFindingV2:
    return MachineReviewProviderFindingV2(
        severity=severity,
        category=category,
        record_type=record_type,
        record_ref=record_ref,
        message="Kinetics record needs a closer look.",
        evidence_keys=("missing_checks.tunneling_model",),
        recommended_action=recommended_action,
    )


def _persist_via_provider(
    db_session: Session,
    submission: Submission,
    result: MachineReviewProviderResultV2,
) -> SubmissionAuditEvent:
    """Run a fixed result through the fake provider, then persist it as a row.

    Exercises the real provider plumbing (provider -> serializer -> recorder)
    rather than hand-writing the audit row.
    """
    provider = FakeMachineReviewProvider(fixed_result=result)
    produced = provider.review_submission(
        MachineReviewContext(submission_id=submission.id)
    )
    event = record_machine_review_v2_audit_event(
        db_session,
        submission=submission,
        result=produced,
    )
    db_session.flush()
    return event


# --------------------------------------------------------------------------- #
# Main happy path: persisted v2 warning -> inspection -> task
# --------------------------------------------------------------------------- #


def test_persisted_v2_warning_provider_result_inspects_and_creates_task(
    client, db_session, login_as, _api_admin_user
):
    """A persisted v2 warning result reads back, inspects, and creates one task."""
    submission = _new_submission(db_session, _api_admin_user)
    _link_kinetics(db_session, submission, 9001)
    result = _v2_result(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(
            _kinetics_finding(
                record_ref="9001",
                severity=MachineReviewSeverity.warning,
                category=MachineReviewCategory.kinetics,
                recommended_action="Confirm the tunneling treatment with the uploader.",
            ),
        ),
    )
    event = _persist_via_provider(db_session, submission, result)
    event_id = event.id

    # Raw details_json is persisted with the v2 marker + findings.
    assert event.event_kind is SubmissionAuditEventKind.llm_precheck_recorded
    assert event.actor_kind is SubmissionActorKind.llm
    assert event.details_json["schema_version"] == MACHINE_REVIEW_V2_SCHEMA_VERSION

    # DB readback (fresh load) preserves schema_version and findings.
    db_session.expire_all()
    reloaded = db_session.get(SubmissionAuditEvent, event_id)
    assert reloaded is not None
    assert reloaded.details_json["schema_version"] == MACHINE_REVIEW_V2_SCHEMA_VERSION
    assert reloaded.details_json["status"] == "machine_screened_warning"
    assert len(reloaded.details_json["findings"]) == 1
    finding = reloaded.details_json["findings"][0]
    assert finding["category"] == "kinetics"
    assert finding["record_ref"] == "9001"
    assert finding["recommended_action"]

    login_as(_api_admin_user)

    # Inspection projects exactly one record summary.
    body = client.get(_inspect_url(submission.id)).json()
    assert len(body["record_summaries"]) == 1
    record = body["record_summaries"][0]
    assert record["record_type"] == "kinetics"
    assert record["record_ref"] == "9001"
    assert record["record_id"] == 9001
    assert record["latest_summary"]["status"] == "machine_screened_warning"
    assert record["latest_summary"]["highest_severity"] == "warning"
    assert event_id in body["source_audit_event_ids"]
    assert body["unmapped_findings_count"] == 0

    # Explicit build creates exactly one task.
    build = client.post(_build_url(submission.id))
    assert build.status_code == 200, build.text
    assert build.json()["created_count"] == 1
    task_id = build.json()["task_ids"][0]

    # The task lists with the expected v2-derived fields.
    listing = client.get(_TASKS_BASE).json()
    task_ids = [t["id"] for t in listing["items"]]
    assert task_id in task_ids
    task = client.get(f"{_TASKS_BASE}/{task_id}").json()
    assert task["workflow_state"] == "needs_curator_review"
    assert task["machine_review_status"] == "machine_screened_warning"
    assert task["highest_severity"] == "warning"
    assert task["record_type"] == "kinetics"
    assert task["record_id"] == 9001
    assert task["source_audit_event_id"] == event_id


# --------------------------------------------------------------------------- #
# Critical: v2-only category survives persistence/readback
# --------------------------------------------------------------------------- #


def test_persisted_v2_critical_provider_result_inspects_and_creates_task(
    client, db_session, login_as, _api_admin_user
):
    """A persisted v2 critical result with a v2-only category creates one task.

    The finding keeps the v2-only ``transition_state_validation`` category on a
    kinetics-linked record to prove the contract survives persistence/readback
    without needing transition-state fixtures.
    """
    submission = _new_submission(db_session, _api_admin_user)
    _link_kinetics(db_session, submission, 9002)
    result = _v2_result(
        status=MachineReviewStatus.machine_screened_needs_attention,
        findings=(
            _kinetics_finding(
                record_ref="9002",
                severity=MachineReviewSeverity.critical,
                category=MachineReviewCategory.transition_state_validation,
                recommended_action=(
                    "Do not promote to benchmark_reference until path validation "
                    "is inspected."
                ),
            ),
        ),
    )
    event = _persist_via_provider(db_session, submission, result)
    login_as(_api_admin_user)

    body = client.get(_inspect_url(submission.id)).json()
    record = body["record_summaries"][0]
    assert record["latest_summary"]["status"] == "machine_screened_needs_attention"
    assert record["latest_summary"]["highest_severity"] == "critical"
    # The v2-only category survived the round trip.
    assert event.details_json["findings"][0]["category"] == "transition_state_validation"

    build = client.post(_build_url(submission.id)).json()
    assert build["created_count"] == 1
    task = client.get(f"{_TASKS_BASE}/{build['task_ids'][0]}").json()
    assert task["machine_review_status"] == "machine_screened_needs_attention"
    assert task["highest_severity"] == "critical"


# --------------------------------------------------------------------------- #
# Submission-scoped: diagnostic only, no record summary, no task
# --------------------------------------------------------------------------- #


def test_persisted_v2_submission_scoped_result_does_not_create_task(
    client, db_session, login_as, _api_admin_user
):
    """A v2 finding with no record identity stays a submission-scoped diagnostic."""
    submission = _new_submission(db_session, _api_admin_user)
    _link_kinetics(db_session, submission, 9001)
    result = _v2_result(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(
            _kinetics_finding(
                record_ref=None,
                record_type=None,
                severity=MachineReviewSeverity.warning,
                category=MachineReviewCategory.consistency,
                recommended_action="Review the submission as a whole.",
            ),
        ),
    )
    _persist_via_provider(db_session, submission, result)
    login_as(_api_admin_user)

    body = client.get(_inspect_url(submission.id)).json()
    assert body["record_summaries"] == []
    assert body["unmapped_findings_count"] == 1
    # Submission-scoped is the expected shape, so it is not a mapping warning.
    assert body["mapping_warnings"] == []

    build = client.post(_build_url(submission.id)).json()
    assert build["created_count"] == 0
    assert client.get(_TASKS_BASE).json()["items"] == []


# --------------------------------------------------------------------------- #
# Unlinked record: mapping warning, no record summary, no task
# --------------------------------------------------------------------------- #


def test_persisted_v2_unlinked_record_result_stays_diagnostic(
    client, db_session, login_as, _api_admin_user
):
    """A v2 finding naming an unlinked record is kept as a mapping warning only."""
    submission = _new_submission(db_session, _api_admin_user)
    _link_kinetics(db_session, submission, 9001)
    result = _v2_result(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(
            _kinetics_finding(
                record_ref="7777",  # not linked to this submission
                severity=MachineReviewSeverity.warning,
                category=MachineReviewCategory.kinetics,
                recommended_action="Check the unlinked record.",
            ),
        ),
    )
    _persist_via_provider(db_session, submission, result)
    login_as(_api_admin_user)

    body = client.get(_inspect_url(submission.id)).json()
    assert body["record_summaries"] == []
    assert body["unmapped_findings_count"] == 1
    assert len(body["mapping_warnings"]) == 1
    assert "not linked" in body["mapping_warnings"][0]

    build = client.post(_build_url(submission.id)).json()
    assert build["created_count"] == 0


# --------------------------------------------------------------------------- #
# Malformed v2 payload: parse warning, no summary, no task, no exception
# --------------------------------------------------------------------------- #


def test_persisted_v2_malformed_result_becomes_parse_warning(
    client, db_session, login_as, _api_admin_user
):
    """A malformed v2 payload (used_rag=true) degrades to a parse warning."""
    submission = _new_submission(db_session, _api_admin_user)
    _link_kinetics(db_session, submission, 9001)
    # A raw, malformed v2 event written directly: it carries the v2 marker but
    # violates the contract (used_rag must be false), so it cannot validate.
    db_session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=SubmissionActorKind.llm,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json={
                "schema_version": "machine_review_v2",
                "status": "machine_screened_warning",
                "used_rag": True,
            },
        )
    )
    db_session.flush()
    login_as(_api_admin_user)

    body = client.get(_inspect_url(submission.id))
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["record_summaries"] == []
    assert len(payload["parse_warnings"]) == 1

    build = client.post(_build_url(submission.id))
    assert build.status_code == 200, build.text
    assert build.json()["created_count"] == 0
    assert client.get(_TASKS_BASE).json()["items"] == []


# --------------------------------------------------------------------------- #
# Legacy v1: marker-less payload still works
# --------------------------------------------------------------------------- #


def test_persisted_v1_legacy_event_still_inspects_and_creates_task(
    client, db_session, login_as, _api_admin_user
):
    """A marker-less v1 LLMPrecheckResult event still maps and creates a task."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    v1_result = LLMPrecheckResult(
        label=LLMPrecheckLabel.warning,
        summary="legacy advisory",
        findings=(
            LLMFinding(
                severity=LLMFindingSeverity.warning,
                category=LLMFindingCategory.provenance,
                record_type="calculation",
                record_id=9001,
                message="Missing source artifact summary.",
                evidence_keys=("missing_checks.source_artifact_present",),
            ),
        ),
        model="fake_test/simple-v1",
        used_rag=False,
    )
    event = record_llm_precheck_audit_event(
        db_session,
        submission=submission,
        result=v1_result,
        provider="FakeLLMPrecheckProvider",
    )
    db_session.flush()

    # The legacy payload has no v2 marker -> it takes the adapter's v1 path.
    assert "schema_version" not in event.details_json

    login_as(_api_admin_user)
    body = client.get(_inspect_url(submission.id)).json()
    assert len(body["record_summaries"]) == 1
    assert body["record_summaries"][0]["latest_summary"]["status"] == (
        "machine_screened_warning"
    )

    build = client.post(_build_url(submission.id)).json()
    assert build["created_count"] == 1


# --------------------------------------------------------------------------- #
# Public-boundary + non-interference regression
# --------------------------------------------------------------------------- #


def test_persisted_v2_ingestion_does_not_change_public_trust_or_submission(
    client, db_session, login_as, _api_admin_user
):
    """The full persisted v2 path perturbs no public trust / submission / record state."""
    submission = _new_submission(db_session, _api_admin_user)
    _link_kinetics(db_session, submission, 9001)
    result = _v2_result(
        status=MachineReviewStatus.machine_screened_warning,
        findings=(
            _kinetics_finding(
                record_ref="9001",
                severity=MachineReviewSeverity.warning,
                category=MachineReviewCategory.kinetics,
                recommended_action="Confirm tunneling.",
            ),
        ),
    )
    before = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )

    _persist_via_provider(db_session, submission, result)
    login_as(_api_admin_user)

    # Exercise the whole admin path.
    assert client.get(_inspect_url(submission.id)).status_code == 200
    assert client.post(_build_url(submission.id)).status_code == 200

    # Submission lifecycle/moderation fields are unchanged.
    db_session.refresh(submission)
    after = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    assert after == before

    # No authoritative human review was written for the kinetics record.
    review = db_session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == SubmissionRecordType.kinetics,
            RecordReview.record_id == 9001,
        )
    )
    assert review is None

    # The public TrustFragment still has the frozen precheck shape and no
    # machine_review block.
    evaluation = EvidenceEvaluation(
        record_type="kinetics",
        record_id=9001,
        rubric="computed_kinetics",
        rubric_version=1,
        label=EvidenceBadge.partial,
        passed_checks=("rate_present",),
        missing_checks=("tunneling_model",),
        warning_checks=(),
        not_applicable_checks=(),
        passed_count=1,
        possible_count=2,
        evidence_completeness=0.5,
    )
    dumped = build_trust_fragment(evaluation).model_dump(mode="json")
    assert dumped["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert "machine_review" not in dumped
    assert set(dumped) == {
        "review_status",
        "trust_status",
        "evidence",
        "llm_precheck",
        "is_certified",
    }
