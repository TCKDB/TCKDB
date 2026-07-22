"""API tests for GET/POST /api/v1/scientific/reactions/search."""

from __future__ import annotations

from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _setup(db_session, *, reactant_smiles: str, product_smiles: str):
    rs = make_species(
        db_session, smiles=reactant_smiles, inchi_key=next_inchi_key("RA")
    )
    ps = make_species(
        db_session, smiles=product_smiles, inchi_key=next_inchi_key("RB")
    )
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def test_get_handles_repeated_reactants_and_products(client, db_session):
    rs1 = make_species(db_session, smiles="A1", inchi_key=next_inchi_key("MR1"))
    rs2 = make_species(db_session, smiles="A2", inchi_key=next_inchi_key("MR2"))
    ps1 = make_species(db_session, smiles="B1", inchi_key=next_inchi_key("MP1"))
    ps2 = make_species(db_session, smiles="B2", inchi_key=next_inchi_key("MP2"))
    chem = make_chem_reaction(db_session, reactants=[rs1, rs2], products=[ps1, ps2])
    make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, s) for s in (rs1, rs2)],
        product_entries=[make_species_entry(db_session, s) for s in (ps1, ps2)],
    )

    resp = client.get(
        "/api/v1/scientific/reactions/search"
        "?reactants=A1&reactants=A2&products=B1&products=B2"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["records"]) == 1
    rec = body["records"][0]
    assert {p["smiles"] for p in rec["reactants"]} == {"A1", "A2"}
    assert {p["smiles"] for p in rec["products"]} == {"B1", "B2"}


def test_post_accepts_json_body(client, db_session):
    _setup(db_session, reactant_smiles="P1", product_smiles="P2")

    resp = client.post(
        "/api/v1/scientific/reactions/search",
        json={
            "reactants": ["P1"],
            "products": ["P2"],
            "direction": "either",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["records"]) == 1
    assert body["records"][0]["reactants"][0]["smiles"] == "P1"


def test_collapse_first_offset_one_returns_empty(client, db_session):
    _setup(db_session, reactant_smiles="ROFF_A", product_smiles="ROFF_B")

    response = client.get(
        "/api/v1/scientific/reactions/search",
        params={
            "reactants": "ROFF_A",
            "products": "ROFF_B",
            "collapse": "first",
            "offset": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["records"] == []
    assert response.json()["pagination"]["total"] == 1


def test_post_rejects_query_string_filters(client, db_session):
    _setup(db_session, reactant_smiles="Q1", product_smiles="Q2")

    resp = client.post(
        "/api/v1/scientific/reactions/search?reactants=Q1",
        json={"reactants": ["Q1"], "products": ["Q2"]},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_get_rejects_direction_exact(client, db_session):
    _setup(db_session, reactant_smiles="X1", product_smiles="X2")

    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=X1&products=X2&direction=exact"
    )
    # FastAPI rejects at enum-validation time → 422.
    assert resp.status_code == 422


def test_get_rejects_client_sort(client, db_session):
    _setup(db_session, reactant_smiles="Y1", product_smiles="Y2")

    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=Y1&products=Y2&sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_post_sort_in_body_rejected(client, db_session):
    _setup(db_session, reactant_smiles="Z1", product_smiles="Z2")

    resp = client.post(
        "/api/v1/scientific/reactions/search",
        json={"reactants": ["Z1"], "products": ["Z2"], "sort": "anything"},
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_get_empty_result_returns_200(client, db_session):
    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=NEVER_A&products=NEVER_B"
    )
    assert resp.status_code == 200
    assert resp.json()["records"] == []


def test_get_includes_kinetics_count_when_available(client, db_session):
    entry = _setup(db_session, reactant_smiles="K1", product_smiles="K2")
    make_kinetics(db_session, reaction_entry=entry)

    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=K1&products=K2"
    )
    assert resp.status_code == 200
    avail = resp.json()["records"][0]["availability"]
    assert avail["has_kinetics"] is True
    assert avail["kinetics_count"] == 1
