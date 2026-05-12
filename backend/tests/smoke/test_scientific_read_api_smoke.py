"""Opt-in deployment smoke tests for /api/v1/scientific/* over real HTTP.

These tests run only when ``TCKDB_SMOKE_BASE_URL`` is set in the
environment. They are **not** part of the normal backend test suite —
the whole module is skipped by default. They are designed to be run
post-deploy against a live TCKDB instance to verify the scientific
read/query contract is up.

The tests treat empty result sets (``records: []``) as success. They
fail only when the API contract is broken: an endpoint is missing,
returns the wrong status code, the response envelope is malformed, the
error contract is broken, or the server returns an unexpected error.

Configuration via environment variables:

    TCKDB_SMOKE_BASE_URL   required; base URL including /api/v1
                           e.g. https://tckdb.example.com/api/v1
    TCKDB_SMOKE_API_KEY    optional; sent via the X-API-Key header
                           if the deployment requires auth
    TCKDB_SMOKE_TIMEOUT    optional; per-request timeout in seconds
                           (default: 30)

Run only the smoke tests::

    TCKDB_SMOKE_BASE_URL=http://127.0.0.1:8000/api/v1 \\
        conda run -n tckdb_env pytest backend/tests/smoke/ -q

When ``TCKDB_SMOKE_BASE_URL`` is unset the entire module is collected
but every test reports SKIP — no network calls happen.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

import httpx
import pytest

API_KEY_HEADER = "X-API-Key"

_BASE_URL_ENV = "TCKDB_SMOKE_BASE_URL"
_API_KEY_ENV = "TCKDB_SMOKE_API_KEY"
_TIMEOUT_ENV = "TCKDB_SMOKE_TIMEOUT"


# ---------------------------------------------------------------------------
# Module-level skip when the env var is unset
# ---------------------------------------------------------------------------


_BASE_URL = os.environ.get(_BASE_URL_ENV)

pytestmark = pytest.mark.skipif(
    not _BASE_URL,
    reason=(
        f"{_BASE_URL_ENV} is not set; scientific read smoke tests are "
        "opt-in and skipped in the normal test run."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _join(base: str, path: str) -> str:
    """Join ``base`` and ``path`` cleanly (no double slashes)."""
    return base.rstrip("/") + ("" if path.startswith("/") else "/") + path


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json"}
    api_key = os.environ.get(_API_KEY_ENV)
    if api_key:
        headers[API_KEY_HEADER] = api_key
    return headers


def _timeout() -> float:
    return float(os.environ.get(_TIMEOUT_ENV, "30"))


def _assert_scientific_envelope(body: Any, *, paginated: bool = True) -> None:
    """Validate the canonical /scientific/* response envelope shape."""
    assert isinstance(body, dict), f"expected dict envelope, got {type(body).__name__}"
    assert "request" in body, "missing 'request' key in scientific envelope"
    assert "review_summary" in body, "missing 'review_summary' key"
    review = body["review_summary"]
    assert isinstance(review, dict)
    for key in ("approved", "under_review", "not_reviewed", "deprecated", "rejected", "total"):
        assert key in review, f"review_summary missing '{key}'"

    if paginated:
        assert "records" in body, "missing 'records' key"
        assert isinstance(body["records"], list)
        assert "pagination" in body, "missing 'pagination' key"
        pagination = body["pagination"]
        for key in ("offset", "limit", "returned", "total"):
            assert key in pagination, f"pagination missing '{key}'"
        assert pagination["returned"] == len(body["records"])


def _assert_error_envelope(
    body: Any,
    *,
    expected_substrings: Iterable[str] = (),
) -> None:
    """Validate the canonical 4xx error envelope shape."""
    assert isinstance(body, dict), "expected dict error body"
    detail = body.get("detail")
    assert detail is not None, "missing 'detail' on error response"
    detail_str = detail if isinstance(detail, str) else repr(detail)
    for substr in expected_substrings:
        assert substr in detail_str, (
            f"expected {substr!r} substring in error detail, got: {detail_str!r}"
        )


@pytest.fixture(scope="module")
def http() -> httpx.Client:
    with httpx.Client(timeout=_timeout(), headers=_headers()) as client:
        yield client


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


def test_openapi_exposes_scientific_routes(http: httpx.Client):
    """OpenAPI document must list every chemistry-first scientific endpoint."""
    # FastAPI exposes OpenAPI at /openapi.json, *not* under /api/v1. So we
    # strip the /api/v1 suffix from the base URL to find the doc.
    base = _BASE_URL.rstrip("/")
    if base.endswith("/api/v1"):
        openapi_root = base[: -len("/api/v1")]
    else:
        openapi_root = base
    resp = http.get(f"{openapi_root}/openapi.json")
    assert resp.status_code == 200, (
        f"GET /openapi.json returned {resp.status_code} (expected 200). "
        "Smoke deploy may not be a TCKDB FastAPI app."
    )
    paths = set(resp.json()["paths"].keys())
    expected = {
        "/api/v1/scientific/species/search",
        "/api/v1/scientific/reactions/search",
        "/api/v1/scientific/thermo/search",
        "/api/v1/scientific/kinetics/search",
        "/api/v1/scientific/species-calculations/search",
        "/api/v1/scientific/species-entries/{species_entry_id}/thermo",
        "/api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics",
        "/api/v1/scientific/reaction-entries/{reaction_entry_id}/full",
    }
    missing = expected - paths
    assert not missing, f"OpenAPI is missing scientific paths: {missing}"


def test_species_search_returns_valid_envelope(http: httpx.Client):
    """GET species/search with a harmless filter returns the canonical envelope."""
    resp = http.get(
        _join(_BASE_URL, "/scientific/species/search"),
        params={"smiles": "TCKDB_SMOKE_NONEXISTENT_SMILES"},
    )
    assert resp.status_code == 200, (
        f"GET species/search returned {resp.status_code}: {resp.text[:200]}"
    )
    body = resp.json()
    _assert_scientific_envelope(body)
    # An unmatched SMILES is expected to be empty — that is success here.
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_thermo_search_post_returns_valid_envelope(http: httpx.Client):
    """POST /scientific/thermo/search with a harmless body returns 200 + envelope."""
    resp = http.post(
        _join(_BASE_URL, "/scientific/thermo/search"),
        json={"smiles": "TCKDB_SMOKE_NONEXISTENT_SMILES"},
    )
    assert resp.status_code == 200, (
        f"POST thermo/search returned {resp.status_code}: {resp.text[:200]}"
    )
    body = resp.json()
    _assert_scientific_envelope(body)
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_kinetics_search_post_returns_valid_envelope(http: httpx.Client):
    """POST /scientific/kinetics/search with a harmless body returns 200 + envelope."""
    resp = http.post(
        _join(_BASE_URL, "/scientific/kinetics/search"),
        json={
            "reactants": ["TCKDB_SMOKE_NONEXISTENT_REACTANT"],
            "products": ["TCKDB_SMOKE_NONEXISTENT_PRODUCT"],
            "direction": "either",
        },
    )
    assert resp.status_code == 200, (
        f"POST kinetics/search returned {resp.status_code}: {resp.text[:200]}"
    )
    body = resp.json()
    _assert_scientific_envelope(body)
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_species_calculations_search_post_returns_valid_envelope(http: httpx.Client):
    """POST /scientific/species-calculations/search returns 200 + envelope."""
    resp = http.post(
        _join(_BASE_URL, "/scientific/species-calculations/search"),
        json={"smiles": "TCKDB_SMOKE_NONEXISTENT_SMILES"},
    )
    assert resp.status_code == 200, (
        f"POST species-calculations/search returned {resp.status_code}: "
        f"{resp.text[:200]}"
    )
    body = resp.json()
    _assert_scientific_envelope(body)
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_invalid_include_token_returns_422(http: httpx.Client):
    """Search endpoints must reject unknown include tokens with 422."""
    resp = http.get(
        _join(_BASE_URL, "/scientific/species/search"),
        params={
            "smiles": "TCKDB_SMOKE_NONEXISTENT_SMILES",
            "include": "TCKDB_SMOKE_INVALID_INCLUDE_TOKEN",
        },
    )
    assert resp.status_code == 422, (
        f"expected 422 for unknown include token, got {resp.status_code}: "
        f"{resp.text[:200]}"
    )
    _assert_error_envelope(resp.json(), expected_substrings=["unknown_include_token"])


def test_invalid_explicit_id_returns_404_for_detail_endpoint(http: httpx.Client):
    """Detail endpoints must 404 cleanly on an unknown explicit id."""
    # 999_999_999 is essentially guaranteed not to exist in any real instance.
    resp = http.get(
        _join(_BASE_URL, "/scientific/reaction-entries/999999999/kinetics"),
    )
    assert resp.status_code == 404, (
        f"expected 404 for unknown reaction_entry_id, got {resp.status_code}: "
        f"{resp.text[:200]}"
    )
    _assert_error_envelope(resp.json(), expected_substrings=["reaction_entry"])


def test_client_supplied_sort_returns_422(http: httpx.Client):
    """v0 contract: client-supplied sort= is rejected with 422."""
    resp = http.get(
        _join(_BASE_URL, "/scientific/species/search"),
        params={
            "smiles": "TCKDB_SMOKE_NONEXISTENT_SMILES",
            "sort": "anything",
        },
    )
    assert resp.status_code == 422, (
        f"expected 422 for client-supplied sort, got {resp.status_code}"
    )
    _assert_error_envelope(resp.json(), expected_substrings=["client_sort_not_supported"])


def test_post_kinetics_search_rejects_query_string(http: httpx.Client):
    """v0 contract: POST search rejects query-string filters."""
    resp = http.post(
        _join(_BASE_URL, "/scientific/kinetics/search"),
        params={"reactants": "TCKDB_SMOKE"},
        json={"reactants": ["TCKDB_SMOKE"], "products": ["TCKDB_SMOKE"]},
    )
    assert resp.status_code == 422, (
        f"expected 422 for POST with query string, got {resp.status_code}"
    )
    _assert_error_envelope(
        resp.json(), expected_substrings=["post_search_fields_must_be_in_body"]
    )
