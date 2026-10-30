"""Deposit-time warnings are stored with the record they are about and shown when it is read.

The upload response has always carried the ``warn`` tier. Before this change no scientific read did, so a reader
deciding whether to trust a value could not learn that it tripped an expectation. These tests deposit through the
real endpoints and read back through the real reads.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ReactionEntry
from app.db.models.species import ConformerObservation
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.services.deposit_warnings import REGISTER_ENTRY, content_hash
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.api import test_api_artifact_charge_multiplicity_hook as hook
from tests.api.artifact_urls import artifacts_url
from tests.api.test_api_ts_contract_621 import _bundle, _irc_evidence, _ok, _post_bundle
from tests.services.scientific_read._factories import make_calculation, make_species, make_species_entry, next_inchi_key

_MISSING_IRC = "transition_state_missing_irc_evidence"


def _owned_calculation(session) -> Calculation:
    species = make_species(session, inchi_key=next_inchi_key("DWARN"))
    return make_calculation(session, species_entry_id=make_species_entry(session, species).id)


def _warnings_of(body: dict, ref: str) -> dict:
    (fragment,) = [w for w in body["record_warnings"] if w["record_ref"] == ref]
    assert set(fragment) == {"record_ref", "status", "items"}
    return fragment


def _codes(fragment: dict) -> set[str]:
    return {item["code"] for item in fragment["items"]}


# ---------------------------------------------------------------------------
# The reproduction, flipped: the warning is on the read.
# ---------------------------------------------------------------------------


def test_a_ts_without_irc_evidence_shows_the_warning_on_its_reads(client, db_session):
    result = _ok(_post_bundle(client, _bundle([])))
    assert _MISSING_IRC in {w["code"] for w in result["warnings"]}
    # The upload response is what it always was, plus the optional ``context``.
    assert all(set(w) == {"field", "code", "message", "context"} for w in result["warnings"])

    tse = db_session.get(TransitionStateEntry, result["transition_state_entry_id"])
    ts = db_session.get(TransitionState, tse.transition_state_id)
    rxe = db_session.get(ReactionEntry, result["reaction_entry_id"])
    kin = db_session.get(Kinetics, result["kinetics_ids"][0])

    entry = client.get(f"/api/v1/scientific/transition-state-entries/{tse.public_ref}")
    assert entry.status_code == 200, entry.text[:500]
    fragment = _warnings_of(entry.json(), tse.public_ref)
    assert fragment["status"] == "recorded"
    assert _MISSING_IRC in _codes(fragment)
    item = next(i for i in fragment["items"] if i["code"] == _MISSING_IRC)
    assert item["register_entry"] == 19
    assert item["message"] and item["field"] and item["check_version"] == 1
    assert "id" not in item

    # The concept read names its entries' warnings too.
    concept = client.get(f"/api/v1/scientific/transition-states/{ts.public_ref}")
    assert _MISSING_IRC in _codes(_warnings_of(concept.json(), tse.public_ref))

    # The kinetics citing the saddle point was checked by the same deposit: recorded, with what fired about it.
    kinetics = client.get(f"/api/v1/scientific/reaction-entries/{rxe.public_ref}/kinetics")
    assert kinetics.status_code == 200
    assert _warnings_of(kinetics.json(), kin.public_ref)["status"] == "recorded"

    # A passing IRC removes the warning from the read, as it does from the response.
    again = _ok(_post_bundle(client, _bundle([_irc_evidence()])))
    assert _MISSING_IRC not in {w["code"] for w in again["warnings"]}
    tse2 = db_session.get(TransitionStateEntry, again["transition_state_entry_id"])
    entry2 = client.get(f"/api/v1/scientific/transition-state-entries/{tse2.public_ref}")
    fragment2 = _warnings_of(entry2.json(), tse2.public_ref)
    assert fragment2["status"] == "recorded"
    assert _MISSING_IRC not in _codes(fragment2)


def test_list_and_search_responses_do_not_carry_warnings(client, db_session):
    result = _ok(_post_bundle(client, _bundle([])))
    calc_ref = result["calculation_key_refs"]["ts-freq"]
    search = client.get("/api/v1/scientific/calculations/search", params={"calculation_type": "freq", "limit": 5})
    assert search.status_code == 200, search.text[:300]
    assert "record_warnings" not in search.json()
    assert "record_warnings" in client.get(f"/api/v1/scientific/calculations/{calc_ref}").json()


# ---------------------------------------------------------------------------
# recorded with nothing versus not recorded
# ---------------------------------------------------------------------------


def test_a_clean_deposit_reads_recorded_with_no_items(client, db_session):
    payload = hook._conformer_payload(smiles="C", charge=0, multiplicity=1)
    payload["calculation"] = {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        "sp_result": {"electronic_energy_hartree": -40.5},
    }
    response = client.post("/api/v1/uploads/conformers", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["warnings"] == []
    calc_ref = body["primary_calculation"]["calculation_ref"]
    read = client.get(f"/api/v1/scientific/calculations/{calc_ref}")
    assert _warnings_of(read.json(), calc_ref) == {"record_ref": calc_ref, "status": "recorded", "items": []}

    observation = db_session.get(ConformerObservation, body["id"])
    obs_read = client.get(f"/api/v1/scientific/conformer-observations/{observation.public_ref}")
    assert obs_read.status_code == 200, obs_read.text[:300]
    assert _warnings_of(obs_read.json(), observation.public_ref) == {
        "record_ref": observation.public_ref,
        "status": "recorded",
        "items": [],
    }


def test_a_record_from_before_warnings_were_stored_reads_not_recorded(client, db_session):
    calc = _owned_calculation(db_session)  # written with no deposit, so no marker: how every old record looks
    read = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert read.status_code == 200
    assert _warnings_of(read.json(), calc.public_ref) == {
        "record_ref": calc.public_ref,
        "status": "not_recorded",
        "items": [],
    }


# ---------------------------------------------------------------------------
# Unconditionally append-only, approved or not
# ---------------------------------------------------------------------------


def _one_warning_row(db_session, calc: Calculation, submission_id: int) -> DepositWarning:
    row = DepositWarning(
        submission_id=submission_id,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        code="charge_mismatch",
        register_entry=12,
        field="species_entry.charge",
        message="x",
        content_hash=content_hash("charge_mismatch", "species_entry.charge", "x", None),
        check_version=1,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _submission_id(client) -> int:
    response = client.post(
        "/api/v1/uploads/conformers",
        json=hook._conformer_payload(smiles="C", charge=0, multiplicity=1),
    )
    assert response.status_code == 201
    return response.json()["submission_id"]


def test_warning_rows_refuse_update_delete_and_truncate_even_under_an_approved_record(client, db_session):
    submission_id = _submission_id(client)
    calc = _owned_calculation(db_session)
    row = _one_warning_row(db_session, calc, submission_id)
    curator = AppUser(username="warning-freeze-curator", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    ensure_record_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id)
    review = set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    assert review.first_approved_at is not None

    with pytest.raises(DBAPIError), db_session.begin_nested():
        row.message = "rewritten"
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("TRUNCATE deposit_warning"))

    # INSERT stays open under the approved record: a later artifact upload can add a warning.
    later = DepositWarning(
        submission_id=submission_id,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        code="multiplicity_mismatch",
        register_entry=12,
        field="species_entry.multiplicity",
        message="later",
        content_hash=content_hash("multiplicity_mismatch", "species_entry.multiplicity", "later", None),
        check_version=1,
    )
    db_session.add(later)
    db_session.flush()


def test_warning_rows_refuse_update_and_delete_when_nothing_is_approved(client, db_session):
    submission_id = _submission_id(client)
    row = _one_warning_row(db_session, _owned_calculation(db_session), submission_id)
    with pytest.raises(DBAPIError), db_session.begin_nested():
        row.code = "other"
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()


def test_a_duplicate_within_one_submission_is_dropped(client, db_session):
    submission_id = _submission_id(client)
    calc = _owned_calculation(db_session)
    _one_warning_row(db_session, calc, submission_id)
    with pytest.raises(DBAPIError), db_session.begin_nested():
        _one_warning_row(db_session, calc, submission_id)


# ---------------------------------------------------------------------------
# An artifact upload adds a warning to an existing calculation
# ---------------------------------------------------------------------------


def test_an_artifact_upload_adds_its_warning_to_the_calculation(client, db_session, stub_store_artifact):
    calc_id = hook._create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
    calc = db_session.get(Calculation, calc_id)
    before = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json()
    assert "multiplicity_mismatch" not in _codes(_warnings_of(before, calc.public_ref))

    response = client.post(artifacts_url(client, calc_id), json={"artifacts": [hook._output_log()]})
    assert response.status_code == 201, response.text
    assert "multiplicity_mismatch" in {w["code"] for w in response.json()["warnings"]}

    after = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json()
    fragment = _warnings_of(after, calc.public_ref)
    assert fragment["status"] == "recorded"
    mismatch = next(i for i in fragment["items"] if i["code"] == "multiplicity_mismatch")
    assert mismatch["register_entry"] == 12
    assert mismatch["context"] == {"declared": 2, "logged": hook.LOG_MULTIPLICITY, "software": mismatch["context"]["software"]}


@pytest.fixture
def stub_store_artifact(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


# ---------------------------------------------------------------------------
# The register numbers stored with a warning are the register's own
# ---------------------------------------------------------------------------


def test_stored_register_entries_match_the_generated_register():
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[3] / "docs" / "guides" / "scientific_check_register.md").read_text()
    declared: dict[str, int] = {}
    for block in re.split(r"\n(?=### \d+\. )", doc)[1:]:
        number = int(re.match(r"### (\d+)\.", block).group(1))
        channel = re.search(r"reaches a client via\*\* \| (.*?) \|", block).group(1)
        code_line = re.search(r"\*\*Code\*\* \| (.*?) \|", block).group(1)
        if "UploadWarning" not in channel:
            continue
        for code in re.findall(r"`([a-z_0-9]+)`", code_line):
            declared[code] = number
    assert declared == REGISTER_ENTRY


def test_the_curated_profile_shows_the_warnings_of_an_approved_record(client, db_session):
    result = _ok(_post_bundle(client, _bundle([])))
    tse = db_session.get(TransitionStateEntry, result["transition_state_entry_id"])
    curator = AppUser(username="warning-curated-curator", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    for record_type, record_id in (
        (SubmissionRecordType.transition_state_entry, tse.id),
        (SubmissionRecordType.transition_state, tse.transition_state_id),
    ):
        ensure_record_review(db_session, record_type=record_type, record_id=record_id)
        set_record_review_status(
            db_session, record_type=record_type, record_id=record_id, status=RecordReviewStatus.approved, actor=curator
        )
    read = client.get(f"/api/v1/scientific/transition-state-entries/{tse.public_ref}", params={"profile": "curated"})
    assert read.status_code == 200, read.text[:400]
    fragment = _warnings_of(read.json(), tse.public_ref)
    assert fragment["status"] == "recorded"
    assert _MISSING_IRC in _codes(fragment)


def test_a_sub_tolerance_mode_on_a_minimum_is_stored_on_the_frequency_calculation(client):
    from tests.api.test_api_minimum_imaginary_tau import _payload

    response = client.post("/api/v1/uploads/conformers", json=_payload(imag_freq_cm1=-15.0))
    assert response.status_code == 201, response.text
    body = response.json()
    assert "minimum_imaginary_modes_below_tau" in {w["code"] for w in body["warnings"]}

    freq_ref = body["additional_calculations"][0]["calculation_ref"]
    primary_ref = body["primary_calculation"]["calculation_ref"]
    freq = _warnings_of(client.get(f"/api/v1/scientific/calculations/{freq_ref}").json(), freq_ref)
    assert freq["status"] == "recorded"
    item = next(i for i in freq["items"] if i["code"] == "minimum_imaginary_modes_below_tau")
    assert item["register_entry"] == 17
    # Not on the sp it was deposited beside.
    primary = _warnings_of(client.get(f"/api/v1/scientific/calculations/{primary_ref}").json(), primary_ref)
    assert "minimum_imaginary_modes_below_tau" not in _codes(primary)


# ---------------------------------------------------------------------------
# Review round: reads, artifacts without a submission, isolation, the curated guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _doi_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Enthalpy of formation of water",
            "container-title": ["J. Phys. Chem. Ref. Data"],
            "issued": 1998,
            "URL": f"https://doi.org/{doi}",
        },
    )


def test_the_plain_network_detail_read_shows_its_warnings(client, db_session):
    from app.db.models.network import Network

    response = client.post(
        "/api/v1/uploads/networks",
        json={
            "name": "warning read network",
            "literature": {"doi": "10.1063/1.555991", "title": "A Completely Different Paper"},
        },
    )
    assert response.status_code == 201, response.text[:400]
    assert "literature_title_mismatch" in {w["code"] for w in response.json()["warnings"]}
    network = db_session.get(Network, response.json()["id"])
    read = client.get(f"/api/v1/scientific/networks/{network.public_ref}")
    assert read.status_code == 200, read.text[:400]
    fragment = _warnings_of(read.json(), network.public_ref)
    assert fragment["status"] == "recorded"
    assert "literature_title_mismatch" in _codes(fragment)


def test_the_per_parent_kinetics_and_thermo_reads_carry_their_records_warnings(client, db_session):
    from app.db.models.reaction import ReactionEntry
    from app.db.models.thermo import Thermo

    result = _ok(_post_bundle(client, _bundle([])))
    kinetics = db_session.get(Kinetics, result["kinetics_ids"][0])
    rxe = db_session.get(ReactionEntry, result["reaction_entry_id"])
    read = client.get(f"/api/v1/scientific/reaction-entries/{rxe.public_ref}/kinetics")
    assert read.status_code == 200, read.text[:300]
    fragment = _warnings_of(read.json(), kinetics.public_ref)
    assert fragment["status"] == "recorded"
    assert "missing_level_of_theory_provenance" in _codes(fragment)

    thermo = db_session.get(Thermo, result["thermo_ids"][0])
    entry = thermo.species_entry
    thermo_read = client.get(f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo")
    assert thermo_read.status_code == 200, thermo_read.text[:300]
    thermo_fragment = _warnings_of(thermo_read.json(), thermo.public_ref)
    assert thermo_fragment["status"] == "recorded"
    assert "missing_workflow_tool_provenance" in _codes(thermo_fragment)


def test_bundle_submit_stores_the_warnings_of_the_records_it_creates(client, db_session):
    from app.db.models.thermo import Thermo
    from tests.api.test_api_bundle_upload_warnings import SUBMIT, _kinetics_with_literature_bundle, _thermo_bundle

    body = client.post(SUBMIT, json=_thermo_bundle()).json()
    (thermo_record,) = [r for r in body["records"] if r["record_type"] == "thermo"]
    thermo = db_session.get(Thermo, thermo_record["record_id"])
    thermo_read = client.get(f"/api/v1/scientific/species-entries/{thermo.species_entry.public_ref}/thermo")
    fragment = _warnings_of(thermo_read.json(), thermo.public_ref)
    assert fragment["status"] == "recorded"
    assert "composite_delta_prefer_scheme_terms" in _codes(fragment) or "missing_software_release_provenance" in _codes(fragment)

    kin_body = client.post(SUBMIT, json=_kinetics_with_literature_bundle()).json()
    (kin_record,) = [r for r in kin_body["records"] if r["record_type"] == "kinetics"]
    kinetics = db_session.get(Kinetics, kin_record["record_id"])
    read = client.get(f"/api/v1/scientific/reaction-entries/{kinetics.reaction_entry.public_ref}/kinetics")
    kin_fragment = _warnings_of(read.json(), kinetics.public_ref)
    assert kin_fragment["status"] == "recorded"
    assert "missing_kinetics_interpretation_assignments" in _codes(kin_fragment)


def test_a_warning_row_of_another_record_type_is_never_read_as_this_ones(client, db_session):
    calc = _owned_calculation(db_session)
    submission_id = _submission_id(client)
    # Same id, different record type: a thermo with this id would own these rows, not the calculation.
    for code, field, message in (("charge_mismatch", "species_entry.charge", "x"),):
        db_session.add(
            DepositWarning(
                submission_id=submission_id,
                record_type=SubmissionRecordType.thermo,
                record_id=calc.id,
                code=code,
                field=field,
                message=message,
                content_hash=content_hash(code, field, message, None),
                check_version=1,
            )
        )
    db_session.add(
        DepositWarning(submission_id=submission_id, record_type=SubmissionRecordType.thermo, record_id=calc.id, check_version=1)
    )
    db_session.flush()
    read = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert _warnings_of(read.json(), calc.public_ref) == {"record_ref": calc.public_ref, "status": "not_recorded", "items": []}


def _link_to_live_submission(db_session, client, calc) -> int:
    """Link ``calc`` (a record from before warnings were stored) to a live submission of the test user."""
    from app.services.submission import link_record

    submission_id = _submission_id(client)
    from app.db.models.submission import Submission

    link_record(db_session, submission=db_session.get(Submission, submission_id), record_type=SubmissionRecordType.calculation, record_id=calc.id)
    return submission_id


def test_an_artifact_warning_on_a_pre_change_record_reads_not_recorded_with_its_item_and_writes_no_marker(
    client, db_session, stub_store_artifact
):
    calc = _owned_calculation(db_session)
    submission_id = _link_to_live_submission(db_session, client, calc)
    markers_before = db_session.scalar(
        select(func.count()).select_from(DepositWarning).where(
            DepositWarning.record_type == SubmissionRecordType.calculation, DepositWarning.record_id == calc.id, DepositWarning.code.is_(None)
        )
    )

    response = client.post(artifacts_url(client, calc.id), json={"artifacts": [hook._output_log()]})
    assert response.status_code == 201, response.text[:400]
    assert "multiplicity_mismatch" in {w["code"] for w in response.json()["warnings"]}

    markers_after = db_session.scalar(
        select(func.count()).select_from(DepositWarning).where(
            DepositWarning.record_type == SubmissionRecordType.calculation, DepositWarning.record_id == calc.id, DepositWarning.code.is_(None)
        )
    )
    assert markers_before == markers_after == 0
    stored = db_session.scalars(
        select(DepositWarning).where(
            DepositWarning.record_type == SubmissionRecordType.calculation,
            DepositWarning.record_id == calc.id,
            DepositWarning.code == "multiplicity_mismatch",
        )
    ).all()
    assert [r.submission_id for r in stored] == [submission_id]
    fragment = _warnings_of(client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json(), calc.public_ref)
    # ``status`` describes the deposit-time check, which was never recorded; ``items`` still lists what the artifact earned.
    assert fragment["status"] == "not_recorded"
    assert "multiplicity_mismatch" in _codes(fragment)


def test_an_artifact_warning_is_kept_when_the_calculation_has_no_live_submission(client, db_session, stub_store_artifact):
    calc_id = hook._create_calc(client, smiles="[CH3]", charge=0, multiplicity=2)
    calc = db_session.get(Calculation, calc_id)
    from app.db.models.common import SubmissionStatus
    from app.db.models.submission import Submission, SubmissionRecordLink

    submission = db_session.scalars(
        select(Submission).join(SubmissionRecordLink, SubmissionRecordLink.submission_id == Submission.id).where(
            SubmissionRecordLink.record_id == calc.id, SubmissionRecordLink.record_type == SubmissionRecordType.calculation
        )
    ).first()
    submission.status = SubmissionStatus.failed
    db_session.flush()

    response = client.post(artifacts_url(client, calc_id), json={"artifacts": [hook._output_log()]})
    assert response.status_code == 201, response.text[:400]
    assert "multiplicity_mismatch" in {w["code"] for w in response.json()["warnings"]}
    (row,) = db_session.scalars(
        select(DepositWarning).where(DepositWarning.record_id == calc_id, DepositWarning.code == "multiplicity_mismatch")
    ).all()
    assert row.submission_id is None and row.record_type is SubmissionRecordType.calculation
    fragment = _warnings_of(client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json(), calc.public_ref)
    assert "multiplicity_mismatch" in _codes(fragment)
    # Repeating the upload does not repeat the warning: identical content dedupes by hash.
    client.post(artifacts_url(client, calc_id), json={"artifacts": [hook._output_log(filename="again.log")]})
    again = db_session.scalars(
        select(DepositWarning).where(DepositWarning.record_id == calc_id, DepositWarning.code == "multiplicity_mismatch")
    ).all()
    assert len(again) == 1


def test_a_failure_to_store_warnings_does_not_veto_the_deposit(client, db_session, monkeypatch):
    import app.services.deposit_warnings as module

    real_row = module._row

    def broken(submission_id, target, warning):
        row = real_row(submission_id, target, warning)
        row["check_version"] = 10**9  # smallint overflow: the database refuses the insert
        return row

    monkeypatch.setattr(module, "_row", broken)
    result = _ok(_post_bundle(client, _bundle([])))
    assert _MISSING_IRC in {w["code"] for w in result["warnings"]}  # the response is unchanged
    tse = db_session.get(TransitionStateEntry, result["transition_state_entry_id"])
    read = client.get(f"/api/v1/scientific/transition-state-entries/{tse.public_ref}")
    assert read.status_code == 200
    assert _warnings_of(read.json(), tse.public_ref) == {"record_ref": tse.public_ref, "status": "not_recorded", "items": []}
    assert db_session.scalar(select(func.count()).select_from(DepositWarning).where(DepositWarning.submission_id == result["submission_id"])) == 0


def test_a_failure_to_store_warnings_does_not_veto_a_job(client, db_session, monkeypatch):
    import app.services.deposit_warnings as module
    import app.workers.upload_worker as upload_worker
    from app.db.models.common import UploadJobStatus
    from app.db.models.upload_job import UploadJob
    from tests.workers.test_upload_worker_warnings import CASES, UploadJobKind

    real_row = module._row
    monkeypatch.setattr(
        module, "_row", lambda *a: {**real_row(*a), "check_version": 10**9}
    )
    build, _direct, job_url, _required = CASES[UploadJobKind.transition_state]
    queued = client.post(job_url, json=build())
    job = db_session.get(UploadJob, queued.json()["job_id"])
    upload_worker.run_one_job(db_session, job)
    assert job.status is UploadJobStatus.complete
    assert db_session.scalar(select(func.count()).select_from(DepositWarning).where(DepositWarning.submission_id == queued.json()["submission_id"])) == 0


def test_a_context_with_nan_or_infinity_is_stored_as_null(client, db_session):
    from tckdb_schemas.upload_warning import UploadWarning

    from app.services.deposit_warnings import store_deposit_warnings

    submission_id = _submission_id(client)
    calc = _owned_calculation(db_session)

    class Target:
        record_type = SubmissionRecordType.calculation
        record_id = calc.id

    store_deposit_warnings(
        db_session,
        submission_id,
        [UploadWarning(field="f", code="sp_energy_payload_log_mismatch", message="m", context={"delta": float("nan"), "big": float("inf"), "ok": 1.5})],
        targets=[Target()],
        mark_checked=False,
    )
    (row,) = db_session.scalars(select(DepositWarning).where(DepositWarning.record_id == calc.id, DepositWarning.code.is_not(None))).all()
    assert row.context == {"delta": None, "big": None, "ok": 1.5}


def test_a_hidden_ref_in_a_warning_context_blanks_the_context_not_the_entry(client, db_session):
    curator = AppUser(username="warning-redaction-curator", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    visible = _owned_calculation(db_session)
    hidden = _owned_calculation(db_session)
    for calc, status in ((visible, RecordReviewStatus.approved), (hidden, RecordReviewStatus.rejected)):
        ensure_record_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id)
        set_record_review_status(
            db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=status, actor=curator, note="x"
        )
    submission_id = _submission_id(client)
    quoted = {"source": hidden.public_ref}
    message = f"compare with {hidden.public_ref}"
    for code, field, body_text, context in (
        ("sp_energy_payload_log_mismatch", "a", "plain", quoted),
        ("charge_mismatch", "b", message, None),
    ):
        db_session.add(
            DepositWarning(
                submission_id=submission_id,
                record_type=SubmissionRecordType.calculation,
                record_id=visible.id,
                code=code,
                field=field,
                message=body_text,
                context=context,
                content_hash=content_hash(code, field, body_text, context),
                check_version=1,
            )
        )
    db_session.add(
        DepositWarning(submission_id=submission_id, record_type=SubmissionRecordType.calculation, record_id=visible.id, check_version=1)
    )
    db_session.flush()

    from app.services.auth import create_api_key

    owner = db_session.scalars(select(AppUser).where(AppUser.username == "testuser")).one()
    _, key = create_api_key(db_session, owner)
    read = client.get(
        f"/api/v1/scientific/calculations/{visible.public_ref}",
        params={"profile": "curated"},
        headers={"X-API-Key": key},
    )
    assert read.status_code == 200, read.text[:500]
    fragment = _warnings_of(read.json(), visible.public_ref)
    assert fragment["status"] == "recorded"
    by_code = {item["code"]: item for item in fragment["items"]}
    assert set(by_code) == {"sp_energy_payload_log_mismatch", "charge_mismatch"}
    # The context that quoted a hidden record is blanked; the warning, in TCKDB's words, stays.
    assert by_code["sp_energy_payload_log_mismatch"]["context"] is None
    assert by_code["sp_energy_payload_log_mismatch"]["message"].startswith("The single-point energy deposited")
    # The owner's copy of the stored text is blanked where it named the hidden record, kept where it did not.
    assert by_code["sp_energy_payload_log_mismatch"]["depositor_message"] == "plain"
    assert "depositor_message" not in by_code["charge_mismatch"]
    assert hidden.public_ref not in read.text


def test_a_nan_context_reads_exactly_as_the_upload_response_showed_it(client, db_session, monkeypatch):
    """The depositor's response and a later reader's view agree: a non-finite number is null in both."""
    from tckdb_schemas.upload_warning import UploadWarning

    import app.api.routes.uploads as uploads
    from tests.api.test_api_uploads import _transition_state_payload

    real = uploads.transition_state_request_warnings
    extra = UploadWarning(
        field="validation_evidence",
        code="sp_energy_payload_log_mismatch",
        message="nan context",
        context={"delta_hartree": float("nan"), "ok": 1.5, "nested": {"big": float("inf")}},
    )
    monkeypatch.setattr(uploads, "transition_state_request_warnings", lambda request: [*real(request), extra])

    response = client.post("/api/v1/uploads/transition-states", json=_transition_state_payload())
    assert response.status_code == 201, response.text[:400]
    body = response.json()
    (returned,) = [w for w in body["warnings"] if w["code"] == "sp_energy_payload_log_mismatch"]
    tse = db_session.get(TransitionStateEntry, body["id"])
    fragment = _warnings_of(
        client.get(f"/api/v1/scientific/transition-state-entries/{tse.public_ref}").json(), tse.public_ref
    )
    (read,) = [i for i in fragment["items"] if i["code"] == "sp_energy_payload_log_mismatch"]
    assert returned["context"] == read["context"] == {"delta_hartree": None, "ok": 1.5, "nested": {"big": None}}
