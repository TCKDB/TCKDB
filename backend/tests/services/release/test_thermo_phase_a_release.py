import hashlib
import json

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.record_review import set_record_review_status
from app.services.release.manifest import load_manifest, verify_release
from app.workflows.thermo import persist_thermo_upload
from tests.schemas.test_thermo_phase_a_contract import IDENTITY
from tests.services.release.test_release_manifest import _publish_with_selection


def test_frozen_zero_kelvin_and_null_state_survive_new_uploads(
    db_session, draft_release, curator, monkeypatch,
):
    values = {"phase": None, "reference_pressure_bar": None,
              "enthalpy_formation_0k_kj_mol": 0,
              "enthalpy_formation_0k_uncertainty_kj_mol": 0.125}
    row = persist_thermo_upload(db_session, ThermoUploadRequest(species_entry=IDENTITY, **values))
    set_record_review_status(db_session, record_type=SubmissionRecordType.thermo,
                             record_id=row.id, status=RecordReviewStatus.approved,
                             actor=curator, note="Phase A custody fixture")
    manifest = _publish_with_selection(db_session, draft_release, curator, row, row.species_entry)
    frozen = {a.path: (a.content, a.sha256) for a in manifest.artifacts}
    candidate = next(json.loads(line) for line in frozen["candidate_records.ndjson"][0].splitlines()
                     if json.loads(line)["record_ref"] == row.public_ref)
    assert {key: candidate["record"][key] for key in values} == values
    persist_thermo_upload(db_session, ThermoUploadRequest(species_entry=IDENTITY, h298_kj_mol=-123))
    from app.services.release import versions
    monkeypatch.setattr(versions, "backend_version", lambda: "999")
    db_session.expire_all()
    again = load_manifest(db_session, draft_release)
    assert {a.path: (a.content, a.sha256) for a in again.artifacts} == frozen
    for content, digest in frozen.values():
        assert hashlib.sha256(content).hexdigest() == digest
    assert verify_release(db_session, draft_release).ok
