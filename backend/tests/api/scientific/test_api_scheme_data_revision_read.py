"""The scientific scheme read reports ``data_revision`` and the atom-param sign (#619).

Both come from a real deposit through the resolver, not from a factory that
writes the columns directly, so a write path that dropped either would show
here as ``null``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.workflows.energy_correction_upload import EnergyCorrectionSchemeRef
from app.services.energy_correction_resolution import resolve_or_create_scheme


def _scheme(db_session: Session, **overrides):
    base = {
        "kind": "atom_energy",
        "name": "Arkane atom energies",
        "level_of_theory": {"method": "B3LYP", "basis": "def2-TZVP"},
        "units": "hartree",
        "atom_params": [{"element": "H", "value": -0.5}],
    }
    base.update(overrides)
    return resolve_or_create_scheme(db_session, EnergyCorrectionSchemeRef(**base))


def _core(client, ref: str) -> dict:
    resp = client.get(f"/api/v1/scientific/energy-correction-schemes/{ref}")
    assert resp.status_code == 200, resp.text
    return resp.json()["record"]["energy_correction_scheme"]


def test_a_revised_signed_scheme_reads_back_both(client, db_session):
    scheme = _scheme(
        db_session,
        data_revision="ABCDEF0123456789",
        atom_params_applied_as="subtracted",
    )
    core = _core(client, scheme.public_ref)
    assert core["data_revision"] == "abcdef0123456789"
    assert core["atom_params_applied_as"] == "subtracted"
    assert core["units"] == "hartree"


def test_an_unstated_scheme_reads_back_null_for_both(client, db_session):
    scheme = _scheme(db_session)
    core = _core(client, scheme.public_ref)
    assert core["data_revision"] is None
    assert core["atom_params_applied_as"] is None


def test_the_search_surface_carries_them_too(client, db_session):
    scheme = _scheme(db_session, data_revision="v3.3.0", atom_params_applied_as="added")
    resp = client.get(
        "/api/v1/scientific/energy-correction-schemes/search?scheme_kind=atom_energy"
    )
    assert resp.status_code == 200, resp.text
    (record,) = [
        r
        for r in resp.json()["records"]
        if r["energy_correction_scheme"]["energy_correction_scheme_ref"] == scheme.public_ref
    ]
    assert record["energy_correction_scheme"]["data_revision"] == "v3.3.0"
    assert record["energy_correction_scheme"]["atom_params_applied_as"] == "added"
