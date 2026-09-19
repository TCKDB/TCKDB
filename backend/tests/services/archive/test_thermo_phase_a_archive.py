import io

from sqlalchemy import select

from app.db.models.thermo import Thermo, ThermoNASA
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.archive import restore_archive, write_archive
from app.workflows.thermo import persist_thermo_upload
from tests.schemas.test_thermo_phase_a_contract import IDENTITY
from tests.services.archive.test_archive import _empty_archive_tables


def test_archive_preserves_unknown_state_zero_kelvin_and_legacy_partial_fit(db_session):
    values = {"phase": None, "reference_pressure_bar": None,
              "enthalpy_formation_0k_kj_mol": 0,
              "enthalpy_formation_0k_uncertainty_kj_mol": 0.125}
    row = persist_thermo_upload(db_session, ThermoUploadRequest(species_entry=IDENTITY, **values))
    db_session.add(ThermoNASA(thermo_id=row.id, a1=3.5))
    db_session.flush()
    ref = row.public_ref
    archive = io.BytesIO()
    write_archive(db_session, archive)
    frozen = archive.getvalue()
    _empty_archive_tables(db_session)
    restore_archive(db_session, io.BytesIO(frozen))
    restored = db_session.scalar(select(Thermo).where(Thermo.public_ref == ref))
    assert {key: getattr(restored, key) for key in values} == values
    assert restored.nasa.a1 == 3.5 and restored.nasa.b1 is None
    assert archive.getvalue() == frozen
