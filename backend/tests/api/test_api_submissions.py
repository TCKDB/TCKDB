"""End-to-end API tests for ``/api/v1/submissions/*``.

Exercises the full moderated lifecycle exposed by the new submissions
router:

* listing (mine, for-review)
* read-one + audit-events + record-links visibility
* approve / reject permission and state transitions
* supersede asserting an existing replacement link

Also pins the contract that ``/uploads/*`` direct ingestion does NOT
populate any submission table — the moderated lifecycle stays scoped to
``/bundles/*`` and ``/submissions/*``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from app.api.config import Settings
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionStatus,
)
from app.db.models.kinetics import Kinetics
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.thermo import Thermo
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.llm_precheck.service import run_llm_precheck_for_submission
from app.services.submission import (
    append_audit_event,
    create_submission,
    link_record,
    record_llm_precheck_audit_event,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_submission(
    db_session,
    *,
    created_by: int,
    kind: SubmissionKind = SubmissionKind.thermo,
    title: str = "test submission",
    supersedes_submission_id: int | None = None,
) -> Submission:
    sub = create_submission(
        db_session,
        created_by=created_by,
        submission_kind=kind,
        title=title,
        supersedes_submission_id=supersedes_submission_id,
    )
    db_session.flush()
    return sub


def _load_bundle(filename: str) -> dict:
    return json.loads((EXAMPLES_DIR / filename).read_text())


def _scientific_record_counts(db_session) -> dict[str, int]:
    return {
        "calculation": db_session.scalar(select(func.count()).select_from(Calculation))
        or 0,
        "kinetics": db_session.scalar(select(func.count()).select_from(Kinetics)) or 0,
        "thermo": db_session.scalar(select(func.count()).select_from(Thermo)) or 0,
    }


# ---------------------------------------------------------------------------
# GET /submissions/mine
# ---------------------------------------------------------------------------


class TestListMine:
    def test_returns_empty_when_user_has_none(self, client):
        resp = client.get("/api/v1/submissions/mine")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_only_my_submissions(
        self, client, db_session, _api_test_user, _api_other_user
    ):
        mine_a = _seed_submission(db_session, created_by=_api_test_user, title="a")
        mine_b = _seed_submission(db_session, created_by=_api_test_user, title="b")
        _seed_submission(db_session, created_by=_api_other_user, title="other")

        resp = client.get("/api/v1/submissions/mine")
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json()]
        assert set(ids) == {mine_a.id, mine_b.id}

    def test_status_filter(self, client, db_session, _api_test_user):
        sub = _seed_submission(db_session, created_by=_api_test_user)

        resp = client.get(
            "/api/v1/submissions/mine",
            params={"statuses": [SubmissionStatus.approved.value]},
        )
        assert resp.status_code == 200
        assert resp.json() == []

        resp = client.get(
            "/api/v1/submissions/mine",
            params={"statuses": [SubmissionStatus.pending.value]},
        )
        assert resp.status_code == 200
        assert [r["id"] for r in resp.json()] == [sub.id]


# ---------------------------------------------------------------------------
# GET /submissions/for-review
# ---------------------------------------------------------------------------


class TestListForReview:
    def test_user_role_gets_403(self, client, db_session, _api_test_user):
        _seed_submission(db_session, created_by=_api_test_user)
        resp = client.get("/api/v1/submissions/for-review")
        assert resp.status_code == 403

    def test_curator_sees_pending(
        self, client, db_session, _api_test_user, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        login_as(_api_curator_user)

        resp = client.get("/api/v1/submissions/for-review")
        assert resp.status_code == 200
        ids = [r["id"] for r in resp.json()]
        assert sub.id in ids


# ---------------------------------------------------------------------------
# GET /submissions/{id}
# ---------------------------------------------------------------------------


class TestReadById:
    def test_creator_can_read_own(self, client, db_session, _api_test_user):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        resp = client.get(f"/api/v1/submissions/{sub.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == sub.id

    def test_other_user_gets_403(
        self, client, db_session, _api_other_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_other_user)
        # Default client identity is _api_test_user (different from the creator).
        resp = client.get(f"/api/v1/submissions/{sub.id}")
        assert resp.status_code == 403

    def test_curator_can_read_any(
        self, client, db_session, _api_test_user, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        login_as(_api_curator_user)
        resp = client.get(f"/api/v1/submissions/{sub.id}")
        assert resp.status_code == 200

    def test_admin_can_read_any(
        self, client, db_session, _api_test_user, _api_admin_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        login_as(_api_admin_user)
        resp = client.get(f"/api/v1/submissions/{sub.id}")
        assert resp.status_code == 200

    def test_unknown_id_returns_404(self, client):
        resp = client.get("/api/v1/submissions/999999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /submissions/{id}/approve
# ---------------------------------------------------------------------------


class TestApprove:
    def test_user_role_gets_403(self, client, db_session, _api_test_user):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        resp = client.post(f"/api/v1/submissions/{sub.id}/approve")
        assert resp.status_code == 403

    def test_curator_can_approve(
        self, client, db_session, _api_test_user, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        login_as(_api_curator_user)

        resp = client.post(
            f"/api/v1/submissions/{sub.id}/approve",
            json={"summary": "looks good"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == SubmissionStatus.approved.value
        assert resp.json()["approved_by"] == _api_curator_user

        # Audit event appended
        events = client.get(f"/api/v1/submissions/{sub.id}/audit-events").json()
        kinds = [e["event_kind"] for e in events]
        assert SubmissionAuditEventKind.curator_approved.value in kinds

    def test_curator_who_is_creator_gets_400(
        self, client, db_session, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_curator_user)
        login_as(_api_curator_user)

        resp = client.post(f"/api/v1/submissions/{sub.id}/approve")
        assert resp.status_code == 400
        assert "self" in resp.text.lower() or "uploader" in resp.text.lower()

    def test_double_approve_rejected(
        self, client, db_session, _api_test_user, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        login_as(_api_curator_user)

        first = client.post(f"/api/v1/submissions/{sub.id}/approve")
        assert first.status_code == 200
        second = client.post(f"/api/v1/submissions/{sub.id}/approve")
        assert second.status_code == 400


# ---------------------------------------------------------------------------
# POST /submissions/{id}/reject
# ---------------------------------------------------------------------------


class TestReject:
    def test_user_role_gets_403(self, client, db_session, _api_test_user):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        resp = client.post(
            f"/api/v1/submissions/{sub.id}/reject", json={"reason": "no"}
        )
        assert resp.status_code == 403

    def test_curator_can_reject_with_reason(
        self, client, db_session, _api_test_user, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        login_as(_api_curator_user)

        resp = client.post(
            f"/api/v1/submissions/{sub.id}/reject",
            json={"reason": "missing freq"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == SubmissionStatus.rejected.value
        assert body["rejection_reason"] == "missing freq"
        assert body["rejected_by"] == _api_curator_user

        events = client.get(f"/api/v1/submissions/{sub.id}/audit-events").json()
        kinds = [e["event_kind"] for e in events]
        assert SubmissionAuditEventKind.curator_rejected.value in kinds

    def test_missing_reason_returns_422(
        self, client, db_session, _api_test_user, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        login_as(_api_curator_user)

        resp = client.post(f"/api/v1/submissions/{sub.id}/reject", json={})
        assert resp.status_code == 422

        resp_empty = client.post(
            f"/api/v1/submissions/{sub.id}/reject", json={"reason": ""}
        )
        assert resp_empty.status_code == 422

    def test_curator_who_is_creator_gets_400(
        self, client, db_session, _api_curator_user, login_as
    ):
        sub = _seed_submission(db_session, created_by=_api_curator_user)
        login_as(_api_curator_user)

        resp = client.post(
            f"/api/v1/submissions/{sub.id}/reject", json={"reason": "self"}
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /submissions/{id}/supersede
# ---------------------------------------------------------------------------


class TestSupersede:
    def test_supersede_marks_old_and_appends_audit(
        self, client, db_session, _api_test_user
    ):
        old = _seed_submission(db_session, created_by=_api_test_user, title="old")
        new = _seed_submission(
            db_session,
            created_by=_api_test_user,
            title="new",
            supersedes_submission_id=old.id,
        )

        resp = client.post(
            f"/api/v1/submissions/{old.id}/supersede",
            json={"new_submission_id": new.id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == SubmissionStatus.superseded.value

        events = client.get(f"/api/v1/submissions/{old.id}/audit-events").json()
        kinds = [e["event_kind"] for e in events]
        assert SubmissionAuditEventKind.submission_superseded.value in kinds

    def test_self_supersede_returns_400(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        resp = client.post(
            f"/api/v1/submissions/{sub.id}/supersede",
            json={"new_submission_id": sub.id},
        )
        assert resp.status_code == 400

    def test_new_without_supersedes_link_returns_400(
        self, client, db_session, _api_test_user
    ):
        old = _seed_submission(db_session, created_by=_api_test_user, title="old")
        new = _seed_submission(
            db_session, created_by=_api_test_user, title="new"
        )  # no supersedes_submission_id

        resp = client.post(
            f"/api/v1/submissions/{old.id}/supersede",
            json={"new_submission_id": new.id},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Audit events / record links visibility
# ---------------------------------------------------------------------------


class TestAuditEventsVisibility:
    def test_creator_sees_own_audit(self, client, db_session, _api_test_user):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        resp = client.get(f"/api/v1/submissions/{sub.id}/audit-events")
        assert resp.status_code == 200
        # submission_created event from create_submission
        kinds = [e["event_kind"] for e in resp.json()]
        assert SubmissionAuditEventKind.submission_created.value in kinds

    def test_other_user_gets_403(
        self, client, db_session, _api_other_user
    ):
        sub = _seed_submission(db_session, created_by=_api_other_user)
        resp = client.get(f"/api/v1/submissions/{sub.id}/audit-events")
        assert resp.status_code == 403

    def test_llm_precheck_audit_event_round_trips_for_creator(
        self, client, db_session, _api_test_user, _api_other_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        link_record(
            db_session,
            submission=sub,
            record_type=SubmissionRecordType.calculation,
            record_id=321,
            role="primary",
        )
        baseline_status = sub.status
        baseline_counts = _scientific_record_counts(db_session)

        run_llm_precheck_for_submission(
            db_session,
            sub.id,
            settings_obj=Settings(ai_review_assistant_mode="test"),
        )
        db_session.refresh(sub)

        assert sub.status is baseline_status
        assert _scientific_record_counts(db_session) == baseline_counts

        resp = client.get(f"/api/v1/submissions/{sub.id}/audit-events")
        assert resp.status_code == 200
        events = resp.json()

        created = next(
            e
            for e in events
            if e["event_kind"] == SubmissionAuditEventKind.submission_created.value
        )
        assert created["actor_kind"] == "user"
        assert created["details_json"] is None

        precheck = next(
            e
            for e in events
            if e["event_kind"] == SubmissionAuditEventKind.llm_precheck_recorded.value
        )
        assert precheck["actor_kind"] == SubmissionActorKind.llm.value
        assert precheck["summary"] == "Fake precheck inspected 1 linked record(s)."
        assert precheck["from_status"] is None
        assert precheck["to_status"] is None
        assert precheck["details_json"] == {
            "label": "pass",
            "summary": "Fake precheck inspected 1 linked record(s).",
            "findings": [],
            "model": "fake_test/simple-v1",
            "used_rag": False,
            "provider": "FakeLLMPrecheckProvider",
            "mode": "test",
        }

        other_sub = _seed_submission(db_session, created_by=_api_other_user)
        run_llm_precheck_for_submission(
            db_session,
            other_sub.id,
            settings_obj=Settings(ai_review_assistant_mode="test"),
        )
        resp = client.get(f"/api/v1/submissions/{other_sub.id}/audit-events")
        assert resp.status_code == 403

    def test_llm_precheck_audit_details_include_structured_findings(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        result = LLMPrecheckResult(
            label=LLMPrecheckLabel.warning,
            summary="Configured warning",
            findings=(
                LLMFinding(
                    severity=LLMFindingSeverity.warning,
                    category=LLMFindingCategory.provenance,
                    record_type="calculation",
                    record_id=42,
                    message="Missing source artifact summary.",
                    evidence_keys=("missing_checks.source_artifact_present",),
                ),
            ),
            model="fake/test",
            used_rag=False,
        )

        record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=result,
            provider="fake_test",
            mode="test",
        )

        resp = client.get(f"/api/v1/submissions/{sub.id}/audit-events")
        assert resp.status_code == 200
        precheck = next(
            e
            for e in resp.json()
            if e["event_kind"] == SubmissionAuditEventKind.llm_precheck_recorded.value
        )
        assert precheck["actor_kind"] == "llm"
        assert precheck["details_json"] == {
            "label": "warning",
            "summary": "Configured warning",
            "findings": [
                {
                    "severity": "warning",
                    "category": "provenance",
                    "record_type": "calculation",
                    "record_id": 42,
                    "message": "Missing source artifact summary.",
                    "evidence_keys": ["missing_checks.source_artifact_present"],
                }
            ],
            "model": "fake/test",
            "used_rag": False,
            "provider": "fake_test",
            "mode": "test",
        }


class TestAIReviewSummary:
    def test_latest_card_chooses_newest_llm_precheck_event(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        older = LLMPrecheckResult(
            label=LLMPrecheckLabel.warning,
            summary="Older warning",
            model="fake/old",
            used_rag=False,
        )
        newer = LLMPrecheckResult(
            label=LLMPrecheckLabel.needs_attention,
            summary="Newer needs attention",
            model="fake/new",
            used_rag=True,
        )
        old_event = record_llm_precheck_audit_event(
            db_session, submission=sub, result=older
        )
        new_event = record_llm_precheck_audit_event(
            db_session, submission=sub, result=newer
        )
        old_event.created_at = datetime(2026, 1, 1, 12, 0, 0)
        new_event.created_at = datetime(2026, 1, 2, 12, 0, 0)
        db_session.flush()

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")

        assert resp.status_code == 200
        assert resp.json() == {
            "label": "needs_attention",
            "summary": "Newer needs attention",
            "model": "fake/new",
            "used_rag": True,
            "created_at": "2026-01-02T12:00:00",
            "finding_counts": {"info": 0, "warning": 0, "critical": 0},
        }

    def test_latest_card_uses_primary_key_tie_breaker(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        first = record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=LLMPrecheckResult(
                label=LLMPrecheckLabel.warning,
                summary="Lower id",
                model="fake/low",
            ),
        )
        second = record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=LLMPrecheckResult(
                label=LLMPrecheckLabel.warning,
                summary="Higher id",
                model="fake/high",
            ),
        )
        same_created_at = datetime(2026, 1, 3, 12, 0, 0)
        first.created_at = same_created_at
        second.created_at = same_created_at
        db_session.flush()

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")

        assert resp.status_code == 200
        assert second.id > first.id
        assert resp.json()["summary"] == "Higher id"
        assert resp.json()["model"] == "fake/high"

    def test_finding_counts_are_deterministic(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=LLMPrecheckResult(
                label=LLMPrecheckLabel.warning,
                summary="Mixed severities",
                findings=(
                    LLMFinding(
                        severity=LLMFindingSeverity.warning,
                        category=LLMFindingCategory.provenance,
                        message="Missing source evidence.",
                    ),
                    LLMFinding(
                        severity=LLMFindingSeverity.info,
                        category=LLMFindingCategory.consistency,
                        message="Review note.",
                    ),
                    LLMFinding(
                        severity=LLMFindingSeverity.warning,
                        category=LLMFindingCategory.units,
                        message="Check units.",
                    ),
                    LLMFinding(
                        severity=LLMFindingSeverity.critical,
                        category=LLMFindingCategory.geometry,
                        message="Invalid geometry.",
                    ),
                ),
                model="fake/test",
            ),
        )

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")

        assert resp.status_code == 200
        assert resp.json()["finding_counts"] == {
            "info": 1,
            "warning": 2,
            "critical": 1,
        }

    def test_missing_findings_behaves_like_empty_list(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        append_audit_event(
            db_session,
            submission=sub,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            actor_kind=SubmissionActorKind.llm,
            summary="No findings key",
            details_json={
                "label": "warning",
                "summary": "No findings key",
                "model": "fake/test",
                "used_rag": False,
                "future_field": {"ignored": True},
            },
        )

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")

        assert resp.status_code == 200
        assert resp.json()["finding_counts"] == {
            "info": 0,
            "warning": 0,
            "critical": 0,
        }

    def test_summary_falls_back_to_audit_event_summary(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        append_audit_event(
            db_session,
            submission=sub,
            event_kind=SubmissionAuditEventKind.llm_precheck_recorded,
            actor_kind=SubmissionActorKind.llm,
            summary="Fallback summary",
            details_json={
                "label": "warning",
                "findings": [],
                "model": "fake/test",
                "used_rag": False,
            },
        )

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")

        assert resp.status_code == 200
        assert resp.json()["summary"] == "Fallback summary"

    def test_no_llm_precheck_event_returns_null(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")

        assert resp.status_code == 200
        assert resp.json() is None

    def test_visibility_follows_submission_policy(
        self,
        client,
        db_session,
        _api_test_user,
        _api_other_user,
        _api_curator_user,
        login_as,
    ):
        sub = _seed_submission(db_session, created_by=_api_other_user)
        record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=LLMPrecheckResult(
                label=LLMPrecheckLabel.warning,
                summary="Private summary",
            ),
        )

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")
        assert resp.status_code == 403

        login_as(_api_other_user)
        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")
        assert resp.status_code == 200
        assert resp.json()["summary"] == "Private summary"

        login_as(_api_curator_user)
        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")
        assert resp.status_code == 200
        assert resp.json()["summary"] == "Private summary"

    def test_audit_events_endpoint_still_exposes_full_details_json(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=LLMPrecheckResult(
                label=LLMPrecheckLabel.warning,
                summary="Full detail summary",
                findings=(
                    LLMFinding(
                        severity=LLMFindingSeverity.warning,
                        category=LLMFindingCategory.provenance,
                        message="Missing source artifact summary.",
                    ),
                ),
                model="fake/test",
                used_rag=False,
            ),
            provider="fake_test",
            mode="test",
        )

        resp = client.get(f"/api/v1/submissions/{sub.id}/audit-events")

        assert resp.status_code == 200
        precheck = next(
            e
            for e in resp.json()
            if e["event_kind"] == SubmissionAuditEventKind.llm_precheck_recorded.value
        )
        assert precheck["details_json"]["findings"] == [
            {
                "severity": "warning",
                "category": "provenance",
                "record_type": None,
                "record_id": None,
                "message": "Missing source artifact summary.",
                "evidence_keys": [],
            }
        ]
        assert precheck["details_json"]["provider"] == "fake_test"
        assert precheck["details_json"]["mode"] == "test"

    def test_failed_to_review_is_advisory_not_upload_failure(
        self, client, db_session, _api_test_user
    ):
        sub = _seed_submission(db_session, created_by=_api_test_user)
        baseline_status = sub.status
        baseline_counts = _scientific_record_counts(db_session)
        record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=LLMPrecheckResult(
                label=LLMPrecheckLabel.failed_to_review,
                summary="AI Review Assistant could not complete review.",
                model="fake/test",
            ),
            error_kind="provider_error",
        )
        db_session.refresh(sub)

        resp = client.get(f"/api/v1/submissions/{sub.id}/ai-review-summary")

        assert resp.status_code == 200
        assert resp.json()["label"] == "failed_to_review"
        assert sub.status is baseline_status
        assert _scientific_record_counts(db_session) == baseline_counts

    def test_public_scientific_reads_remain_unchanged(
        self, client, db_session, _api_test_user
    ):
        species = make_species(
            db_session, smiles="CCO", inchi_key=next_inchi_key("AISUM")
        )
        entry = make_species_entry(db_session, species)
        calc = make_calculation(
            db_session,
            type=CalculationType.opt,
            species_entry_id=entry.id,
        )
        sub = _seed_submission(
            db_session,
            created_by=_api_test_user,
            kind=SubmissionKind.other,
        )
        link_record(
            db_session,
            submission=sub,
            record_type=SubmissionRecordType.calculation,
            record_id=calc.id,
            role="primary",
        )
        record_llm_precheck_audit_event(
            db_session,
            submission=sub,
            result=LLMPrecheckResult(
                label=LLMPrecheckLabel.warning,
                summary="Submission advisory warning.",
                model="fake/test",
            ),
        )

        resp = client.get(
            f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
        )

        assert resp.status_code == 200
        trust = resp.json()["record"]["trust"]
        assert trust["llm_precheck"] == {
            "enabled": False,
            "label": "not_run",
            "summary": None,
        }
        evidence = trust["evidence"]
        assert "passed_checks" in evidence
        assert "missing_checks" in evidence
        assert "warning_checks" in evidence
        assert "not_applicable_checks" in evidence


# ---------------------------------------------------------------------------
# Bundle ingest creates submission + record links
# ---------------------------------------------------------------------------


class TestBundleSubmissionWiring:
    def test_thermo_bundle_creates_submission_audit_and_links(
        self, client, db_session
    ):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201, resp.text
        submission_id = resp.json()["submission_id"]

        # Submission row
        sub_resp = client.get(f"/api/v1/submissions/{submission_id}")
        assert sub_resp.status_code == 200

        # Audit trail: at minimum submission_created + ingestion_succeeded
        events = client.get(
            f"/api/v1/submissions/{submission_id}/audit-events"
        ).json()
        kinds = [e["event_kind"] for e in events]
        assert SubmissionAuditEventKind.submission_created.value in kinds
        assert SubmissionAuditEventKind.ingestion_succeeded.value in kinds

        # Record links: at least one for the imported thermo row
        links = client.get(
            f"/api/v1/submissions/{submission_id}/record-links"
        ).json()
        assert len(links) >= 1
        link_types = {link["record_type"] for link in links}
        assert "thermo" in link_types


# ---------------------------------------------------------------------------
# Direct uploads must not touch submission tables
# ---------------------------------------------------------------------------


_CONFORMER_PAYLOAD: dict = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    "calculation": {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    },
}


class TestDirectUploadsCreateSubmission:
    """Direct ``/uploads/*`` calls are reviewable contributions: each creates
    a submission, an audit trail, and record links — the same wrapper the
    hosted bundle path produces, differing only by payload shape.
    """

    def test_conformer_upload_creates_submission(self, client, db_session):
        resp = client.post("/api/v1/uploads/conformers", json=_CONFORMER_PAYLOAD)
        assert resp.status_code == 201
        submission_id = resp.json()["submission_id"]
        assert submission_id is not None

        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.conformer

        # Audit events and record links exist for the upload event.
        for model in (SubmissionAuditEvent, SubmissionRecordLink):
            count = (
                db_session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.submission_id == submission_id)
                )
                or 0
            )
            assert count > 0, f"{model.__tablename__} unexpectedly empty"
