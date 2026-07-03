"""API tests for GET /api/v1/scientific/meta/* (Phase 7 vocabulary reads)."""

from __future__ import annotations

from tests.services.scientific_read._factories import make_lot, make_software


def test_methods_lists_distinct_with_counts(client, db_session):
    make_lot(db_session, method="CCSD(T)", basis="cc-pVTZ")
    make_lot(db_session, method="B3LYP", basis="6-31G(d)")

    body = client.get("/api/v1/scientific/meta/methods").json()

    methods = {r["value"] for r in body["results"]}
    assert {"CCSD(T)", "B3LYP"} <= methods
    for r in body["results"]:
        assert r["count"] >= 1


def test_basis_sets_lists_distinct(client, db_session):
    make_lot(db_session, method="B3LYP", basis="6-311+G(3df,2p)")
    body = client.get("/api/v1/scientific/meta/basis-sets").json()
    assert "6-311+G(3df,2p)" in {r["value"] for r in body["results"]}


def test_software_lists_distinct(client, db_session):
    make_software(db_session, name="Orca")
    body = client.get("/api/v1/scientific/meta/software").json()
    assert "Orca" in {r["value"] for r in body["results"]}


def test_reaction_families_lists_canonical_vocabulary(client, db_session):
    # The seeded canonical families are always present (count >= 0).
    body = client.get("/api/v1/scientific/meta/reaction-families").json()
    assert isinstance(body["results"], list)
    assert len(body["results"]) >= 1
    assert all("value" in r and "count" in r for r in body["results"])
