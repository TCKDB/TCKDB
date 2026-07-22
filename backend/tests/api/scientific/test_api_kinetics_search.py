"""API tests for GET/POST /api/v1/scientific/kinetics/search."""

from __future__ import annotations

from app.db.models.common import ScientificOriginKind
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _setup(db_session, *, r: str = "A", p: str = "B"):
    rs = make_species(db_session, smiles=r, inchi_key=next_inchi_key("AKR"))
    ps = make_species(db_session, smiles=p, inchi_key=next_inchi_key("AKP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    k = make_kinetics(db_session, reaction_entry=entry)
    return chem, entry, k


def test_get_returns_200_with_envelope(client, db_session):
    chem, entry, k = _setup(db_session, r="X1", p="Y1")

    resp = client.get(
        "/api/v1/scientific/kinetics/search?reactants=X1&products=Y1"
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["records"]) == 1
    rec = body["records"][0]
    # Phase D: identify records by public ref in default responses.
    assert rec["reaction"]["reaction_ref"] == chem.public_ref
    assert rec["reaction"]["reaction_entry_ref"] == entry.public_ref
    assert rec["kinetics"]["kinetics_ref"] == k.public_ref


def test_post_accepts_json_body(client, db_session):
    _setup(db_session, r="X2", p="Y2")

    resp = client.post(
        "/api/v1/scientific/kinetics/search",
        json={
            "reactants": ["X2"],
            "products": ["Y2"],
            "direction": "either",
            "include": ["provenance"],
        },
    )

    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1


def test_pressure_bar_is_canonical_and_deprecated_alias_conflicts(client, db_session):
    _setup(db_session, r="XP1", p="YP1")

    canonical = client.post(
        "/api/v1/scientific/kinetics/search",
        json={"reactants": ["XP1"], "products": ["YP1"], "pressure_bar": 1.0},
    )
    assert canonical.status_code == 200
    assert canonical.json()["request"]["filter"]["pressure_bar"] == 1.0

    post_conflict = client.post(
        "/api/v1/scientific/kinetics/search",
        json={
            "reactants": ["XP1"],
            "products": ["YP1"],
            "pressure_bar": 1.0,
            "pressure": 10.0,
        },
    )
    get_conflict = client.get(
        "/api/v1/scientific/kinetics/search",
        params={
            "reactants": "XP1",
            "products": "YP1",
            "pressure_bar": 1.0,
            "pressure": 10.0,
        },
    )
    for conflict in (post_conflict, get_conflict):
        assert conflict.status_code == 422
        assert conflict.json()["code"] == "pressure_alias_conflict"
        assert conflict.json()["context"] == {}


def test_ordinary_validation_errors_keep_generic_codes(client):
    post_error = client.post(
        "/api/v1/scientific/kinetics/search",
        json={"reactants": "not-a-list", "products": 4},
    )
    get_error = client.get(
        "/api/v1/scientific/kinetics/search?reactants=XP1&limit=999",
    )

    assert post_error.json()["code"] == "request_validation_error"
    assert get_error.json()["code"] == "request_validation_error"


def test_assessment_summary_is_opt_in_for_get_and_post(client, db_session):
    _setup(db_session, r="Q1", p="Q2")
    base = "/api/v1/scientific/kinetics/search"

    default_record = client.get(
        f"{base}?reactants=Q1&products=Q2"
    ).json()["records"][0]["kinetics"]
    assert "assessments" not in default_record

    get_body = client.get(
        f"{base}?reactants=Q1&products=Q2&include=assessments"
    ).json()
    summary = get_body["records"][0]["kinetics"]["assessments"]
    assert summary["deterministic_trust"]["rubric"] == "computed_kinetics"
    assert summary["reproducibility"]["state"] == "unassessed"

    post = client.post(
        base,
        json={
            "reactants": ["Q1"],
            "products": ["Q2"],
            "include": ["assessments"],
        },
    )
    assert post.status_code == 200, post.text
    assert post.json()["records"][0]["kinetics"]["assessments"] == summary


def test_post_rejects_query_string_filters(client, db_session):
    _setup(db_session, r="X3", p="Y3")

    resp = client.post(
        "/api/v1/scientific/kinetics/search?reactants=X3",
        json={"reactants": ["X3"], "products": ["Y3"]},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_get_repeated_query_params(client, db_session):
    rs1 = make_species(db_session, smiles="R1", inchi_key=next_inchi_key("MR1"))
    rs2 = make_species(db_session, smiles="R2", inchi_key=next_inchi_key("MR2"))
    ps1 = make_species(db_session, smiles="P1", inchi_key=next_inchi_key("MP1"))
    ps2 = make_species(db_session, smiles="P2", inchi_key=next_inchi_key("MP2"))
    chem = make_chem_reaction(db_session, reactants=[rs1, rs2], products=[ps1, ps2])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, s) for s in (rs1, rs2)],
        product_entries=[make_species_entry(db_session, s) for s in (ps1, ps2)],
    )
    make_kinetics(db_session, reaction_entry=entry)

    resp = client.get(
        "/api/v1/scientific/kinetics/search"
        "?reactants=R1&reactants=R2&products=P1&products=P2"
    )
    assert resp.status_code == 200
    assert len(resp.json()["records"]) == 1


def test_get_rejects_direction_exact(client, db_session):
    resp = client.get(
        "/api/v1/scientific/kinetics/search?reactants=A&products=B&direction=exact"
    )
    assert resp.status_code == 422


def test_get_rejects_client_sort(client, db_session):
    _setup(db_session, r="S1", p="S2")
    resp = client.get(
        "/api/v1/scientific/kinetics/search?reactants=S1&products=S2&sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_invalid_temperature_range_returns_422(client, db_session):
    _setup(db_session, r="T1", p="T2")
    resp = client.get(
        "/api/v1/scientific/kinetics/search"
        "?reactants=T1&products=T2&temperature_min=2000&temperature_max=300"
    )
    assert resp.status_code == 422
    assert "invalid_temperature_range" in resp.text


def test_invalid_include_token_returns_422(client, db_session):
    _setup(db_session, r="I1", p="I2")
    resp = client.get(
        "/api/v1/scientific/kinetics/search?reactants=I1&products=I2&include=banana"
    )
    assert resp.status_code == 422


def test_non_ts_backed_provenance_nulls_in_response(client, db_session):
    rs = make_species(db_session, smiles="ER", inchi_key=next_inchi_key("EXR"))
    ps = make_species(db_session, smiles="EP", inchi_key=next_inchi_key("EXP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    resp = client.post(
        "/api/v1/scientific/kinetics/search",
        json={"reactants": ["ER"], "products": ["EP"]},
    )
    body = resp.json()
    p = body["records"][0]["kinetics"]["provenance"]
    # Phase D: integer TS-chain ids are hidden in the default response.
    # The corresponding ref siblings remain visible and are null for
    # non-TS-backed kinetics.
    assert p["transition_state_entry_ref"] is None
    assert p["ts_opt_calculation_ref"] is None
