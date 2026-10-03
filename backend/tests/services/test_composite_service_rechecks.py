"""The service re-runs every rule the wire carries for assembled composites (ADR 0021, P5).

A payload built with ``model_copy`` / ``model_construct`` skips the models' validators, so each rule
that was only a validator is provoked here by such a payload and must still be refused where the
data is written.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    CompositeInputPayload,
    CompositeResultPayload,
)
from tckdb_schemas.fragments.refs import LevelOfTheoryRef
from tckdb_schemas.workflows.computed_species_upload import CalculationDependencyInBundle, ComputedSpeciesUploadRequest

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationDependencyRole, CalculationType, CompositeAssembly
from app.services.calculation_resolution import (
    add_dependency_edge_idempotent,
    resolve_and_persist_calculation_with_results,
    resolve_level_of_theory_ref,
)
from app.services.composite_input_resolution import finalize_composite_inputs
from app.services.composite_result_resolution import persist_composite_result
from app.workflows.computed_species import persist_computed_species_upload
from tests import composite_p5_fixtures as f
from tests.services.scientific_read._factories import make_calculation, make_species, make_species_entry, next_inchi_key
from tests.services.test_composite_user_scheme import (
    _assembled_payload,
    _clean_inputs,
    _refs_for,
    _species_entry,
    _sps,
)

DERIVED = "composite_input_edge_is_derived"


def test_a_composite_input_edge_cannot_be_written_by_anything_but_the_derived_path(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CMPEDG"))
    entry = make_species_entry(db_session, species)
    sp = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    composite = make_calculation(db_session, type=CalculationType.composite, species_entry_id=entry.id)
    with pytest.raises(CodedValueError) as err:
        add_dependency_edge_idempotent(
            db_session,
            parent_calculation_id=sp.id,
            child_calculation_id=composite.id,
            dependency_role=CalculationDependencyRole.composite_input,
            context="depends_on",
        )
    assert err.value.code == DERIVED
    edge = add_dependency_edge_idempotent(
        db_session,
        parent_calculation_id=sp.id,
        child_calculation_id=composite.id,
        dependency_role=CalculationDependencyRole.composite_input,
        context="composite_result.inputs",
        derived=True,
    )
    assert edge.dependency_role is CalculationDependencyRole.composite_input


def test_the_bundle_workflow_refuses_a_declared_composite_input_edge_the_wire_never_saw(db_session):
    """The reviewer's probe: the edge was written onto an sp child because only the wire refused it."""
    bundle = f.bundle([f.sp("spt", f.TZ, -76.3)])
    request = ComputedSpeciesUploadRequest.model_validate(bundle)
    sp = request.conformers[0].additional_calculations[0]
    sp.depends_on = [
        CalculationDependencyInBundle.model_construct(
            parent_calculation_key="opt0", role=CalculationDependencyRole.composite_input
        )
    ]
    with pytest.raises(CodedValueError) as err:
        persist_computed_species_upload(db_session, request)
    assert err.value.code == DERIVED


def test_the_resolver_refuses_method_level_fields_beside_a_scheme(db_session):
    ref = LevelOfTheoryRef(composite_scheme=f.SCHEME_B).model_copy(update={"basis": "cc-pVQZ", "dispersion": "d3bj"})
    with pytest.raises(CodedValidationError) as err:
        resolve_level_of_theory_ref(db_session, ref)
    assert err.value.code == "composite_scheme_malformed"
    assert err.value.context["rule"] == "ordinary_fields_with_scheme"
    assert err.value.context["fields"] == ["basis", "dispersion"]


