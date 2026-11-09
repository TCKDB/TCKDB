"""An artifact attached after the deposit re-runs the checks that depend on file presence.

``converged_opt_no_usable_energy`` is judged at deposit. A client that uploads the calculation first and the output
log second leaves a warning behind that the log has made untrue. The artifact route now re-evaluates the check from
stored state: a warning that no longer applies is retired (kept in ``deposit_warning_retirement``, not shown as
current), one whose facts changed is superseded, and one that would now be raised is added.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning, DepositWarningRetirement
from app.services.deposit_warnings import deposit_warnings_for
from tests.api.artifact_urls import artifacts_url
from tests.api.test_api_artifact_sp_energy_hook import XYZ_CH4, _b64, _molpro_output_log  # noqa: F401

CODE = "converged_opt_no_usable_energy"
BASE = "/api/v1/scientific"


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}"
    )


def _deposit_opt(client, *, energy: float | None, smiles_tag: str = "") -> int:
    opt_result: dict = {"converged": True}
    if energy is not None:
        opt_result["final_energy_hartree"] = energy
    response = client.post(
        "/api/v1/uploads/conformers",
        json={
            "species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1},
            "geometry": {"xyz_text": XYZ_CH4},
            "calculation": {
                "type": "opt",
                "software_release": {"name": "Molpro", "version": "2022.1"},
                "level_of_theory": {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
                "opt_result": opt_result,
            },
            "label": f"retire-{smiles_tag}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["primary_calculation"]["calculation_id"]


def _current(client, db_session, calc_id: int) -> list[dict]:
    ref = db_session.get(Calculation, calc_id).public_ref
    read = client.get(f"{BASE}/calculations/{ref}")
    assert read.status_code == 200, read.text
    (entry,) = [w for w in read.json()["record_warnings"] if w["record_ref"] == ref]
    return [i for i in entry["items"] if i["code"] == CODE]


def _history(db_session, calc_id: int) -> list[DepositWarning]:
    return list(
        db_session.scalars(
            select(DepositWarning)
            .where(DepositWarning.record_type == RT.calculation, DepositWarning.record_id == calc_id, DepositWarning.code == CODE)
            .order_by(DepositWarning.id)
        )
    )


def _retirements(db_session, calc_id: int) -> list[DepositWarningRetirement]:
    ids = [w.id for w in _history(db_session, calc_id)]
    return list(
        db_session.scalars(
            select(DepositWarningRetirement).where(DepositWarningRetirement.deposit_warning_id.in_(ids))
        )
    ) if ids else []


def _attach(client, calc_id: int, **artifact) -> dict:
    response = client.post(artifacts_url(client, calc_id), json={"artifacts": [{**_molpro_output_log(), **artifact}]})
    assert response.status_code == 201, response.text
    return response.json()


def test_two_phase_deposit_retires_the_warning_once_the_file_arrives(client, db_session):
    calc_id = _deposit_opt(client, energy=-40.45, smiles_tag="a")

    # Phase 1: energy, no file -> the rule's own condition is not yet met.
    (item,) = _current(client, db_session, calc_id)
    assert item["context"] == {"has_energy": True, "has_artifact": False}

    # Phase 2: the output log.
    body = _attach(client, calc_id)
    assert CODE not in [w["code"] for w in body["warnings"]]

    assert _current(client, db_session, calc_id) == []
    (row,) = _history(db_session, calc_id)  # still stored
    (retirement,) = _retirements(db_session, calc_id)
    assert retirement.deposit_warning_id == row.id
    assert retirement.retired_reason_code == "artifact_attached"
    # History stays queryable through the service.
    kept = deposit_warnings_for(db_session, RT.calculation, [calc_id], include_retired=True)[calc_id]["items"]
    assert [i.code for i in kept] == [CODE]
    assert [i.code for i in deposit_warnings_for(db_session, RT.calculation, [calc_id])[calc_id]["items"]] == []


def test_a_later_artifact_changes_nothing_more(client, db_session):
    calc_id = _deposit_opt(client, energy=-40.45, smiles_tag="b")
    _attach(client, calc_id)
    before = (len(_history(db_session, calc_id)), len(_retirements(db_session, calc_id)))
    _attach(client, calc_id, filename="second.out")
    assert (len(_history(db_session, calc_id)), len(_retirements(db_session, calc_id))) == before == (1, 1)


def test_a_deposit_with_no_file_keeps_its_warning(client, db_session):
    calc_id = _deposit_opt(client, energy=-40.45, smiles_tag="c")
    assert len(_current(client, db_session, calc_id)) == 1
    assert _retirements(db_session, calc_id) == []


def test_a_file_that_does_not_supply_the_missing_energy_supersedes_rather_than_clears(client, db_session):
    """No energy on the opt: the file arrives but the rule is still unmet, and it now says so truthfully."""
    calc_id = _deposit_opt(client, energy=None, smiles_tag="d")
    (item,) = _current(client, db_session, calc_id)
    assert item["context"] == {"has_energy": False, "has_artifact": False}

    _attach(client, calc_id)

    (item,) = _current(client, db_session, calc_id)
    assert item["context"] == {"has_energy": False, "has_artifact": True}
    old, new = _history(db_session, calc_id)
    (retirement,) = _retirements(db_session, calc_id)
    assert retirement.deposit_warning_id == old.id and retirement.retired_reason_code == "superseded_by_recheck"
    assert new.id != old.id


def test_an_artifact_on_a_calculation_with_no_such_warning_writes_nothing(client, db_session):
    response = client.post(
        "/api/v1/uploads/conformers",
        json={
            "species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1},
            "geometry": {"xyz_text": XYZ_CH4},
            "calculation": {
                "type": "sp",
                "software_release": {"name": "Molpro", "version": "2022.1"},
                "level_of_theory": {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
                "sp_result": {"electronic_energy_hartree": -40.457885930635},
            },
            "label": "retire-e",
        },
    )
    calc_id = response.json()["primary_calculation"]["calculation_id"]
    _attach(client, calc_id)
    assert _history(db_session, calc_id) == []
    assert _retirements(db_session, calc_id) == []
