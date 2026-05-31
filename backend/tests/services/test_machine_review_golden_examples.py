"""Golden fake-provider machine-review examples.

Drives a set of realistic, repeatable fake-provider payloads
(`backend/tests/fixtures/machine_review/*.json`) through the *real* private
pipeline end-to-end:

    submission_audit_event.details_json
      -> machine_review audit adapter (validate + safe map)
      -> build_submission_machine_review_inspection
      -> build_curator_tasks_for_submission
      -> machine_review_curator_task rows

Each fixture is a self-contained golden input (its linked records + the exact
persisted `details_json`) plus an `expected` block. These let a maintainer
evaluate whether statuses are understandable, findings map correctly, false
positives look manageable, task creation is sane, and public exposure remains
premature — without a real provider.

Stable-output policy: timestamps (`reviewed_at`/`created_at`) are not asserted
for exact wall-clock values; the assertions are on statuses, counts, mapping/
parse diagnostics, task workflow state, and fingerprint stability.

See `backend/docs/specs/machine_review_golden_examples.md` for the per-case
narrative. NB: the persisted precheck vocabulary (`LLMFindingCategory`) is a
subset of the service-layer `MachineReviewCategory`, so the transition-state
case uses `consistency` (the precheck path cannot emit
`transition_state_validation`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.record_review import RecordReview
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.services.machine_review import (
    build_curator_tasks_for_submission,
    build_submission_machine_review_inspection,
    compute_finding_fingerprint,
)
from app.services.machine_review.schemas import (
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewSeverity,
)
from app.services.submission import create_submission, link_record
from app.services.trust import build_trust_fragment
from app.services.trust.models import EvidenceBadge, EvidenceEvaluation

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "machine_review"
_CASES = (
    "clean_pass_no_tasks",
    "kinetics_warning_creates_task",
    "transition_state_critical_creates_task",
    "submission_scoped_finding_no_task",
    "unlinked_record_finding_diagnostic_only",
    "malformed_payload_parse_warning_only",
)


def _load(case: str) -> dict:
    return json.loads((_FIXTURES / f"{case}.json").read_text())


def _seed(db_session: Session, user_id: int, fixture: dict) -> Submission:
    """Seed a submission, its record links, and one machine-review audit event
    whose details_json is exactly the golden payload."""
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title=fixture["case"],
        summary="golden example",
    )
    db_session.flush()
    for link in fixture["linked_records"]:
        link_record(
            db_session,
            submission=submission,
            record_type=SubmissionRecordType(link["record_type"]),
            record_id=link["record_id"],
            role=link.get("role"),
        )
    db_session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=SubmissionActorKind.llm,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json=fixture["audit_event_details_json"],
        )
    )
    db_session.flush()
    return submission


def _links(db_session: Session, submission_id: int):
    return list(
        db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id
            )
        ).all()
    )


def _events(db_session: Session, submission_id: int):
    return list(
        db_session.scalars(
            select(SubmissionAuditEvent).where(
                SubmissionAuditEvent.submission_id == submission_id
            )
        ).all()
    )


def _inspect(db_session: Session, submission: Submission):
    return build_submission_machine_review_inspection(
        submission_id=submission.id,
        submission_record_links=_links(db_session, submission.id),
        submission_audit_events=_events(db_session, submission.id),
    )


def _count_tasks(db_session: Session, submission_id: int) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(MachineReviewCuratorTask)
        .where(MachineReviewCuratorTask.submission_id == submission_id)
    )


# --------------------------------------------------------------------------- #
# Per-case golden assertions (inspection + curator task build)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", _CASES)
def test_golden_case_inspection_and_build(db_session, _api_test_user, case):
    fixture = _load(case)
    expected = fixture["expected"]
    submission = _seed(db_session, _api_test_user, fixture)

    inspection = _inspect(db_session, submission)

    assert len(inspection.record_inspections) == expected["record_summary_count"]
    assert len(inspection.unmapped_findings) == expected["unmapped_findings_count"]
    assert len(inspection.mapping_warnings) == expected["mapping_warnings_count"]
    assert len(inspection.parse_warnings) == expected["parse_warnings_count"]

    if "mapping_warning_contains" in expected:
        assert any(
            expected["mapping_warning_contains"] in w
            for w in inspection.mapping_warnings
        )

    if expected["record_summary_count"] == 1:
        record = inspection.record_inspections[0]
        assert record.record_type == expected["record_type"]
        assert record.record_id == expected["record_id"]
        assert record.latest_summary.status.value == expected["latest_status"]
        assert record.latest_summary.highest_severity.value == expected["highest_severity"]

    result = build_curator_tasks_for_submission(db_session, inspection=inspection)
    assert result.created_count == expected["created_tasks"]
    assert _count_tasks(db_session, submission.id) == expected["created_tasks"]

    if expected["created_tasks"] == 1:
        task = db_session.get(MachineReviewCuratorTask, result.task_ids[0])
        assert task.workflow_state.value == expected["workflow_state"]
        assert task.record_id == expected["record_id"]
        assert task.highest_severity.value == expected["highest_severity"]
        assert task.machine_review_status.value == expected["latest_status"]


def test_clean_pass_creates_no_record_summary_or_task(db_session, _api_test_user):
    """Documented behavior: the adapter only builds record reviews from findings,
    so a pass with no findings yields no record summary and no task."""
    fixture = _load("clean_pass_no_tasks")
    submission = _seed(db_session, _api_test_user, fixture)
    inspection = _inspect(db_session, submission)
    assert inspection.record_inspections == ()
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)
    assert result.created_count == 0
    assert result.skipped_info_count == 0


def test_critical_case_does_not_affect_sibling_record(db_session, _api_test_user):
    """The critical TS finding maps only to its own record; a sibling linked
    record receives no summary and no task."""
    fixture = _load("transition_state_critical_creates_task")
    submission = create_submission(
        db_session,
        created_by=_api_test_user,
        submission_kind=SubmissionKind.thermo,
        title="ts critical + sibling",
        summary="golden",
    )
    db_session.flush()
    link_record(
        db_session, submission=submission,
        record_type=SubmissionRecordType.transition_state_entry, record_id=9002, role="ts",
    )
    # A sibling kinetics record the finding does NOT name.
    link_record(
        db_session, submission=submission,
        record_type=SubmissionRecordType.kinetics, record_id=9001, role="sibling",
    )
    db_session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=SubmissionActorKind.llm,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json=fixture["audit_event_details_json"],
        )
    )
    db_session.flush()

    inspection = _inspect(db_session, submission)
    assert len(inspection.record_inspections) == 1
    assert inspection.record_inspections[0].record_id == 9002
    result = build_curator_tasks_for_submission(db_session, inspection=inspection)
    assert result.created_count == 1
    # Only the TS record got a task; the sibling kinetics record did not.
    task = db_session.get(MachineReviewCuratorTask, result.task_ids[0])
    assert task.record_type is SubmissionRecordType.transition_state_entry


# --------------------------------------------------------------------------- #
# Fingerprint behavior
# --------------------------------------------------------------------------- #


def _finding(
    *,
    message="Note mentions tunneling but tunneling_model is null.",
    evidence_keys=("missing_checks.tunneling_model", "kinetics.note"),
    recommended_action="Clarify whether tunneling was applied.",
) -> MachineReviewFinding:
    return MachineReviewFinding(
        severity=MachineReviewSeverity.warning,
        category=MachineReviewCategory.kinetics,
        record_type="kinetics",
        record_ref="9001",
        message=message,
        evidence_keys=evidence_keys,
        recommended_action=recommended_action,
    )


def _fp(finding: MachineReviewFinding) -> str:
    return compute_finding_fingerprint(
        finding=finding, record_type="kinetics", record_id=9001
    )


def test_fingerprint_evidence_key_ordering_does_not_matter():
    a = _fp(_finding(evidence_keys=("a", "b", "c")))
    b = _fp(_finding(evidence_keys=("c", "b", "a")))
    assert a == b


def test_fingerprint_changes_with_message():
    assert _fp(_finding(message="one")) != _fp(_finding(message="two"))


def test_fingerprint_changes_with_evidence_keys():
    assert _fp(_finding(evidence_keys=("a",))) != _fp(_finding(evidence_keys=("a", "b")))


def test_fingerprint_changes_with_recommended_action():
    assert _fp(_finding(recommended_action="x")) != _fp(_finding(recommended_action="y"))


def test_build_dedups_across_audit_event_and_model(db_session, _api_test_user):
    """Re-running the precheck (new audit event id, different model/provider) for
    the same finding reuses the task — fingerprint excludes event id, model,
    and provider."""
    fixture = _load("kinetics_warning_creates_task")
    submission = _seed(db_session, _api_test_user, fixture)

    first = build_curator_tasks_for_submission(
        db_session, inspection=_inspect(db_session, submission)
    )
    assert first.created_count == 1

    # A fresh precheck run: new audit event (new id) with the same finding but a
    # different model/provider.
    rerun_details = dict(fixture["audit_event_details_json"])
    rerun_details["model"] = "other-model/v9"
    rerun_details["provider"] = "OtherProvider"
    db_session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=SubmissionActorKind.llm,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json=rerun_details,
        )
    )
    db_session.flush()

    second = build_curator_tasks_for_submission(
        db_session, inspection=_inspect(db_session, submission)
    )
    assert second.created_count == 0
    assert second.reused_count == 1
    assert _count_tasks(db_session, submission.id) == 1


# --------------------------------------------------------------------------- #
# API-level golden + public-boundary regression
# --------------------------------------------------------------------------- #


def test_kinetics_warning_via_admin_api_and_public_boundary(
    client, db_session, login_as, _api_admin_user
):
    """The kinetics warning case through the real admin endpoints, plus a
    public-boundary regression: the public TrustFragment still has no
    machine_review and trust.llm_precheck stays frozen."""
    fixture = _load("kinetics_warning_creates_task")
    submission = _seed(db_session, _api_admin_user, fixture)
    db_session.expire(submission)  # let the endpoint reload relationships fresh
    login_as(_api_admin_user)

    base = "/api/v1/admin/machine-review/curator-tasks"
    insp_url = (
        f"/api/v1/admin/submissions/{submission.id}/machine-review-inspection"
    )

    insp = client.get(insp_url).json()
    assert len(insp["record_summaries"]) == 1
    rec = insp["record_summaries"][0]
    assert rec["record_type"] == "kinetics"
    assert rec["latest_summary"]["status"] == "machine_screened_warning"

    build = client.post(f"{base}/build-for-submission/{submission.id}").json()
    assert build["created_count"] == 1
    task = client.get(f"{base}/{build['task_ids'][0]}").json()
    assert task["workflow_state"] == "needs_curator_review"
    assert task["highest_severity"] == "warning"
    assert "machine_review" not in task

    # No record_review row was created for the addressed record.
    assert (
        db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.kinetics,
                RecordReview.record_id == 9001,
            )
        )
        is None
    )

    # Public trust shape unchanged.
    evaluation = EvidenceEvaluation(
        record_type="kinetics",
        record_id=9001,
        rubric="computed_calculation",
        rubric_version=1,
        label=EvidenceBadge.partial,
        passed_checks=("opt_converged",),
        missing_checks=("source_artifact_present",),
        warning_checks=(),
        not_applicable_checks=(),
        passed_count=1,
        possible_count=2,
        evidence_completeness=0.5,
    )
    dumped = build_trust_fragment(evaluation).model_dump(mode="json")
    assert dumped["llm_precheck"] == {"enabled": False, "label": "not_run", "summary": None}
    assert "machine_review" not in dumped
