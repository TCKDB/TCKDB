"""Structure determinations on the computed-species and computed-reaction bundles, through the real API.

The bundles are the main producer path, so a determination states there what it states on the standalone routes: its
owner is the conformer (or saddle) it sits on, it pins the bundle's own calculations by their bundle-global key, a basin
claim pins only calculations anchored to its own observation, and an undeclared key is refused with the shared code.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from tests.api.test_api_bundle_extras_622 import _calc_ids, _ethane_bundle, _reaction_bundle
from tests.api.test_api_bundle_extras_622_sources import _two_conformer_ethane
from tests.workflows.test_computed_reaction_upload import _minimal_payload

SPECIES = "/api/v1/uploads/computed-species"
REACTION = "/api/v1/uploads/computed-reaction"
WTR = {"name": "ARC", "version": "1.1.0"}


def _determination(opt: str, sp: str, freq: str, **changes) -> dict:
    declared = {
        "key": f"{opt}-basin",
        "target_kind": "conformer_basin",
        "quantity": "electronic_energy",
        "evaluated_geometry": {"calculation_key": opt},
        "sources": [
            {"role": "geometry_optimization", "calculation_key": opt},
            {"role": "energy", "calculation_key": sp},
            {"role": "curvature", "calculation_key": freq},
        ],
        "workflow_tool_release": WTR,
    }
    declared.update(changes)
    return declared


def _rows(db_session) -> list[StructureDetermination]:
    return list(db_session.scalars(select(StructureDetermination).order_by(StructureDetermination.id)))


def _sources(db_session, determination: StructureDetermination) -> dict[str, int]:
    return {
        s.role.value: s.calculation_id
        for s in db_session.scalars(
            select(StructureDeterminationSource).where(StructureDeterminationSource.determination_id == determination.id)
        )
    }


def test_a_species_bundle_conformer_states_a_basin_over_its_own_calculations(client, db_session):
    payload = _ethane_bundle()
    payload["conformers"][0]["structure_determinations"] = [_determination("opt0", "sp0", "freq0")]
    resp = client.post(SPECIES, json=payload)
    assert resp.status_code == 201, resp.text[:800]
    ids = _calc_ids(resp)
    (row,) = _rows(db_session)
    assert row.target_kind.value == "conformer_basin" and row.conformer_observation_id is not None
    assert row.species_entry_id is not None and row.transition_state_entry_id is None
    assert _sources(db_session, row) == {
        "geometry_optimization": ids["opt0"],
        "energy": ids["sp0"],
        "curvature": ids["freq0"],
    }


def test_each_conformer_of_a_species_bundle_owns_its_own_basin(client, db_session):
    payload = _two_conformer_ethane()
    payload["conformers"][0]["structure_determinations"] = [
        _determination("opt0", "sp0", "freq0")
    ]
    payload["conformers"][1]["structure_determinations"] = [
        _determination("opt1", "sp1", "opt1", sources=[
            {"role": "geometry_optimization", "calculation_key": "opt1"},
            {"role": "energy", "calculation_key": "sp1"},
        ])
    ]
    resp = client.post(SPECIES, json=payload)
    assert resp.status_code == 201, resp.text[:800]
    rows = _rows(db_session)
    assert len(rows) == 2 and len({r.conformer_observation_id for r in rows}) == 2


def test_a_basin_cannot_pin_a_calculation_of_another_conformer_of_the_same_bundle(client, db_session):
    payload = _two_conformer_ethane()
    # The key resolves, so only the observation rule can catch it: sp1 belongs to conformer c1.
    payload["conformers"][0]["structure_determinations"] = [_determination("opt0", "sp1", "freq0")]
    resp = client.post(SPECIES, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "structure_determination_mismatch" and body["context"]["reason"] == "observation"
    assert _rows(db_session) == []


def test_an_undeclared_key_in_a_species_bundle_is_refused_with_the_shared_code_before_anything_is_written(client, db_session):
    payload = _ethane_bundle()
    payload["conformers"][0]["structure_determinations"] = [_determination("opt0", "nope", "freq0")]
    resp = client.post(SPECIES, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "calculation_key_undeclared"
    assert body["context"]["key"] == "nope" and "sp0" in body["context"]["declared_keys"]
    assert _rows(db_session) == []


def test_a_reaction_bundle_species_conformer_states_a_basin_and_a_sibling_species_is_not_in_scope(client, db_session):
    bundle = _reaction_bundle()
    bundle["species"][1]["conformers"][0]["structure_determinations"] = [
        _determination("h2-opt", "h2-sp", "h2-freq")
    ]
    resp = client.post(REACTION, json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    keys = resp.json()["calculation_keys"]
    (row,) = _rows(db_session)
    assert row.target_kind.value == "conformer_basin"
    assert _sources(db_session, row) == {
        "geometry_optimization": keys["h2-opt"],
        "energy": keys["h2-sp"],
        "curvature": keys["h2-freq"],
    }
    # The key resolves in the bundle, but it belongs to another species: refused on its owner.
    crossed = _reaction_bundle()
    crossed["species"][1]["conformers"][0]["structure_determinations"] = [
        _determination("h2-opt", "h-sp", "h2-freq")
    ]
    refused = client.post(REACTION, json=crossed)
    assert refused.status_code == 422, refused.text[:800]
    assert refused.json()["code"] == "structure_determination_mismatch"
    assert refused.json()["context"]["reason"] == "owner"


def test_a_reaction_bundle_saddle_is_owned_by_its_transition_state_entry(client, db_session):
    payload = _minimal_payload()
    payload["transition_state"]["structure_determinations"] = [
        {
            "key": "saddle",
            "target_kind": "saddle_point",
            "evaluated_geometry": {"calculation_key": "ts-opt"},
            "sources": [
                {"role": "geometry_optimization", "calculation_key": "ts-opt"},
                {"role": "curvature", "calculation_key": "ts-freq"},
            ],
            "workflow_tool_release": WTR,
        }
    ]
    resp = client.post(REACTION, json=payload)
    assert resp.status_code == 201, resp.text[:800]
    keys = resp.json()["calculation_keys"]
    (row,) = _rows(db_session)
    assert row.target_kind.value == "saddle_point" and row.quantity is None
    assert row.transition_state_entry_id is not None and row.species_entry_id is None
    assert _sources(db_session, row) == {"geometry_optimization": keys["ts-opt"], "curvature": keys["ts-freq"]}


def test_a_bundle_that_states_no_determination_makes_none(client, db_session):
    assert client.post(SPECIES, json=_ethane_bundle()).status_code == 201
    assert client.post(REACTION, json=_reaction_bundle()).status_code == 201
    assert _rows(db_session) == []
