"""HTTP tests for the ML-dataset export endpoints (the "living RDB7" surface).

Cover auth gating, NDJSON streaming shape, and the key filters for both the
species and reaction ML exports.
"""

from __future__ import annotations

import json

from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    attach_geometry_atoms,
    attach_opt_result,
    attach_output_geometry,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _approve(db_session, record_type, record_id):
    set_review(
        db_session,
        record_type=record_type,
        record_id=record_id,
        status=RecordReviewStatus.approved,
    )


def _seed_species(db_session, *, smiles="O", approve=True):
    sp = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("HT"))
    entry = make_species_entry(db_session, sp)
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    geometry = make_geometry(db_session, natoms=3)
    attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=["O", "H", "H"],
        coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.96], [0.93, 0.0, -0.24]],
    )
    calc = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=geometry,
        role=CalculationGeometryRole.final,
    )
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-76.3)
    if approve:
        _approve(db_session, SubmissionRecordType.species_entry, entry.id)
    return entry


def _parse_ndjson(resp):
    return [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_ml_species_requires_curator(client, db_session):
    resp = client.get(
        "/api/v1/scientific/export/ml/species.ndjson",
        params={"species_ref": "x"},
    )
    assert resp.status_code == 403


def test_ml_reactions_requires_curator(client, db_session):
    resp = client.get(
        "/api/v1/scientific/export/ml/reactions.ndjson",
        params={"reaction_ref": "x"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Species endpoint
# ---------------------------------------------------------------------------


def test_ml_species_streams(client, db_session, login_as, _api_curator_user):
    entry = _seed_species(db_session)
    login_as(_api_curator_user)

    resp = client.get(
        "/api/v1/scientific/export/ml/species.ndjson",
        params={"species_ref": entry.public_ref},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    parsed = _parse_ndjson(resp)
    assert parsed[0]["record_type"] == "manifest"
    assert parsed[0]["dataset"] == "species"
    assert parsed[-1]["record_type"] == "export_summary"
    records = [p for p in parsed if p["record_type"] == "ml_species"]
    assert len(records) == 1
    assert records[0]["geometry"]["symbols"] == ["O", "H", "H"]
    assert records[0]["energies"][0]["level_of_theory"]["label"] == "wb97xd/def2tzvp"


def test_ml_species_empty_seed_is_422(
    client, db_session, login_as, _api_curator_user
):
    login_as(_api_curator_user)
    resp = client.get("/api/v1/scientific/export/ml/species.ndjson")
    assert resp.status_code == 422


def test_ml_species_element_filter(
    client, db_session, login_as, _api_curator_user
):
    entry = _seed_species(db_session, smiles="O")
    login_as(_api_curator_user)

    resp = client.get(
        "/api/v1/scientific/export/ml/species.ndjson",
        params={"species_ref": entry.public_ref, "element": ["C", "H"]},
    )
    assert resp.status_code == 200
    records = [p for p in _parse_ndjson(resp) if p["record_type"] == "ml_species"]
    assert records == []


# ---------------------------------------------------------------------------
# Reaction endpoint
# ---------------------------------------------------------------------------


def test_ml_reactions_streams(client, db_session, login_as, _api_curator_user):
    e_a = _seed_species(db_session, smiles="C")
    e_c = _seed_species(db_session, smiles="CO")
    chem = make_chem_reaction(
        db_session, reactants=[e_a.species], products=[e_c.species]
    )
    rxn_entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c]
    )
    _approve(db_session, SubmissionRecordType.reaction_entry, rxn_entry.id)
    kin = make_kinetics(db_session, reaction_entry=rxn_entry)
    _approve(db_session, SubmissionRecordType.kinetics, kin.id)
    login_as(_api_curator_user)

    resp = client.get(
        "/api/v1/scientific/export/ml/reactions.ndjson",
        params={"reaction_ref": rxn_entry.public_ref},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    parsed = _parse_ndjson(resp)
    assert parsed[0]["dataset"] == "reactions"
    records = [p for p in parsed if p["record_type"] == "ml_reaction"]
    assert len(records) == 1
    assert records[0]["reactants_smiles"] == ["C"]
    assert records[0]["products_smiles"] == ["CO"]
    assert records[0]["kinetics"][0]["kinetics_ref"] == kin.public_ref


def test_ml_reactions_empty_seed_is_422(
    client, db_session, login_as, _api_curator_user
):
    login_as(_api_curator_user)
    resp = client.get("/api/v1/scientific/export/ml/reactions.ndjson")
    assert resp.status_code == 422
