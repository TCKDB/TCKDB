"""The wire rules for a rate's bath gas, shared by every route that deposits one."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import PressureContext
from tckdb_schemas.fragments.kinetics_bath_gas import (
    BATH_GAS_SUM_TOLERANCE,
    BundleKineticsBathGasIn,
    KineticsBathGasUpload,
    check_kinetics_bath_gas,
)
from tckdb_schemas.workflows.computed_reaction_upload import BundleKineticsIn


def _refusal(**kwargs) -> CodedValidationError:
    with pytest.raises(CodedValidationError) as caught:
        check_kinetics_bath_gas(**kwargs)
    return caught.value


def test_an_empty_bath_gas_is_not_stated_and_is_never_refused():
    for context in (None, *PressureContext):
        check_kinetics_bath_gas(pressure_context=context, mole_fractions=[], identities=[])


def test_the_sum_tolerance_is_the_network_solves():
    """A network solve's bath gas must sum to 1.0 within 1e-9; this one is held to the same."""
    assert BATH_GAS_SUM_TOLERANCE == 1e-9
    check_kinetics_bath_gas(pressure_context=None, mole_fractions=[0.5, 0.5 + 9e-10], identities=["a", "b"])
    err = _refusal(pressure_context=None, mole_fractions=[0.5, 0.5 + 2e-9], identities=["a", "b"])
    assert err.code == "kinetics_bath_gas_mole_fraction_sum"


def test_a_sum_below_one_is_refused_like_a_sum_above_one():
    assert _refusal(pressure_context=None, mole_fractions=[0.4, 0.4], identities=["a", "b"]).code == (
        "kinetics_bath_gas_mole_fraction_sum"
    )


def test_the_high_pressure_limit_takes_no_bath_gas():
    err = _refusal(pressure_context=PressureContext.high_p_limit, mole_fractions=[1.0], identities=["a"])
    assert err.code == "kinetics_bath_gas_at_high_pressure_limit"
    assert err.context["field"] == "bath_gas"


def test_a_duplicate_is_named_by_position_and_never_by_row():
    err = _refusal(pressure_context=None, mole_fractions=[0.5, 0.5], identities=["x", "x"])
    assert err.code == "kinetics_bath_gas_duplicate_species"
    assert err.context == {"field": "bath_gas[1]", "first": "bath_gas[0]"}


@pytest.mark.parametrize("fraction", [0.0, -0.5, 1.01])
def test_each_component_is_in_zero_to_one(fraction):
    with pytest.raises(ValidationError):
        BundleKineticsBathGasIn(species_key="ar", mole_fraction=fraction)
    with pytest.raises(ValidationError):
        KineticsBathGasUpload(species_entry={"smiles": "[Ar]", "charge": 0, "multiplicity": 1}, mole_fraction=fraction)


def _fit(**overrides) -> dict:
    base = {
        "scientific_origin": "computed",
        "model_kind": "arrhenius",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 0.0,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
        "reactant_keys": ["a", "b"],
        "product_keys": ["c"],
    }
    base.update(overrides)
    return base


def test_a_bundle_fit_states_its_bath_gas_by_species_key():
    fit = BundleKineticsIn.model_validate(
        _fit(pressure_context="pressure_dependent", bath_gas=[{"species_key": "ar", "mole_fraction": 1.0}])
    )
    assert [bg.species_key for bg in fit.bath_gas] == ["ar"]
    assert BundleKineticsIn.model_validate(_fit()).bath_gas == []


def test_a_bundle_fit_refuses_a_bath_gas_at_the_high_pressure_limit():
    with pytest.raises(ValidationError) as caught:
        BundleKineticsIn.model_validate(
            _fit(pressure_context="high_p_limit", bath_gas=[{"species_key": "ar", "mole_fraction": 1.0}])
        )
    (error,) = caught.value.errors()
    assert error["ctx"]["error"].code == "kinetics_bath_gas_at_high_pressure_limit"
