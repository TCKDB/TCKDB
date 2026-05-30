"""Tests for the admin-only submission machine-review inspection endpoint.

``GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection`` is a
private/debugging surface: it projects a submission's existing
``llm_precheck_recorded`` audit events onto the records linked to that
submission, reusing the private machine-review stack. It is **admin-only** (the
stricter of the two existing gates) and must never imply that machine review is
public scientific trust — it does not touch the public ``TrustFragment`` or the
scientific read routes, and it mutates nothing.

These follow the existing admin-route testing pattern: the ``client`` fixture's
default actor is role=user (so it exercises the 403 path), ``login_as`` swaps
roles, and a fresh app without the auth override exercises the anonymous 401
path (mirroring ``test_api_legacy_route_auth.py``).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.db.models.submission import Submission, SubmissionAuditEvent
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

_BASE = "/api/v1/admin/submissions"


def _url(submission_id: int) -> str:
    return f"{_BASE}/{submission_id}/machine-review-inspection"


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


def _new_submission(db_session: Session, user_id: int) -> Submission:
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title="Admin MR inspection",
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
) -> SubmissionAuditEvent:
    """Append a realistic machine-review (llm_precheck_recorded / llm) event."""
    result = LLMPrecheckResult(
        label=label,
        summary="advisory",
        findings=findings,
        model="fake_test/simple-v1",
        used_rag=False,
    )
    event = record_llm_precheck_audit_event(
        db_session,
        submission=submission,
        result=result,
        provider="FakeLLMPrecheckProvider",
    )
    db_session.flush()
    return event


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


@pytest.fixture
def anon_client(db_session: Session):
    """A client with DB overrides but no auth override -> real auth runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def test_admin_submission_machine_review_inspection_requires_auth(
    anon_client, db_session, _api_test_user
):
    """Anonymous callers are rejected with 401 before any inspection runs."""
    submission = _new_submission(db_session, _api_test_user)

    resp = anon_client.get(_url(submission.id))

    assert resp.status_code == 401, resp.text


def test_admin_submission_machine_review_inspection_requires_admin(
    client, db_session, login_as, _api_test_user, _api_curator_user
):
    """Normal users and curators are forbidden; the gate is admin-only."""
    submission = _new_submission(db_session, _api_test_user)

    # Default actor is role=user.
    assert client.get(_url(submission.id)).status_code == 403

    # Curators are also forbidden — this debugging surface is admin-only.
    login_as(_api_curator_user)
    assert client.get(_url(submission.id)).status_code == 403


# --------------------------------------------------------------------------- #
# Lookup / empty
# --------------------------------------------------------------------------- #


def test_admin_submission_machine_review_inspection_404_for_missing_submission(
    client, login_as, _api_admin_user
):
    """A missing submission yields 404, with no internal id leaked."""
    login_as(_api_admin_user)

    resp = client.get(_url(999_999))

    assert resp.status_code == 404, resp.text


def test_admin_submission_machine_review_inspection_empty_when_no_machine_review_events(
    client, db_session, login_as, _api_admin_user
):
    """A submission with no machine-review events returns an empty inspection."""
    submission = _new_submission(db_session, _api_admin_user)
    login_as(_api_admin_user)

    resp = client.get(_url(submission.id))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "submission_id": submission.id,
        "record_summaries": [],
        "unmapped_findings_count": 0,
        "mapping_warnings": [],
        "parse_warnings": [],
        "source_audit_event_ids": [],
    }


