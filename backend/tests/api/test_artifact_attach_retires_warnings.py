"""Later events (a file, a single point, a Hessian) retire the deposit warnings they make untrue."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole
from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning, DepositWarningRetirement
from app.services.deposit_warnings import deposit_warnings_for
from tests.api.artifact_urls import artifacts_url
from tests.api.test_api_artifact_hessian_hook import GAUSSIAN_LOG, GAUSSIAN_NATOMS, MOLPRO_LOG
from tests.api.test_api_artifact_hessian_hook import _artifact as _hessian_artifact
from tests.api.test_api_artifact_hessian_hook import _create_calc as _create_freq
from tests.api.test_api_artifact_sp_energy_hook import XYZ_CH4, _molpro_output_log
from tests.api.test_deposit_warning_reads import _owner_key, _user

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


def _read_items(client, db_session, calc_id: int, key: str | None = None) -> list[dict]:
    ref = db_session.get(Calculation, calc_id).public_ref
    read = client.get(f"{BASE}/calculations/{ref}", headers={"X-API-Key": key} if key else {})
    assert read.status_code == 200, read.text
    (entry,) = [w for w in read.json()["record_warnings"] if w["record_ref"] == ref]
    return entry["items"]


def _current(client, db_session, calc_id: int) -> list[dict]:
    """The CODE items an anonymous reader sees."""
    return [i for i in _read_items(client, db_session, calc_id) if i["code"] == CODE]


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


# ---------------------------------------------------------------------------
# Who sees a retired warning
# ---------------------------------------------------------------------------


def test_curators_and_admins_see_a_retired_warning_marked_and_nobody_else_does(client, db_session):
    calc_id = _deposit_opt(client, energy=-40.45, smiles_tag="f")
    _attach(client, calc_id)
    curator = _user(db_session, "retire-curator", AppUserRole.curator)[1]
    admin = _user(db_session, "retire-admin", AppUserRole.admin)[1]
    other = _user(db_session, "retire-other", AppUserRole.user)[1]

    for key in (curator, admin):
        (item,) = [i for i in _read_items(client, db_session, calc_id, key) if i["code"] == CODE]
        assert item["retired_reason"] == "artifact_attached"
        assert item["retired_at"]
    readers = [None, _owner_key(db_session), other]
    for key in readers:
        assert [i for i in _read_items(client, db_session, calc_id, key) if i["code"] == CODE] == []
    # A current warning carries neither key, for anyone.
    calc2 = _deposit_opt(client, energy=-40.45, smiles_tag="g")
    (live,) = [i for i in _read_items(client, db_session, calc2, curator) if i["code"] == CODE]
    assert "retired_at" not in live and "retired_reason" not in live


# ---------------------------------------------------------------------------
# A single point deposited later
# ---------------------------------------------------------------------------


def _deposit_sp(client) -> int:
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
            "label": "retire-sp",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["primary_calculation"]["calculation_id"]


def test_depositing_a_single_point_for_the_owner_retires_the_opts_warning(client, db_session):
    calc_id = _deposit_opt(client, energy=None, smiles_tag="h")
    assert len(_current(client, db_session, calc_id)) == 1

    _deposit_sp(client)

    assert _current(client, db_session, calc_id) == []
    (row,) = _history(db_session, calc_id)
    (retirement,) = _retirements(db_session, calc_id)
    assert retirement.deposit_warning_id == row.id
    assert retirement.retired_reason_code == "single_point_deposited"


def test_a_single_point_for_another_owner_retires_nothing(client, db_session):
    calc_id = _deposit_opt(client, energy=None, smiles_tag="i")
    response = client.post(
        "/api/v1/uploads/conformers",
        json={
            "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
            "geometry": {"xyz_text": "3\nwater\nO 0.0 0.0 0.0\nH 0.757 0.586 0.0\nH -0.757 0.586 0.0"},
            "calculation": {
                "type": "sp",
                "software_release": {"name": "Molpro", "version": "2022.1"},
                "level_of_theory": {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
                "sp_result": {"electronic_energy_hartree": -76.3},
            },
            "label": "retire-sp-water",
        },
    )
    assert response.status_code == 201, response.text
    assert len(_current(client, db_session, calc_id)) == 1
    assert _retirements(db_session, calc_id) == []


# ---------------------------------------------------------------------------
# A Hessian stored later
# ---------------------------------------------------------------------------


def test_storing_the_hessian_retires_the_hessian_not_stored_warning(client, db_session):
    calc_id = _create_freq(client, natoms=GAUSSIAN_NATOMS)
    ref = db_session.get(Calculation, calc_id).public_ref
    bad = client.post(
        artifacts_url(client, calc_id),
        json={"artifacts": [_hessian_artifact(MOLPRO_LOG, kind="output_log", filename="wrong.log")]},
    )
    assert bad.status_code == 201, bad.text

    def codes(key=None):
        return [i["code"] for i in _read_items(client, db_session, calc_id, key) if i["code"].startswith("hessian_not_stored")]

    assert codes() == ["hessian_not_stored_geometry_mismatch"]
    good = client.post(
        artifacts_url(client, calc_id),
        json={"artifacts": [_hessian_artifact(GAUSSIAN_LOG, kind="output_log", filename="freq.log")]},
    )
    assert good.status_code == 201, good.text

    assert codes() == []
    curator = _user(db_session, "retire-hess-curator", AppUserRole.curator)[1]
    (item,) = [i for i in _read_items(client, db_session, calc_id, curator) if i["code"].startswith("hessian_not_stored")]
    assert item["retired_reason"] == "hessian_stored"
    assert ref


# ---------------------------------------------------------------------------
# Atomicity and the closed reason set
# ---------------------------------------------------------------------------


def test_a_failed_replacement_leaves_the_old_warning_current(client, db_session, monkeypatch):
    """A failed replacement store retires nothing."""
    from app.services import artifact_warning_recheck as recheck

    calc_id = _deposit_opt(client, energy=None, smiles_tag="j")
    monkeypatch.setattr("app.api.routes.calculations.recheck_calculation_warnings", lambda *a, **k: [])
    _attach(client, calc_id)  # the file is there; the stored warning still says it is not
    monkeypatch.undo()
    monkeypatch.setattr(
        "app.services.artifact_warning_recheck._insert_replacements",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store failed")),
    )
    with pytest.raises(RuntimeError, match="store failed"), db_session.begin_nested():
        recheck.recheck_calculation_warnings(
            db_session, calc_id, cause="artifact_attached", submission_id=None, written_by=None
        )
    assert _retirements(db_session, calc_id) == []
    assert len(_current(client, db_session, calc_id)) == 1


def test_a_retirement_that_already_exists_is_left_alone(client, db_session):
    from app.services.artifact_warning_recheck import _retire

    calc_id = _deposit_opt(client, energy=-40.45, smiles_tag="k")
    _attach(client, calc_id)
    rows = _history(db_session, calc_id)
    _retire(db_session, rows, "hessian_stored", None)  # a second retirement of the same row: no error, no change
    (retirement,) = _retirements(db_session, calc_id)
    assert retirement.retired_reason_code == "artifact_attached"


def test_the_reason_set_in_the_service_the_model_and_the_database_agree(db_session):
    from sqlalchemy import text

    from app.db.models.deposit_warning import RETIREMENT_REASONS

    definition = db_session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_deposit_warning_retirement_reason_known'"
        )
    )
    assert definition and all(f"'{reason}'" in definition for reason in RETIREMENT_REASONS)
    assert definition.count("::text") == len(RETIREMENT_REASONS)


def test_a_batch_with_a_failing_file_before_the_good_one_leaves_no_current_hessian_warning(client, db_session):
    calc_id = _create_freq(client, natoms=GAUSSIAN_NATOMS)
    response = client.post(
        artifacts_url(client, calc_id),
        json={
            "artifacts": [
                _hessian_artifact(MOLPRO_LOG, kind="output_log", filename="bad.log"),
                _hessian_artifact(GAUSSIAN_LOG, kind="output_log", filename="freq.log"),
            ]
        },
    )
    assert response.status_code == 201, response.text
    assert [i for i in _read_items(client, db_session, calc_id) if i["code"].startswith("hessian_not_stored")] == []
    curator = _user(db_session, "retire-batch-curator", AppUserRole.curator)[1]
    (item,) = [i for i in _read_items(client, db_session, calc_id, curator) if i["code"].startswith("hessian_not_stored")]
    assert item["retired_reason"] == "hessian_stored"
    ids = [w.id for w in db_session.scalars(
        select(DepositWarning).where(DepositWarning.record_id == calc_id, DepositWarning.code.startswith("hessian_not_stored"))
    )]
    (retirement,) = db_session.scalars(
        select(DepositWarningRetirement).where(DepositWarningRetirement.deposit_warning_id.in_(ids))
    ).all()
    assert retirement.retired_by_user_id is not None


# ---------------------------------------------------------------------------
# Write order and the identical-retired-row guard
# ---------------------------------------------------------------------------


def _energyless_opt_with_file(client, db_session, monkeypatch, tag: str) -> int:
    """An opt whose stored warning says no file although one is attached."""
    calc_id = _deposit_opt(client, energy=None, smiles_tag=tag)
    with monkeypatch.context() as m:
        m.setattr("app.api.routes.calculations.recheck_calculation_warnings", lambda *a, **k: [])
        _attach(client, calc_id)
    return calc_id


def test_the_replacement_is_stored_before_the_old_warning_is_retired(client, db_session, monkeypatch):
    from app.services import artifact_warning_recheck as recheck

    calc_id = _energyless_opt_with_file(client, db_session, monkeypatch, "l")
    seen: dict[str, int] = {}
    real_insert = recheck._insert_replacements

    def spy(*args, **kwargs):
        seen["retired_when_storing"] = len(_retirements(db_session, calc_id))
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(recheck, "_insert_replacements", spy)
    recheck.recheck_calculation_warnings(db_session, calc_id, cause="artifact_attached", submission_id=None, written_by=None)
    assert seen == {"retired_when_storing": 0}
    (item,) = _current(client, db_session, calc_id)
    assert item["context"] == {"has_energy": False, "has_artifact": True}


def test_an_identical_retired_row_keeps_the_old_warning_current(client, db_session, monkeypatch):
    """A replacement identical to a retired row cannot become current, so the old warning stays."""
    from tckdb_schemas.upload_warning import UploadWarning

    from app.services import artifact_warning_recheck as recheck
    from app.services.deposit_warnings import _row, _Target

    calc_id = _energyless_opt_with_file(client, db_session, monkeypatch, "m")
    (old,) = _history(db_session, calc_id)
    twin = UploadWarning.from_code(field="opt_result", code=CODE, context={"has_energy": False, "has_artifact": True})
    twin_row = DepositWarning(**_row(old.submission_id, _Target(RT.calculation, calc_id), twin, None))
    db_session.add(twin_row)
    db_session.flush()
    db_session.add(DepositWarningRetirement(deposit_warning_id=twin_row.id, retired_reason_code="artifact_attached"))
    db_session.flush()

    recheck.recheck_calculation_warnings(
        db_session, calc_id, cause="artifact_attached", submission_id=old.submission_id, written_by=None
    )
    assert not [r for r in _retirements(db_session, calc_id) if r.deposit_warning_id == old.id]
    (item,) = _current(client, db_session, calc_id)
    assert item["context"] == {"has_energy": False, "has_artifact": False}


# ---------------------------------------------------------------------------
# The envelope itself hides retired warnings from anyone who is not a curator
# ---------------------------------------------------------------------------


def test_the_envelope_loads_retired_warnings_only_for_a_published_curator(client, db_session):
    from app.db.models.app_user import AppUser
    from app.services.deposit_warnings import deposit_warning_envelope
    from app.services.scientific_read.warning_views import ReadActor, reset_read_actor, set_read_actor

    calc_id = _deposit_opt(client, energy=-40.45, smiles_tag="n")
    _attach(client, calc_id)
    ref = db_session.get(Calculation, calc_id).public_ref

    def codes() -> list[str]:
        (entry,) = deposit_warning_envelope(db_session, RT.calculation, {calc_id: ref})["record_warnings"]
        return [i.code for i in entry.items]

    assert codes() == []  # no actor published: anonymous
    user = db_session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    token = set_read_actor(ReadActor(user_id=user.id, role=AppUserRole.user))
    try:
        assert codes() == []
    finally:
        reset_read_actor(token)
    token = set_read_actor(ReadActor(user_id=user.id, role=AppUserRole.curator))
    try:
        assert codes() == [CODE]
    finally:
        reset_read_actor(token)
