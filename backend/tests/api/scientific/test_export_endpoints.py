"""HTTP tests for the bulk-export endpoints (M2/M3).

Cover auth gating, NDJSON streaming shape, and the CHEMKIN zip response.
"""

from __future__ import annotations

import io
import json
import zipfile

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)


def _seed_reaction(db_session):
    def species(smiles):
        sp = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("AP"))
        entry = make_species_entry(db_session, sp)
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        attach_thermo_nasa(db_session, thermo=thermo)
        set_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=thermo.id,
            status=RecordReviewStatus.approved,
        )
        return entry

    e_a = species("C")
    e_c = species("CO")
    chem = make_chem_reaction(
        db_session, reactants=[e_a.species], products=[e_c.species]
    )
    entry = make_reaction_entry(
        db_session, reaction=chem, reactant_entries=[e_a], product_entries=[e_c]
    )
    kin = make_kinetics(db_session, reaction_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=kin.id,
        status=RecordReviewStatus.approved,
    )
    return entry


def test_ndjson_requires_curator(client, db_session):
    # The default client acts as a regular-role user → 403.
    resp = client.get(
        "/api/v1/scientific/export/ndjson", params={"reaction_ref": "x"}
    )
    assert resp.status_code == 403


def test_ndjson_export_streams(client, db_session, login_as, _api_curator_user):
    entry = _seed_reaction(db_session)
    login_as(_api_curator_user)

    resp = client.get(
        "/api/v1/scientific/export/ndjson",
        params={"reaction_ref": entry.public_ref},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["record_type"] == "manifest"
    assert parsed[-1]["record_type"] == "export_summary"
    kinds = [p["record_type"] for p in parsed]
    assert kinds.count("species") == 2
    assert kinds.count("reaction") == 1


def test_ndjson_empty_seed_is_422(client, db_session, login_as, _api_curator_user):
    login_as(_api_curator_user)
    resp = client.get("/api/v1/scientific/export/ndjson")
    assert resp.status_code == 422


def test_chemkin_export_returns_zip(
    client, db_session, login_as, _api_curator_user
):
    entry = _seed_reaction(db_session)
    login_as(_api_curator_user)

    resp = client.post(
        "/api/v1/scientific/export/chemkin",
        json={"seed": {"reaction_refs": [entry.public_ref]}},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert {"chem.inp", "therm.dat", "tran.dat", "manifest.json"} <= names
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["counts"]["species"] == 2
    assert "REACTIONS" in zf.read("chem.inp").decode()


def test_chemkin_requires_curator(client, db_session):
    resp = client.post(
        "/api/v1/scientific/export/chemkin",
        json={"seed": {"reaction_refs": ["x"]}},
    )
    assert resp.status_code == 403
