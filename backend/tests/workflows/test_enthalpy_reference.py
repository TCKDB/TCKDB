"""A declaration belongs to the actual enthalpy content, never to its origin."""

from types import SimpleNamespace

import pytest
from tckdb_schemas.thermo import ThermoStateFields

from app.api.error_contract import CodedValueError
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.workflows.thermo import assert_enthalpy_reference, persist_thermo_upload

REFERENCE = "formation_298k"


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


def test_enthalpy_declaration_absent_message_is_pinned():
    # Pins the sentence, not just the code: docs/guides/depositing_a_thermo_
    # record.md quotes this verbatim (see test_depositing_a_thermo_record_doc.py),
    # and rewriting the wording here should go red somewhere, not sail
    # through untested the way it once did.
    with pytest.raises(CodedValueError) as caught:
        assert_enthalpy_reference({"h298_kj_mol": 0.0})
    assert str(caught.value) == (
        "Enthalpy content requires enthalpy_reference_kind. Declare "
        "formation_298k only when the source states that convention; "
        "other enthalpy quantities belong in molecular_property_observation."
    )


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


def test_enthalpy_declaration_without_content_message_is_pinned():
    with pytest.raises(CodedValueError) as caught:
        assert_enthalpy_reference({"s298_j_mol_k": 10, "enthalpy_reference_kind": REFERENCE})
    assert str(caught.value) == (
        "enthalpy_reference_kind declares a quantity this thermo record does not carry. "
        "Omit the declaration for entropy and heat-capacity-only records."
    )


@pytest.mark.parametrize("reference", ["sensible_increment", "absolute_quantum_enthalpy", "unspecified"])
def test_other_quantities_name_observation_route(reference):
    with pytest.raises(CodedValueError) as caught:
        assert_enthalpy_reference({"h298_kj_mol": 10, "enthalpy_reference_kind": reference})
    assert caught.value.code == "enthalpy_quantity_not_storable_here"
    assert str(caught.value) == (
        "Thermo accepts only formation_298k enthalpies. "
        "Deposit sensible increments and absolute enthalpies through "
        "the molecular_property_observation route instead."
    )


@pytest.mark.parametrize("typo", ["Formation_298k", "FORMATION_298K", " formation_298k"])
def test_mistyped_reference_value_gets_its_own_outcome(typo):
    """A near-miss of the one legal value is a typo, not a different
    quantity -- it must not fall through to the same refusal that tells a
    depositor to go use molecular_property_observation instead."""
    with pytest.raises(CodedValueError) as caught:
        assert_enthalpy_reference({"h298_kj_mol": 10, "enthalpy_reference_kind": typo})
    assert caught.value.code == "enthalpy_reference_kind_unrecognized"
    assert caught.value.code != "enthalpy_quantity_not_storable_here"
    assert str(caught.value) == (
        f"'{typo}' is not a recognized enthalpy_reference_kind -- did you mean "
        "'formation_298k'? Matching is exact and case-sensitive."
    )


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
