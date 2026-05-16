"""Tests for the tckdb-client/server compatibility check.

The dependency :func:`app.api.client_version.require_supported_tckdb_client`
is wired onto every write/upload route. Read endpoints are exempt. These
tests use a lightweight write endpoint (the conformer job-enqueue route)
to assert the gate fires before any business logic runs.
"""

from __future__ import annotations

import pytest

from app.api.config import settings

CLIENT_NAME = "tckdb-client"
NAME_HEADER = "X-TCKDB-Client-Name"
VERSION_HEADER = "X-TCKDB-Client-Version"

# Cheap write target: it carries the compat dep but does not need a
# valid body to exercise the dep, because FastAPI resolves explicit
# ``dependencies=[...]`` items before parsing the request body.
WRITE_URL = "/api/v1/jobs/conformer"

# Cheap read target: legitimate response (200 or schema-driven error).
READ_URL = "/api/v1/scientific/species/search"


@pytest.fixture
def supported_version() -> str:
    """A version that is at or above the configured minimum."""
    return settings.min_supported_tckdb_client_version


@pytest.fixture
def too_old_version() -> str:
    """A version guaranteed to be below the configured minimum."""
    return "0.0.1"


def test_write_rejects_old_tckdb_client_version(client, too_old_version):
    response = client.post(
        WRITE_URL,
        headers={NAME_HEADER: CLIENT_NAME, VERSION_HEADER: too_old_version},
        json={},
    )
    assert response.status_code == 426
    body = response.json()
    assert body["detail"]["code"] == "tckdb_client_version_unsupported"
    assert body["detail"]["client_name"] == CLIENT_NAME
    assert body["detail"]["client_version"] == too_old_version
    assert (
        body["detail"]["minimum_supported_version"]
        == settings.min_supported_tckdb_client_version
    )


def test_write_rejects_missing_version_when_client_identifies(client):
    response = client.post(
        WRITE_URL, headers={NAME_HEADER: CLIENT_NAME}, json={}
    )
    assert response.status_code == 426
    assert response.json()["detail"]["code"] == "tckdb_client_version_missing"


def test_write_rejects_unparsable_version(client):
    response = client.post(
        WRITE_URL,
        headers={NAME_HEADER: CLIENT_NAME, VERSION_HEADER: "not-a-version"},
        json={},
    )
    assert response.status_code == 426
    assert response.json()["detail"]["code"] == "tckdb_client_version_invalid"


def test_write_accepts_supported_version(client, supported_version):
    """Equal-or-greater version passes the compat gate.

    The handler may still return 422 (empty payload) — what we care
    about is that the 426 gate did not fire.
    """
    response = client.post(
        WRITE_URL,
        headers={NAME_HEADER: CLIENT_NAME, VERSION_HEADER: supported_version},
        json={},
    )
    assert response.status_code != 426


def test_write_accepts_no_client_header(client):
    """Raw HTTP callers that omit the client name are passed through."""
    response = client.post(WRITE_URL, json={})
    assert response.status_code != 426


def test_write_accepts_unknown_client_name(client):
    """Only ``tckdb-client`` is gated; other clients are not blocked yet."""
    response = client.post(
        WRITE_URL,
        headers={NAME_HEADER: "some-other-client", VERSION_HEADER: "0.0.1"},
        json={},
    )
    assert response.status_code != 426


def test_read_endpoint_not_gated_by_old_client(client, too_old_version):
    """Read paths must stay reachable so users can debug while upgrading."""
    response = client.get(
        READ_URL,
        params={"smiles": "C"},
        headers={NAME_HEADER: CLIENT_NAME, VERSION_HEADER: too_old_version},
    )
    assert response.status_code != 426


def test_meta_returns_compatibility_info(client):
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["server"] == "tckdb"
    assert (
        body["minimum_supported_tckdb_client_version"]
        == settings.min_supported_tckdb_client_version
    )
    assert (
        body["enforce_tckdb_client_version_on_writes"]
        is settings.enforce_tckdb_client_version_on_writes
    )


def test_enforcement_disabled_allows_old_client(client, monkeypatch, too_old_version):
    """When the enforcement flag is off, the gate becomes a no-op."""
    monkeypatch.setattr(
        settings, "enforce_tckdb_client_version_on_writes", False
    )
    response = client.post(
        WRITE_URL,
        headers={NAME_HEADER: CLIENT_NAME, VERSION_HEADER: too_old_version},
        json={},
    )
    assert response.status_code != 426
