"""API tests for GET/POST /api/v1/scientific/species-calculations/search."""

from __future__ import annotations

from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import (
    attach_sp_result,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _seed(db_session, *, smiles: str = "CC"):
    species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("ASC"))
    entry = make_species_entry(db_session, species)
    return species, entry


def test_get_returns_200_with_envelope(client, db_session):
    species, entry = _seed(db_session, smiles="C[CH2]")
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=C[CH2]"
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "request" in body and "review_summary" in body and "records" in body
    assert "pagination" in body
    assert len(body["records"]) == 1
    rec = body["records"][0]
    # Phase D: identify records by public ref in default responses.
    assert rec["species"]["species_ref"] == species.public_ref
    assert rec["species"]["species_entry_ref"] == entry.public_ref
    assert rec["calculation"]["calculation_ref"] == calc.public_ref


def test_post_accepts_json_body(client, db_session):
    _, entry = _seed(db_session, smiles="OCO")
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    resp = client.post(
        "/api/v1/scientific/species-calculations/search",
        json={"smiles": "OCO", "calculation_type": "sp"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1


def test_post_rejects_query_string_filters(client, db_session):
    _, entry = _seed(db_session, smiles="QQ")
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    resp = client.post(
        "/api/v1/scientific/species-calculations/search?smiles=QQ",
        json={"smiles": "QQ"},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_lowest_energy_with_sp_returns_lowest_first(client, db_session):
    _, entry = _seed(db_session, smiles="LE")
    high = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_sp_result(db_session, calculation=high, electronic_energy_hartree=-100.0)
    low = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_sp_result(db_session, calculation=low, electronic_energy_hartree=-200.0)

    resp = client.get(
        "/api/v1/scientific/species-calculations/search"
        "?smiles=LE&calculation_type=sp&ranking=lowest_energy&collapse=first"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["records"]) == 1
    assert body["records"][0]["calculation"]["calculation_ref"] == low.public_ref
    # pre-collapse total
    assert body["pagination"]["total"] == 2


def test_lowest_energy_with_freq_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-calculations/search"
        "?smiles=X&calculation_type=freq&ranking=lowest_energy"
    )
    assert resp.status_code == 422
    assert "unsupported_ranking_for_calculation_type" in resp.text


def test_404_on_unknown_species_entry_id(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?species_entry_id=999999"
    )
    assert resp.status_code == 404


def test_empty_records_on_unknown_smiles(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=DOES_NOT_EXIST"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_unknown_include_token_returns_422(client, db_session):
    _, entry = _seed(db_session, smiles="II")
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=II&include=banana"
    )
    assert resp.status_code == 422


def test_known_but_illegal_include_token_returns_422(client, db_session):
    """include=kinetics is legal at /scientific/reactions/search but not here."""
    _, entry = _seed(db_session, smiles="IK")
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=IK&include=kinetics"
    )
    assert resp.status_code == 422


def test_client_supplied_sort_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=X&sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_invalid_calculation_type_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=X&calculation_type=banana"
    )
    assert resp.status_code == 422


def test_openapi_exposes_endpoint_with_get_and_post(client):
    resp = client.get("/openapi.json")
    paths = resp.json()["paths"]
    assert "/api/v1/scientific/species-calculations/search" in paths
    methods = paths["/api/v1/scientific/species-calculations/search"]
    assert "get" in methods
    assert "post" in methods
    for op in methods.values():
        assert "scientific" in op.get("tags", [])


def test_method_basis_filter_via_calculation_lot(client, db_session):
    _, entry = _seed(db_session, smiles="MB")
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    in_lot = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    resp = client.get(
        "/api/v1/scientific/species-calculations/search"
        "?smiles=MB&method=wb97xd&basis=def2tzvp"
    )
    body = resp.json()
    assert {
        r["calculation"]["calculation_ref"] for r in body["records"]
    } == {in_lot.public_ref}
