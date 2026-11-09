"""Thermo imported through the CHEMKIN adapter is selectable and reports who declared its target."""

from __future__ import annotations

from collections import Counter

from app.db.models.common import ThermoDeclarationSource, ThermoTargetKind
from app.db.models.thermo import Thermo
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.workflows.thermo import persist_thermo_upload
from tests.integration.test_chemkin_round_trip_real import FORMIC, _import_payloads

EQUILIBRIUM = {"target": {"kind": "equilibrium_ensemble"}}


def _select(client, row):
    return client.post(
        f"/api/v1/scientific/species-entries/{row.species_entry.public_ref}/thermo/select", json=EQUILIBRIUM
    )


def test_every_formic_acid_species_selects_its_sole_candidate(db_session, client):
    _mech, _resolved, _normalized, built = _import_payloads(FORMIC)
    assert len(built.thermo) == 123

    outcomes: Counter[str] = Counter()
    for payload in built.thermo:
        row = persist_thermo_upload(db_session, ThermoUploadRequest(**payload), review_policy=None)
        assert row.thermodynamic_target_kind is ThermoTargetKind.equilibrium_ensemble
        assert row.thermodynamic_target_declared_by is ThermoDeclarationSource.chemkin_format

        response = _select(client, row)
        assert response.status_code == 200, response.text
        body = response.json()
        outcomes[body["outcome"]] += 1
        assert body["selection"]["thermo_ref"] == row.public_ref
        (candidate,) = body["candidates"]
        assert candidate["target_declared_by"] == "chemkin_format"

    assert outcomes == {"sole_eligible_candidate": 123}


def test_the_read_says_the_chemkin_format_declared_the_target(db_session, client):
    _mech, _resolved, _normalized, built = _import_payloads(FORMIC)
    row = persist_thermo_upload(db_session, ThermoUploadRequest(**built.thermo[0]), review_policy=None)

    records = client.get(f"/api/v1/scientific/species-entries/{row.species_entry.public_ref}/thermo").json()["records"]
    assert [r["thermodynamic_target"] for r in records] == [
        {"kind": "equilibrium_ensemble", "conformer_group_ref": None, "declared_by": "chemkin_format"}
    ]


def test_a_payload_without_a_target_still_stores_no_target_and_the_default_source(db_session):
    _mech, _resolved, _normalized, built = _import_payloads(FORMIC)
    payload = {k: v for k, v in built.thermo[0].items() if k != "thermodynamic_target"}
    row = persist_thermo_upload(db_session, ThermoUploadRequest(**payload), review_policy=None)
    assert row.thermodynamic_target_kind is None
    assert db_session.get(Thermo, row.id).thermodynamic_target_declared_by is ThermoDeclarationSource.depositor
