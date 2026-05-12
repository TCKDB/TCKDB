"""Tests for F7 — integer path-handle probe oracle.

Refs remain the documented public form. Integer compatibility
handles continue to work, but the 404 response shape for unknown
integer handles must match the unknown-ref shape closely enough
that an attacker cannot tell them apart from the body alone. In
particular:

- The integer must never appear in the response body.
- Both unknown-integer and unknown-ref responses carry the same
  stable ``code: "handle_not_found"``.
- Wrong-prefix and malformed handles continue to return 422.
- Valid refs and valid integer handles still resolve.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def seeded_geometry(db_session):
    """Create one geometry so valid-handle paths have something to return."""
    from tests.services.scientific_read._factories import make_geometry

    return make_geometry(db_session, natoms=2, xyz_text=None)


# ---------------------------------------------------------------------------
# Unknown integer handles — sanitized 404
# ---------------------------------------------------------------------------


_UNKNOWN_INT = 99999991
_UNKNOWN_INT_ALT = 99999992


@pytest.mark.parametrize(
    "label,path",
    [
        ("species-entry thermo",
         f"/api/v1/scientific/species-entries/{_UNKNOWN_INT}/thermo"),
        ("reaction-entry kinetics",
         f"/api/v1/scientific/reaction-entries/{_UNKNOWN_INT}/kinetics"),
        ("reaction-entry full",
         f"/api/v1/scientific/reaction-entries/{_UNKNOWN_INT}/full"),
        ("geometry detail",
         f"/api/v1/scientific/geometries/{_UNKNOWN_INT}"),
    ],
)
def test_unknown_integer_handle_returns_sanitized_404(client, label, path):
    r = client.get(path)
    assert r.status_code == 404
    body = r.json()
    assert body.get("code") == "handle_not_found"
    # The integer probe value must not appear in the public body.
    assert str(_UNKNOWN_INT) not in repr(body)


def test_unknown_ref_handle_uses_same_code_as_unknown_int(client):
    r_int = client.get(
        f"/api/v1/scientific/reaction-entries/{_UNKNOWN_INT}/full"
    )
    r_ref = client.get(
        "/api/v1/scientific/reaction-entries/"
        "rxe_zzzzzzzzzzzzzzzzzzzzzzzzzz/full"
    )
    assert r_int.status_code == 404
    assert r_ref.status_code == 404
    assert r_int.json().get("code") == r_ref.json().get("code") == "handle_not_found"


def test_two_unknown_integer_handles_return_identical_bodies(client):
    """Two distinct unknown ids must produce byte-identical response bodies.

    Otherwise the integer id is still leaking through some field.
    """
    a = client.get(
        f"/api/v1/scientific/geometries/{_UNKNOWN_INT}"
    ).json()
    b = client.get(
        f"/api/v1/scientific/geometries/{_UNKNOWN_INT_ALT}"
    ).json()
    assert a == b


# ---------------------------------------------------------------------------
# Wrong-prefix / malformed handles still 422
# ---------------------------------------------------------------------------


def test_wrong_prefix_ref_returns_422_handle_type_mismatch(client):
    r = client.get(
        "/api/v1/scientific/reaction-entries/"
        "spe_abcdefghijklmnopqrstuvwxyz/full"
    )
    assert r.status_code == 422
    assert "handle_type_mismatch" in r.json()["detail"]


def test_malformed_handle_returns_422_invalid_handle(client):
    r = client.get("/api/v1/scientific/geometries/!!notarealhandle!!")
    assert r.status_code == 422
    assert "invalid_handle" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Valid handles still work
# ---------------------------------------------------------------------------


def test_valid_ref_handle_still_resolves(client, seeded_geometry):
    r = client.get(f"/api/v1/scientific/geometries/{seeded_geometry.public_ref}")
    assert r.status_code == 200


def test_valid_integer_handle_still_resolves(client, seeded_geometry):
    r = client.get(f"/api/v1/scientific/geometries/{seeded_geometry.id}")
    assert r.status_code == 200
