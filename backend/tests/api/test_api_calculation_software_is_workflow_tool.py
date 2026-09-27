"""A calculation cannot name a workflow tool as its software (issue #305, item 1).

Arkane computes thermochemistry and rate coefficients from the output of an
electronic-structure program; it runs no calculation. A calculation whose
``software_release.name`` is Arkane is refused with a coded 422, and -- the
half that matters to the vocabulary -- no ``software`` row is created for it.

The analysis-software slot on a product row (thermo/statmech/kinetics,
filled from ``analysis_software_release``) is deliberately *not* checked;
``test_api_bundle_thermo_and_scf_provenance.py`` still pins Arkane there,
and moving it is an owner decision recorded in the #305 PR.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models.calculation import Calculation
from app.db.models.software import Software, SoftwareRelease


def _hydrogen_conformer_payload(*, software_release: dict) -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": software_release,
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": "conf-a",
        "note": "test upload",
    }


def _software_names(db_session) -> list[str]:
    return sorted(db_session.scalars(select(Software.name)).all())


@pytest.mark.parametrize("declared", ["Arkane", "arkane", "  ARKANE "])
def test_calculation_declaring_arkane_is_refused(client, db_session, declared):
    before = _software_names(db_session)
    calcs_before = db_session.scalar(select(func.count(Calculation.id)))

    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_hydrogen_conformer_payload(
            software_release={"name": declared, "revision": "b" * 40}
        ),
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "calculation_software_is_workflow_tool"
    assert body["context"] == {
        "field": "software_release.name",
        "declared_name": declared.strip(),
        "workflow_tool": "Arkane",
    }
    # Nothing registered: the refusal happens before resolution.
    assert _software_names(db_session) == before
    assert "Arkane" not in _software_names(db_session)
    assert db_session.scalar(select(func.count(Calculation.id))) == calcs_before


def test_an_electronic_structure_program_is_still_accepted(client, db_session):
    """The negative control: the guard must not refuse a real ESS name, or
    the refusal test above would pass for the wrong reason."""
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_hydrogen_conformer_payload(
            software_release={"name": "ORCA", "version": "6.1.0"}
        ),
    )

    assert resp.status_code == 201, resp.text
    calc = db_session.get(
        Calculation, resp.json()["primary_calculation"]["calculation_id"]
    )
    release = db_session.get(SoftwareRelease, calc.software_release_id)
    assert (release.software.name, release.version) == ("ORCA", "6.1.0")
