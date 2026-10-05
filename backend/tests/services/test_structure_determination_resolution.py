"""Resolving structure determinations: what is recorded, what is refused, and what is never guessed."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tckdb_schemas.structure_declarations import StructureDeterminationDeclaration

from app.api.error_contract import CodedValueError
from app.db.models.common import CalculationType, StructureSourceRole
from app.db.models.geometry import GeometryAtom
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from app.services.calculation_geometry_composition import _species_entry_reference
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
    make_conformer_group,
    make_conformer_observation,
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


def _geometry_of(session, entry):
    """A stored geometry made of exactly the atoms of ``entry``'s species.

    A source repeats the geometry its calculation is linked to, and the write path checks that composition (as every
    link of a geometry to a calculation is checked), so a fixture that links an empty geometry to a methane
    calculation is a fixture the application would have refused.
    """
    counts = _species_entry_reference(session, entry.id)
    atoms = [element for element, n in sorted(counts.items()) for _ in range(n)]
    geometry = make_geometry(session, natoms=len(atoms))
    for index, element in enumerate(atoms):
        session.add(GeometryAtom(geometry_id=geometry.id, atom_index=index + 1, element=element, x=float(index), y=0.0, z=0.0))
    session.flush()
    return geometry


@pytest.fixture
def subject(db_session):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))
    geometry = _geometry_of(db_session, entry)
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


def test_the_same_key_over_a_different_set_of_calculations_is_refused_and_another_key_is_another_determination(
    db_session, subject
):
    (first,) = _persist(db_session, subject, _declaration())
    other_sp = make_calculation(db_session, type=CalculationType.sp, species_entry_id=subject["entry"].id)
    attach_input_geometry(db_session, calculation=other_sp, geometry=subject["geometry"])
    calcs = {**subject["calcs"], "sp2": other_sp}
    restated = _declaration(
        sources=[
            {"role": "geometry_optimization", "calculation_key": "opt"},
            {"role": "energy", "calculation_key": "sp2"},
        ]
    )
    with pytest.raises(CodedValueError) as exc:
        persist_structure_determinations(
            db_session, [restated], owner=DeterminationOwner(species_entry_id=subject["entry"].id), calculations_by_key=calcs
        )
    _mismatch(exc, "content")
    # The key is the identifier: a different set of calculations is stated under a different key.
    (second,) = persist_structure_determinations(
        db_session,
        [_declaration(key="d2", sources=restated.model_dump(mode="json")["sources"])],
        owner=DeterminationOwner(species_entry_id=subject["entry"].id),
        calculations_by_key=calcs,
    )
    assert second.id != first.id and second.public_ref != first.public_ref
    assert {s.calculation_id for s in first.sources} != {s.calculation_id for s in second.sources}


def test_one_request_cannot_state_the_same_key_twice(db_session, subject):
    with pytest.raises(CodedValueError) as exc:
        _persist(db_session, subject, _declaration(), _declaration())
    _mismatch(exc, "content")


def test_the_same_key_for_another_owner_or_source_is_another_determination(db_session, subject):
    (first,) = _persist(db_session, subject, _declaration())
    other_entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))
    geometry = _geometry_of(db_session, other_entry)
    opt = make_calculation(db_session, type=CalculationType.opt, species_entry_id=other_entry.id)
    attach_output_geometry(db_session, calculation=opt, geometry=geometry)
    sp = make_calculation(db_session, type=CalculationType.sp, species_entry_id=other_entry.id)
    attach_input_geometry(db_session, calculation=sp, geometry=geometry)
    (elsewhere,) = persist_structure_determinations(
        db_session, [_declaration()], owner=DeterminationOwner(species_entry_id=other_entry.id),
        calculations_by_key={"opt": opt, "sp": sp},
    )
    assert elsewhere.id != first.id
    (other_source,) = _persist(db_session, subject, _declaration(workflow_tool_release={"name": "ARC", "version": "9.9.9"}))
    assert other_source.id != first.id


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


def test_the_identity_hash_is_the_identifier_and_the_content_hash_is_everything_else():
    owner = DeterminationOwner(species_entry_id=1)
    base = {
        "owner": owner,
        "target_kind": "geometry",
        "literature_id": None,
        "workflow_tool_release_id": 5,
        "determination_key": "k",
    }
    reference = identity_hash(**base)
    for change in (
        {"owner": DeterminationOwner(species_entry_id=2)},
        {"owner": DeterminationOwner(transition_state_entry_id=1)},
        {"literature_id": 3},
        {"workflow_tool_release_id": 6},
        {"determination_key": "other"},
    ):
        assert identity_hash(**{**base, **change}) != reference, change
    # An observation matters to a basin claim only: a geometry claim is the same claim whichever upload created an
    # observation beside it.
    with_observation = DeterminationOwner(species_entry_id=1, conformer_observation_id=7)
    assert identity_hash(**{**base, "owner": with_observation}) == reference
    basin = {**base, "target_kind": "conformer_basin"}
    assert identity_hash(**{**basin, "owner": with_observation}) != identity_hash(
        **{**basin, "owner": DeterminationOwner(species_entry_id=1, conformer_observation_id=8)}
    )
    # What the determination claims is not in the identifier.
    assert identity_hash(**{**base, "target_kind": "saddle_point"}) == reference
    claim = {
        "target_kind": "geometry",
        "quantity": "electronic_energy",
        "energy_convention": None,
        "actual_recipe": None,
        "evaluated_geometry_id": 9,
        "sources": [("energy", 3)],
    }
    reference_content = content_hash(**claim)
    for change in (
        {"target_kind": "saddle_point"},
        {"quantity": None},
        {"energy_convention": {"zero_point_treatment": "scaled_harmonic"}},
        {"actual_recipe": {"version": 1}},
        {"evaluated_geometry_id": 10},
        {"sources": [("energy", 4)]},
        {"sources": [("energy", 3), ("curvature", 3)]},
    ):
        assert content_hash(**{**claim, **change}) != reference_content, change
    # Source order does not matter; the source set does.
    assert content_hash(**{**claim, "sources": [("curvature", 3), ("energy", 3)]}) == content_hash(
        **{**claim, "sources": [("energy", 3), ("curvature", 3)]}
    )
    assert content_hash(**claim) == reference_content


def _basin_world(db_session):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))
    group = make_conformer_group(db_session, entry)
    mine = make_conformer_observation(db_session, conformer_group=group)
    elsewhere = make_conformer_observation(db_session, conformer_group=group)
    geometry = _geometry_of(db_session, entry)

    def calc(kind, observation, *, side="input"):
        c = make_calculation(db_session, type=kind, species_entry_id=entry.id, conformer_observation_id=observation.id if observation else None)
        (attach_input_geometry if side == "input" else attach_output_geometry)(db_session, calculation=c, geometry=geometry)
        return c

    return entry, mine, elsewhere, calc


def _basin(**changes):
    return _declaration(target_kind="conformer_basin", **changes)


def test_a_basin_claim_pins_only_calculations_anchored_to_its_own_observation(db_session):
    entry, mine, elsewhere, calc = _basin_world(db_session)
    opt = calc(CalculationType.opt, mine, side="output")
    good_sp = calc(CalculationType.sp, mine)
    foreign_sp = calc(CalculationType.sp, elsewhere)
    unanchored_sp = calc(CalculationType.sp, None)
    owner = DeterminationOwner(species_entry_id=entry.id, conformer_observation_id=mine.id)
    persist_structure_determinations(
        db_session, [_basin(key="ok")], owner=owner, calculations_by_key={"opt": opt, "sp": good_sp}
    )
    for bad in (foreign_sp, unanchored_sp):
        with pytest.raises(CodedValueError) as exc:
            persist_structure_determinations(
                db_session, [_basin(key="bad")], owner=owner, calculations_by_key={"opt": opt, "sp": bad}
            )
        _mismatch(exc, "observation")
    # The evaluating calculation is judged the same way, and the owner check comes first when it fails.
    with pytest.raises(CodedValueError) as exc:
        persist_structure_determinations(
            db_session,
            [_basin(key="bad2", evaluated_geometry={"calculation_key": "other"}, sources=[
                {"role": "energy", "calculation_key": "sp"}, {"role": "geometry_optimization", "calculation_key": "other"}])],
            owner=owner,
            calculations_by_key={"sp": good_sp, "other": calc(CalculationType.opt, elsewhere, side="output")},
        )
    _mismatch(exc, "observation")
    # A calculation of another entry fails on its owner before its observation is looked at.
    stranger = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))
    foreign_owner = make_calculation(db_session, type=CalculationType.sp, species_entry_id=stranger.id, conformer_observation_id=mine.id)
    attach_input_geometry(db_session, calculation=foreign_owner, geometry=make_geometry(db_session))
    with pytest.raises(CodedValueError) as exc:
        persist_structure_determinations(
            db_session, [_basin(key="bad3")], owner=owner, calculations_by_key={"opt": opt, "sp": foreign_owner}
        )
    _mismatch(exc, "owner")


def test_a_geometry_claim_may_pin_unanchored_calculations(db_session):
    entry, mine, elsewhere, calc = _basin_world(db_session)
    opt = calc(CalculationType.opt, None, side="output")
    sp = calc(CalculationType.sp, elsewhere)
    persist_structure_determinations(
        db_session, [_declaration(key="geometry-claim")], owner=DeterminationOwner(species_entry_id=entry.id, conformer_observation_id=mine.id),
        calculations_by_key={"opt": opt, "sp": sp},
    )


def test_the_evaluating_calculation_must_belong_to_the_owner_and_be_one_of_the_pinned_sources(db_session, subject):
    entry = subject["entry"]
    stranger = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key()))
    geometry = _geometry_of(db_session, stranger)
    foreign = make_calculation(db_session, type=CalculationType.opt, species_entry_id=stranger.id)
    attach_output_geometry(db_session, calculation=foreign, geometry=geometry)
    calcs = {**subject["calcs"], "foreign": foreign}
    with pytest.raises(CodedValueError) as exc:
        persist_structure_determinations(
            db_session, [_declaration(evaluated_geometry={"calculation_key": "foreign"})],
            owner=DeterminationOwner(species_entry_id=entry.id), calculations_by_key=calcs,
        )
    _mismatch(exc, "owner")
    # A calculation of the owner that the determination does not pin cannot supply its geometry either.
    extra = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    attach_output_geometry(db_session, calculation=extra, geometry=make_geometry(db_session))
    with pytest.raises(CodedValueError) as exc:
        persist_structure_determinations(
            db_session, [_declaration(key="unpinned", evaluated_geometry={"calculation_key": "extra"})],
            owner=DeterminationOwner(species_entry_id=entry.id), calculations_by_key={**subject["calcs"], "extra": extra},
        )
    _mismatch(exc, "geometry")


def test_a_frequency_job_describes_its_input_geometry_not_its_output(db_session, subject):
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=subject["entry"].id)
    attach_input_geometry(db_session, calculation=freq, geometry=subject["geometry"])
    attach_output_geometry(db_session, calculation=freq, geometry=make_geometry(db_session))
    calcs = {**subject["calcs"], "freq": freq}
    (row,) = persist_structure_determinations(
        db_session,
        [_declaration(sources=[
            {"role": "geometry_optimization", "calculation_key": "opt"},
            {"role": "energy", "calculation_key": "sp"},
            {"role": "curvature", "calculation_key": "freq"},
        ])],
        owner=DeterminationOwner(species_entry_id=subject["entry"].id),
        calculations_by_key=calcs,
    )
    by_role = {s.role: s.geometry_id for s in row.sources}
    assert by_role[StructureSourceRole.curvature] == subject["geometry"].id
    assert by_role[StructureSourceRole.curvature] != freq.output_geometries[0].geometry_id
