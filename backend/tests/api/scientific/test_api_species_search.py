"""API tests for GET /api/v1/scientific/species/search."""

from __future__ import annotations

from app.api.config import settings
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


def test_collapse_first_offset_one_returns_empty(client, db_session):
    species = make_species(
        db_session, smiles="X_OFFSET", inchi_key=next_inchi_key("APIOFF")
    )
    make_species_entry(db_session, species)

    response = client.get(
        "/api/v1/scientific/species/search",
        params={"smiles": "X_OFFSET", "collapse": "first", "offset": 1},
    )

    assert response.status_code == 200
    assert response.json()["records"] == []
    assert response.json()["pagination"]["total"] == 1


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


def test_get_limit_above_the_framework_bound_is_rejected_by_the_framework(
    client, db_session
):
    """``Query(le=200)`` is the outer bound, and it is not the service.

    Kept, and now asserted precisely: this request never reaches
    ``validate_pagination``, so it says nothing about the pagination
    codes. The two tests below are the ones that do.
    """
    resp = client.get("/api/v1/scientific/species/search?smiles=X&limit=999")
    assert resp.status_code == 422
    assert resp.json()["code"] == "request_validation_error"


def test_get_limit_above_the_service_cap_is_rejected_by_the_service(
    client, db_session, monkeypatch
):
    """A limit inside ``Query``'s bound but above the hosted cap.

    The two caps are independent: ``MAX_LIMIT`` is the schema's, and
    ``settings.public_max_limit`` is the deployment's. They are equal in
    the shipped configuration, which is why no request could reach this
    branch through a GET route until the hosted cap is lowered -- and why
    the branch went untested for as long as it did.
    """
    monkeypatch.setattr(settings, "public_max_limit", 10)
    resp = client.get("/api/v1/scientific/species/search?smiles=X&limit=50")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "limit_too_large"


def test_get_offset_beyond_the_shipped_deep_paging_cap_is_rejected(
    client, db_session
):
    """No monkeypatch: the shipped ``public_max_offset`` is the bound.

    ``offset`` has no ``le`` on any route, so this is reachable against
    the configuration TCKDB actually runs -- which is the point of
    asserting it here rather than under a lowered cap like its siblings.

    Both sides of the boundary are asserted. An expected value derived
    from the same constant the guard reads will follow that constant
    wherever it moves; pinning the cap itself as *accepted* is what stops
    this passing if the comparison is widened.
    """
    base = "/api/v1/scientific/species/search?smiles=X&offset="

    allowed = client.get(f"{base}{settings.public_max_offset}")
    assert allowed.status_code == 200, allowed.text

    resp = client.get(f"{base}{settings.public_max_offset + 1}")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "offset_too_large"
