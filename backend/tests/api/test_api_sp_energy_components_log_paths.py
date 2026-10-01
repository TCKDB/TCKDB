"""Energy components cannot slip past their checks through the log-fill paths (ADR 0021, P4).

Reproduction on the first version of #653: a single point with no energy,
``reference=-40.0``, ``correlation=-0.1`` and a Molpro CH4 log returned 201 on
both ``POST /uploads/computed-species`` (inline log) and
``POST /calculations/{id}/artifacts``. The write-time check saw no energy and
passed; the log then filled -40.457886 and the stored parts summed to -40.1.

The rule now: components arrive only with the energy they are parts of
(``sp_energy_components_require_energy``), so a log can only ever confirm or
disagree with a value that was already checked. The artifacts route does not
accept components at all, so it cannot attach them later.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import CalculationType, EnergyComponentKind
from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload, SPEnergyComponentPayload

from app.db.models.calculation import CalculationSPEnergyComponent
from app.services.calculation_resolution import resolve_and_persist_calculation_with_results
from tests.api.test_api_artifact_sp_energy_hook import (  # noqa: F401  (fixture is used by name)
    CH4_ENERGY,
    _computed_species_bundle,
    _create_calc,
    _molpro_output_log,
    _stored_energy,
    stub_store_artifact,
)
from tests.services.scientific_read._factories import make_species, make_species_entry, next_inchi_key

PARTS = [
    {"component": "reference", "value_hartree": -40.0},
    {"component": "correlation", "value_hartree": -0.1},
]


def _component_rows(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(CalculationSPEnergyComponent))


def test_an_inline_log_cannot_fill_an_energy_under_parts_that_were_never_checked(
    client, db_session, stub_store_artifact  # noqa: F811
):
    bundle = _computed_species_bundle(sp_energy=None)
    bundle["conformers"][0]["additional_calculations"][0]["sp_energy_components"] = PARTS
    before = _component_rows(db_session)

    resp = client.post("/api/v1/uploads/computed-species", json=bundle)

    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "sp_energy_components_require_energy", resp.json()
    assert _component_rows(db_session) == before


def test_parts_that_contradict_a_stated_energy_are_refused_on_the_same_bundle(
    client, db_session, stub_store_artifact  # noqa: F811
):
    bundle = _computed_species_bundle(sp_energy=CH4_ENERGY)
    bundle["conformers"][0]["additional_calculations"][0]["sp_energy_components"] = PARTS
    resp = client.post("/api/v1/uploads/computed-species", json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "sp_energy_components_do_not_sum"


def test_the_artifact_route_fills_the_energy_but_never_attaches_components(
    client, db_session, stub_store_artifact  # noqa: F811
):
    calc_id = _create_calc(client, sp_energy=None)
    before = _component_rows(db_session)

    resp = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [_molpro_output_log()]}
    )

    assert resp.status_code == 201, resp.text[:800]
    assert _stored_energy(db_session, calc_id) == CH4_ENERGY
    assert _component_rows(db_session) == before


def test_components_smuggled_into_an_artifact_request_are_ignored_not_stored(
    client, db_session, stub_store_artifact  # noqa: F811
):
    """The route's body model has no such field; whatever it ignores it does not store."""
    calc_id = _create_calc(client, sp_energy=None)
    resp = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts",
        json={"artifacts": [_molpro_output_log()], "sp_energy_components": PARTS},
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _stored_energy(db_session, calc_id) == CH4_ENERGY
    assert _component_rows(db_session) == 0


# ---------------------------------------------------------------------------
# The write-time re-check, on its own
# ---------------------------------------------------------------------------


def _valid_sp() -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload.model_validate(
        {
            "type": "sp",
            "software_release": {"name": "orca", "version": "6.0.1"},
            "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pCVTZ"},
            "sp_result": {"electronic_energy_hartree": -40.1},
        }
    )


def _unvalidated(base: CalculationWithResultsPayload, components, **update) -> CalculationWithResultsPayload:
    """A payload that skipped validation, as a programmatic caller could build one."""
    return base.model_copy(
        update={
            "sp_energy_components": [
                SPEnergyComponentPayload.model_construct(component=EnergyComponentKind(c), value_hartree=v)
                for c, v in components
            ],
            **update,
        }
    )


def _entry(db_session) -> int:
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("SPWR"))).id


def test_the_write_refuses_parts_that_do_not_sum_even_if_validation_was_skipped(db_session):
    bad = _unvalidated(_valid_sp(), [("reference", -40.0), ("correlation", -0.5)])
    with pytest.raises(CodedValidationError) as err:
        resolve_and_persist_calculation_with_results(db_session, bad, species_entry_id=_entry(db_session))
    assert err.value.code == "sp_energy_components_do_not_sum"


def test_the_write_refuses_parts_without_an_energy_even_if_validation_was_skipped(db_session):
    bad = _unvalidated(_valid_sp(), [("reference", -40.0), ("correlation", -0.1)], sp_result=None)
    with pytest.raises(CodedValidationError) as err:
        resolve_and_persist_calculation_with_results(db_session, bad, species_entry_id=_entry(db_session))
    assert err.value.code == "sp_energy_components_require_energy"


def test_the_write_refuses_parts_on_a_non_single_point_even_if_validation_was_skipped(db_session):
    bad = _unvalidated(
        _valid_sp(), [("reference", -40.0)], type=CalculationType.opt, sp_result=None
    )
    with pytest.raises(CodedValidationError) as err:
        resolve_and_persist_calculation_with_results(db_session, bad, species_entry_id=_entry(db_session))
    assert err.value.code == "sp_energy_component_not_on_sp"


def test_the_write_stores_parts_that_pass(db_session):
    good = _unvalidated(_valid_sp(), [("reference", -40.0), ("correlation", -0.1)])
    calc = resolve_and_persist_calculation_with_results(
        db_session, good, species_entry_id=_entry(db_session)
    )
    db_session.flush()
    assert {c.component.value: c.value_hartree for c in calc.sp_energy_components} == {
        "reference": -40.0,
        "correlation": -0.1,
    }
