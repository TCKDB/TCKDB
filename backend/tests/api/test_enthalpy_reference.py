"""All three deposit routes enforce the same declared enthalpy contract."""

import pytest
from sqlalchemy import select

from app.db.models.thermo import Thermo
from tests.api.test_api_bundle_thermo_and_scf_provenance import _reaction_bundle, _species_bundle

REFERENCE = "formation_298k"
NASA = {"t_low": 200, "t_mid": 1000, "t_high": 3000,
        **{f"{prefix}{i}": 1.0 for prefix in ("a", "b") for i in range(1, 8)}}
# NASA-9 and Wilhoit content, only exercised so far at the unit level
# (assert_enthalpy_reference called directly on a stub dict) -- these give
# the same rule route-level coverage: a real upload through /uploads/{route}
# and a real read-back, not just the isolated validator function.
NASA9 = [{
    "interval_index": 1, "t_min_k": 200.0, "t_max_k": 1000.0,
    "a1": 1.0, "a2": 2.0, "a3": 3.0, "a4": 4.0, "a5": 5.0,
    "a6": 6.0, "a7": 7.0, "a8": 8.0, "a9": 9.0,
}]
WILHOIT = {
    "cp0_j_mol_k": 33.3, "cp_inf_j_mol_k": 108.9, "b_k": 500.0,
    "a0": -1.5, "a1": 2.5, "a2": -0.3, "a3": 0.1, "h0_kj_mol": -241.8,
}
POINT = [{"temperature_k": 400, "h_kj_mol": -74.5}]


@pytest.mark.parametrize("route", ["thermo", "computed-species", "computed-reaction"])
@pytest.mark.parametrize("content,reference,code", [
    ({"h298_kj_mol": 0}, REFERENCE, None),
    ({"h298_kj_mol": 0}, None, "enthalpy_declaration_absent"),
    ({"nasa": NASA}, REFERENCE, None),
    ({"nasa": NASA}, None, "enthalpy_declaration_absent"),
    ({"points": POINT}, REFERENCE, None),
    ({"points": POINT}, None, "enthalpy_declaration_absent"),
    ({"s298_j_mol_k": 10}, REFERENCE, "enthalpy_declaration_without_content"),
    ({"s298_j_mol_k": 10}, None, None),
    ({"h298_kj_mol": 0}, "sensible_increment", "enthalpy_quantity_not_storable_here"),
], ids=["scalar_declared", "scalar_absent", "fit_only_declared", "fit_only_absent",
        "point_declared", "point_absent",
        "declaration_without_content", "entropy_only", "other_quantity"])
def test_deposit_enthalpy_contract(client, db_session, route, content, reference, code):
    thermo = {**content, "enthalpy_reference_kind": reference}
    if route == "thermo":
        payload = {**thermo, "scientific_origin": "computed",
                   "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}}
    elif route == "computed-species":
        payload = _species_bundle(thermo=thermo)
    else:
        payload = _reaction_bundle()
        payload["species"][0]["thermo"] = thermo
    response = client.post(f"/api/v1/uploads/{route}", json=payload)
    assert response.status_code == (422 if code else 201), response.text
    if code:
        assert response.json()["code"] == code
    else:
        row = db_session.scalars(select(Thermo)).one()
        assert row.enthalpy_reference_kind == reference
        assert row.h298_kj_mol == content.get("h298_kj_mol")

        public = client.get(f"/api/v1/scientific/species-entries/{row.species_entry_id}/thermo")
        assert public.status_code == 200, public.text
        record = public.json()["records"][0]
        assert record["enthalpy_reference_kind"] == reference
        assert record["phase"] == "gas"
        # reference_pressure_bar is never defaulted (issue #529).
        assert record["reference_pressure_bar"] is None


# NASA-9 and Wilhoit are not representable in the computed-species /
# computed-reaction bundle schemas (`ThermoInBundle` carries `nasa` and
# `points` but not `nasa9_intervals` or `wilhoit` -- a pre-existing, separate
# gap, not part of this rule), so their route-level coverage is the
# standalone /uploads/thermo route only.
@pytest.mark.parametrize("content,reference,code", [
    ({"nasa9_intervals": NASA9}, REFERENCE, None),
    ({"nasa9_intervals": NASA9}, None, "enthalpy_declaration_absent"),
    ({"wilhoit": WILHOIT}, REFERENCE, None),
    ({"wilhoit": WILHOIT}, None, "enthalpy_declaration_absent"),
], ids=["nasa9_declared", "nasa9_absent", "wilhoit_declared", "wilhoit_absent"])
def test_deposit_enthalpy_contract_nasa9_and_wilhoit(client, db_session, content, reference, code):
    payload = {**content, "enthalpy_reference_kind": reference, "scientific_origin": "computed",
               "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}}
    response = client.post("/api/v1/uploads/thermo", json=payload)
    assert response.status_code == (422 if code else 201), response.text
    if code:
        assert response.json()["code"] == code
    else:
        row = db_session.scalars(select(Thermo)).one()
        assert row.enthalpy_reference_kind == reference

        public = client.get(f"/api/v1/scientific/species-entries/{row.species_entry_id}/thermo")
        assert public.status_code == 200, public.text
        record = public.json()["records"][0]
        assert record["enthalpy_reference_kind"] == reference
