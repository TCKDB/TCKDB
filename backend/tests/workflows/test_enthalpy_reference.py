"""A declaration belongs to the actual enthalpy content, never to its origin."""

from types import SimpleNamespace

import pytest
from tckdb_schemas.thermo import ThermoStateFields

from app.api.error_contract import CodedValueError
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.workflows.thermo import assert_enthalpy_reference, persist_thermo_upload

REFERENCE = "formation_from_elements_298k"


@pytest.mark.parametrize("content", [
    {"h298_kj_mol": 0.0},
    {"nasa": {}},
    {"nasa9_intervals": [{}]},
    {"wilhoit": {"h0_kj_mol": 0.0}},
    {"points": [{"temperature_k": 400, "h_kj_mol": 0.0}]},
], ids=["scalar", "nasa7", "nasa9", "wilhoit_h0", "point_h"])
def test_each_enthalpy_representation_requires_declaration(content):
    with pytest.raises(CodedValueError) as caught:
        assert_enthalpy_reference(content)
    assert caught.value.code == "enthalpy_declaration_absent"
    declared = {**content, "enthalpy_reference_kind": REFERENCE}
    assert_enthalpy_reference(declared)
    assert_enthalpy_reference(SimpleNamespace(**declared))
    assert "h298_kj_mol" not in declared or declared["h298_kj_mol"] == 0


@pytest.mark.parametrize("content", [
    {"s298_j_mol_k": 10},
    {"points": [{"temperature_k": 400, "cp_j_mol_k": 30}]},
    {"wilhoit": {"cp0_j_mol_k": 20, "h0_kj_mol": None}},
    {"enthalpy_formation_0k_kj_mol": 0},
])
def test_declaration_without_enthalpy_is_refused(content):
    assert_enthalpy_reference(content)
    with pytest.raises(CodedValueError) as caught:
        assert_enthalpy_reference({**content, "enthalpy_reference_kind": REFERENCE})
    assert caught.value.code == "enthalpy_declaration_without_content"


@pytest.mark.parametrize("reference", ["sensible_increment", "absolute_quantum_enthalpy", "unspecified"])
def test_other_quantities_name_observation_route(reference):
    with pytest.raises(CodedValueError) as caught:
        assert_enthalpy_reference({"h298_kj_mol": 10, "enthalpy_reference_kind": reference})
    assert caught.value.code == "enthalpy_quantity_not_storable_here"
    assert "molecular_property_observation" in str(caught.value)


def test_computed_origin_does_not_default_reference():
    assert ThermoStateFields().enthalpy_reference_kind is None
    request = ThermoUploadRequest(
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        scientific_origin="computed", h298_kj_mol=0,
    )
    assert request.enthalpy_reference_kind is None
    with pytest.raises(CodedValueError) as caught:
        persist_thermo_upload(None, request)
    assert caught.value.code == "enthalpy_declaration_absent"
