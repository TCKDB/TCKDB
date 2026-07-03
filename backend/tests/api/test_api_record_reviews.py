"""API tests for the record_review feature.

Covers:

* direct ``/uploads/*`` paths create ``record_review`` rows at
  ``not_reviewed`` and leave the submission tables empty,
* ``/bundles/submit`` creates ``submission`` + ``submission_record_link``
  + ``record_review(under_review)`` rows for linked records,
* approving a submission flips linked records to ``approved``,
* rejecting a submission flips linked records to ``rejected``,
* uploader cannot approve their own submission,
* ``PATCH /record-reviews`` is curator/admin-gated,
* ``PATCH`` enforces the disallowed-transition policy,
* the unique constraint stops duplicate current-state rows.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.record_review import RecordReview
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"


def _hydrogen_conformer_payload(label: str = "conf-record-review") -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
        "note": "review row test",
    }


def _load_bundle(filename: str) -> dict:
    return json.loads((EXAMPLES_DIR / filename).read_text())


# ---------------------------------------------------------------------------
# Direct uploads → reviewable submission, records under_review
# ---------------------------------------------------------------------------


class TestDirectUploadsCreateUnderReviewSubmissions:
    """Every accepted ``/uploads/*`` call creates a submission wrapper, links
    the produced records to it, and initialises their review rows at
    ``under_review`` — the same reviewable semantics as the hosted bundle
    path, differing only by payload shape.
    """

    def test_conformer_upload_creates_submission_and_under_review_rows(
        self, client, db_session
    ):
        before_subs = (
            db_session.scalar(select(func.count()).select_from(Submission)) or 0
        )

        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(label="conf-direct-upload"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        # A submission wrapper is created for the direct upload.
        after_subs = (
            db_session.scalar(select(func.count()).select_from(Submission)) or 0
        )
        assert after_subs == before_subs + 1

        submission_id = body["submission_id"]
        assert submission_id is not None
        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.conformer
        assert submission.source_kind is SubmissionSourceKind.api
        # Entered review, not approved: success != scientific approval.
        assert submission.status is SubmissionStatus.pending

        # Primary record gets an under_review review row linked to the submission.
        observation_id = body["id"]
        review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type
                == SubmissionRecordType.conformer_observation,
                RecordReview.record_id == observation_id,
            )
        )
        assert review is not None
        assert review.status is RecordReviewStatus.under_review
        assert review.submission_id == submission_id
        assert review.reviewed_by is None

        # The observation is linked to the submission.
        link = db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type
                == SubmissionRecordType.conformer_observation,
                SubmissionRecordLink.record_id == observation_id,
            )
        )
        assert link is not None

        # Calculation gets one too — included by Decision 2 in the design.
        calc_id = body["primary_calculation"]["calculation_id"]
        calc_review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.calculation,
                RecordReview.record_id == calc_id,
            )
        )
        assert calc_review is not None
        assert calc_review.status is RecordReviewStatus.under_review
        assert calc_review.submission_id == submission_id

        # Audit trail: submission_created + ingestion_succeeded.
        kinds = {
            e.event_kind
            for e in db_session.scalars(
                select(SubmissionAuditEvent).where(
                    SubmissionAuditEvent.submission_id == submission_id
                )
            ).all()
        }
        assert SubmissionAuditEventKind.submission_created in kinds
        assert SubmissionAuditEventKind.ingestion_succeeded in kinds

    def test_thermo_upload_creates_submission_and_under_review_row(
        self, client, db_session
    ):
        resp = client.post(
            "/api/v1/uploads/thermo",
            json={
                "species_entry": {
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "scientific_origin": "computed",
                "h298_kj_mol": 217.998,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        thermo_id = body["id"]
        submission_id = body["submission_id"]
        assert submission_id is not None

        review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.thermo,
                RecordReview.record_id == thermo_id,
            )
        )
        assert review is not None
        assert review.status is RecordReviewStatus.under_review
        assert review.submission_id == submission_id

        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.thermo

        # The thermo product is linked to the submission.
        link = db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type == SubmissionRecordType.thermo,
                SubmissionRecordLink.record_id == thermo_id,
            )
        )
        assert link is not None

    def test_computed_species_creates_submission_and_under_review_rows(
        self, client, db_session
    ):
        # Minimal valid computed-species bundle with one conformer + opt
        # primary calc.
        bundle = {
            "species_entry": {
                "smiles": "[H]",
                "charge": 0,
                "multiplicity": 2,
            },
            "conformers": [
                {
                    "key": "conf-a",
                    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                    "label": "conf-a",
                    "primary_calculation": {
                        "key": "primary-opt",
                        "type": "opt",
                        "software_release": {
                            "name": "Gaussian",
                            "version": "16",
                        },
                        "level_of_theory": {
                            "method": "B3LYP",
                            "basis": "6-31G(d)",
                        },
                    },
                    "additional_calculations": [],
                }
            ],
        }

        resp = client.post("/api/v1/uploads/computed-species", json=bundle)
        assert resp.status_code == 201, resp.text
        body = resp.json()

        submission_id = body["submission_id"]
        assert submission_id is not None
        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.computed_species

        species_review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.species_entry,
                RecordReview.record_id == body["species_entry_id"],
            )
        )
        assert species_review is not None
        assert species_review.status is RecordReviewStatus.under_review
        assert species_review.submission_id == submission_id

        # The species_entry is linked to the submission.
        link = db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type
                == SubmissionRecordType.species_entry,
                SubmissionRecordLink.record_id == body["species_entry_id"],
            )
        )
        assert link is not None


# ---------------------------------------------------------------------------
# Bundle submit → under_review
# ---------------------------------------------------------------------------


class TestBundleSubmitCreatesUnderReviewRows:
    def test_thermo_bundle_review_rows(self, client, db_session):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201, resp.text
        body = resp.json()

        submission_id = body["submission_id"]

        # Every submission_record_link target has an under_review review row.
        link_pairs = db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id
            )
        ).all()
        assert link_pairs, "bundle submit should create record links"
        for link in link_pairs:
            review = db_session.scalar(
                select(RecordReview).where(
                    RecordReview.record_type == link.record_type,
                    RecordReview.record_id == link.record_id,
                )
            )
            assert review is not None
            assert review.status is RecordReviewStatus.under_review
            assert review.submission_id == submission_id


# ---------------------------------------------------------------------------
# Approve / reject flip linked review rows
# ---------------------------------------------------------------------------


class TestSubmissionApprovalFlipsReviewState:
    def test_approve_flips_to_approved(
        self, client, db_session, login_as, _api_curator_user
    ):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201
        submission_id = resp.json()["submission_id"]

        login_as(_api_curator_user)
        approve_resp = client.post(
            f"/api/v1/submissions/{submission_id}/approve",
            json={"summary": "looks good"},
        )
        assert approve_resp.status_code == 200, approve_resp.text

        rows = db_session.scalars(
            select(RecordReview).where(
                RecordReview.submission_id == submission_id
            )
        ).all()
        assert rows
        for r in rows:
            assert r.status is RecordReviewStatus.approved
            assert r.reviewed_by == _api_curator_user
            assert r.reviewed_at is not None

    def test_reject_flips_to_rejected(
        self, client, db_session, login_as, _api_curator_user
    ):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201
        submission_id = resp.json()["submission_id"]

        login_as(_api_curator_user)
        reject_resp = client.post(
            f"/api/v1/submissions/{submission_id}/reject",
            json={"reason": "scientific issue"},
        )
        assert reject_resp.status_code == 200, reject_resp.text

        rows = db_session.scalars(
            select(RecordReview).where(
                RecordReview.submission_id == submission_id
            )
        ).all()
        assert rows
        for r in rows:
            assert r.status is RecordReviewStatus.rejected
            assert r.reviewed_by == _api_curator_user

    def test_uploader_cannot_approve_own_submission(self, client, db_session):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201
        submission_id = resp.json()["submission_id"]

        # The default test user is the uploader; they're a normal-role
        # user and so are blocked twice over (role and self-approval).
        approve_resp = client.post(
            f"/api/v1/submissions/{submission_id}/approve",
        )
        assert approve_resp.status_code == 403, approve_resp.text


# ---------------------------------------------------------------------------
# /record-reviews API endpoints
# ---------------------------------------------------------------------------


class TestRecordReviewApi:
    def _seed_thermo(self, client) -> int:
        resp = client.post(
            "/api/v1/uploads/thermo",
            json={
                "species_entry": {
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "scientific_origin": "computed",
                "h298_kj_mol": 217.998,
            },
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_get_one(self, client):
        thermo_id = self._seed_thermo(client)
        resp = client.get(f"/api/v1/record-reviews/thermo/{thermo_id}")
        assert resp.status_code == 200
        body = resp.json()
        # Direct uploads now enter review as part of a submission.
        assert body["status"] == "under_review"
        assert body["record_type"] == "thermo"
        assert body["record_id"] == thermo_id

    def test_get_one_404(self, client):
        resp = client.get("/api/v1/record-reviews/thermo/9999999")
        assert resp.status_code == 404

    def test_list_filters_by_status(self, client):
        self._seed_thermo(client)
        resp = client.get(
            "/api/v1/record-reviews",
            params={"status": "under_review", "limit": 50},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert rows
        assert all(r["status"] == "under_review" for r in rows)

    def test_patch_requires_curator(self, client):
        thermo_id = self._seed_thermo(client)
        # Default test user is role=user → 403.
        resp = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "approved"},
        )
        assert resp.status_code == 403

    def test_curator_can_approve(self, client, login_as, _api_curator_user):
        thermo_id = self._seed_thermo(client)
        login_as(_api_curator_user)
        resp = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "approved"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved"
        assert body["reviewed_by"] == _api_curator_user

    def test_disallowed_transition(self, client, login_as, _api_curator_user):
        thermo_id = self._seed_thermo(client)
        login_as(_api_curator_user)
        # First go approved.
        approve = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "approved"},
        )
        assert approve.status_code == 200
        # approved → rejected is disallowed.
        bad = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "rejected"},
        )
        assert bad.status_code == 400
        assert "Disallowed" in bad.json()["detail"]
