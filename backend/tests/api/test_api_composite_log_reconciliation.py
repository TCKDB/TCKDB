"""A composite calculation with an attached Gaussian log, end to end (ADR 0021, P3b).

Both ways an output log reaches a calculation: the artifacts route
(``POST /calculations/{id}/artifacts``) and inline on a computed-species bundle
calculation. The log is a real CBS-QB3 run; the deposited ``composite_result``
is built from the numbers it prints.

What would make these vacuous, and what stops it
------------------------------------------------
* "No warning" is only meaningful when the same log, with one number moved,
  produces the warning: each route has both, and the warning tests assert the
  deposited value is unchanged afterwards (nothing overwritten, nothing filled).
* A legacy ``sp`` / ``opt`` at the CBS-QB3 level with the same log must still get
  no single-point energy filled, and no composite warning (that hook is not theirs).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.calculation import CalculationCompositeResult, CalculationSPResult

_LOG = (
    Path(__file__).resolve().parent.parent / "fixtures" / "gaussian_composite" / "cbs_qb3_ts_c2h5no2_g16.out"
).read_bytes()
_E0 = -283.819775
_ZPE = 0.072623
_ELECTRONIC = -283.892398

_WATER_XYZ = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
_WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}
_SOFTWARE = {"name": "Gaussian", "version": "16"}

_COMPOSITE_WARNING_CODES = {"composite_energy_log_mismatch", "composite_log_method_mismatch"}


@pytest.fixture(autouse=True)
def _stub_store_artifact(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _output_log(filename: str = "cbs-qb3.log") -> dict:
    return {"kind": "output_log", "filename": filename, "content_base64": base64.b64encode(_LOG).decode()}


def _composite(*, level: str = "CBS-QB3", **result) -> dict:
    block = {
        "assembly": "program_run",
        "electronic_energy_hartree": _ELECTRONIC,
        "e0_hartree": _E0,
        "recipe_zpe_hartree": _ZPE,
    }
    block.update(result)
    return {
        "type": "composite",
        "software_release": _SOFTWARE,
        "level_of_theory": {"method": level},
        "composite_result": block,
    }


def _codes(response) -> list[str]:
    return [w["code"] for w in response.json().get("warnings", [])]


def _deposit(client, calc: dict) -> int:
    resp = client.post(
        "/api/v1/uploads/conformers",
        json={"species_entry": dict(_WATER), "geometry": {"xyz_text": _WATER_XYZ}, "calculation": calc},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


def _attach(client, calc_id: int):
    resp = client.post(f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [_output_log()]})
    assert resp.status_code == 201, resp.text
    return resp


def _stored(db_session, calc_id: int) -> tuple:
    db_session.expire_all()
    row = db_session.get(CalculationCompositeResult, calc_id)
    return row.e0_hartree, row.electronic_energy_hartree, row.recipe_zpe_hartree


# ---------------------------------------------------------------------------
# The artifacts route
# ---------------------------------------------------------------------------


def test_artifacts_route_matching_log_gives_no_composite_warning(client, db_session):
    calc_id = _deposit(client, _composite())
    resp = _attach(client, calc_id)
    assert not _COMPOSITE_WARNING_CODES & set(_codes(resp))
    assert _stored(db_session, calc_id) == (_E0, _ELECTRONIC, _ZPE)


def test_artifacts_route_disagreeing_log_warns_and_keeps_the_deposit(client, db_session):
    wrong_e0 = _E0 + 0.01
    calc_id = _deposit(client, _composite(e0_hartree=wrong_e0, electronic_energy_hartree=None))
    resp = _attach(client, calc_id)
    assert "composite_energy_log_mismatch" in _codes(resp)
    assert _stored(db_session, calc_id) == (wrong_e0, None, _ZPE)  # kept; nothing filled


def test_artifacts_route_never_fills_a_null_energy_from_the_log(client, db_session):
    calc_id = _deposit(client, _composite(e0_hartree=None, electronic_energy_hartree=None, recipe_zpe_hartree=None))
    resp = _attach(client, calc_id)
    assert not _COMPOSITE_WARNING_CODES & set(_codes(resp))
    assert _stored(db_session, calc_id) == (None, None, None)


def test_artifacts_route_log_of_another_method_than_the_level_warns(client, db_session):
    calc_id = _deposit(client, _composite(level="CBS-4M"))
    resp = _attach(client, calc_id)
    assert "composite_log_method_mismatch" in _codes(resp)
    # Energies were not compared, and the method was not re-keyed.
    assert "composite_energy_log_mismatch" not in _codes(resp)
    assert _stored(db_session, calc_id) == (_E0, _ELECTRONIC, _ZPE)


# ---------------------------------------------------------------------------
# A computed-species bundle, log inline on the composite primary
# ---------------------------------------------------------------------------


def _bundle(primary: dict) -> dict:
    return {
        "species_entry": dict(_WATER),
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _WATER_XYZ},
                "primary_calculation": {"key": "primary", "artifacts": [_output_log()], **primary},
            }
        ],
    }


def test_bundle_matching_log_gives_no_composite_warning(client, db_session):
    resp = client.post("/api/v1/uploads/computed-species", json=_bundle(_composite()))
    assert resp.status_code == 201, resp.text
    assert not _COMPOSITE_WARNING_CODES & set(_codes(resp))


def test_bundle_disagreeing_log_warns_in_the_response(client, db_session):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_bundle(_composite(electronic_energy_hartree=_ELECTRONIC + 0.01, e0_hartree=None)),
    )
    assert resp.status_code == 201, resp.text
    assert "composite_energy_log_mismatch" in _codes(resp)
    row = db_session.scalars(select(CalculationCompositeResult)).one()
    assert (row.e0_hartree, row.electronic_energy_hartree) == (None, _ELECTRONIC + 0.01)  # kept; nothing filled


# ---------------------------------------------------------------------------
# The legacy shapes: an sp / opt at the named-composite level
# ---------------------------------------------------------------------------


def test_a_legacy_sp_with_a_composite_log_gets_no_sp_energy_and_no_composite_warning(client, db_session):
    calc_id = _deposit(
        client,
        {
            "type": "sp",
            "software_release": _SOFTWARE,
            "level_of_theory": {"method": "CBS-QB3"},
            "sp_result": {},
        },
    )
    resp = _attach(client, calc_id)
    codes = set(_codes(resp))
    assert "sp_energy_filled_from_log" not in codes
    assert not _COMPOSITE_WARNING_CODES & codes
    db_session.expire_all()
    row = db_session.get(CalculationSPResult, calc_id)
    assert row is None or row.electronic_energy_hartree is None


def test_a_legacy_sp_keeps_its_reported_energy_unflagged(client, db_session):
    """The producer's sp value stands: a composite log is not evidence against it."""
    calc_id = _deposit(
        client,
        {
            "type": "sp",
            "software_release": _SOFTWARE,
            "level_of_theory": {"method": "CBS-QB3"},
            "sp_result": {"electronic_energy_hartree": _E0},
        },
    )
    resp = _attach(client, calc_id)
    assert "sp_energy_payload_log_mismatch" not in _codes(resp)
    db_session.expire_all()
    assert db_session.get(CalculationSPResult, calc_id).electronic_energy_hartree == _E0
