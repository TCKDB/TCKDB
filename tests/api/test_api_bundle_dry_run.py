"""API tests for the hosted contribution-bundle dry-run endpoint.

POST /api/v1/bundles/dry-run

Covers auth (anonymous/API key/invalid key), thermo + kinetics happy paths,
schema-validation failure, and a no-mutation guarantee against the tables
the spec calls out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.thermo import Thermo
from app.db.models.upload_job import UploadJob


REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"
ENDPOINT = "/api/v1/bundles/dry-run"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_client(db_session) -> Iterator[TestClient]:
    """TestClient WITHOUT the get_current_user override.

    Mirrors the pattern in tests/api/test_api_auth.py — needed for the
    anonymous and bad-API-key tests, which would otherwise be silently
    authenticated by the default ``client`` fixture.
    """
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_bundle_dict(filename: str) -> dict:
    return json.loads((EXAMPLES_DIR / filename).read_text())


def _no_mutation_counts(session) -> dict[str, int]:
    """Snapshot every table the spec requires dry-run not to touch."""
    return {
        "species": session.scalar(select(func.count()).select_from(Species)) or 0,
        "species_entry": session.scalar(
            select(func.count()).select_from(SpeciesEntry)
        )
        or 0,
        "chem_reaction": session.scalar(
            select(func.count()).select_from(ChemReaction)
        )
        or 0,
        "reaction_entry": session.scalar(
            select(func.count()).select_from(ReactionEntry)
        )
        or 0,
        "thermo": session.scalar(select(func.count()).select_from(Thermo)) or 0,
        "kinetics": session.scalar(select(func.count()).select_from(Kinetics)) or 0,
        "submission": session.scalar(select(func.count()).select_from(Submission))
        or 0,
        "submission_audit_event": session.scalar(
            select(func.count()).select_from(SubmissionAuditEvent)
        )
        or 0,
        "submission_record_link": session.scalar(
            select(func.count()).select_from(SubmissionRecordLink)
        )
        or 0,
        "upload_job": session.scalar(select(func.count()).select_from(UploadJob))
        or 0,
    }


# ---------------------------------------------------------------------------
# Auth tests (use raw_client — real auth dependency)
# ---------------------------------------------------------------------------


def test_anonymous_dry_run_rejected(raw_client) -> None:
    bundle = _load_bundle_dict("thermo-bundle-v0.json")
    resp = raw_client.post(ENDPOINT, json=bundle)
    assert resp.status_code == 401


def test_invalid_api_key_rejected(raw_client) -> None:
    bundle = _load_bundle_dict("thermo-bundle-v0.json")
    resp = raw_client.post(
        ENDPOINT, json=bundle, headers={"X-API-Key": "definitely-not-valid"}
    )
    assert resp.status_code == 401


def test_valid_api_key_accepted(raw_client) -> None:
    """The session-scoped fixture seeds an API key (see tests/conftest.py)."""
    bundle = _load_bundle_dict("thermo-bundle-v0.json")
    resp = raw_client.post(
        ENDPOINT,
        json=bundle,
        headers={"X-API-Key": "test-api-key-for-tckdb"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bundle_kind"] == "thermo"
    assert body["bundle_valid"] is True


# ---------------------------------------------------------------------------
# Happy-path tests (use default client — auth pre-overridden)
# ---------------------------------------------------------------------------


def test_authenticated_thermo_dry_run_succeeds(client) -> None:
    bundle = _load_bundle_dict("thermo-bundle-v0.json")

    before = _no_mutation_counts(client._db_session)
    resp = client.post(ENDPOINT, json=bundle)
    after = _no_mutation_counts(client._db_session)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bundle_valid"] is True
    assert body["bundle_kind"] == "thermo"

    summary = body["summary"]
    assert summary["records_seen"] >= 3  # species, species_entry, thermo (min)
    assert summary["would_append"] >= 1
    assert summary["errors"] == 0

    # At least one item describes the thermo append.
    actions = {(it["record_type"], it["action"]) for it in body["items"]}
    assert ("thermo", "would_append") in actions

    # No mutation in any tracked table.
    assert before == after


def test_authenticated_kinetics_dry_run_succeeds(client) -> None:
    bundle = _load_bundle_dict("kinetics-bundle-v0.json")

    before = _no_mutation_counts(client._db_session)
    resp = client.post(ENDPOINT, json=bundle)
    after = _no_mutation_counts(client._db_session)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bundle_valid"] is True
    assert body["bundle_kind"] == "kinetics"

    actions = {(it["record_type"], it["action"]) for it in body["items"]}
    assert ("kinetics", "would_append") in actions
    # The example kinetics bundle carries software + workflow tool refs.
    assert any(it["record_type"] == "software_release" for it in body["items"])
    assert any(it["record_type"] == "workflow_tool_release" for it in body["items"])

    assert before == after


def test_invalid_bundle_returns_validation_error(client) -> None:
    """Structurally invalid bundles must fail through normal validation —
    not be wrapped in a 200 dry-run result."""
    bundle = _load_bundle_dict("thermo-bundle-v0.json")
    # Drop the bundle_kind so the bundle no longer satisfies the schema.
    del bundle["bundle_kind"]

    resp = client.post(ENDPOINT, json=bundle)
    assert resp.status_code == 422


def test_dry_run_response_shape(client) -> None:
    """Top-level response carries all required keys with the right types."""
    bundle = _load_bundle_dict("thermo-bundle-v0.json")
    resp = client.post(ENDPOINT, json=bundle)
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) >= {
        "bundle_valid",
        "bundle_kind",
        "summary",
        "items",
        "messages",
    }
    assert isinstance(body["items"], list)
    assert isinstance(body["messages"], list)
    summary = body["summary"]
    assert set(summary.keys()) >= {
        "records_seen",
        "would_create",
        "would_reuse",
        "would_append",
        "unsupported",
        "errors",
        "warnings",
    }
