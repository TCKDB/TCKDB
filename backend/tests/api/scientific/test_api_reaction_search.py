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


# ---------------------------------------------------------------------------
# match=contains (default) vs match=exact, over HTTP
# ---------------------------------------------------------------------------


def _setup_two_by_two(db_session, prefix: str):
    """A + B <=> C + D, so every partial query has something to be partial about."""
    species = {
        role: make_species(
            db_session, smiles=f"{prefix}_{role}", inchi_key=next_inchi_key(prefix)
        )
        for role in ("A", "B", "C", "D")
    }
    chem = make_chem_reaction(
        db_session,
        reactants=[species["A"], species["B"]],
        products=[species["C"], species["D"]],
    )
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[
            make_species_entry(db_session, species[r]) for r in ("A", "B")
        ],
        product_entries=[
            make_species_entry(db_session, species[r]) for r in ("C", "D")
        ],
    )


def test_get_reactants_only_returns_the_reaction(client, db_session):
    """"What consumes this species?" over HTTP — 200 with a record, not 200 with none."""
    _setup_two_by_two(db_session, "HCT")

    resp = client.get("/api/v1/scientific/reactions/search?reactants=HCT_A")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["records"]) == 1
    assert body["pagination"]["total"] == 1
    assert {p["smiles"] for p in body["records"][0]["reactants"]} == {
        "HCT_A",
        "HCT_B",
    }


def test_get_products_only_returns_the_reaction(client, db_session):
    _setup_two_by_two(db_session, "HPO")

    resp = client.get("/api/v1/scientific/reactions/search?products=HPO_C")

    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1


def test_get_match_exact_rejects_the_partial_query(client, db_session):
    """The migration path: ``match=exact`` restores the pre-``match`` answer."""
    _setup_two_by_two(db_session, "HEX")

    contains = client.get(
        "/api/v1/scientific/reactions/search?reactants=HEX_A&products=HEX_C"
    )
    exact = client.get(
        "/api/v1/scientific/reactions/search"
        "?reactants=HEX_A&products=HEX_C&match=exact"
    )

    assert len(contains.json()["records"]) == 1
    assert exact.status_code == 200
    assert exact.json()["records"] == []


def test_get_match_exact_still_matches_the_whole_equation(client, db_session):
    _setup_two_by_two(db_session, "HEW")

    resp = client.get(
        "/api/v1/scientific/reactions/search"
        "?reactants=HEW_A&reactants=HEW_B"
        "&products=HEW_C&products=HEW_D&match=exact"
    )

    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1


def test_get_rejects_unknown_match_value(client, db_session):
    _setup_two_by_two(db_session, "HBAD")

    resp = client.get(
        "/api/v1/scientific/reactions/search?reactants=HBAD_A&match=subset"
    )

    assert resp.status_code == 422


def test_post_accepts_match_in_body(client, db_session):
    _setup_two_by_two(db_session, "HPB")

    resp = client.post(
        "/api/v1/scientific/reactions/search",
        json={"reactants": ["HPB_A"], "match": "contains"},
    )

    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1


def test_response_echoes_the_match_mode(client, db_session):
    _setup_two_by_two(db_session, "HECH")

    resp = client.get("/api/v1/scientific/reactions/search?reactants=HECH_A")

    assert resp.json()["request"]["filter"]["match"] == "contains"


def test_get_direction_reverse_composes_with_contains(client, db_session):
    """``direction`` and ``match`` are independent axes; check they compose."""
    _setup_two_by_two(db_session, "HDIR")

    reverse_hit = client.get(
        "/api/v1/scientific/reactions/search?reactants=HDIR_C&direction=reverse"
    )
    reverse_miss = client.get(
        "/api/v1/scientific/reactions/search?reactants=HDIR_A&direction=reverse"
    )

    assert len(reverse_hit.json()["records"]) == 1
    assert reverse_hit.json()["records"][0]["matched_direction"] == "reverse"
    assert reverse_miss.json()["records"] == []
