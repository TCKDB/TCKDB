"""API tests for GET /api/v1/scientific/reaction-entries/{id}/kinetics."""

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


def _entry(db_session):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key("KAPI1"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key("KAPI2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def test_returns_200_for_valid_reaction_entry_id(client, db_session):
    entry = _entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reaction_entry_id"] == entry.id
    assert len(body["records"]) == 1


def test_returns_404_for_missing_reaction_entry_id(client, db_session):
    resp = client.get("/api/v1/scientific/reaction-entries/999999/kinetics")
    assert resp.status_code == 404
    assert "reaction_entry not found" in resp.text


def test_rejects_temperature_min_greater_than_max(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
        "?temperature_min=2000&temperature_max=300"
    )
    assert resp.status_code == 422
    assert "invalid_temperature_range" in resp.text


def test_rejects_client_sort(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics?sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_non_ts_backed_provenance_returns_nulls(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
    )
    assert resp.status_code == 200
    record = resp.json()["records"][0]
    p = record["provenance"]
    assert p["transition_state_entry_id"] is None
    assert p["ts_opt_calculation_id"] is None
    assert p["ts_freq_calculation_id"] is None
    assert p["ts_sp_calculation_id"] is None
    assert p["path_search"] is None
    assert p["irc"] is None
    # Non-TS provenance keys are still present in the JSON shape.
    assert "literature" in p
    assert "software_release" in p
    assert "workflow_tool_release" in p


def test_temperature_coverage_metadata_present(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session, reaction_entry=entry, tmin_k=300.0, tmax_k=1500.0
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
        "?temperature_min=300&temperature_max=2000"
    )
    cov = resp.json()["records"][0]["temperature_coverage"]
    assert cov["covers_requested_range"] is False
    assert cov["extrapolation_distance_k"] == 500.0


def test_unknown_include_token_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text
