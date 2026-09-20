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
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import MachineReviewCuratorTaskState as _STATE
from app.db.models.common import MachineReviewSeverity as DBSeverity
from app.db.models.common import MachineReviewStatus as DBStatus
from app.db.models.common import SubmissionKind, SubmissionRecordType
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.network import Network
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
from app.services.trust.models import (
    EvidenceBadge,
    EvidenceEvaluation,
    EvidenceOutcome,
)
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_energy_correction_scheme,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

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
    record_type: SubmissionRecordType = SubmissionRecordType.kinetics,
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
        record_type=record_type,
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
    """404 *and* the code, because the status alone proved nothing.

    This asserted ``status_code == 404`` only, which passes against a
    handler that 404s on everything -- and it did pass while ``GET``
    answered ``code="http_404"`` and every write route answered
    ``curator_task_not_found`` for the identical condition.
    """
    login_as(_api_admin_user)
    resp = client.get(f"{_BASE}/999999")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "curator_task_not_found", body
    # DR-0028 Req. 2: the looked-up row id goes to the operator's log, never
    # into the body. 999999 is the caller's own path parameter.
    assert body["context"] == {}, body


def test_every_curator_task_route_names_a_missing_task_the_same_way(
    client, db_session, login_as, _api_admin_user
):
    """One condition, one code, on all five routes that can meet it.

    ``GET`` used to answer ``http_404`` because ``api/routes/admin.py``
    kept a private ``_get_curator_task_or_404`` raising its own
    ``HTTPException`` rather than calling the service helper the four
    write routes call. An admin UI that branches on
    ``curator_task_not_found`` to drop a vanished task from its queue got
    the branch on assign and not on read.

    Asserting the *set* rather than five separate equalities is the
    point: a sixth route added later that reaches for the bare
    ``HTTPException`` again fails here, and the failure names it.
    """
    login_as(_api_admin_user)
    missing = 999999
    requests = {
        "GET": lambda: client.get(f"{_BASE}/{missing}"),
        "assign": lambda: client.post(
            f"{_BASE}/{missing}/assign", json={"assignee_id": None}
        ),
        "start-review": lambda: client.post(f"{_BASE}/{missing}/start-review", json={}),
        "resolve": lambda: client.post(
            f"{_BASE}/{missing}/resolve",
            json={"resolution_state": "resolved_no_action", "resolution_note": "x"},
        ),
        "reopen": lambda: client.post(f"{_BASE}/{missing}/reopen", json={}),
    }
    answers = {}
    for name, send in requests.items():
        resp = send()
        answers[name] = (resp.status_code, resp.json().get("code"))
    assert set(answers.values()) == {(404, "curator_task_not_found")}, answers

    # The accept-half. Without it the assertion above is satisfied by a
    # route that 404s on every id, including ids that exist.
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(db_session, submission.id)
    found = client.get(f"{_BASE}/{task.id}")
    assert found.status_code == 200, found.text
    assert found.json()["id"] == task.id
    assigned = client.post(
        f"{_BASE}/{task.id}/assign", json={"assignee_id": _api_admin_user}
    )
    assert assigned.status_code == 200, assigned.text


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
        checks={
            "opt_converged": EvidenceOutcome.passed,
            "source_artifact_present": EvidenceOutcome.missing,
        },
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


# --------------------------------------------------------------------------- #
# record_public_ref -- getting a curator from a task to the record
# --------------------------------------------------------------------------- #
#
# The task row stores ``record_id``, an internal database id. No read route
# answers to it, so on its own it takes a curator nowhere: the identifier the
# archive is addressed by is ``public_ref``. These tests pin the route's job of
# resolving one to the other, and the two ways it is allowed to answer "I
# cannot name this record".


def test_get_task_carries_the_records_public_ref(
    client, db_session, login_as, _api_admin_user
):
    species = make_species(db_session)
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.species,
        record_id=species.id,
    )
    login_as(_api_admin_user)

    body = client.get(f"{_BASE}/{task.id}").json()

    # Equality with the row's own ref, not a shape check: a route that returned
    # some other species' ref would satisfy `startswith("spc_")` perfectly.
    assert body["record_public_ref"] == species.public_ref
    assert body["record_id"] == species.id


def test_list_resolves_each_task_against_its_own_record_type(
    client, db_session, login_as, _api_admin_user
):
    """Two tasks, two record types, two tables.

    The list route resolves a page in one query per record type. Ids are
    per-table sequences, so the mistake this catches -- answering from the
    wrong table -- is not hypothetical: a species and a network routinely
    share an id.
    """
    species = make_species(db_session)
    network = Network(name="curator-task-list-ref", description="fixture")
    db_session.add(network)
    db_session.flush()

    submission = _new_submission(db_session, _api_admin_user)
    species_task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.species,
        record_id=species.id,
        fingerprint="b" * 64,
    )
    network_task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.network,
        record_id=network.id,
        fingerprint="c" * 64,
    )
    login_as(_api_admin_user)

    items = client.get(f"{_BASE}?submission_id={submission.id}").json()["items"]
    by_id = {item["id"]: item for item in items}

    assert by_id[species_task.id]["record_public_ref"] == species.public_ref
    assert by_id[network_task.id]["record_public_ref"] == network.public_ref


