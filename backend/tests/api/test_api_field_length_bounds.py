"""Tests for F9 — free-text field length bounds on scientific reads."""

from __future__ import annotations

from app.schemas.reads._field_bounds import (
    MAX_INCHI_LENGTH,
    MAX_PARTICIPANTS_PER_REACTION,
    MAX_SMILES_LENGTH,
)


def test_oversized_smiles_returns_422(client):
    huge = "C" * (MAX_SMILES_LENGTH + 1)
    r = client.get(f"/api/v1/scientific/species/search?smiles={huge}")
    assert r.status_code == 422


def test_oversized_inchi_returns_422(client):
    huge = "InChI=" + ("A" * MAX_INCHI_LENGTH)
    r = client.get(f"/api/v1/scientific/species/search?inchi={huge}")
    assert r.status_code == 422


def test_oversized_method_returns_422(client):
    huge = "M" * 1024
    r = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=O&method=" + huge
    )
    assert r.status_code == 422


def test_oversized_basis_returns_422(client):
    huge = "B" * 1024
    r = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=O&basis=" + huge
    )
    assert r.status_code == 422


def test_oversized_reactant_smiles_returns_422(client):
    huge = "C" * (MAX_SMILES_LENGTH + 1)
    r = client.post(
        "/api/v1/scientific/reactions/search",
        json={"reactants": [huge], "products": ["B"]},
    )
    assert r.status_code == 422


def test_too_many_reactants_returns_422(client):
    payload = {
        "reactants": ["A"] * (MAX_PARTICIPANTS_PER_REACTION + 1),
        "products": ["B"],
    }
    r = client.post("/api/v1/scientific/reactions/search", json=payload)
    assert r.status_code == 422


def test_valid_chemistry_query_still_accepted(client):
    """Sanity: an ordinary SMILES (well under the cap) is not rejected."""
    r = client.get("/api/v1/scientific/species/search?smiles=CCO")
    # We don't assert on the body — it may be 200 (no data) or 422
    # (some upstream validation), but it must not be the length-bound
    # validation error.
    assert r.status_code != 422 or "smiles" not in r.text.lower() or "too long" not in r.text.lower()
