"""What the loader may reveal, count and load: hidden records, dependency shapes, and the Hessian payload.

A record the read profile hides must behave exactly like one that does not exist, in every field the loader fills,
every count it reports and every bound it enforces. Each test here builds the hidden record, runs the same request
under the curated profile (which hides everything below ``approved``) and under the default one (the control: the
record is visible, so the assertion is sensitive to the thing it forbids).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event

from app.api.error_contract import CodedValueError
from app.db.models.common import (
    CalculationDependencyRole,
    RecordReviewStatus,
    StructureDeterminationTargetKind,
    SubmissionRecordType,
)
from app.db.models.transition_state import TransitionStateValidationEvidence
from app.services.structure_selection import SelectionBounds, assess_entry_structures
from app.services.structure_selection.bounds import parse_quantity
from tests.services.scientific_read._factories import (
    attach_dependency,
    attach_hessian,
    make_conformer_group,
    make_conformer_observation,
    set_review,
)
from tests.services.structure_selection._support import (
    curated,
    declaration,
    make_ts_world,
    make_world,
    minimum_request,
    request,
    reset_current_read_profile,
    saddle_request,
)
from tests.services.structure_selection.test_service import build_basin, by_ref, run


def dump(result) -> str:
    """Everything a caller can read off a result, as text (a ref anywhere in it is a leak)."""
    return json.dumps(
        {
            "calculations": [c.to_dict() for c in result.calculations],
            "determinations": [d.to_dict() for d in result.determinations],
            "assessments": [a.to_dict() for a in result.assessments],
            "excluded": result.excluded_by_review,
            "notes": result.notes,
        },
        default=str,
    )


def under_curated(session, world, req):
    token = curated()
    try:
        return run(session, world, req)
    finally:
        reset_current_read_profile(token)


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def _basin_over_a_hidden_chain(session, *, hidden_opt: bool):
    """A basin whose optimisation (and an ancestor of it) the curated profile hides."""
    world = make_world(session)
    ok = RecordReviewStatus.approved
    ancestor = world.opt("anc", -76.1, declared=declaration(), status=None)
    opt = world.opt("o", -76.41, declared=declaration(), status=None if hidden_opt else ok)
    sp = world.sp("s", -76.4, declared=declaration())
    freq = world.freq("f", declared=declaration())
    attach_dependency(session, parent=ancestor, child=opt, role=CalculationDependencyRole.optimized_from)
    attach_dependency(session, parent=opt, child=sp, role=CalculationDependencyRole.single_point_on)
    attach_dependency(session, parent=opt, child=freq, role=CalculationDependencyRole.freq_on)
    world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", freq)])
    return world, ancestor, opt


@pytest.mark.parametrize("hidden_opt", [True, False])
def test_a_hidden_ancestor_never_appears_in_a_visible_calculations_lineage(db_session, hidden_opt):
    world, ancestor, opt = _basin_over_a_hidden_chain(db_session, hidden_opt=hidden_opt)
    control = run(db_session, world, minimum_request())
    assert ancestor.public_ref in dump(control)  # visible under the default profile, so the check below can fail
    result = under_curated(db_session, world, minimum_request())
    text = dump(result)
    assert ancestor.public_ref not in text
    if hidden_opt:
        assert opt.public_ref not in text
        # The pinned optimisation reads as unavailable, with the one generic reason that names no record.
        roles = {s.role: s for s in result.determinations[0].sources}
        assert roles["geometry_optimization"].calculation_ref is None
        assert roles["geometry_optimization"].unavailable_reason == "required_evidence_unavailable"
    sp = next(c for c in result.calculations if c.type == "sp")
    assert all(opt.public_ref != e.parent_ref or not hidden_opt for e in sp.lineage)


def test_a_hidden_ancestor_cannot_cause_or_hide_a_depth_refusal(db_session):
    world = make_world(db_session)
    sp = world.sp("s", -76.4, declared=declaration())
    freq = world.freq("f", declared=declaration())
    opt = world.opt("o", -76.41, declared=declaration())
    attach_dependency(db_session, parent=opt, child=sp, role=CalculationDependencyRole.single_point_on)
    attach_dependency(db_session, parent=opt, child=freq, role=CalculationDependencyRole.freq_on)
    # Above the optimisation: a hidden calculation, then a long visible chain only it connects to.
    above = world.opt("h", -76.0, declared=declaration(), status=None)
    attach_dependency(db_session, parent=above, child=opt, role=CalculationDependencyRole.optimized_from)
    previous = above
    for i in range(10):
        link = world.opt(f"c{i}", -75.0 - i, declared=declaration())
        attach_dependency(db_session, parent=link, child=previous, role=CalculationDependencyRole.optimized_from)
        previous = link
    world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", freq)])
    bounds = SelectionBounds(dependency_depth=3)
    # The control: with every record visible the required traversal does continue past the bound.
    with pytest.raises(CodedValueError) as exc:
        run(db_session, world, minimum_request(bounds=bounds))
    assert exc.value.code == "structure_selection_traversal_too_deep"
    # Under the curated profile the hidden calculation is absent, so the walk ends there and nothing is refused:
    # neither the refusal nor its absence tells the caller anything lies beyond.
    result = under_curated(db_session, world, minimum_request(bounds=bounds))
    assert result.visible_units == 1


# ---------------------------------------------------------------------------
# Evidence, observations and counts
# ---------------------------------------------------------------------------


def test_a_hidden_irc_calculation_neither_appears_nor_certifies_connectivity(db_session):
    world = make_ts_world(db_session, entry_status=None)
    opt = world.opt("o", -40.2, declared=declaration())
    sp = world.sp("s", -40.3, declared=declaration())
    freq = world.freq("f", frequencies=(-1500.0, 100.0, 200.0), declared=declaration())
    irc = world.opt("irc", None, declared=declaration(), status=None)  # hidden under the curated profile
    det = world.determination(
        [("geometry_optimization", opt), ("energy", sp), ("curvature", freq), ("connectivity", irc)],
        target=StructureDeterminationTargetKind.saddle_point,
    )
    db_session.add(
        TransitionStateValidationEvidence(
            transition_state_entry_id=world.entry.id, kind="irc", passed=True, rationale="connects the declared wells",
            reconstruction_calculation_id=irc.id, transition_state_geometry_id=world.geometry.id,
        )
    )
    db_session.flush()
    world.entry_status = RecordReviewStatus.approved
    world.settle()
    req = saddle_request(require_connectivity=True)
    control = assess_entry_structures(db_session, entry_id=world.entry.id, request=req, require_snapshot=False)
    assert irc.public_ref in dump(control)
    token = curated()
    try:
        result = assess_entry_structures(db_session, entry_id=world.entry.id, request=req, require_snapshot=False)
    finally:
        reset_current_read_profile(token)
    assert irc.public_ref not in dump(result)
    assessment = by_ref(result)[det.public_ref]
    assert not assessment.physically_eligible  # a hidden witness cannot certify
    assert [r.code for r in assessment.reasons if "connectivity" in r.code or "evidence" in r.code] == [
        "required_evidence_unavailable"
    ]


def test_a_visible_calculation_names_no_hidden_observation(db_session):
    world = make_world(db_session)
    obs = make_conformer_observation(db_session, conformer_group=make_conformer_group(db_session, world.entry))
    c = world.sp("c", -76.4, declared=declaration(), observation=obs)
    control = run(db_session, world, request())
    assert control.calculations[0].conformer_observation_ref is not None
    result = under_curated(db_session, world, request())
    assert result.calculations[0].calculation_ref == c.public_ref
    assert result.calculations[0].conformer_observation_ref is None
    assert obs.public_ref not in dump(result)
    set_review(db_session, record_type=SubmissionRecordType.conformer_observation, record_id=obs.id, status=RecordReviewStatus.approved)
    assert under_curated(db_session, world, request()).calculations[0].conformer_observation_ref == obs.public_ref


def test_counts_cover_the_rows_the_caller_can_see_and_nothing_else(db_session):
    def population(with_hidden_source: bool):
        world = make_world(db_session)
        build_basin(world, energy=-76.40)
        if with_hidden_source:
            extra = world.sp("x", -75.0, declared=declaration(), status=None)
            world.determination([("energy", extra)], target=StructureDeterminationTargetKind.geometry, key="extra")
        return world

    plain, with_hidden = population(False), population(True)
    a = under_curated(db_session, plain, minimum_request())
    b = under_curated(db_session, with_hidden, minimum_request())
    # The determination pinning only a hidden calculation is in the population (its owner is visible); its source
    # row and the hidden calculation's result rows are not counted.
    assert b.nested_rows - a.nested_rows == 0
    limit = a.nested_rows
    with pytest.raises(CodedValueError) as exc:
        under_curated(db_session, with_hidden, minimum_request(bounds=SelectionBounds(nested_rows=limit - 1)))
    assert exc.value.context["visible"] == limit


# ---------------------------------------------------------------------------
# The entry's own floor, at every grain
# ---------------------------------------------------------------------------


def test_the_entrys_review_floor_applies_to_a_calculation_as_it_does_to_a_determination(db_session):
    world = make_world(db_session, entry_status=None)  # the entry itself is not reviewed
    c = world.sp("c", -76.4, declared=declaration())
    floor = RecordReviewStatus.approved
    result = run(db_session, world, request(min_review_status=floor))
    assert result.assessments == ()
    assert [(e["unit_ref"], e["reason"]) for e in result.excluded_by_review] == [(c.public_ref, "owner_below_review_floor")]
    # The same entry's determination is excluded for the same reason.
    d_world = make_world(db_session, entry_status=None)
    d, *_ = build_basin(d_world)
    d_result = run(db_session, d_world, minimum_request(min_review_status=floor))
    assert [(e["unit_ref"], e["reason"]) for e in d_result.excluded_by_review] == [(d.public_ref, "owner_below_review_floor")]
    # An approved entry lets the calculation through.
    ok_world = make_world(db_session)
    ok_world.sp("c", -76.4, declared=declaration())
    assert len(run(db_session, ok_world, request(min_review_status=floor)).assessments) == 1


# ---------------------------------------------------------------------------
# Dependency shapes
# ---------------------------------------------------------------------------


def test_a_shared_parent_under_two_paths_is_a_diamond_not_a_cycle(db_session):
    world = make_world(db_session)
    root = world.opt("root", -76.0, declared=declaration())
    left = world.opt("left", -76.1, declared=declaration())
    right = world.opt("right", -76.2, declared=declaration())
    bottom = world.opt("bottom", -76.41, declared=declaration())
    sp = world.sp("s", -76.4, declared=declaration())
    freq = world.freq("f", declared=declaration())
    for parent, child in ((root, left), (root, right)):
        attach_dependency(db_session, parent=parent, child=child, role=CalculationDependencyRole.optimized_from)
    for parent in (left, right):  # a child has one optimized_from parent, so the second path is a composite input
        attach_dependency(db_session, parent=parent, child=bottom, role=CalculationDependencyRole.composite_input)
    attach_dependency(db_session, parent=bottom, child=sp, role=CalculationDependencyRole.single_point_on)
    attach_dependency(db_session, parent=bottom, child=freq, role=CalculationDependencyRole.freq_on)
    d = world.determination([("geometry_optimization", bottom), ("energy", sp), ("curvature", freq)])
    result = run(db_session, world, minimum_request())
    a = by_ref(result)[d.public_ref]
    assert a.physically_eligible and "dependency_cycle" not in {r.code for r in a.reasons}
    sp_facts = next(c for c in result.calculations if c.calculation_ref == sp.public_ref)
    assert sp_facts.lineage_cyclic is False
    assert {e.parent_ref for e in sp_facts.lineage} >= {bottom.public_ref, left.public_ref, right.public_ref, root.public_ref}


def test_a_real_cycle_is_still_a_cycle(db_session):
    world = make_world(db_session)
    a = world.opt("a", -76.4, declared=declaration())
    b = world.opt("b", -76.3, declared=declaration())
    sp = world.sp("s", -76.41, declared=declaration())
    freq = world.freq("f", declared=declaration())
    attach_dependency(db_session, parent=a, child=b, role=CalculationDependencyRole.optimized_from)
    attach_dependency(db_session, parent=b, child=a, role=CalculationDependencyRole.optimized_from)
    attach_dependency(db_session, parent=a, child=sp, role=CalculationDependencyRole.single_point_on)
    d = world.determination([("geometry_optimization", a), ("energy", sp), ("curvature", freq)])
    result = run(db_session, world, minimum_request())
    assessment = by_ref(result)[d.public_ref]
    assert assessment.applicability.value == "unresolved" and any(r.code == "dependency_cycle" for r in assessment.reasons)


# ---------------------------------------------------------------------------
# The Hessian payload
# ---------------------------------------------------------------------------


def test_the_hessian_matrix_is_never_selected(db_session):
    world = make_world(db_session)
    freq = world.freq("f", declared=declaration())
    attach_hessian(db_session, calculation=freq, geometry=world.geometry, natoms=3)
    sp = world.sp("s", -76.4, declared=declaration())
    opt = world.opt("o", -76.41, declared=declaration())
    d = world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", freq)])
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = run(db_session, world, minimum_request())
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert any("calc_hessian" in s for s in statements)  # the existence and geometry were read
    assert not any("lower_triangle_hartree_bohr2" in s for s in statements)
    facts = next(c for c in result.calculations if c.calculation_ref == freq.public_ref)
    assert facts.curvature.has_hessian and facts.curvature.hessian_geometry_ref is not None
    assert d.public_ref in by_ref(result)


# ---------------------------------------------------------------------------
# Deferred quantities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["enthalpy", "enthalpy_298", "gibbs_energy", "entropy", "heat_capacity", "barrier", "rate"])
def test_a_recognised_deferred_quantity_is_a_structured_refusal(name):
    with pytest.raises(CodedValueError) as exc:
        parse_quantity(name)
    assert exc.value.code == "structure_selection_unsupported"
    assert exc.value.context == {"reason": "deferred_quantity", "quantity": name}


def test_a_supported_quantity_parses_and_an_unknown_one_is_an_invalid_request():
    assert parse_quantity("electronic_energy").value == "electronic_energy"
    assert parse_quantity("zero_kelvin_energy").value == "zero_kelvin_energy"
    with pytest.raises(ValueError, match="unknown quantity"):
        parse_quantity("kinetic_energy")
