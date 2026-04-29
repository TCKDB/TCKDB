"""Tests for ``POST /api/v1/calculations/{calculation_id}/artifacts``.

These tests exercise the calculation-targeted artifact upload endpoint
end-to-end: happy path, batch atomicity, authorization, validation,
idempotency (including cross-calc-id isolation), and pass-2 storage
failure with compensating delete.

S3 writes are stubbed with a per-test fake so the suite does not
require a live MinIO; storage round-trips are covered by
``backend/tests/services/test_artifact_storage.py``.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import (
    can_modify_calculation_artifacts,
    get_current_user,
    get_db,
    get_write_db,
)
from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationArtifact
from app.db.models.common import (
    AppUserRole,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.idempotency import IdempotencyRecord
from app.db.models.submission import Submission, SubmissionRecordLink

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GAUSSIAN_OPT_LOG = FIXTURES / "gaussian" / "opt_g09.log"
GAUSSIAN_FREQ_LOG = FIXTURES / "gaussian" / "freq_g09.log"

KEY_HEADER = "Idempotency-Key"

CONFORMER_PAYLOAD: dict = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    "calculation": {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    },
    "label": "h-conf-art-test",
    "note": "artifact upload test",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _sha_lower(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _output_log_artifact(
    *,
    declare_sha: bool = False,
    declare_bytes: bool = False,
    overrides: dict | None = None,
) -> dict:
    content = GAUSSIAN_OPT_LOG.read_bytes()
    payload: dict = {
        "kind": "output_log",
        "filename": "opt.log",
        "content_base64": _b64(content),
    }
    if declare_sha:
        payload["sha256"] = _sha_lower(content)
    if declare_bytes:
        payload["bytes"] = len(content)
    if overrides:
        payload.update(overrides)
    return payload


def _ancillary_artifact(content: bytes = b"hello-ancillary") -> dict:
    return {
        "kind": "ancillary",
        "filename": "note.txt",
        "content_base64": _b64(content),
    }


def _create_calc_via_conformer_upload(client: TestClient) -> int:
    """Use the public conformer upload to create a calculation row."""
    resp = client.post("/api/v1/uploads/conformers", json=CONFORMER_PAYLOAD)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["primary_calculation"]["calculation_id"]


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[tuple[str, str]]:
    """Replace the S3 ``store_artifact`` call with an in-memory fake.

    Returns a list of ``(uri, sha)`` pairs in the order the storage path
    was invoked, for assertions about how many writes were attempted.
    """
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return written


@pytest.fixture
def stub_delete_artifact(monkeypatch) -> list[str]:
    """Capture compensating delete calls so tests can assert cleanup."""
    deleted: list[str] = []

    def _fake_delete(sha256: str, *, client=None, bucket=None) -> None:
        deleted.append(sha256)

    monkeypatch.setattr(
        "app.services.artifact_persistence.delete_artifact_object", _fake_delete
    )
    return deleted


def _artifact_count_for(db_session: Session, calculation_id: int) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(CalculationArtifact)
        .where(CalculationArtifact.calculation_id == calculation_id)
    ) or 0


# ---------------------------------------------------------------------------
# Multi-user fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def submission_factory(db_session):
    """Build a submission satisfying the DB-level state-machine checks.

    The submission table enforces that rejected/approved rows have the
    appropriate decider columns set. This factory hides the boilerplate
    so authorization tests can focus on the relevant link.
    """

    def _make(*, owner: AppUser, status: SubmissionStatus) -> Submission:
        kwargs: dict = {
            "created_by": owner.id,
            "submission_kind": SubmissionKind.conformer,
            "source_kind": SubmissionSourceKind.api,
            "status": status,
        }
        if status is SubmissionStatus.rejected:
            decider = AppUser(
                username=f"sub-rejecter-{owner.username}",
                role=AppUserRole.curator,
            )
            db_session.add(decider)
            db_session.flush()
            kwargs.update(
                rejection_reason="test rejection",
                rejected_by=decider.id,
            )
        elif status is SubmissionStatus.approved:
            decider = AppUser(
                username=f"sub-approver-{owner.username}",
                role=AppUserRole.curator,
            )
            db_session.add(decider)
            db_session.flush()
            kwargs.update(approved_by=decider.id)

        sub = Submission(**kwargs)
        db_session.add(sub)
        db_session.flush()
        return sub

    return _make


@pytest.fixture
def make_user_client(db_session):
    """Build a TestClient for a freshly-created user with the given role.

    Each call returns ``(client, user)`` and uses the per-test rollback
    session so all rows produced are scoped to the current test.
    """

    def _make(*, username: str, role: AppUserRole = AppUserRole.user):
        user = AppUser(username=username, role=role)
        db_session.add(user)
        db_session.flush()

        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_write_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)
        return client, user

    return _make


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_creator_uploads_one_output_log(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_output_log_artifact(declare_sha=True, declare_bytes=True)]},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["calculation_id"] == calc_id
        assert len(body["artifacts"]) == 1
        a = body["artifacts"][0]
        assert a["kind"] == "output_log"
        assert a["uri"].startswith("s3://")
        assert a["sha256"] is not None and len(a["sha256"]) == 64
        assert a["bytes"] is not None and a["bytes"] > 0
        # Metadata: filename round-trips, created_by is set from the
        # authenticated caller, note defaults to None.
        assert a["filename"] == "opt.log"
        assert a["created_by"] is not None
        assert a["note"] is None
        assert _artifact_count_for(db_session, calc_id) == 1

        # DB row stores the same metadata.
        row = db_session.scalars(
            select(CalculationArtifact).where(
                CalculationArtifact.calculation_id == calc_id
            )
        ).one()
        assert row.filename == "opt.log"
        assert row.created_by == a["created_by"]
        assert row.note is None

        # Symmetry: GET /artifacts surfaces the new row with metadata.
        listed = client.get(f"/api/v1/calculations/{calc_id}/artifacts")
        assert listed.status_code == 200
        listed_body = listed.json()
        assert len(listed_body) == 1
        assert listed_body[0]["filename"] == "opt.log"
        assert listed_body[0]["created_by"] == a["created_by"]
        assert listed_body[0]["note"] is None

    def test_batch_of_multiple_artifacts(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        opt_log = _output_log_artifact()
        opt_log["filename"] = "opt.log"
        freq_content = GAUSSIAN_FREQ_LOG.read_bytes()
        freq_log = {
            "kind": "output_log",
            "filename": "freq.log",
            "content_base64": _b64(freq_content),
        }

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [opt_log, freq_log]},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert [a["kind"] for a in body["artifacts"]] == ["output_log", "output_log"]
        assert _artifact_count_for(db_session, calc_id) == 2


# ---------------------------------------------------------------------------
# Batch atomicity
# ---------------------------------------------------------------------------


class TestBatchAtomicity:
    def test_sha_mismatch_in_batch_rejects_all(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        good = _output_log_artifact(declare_sha=True)
        bad = _output_log_artifact()
        bad["sha256"] = "0" * 64  # wrong but well-formed
        another_good = _output_log_artifact()

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [good, bad, another_good]},
        )
        assert resp.status_code == 422, resp.text
        assert _artifact_count_for(db_session, calc_id) == 0
        # No S3 write attempts because pass-1 short-circuits before pass-2.
        assert stub_store_artifact == []

    def test_invalid_ess_signature_rejects_batch(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        good = _output_log_artifact()
        ancillary = _ancillary_artifact()
        bad_log = {
            "kind": "output_log",
            "filename": "fake.log",
            "content_base64": _b64(b"not a real ESS log\n" * 10),
        }

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [good, ancillary, bad_log]},
        )
        assert resp.status_code == 422
        assert _artifact_count_for(db_session, calc_id) == 0
        assert stub_store_artifact == []

    def test_partial_success_retry_landing_one_per_request(
        self, client, db_session, stub_store_artifact
    ) -> None:
        """Atomicity-respecting partial success.

        A and B in one batch where B is invalid → 422, no rows.
        Resend just A → 201, A lands.
        Resend a fixed B → 201, B lands.
        """
        calc_id = _create_calc_via_conformer_upload(client)
        good = _output_log_artifact()
        bad = _output_log_artifact()
        bad["sha256"] = "0" * 64

        r1 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [good, bad]},
        )
        assert r1.status_code == 422
        assert _artifact_count_for(db_session, calc_id) == 0

        r2 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [good]},
        )
        assert r2.status_code == 201
        assert _artifact_count_for(db_session, calc_id) == 1

        freq_content = GAUSSIAN_FREQ_LOG.read_bytes()
        freq_log = {
            "kind": "output_log",
            "filename": "freq.log",
            "content_base64": _b64(freq_content),
        }
        r3 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [freq_log]},
        )
        assert r3.status_code == 201
        assert _artifact_count_for(db_session, calc_id) == 2


# ---------------------------------------------------------------------------
# Pass-2 storage failure → compensating delete + 503
# ---------------------------------------------------------------------------


class TestStorageFailure:
    def test_storage_failure_triggers_compensation_and_503(
        self, client, db_session, monkeypatch, stub_delete_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)

        # Succeed on the first store, raise on the second so compensation
        # has something to delete.
        call_count = {"n": 0}
        stored: list[str] = []

        def _flaky_store(content: bytes, sha256: str) -> str:
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RuntimeError("simulated S3 outage")
            stored.append(sha256)
            return f"s3://test-bucket/{sha256[:2]}/{sha256}"

        monkeypatch.setattr(
            "app.services.artifact_persistence.store_artifact", _flaky_store
        )

        opt_log = _output_log_artifact()
        freq_content = GAUSSIAN_FREQ_LOG.read_bytes()
        freq_log = {
            "kind": "output_log",
            "filename": "freq.log",
            "content_base64": _b64(freq_content),
        }
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [opt_log, freq_log]},
        )
        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["code"] == "artifact_storage_unavailable"
        # First object was stored, then deleted as compensation.
        assert stored == stub_delete_artifact
        assert _artifact_count_for(db_session, calc_id) == 0

    def test_flush_failure_triggers_compensation(
        self, client, db_session, monkeypatch, stub_store_artifact, stub_delete_artifact
    ) -> None:
        """Regression: a SQL-layer flush failure (e.g. schema drift, FK
        violation, constraint conflict) must trigger the same
        compensation as a storage failure — every already-stored S3
        object is deleted and no rows persist.

        The original SQL exception propagates as itself (it is NOT
        wrapped as ``ArtifactStorageUnavailable`` — that label means
        "object store cannot accept writes," which a flush failure is
        not). The HTTP status the route ultimately returns is the
        default exception handler's choice; the correctness property
        asserted here is the cleanup, not the status code.
        """
        import pytest as _pytest
        from sqlalchemy.exc import IntegrityError

        calc_id = _create_calc_via_conformer_upload(client)

        # Storage succeeds for both artifacts; the *artifact-batch* flush
        # blows up. The patched flush is conditional on pending
        # CalculationArtifact rows so route-side autoflush calls (e.g.
        # the autoflush triggered by `session.get(Calculation, ...)` at
        # the start of the route handler) pass through unmodified.
        opt_log = _output_log_artifact()
        freq_content = GAUSSIAN_FREQ_LOG.read_bytes()
        freq_log = {
            "kind": "output_log",
            "filename": "freq.log",
            "content_base64": _b64(freq_content),
        }

        original_flush = db_session.flush

        def _conditionally_exploding_flush(*args, **kwargs):
            # Only blow up when the batch has actually queued artifact rows.
            pending_artifacts = [
                obj for obj in db_session.new
                if isinstance(obj, CalculationArtifact)
            ]
            if pending_artifacts:
                raise IntegrityError(
                    "simulated flush failure (e.g. schema drift / FK violation)",
                    params=None,
                    orig=Exception("test"),
                )
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(db_session, "flush", _conditionally_exploding_flush)

        # The IntegrityError handler in app/api/errors.py maps SQL
        # IntegrityErrors to a 409 with code=integrity_conflict. The
        # exact status mapping is the existing handler's choice; what
        # this test asserts is that compensation ran, not the status.
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [opt_log, freq_log]},
        )
        assert resp.status_code == 409, resp.text
        assert resp.json().get("code") == "integrity_conflict"

        # Both artifacts were stored to S3 before flush blew up; both
        # were compensated by delete.
        assert len(stub_store_artifact) == 2
        stored_shas = [sha for (_uri, sha) in stub_store_artifact]
        assert sorted(stub_delete_artifact) == sorted(stored_shas)

        # No rows persisted: the conditional patch lets the count query's
        # autoflush pass through (no pending artifact rows after
        # compensation expunged them), so the query succeeds and returns 0.
        assert _artifact_count_for(db_session, calc_id) == 0


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_unauthenticated_returns_401(self, db_session) -> None:
        # A client with no get_current_user override surfaces 401 from
        # the auth dependency (no API key, no cookie).
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_write_db] = lambda: db_session
        anon_client = TestClient(app)
        resp = anon_client.post(
            "/api/v1/calculations/1/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 401

    def test_creator_authorized(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 201

    def test_unrelated_user_forbidden(
        self,
        client,
        db_session,
        stub_store_artifact,
        make_user_client,
    ) -> None:
        # Creator uploads via the standard `client` (testuser).
        calc_id = _create_calc_via_conformer_upload(client)

        # An unrelated user cannot attach artifacts.
        other_client, _ = make_user_client(username="other-art-user")
        resp = other_client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert "not authorized" in body["detail"].lower()
        # 403 detail must not leak internal IDs.
        assert str(calc_id) not in body["detail"]

    def test_curator_override(
        self, client, db_session, stub_store_artifact, make_user_client
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        curator_client, _ = make_user_client(
            username="art-curator", role=AppUserRole.curator
        )
        resp = curator_client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 201

    def test_admin_override(
        self, client, db_session, stub_store_artifact, make_user_client
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        admin_client, _ = make_user_client(
            username="art-admin", role=AppUserRole.admin
        )
        resp = admin_client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 201

    def test_submission_owner_via_pending_link(
        self,
        client,
        db_session,
        stub_store_artifact,
        make_user_client,
        submission_factory,
    ) -> None:
        # `client` (testuser) creates the calc.
        calc_id = _create_calc_via_conformer_upload(client)

        # A different user owns a *pending* submission whose record link
        # points at this calculation. They can attach artifacts.
        other_client, other_user = make_user_client(
            username="art-submission-owner"
        )
        sub = submission_factory(
            owner=other_user, status=SubmissionStatus.pending
        )
        db_session.add(
            SubmissionRecordLink(
                submission_id=sub.id,
                record_type=SubmissionRecordType.calculation,
                record_id=calc_id,
            )
        )
        db_session.flush()

        resp = other_client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 201, resp.text

    def test_submission_owner_only_rejected_links_forbidden(
        self,
        client,
        db_session,
        stub_store_artifact,
        make_user_client,
        submission_factory,
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        other_client, other_user = make_user_client(
            username="art-rejected-owner"
        )
        rejected = submission_factory(
            owner=other_user, status=SubmissionStatus.rejected
        )
        superseded = submission_factory(
            owner=other_user, status=SubmissionStatus.superseded
        )
        db_session.add_all(
            [
                SubmissionRecordLink(
                    submission_id=rejected.id,
                    record_type=SubmissionRecordType.calculation,
                    record_id=calc_id,
                ),
                SubmissionRecordLink(
                    submission_id=superseded.id,
                    record_type=SubmissionRecordType.calculation,
                    record_id=calc_id,
                ),
            ]
        )
        db_session.flush()

        resp = other_client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 403

    def test_submission_owner_with_mixed_links_authorized_via_approved(
        self,
        client,
        db_session,
        stub_store_artifact,
        make_user_client,
        submission_factory,
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        other_client, other_user = make_user_client(
            username="art-mixed-owner"
        )
        rejected = submission_factory(
            owner=other_user, status=SubmissionStatus.rejected
        )
        approved = submission_factory(
            owner=other_user, status=SubmissionStatus.approved
        )
        db_session.add_all(
            [
                SubmissionRecordLink(
                    submission_id=rejected.id,
                    record_type=SubmissionRecordType.calculation,
                    record_id=calc_id,
                ),
                SubmissionRecordLink(
                    submission_id=approved.id,
                    record_type=SubmissionRecordType.calculation,
                    record_id=calc_id,
                ),
            ]
        )
        db_session.flush()

        resp = other_client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 201

    def test_other_user_owning_unrelated_submission_still_forbidden(
        self,
        client,
        db_session,
        stub_store_artifact,
        make_user_client,
        submission_factory,
    ) -> None:
        """A user owning a submission that does NOT link to this calc
        gets no positive evidence and must still be denied."""
        calc_id = _create_calc_via_conformer_upload(client)
        other_client, other_user = make_user_client(
            username="art-unrelated-submission-owner"
        )
        submission_factory(
            owner=other_user, status=SubmissionStatus.approved
        )
        # No SubmissionRecordLink for this calc.

        resp = other_client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 403


class TestAuthorizationHelperUnit:
    """Direct unit tests for ``can_modify_calculation_artifacts``."""

    def _seed_calc(
        self,
        db_session: Session,
        *,
        created_by: int | None,
    ) -> int:
        """Create a real Calculation row, satisfying the one-owner check.

        We attach a freshly-minted SpeciesEntry so the
        ``ck_calculation_one_owner`` invariant holds without going through
        the full conformer workflow.
        """
        import secrets

        from app.db.models.calculation import Calculation
        from app.db.models.common import (
            CalculationType,
            MoleculeKind,
            StereoKind,
        )
        from app.db.models.species import Species, SpeciesEntry

        # Unique inchi_key per call so seeding multiple times in one test
        # does not collide on the species uniqueness constraint.
        inchi_key = secrets.token_hex(13).upper()[:14] + "-" + secrets.token_hex(5).upper()[:10] + "-N"
        species = Species(
            kind=MoleculeKind.molecule,
            smiles=f"[{secrets.token_hex(2)}]",
            inchi_key=inchi_key,
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind.unspecified,
        )
        db_session.add(species)
        db_session.flush()
        entry = SpeciesEntry(species_id=species.id)
        db_session.add(entry)
        db_session.flush()
        calc = Calculation(
            type=CalculationType.sp,
            species_entry_id=entry.id,
            created_by=created_by,
        )
        db_session.add(calc)
        db_session.flush()
        return calc.id

    def _user(
        self, db_session: Session, role: AppUserRole, username: str
    ) -> AppUser:
        u = AppUser(username=username, role=role)
        db_session.add(u)
        db_session.flush()
        return u

    def test_creator_match(self, db_session) -> None:
        from app.db.models.calculation import Calculation

        u = self._user(db_session, AppUserRole.user, "h-creator")
        cid = self._seed_calc(db_session, created_by=u.id)
        calc = db_session.get(Calculation, cid)
        assert can_modify_calculation_artifacts(db_session, calc, u) is True

    def test_unrelated_user_denied(self, db_session) -> None:
        from app.db.models.calculation import Calculation

        creator = self._user(db_session, AppUserRole.user, "h-c2")
        other = self._user(db_session, AppUserRole.user, "h-other2")
        cid = self._seed_calc(db_session, created_by=creator.id)
        calc = db_session.get(Calculation, cid)
        assert can_modify_calculation_artifacts(db_session, calc, other) is False

    def test_curator_override(self, db_session) -> None:
        from app.db.models.calculation import Calculation

        creator = self._user(db_session, AppUserRole.user, "h-c3")
        curator = self._user(db_session, AppUserRole.curator, "h-cur")
        cid = self._seed_calc(db_session, created_by=creator.id)
        calc = db_session.get(Calculation, cid)
        assert can_modify_calculation_artifacts(db_session, calc, curator) is True

    def test_created_by_null_curator_passes_user_denied(self, db_session) -> None:
        from app.db.models.calculation import Calculation

        user = self._user(db_session, AppUserRole.user, "h-nullcalc-user")
        admin = self._user(db_session, AppUserRole.admin, "h-nullcalc-admin")
        cid = self._seed_calc(db_session, created_by=None)
        calc = db_session.get(Calculation, cid)
        assert can_modify_calculation_artifacts(db_session, calc, user) is False
        assert can_modify_calculation_artifacts(db_session, calc, admin) is True

    def test_submission_link_status_filter(
        self, db_session, submission_factory
    ) -> None:
        from app.db.models.calculation import Calculation

        creator = self._user(db_session, AppUserRole.user, "h-sub-creator")
        owner = self._user(db_session, AppUserRole.user, "h-sub-owner")
        cid = self._seed_calc(db_session, created_by=creator.id)
        calc = db_session.get(Calculation, cid)

        # Rejected link only → denied.
        rejected = submission_factory(
            owner=owner, status=SubmissionStatus.rejected
        )
        db_session.add(
            SubmissionRecordLink(
                submission_id=rejected.id,
                record_type=SubmissionRecordType.calculation,
                record_id=cid,
            )
        )
        db_session.flush()
        assert can_modify_calculation_artifacts(db_session, calc, owner) is False

        # Add a precheck_passed link → authorized.
        passed = submission_factory(
            owner=owner, status=SubmissionStatus.precheck_passed
        )
        db_session.add(
            SubmissionRecordLink(
                submission_id=passed.id,
                record_type=SubmissionRecordType.calculation,
                record_id=cid,
            )
        )
        db_session.flush()
        assert can_modify_calculation_artifacts(db_session, calc, owner) is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_kind_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        bad = {
            "kind": "not_a_kind",
            "filename": "x.dat",
            "content_base64": _b64(b"hello"),
        }
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [bad]}
        )
        assert resp.status_code == 422

    def test_bytes_zero_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        bad = _ancillary_artifact()
        bad["bytes"] = 0
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [bad]}
        )
        assert resp.status_code == 422

    def test_uppercase_sha_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        bad = _ancillary_artifact()
        bad["sha256"] = "A" * 64
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [bad]}
        )
        assert resp.status_code == 422

    def test_missing_filename_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        bad = _ancillary_artifact()
        del bad["filename"]
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [bad]}
        )
        assert resp.status_code == 422

    def test_sha_mismatch_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        bad = _output_log_artifact()
        bad["sha256"] = "0" * 64
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [bad]}
        )
        assert resp.status_code == 422

    def test_bytes_mismatch_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        bad = _output_log_artifact()
        bad["bytes"] = 1
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [bad]}
        )
        assert resp.status_code == 422

    def test_output_log_without_signature_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        bad = {
            "kind": "output_log",
            "filename": "fake.log",
            "content_base64": _b64(b"this is not a real ESS log file\n" * 10),
        }
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [bad]}
        )
        assert resp.status_code == 422

    def test_nonexistent_calculation_returns_404(
        self, client, db_session, stub_store_artifact
    ) -> None:
        resp = client.post(
            "/api/v1/calculations/99999999/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert resp.status_code == 404

    def test_empty_batch_rejected(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": []}
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _idempotency_count(db_session: Session) -> int:
    return db_session.scalar(
        select(func.count()).select_from(IdempotencyRecord)
    ) or 0


class TestIdempotency:
    def test_replay_same_key_same_payload(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        payload = {"artifacts": [_ancillary_artifact()]}
        key = "art-idem-replay-aaaaaaaa"
        r1 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json=payload,
            headers={KEY_HEADER: key},
        )
        assert r1.status_code == 201
        before_count = _artifact_count_for(db_session, calc_id)
        before_idem = _idempotency_count(db_session)

        r2 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json=payload,
            headers={KEY_HEADER: key},
        )
        assert r2.status_code == 201
        # No new artifact rows on replay.
        assert _artifact_count_for(db_session, calc_id) == before_count
        # No new idempotency rows on replay.
        assert _idempotency_count(db_session) == before_idem
        # And the response body is the cached one.
        assert r1.json() == r2.json()

    def test_cross_calc_id_isolation(
        self, client, db_session, stub_store_artifact
    ) -> None:
        """Same idempotency key + same body sent to calc A then calc B
        must not replay calc A's response on calc B."""
        # Create two distinct calculations.
        calc_a = _create_calc_via_conformer_upload(client)
        # Second conformer upload — same species but a different
        # observation, producing a fresh calculation row.
        second_payload = dict(CONFORMER_PAYLOAD)
        second_payload["label"] = "h-conf-art-test-2"
        resp = client.post("/api/v1/uploads/conformers", json=second_payload)
        assert resp.status_code == 201
        calc_b = resp.json()["primary_calculation"]["calculation_id"]
        assert calc_a != calc_b

        payload = {"artifacts": [_ancillary_artifact()]}
        key = "art-idem-xcalc-bbbbbbbb"

        r_a = client.post(
            f"/api/v1/calculations/{calc_a}/artifacts",
            json=payload,
            headers={KEY_HEADER: key},
        )
        assert r_a.status_code == 201
        body_a = r_a.json()
        assert body_a["calculation_id"] == calc_a

        r_b = client.post(
            f"/api/v1/calculations/{calc_b}/artifacts",
            json=payload,
            headers={KEY_HEADER: key},
        )
        assert r_b.status_code == 201, r_b.text
        body_b = r_b.json()
        # Calc B got a fresh response targeted at calc B, NOT a replay
        # of calc A's response.
        assert body_b["calculation_id"] == calc_b
        assert _artifact_count_for(db_session, calc_a) == 1
        assert _artifact_count_for(db_session, calc_b) == 1

    def test_same_content_different_key_creates_two_rows(
        self, client, db_session, stub_store_artifact
    ) -> None:
        calc_id = _create_calc_via_conformer_upload(client)
        payload = {"artifacts": [_ancillary_artifact(b"identical-bytes")]}

        r1 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json=payload,
            headers={KEY_HEADER: "art-content-key1-aaaaaaa"},
        )
        r2 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json=payload,
            headers={KEY_HEADER: "art-content-key2-bbbbbbb"},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        # Two rows.
        assert _artifact_count_for(db_session, calc_id) == 2
        # But content-addressed storage dedupes the bytes — both URIs
        # point at the same SHA-derived key.
        assert r1.json()["artifacts"][0]["uri"] == r2.json()["artifacts"][0]["uri"]


