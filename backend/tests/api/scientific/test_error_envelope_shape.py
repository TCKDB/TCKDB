"""Lock the Phase D error-envelope shape for the scientific read API.

The Phase D scientific read API intentionally follows the existing
FastAPI-style error envelope used by the wider backend (write paths,
lookup paths, admin paths). The spec at
``docs/specs/read_api_mvp.md`` §Error model documents this shape.

These tests exist so that the envelope cannot drift back toward the
older draft ``{"error": {"code", "message", "legal_values"}}`` shape
without a coordinated, cross-surface change. Adding a new error
category here is fine; changing the shape is not, without updating
the spec, the guides, and ``tckdb-client`` together.
"""

from __future__ import annotations


def test_unknown_include_token_uses_detail_string_envelope(client, db_session):
    """422 unknown_include_token returns ``{"detail": "<code>: <message>"}``."""
    resp = client.get(
        "/api/v1/scientific/species/search?smiles=O&include=banana"
    )

    assert resp.status_code == 422
    body = resp.json()
    # Phase D envelope: flat `detail`, no nested `error` block.
    assert "detail" in body
    assert "error" not in body
    # Stable code is the prefix of `detail` up to the first ": ".
    detail = body["detail"]
    assert isinstance(detail, str)
    assert detail.startswith("unknown_include_token:")


def test_client_sort_uses_detail_string_envelope(client, db_session):
    """422 client_sort_not_supported uses the same flat envelope."""
    resp = client.get(
        "/api/v1/scientific/species/search?smiles=O&sort=anything"
    )

    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body
    assert "error" not in body
    assert isinstance(body["detail"], str)
    assert body["detail"].startswith("client_sort_not_supported:")


def test_unknown_path_handle_uses_detail_plus_code(client, db_session):
    """404 handle_not_found uses ``{"detail": "...", "code": "handle_not_found"}``.

    A separate top-level ``code`` field is the only place the envelope
    diverges from the flat 422 shape; it is still **not** a nested
    ``error`` object. Frontends should read either ``body.detail`` or
    ``body.code`` — never ``body.error.code``.
    """
    # Well-formed ref of the right prefix that resolves to nothing.
    resp = client.get(
        "/api/v1/scientific/reaction-entries/"
        "rxe_aaaaaaaaaaaaaaaaaaaaaaaaaa/kinetics"
    )

    assert resp.status_code == 404
    body = resp.json()
    assert "detail" in body
    assert "error" not in body
    assert body.get("code") == "handle_not_found"
