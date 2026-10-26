"""A thermo block built without ``enthalpy_reference_kind`` is accepted by the server.

The client builders default the declaration to ``formation_298k`` when the
argument is omitted and the record carries enthalpy content. The server stays
strict, so this posts the defaulted payload through the real route and checks
what is stored, plus the control: a payload without the declaration is still refused.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from tests._ci_dependency import require_module

# Skips locally without the client; fails on CI, which installs it (#575).
require_module("tckdb_client.builders", install="pip install -e clients/python")

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    Thermo,
)

from app.db.models.thermo import Thermo as ThermoRow

COEFFS = [0.5, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0]

FORMS = {
    "scalar": lambda **kw: Thermo.scalar(h298_kj_mol=-241.8, s298_j_mol_k=188.8, **kw),
    "nasa": lambda **kw: Thermo.nasa(
        coeffs_low=COEFFS, coeffs_high=COEFFS, t_low=200, t_mid=1000, t_high=5000, **kw),
    "points": lambda **kw: Thermo.points([{"temperature_k": 400, "h_kj_mol": -241.0}], **kw),
}


def _payload(thermo: Thermo) -> dict:
    geom = Geometry.from_xyz(
        "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
    )
    opt = Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="wb97xd", basis="def2tzvp"),
        output_geometry=geom, final_energy_hartree=-76.4, converged=True, label="opt",
    )
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt],
        primary_calculation=opt,
        thermo=thermo,
    ).to_payload()


@pytest.mark.parametrize("form", list(FORMS))
def test_defaulted_declaration_is_accepted_and_stored(client, db_session, form):
    payload = _payload(FORMS[form]())
    assert payload["thermo"]["enthalpy_reference_kind"] == "formation_298k"
    response = client.post("/api/v1/uploads/computed-species", json=payload)
    assert response.status_code == 201, response.text
    row = db_session.scalars(select(ThermoRow)).one()
    assert row.enthalpy_reference_kind == "formation_298k"


@pytest.mark.parametrize("form", list(FORMS))
def test_server_still_refuses_a_payload_that_bypasses_the_builder(client, form):
    """The builder itself refuses an explicit ``None``; a payload edited after
    the build (or written by hand) is judged by the unchanged server rule."""
    payload = _payload(FORMS[form]())
    payload["thermo"]["enthalpy_reference_kind"] = None
    response = client.post("/api/v1/uploads/computed-species", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "enthalpy_declaration_absent"
