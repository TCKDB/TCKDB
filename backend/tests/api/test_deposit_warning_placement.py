"""Every deposit-time warning is stored on the exact record it is about.

A warning is born from a payload position. These tests deposit through the real routes and jobs and read back the
rows, so a workflow that stops saying which row it built from which payload object fails here, not in a reader's
misleading "recorded, nothing fired".

The invariant, for every route and job kind: no warning is lost, and every warning is on a record unless it is an
identity-subject warning (about a shared species, reaction, literature or ref row, which never carries a deposit's
warning) with no single result record to take it. The probes then pin exact rows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tckdb_schemas.upload_warning import UploadWarning

import app.workers.upload_worker as upload_worker
from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType as RT
from app.db.models.deposit_warning import DepositWarning
from app.db.models.kinetics import Kinetics
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.species import ConformerObservation
from app.db.models.statmech import Statmech
from app.db.models.submission import SubmissionRecordLink
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.transport import Transport
from app.db.models.upload_job import UploadJob
from app.services.deposit_warnings import is_identity_subject
from tests.api import test_api_artifact_charge_multiplicity_hook as hook
from tests.api.test_api_bundle_upload_warnings import (
    SUBMIT,
    _kinetics_with_literature_bundle,
    _thermo_bundle,
)
from tests.api.test_api_composite_program_run import _CBS_QB3, _sp
from tests.api.test_api_provenance_warnings import _statmech_payload
from tests.api.test_api_ts_contract_621 import _bundle
from tests.workers.test_upload_worker_warnings import CASES, _computed_reaction, _pdep

_MODEL = {
    RT.calculation: Calculation,
    RT.kinetics: Kinetics,
    RT.thermo: Thermo,
    RT.statmech: Statmech,
    RT.transport: Transport,
    RT.transition_state_entry: TransitionStateEntry,
    RT.conformer_observation: ConformerObservation,
    RT.network_solve: NetworkSolve,
    RT.network: Network,
}


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Enthalpy of formation of water",
            "container-title": ["J. Phys. Chem. Ref. Data"],
            "issued": 1998,
            "URL": f"https://doi.org/{doi}",
        },
    )
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _stored(session, submission_id: int, code: str | None = None) -> list[DepositWarning]:
    stmt = select(DepositWarning).where(DepositWarning.submission_id == submission_id, DepositWarning.code.is_not(None))
    if code is not None:
        stmt = stmt.where(DepositWarning.code == code)
    return list(session.scalars(stmt.order_by(DepositWarning.id)))


def _markers(session, submission_id: int) -> list[DepositWarning]:
    return list(
        session.scalars(
            select(DepositWarning).where(DepositWarning.submission_id == submission_id, DepositWarning.code.is_(None))
        )
    )


def _is_identity(code: str, field: str) -> bool:
    return is_identity_subject(UploadWarning(field=field, code=code, message="x"))


def _content(code: str, field: str, message: str) -> tuple[str, str, str]:
    return (code, field, message)


def _signature(session, submission_id: int) -> set[tuple]:
    """Each stored row as (record type, position of the record among this submission's records of that type, code, field).

    Two deposits of one payload create different rows, so rows are compared by *which* record of the deposit they sit
    on (the n-th calculation, the thermo), not by id and not by type alone.
    """
    links = session.scalars(
        select(SubmissionRecordLink).where(SubmissionRecordLink.submission_id == submission_id).order_by(SubmissionRecordLink.id)
    ).all()
    order: dict[RT, list[int]] = {}
    for link in links:
        order.setdefault(link.record_type, []).append(link.record_id)
    rows = session.scalars(select(DepositWarning).where(DepositWarning.submission_id == submission_id)).all()
    out = set()
    for row in rows:
        position = order[row.record_type].index(row.record_id) if row.record_type is not None else None
        out.add((row.record_type.value if row.record_type else None, position, row.code, row.field))
    return out


def _check_invariant(session, submission_id: int, returned: list[dict], *, label: str) -> None:
    stored = _stored(session, submission_id)
    stored_content = {_content(r.code, r.field, r.message) for r in stored}
    for warning in returned:
        assert _content(warning["code"], warning["field"], warning["message"]) in stored_content, (label, warning["code"])
    for row in stored:
        if row.record_type is None:
            assert _is_identity(row.code, row.field), (
                f"{label}: {row.code} at {row.field} is on the submission but is not an identity-subject warning"
            )
    if not any(r.record_type is None and not _is_identity(r.code, r.field) for r in stored):
        assert _markers(session, submission_id), f"{label}: nothing could not be placed, yet no record was marked checked"


def _extra_cases():
    return {
        "computed_species_artifact": (
            "/api/v1/uploads/computed-species",
            lambda: hook._computed_species_bundle(charge=0, multiplicity=3),
        ),
        "statmech": ("/api/v1/uploads/statmech", lambda: _statmech_payload()),
    }


def _sync_cases():
    cases = {kind.value: (case[1], case[0]) for kind, case in CASES.items()}
    cases.update(_extra_cases())
    return cases


# ---------------------------------------------------------------------------
# the invariant, for every route, job kind and bundle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_sync_cases()))
def test_every_route_places_every_warning(client, name):
    url, build = _sync_cases()[name]
    response = client.post(url, json=build())
    assert response.status_code == 201, response.text[:400]
    body = response.json()
    assert body["warnings"], f"{name}: the fixture earns no warning, so this proves nothing"
    _check_invariant(client._db_session, body["submission_id"], body["warnings"], label=name)


@pytest.mark.parametrize("name", ["thermo", "kinetics"])
def test_bundle_submit_places_every_warning(client, name):
    bundle = _thermo_bundle() if name == "thermo" else _kinetics_with_literature_bundle()
    response = client.post(SUBMIT, json=bundle)
    assert response.status_code == 201, response.text[:400]
    body = response.json()
    warnings = [m for m in body["messages"] if m["level"] == "warning"]
    assert warnings
    _check_invariant(client._db_session, body["submission_id"], warnings, label=f"bundle {name}")


@pytest.mark.parametrize("kind", list(CASES), ids=lambda k: k.value)
def test_job_path_places_every_warning_on_the_same_records(client, kind):
    build, direct_url, job_url, _required = CASES[kind]
    payload = build()
    session = client._db_session

    savepoint = session.begin_nested()
    try:
        direct = client.post(direct_url, json=payload)
        assert direct.status_code == 201, direct.text[:400]
        direct_signature = _signature(session, direct.json()["submission_id"])
    finally:
        savepoint.rollback()

    queued = client.post(job_url, json=payload)
    assert queued.status_code == 202, queued.text[:400]
    upload_worker.run_one_job(session, session.get(UploadJob, queued.json()["job_id"]))
    job_submission = queued.json()["submission_id"]

    assert _signature(session, job_submission) == direct_signature
    result = client.get(f"/api/v1/jobs/{queued.json()['job_id']}").json()["result"]
    _check_invariant(session, job_submission, result["warnings"], label=f"job {kind.value}")


# ---------------------------------------------------------------------------
# probes: each lands on the exact record
# ---------------------------------------------------------------------------


def _row(session, submission_id: int, code: str, field: str) -> list[DepositWarning]:
    return [r for r in _stored(session, submission_id, code) if r.field == field]


def test_a_kinetics_path_lands_on_that_kinetics_row(client):
    response = client.post("/api/v1/uploads/computed-reaction", json=_bundle([]))
    body = response.json()
    (row,) = _row(client._db_session, body["submission_id"], "missing_level_of_theory_provenance", "kinetics[0].energy_level_of_theory")
    assert (row.record_type, row.record_id) == (RT.kinetics, body["kinetics_ids"][0])


def test_a_species_thermo_path_lands_on_that_species_thermo_row(client):
    session = client._db_session
    response = client.post("/api/v1/uploads/computed-reaction", json=_bundle([]))
    body = response.json()
    for key, smiles in (("h", "[H]"), ("ch3", "[CH3]"), ("ch4", "C")):
        (row,) = _row(session, body["submission_id"], "missing_workflow_tool_provenance", f"species['{key}'].thermo.workflow_tool_release")
        assert row.record_type is RT.thermo
        assert session.get(Thermo, row.record_id).species_entry.species.smiles == smiles


def test_a_transition_state_path_lands_on_the_transition_state_entry(client):
    body = client.post("/api/v1/uploads/computed-reaction", json=_bundle([])).json()
    for code, field in (
        ("transition_state_missing_irc_evidence", "transition_state.validation_evidence"),
        ("reaction_atom_map_absent", "atom_map"),
    ):
        (row,) = _row(client._db_session, body["submission_id"], code, field)
        assert (row.record_type, row.record_id) == (RT.transition_state_entry, body["transition_state_entry_id"])


def test_a_bundle_root_provenance_gap_is_stored_on_every_kinetics_row(client):
    body = client.post("/api/v1/uploads/computed-reaction", json=_bundle([])).json()
    rows = _row(client._db_session, body["submission_id"], "missing_workflow_tool_provenance", "workflow_tool_release")
    assert {(r.record_type, r.record_id) for r in rows} == {(RT.kinetics, kid) for kid in body["kinetics_ids"]}


def test_a_bundle_calculation_key_lands_on_that_calculation(client):
    body = client.post("/api/v1/uploads/computed-reaction", json=_computed_reaction()).json()
    (row,) = _row(
        client._db_session, body["submission_id"], "n_imag_contradicts_minimum", "species['ch3'].calculations['x-freq']"
    )
    assert (row.record_type, row.record_id) == (RT.calculation, body["calculation_keys"]["x-freq"])


def test_a_computed_species_calculation_key_lands_on_that_freq_calculation(client):
    payload = hook._computed_species_bundle(charge=0, multiplicity=1)
    payload["conformers"][0]["additional_calculations"] = [
        {
            "key": "freq0",
            "type": "freq",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "freq_result": {"n_imag": 1, "imag_freq_cm1": -15.0},
        }
    ]
    response = client.post("/api/v1/uploads/computed-species", json=payload)
    assert response.status_code == 201, response.text[:500]
    body = response.json()
    session = client._db_session
    codes = {w["code"] for w in body["warnings"]}
    assert "minimum_imaginary_modes_below_tau" in codes
    (row,) = [r for r in _stored(session, body["submission_id"], "minimum_imaginary_modes_below_tau")]
    assert row.record_type is RT.calculation
    freq_calc = session.get(Calculation, row.record_id)
    assert freq_calc.type.value == "freq"
    assert "freq0" in row.field


def test_a_network_pdep_single_point_warning_lands_on_that_single_point(client):
    session = client._db_session
    response = client.post("/api/v1/uploads/networks/pdep", json=_pdep())
    body = response.json()
    (row,) = _stored(session, body["submission_id"], "named_composite_deposited_as_sp")
    assert row.record_type is RT.calculation
    calc = session.get(Calculation, row.record_id)
    assert calc.type.value == "sp" and calc.lot.method == "CBS-QB3"
    solve_row = _row(session, body["submission_id"], "network_state_energy_sum_not_compared", "solve.state_energies")
    assert solve_row and all(r.record_type is RT.network_solve for r in solve_row)


# ---------------------------------------------------------------------------
# distinct warnings are never collapsed
# ---------------------------------------------------------------------------


def test_two_single_points_at_different_named_methods_each_keep_their_warning(client):
    payload = hook._conformer_payload(smiles="C", charge=0, multiplicity=1)
    payload["calculation"] = {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "CBS-QB3"},
        "sp_result": {"electronic_energy_hartree": -40.5},
    }
    payload["additional_calculations"] = [
        {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "G4"},
            "sp_result": {"electronic_energy_hartree": -40.6},
        }
    ]
    body = client.post("/api/v1/uploads/conformers", json=payload).json()
    stored = _stored(client._db_session, body["submission_id"])
    assert len(body["warnings"]) == 3
    assert len(stored) == len(body["warnings"])
    named = [r for r in stored if r.code == "named_composite_deposited_as_sp"]
    assert len({r.record_id for r in named}) == 2


def test_three_single_points_at_one_named_method_each_keep_their_warning(client):
    payload = _bundle([])

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "sp" and node.get("key") in ("ch3-sp", "h-sp", "ch4-sp"):
                node["level_of_theory"] = {"method": "CBS-QB3"}
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    body = client.post("/api/v1/uploads/computed-reaction", json=payload).json()
    stored = _stored(client._db_session, body["submission_id"])
    assert len(body["warnings"]) == 10
    assert len(stored) == len(body["warnings"])
    named = [r for r in stored if r.code == "named_composite_deposited_as_sp"]
    assert len(named) == 3 and len({r.record_id for r in named}) == 3


def test_only_truly_identical_warnings_dedupe(client):
    """Same record, same code and field, different message: two rows. Identical content: one."""
    from app.services.deposit_warnings import store_deposit_warnings

    session = client._db_session
    submission_id = client.post("/api/v1/uploads/conformers", json=hook._conformer_payload(smiles="C", charge=0, multiplicity=1)).json()[
        "submission_id"
    ]
    link = session.scalars(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.submission_id == submission_id, SubmissionRecordLink.record_type == RT.calculation
        )
    ).first()

    class Target:
        record_type = RT.calculation
        record_id = link.record_id

    warnings = [
        UploadWarning(field="x", code="converged_opt_no_usable_energy", message="one"),
        UploadWarning(field="x", code="converged_opt_no_usable_energy", message="two"),
        UploadWarning(field="x", code="converged_opt_no_usable_energy", message="one"),
    ]
    store_deposit_warnings(session, submission_id, warnings, targets=[Target()], mark_checked=False)
    rows = [r for r in _stored(session, submission_id) if r.message in ("one", "two")]
    assert sorted(r.message for r in rows) == ["one", "two"]