def test_a_task_whose_record_is_gone_reports_null_not_an_error(
    client, db_session, login_as, _api_admin_user
):
    """Tasks outlive the records that raised them, and that is not a 500.

    ``machine_review_curator_task.record_id`` is a plain column, not a foreign
    key, so nothing stops a record being removed while its task stays open. The
    curator still needs to see the task -- and to be told plainly that it can
    no longer be followed to a record.
    """
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.species,
        record_id=9_000_000_002,
    )
    login_as(_api_admin_user)

    resp = client.get(f"{_BASE}/{task.id}")

    assert resp.status_code == 200
    assert resp.json()["record_public_ref"] is None


def test_the_write_routes_carry_the_ref_too(
    client, db_session, login_as, _api_admin_user
):
    """Assign / start-review / resolve / reopen answer with the same schema.

    A UI that renders the response of an action it just took would otherwise
    lose the link on every click, and only the read routes would look right.
    """
    species = make_species(db_session)
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.species,
        record_id=species.id,
    )
    login_as(_api_admin_user)

    assigned = client.post(
        f"{_BASE}/{task.id}/assign", json={"assignee_id": _api_admin_user}
    )
    assert assigned.status_code == 200
    assert assigned.json()["record_public_ref"] == species.public_ref

    started = client.post(f"{_BASE}/{task.id}/start-review", json={})
    assert started.status_code == 200
    assert started.json()["record_public_ref"] == species.public_ref

    resolved = client.post(
        f"{_BASE}/{task.id}/resolve",
        json={
            "resolution_state": "dismissed_machine_finding",
            "resolution_note": "Checked; the record is fine.",
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["record_public_ref"] == species.public_ref

    reopened = client.post(f"{_BASE}/{task.id}/reopen", json={})
    assert reopened.status_code == 200
    assert reopened.json()["record_public_ref"] == species.public_ref


# --------------------------------------------------------------------------- #
# Containers (task #267)
# --------------------------------------------------------------------------- #
#
# Six of the eighteen ``SubmissionRecordType`` members name a table with no
# page of its own -- ``thermo`` is one. Before this slice a task against a
# thermo row carried nothing that let a client find it: ``record_public_ref``
# addresses no route (thermo has no page), and the response had no
# ``container_type`` / ``container_ref`` field at all. That is the same
# defect PR #488 fixed on ``/api/v1/record-reviews``; these tests pin its
# repair on this route, resolved by the same
# ``app.services.record_containers`` this route now reuses rather than a
# second mapping.


def test_get_task_carries_a_container_for_a_containerless_record_type(
    client, db_session, login_as, _api_admin_user
):
    """Reproduces the curator-queue defect: a thermo task named nowhere to go.

    ``thermo`` has its own ``public_ref`` (``thm_...``), but no page of its
    own -- the frontend has no route for it (``recordRoute`` in
    ``domain/recordRoute.ts`` does not know ``thermo``), because it is
    rendered only as a tab on its species entry. Before the fix this
    assertion fails with a ``KeyError``: the response body carries no
    ``container_type`` / ``container_ref`` keys at all, so a client had no
    way to route to the one page that actually shows this record.
    """
    entry = make_species_entry(db_session, make_species(db_session))
    thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-120.0)
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
    )
    login_as(_api_admin_user)

    body = client.get(f"{_BASE}/{task.id}").json()

    assert body["record_public_ref"] == thermo.public_ref
    # Located via the species entry it is a tab on.
    assert body["container_type"] == "species_entry"
    assert body["container_ref"] == entry.public_ref


def test_a_correction_with_no_ref_of_its_own_still_names_its_container(
    client, db_session, login_as, _api_admin_user
):
    """``applied_energy_correction`` has no ``public_ref`` column at all.

    Both null causes stay distinguishable: the record's own ref is null
    (it has none, not a lookup failure), while the container still resolves.
    """
    entry = make_species_entry(db_session, make_species(db_session))
    correction = make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=make_energy_correction_scheme(db_session, name="curator_task_container"),
    )
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.applied_energy_correction,
        record_id=correction.id,
    )
    login_as(_api_admin_user)

    body = client.get(f"{_BASE}/{task.id}").json()

    assert body["record_public_ref"] is None
    assert body["container_type"] == "species_entry"
    assert body["container_ref"] == entry.public_ref


def test_a_root_record_type_has_no_container(
    client, db_session, login_as, _api_admin_user
):
    """``species`` is a root: no owning parent, and that is a normal answer."""
    species = make_species(db_session)
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.species,
        record_id=species.id,
    )
    login_as(_api_admin_user)

    body = client.get(f"{_BASE}/{task.id}").json()

    assert body["record_public_ref"] == species.public_ref
    assert body["container_type"] is None
    assert body["container_ref"] is None


