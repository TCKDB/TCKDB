"""Tests for ``POST /api/v1/uploads/thermoml`` (Phase C-E6).

Lets anyone deposit a ThermoML XML document directly -- not only the NIST
bulk archive (``backend/scripts/thermoml_cp_import.py --archive``). Exercises
the route end to end: happy path (refs only, never a database id), coded
refusals, idempotency (required here, unlike every sibling ``/uploads/*``
route -- see the route's own docstring), and authorization -- mirroring
``test_api_calculation_artifacts.py``'s structure for the nearest sibling
(the only other route that carries inline base64 file bytes).

This route always commits (no preview flag -- see
``ThermoMLUploadRequest``'s docstring for why: no sibling ``/uploads/*``
route previews either). A preview is available only through the backend
CLI's default (no ``--commit``) dry-run mode.
"""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import get_db, get_write_db

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "importers"
    / "thermoml"
    / "fixtures"
)
BENZENE_XML = FIXTURES / "cp_ideal_gas_statistical_thermodynamics.xml"
LIQUID_XML = FIXTURES / "cp_liquid_unsupported.xml"
SCHEMA_INVALID_XML = FIXTURES / "schema_invalid.xml"

KEY_HEADER = "Idempotency-Key"

_RIGHTS = {"license": "CC0-1.0", "depositor_attests_right_to_license": True}


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _payload(
    path: Path,
    *,
    filename: str = "article.xml",
    rights: dict | None = _RIGHTS,
    doi: str | None = None,
) -> dict:
    body: dict = {
        "filename": filename,
        "content_base64": _b64(path),
    }
    if rights is not None:
        body["rights"] = rights
    if doi is not None:
        body["doi"] = doi
    return body


def _headers(key: str = "default") -> dict:
    # Idempotency-Key must be 16-200 chars (app.services.idempotency._KEY_PATTERN);
    # pad every test-supplied suffix out to that floor rather than hand-picking
    # a long-enough literal for each call site.
    full_key = f"e6-thermoml-test-{key}"
    return {KEY_HEADER: full_key.ljust(16, "0")}


def _ids_in(obj) -> set[str]:
    """Every dict key ending in ``_id`` (or bare ``id``), anywhere in the
    response tree -- the C-E6 brief's own scan-for-``_id``-keys check."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "id" or key.endswith("_id"):
                found.add(key)
            found |= _ids_in(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= _ids_in(item)
    return found


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_commit_persists_and_response_names_only_public_refs(
        self, client
    ) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-happy-path"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["schema_valid"] is True
        assert body["payload_count"] == 4
        assert body["inserted_count"] == 4
        assert body["submission_ref"] is not None
        assert body["submission_ref"].startswith("sub_")
        for disposition in body["dispositions"]:
            assert disposition["action"] == "inserted"
            assert disposition["observation_ref"].startswith("mpo_")
        assert not _ids_in(body), f"response leaked database ids: {_ids_in(body)}"

    def test_doi_is_taken_from_the_files_own_sdoi(self, client) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-doi-from-file"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["doi"] == "10.1016/j.jct.2013.08.022"


# ---------------------------------------------------------------------------
# Coded refusals
# ---------------------------------------------------------------------------


class TestCodedRefusals:
    def test_schema_invalid_document(self, client) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(SCHEMA_INVALID_XML),
            headers=_headers("e6-schema-invalid"),
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "thermoml_schema_invalid"

    def test_liquid_only_document_has_no_supported_content(self, client) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(LIQUID_XML),
            headers=_headers("e6-liquid-only"),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "thermoml_no_supported_content"
        assert body["context"]["reasons"], "expected unsupported/rejected reasons"

    def test_oversized_file_is_refused(self, client, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.api.routes.uploads.MAX_ENCODED_ARTIFACT_LEN", 16
        )
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-oversized"),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "thermoml_file_too_large"
        assert body["context"]["max_bytes"]

    def test_doi_conflicting_with_the_files_own_sdoi_is_refused(
        self, client
    ) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML, doi="10.1000/not-the-real-doi"),
            headers=_headers("e6-doi-conflict"),
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "thermoml_doi_conflict"

    def test_invalid_base64_is_refused(self, client) -> None:
        body = _payload(BENZENE_XML)
        body["content_base64"] = "not valid base64!!"
        resp = client.post(
            "/api/v1/uploads/thermoml", json=body, headers=_headers("e6-bad-b64")
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "thermoml_invalid_base64"

    def test_missing_rights_is_refused_by_ordinary_field_requiredness(
        self, client
    ) -> None:
        """``rights`` is a required field on ``ThermoMLUploadRequest`` --
        omitting it hits the same generic Pydantic 422 every other
        required field on every other schema in this codebase already
        produces. There is no bespoke "rights missing" refusal to
        invent: DepositRights is optional everywhere else in TCKDB
        (see its own docstring -- absence bites at release time), but
        this route's whole content is a third-party document with no
        source_terms fallback, so rights is required here specifically.
        """
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML, rights=None),
            headers=_headers("e6-no-rights"),
        )
        assert resp.status_code == 422, resp.text

    def test_missing_idempotency_key_is_refused(self, client) -> None:
        """Unlike every sibling ``/uploads/*`` route, this one requires
        ``Idempotency-Key`` (DR-0024 + this work package's brief). The
        route declares a second, required binding of the same header, so
        FastAPI's ordinary missing-required-header 422 fires -- the
        existing validation mechanism, not a new bespoke refusal.

        Mutation: drop the required ``_idempotency_key_required: str =
        Header(...)`` parameter from ``upload_thermoml`` -- this goes red
        because a keyless request would then succeed (201).
        """
        resp = client.post(
            "/api/v1/uploads/thermoml", json=_payload(BENZENE_XML)
        )
        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Idempotency (DR-0024) -- required on this route, unlike its siblings.
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_replay_with_same_key_returns_identical_response_no_new_rows(
        self, client, db_session
    ) -> None:
        from sqlalchemy import select

        from app.db.models.molecular_property_observation import (
            MolecularPropertyObservation,
        )

        headers = _headers("e6-replay-test-key-001")
        payload = _payload(BENZENE_XML)

        first = client.post(
            "/api/v1/uploads/thermoml", json=payload, headers=headers
        )
        assert first.status_code == 201, first.text
        rows_after_first = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()

        second = client.post(
            "/api/v1/uploads/thermoml", json=payload, headers=headers
        )
        assert second.status_code == 201, second.text
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert second.json() == first.json()

        rows_after_second = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()
        assert len(rows_after_second) == len(rows_after_first)

    def test_same_key_different_payload_conflicts(self, client) -> None:
        headers = _headers("e6-conflict-test-key-001")
        resp1 = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=headers,
        )
        assert resp1.status_code == 201, resp1.text

        resp2 = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML, filename="different.xml"),
            headers=headers,
        )
        assert resp2.status_code == 409, resp2.text
        assert resp2.json()["code"] == "idempotency_conflict"


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
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-anon"),
        )
        assert resp.status_code == 401

    def test_ordinary_user_role_suffices(self, client) -> None:
        # ``client`` is already a plain user-role account (see
        # backend/tests/conftest.py::_api_test_user) -- this is a
        # standard upload, not a curator-only action.
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-user-role"),
        )
        assert resp.status_code == 201, resp.text
