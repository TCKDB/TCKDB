"""API tests for GET /api/v1/scientific/species-entries/{id}/thermo."""

from __future__ import annotations

from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def _entry(db_session):
    species = make_species(
        db_session, smiles="CC", inchi_key=next_inchi_key("THAPI")
    )
    return make_species_entry(db_session, species)


def test_returns_200_for_valid_species_entry_id(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["species_entry_id"] == entry.id
    assert len(body["records"]) == 1
    assert body["records"][0]["model_kind"] == "scalar"


def test_returns_404_for_missing_species_entry_id(client, db_session):
    resp = client.get("/api/v1/scientific/species-entries/999999/thermo")
    assert resp.status_code == 404
    assert "species_entry not found" in resp.text


def test_rejects_invalid_pagination(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?limit=999"
    )
    assert resp.status_code == 422


def test_returns_nasa_block_when_present(client, db_session):
    entry = _entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
    )
    body = resp.json()
    assert body["records"][0]["model_kind"] == "nasa"
    nasa = body["records"][0]["nasa"]
    assert nasa["t_low"] == 200.0
    assert nasa["t_high"] == 6000.0


def test_rejects_client_sort(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_temperature_min_greater_than_max_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
        "?temperature_min=3000&temperature_max=300"
    )
    assert resp.status_code == 422
    assert "invalid_temperature_range" in resp.text


def test_unknown_include_token_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text
