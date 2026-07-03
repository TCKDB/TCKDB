"""API tests for GET /api/v1/scientific/species/search."""

from __future__ import annotations

from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def test_get_returns_200_with_envelope(client, db_session):
    species = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("API1"))
    make_species_entry(db_session, species)

    resp = client.get("/api/v1/scientific/species/search?smiles=CC")

    assert resp.status_code == 200
    body = resp.json()
    assert "request" in body and "review_summary" in body and "records" in body
    assert "pagination" in body
    # Phase D: default responses identify records by public ref.
    matching = [
        r for r in body["records"] if r["species_ref"] == species.public_ref
    ]
    assert len(matching) == 1


def test_get_parses_collapse_offset_limit(client, db_session):
    # Two spin variants (same smiles, different multiplicity) — distinct
    # species under DR-0031 that both match a by-smiles search, giving two
    # pre-collapse candidates.
    a = make_species(
        db_session, smiles="X", inchi_key=next_inchi_key("CO1"), multiplicity=1
    )
    make_species_entry(db_session, a)
    b = make_species(
        db_session, smiles="X", inchi_key=next_inchi_key("CO2"), multiplicity=3
    )
    make_species_entry(db_session, b)

    resp = client.get(
        "/api/v1/scientific/species/search?smiles=X&collapse=first&offset=0&limit=5"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["limit"] == 5
    assert body["pagination"]["total"] == 2
    assert len(body["records"]) == 1


def test_get_parses_include_repeated_and_comma_forms(client, db_session):
    species = make_species(db_session, smiles="OC", inchi_key=next_inchi_key("INC"))
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)

    # Comma-separated form
    resp_a = client.get(
        "/api/v1/scientific/species/search?smiles=OC&include=thermo,statmech"
    )
    # Repeated form
    resp_b = client.get(
        "/api/v1/scientific/species/search?smiles=OC&include=thermo&include=statmech"
    )
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    body_a = resp_a.json()
    body_b = resp_b.json()
    # Both should populate thermo_summary on the entry.
    assert body_a["records"][0]["entries"][0]["thermo_summary"] is not None
    assert body_b["records"][0]["entries"][0]["thermo_summary"] is not None


def test_get_rejects_client_supplied_sort(client, db_session):
    species = make_species(db_session, smiles="N", inchi_key=next_inchi_key("S1"))
    make_species_entry(db_session, species)

    resp = client.get("/api/v1/scientific/species/search?smiles=N&sort=anything")
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_get_rejects_unknown_include_token(client, db_session):
    species = make_species(db_session, smiles="P", inchi_key=next_inchi_key("S2"))
    make_species_entry(db_session, species)

    resp = client.get("/api/v1/scientific/species/search?smiles=P&include=banana")
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_get_no_identifier_returns_422(client, db_session):
    resp = client.get("/api/v1/scientific/species/search")
    assert resp.status_code == 422


def test_get_unknown_smiles_returns_200_empty_records(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species/search?smiles=DOES_NOT_EXIST_SMILES"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_get_invalid_pagination_limit_rejected(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species/search?smiles=X&limit=999"
    )
    # FastAPI Query(le=200) catches it before reaching the service.
    assert resp.status_code == 422
