"""Resolving structure determinations: what is recorded, what is refused, and what is never guessed."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tckdb_schemas.structure_declarations import StructureDeterminationDeclaration

from app.api.error_contract import CodedValueError
from app.db.models.common import CalculationType, StructureSourceRole
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from app.services.structure_determination_resolution import (
    DeterminationOwner,
    content_hash,
    identity_hash,
    persist_structure_determinations,
)
from tests.services.scientific_read._factories import (
    attach_input_geometry,
    attach_output_geometry,
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    make_workflow_tool_release,
    next_inchi_key,
)

WTR = {"name": "ARC", "version": "1.1.0"}


def _declaration(**changes) -> StructureDeterminationDeclaration:
    raw = {
        "key": "d1",
        "target_kind": "geometry",
        "quantity": "electronic_energy",
        "evaluated_geometry": {"calculation_key": "opt"},
        "sources": [
            {"role": "geometry_optimization", "calculation_key": "opt"},
            {"role": "energy", "calculation_key": "sp"},
        ],
        "workflow_tool_release": WTR,
    }
    raw.update(changes)
    return StructureDeterminationDeclaration.model_validate(raw)


@pytest.fixture
def subject(db_session):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))
    geometry = make_geometry(db_session)
    opt = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    attach_output_geometry(db_session, calculation=opt, geometry=geometry)
    sp = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    attach_input_geometry(db_session, calculation=sp, geometry=geometry)
    make_workflow_tool_release(db_session, name="ARC", version="1.1.0")
    return {"entry": entry, "geometry": geometry, "calcs": {"opt": opt, "sp": sp}}


def _persist(db_session, subject, *declarations):
    return persist_structure_determinations(
        db_session,
        list(declarations),
        owner=DeterminationOwner(species_entry_id=subject["entry"].id),
        calculations_by_key=subject["calcs"],
    )


def _mismatch(exc_info, reason: str) -> None:
    error = exc_info.value
    assert error.code == "structure_determination_mismatch"
    assert error.context["reason"] == reason


def test_stating_the_same_determination_again_resolves_to_the_same_row(db_session, subject):
    (first,) = _persist(db_session, subject, _declaration())
    (again,) = _persist(db_session, subject, _declaration())
    assert again.id == first.id
    assert len(db_session.scalars(select(StructureDetermination)).all()) == 1
    assert len(db_session.scalars(select(StructureDeterminationSource)).all()) == 2


def test_the_same_determination_with_a_different_claim_is_refused_not_merged(db_session, subject):
    _persist(db_session, subject, _declaration())
    with pytest.raises(CodedValueError) as exc:
        _persist(
            db_session,
            subject,
            _declaration(
                quantity="zero_kelvin_energy", energy_convention={"zero_point_treatment": "unscaled_harmonic"}
            ),
        )
    _mismatch(exc, "content")
    # The stored determination is unchanged.
    stored = db_session.scalars(select(StructureDetermination)).one()
    assert stored.quantity is not None and stored.quantity.value == "electronic_energy"
    assert stored.energy_convention is None


def test_a_different_set_of_calculations_is_a_different_determination(db_session, subject):
    (first,) = _persist(db_session, subject, _declaration())
    other_sp = make_calculation(db_session, type=CalculationType.sp, species_entry_id=subject["entry"].id)
    attach_input_geometry(db_session, calculation=other_sp, geometry=subject["geometry"])
    calcs = {**subject["calcs"], "sp2": other_sp}
    (second,) = persist_structure_determinations(
        db_session,
        [_declaration(sources=[{"role": "energy", "calculation_key": "sp2"}])],
        owner=DeterminationOwner(species_entry_id=subject["entry"].id),
        calculations_by_key=calcs,
    )
    assert second.id != first.id
    assert second.public_ref != first.public_ref


def test_one_request_cannot_state_the_same_determination_twice(db_session, subject):
    with pytest.raises(CodedValueError) as exc:
        _persist(db_session, subject, _declaration(), _declaration())
    _mismatch(exc, "content")


def test_a_source_geometry_is_recorded_only_where_the_calculation_type_names_one_side(db_session, subject):
    composite = make_calculation(db_session, type=CalculationType.composite, species_entry_id=subject["entry"].id)
    attach_input_geometry(db_session, calculation=composite, geometry=subject["geometry"])
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=subject["entry"].id)
    # Two input geometries: no single one is named.
    attach_input_geometry(db_session, calculation=freq, geometry=subject["geometry"])
    attach_input_geometry(db_session, calculation=freq, geometry=make_geometry(db_session), input_order=2)
    calcs = {**subject["calcs"], "composite": composite, "freq": freq}
    (row,) = persist_structure_determinations(
        db_session,
        [
            _declaration(
                sources=[
                    {"role": "geometry_optimization", "calculation_key": "opt"},
                    {"role": "energy", "calculation_key": "sp"},
                    {"role": "correction", "calculation_key": "composite"},
                    {"role": "curvature", "calculation_key": "freq"},
                ]
            )
        ],
        owner=DeterminationOwner(species_entry_id=subject["entry"].id),
        calculations_by_key=calcs,
    )
    geometry = {s.role: s.geometry_id for s in row.sources}
    assert geometry[StructureSourceRole.geometry_optimization] == subject["geometry"].id
    assert geometry[StructureSourceRole.energy] == subject["geometry"].id
    # A composite names no side here, and a frequency job with two input geometries names none: NULL, never a guess.
    assert geometry[StructureSourceRole.correction] is None
    assert geometry[StructureSourceRole.curvature] is None


def test_an_ambiguous_evaluated_geometry_is_refused(db_session, subject):
    second = make_geometry(db_session)
    attach_output_geometry(db_session, calculation=subject["calcs"]["opt"], geometry=second, output_order=2)
    with pytest.raises(CodedValueError) as exc:
        _persist(db_session, subject, _declaration())
    _mismatch(exc, "geometry")


def test_a_basin_needs_the_observation_the_upload_created(db_session, subject):
    with pytest.raises(CodedValueError) as exc:
        _persist(db_session, subject, _declaration(target_kind="conformer_basin"))
    _mismatch(exc, "target")


def test_a_pin_naming_another_owners_calculation_is_refused(db_session, subject):
    other = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))
    foreign = make_calculation(db_session, type=CalculationType.sp, species_entry_id=other.id)
    attach_input_geometry(db_session, calculation=foreign, geometry=subject["geometry"])
    calcs = {**subject["calcs"], "foreign": foreign}
    with pytest.raises(CodedValueError) as exc:
        persist_structure_determinations(
            db_session,
            [_declaration(sources=[{"role": "energy", "calculation_key": "foreign"}])],
            owner=DeterminationOwner(species_entry_id=subject["entry"].id),
            calculations_by_key=calcs,
        )
    _mismatch(exc, "owner")


def test_the_identity_hash_separates_what_is_a_different_claim_and_the_content_hash_what_restates_it():
    owner = DeterminationOwner(species_entry_id=1)
    base = {
        "owner": owner,
        "target_kind": "geometry",
        "literature_id": None,
        "workflow_tool_release_id": 5,
        "determination_key": "k",
        "evaluated_geometry_id": 9,
        "sources": [("energy", 3)],
    }
    reference = identity_hash(**base)
    for change in (
        {"owner": DeterminationOwner(species_entry_id=2)},
        {"target_kind": "conformer_basin"},
        {"workflow_tool_release_id": 6},
        {"determination_key": "other"},
        {"evaluated_geometry_id": 10},
        {"sources": [("energy", 4)]},
        {"sources": [("energy", 3), ("curvature", 3)]},
    ):
        assert identity_hash(**{**base, **change}) != reference, change
    # Source order does not matter; the source set does.
    assert identity_hash(**{**base, "sources": [("curvature", 3), ("energy", 3)]}) == identity_hash(
        **{**base, "sources": [("energy", 3), ("curvature", 3)]}
    )
    claim = {"quantity": "electronic_energy", "energy_convention": None, "actual_recipe": None}
    assert content_hash(**claim) != content_hash(**{**claim, "quantity": None})
    assert content_hash(**claim) != content_hash(**{**claim, "actual_recipe": {"version": 1}})
    assert content_hash(**claim) == content_hash(**claim)
