"""API tests for GET/POST /api/v1/scientific/thermo/search."""

from __future__ import annotations

from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def _seed(db_session, smiles: str = "CC"):
    species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("ATS"))
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    return species, entry, thermo


def test_get_returns_200_with_envelope(client, db_session):
    species, entry, thermo = _seed(db_session, smiles="C[CH2]")

    resp = client.get("/api/v1/scientific/thermo/search?smiles=C[CH2]")

    assert resp.status_code == 200
    body = resp.json()
    assert "request" in body and "review_summary" in body and "records" in body
    assert len(body["records"]) == 1
    rec = body["records"][0]
    # Phase D: identify records by public ref in default responses.
    assert rec["species"]["species_ref"] == species.public_ref
    assert rec["species"]["species_entry_ref"] == entry.public_ref
    assert rec["thermo"]["thermo_ref"] == thermo.public_ref


def test_post_accepts_json_body(client, db_session):
    _seed(db_session, smiles="OCO")

    resp = client.post(
        "/api/v1/scientific/thermo/search",
        json={"smiles": "OCO", "temperature_min": 300, "temperature_max": 3000},
    )

    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1


def test_post_rejects_query_string_filters(client, db_session):
    _seed(db_session, smiles="QQ")

    resp = client.post(
        "/api/v1/scientific/thermo/search?smiles=QQ",
        json={"smiles": "QQ"},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_invalid_temperature_range_returns_422(client, db_session):
    _seed(db_session, smiles="TR")
    resp = client.get(
        "/api/v1/scientific/thermo/search?smiles=TR&temperature_min=3000&temperature_max=300"
    )
    assert resp.status_code == 422
    assert "invalid_temperature_range" in resp.text


def test_an_inverted_temperature_range_names_both_bounds_in_context(
    client, db_session
):
    """The two bounds that are the wrong way round, structured.

    ``invalid_temperature_range`` is a relationship code -- it says two
    values are in the wrong order and names neither -- and its ``context``
    was empty because the raise was a plain ``ValueError``, which has
    nowhere to put facts. Its sibling ``invalid_range`` in
    ``scientific_read/analytics.py`` has always carried its bounds.

    Asserted key by key on the ``(status, code)`` pair. "``context`` is
    non-empty" would pass against a handler that stuffed in a constant.
    """
    _seed(db_session, smiles="TRC")
    resp = client.get(
        "/api/v1/scientific/thermo/search"
        "?smiles=TRC&temperature_min=3000&temperature_max=300"
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "invalid_temperature_range", body
    assert body["context"]["temperature_min_k"] == 3000.0, body["context"]
    assert body["context"]["temperature_max_k"] == 300.0, body["context"]
    # The published prose did not move: only the route into ``code`` did.
    assert body["detail"] == (
        "invalid_temperature_range: temperature_min must be <= temperature_max"
    ), body["detail"]


def test_invalid_include_token_returns_422(client, db_session):
    _seed(db_session, smiles="II")
    resp = client.get(
        "/api/v1/scientific/thermo/search?smiles=II&include=banana"
    )
    assert resp.status_code == 422


def test_missing_identifier_returns_422(client, db_session):
    resp = client.get("/api/v1/scientific/thermo/search")
    assert resp.status_code == 422


def test_statmech_include_token_rejected_at_thermo_search(client, db_session):
    """``include=statmech`` is not legal on ``/scientific/thermo/search``.

    A previous draft accepted it as a no-op placeholder; Option B in
    the v0 include-grammar reconciliation removes no-op acceptance so
    the grammar matches its semantics. If a future phase wires
    statmech data through here, this test should be updated alongside
    the legal-set change.
    """
    _seed(db_session, smiles="O")
    resp = client.get(
        "/api/v1/scientific/thermo/search?smiles=O&include=statmech"
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body and "error" not in body
    assert body["detail"].startswith("unknown_include_token:")
    assert "statmech" in body["detail"]


def test_conformers_include_token_rejected_at_thermo_search(client, db_session):
    """``include=conformers`` is not legal on ``/scientific/thermo/search``.

    Same rationale as the statmech test above.
    """
    _seed(db_session, smiles="N")
    resp = client.get(
        "/api/v1/scientific/thermo/search?smiles=N&include=conformers"
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "detail" in body and "error" not in body
    assert body["detail"].startswith("unknown_include_token:")
    assert "conformers" in body["detail"]