def test_list_gives_each_task_its_own_container(
    client, db_session, login_as, _api_admin_user
):
    """Two thermo tasks on two different species entries, on one page.

    A mapper that resolved one container and reused it for the page, or that
    paired containers to rows positionally, would pass a check that only
    asserted non-null -- and would send a curator to the wrong record.
    """
    first_entry = make_species_entry(db_session, make_species(db_session))
    first_thermo = make_thermo_scalar(
        db_session, species_entry=first_entry, h298_kj_mol=-10.0
    )
    second_entry = make_species_entry(db_session, make_species(db_session))
    second_thermo = make_thermo_scalar(
        db_session, species_entry=second_entry, h298_kj_mol=-20.0
    )
    submission = _new_submission(db_session, _api_admin_user)
    first_task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.thermo,
        record_id=first_thermo.id,
        fingerprint="d" * 64,
    )
    second_task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.thermo,
        record_id=second_thermo.id,
        fingerprint="e" * 64,
    )
    login_as(_api_admin_user)

    items = client.get(f"{_BASE}?submission_id={submission.id}").json()["items"]
    by_id = {item["id"]: item for item in items}

    assert by_id[first_task.id]["container_ref"] == first_entry.public_ref
    assert by_id[second_task.id]["container_ref"] == second_entry.public_ref
    assert (
        by_id[first_task.id]["container_ref"]
        != by_id[second_task.id]["container_ref"]
    )


def test_the_write_routes_carry_the_container_too(
    client, db_session, login_as, _api_admin_user
):
    """Assign / start-review / resolve / reopen answer with the container too."""
    entry = make_species_entry(db_session, make_species(db_session))
    thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-30.0)
    submission = _new_submission(db_session, _api_admin_user)
    task = _make_task(
        db_session,
        submission.id,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
    )
    login_as(_api_admin_user)

    assigned = client.post(
        f"{_BASE}/{task.id}/assign", json={"assignee_id": _api_admin_user}
    )
    assert assigned.json()["container_ref"] == entry.public_ref

    started = client.post(f"{_BASE}/{task.id}/start-review", json={})
    assert started.json()["container_ref"] == entry.public_ref

    resolved = client.post(
        f"{_BASE}/{task.id}/resolve",
        json={
            "resolution_state": "dismissed_machine_finding",
            "resolution_note": "Checked; the record is fine.",
        },
    )
    assert resolved.json()["container_ref"] == entry.public_ref

    reopened = client.post(f"{_BASE}/{task.id}/reopen", json={})
    assert reopened.json()["container_ref"] == entry.public_ref


def test_a_longer_page_of_distinct_containers_does_not_cost_more_queries(
    client, db_session, login_as, _api_admin_user
):
    """The bulk container resolve, measured at the route, on DISTINCT parents.

    Every row in this page sits on its OWN species entry -- not one shared
    parent. A page whose rows all share a parent cannot tell a grouped
    resolver from a per-parent loop, because with one distinct container
    they cost the same (this is the pin #488 got wrong first time; see
    ``app/services/record_containers.py``). So the fixture asserts the
    parents really are distinct before measuring, and the query count must
    not move between a 2-row and a 10-row page.
    """
    submission = _new_submission(db_session, _api_admin_user)

    def _seed(n: int, start_fingerprint: int) -> list[int]:
        task_ids = []
        for i in range(n):
            entry = make_species_entry(db_session, make_species(db_session))
            thermo = make_thermo_scalar(
                db_session, species_entry=entry, h298_kj_mol=-1.0 - i
            )
            task = _make_task(
                db_session,
                submission.id,
                record_type=SubmissionRecordType.thermo,
                record_id=thermo.id,
                fingerprint=f"{start_fingerprint + i:064d}",
            )
            task_ids.append(task.id)
        return task_ids

    def _count() -> int:
        statements = 0
        engine = db_session.connection().engine

        def _before(conn, cursor, statement, parameters, context, executemany):
            nonlocal statements
            statements += 1

        event.listen(engine, "before_cursor_execute", _before)
        try:
            resp = client.get(f"{_BASE}?submission_id={submission.id}&limit=200")
            assert resp.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _before)
        return resp, statements

    login_as(_api_admin_user)

    _seed(2, 100)
    two_resp, two_queries = _count()
    two_containers = {
        item["container_ref"] for item in two_resp.json()["items"]
    }
    # The parents really are distinct -- otherwise this test degrades into a
    # single-shared-parent test and silently stops measuring anything.
    assert len(two_containers) == 2, "fixture failed to build distinct parents"

    _seed(8, 200)
    ten_resp, ten_queries = _count()
    ten_containers = {item["container_ref"] for item in ten_resp.json()["items"]}
    assert len(ten_containers) == 10, "fixture failed to build distinct parents"

    assert ten_queries == two_queries, (
        f"the page cost {two_queries} queries for 2 rows on 2 distinct "
        f"parents and {ten_queries} for 10 rows on 10 distinct parents -- "
        "the container resolve is running per row or per parent"
    )