def test_admin_submission_machine_review_inspection_ignores_non_machine_review_events(
    client, db_session, login_as, _api_admin_user
):
    """The submission_created (user) event is present but is not a source."""
    submission = _new_submission(db_session, _api_admin_user)
    # Sanity: the submission already carries a non-MR audit event.
    assert any(
        e.event_kind is SubmissionAuditEventKind.submission_created
        and e.actor_kind is SubmissionActorKind.user
        for e in submission.audit_events
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert body["record_summaries"] == []
    assert body["source_audit_event_ids"] == []


# --------------------------------------------------------------------------- #
# Mapping behavior
# --------------------------------------------------------------------------- #


def test_admin_submission_machine_review_inspection_maps_record_finding(
    client, db_session, login_as, _api_admin_user
):
    """A finding naming a linked record becomes that record's latest summary."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    event = _record_mr_event(
        db_session,
        submission,
        findings=(_record_finding(record_id=9001, severity=LLMFindingSeverity.warning),),
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert len(body["record_summaries"]) == 1
    record = body["record_summaries"][0]
    assert record["record_type"] == "calculation"
    assert record["record_ref"] == "9001"
    assert record["record_id"] == 9001
    assert record["latest_summary"]["status"] == "machine_screened_warning"
    assert record["all_record_reviews_count"] == 1
    assert body["source_audit_event_ids"] == [event.id]
    assert body["unmapped_findings_count"] == 0


def test_admin_submission_machine_review_inspection_does_not_fan_out_submission_scoped_finding(
    client, db_session, login_as, _api_admin_user
):
    """A submission-scoped finding never becomes any linked record's summary."""
    submission = _new_submission(db_session, _api_admin_user)
    for rid in (9001, 9002):
        link_record(
            db_session,
            submission=submission,
            record_type=SubmissionRecordType.calculation,
            record_id=rid,
            role=f"r{rid}",
        )
    _record_mr_event(
        db_session,
        submission,
        label=LLMPrecheckLabel.needs_attention,
        findings=(_record_finding(record_id=None, record_type=None,
                                  severity=LLMFindingSeverity.critical),),
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    # No fan-out onto either linked record; the finding is a diagnostic only.
    assert body["record_summaries"] == []
    assert body["unmapped_findings_count"] == 1


def test_admin_submission_machine_review_inspection_preserves_mapping_warnings(
    client, db_session, login_as, _api_admin_user
):
    """A finding naming an unlinked record is kept as a mapping warning."""
    submission = _new_submission(db_session, _api_admin_user)
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
        findings=(_record_finding(record_id=7777),),  # 7777 is not linked
    )
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert body["record_summaries"] == []
    assert body["unmapped_findings_count"] == 1
    assert len(body["mapping_warnings"]) == 1
    assert "not linked" in body["mapping_warnings"][0]


def test_admin_submission_machine_review_inspection_preserves_parse_warnings(
    client, db_session, login_as, _api_admin_user
):
    """A malformed machine-review event degrades to a preserved parse warning."""
    submission = _new_submission(db_session, _api_admin_user)
    # A raw, malformed llm_precheck_recorded event (label not in the enum).
    db_session.add(
        SubmissionAuditEvent(
            submission_id=submission.id,
            actor_kind=SubmissionActorKind.llm,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            details_json={"label": "not-a-real-label", "findings": []},
        )
    )
    db_session.flush()
    login_as(_api_admin_user)

    body = client.get(_url(submission.id)).json()

    assert body["record_summaries"] == []
    assert len(body["parse_warnings"]) == 1


# --------------------------------------------------------------------------- #
# Boundary: no mutation, public trust shape unchanged
# --------------------------------------------------------------------------- #


def test_admin_submission_machine_review_inspection_does_not_mutate_submission(
    client, db_session, login_as, _api_admin_user
):
    """Inspecting a submission leaves its lifecycle/moderation fields intact."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    _record_mr_event(
        db_session, submission, findings=(_record_finding(record_id=9001),)
    )
    before = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    login_as(_api_admin_user)

    assert client.get(_url(submission.id)).status_code == 200

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


def test_admin_submission_machine_review_inspection_does_not_change_public_trust_shape(
    client, db_session, login_as, _api_admin_user
):
    """The public TrustFragment still has the frozen precheck shape and no machine_review."""
    submission = _new_submission(db_session, _api_admin_user)
    link_record(
        db_session,
        submission=submission,
        record_type=SubmissionRecordType.calculation,
        record_id=9001,
        role="primary",
    )
    _record_mr_event(
        db_session, submission, findings=(_record_finding(record_id=9001),)
    )
    login_as(_api_admin_user)

    # Exercising the admin endpoint must not perturb the public trust shape.
    assert client.get(_url(submission.id)).status_code == 200

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