# ---------------------------------------------------------------------------
# Conformer upload result shape
# ---------------------------------------------------------------------------


class TestConformerUploadResultShape:
    def test_conformer_response_has_primary_and_additional_refs(
        self, client, db_session, stub_store_artifact
    ) -> None:
        payload = {
            "species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
            "geometry": {"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
            "calculation": {
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            },
            "additional_calculations": [
                {
                    "type": "freq",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                },
                {
                    "type": "sp",
                    "software_release": {"name": "Orca", "version": "5.0"},
                    "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
                },
            ],
            "label": "h2-shape-test",
        }
        resp = client.post("/api/v1/uploads/conformers", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["primary_calculation"]["role"] == "primary"
        assert body["primary_calculation"]["request_index"] is None
        assert body["primary_calculation"]["type"] == "opt"
        assert len(body["additional_calculations"]) == 2
        assert [r["request_index"] for r in body["additional_calculations"]] == [0, 1]
        assert all(
            r["role"] == "additional" for r in body["additional_calculations"]
        )
        # The IDs returned can be used to upload an artifact.
        primary_id = body["primary_calculation"]["calculation_id"]
        art_resp = client.post(
            f"/api/v1/calculations/{primary_id}/artifacts",
            json={"artifacts": [_ancillary_artifact()]},
        )
        assert art_resp.status_code == 201
