"""Tests for F17 — path-handle length bounds on scientific routes.

The scientific handle routes accept either an integer PK or a public
ref of the form ``<prefix>_<26-char body>`` (≤ 31 chars). The
``max_length=64`` cap on the path parameter rejects pathological
multi-MB URL components at the FastAPI validation layer, before any
service-side resolution runs.
"""

from __future__ import annotations

import pytest


_HUGE = "x" * 256


# Each entry: (description, path_template, accepts_int)
_SCIENTIFIC_HANDLE_ROUTES = [
    (
        "reaction-entry full",
        "/api/v1/scientific/reaction-entries/{handle}/full",
        True,
    ),
    (
        "reaction-entry kinetics",
        "/api/v1/scientific/reaction-entries/{handle}/kinetics",
        True,
    ),
    (
        "species-entry thermo",
        "/api/v1/scientific/species-entries/{handle}/thermo",
        True,
    ),
    (
        "geometry detail",
        "/api/v1/scientific/geometries/{handle}",
        True,
    ),
]


@pytest.mark.parametrize(
    "label,path_template,accepts_int", _SCIENTIFIC_HANDLE_ROUTES
)
def test_oversized_path_handle_returns_422(
    client, label, path_template, accepts_int
):
    """A path component over 64 chars is rejected before the handler runs."""
    path = path_template.format(handle=_HUGE)
    r = client.get(path)
    assert r.status_code == 422, f"{label}: expected 422 got {r.status_code}"


def test_normal_ref_handle_still_works(client, db_session):
    """A valid public ref of ordinary length passes the length gate."""
    from tests.services.scientific_read._factories import make_geometry

    geom = make_geometry(db_session, natoms=2, xyz_text=None)
    r = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert r.status_code == 200


def test_integer_compatibility_handle_still_works(client, db_session):
    """An integer-string handle is still accepted (length well under cap)."""
    from tests.services.scientific_read._factories import make_geometry

    geom = make_geometry(db_session, natoms=2, xyz_text=None)
    r = client.get(f"/api/v1/scientific/geometries/{geom.id}")
    assert r.status_code == 200
