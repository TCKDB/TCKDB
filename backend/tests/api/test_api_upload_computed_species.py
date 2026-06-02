"""API-layer tests for ``POST /api/v1/uploads/computed-species``.

Exercises the route end-to-end: happy path, idempotency, authorization,
artifact validation/storage failure, cross-bundle conformer-group reuse.
S3 writes are stubbed via the same fixtures used by the calculation
artifact endpoint tests.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.calculation import CalculationArtifact

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GAUSSIAN_OPT_LOG = FIXTURES / "gaussian" / "opt_g09.log"
GAUSSIAN_FREQ_LOG = FIXTURES / "gaussian" / "freq_g09.log"

KEY_HEADER = "Idempotency-Key"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[tuple[str, str]]:
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
    deleted: list[str] = []

    def _fake_delete(sha256: str, *, client=None, bucket=None) -> None:
        deleted.append(sha256)

    monkeypatch.setattr(
        "app.services.artifact_persistence.delete_artifact_object", _fake_delete
    )
    return deleted


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _opt_log_artifact() -> dict:
    content = GAUSSIAN_OPT_LOG.read_bytes()
    return {
        "kind": "output_log",
        "filename": "opt.log",
        "content_base64": _b64(content),
    }


def _freq_log_artifact() -> dict:
    content = GAUSSIAN_FREQ_LOG.read_bytes()
    return {
        "kind": "output_log",
        "filename": "freq.log",
        "content_base64": _b64(content),
    }


def _calc(key: str, *, calc_type: str = "opt", **overrides) -> dict:
    base: dict = {
        "key": key,
        "type": calc_type,
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        "software_release": {"name": "Gaussian", "version": "16"},
    }
    if calc_type == "opt":
        base.setdefault("opt_result", {"converged": True})
    elif calc_type == "freq":
        base.setdefault("freq_result", {"n_imag": 0})
    elif calc_type == "sp":
        base.setdefault("sp_result", {"electronic_energy_hartree": -76.4})
    base.update(overrides)
    return base


_H_GEOM = {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"}


def _hydrogen_bundle_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": dict(_H_GEOM),
                "primary_calculation": _calc("opt0", calc_type="opt"),
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_minimal_bundle_201(self, client):
        resp = client.post(
            "/api/v1/uploads/computed-species",
            json=_hydrogen_bundle_payload(),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["type"] == "computed_species"
        assert "species_entry_id" in body
        assert len(body["conformers"]) == 1
        conf = body["conformers"][0]
        assert conf["key"] == "c0"
        assert conf["primary_calculation"]["key"] == "opt0"
        assert conf["primary_calculation"]["role"] == "primary"
        assert conf["primary_calculation"]["calculation_id"] is not None
        assert body["thermo"] is None

    def test_full_bundle_with_thermo_and_artifacts(
        self, client, db_session, stub_store_artifact
    ):
        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["primary_calculation"]["artifacts"] = [
            _opt_log_artifact()
        ]
        payload["conformers"][0]["additional_calculations"] = [
            _calc("freq0", calc_type="freq", artifacts=[_freq_log_artifact()]),
            _calc("sp0", calc_type="sp"),
        ]
        payload["thermo"] = {
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"calculation_key": "sp0", "role": "sp"},
                {"calculation_key": "freq0", "role": "freq"},
                {"calculation_key": "opt0", "role": "opt"},
            ],
        }
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["thermo"] is not None
        assert body["thermo"]["thermo_id"] is not None
        # Two artifacts stored.
        assert len(stub_store_artifact) == 2
        # All three calc rows present.
        assert len(body["conformers"][0]["additional_calculations"]) == 2


# ---------------------------------------------------------------------------
# Validation rejections (Pydantic-time)
# ---------------------------------------------------------------------------


class TestValidationRejections:
    def test_undeclared_dependency_key_returns_422(self, client):
        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["additional_calculations"] = [
            _calc(
                "freq0",
                calc_type="freq",
                depends_on=[
                    {"parent_calculation_key": "ghost", "role": "freq_on"}
                ],
            )
        ]
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422

    def test_undeclared_thermo_source_key_returns_422(self, client):
        payload = _hydrogen_bundle_payload()
        payload["thermo"] = {
            "h298_kj_mol": 1.0,
            "source_calculations": [
                {"calculation_key": "ghost", "role": "sp"},
            ],
        }
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422

    def test_duplicate_conformer_keys_returns_422(self, client):
        payload = _hydrogen_bundle_payload()
        payload["conformers"].append(dict(payload["conformers"][0]))
        # Both have key "c0"; primary calc keys also collide.
        payload["conformers"][1]["primary_calculation"] = _calc("opt1")
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422

    def test_duplicate_calc_keys_returns_422(self, client):
        payload = _hydrogen_bundle_payload()
        payload["conformers"].append(
            {
                "key": "c1",
                "geometry": dict(_H_GEOM),
                "primary_calculation": _calc("opt0"),
            }
        )
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422

    def test_freq_calc_with_opt_result_returns_422(self, client):
        payload = _hydrogen_bundle_payload()
        bad = _calc("freq0", calc_type="freq")
        bad["opt_result"] = {"converged": True}
        bad.pop("freq_result", None)
        payload["conformers"][0]["additional_calculations"] = [bad]
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422

    def test_existing_calculation_id_in_parameters_json_rejected(self, client):
        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["primary_calculation"]["parameters_json"] = {
            "tckdb_origin": {"existing_calculation_id": 99}
        }
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422
        assert "existing_calculation_id" in resp.text

    def test_empty_conformers_returns_422(self, client):
        payload = _hydrogen_bundle_payload()
        payload["conformers"] = []
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Workflow-time role/type compat
# ---------------------------------------------------------------------------


class TestRoleTypeCompatibility:
    def test_thermo_role_opt_pointing_at_freq_returns_422(self, client):
        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["additional_calculations"] = [
            _calc("freq0", calc_type="freq")
        ]
        payload["thermo"] = {
            "h298_kj_mol": 1.0,
            "source_calculations": [
                {"calculation_key": "freq0", "role": "opt"},
            ],
        }
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422
        assert "incompatible" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Artifact handling
# ---------------------------------------------------------------------------


class TestArtifactHandling:
    def test_invalid_artifact_blocks_all_writes(
        self, client, db_session, stub_store_artifact
    ):
        bad_log = {
            "kind": "output_log",
            "filename": "fake.log",
            "content_base64": _b64(b"not real ESS log\n" * 10),
        }
        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["primary_calculation"]["artifacts"] = [
            _opt_log_artifact(),
            bad_log,
        ]
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 422
        # No DB writes.
        assert (
            db_session.scalar(
                select(func.count()).select_from(CalculationArtifact)
            )
            == 0
        )
        # No S3 writes (pass 1 short-circuits before any storage call).
        assert stub_store_artifact == []

    def test_storage_failure_compensates_cross_calc_writes(
        self, client, db_session, monkeypatch, stub_delete_artifact
    ):
        """Calc A's artifact succeeds; calc B's artifact fails on storage.
        Bundle compensation deletes A's S3 object before raising."""
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

        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["primary_calculation"]["artifacts"] = [
            _opt_log_artifact()
        ]
        payload["conformers"][0]["additional_calculations"] = [
            _calc("freq0", calc_type="freq", artifacts=[_freq_log_artifact()]),
        ]
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 503
        # The first stored sha was compensated cross-batch — the load-
        # bearing assertion of bundle-level S3 cleanup. SQL rollback is
        # the route's get_write_db responsibility (verified by primitive
        # endpoint tests); the test client's override does not commit on
        # exception, so we only assert S3 compensation here.
        assert sorted(stored) == sorted(stub_delete_artifact)
        assert len(stored) >= 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_key_same_body_replays(
        self, client, db_session, stub_store_artifact
    ):
        """Replay returns the cached body; the second request must NOT
        produce any duplicate DB rows or S3 writes (DR-0024 contract).

        Includes one artifact so the S3-write-count assertion is
        meaningful — without artifacts a successful replay would write
        zero objects regardless.
        """
        from app.db.models.calculation import Calculation, CalculationArtifact
        from app.db.models.species import (
            ConformerGroup,
            ConformerObservation,
            SpeciesEntry,
        )

        def _counts() -> dict[str, int]:
            return {
                "species_entry": db_session.scalar(
                    select(func.count()).select_from(SpeciesEntry)
                ),
                "conformer_group": db_session.scalar(
                    select(func.count()).select_from(ConformerGroup)
                ),
                "conformer_observation": db_session.scalar(
                    select(func.count()).select_from(ConformerObservation)
                ),
                "calculation": db_session.scalar(
                    select(func.count()).select_from(Calculation)
                ),
                "calculation_artifact": db_session.scalar(
                    select(func.count()).select_from(CalculationArtifact)
                ),
            }

        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["primary_calculation"]["artifacts"] = [
            _opt_log_artifact()
        ]
        headers = {KEY_HEADER: "bundle-replay-key-0001"}

        r1 = client.post(
            "/api/v1/uploads/computed-species", json=payload, headers=headers
        )
        assert r1.status_code == 201, r1.text
        counts_after_first = _counts()
        s3_writes_after_first = list(stub_store_artifact)

        r2 = client.post(
            "/api/v1/uploads/computed-species", json=payload, headers=headers
        )
        assert r2.status_code == 201, r2.text
        # Replay-marker header signals the response came from cache.
        assert r2.headers.get("Idempotency-Replayed") == "true"
        # Same response body.
        assert r1.json() == r2.json()
        # No new DB rows.
        assert _counts() == counts_after_first
        # No new S3 writes.
        assert stub_store_artifact == s3_writes_after_first

    def test_same_key_different_body_409(self, client):
        payload1 = _hydrogen_bundle_payload()
        payload2 = _hydrogen_bundle_payload()
        payload2["note"] = "different"
        headers = {KEY_HEADER: "bundle-conflict-key-0001"}
        r1 = client.post(
            "/api/v1/uploads/computed-species", json=payload1, headers=headers
        )
        assert r1.status_code == 201
        r2 = client.post(
            "/api/v1/uploads/computed-species", json=payload2, headers=headers
        )
        assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_unauthenticated_returns_401(self, db_session) -> None:
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_write_db] = lambda: db_session
        anon_client = TestClient(app)
        resp = anon_client.post(
            "/api/v1/uploads/computed-species",
            json=_hydrogen_bundle_payload(),
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Artifact → submission links (ingestion-audit model)
# ---------------------------------------------------------------------------


class TestArtifactSubmissionLinks:
    """Uploaded artifacts are linked to the submission as evidence
    (role="artifact"), but are never given record_review rows.
    """

    def test_computed_species_artifacts_linked_to_submission(
        self, client, db_session, stub_store_artifact
    ):
        import copy as _copy

        from sqlalchemy import select

        from app.db.models.common import SubmissionRecordType
        from app.db.models.record_review import RecordReview
        from app.db.models.submission import SubmissionRecordLink

        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["primary_calculation"]["artifacts"] = [
            _opt_log_artifact()
        ]
        resp = client.post("/api/v1/uploads/computed-species", json=payload)
        assert resp.status_code == 201, resp.text
        submission_id = resp.json()["submission_id"]
        assert submission_id is not None

        art_links = db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type == SubmissionRecordType.artifact,
            )
        ).all()
        assert art_links, "uploaded artifact should be linked to the submission"
        assert all(link.role == "artifact" for link in art_links)

        # Artifacts are evidence, not reviewable records: no record_review rows.
        for link in art_links:
            review = db_session.scalar(
                select(RecordReview).where(
                    RecordReview.record_type == SubmissionRecordType.artifact,
                    RecordReview.record_id == link.record_id,
                )
            )
            assert review is None

    def test_computed_reaction_artifacts_linked_to_submission(
        self, client, db_session, stub_store_artifact
    ):
        import copy as _copy

        from sqlalchemy import select

        from app.db.models.common import SubmissionRecordType
        from app.db.models.submission import SubmissionRecordLink
        from tests.api.test_api_kfir_rxn import _BUNDLE as _COMPUTED_REACTION_BUNDLE

        bundle = _copy.deepcopy(_COMPUTED_REACTION_BUNDLE)
        # Attach an artifact to one species-side calculation.
        bundle["species"][0]["calculations"][0]["artifacts"] = [_opt_log_artifact()]

        resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
        assert resp.status_code == 201, resp.text
        submission_id = resp.json()["submission_id"]
        assert submission_id is not None

        art_links = db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type == SubmissionRecordType.artifact,
            )
        ).all()
        assert art_links, "computed-reaction artifact should be linked"
        assert all(link.role == "artifact" for link in art_links)

    def test_idempotent_replay_does_not_duplicate_artifact_links(
        self, client, db_session, stub_store_artifact
    ):
        from sqlalchemy import func, select

        from app.db.models.common import SubmissionRecordType
        from app.db.models.submission import Submission, SubmissionRecordLink

        payload = _hydrogen_bundle_payload()
        payload["conformers"][0]["primary_calculation"]["artifacts"] = [
            _opt_log_artifact()
        ]
        headers = {"Idempotency-Key": "computed-species-artifact-idem-001"}

        first = client.post(
            "/api/v1/uploads/computed-species", json=payload, headers=headers
        )
        assert first.status_code == 201, first.text
        submission_id = first.json()["submission_id"]

        def _artifact_link_count() -> int:
            return (
                db_session.scalar(
                    select(func.count())
                    .select_from(SubmissionRecordLink)
                    .where(
                        SubmissionRecordLink.submission_id == submission_id,
                        SubmissionRecordLink.record_type
                        == SubmissionRecordType.artifact,
                    )
                )
                or 0
            )

        subs_after_first = (
            db_session.scalar(select(func.count()).select_from(Submission)) or 0
        )
        artifact_links_after_first = _artifact_link_count()
        assert artifact_links_after_first > 0

        # Replay: same key + body returns the stored response, no new rows.
        second = client.post(
            "/api/v1/uploads/computed-species", json=payload, headers=headers
        )
        assert second.status_code == 201, second.text
        assert second.json()["submission_id"] == submission_id

        assert (
            db_session.scalar(select(func.count()).select_from(Submission)) or 0
        ) == subs_after_first
        assert _artifact_link_count() == artifact_links_after_first
