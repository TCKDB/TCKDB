"""All three deposit routes enforce the same declared enthalpy contract."""

import pytest
from sqlalchemy import select

from app.db.models.thermo import Thermo
from tests.api.test_api_bundle_thermo_and_scf_provenance import _reaction_bundle, _species_bundle

REFERENCE = "formation_from_elements_298k"
NASA = {"t_low": 200, "t_mid": 1000, "t_high": 3000,
        **{f"{prefix}{i}": 1.0 for prefix in ("a", "b") for i in range(1, 8)}}


@pytest.mark.parametrize("route", ["thermo", "computed-species", "computed-reaction"])
@pytest.mark.parametrize("content,reference,code", [
    ({"h298_kj_mol": 0}, REFERENCE, None),
    ({"h298_kj_mol": 0}, None, "enthalpy_declaration_absent"),
    ({"nasa": NASA}, REFERENCE, None),
    ({"nasa": NASA}, None, "enthalpy_declaration_absent"),
    ({"s298_j_mol_k": 10}, REFERENCE, "enthalpy_declaration_without_content"),
    ({"s298_j_mol_k": 10}, None, None),
    ({"h298_kj_mol": 0}, "sensible_increment", "enthalpy_quantity_not_storable_here"),
], ids=["scalar_declared", "scalar_absent", "fit_only_declared", "fit_only_absent",
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
        assert "phase" not in record and "reference_pressure_bar" not in record
        assert record["reference"]["enthalpy_reference_kind"] == reference
        assert record["reference"]["phase"] == "gas"
        assert record["reference"]["reference_pressure_bar"] == 1
        assert record["reference"]["enthalpy_quantity"]
