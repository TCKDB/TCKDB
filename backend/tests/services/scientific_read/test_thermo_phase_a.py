"""Exact custody and categorical CHEMKIN applicability across public paths."""

from collections import Counter
from types import SimpleNamespace

import pytest

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.schemas.reads.scientific_thermo_search import ThermoSearchRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.contribution_bundle_export import ContributionBundleExportError, _thermo_to_upload
from app.services.scientific_read.chemkin_serialize import _nasa_card, thermo_chemkin_incompatibilities
from app.services.scientific_read.export import SelectedThermo
from app.services.scientific_read.thermo import get_species_thermo
from app.services.scientific_read.thermo_search import search_thermo
from app.services.thermo_contract_inventory import iter_thermo_contract_inventory
from app.workflows.thermo import persist_thermo_upload
from tests.schemas.test_thermo_phase_a_contract import IDENTITY, nasa7


@pytest.mark.parametrize("state", [
    {"phase": None, "reference_pressure_bar": None},
    {"phase": "gas", "reference_pressure_bar": 1},
    {"phase": "gas", "reference_pressure_bar": 1.01325},
    {"phase": "liquid", "reference_pressure_bar": 1},
])
def test_zero_kelvin_custody(db_session, state):
    values = {**state, "enthalpy_formation_0k_kj_mol": 0,
              "enthalpy_formation_0k_uncertainty_kj_mol": 0.125}
    thermo = persist_thermo_upload(db_session, ThermoUploadRequest(species_entry=IDENTITY, **values))
    db_session.flush()
    db_session.expire_all()
    read = get_species_thermo(db_session, species_entry_id=thermo.species_entry_id,
                             request=ThermoReadRequest()).records[0].model_dump(mode="json")
    search = search_thermo(db_session, ThermoSearchRequest(smiles="O")).records[0].thermo.model_dump(mode="json")
    selected = SelectedThermo(thermo, None, [], "scalar", RecordReviewStatus.not_reviewed).to_dict()
    payload, omission = _thermo_to_upload(thermo)
    assert omission is None
    replay = ThermoUploadRequest.model_validate(payload).model_dump(mode="json")
    for projection in (read, search):
        assert {key: projection[key] for key in values} == values
        assert "enthalpy_reference_kind" in projection
        assert projection["enthalpy_reference_kind"] is None
    for projection in (selected, replay):
        assert {key: projection[key] for key in values} == values


def test_historical_incomplete_thermo_readable_but_not_replayable(db_session):
    from app.db.models.thermo import ThermoNASA

    thermo = persist_thermo_upload(db_session, ThermoUploadRequest(enthalpy_reference_kind="formation_298k", species_entry=IDENTITY, h298_kj_mol=0))
    db_session.add(ThermoNASA(thermo_id=thermo.id, a1=3.5))
    db_session.flush()
    db_session.expire_all()
    assert get_species_thermo(db_session, species_entry_id=thermo.species_entry_id,
                             request=ThermoReadRequest()).records
    with pytest.raises(ContributionBundleExportError, match="thermo_upload_incompatible"):
        _thermo_to_upload(thermo)
    inventory = list(iter_thermo_contract_inventory(db_session))
    row = next(row for row in inventory if row["ref"] == thermo.public_ref)
    assert row["upload_incompatibilities"] and row["chemkin_incompatibilities"]


@pytest.mark.parametrize("phase,pressure,reason", [
    (None, None, "unknown phase"), ("gas", None, "unknown reference pressure"),
    ("liquid", 1.01325, "gas phase"), ("gas", 1, "1.01325"),
])
def test_chemkin_state_gaps(phase, pressure, reason):
    thermo = SimpleNamespace(phase=phase, reference_pressure_bar=pressure)
    selected = SimpleNamespace(thermo=thermo, nasa=SimpleNamespace(**nasa7()))
    assert reason in "; ".join(thermo_chemkin_incompatibilities(thermo, selected.nasa))
    with pytest.raises(ValueError, match=reason):
        _nasa_card("H2O", Counter(H=2, O=1), selected)


def test_chemkin_never_fabricates_missing_science():
    thermo = SimpleNamespace(phase="gas", reference_pressure_bar=1.01325)
    complete = nasa7()
    for field in complete:
        partial = {**complete, field: None}
        selected = SimpleNamespace(thermo=thermo, nasa=SimpleNamespace(**partial))
        with pytest.raises(ValueError, match="incomplete"):
            _nasa_card("H2O", Counter(H=2, O=1), selected)
    assert thermo_chemkin_incompatibilities(thermo, None)


def test_chemkin_does_not_substitute_another_selected_candidate():
    from app.services.scientific_read.chemkin_serialize import _build_therm_dat

    unknown = SimpleNamespace(thermo=SimpleNamespace(phase=None, reference_pressure_bar=None), nasa=None)
    eligible = SimpleNamespace(thermo=SimpleNamespace(phase="gas", reference_pressure_bar=1.01325),
                               nasa=SimpleNamespace(**nasa7()))
    species = SimpleNamespace(species_entry=SimpleNamespace(id=1, public_ref="spe_water"),
                              species=SimpleNamespace(smiles="O", public_ref="spc_water"),
                              thermos=[unknown, eligible])
    gaps = []
    thermo_file = _build_therm_dat(SimpleNamespace(species_records=[species]), {1: "H2O"},
                                  {1: Counter(H=2, O=1)}, gaps)
    assert len(gaps) == 1 and gaps[0].ref == "spe_water"
    assert "unknown phase" in gaps[0].detail
    assert "H2O" not in thermo_file


def test_inventory_reports_pending_incompatible_jobs_without_rewriting(db_session):
    from app.db.models.common import UploadJobKind, UploadJobStatus
    from app.db.models.upload_job import UploadJob

    payload = {"enthalpy_reference_kind": "formation_298k", "species_entry": IDENTITY, "nasa": {}}
    job = UploadJob(kind=UploadJobKind.thermo, status=UploadJobStatus.queued, payload=payload)
    db_session.add(job)
    db_session.flush()
    inventory = list(iter_thermo_contract_inventory(db_session))
    report = next(row for row in inventory if row["ref"] == str(job.id))
    assert report["thermo_profile_checked"]
    assert report["upload_incompatibilities"]
    db_session.refresh(job)
    assert job.payload == payload and job.status == UploadJobStatus.queued
