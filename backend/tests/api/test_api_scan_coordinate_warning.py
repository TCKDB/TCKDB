"""A scan whose stored coordinate values disagree with its own geometries earns a warning (#835, ADR 0020).

``scan_point_coordinate_disagrees_with_geometry`` is the deposit-time form of the conformance check in
``app.services.scan_coordinate_conformance``. The pair below is the proof that it speaks when it should and is silent
when it should be: the real ARC payloads under ``tests/fixtures/arc_runs`` still hold ADR 0019's superseded
relative-sweep convention (they are kept in it on purpose), and the QCSchema torsion-drive fixtures hold the coordinate
itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from tckdb_schemas.deposit_warning_messages import render_message

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from tests.api.test_api_auth import _hydrogen_conformer_payload

CODE = "scan_point_coordinate_disagrees_with_geometry"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_ARC_SCAN_1 = (
    _FIXTURES
    / "arc_runs"
    / "scan_1"
    / "tckdb_payloads"
    / "computed_reaction"
    / "rmg_rxn_1142_r1_sbr-O-sbr-rmg_rxn_1142_r2_CCCC-rmg_rxn_1142_p1_sbr-OH-sbr-rmg_rxn_1142_p2_sbr-CH2-sbrCCC"
)
_CONFORMERS = "/api/v1/uploads/conformers"


def _warnings(response) -> list[dict]:
    assert response.status_code in (200, 201), response.text[:800]
    return [w for w in response.json()["warnings"] if w["code"] == CODE]


def test_a_legacy_arc_scan_fixture_earns_the_warning(client) -> None:
    meta = json.loads(_ARC_SCAN_1.with_suffix(".meta.json").read_text())
    payload = json.loads(_ARC_SCAN_1.with_suffix(".payload.json").read_text())
    endpoint = meta["endpoint"] if meta["endpoint"].startswith("/api/") else f"/api/v1{meta['endpoint']}"
    response = client.post(endpoint, json=payload)
    found = _warnings(response)
    assert found, "the old-convention ARC scan fixture should earn the warning"
    for warning in found:
        context = warning["context"]
        assert context["pattern"] == "consistent_with_legacy_relative_axis"
        assert context["coordinate_kind"] == "dihedral"
        assert context["n_points_disagreeing"] >= 1
        assert context["max_residual"] > context["tolerance"] > 0
        assert warning["message"] == render_message(CODE, context)
        assert "start" in warning["message"], "the sentence names the cause a producer should look for"

    # Each warning is on a scan calculation of this deposit, not on the submission as a whole.
    session = client._db_session
    scan_ids = set(session.scalars(select(Calculation.id).where(Calculation.type == CalculationType.scan)))
    rows = session.scalars(select(DepositWarning).where(DepositWarning.code == CODE)).all()
    assert len(rows) == len(found)
    for row in rows:
        assert row.record_type is SubmissionRecordType.calculation
        assert row.record_id in scan_ids


def test_a_qcschema_scan_fixture_earns_none(client, monkeypatch) -> None:
    monkeypatch.setattr("app.services.artifact_persistence.store_artifact", lambda content, sha256: f"s3://t/{sha256}")
    payload = json.loads((_FIXTURES / "qcschema_scan" / "torsiondrive_v2" / "payload.json").read_text())
    response = client.post("/api/v1/uploads/computed-species", json=payload, headers={"Idempotency-Key": "scan-warning-negative"})
    assert response.status_code == 201, response.text[:800]
    assert _warnings(response) == []
    assert client._db_session.scalars(select(Calculation).where(Calculation.type == CalculationType.scan)).first()


def _h2_bond_scan(offset: float) -> dict:
    """A three-point bond scan of H2 whose stored values are the real distances plus ``offset`` angstrom."""
    distances = [0.70, 0.74, 0.78]
    return {
        "dimension": 1,
        "is_relaxed": False,
        "coordinates": [
            {
                "coordinate_index": 1,
                "coordinate_kind": "bond",
                "atom1_index": 1,
                "atom2_index": 2,
                "step_count": 3,
                "step_size": 0.04,
                "start_value": 0.70,
                "end_value": 0.78,
                "value_unit": "angstrom",
            }
        ],
        "points": [
            {
                "point_index": i + 1,
                "electronic_energy_hartree": -1.17 - 1e-3 * i,
                "geometry": {"xyz_text": f"2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 {d}"},
                "coordinate_values": [
                    {"coordinate_index": 1, "coordinate_value": round(d + offset, 6), "value_unit": "angstrom"}
                ],
            }
            for i, d in enumerate(distances)
        ],
    }


def h2_scan_conformer(offset: float) -> dict:
    payload = _hydrogen_conformer_payload()
    payload["species_entry"] = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
    payload["geometry"] = {"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"}
    payload["calculation"]["type"] = "scan"
    payload["calculation"]["scan_result"] = _h2_bond_scan(offset)
    return payload


def test_a_scan_with_offset_values_warns_with_the_numbers_it_measured(client) -> None:
    response = client.post(_CONFORMERS, json=h2_scan_conformer(0.5))
    (warning,) = _warnings(response)
    context = warning["context"]
    assert context["coordinate_index"] == 1
    assert context["coordinate_kind"] == "bond"
    assert context["n_points_disagreeing"] == context["n_points_checked"] == 3
    assert context["max_residual"] == 0.5
    assert 0 < context["tolerance"] < 0.1
    assert warning["field"] == "scan_result"
    assert warning["message"] == render_message(CODE, context)


def test_a_scan_whose_values_match_its_geometries_earns_none(client) -> None:
    assert _warnings(client.post(_CONFORMERS, json=h2_scan_conformer(0.0))) == []


def test_every_workflow_that_reports_named_composite_deposits_reports_scan_coordinates_too() -> None:
    """The check reads flushed rows, so each route that persists calculations has to run it; a new one cannot forget."""
    workflows = Path(__file__).resolve().parents[2] / "app" / "workflows"
    reporting = {p.name for p in workflows.glob("*.py") if "collect_named_composite_deposit_warnings(" in p.read_text()}
    scanning = {p.name for p in workflows.glob("*.py") if "collect_scan_coordinate_warnings(" in p.read_text()}
    assert len(reporting) >= 8, sorted(reporting)
    assert reporting <= scanning, sorted(reporting - scanning)
