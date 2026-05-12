"""Tests for F18 — NotFoundError messages must not leak integer ids."""

from __future__ import annotations


def test_scientific_404_for_unknown_integer_id_omits_id(client):
    """Public-handle 404 on the scientific surface returns a generic body."""
    r = client.get("/api/v1/scientific/reaction-entries/99999999/full")
    assert r.status_code == 404
    body = r.json()
    detail = body["detail"]
    # The integer must not be echoed back.
    assert "99999999" not in detail
    assert "reaction_entry" in detail  # the resource label remains useful


def test_scientific_404_for_unknown_ref_keeps_ref_in_detail(client):
    """Refs are public-by-design; echoing them back is intentional."""
    bogus_ref = "rxe_zzzzzzzzzzzzzzzzzzzzzzzzzz"
    r = client.get(f"/api/v1/scientific/reaction-entries/{bogus_ref}/full")
    assert r.status_code == 404
    body = r.json()
    assert bogus_ref in body["detail"]


def test_scientific_404_for_geometry_integer_id_omits_id(client):
    r = client.get("/api/v1/scientific/geometries/99999999")
    assert r.status_code == 404
    body = r.json()
    assert "99999999" not in body["detail"]
    assert "geometry" in body["detail"]


def test_wrong_prefix_handle_returns_422_not_404(client):
    """Sanity: malformed/wrong-prefix handles still raise 422 (not the new sanitized 404)."""
    r = client.get(
        "/api/v1/scientific/reaction-entries/spe_abcdefghijklmnopqrstuvwxyz/full"
    )
    assert r.status_code == 422
    assert "handle_type_mismatch" in r.json()["detail"]
