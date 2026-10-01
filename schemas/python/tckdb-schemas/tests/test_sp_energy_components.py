"""``sp_energy_components`` at the wire: the checks run in every carrier (ADR 0021, P4).

A single point's electronic energy can arrive in three shapes, and the rules
must not depend on which one a producer uses:

* the primitive ``CalculationWithResultsPayload`` (``sp_result`` block);
* the computed-species bundle's ``CalculationInBundle`` (``sp_result`` block);
* the computed-reaction / network bundle's flat ``CalculationIn``
  (``sp_electronic_energy_hartree``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import CalculationType, EnergyComponentKind
from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload
from tckdb_schemas.shared.calculation_in import CalculationIn, calculation_in_to_with_results_payload
from tckdb_schemas.sp_energy_components import (
    SP_ENERGY_COMPONENT_DUPLICATE,
    SP_ENERGY_COMPONENT_NOT_ON_SP,
    SP_ENERGY_COMPONENT_TOTAL_MISMATCH,
    SP_ENERGY_COMPONENTS_DO_NOT_SUM,
    SUM_TOLERANCE_HARTREE,
    check_sp_energy_components,
)
from tckdb_schemas.workflows.computed_species_upload import CalculationInBundle

ENERGY = -76.4
REFERENCE = -75.9
COMMON = {
    "software_release": {"name": "orca", "version": "6.0.1"},
    "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pCVTZ"},
}


def _code(err: ValidationError) -> str:
    original = err.errors()[0]["ctx"]["error"]
    assert isinstance(original, CodedValidationError)
    return original.code


def _parts(reference=REFERENCE, correlation=ENERGY - REFERENCE):
    return [
        {"component": "reference", "value_hartree": reference},
        {"component": "correlation", "value_hartree": correlation},
    ]


def _primitive(components, *, energy=ENERGY, type_="sp"):
    body = {**COMMON, "type": type_, "sp_energy_components": components}
    if energy is not None:
        body["sp_result"] = {"electronic_energy_hartree": energy}
    return CalculationWithResultsPayload.model_validate(body)


def _bundle(components, *, energy=ENERGY, type_="sp"):
    body = {**COMMON, "key": "c1", "type": type_, "sp_energy_components": components}
    if energy is not None:
        body["sp_result"] = {"electronic_energy_hartree": energy}
    return CalculationInBundle.model_validate(body)


def _flat(components, *, energy=ENERGY, type_="sp"):
    body = {**COMMON, "key": "c1", "type": type_, "sp_energy_components": components}
    if energy is not None:
        body["sp_electronic_energy_hartree"] = energy
    return CalculationIn.model_validate(body)


CARRIERS = pytest.mark.parametrize("build", [_primitive, _bundle, _flat], ids=["primitive", "bundle", "flat"])


@CARRIERS
def test_parts_that_add_up_are_accepted(build):
    built = build(_parts())
    assert [(c.component, c.value_hartree) for c in built.sp_energy_components] == [
        (EnergyComponentKind.reference, REFERENCE),
        (EnergyComponentKind.correlation, pytest.approx(ENERGY - REFERENCE)),
    ]


@CARRIERS
@pytest.mark.parametrize("off", [0.9e-6, -0.9e-6])
def test_parts_just_inside_the_tolerance_are_accepted(build, off):
    build(_parts(correlation=ENERGY - REFERENCE + off))


@CARRIERS
@pytest.mark.parametrize("off", [1.1e-6, -1.1e-6])
def test_parts_just_outside_the_tolerance_are_refused(build, off):
    with pytest.raises(ValidationError) as err:
        build(_parts(correlation=ENERGY - REFERENCE + off))
    assert _code(err.value) == SP_ENERGY_COMPONENTS_DO_NOT_SUM


@CARRIERS
def test_a_duplicate_component_is_refused(build):
    with pytest.raises(ValidationError) as err:
        build(_parts() + [{"component": "reference", "value_hartree": REFERENCE}])
    assert _code(err.value) == SP_ENERGY_COMPONENT_DUPLICATE


@CARRIERS
@pytest.mark.parametrize("type_", ["opt", "freq", "irc", "scan"])
def test_components_on_a_calculation_that_is_not_a_single_point_are_refused(build, type_):
    with pytest.raises(ValidationError) as err:
        build(_parts(), type_=type_, energy=None)
    assert _code(err.value) == SP_ENERGY_COMPONENT_NOT_ON_SP


@CARRIERS
def test_a_total_must_equal_the_energy(build):
    build([{"component": "total", "value_hartree": ENERGY}])
    with pytest.raises(ValidationError) as err:
        build([{"component": "total", "value_hartree": ENERGY + 1e-4}])
    assert _code(err.value) == SP_ENERGY_COMPONENT_TOTAL_MISMATCH


@CARRIERS
def test_nothing_is_checked_or_filled_when_the_energy_is_absent(build):
    built = build(_parts(reference=-1.0, correlation=-2.0), energy=None)
    assert len(built.sp_energy_components) == 2


@CARRIERS
def test_a_non_finite_value_is_refused(build):
    with pytest.raises(ValidationError):
        build([{"component": "reference", "value_hartree": float("nan")}], energy=None)
    with pytest.raises(ValidationError):
        build([{"component": "reference", "value_hartree": float("inf")}], energy=None)


@CARRIERS
def test_an_unknown_component_is_refused(build):
    with pytest.raises(ValidationError):
        build([{"component": "mp2_pair_energy", "value_hartree": -0.1}], energy=None)


def test_the_flat_shape_forwards_components_to_the_primitive_payload():
    flat = _flat(_parts())
    primitive = calculation_in_to_with_results_payload(flat)
    assert [(c.component, c.value_hartree) for c in primitive.sp_energy_components] == [
        (c.component, c.value_hartree) for c in flat.sp_energy_components
    ]
    assert primitive.sp_result is not None and primitive.sp_result.electronic_energy_hartree == ENERGY


def test_a_triples_part_means_reference_plus_correlation_is_not_the_energy():
    # Programs disagree on whether the printed correlation energy includes (T), so the rule
    # does not apply once a triples part is sent; refusing would refuse correct deposits.
    parts = _parts(correlation=-0.4) + [{"component": "triples", "value_hartree": -0.1}]
    _primitive(parts)


def test_core_treatment_is_optional_and_defaults_to_not_stated():
    from tckdb_schemas.fragments.refs import LevelOfTheoryRef

    assert LevelOfTheoryRef(method="CCSD(T)", basis="cc-pCVTZ").core_treatment is None
    assert LevelOfTheoryRef(method="CCSD(T)", core_treatment="all_electron").core_treatment.value == "all_electron"
    with pytest.raises(ValidationError):
        LevelOfTheoryRef(method="CCSD(T)", core_treatment="windowed")


def test_the_tolerance_is_one_microhartree():
    assert SUM_TOLERANCE_HARTREE == 1e-6


def test_the_check_returns_nothing_and_computes_nothing_it_could_store():
    assert check_sp_energy_components([], calculation_type=CalculationType.opt, electronic_energy_hartree=None) is None
    assert (
        check_sp_energy_components(
            [(EnergyComponentKind.reference, REFERENCE)],
            calculation_type=CalculationType.sp,
            electronic_energy_hartree=ENERGY,
        )
        is None
    )
