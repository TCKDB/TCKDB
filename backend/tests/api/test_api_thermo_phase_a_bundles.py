import pytest
from sqlalchemy import select

from app.db.models.thermo import Thermo
from tests.api.test_api_bundle_thermo_and_scf_provenance import _reaction_bundle, _species_bundle


@pytest.mark.parametrize("kind", ["computed-species", "computed-reaction"])
@pytest.mark.parametrize("state,expected", [
    ({}, {"phase": "gas", "reference_pressure_bar": 1}),
    ({"phase": None, "reference_pressure_bar": None}, {"phase": None, "reference_pressure_bar": None}),
    ({"phase": "liquid", "reference_pressure_bar": 1.01325},
     {"phase": "liquid", "reference_pressure_bar": 1.01325}),
    ({"scientific_origin": "experimental"}, {"phase": None, "reference_pressure_bar": None}),
])
def test_bundle_zero_kelvin_state_persistence(client, db_session, kind, state, expected):
    values = {"enthalpy_formation_0k_kj_mol": 0, "enthalpy_formation_0k_uncertainty_kj_mol": 0.125}
    thermo = {**values, **state}
    if kind == "computed-species":
        bundle = _species_bundle(thermo=thermo)
    else:
        bundle = _reaction_bundle()
        bundle["species"][0]["thermo"] = thermo
    response = client.post(f"/api/v1/uploads/{kind}", json=bundle)
    assert response.status_code == 201, response.text
    rows = db_session.scalars(select(Thermo)).all()
    assert len(rows) == 1
    assert {key: getattr(rows[0], key) for key in values} == values
    assert {key: getattr(rows[0], key) for key in expected} == expected