def test_persisting_a_program_run_composite_refuses_inputs(db_session):
    entry = _species_entry(db_session)
    level = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method="CBS-QB3"))
    calc = Calculation(type=CalculationType.composite, species_entry_id=entry, lot_id=level.id, software_release_id=None)
    db_session.add(calc)
    db_session.flush()
    block = CompositeResultPayload.model_construct(
        assembly=CompositeAssembly.program_run,
        electronic_energy_hartree=-76.0,
        e0_hartree=None,
        recipe_zpe_hartree=None,
        terms=[],
        inputs=[CompositeInputPayload(term_key="x", slot="value", calculation_key="a")],
    )
    with pytest.raises(CodedValueError) as err:
        persist_composite_result(db_session, calc, block)
    assert err.value.code == "composite_inputs_require_assembled"


def test_persisting_an_assembled_composite_refuses_a_missing_total(db_session):
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    good = _assembled_payload(_clean_inputs(_refs_for(sps)))
    bad = good.model_copy(update={"composite_result": good.composite_result.model_copy(update={"electronic_energy_hartree": None})})
    with pytest.raises(CodedValueError) as err:
        resolve_and_persist_calculation_with_results(db_session, bad, species_entry_id=entry)
    assert err.value.code == "composite_total_required"


def test_an_input_naming_both_a_key_and_a_ref_is_refused_when_the_inputs_are_written(db_session):
    entry = _species_entry(db_session)
    sps = _sps(db_session, entry)
    good = _assembled_payload(_clean_inputs(_refs_for(sps)))
    first = good.composite_result.inputs[0].model_copy(update={"calculation_key": "spq"})  # now key AND ref
    bad = good.model_copy(
        update={"composite_result": good.composite_result.model_copy(update={"inputs": [first, *good.composite_result.inputs[1:]]})}
    )
    composite = resolve_and_persist_calculation_with_results(db_session, bad, species_entry_id=entry)
    with pytest.raises(CodedValueError) as err:
        finalize_composite_inputs(db_session, [composite.id], calculations_by_key={"spq": sps["spq"]})
    assert err.value.code == "composite_input_reference_invalid"


def test_a_program_run_at_an_inline_scheme_stores_its_breakdown_at_the_canonical_positions(db_session):
    """The remap runs whenever a definition came with the level, not only for an assembled composite.

    The producer lists scheme (c)'s terms as dtq, drel, dboc, base, dcv; the canonical order is base, dboc,
    dtq, drel, dcv. That permutation is not its own inverse (a 5-cycle plus a fixed point), so a swap of the
    mapping with its inverse stores values at the wrong terms and is caught, which a two-term reverse could not.
    """
    import copy

    from sqlalchemy import select

    from app.db.models.calculation import CalculationCompositeTerm

    by_key = {t["key"]: t for t in f.SCHEME_C["terms"]}
    listed = ["dtq", "drel", "dboc", "base", "dcv"]
    scheme = copy.deepcopy(f.SCHEME_C)
    scheme["terms"] = [copy.deepcopy(by_key[key]) for key in listed]
    value = {"base": -76.375, "dcv": -0.01, "dtq": -0.0123, "drel": -0.0045, "dboc": 0.0027}
    total = sum(value.values())
    entry = _species_entry(db_session)
    payload = CalculationWithResultsPayload(
        type="composite",
        software_release=f.SOFTWARE,
        level_of_theory={"composite_scheme": scheme},
        composite_result={
            "assembly": "program_run",
            "electronic_energy_hartree": total,
            "terms": [{"term_position": i, "value_hartree": value[key]} for i, key in enumerate(listed)],
        },
    )
    composite = resolve_and_persist_calculation_with_results(db_session, payload, species_entry_id=entry)
    db_session.flush()
    stored = {
        row.term_position: row.value_hartree
        for row in db_session.scalars(
            select(CalculationCompositeTerm).where(CalculationCompositeTerm.calculation_id == composite.id)
        )
    }
    # Canonical order of scheme (c): base, dboc (a value term), then the three differences, dtq, drel, dcv.
    assert stored == {
        0: value["base"],
        1: value["dboc"],
        2: value["dtq"],
        3: value["drel"],
        4: value["dcv"],
    }
