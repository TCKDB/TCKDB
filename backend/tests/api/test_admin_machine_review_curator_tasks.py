"""Tests for the admin-only machine-review curator task queue API.

Routes under ``/api/v1/admin/machine-review/curator-tasks`` are an admin
workflow surface over ``machine_review_curator_task``: list, get, explicit
build-for-submission, assign, start-review, resolve, reopen. They are
**admin-only** (curators get 403 in this slice) and must never expose public
``trust.machine_review`` or mutate ``submission.status`` / ``RecordReviewStatus``
/ scientific state.

Follows the existing admin-route testing pattern (mirroring
``test_admin_machine_review_inspection.py``): the ``client`` fixture's default
actor is role=user (the 403 path), ``login_as`` swaps roles, and ``anon_client``
exercises the anonymous 401 path.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import MachineReviewCuratorTaskState as _STATE
from app.db.models.common import MachineReviewSeverity as DBSeverity
from app.db.models.common import MachineReviewStatus as DBStatus
from app.db.models.common import SubmissionKind, SubmissionRecordType
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.record_review import RecordReview
from app.db.models.submission import Submission
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.submission import (
    create_submission,
    link_record,
    record_llm_precheck_audit_event,
)
from app.services.trust import build_trust_fragment
from app.services.trust.models import EvidenceBadge, EvidenceEvaluation

_BASE = "/api/v1/admin/machine-review/curator-tasks"


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
        submission_kind=SubmissionKind.thermo,
        title="curator task queue",
        summary="compact",
    )
    db_session.flush()
    return submission


def _record_finding(
    *,
    record_id: int | None,
    record_type: str | None = "calculation",
    severity: LLMFindingSeverity = LLMFindingSeverity.warning,
) -> LLMFinding:
    return LLMFinding(
        severity=severity,
        category=LLMFindingCategory.provenance,
        record_type=record_type,
        record_id=record_id,
        message="Missing source artifact summary.",
        evidence_keys=("missing_checks.source_artifact_present",),
    )


def _record_mr_event(
    db_session: Session,
    submission: Submission,
    *,
    findings: tuple[LLMFinding, ...] = (),
    label: LLMPrecheckLabel = LLMPrecheckLabel.warning,
) -> None:
    result = LLMPrecheckResult(
        label=label,
        summary="advisory",
        findings=findings,
        model="fake_test/simple-v1",
        used_rag=False,
    )
    record_llm_precheck_audit_event(
        db_session,
        submission=submission,
        result=result,
        provider="FakeLLMPrecheckProvider",
    )
    db_session.flush()


def _seed_submission_with_warning(db_session: Session, user_id: int) -> Submission:
    """A submission with one linked record and a mapped warning finding."""
    submission = _new_submission(db_session, user_id)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    _record_mr_event(
        db_session,
        submission,
        findings=(_record_finding(record_id=9001, severity=LLMFindingSeverity.warning),),
    )
    return submission


def _make_task(
    db_session: Session,
    submission_id: int,
    *,
    workflow_state: _STATE = _STATE.needs_curator_review,
    assigned_to: int | None = None,
    record_id: int = 101,
    fingerprint: str = "a" * 64,
    highest_severity: DBSeverity = DBSeverity.warning,
    resolved_by: int | None = None,
    resolution_note: str | None = None,
) -> MachineReviewCuratorTask:
    """Insert one task directly. Terminal states auto-fill the resolution triple."""
    is_terminal = workflow_state in _STATE.terminal_states()
    resolved_at = datetime(2026, 5, 31, 9, 0, 0) if is_terminal else None
    if is_terminal:
        resolution_note = resolution_note or "seed terminal note"
        assert resolved_by is not None, "terminal seed needs resolved_by"
    task = MachineReviewCuratorTask(
        submission_id=submission_id,
        record_type=SubmissionRecordType.kinetics,
        record_id=record_id,
        finding_fingerprint=fingerprint,
        workflow_state=workflow_state,
        machine_review_status=DBStatus.machine_screened_warning,
        highest_severity=highest_severity,
        assigned_to=assigned_to,
        resolved_by=resolved_by,
        resolved_at=resolved_at,
        resolution_note=resolution_note,
    )
    db_session.add(task)
    db_session.flush()
    return task


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


def test_list_tasks_requires_admin(client, login_as, _api_test_user, _api_curator_user):
    assert client.get(_BASE).status_code == 403  # default actor: role=user
    login_as(_api_curator_user)
    assert client.get(_BASE).status_code == 403


def test_get_task_requires_admin(client):
    assert client.get(f"{_BASE}/1").status_code == 403


def test_build_tasks_for_submission_requires_admin(client):
    assert client.post(f"{_BASE}/build-for-submission/1").status_code == 403


def test_curator_gets_403(client, login_as, _api_curator_user):
    login_as(_api_curator_user)
    assert client.get(_BASE).status_code == 403
    assert client.post(f"{_BASE}/1/assign", json={"assignee_id": None}).status_code == 403


def test_normal_user_gets_403(client):
    # The default client actor is a normal user.
    assert client.get(_BASE).status_code == 403


def test_anonymous_gets_401(anon_client):
    assert anon_client.get(_BASE).status_code == 401
    assert anon_client.post(f"{_BASE}/1/assign", json={"assignee_id": None}).status_code == 401


# --------------------------------------------------------------------------- #
# List / get
# --------------------------------------------------------------------------- #


def test_list_tasks_empty(client, login_as, _api_admin_user):
    login_as(_api_admin_user)
    body = client.get(_BASE).json()
    assert body["items"] == []
    assert body["total"] == 0


def test_get_task_returns_task(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id)
    login_as(_api_admin_user)
    resp = client.get(f"{_BASE}/{task.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == task.id
    assert body["workflow_state"] == "needs_curator_review"
    assert body["highest_severity"] == "warning"
    assert "machine_review" not in body


def test_get_task_404(client, login_as, _api_admin_user):
    login_as(_api_admin_user)
    assert client.get(f"{_BASE}/999999").status_code == 404


def test_list_tasks_filters_by_workflow_state(
    client, db_session, login_as, _api_admin_user
):
    submission = _new_submission(db_session, _api_admin_user)
    _make_task(db_session, submission.id, workflow_state=_STATE.needs_curator_review,
               fingerprint="a" * 64)
    _make_task(db_session, submission.id, workflow_state=_STATE.in_curator_review,
               fingerprint="b" * 64)
    login_as(_api_admin_user)

    body = client.get(_BASE, params={"workflow_state": "in_curator_review"}).json()
    assert body["total"] == 1
    assert body["items"][0]["workflow_state"] == "in_curator_review"


def test_list_tasks_filters_by_assigned_to(
    client, db_session, login_as, _api_admin_user, _api_other_user
):
    submission = _new_submission(db_session, _api_admin_user)
    _make_task(db_session, submission.id, assigned_to=_api_admin_user, fingerprint="a" * 64)
    _make_task(db_session, submission.id, assigned_to=None, fingerprint="b" * 64)
    login_as(_api_admin_user)

    body = client.get(_BASE, params={"assigned_to": _api_admin_user}).json()
    assert body["total"] == 1
    assert body["items"][0]["assigned_to"] == _api_admin_user


# --------------------------------------------------------------------------- #
# Build for submission
# --------------------------------------------------------------------------- #


def test_build_tasks_for_submission_creates_warning_task(
    client, db_session, login_as, _api_admin_user
):
    submission = _seed_submission_with_warning(db_session, _api_admin_user)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/build-for-submission/{submission.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_count"] == 1
    assert len(body["task_ids"]) == 1

    task = client.get(f"{_BASE}/{body['task_ids'][0]}").json()
    assert task["record_type"] == "calculation"
    assert task["record_id"] == 9001
    assert task["workflow_state"] == "needs_curator_review"
    assert task["highest_severity"] == "warning"


def test_build_tasks_for_submission_is_idempotent(
    client, db_session, login_as, _api_admin_user
):
    submission = _seed_submission_with_warning(db_session, _api_admin_user)
    login_as(_api_admin_user)

    first = client.post(f"{_BASE}/build-for-submission/{submission.id}").json()
    second = client.post(f"{_BASE}/build-for-submission/{submission.id}").json()
    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert second["reused_count"] == 1


def test_build_tasks_for_submission_404(client, login_as, _api_admin_user):
    login_as(_api_admin_user)
    assert client.post(f"{_BASE}/build-for-submission/999999").status_code == 404


def test_build_tasks_for_submission_does_not_mutate_submission_status(
    client, db_session, login_as, _api_admin_user
):
    submission = _seed_submission_with_warning(db_session, _api_admin_user)
    status_before = submission.status
    login_as(_api_admin_user)

    assert client.post(f"{_BASE}/build-for-submission/{submission.id}").status_code == 200
    db_session.refresh(submission)
    assert submission.status is status_before


# --------------------------------------------------------------------------- #
# Assign
# --------------------------------------------------------------------------- #


def test_assign_task_sets_assignee(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{task.id}/assign", json={"assignee_id": _api_admin_user})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == _api_admin_user


def test_assign_task_null_unassigns(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id, assigned_to=_api_admin_user)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{task.id}/assign", json={"assignee_id": None})
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] is None


# --------------------------------------------------------------------------- #
# Start review
# --------------------------------------------------------------------------- #


def test_start_review_moves_to_in_review(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id, workflow_state=_STATE.needs_curator_review)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{task.id}/start-review")
    assert resp.status_code == 200
    assert resp.json()["workflow_state"] == "in_curator_review"


def test_start_review_uses_authenticated_admin_as_actor(
    client, db_session, login_as, _api_admin_user
):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id, workflow_state=_STATE.untriaged,
                      assigned_to=None)
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{task.id}/start-review")
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == _api_admin_user  # auto-assigned to admin


# --------------------------------------------------------------------------- #
# Resolve
# --------------------------------------------------------------------------- #


def test_resolve_task_requires_note(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id)
    login_as(_api_admin_user)

    resp = client.post(
        f"{_BASE}/{task.id}/resolve",
        json={"resolution_state": "resolved_no_action", "resolution_note": "   "},
    )
    assert resp.status_code == 400


def test_resolve_task_sets_terminal_state(client, db_session, login_as, _api_admin_user):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id)
    login_as(_api_admin_user)

    resp = client.post(
        f"{_BASE}/{task.id}/resolve",
        json={
            "resolution_state": "resolved_no_action",
            "resolution_note": "Checked record; no action needed.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_state"] == "resolved_no_action"
    assert body["resolved_by"] == _api_admin_user
    assert body["resolved_at"] is not None
    assert body["resolution_note"] == "Checked record; no action needed."


def test_resolve_human_reviewed_does_not_change_record_review_status(
    client, db_session, login_as, _api_admin_user
):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id, record_id=101)
    login_as(_api_admin_user)

    resp = client.post(
        f"{_BASE}/{task.id}/resolve",
        json={
            "resolution_state": "resolved_human_reviewed",
            "resolution_note": "Approved through the human-review layer.",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["workflow_state"] == "resolved_human_reviewed"

    review = db_session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == SubmissionRecordType.kinetics,
            RecordReview.record_id == 101,
        )
    )
    assert review is None


# --------------------------------------------------------------------------- #
# Reopen
# --------------------------------------------------------------------------- #


def test_reopen_task_clears_resolution_fields(
    client, db_session, login_as, _api_admin_user
):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=_STATE.dismissed_machine_finding,
        resolved_by=_api_admin_user,
    )
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{task.id}/reopen",
                       json={"target_state": "needs_curator_review"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_state"] == "needs_curator_review"
    assert body["resolved_at"] is None
    assert body["resolved_by"] is None
    assert body["resolution_note"] is None


def test_reopen_task_preserves_assignment_by_default(
    client, db_session, login_as, _api_admin_user
):
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        workflow_state=_STATE.resolved_no_action,
        assigned_to=_api_admin_user,
        resolved_by=_api_admin_user,
    )
    login_as(_api_admin_user)

    resp = client.post(f"{_BASE}/{task.id}/reopen")
    assert resp.status_code == 200
    assert resp.json()["assigned_to"] == _api_admin_user


# --------------------------------------------------------------------------- #
# Public-boundary regression
# --------------------------------------------------------------------------- #


def test_curator_task_api_does_not_change_public_trust_shape(
    client, db_session, login_as, _api_admin_user
):
    """Exercising the admin curator-task API must not perturb the public
    TrustFragment: still no machine_review, precheck frozen at not_run."""
    submission = _seed_submission_with_warning(db_session, _api_admin_user)
    login_as(_api_admin_user)

    build = client.post(f"{_BASE}/build-for-submission/{submission.id}").json()
    task_id = build["task_ids"][0]
    client.post(
        f"{_BASE}/{task_id}/resolve",
        json={"resolution_state": "dismissed_machine_finding",
              "resolution_note": "FP."},
    )

    evaluation = EvidenceEvaluation(
        record_type="calculation",
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
    assert set(dumped) == {
        "review_status",
        "trust_status",
        "evidence",
        "llm_precheck",
        "is_certified",
    }
