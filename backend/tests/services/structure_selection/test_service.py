"""Structure assessment against real rows: loading, the authorized population, bounds, visibility, snapshots,
and the guarantee that nothing is written."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, text

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationQuality,
    RecordReviewStatus,
    StructureDeterminationQuantity,
    StructureDeterminationTargetKind,
    StructureFindingKind,
    StructureFindingVerdict,
    StructureSourceRole,
    SubmissionRecordType,
)
from app.db.models.structure_determination import (
    StructureDetermination,
    StructureDeterminationSource,
    StructureEvidenceFinding,
)
from app.services.read_snapshot import SnapshotNotConsistentError
from app.services.selection_kernel import Applicability as A
from app.services.structure_selection import (
    Grain,
    Intent,
    SelectionBounds,
    StructureRequest,
    ValidationClaim,
    assess_entry_structures,
)
from tests.services.scientific_read._factories import (
    attach_dependency,
    make_conformer_group,
    make_conformer_observation,
    make_geometry,
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


def run(session, world, req, **kw):
    world.settle()
    return assess_entry_structures(session, entry_id=world.entry.id, request=req, require_snapshot=False, **kw)


def by_ref(result):
    return {a.unit_ref: a for a in result.assessments}


def counts(session):
    return tuple(
        session.scalar(select(func.count()).select_from(m))
        for m in (Calculation, StructureDetermination, StructureDeterminationSource, StructureEvidenceFinding)
    )


# ---------------------------------------------------------------------------
# Calculation grain
# ---------------------------------------------------------------------------


def test_finite_null_and_empty_populations_are_distinct(db_session):
    empty = make_world(db_session)
    result = run(db_session, empty, request())
    assert (result.visible_units, result.assessments, result.unresolved_refs) == (0, (), ())

    world = make_world(db_session)
    finite = world.sp("a", -76.40, declared=declaration())
    null = world.sp("b", None, declared=declaration())
    result = run(db_session, world, request())
    assessments = by_ref(result)
    assert assessments[finite.public_ref].applicability is A.applicable
    assert assessments[finite.public_ref].energy.hartree == -76.40
    assert assessments[null.public_ref].applicability is A.unresolved and assessments[null.public_ref].energy is None
    assert result.unresolved_refs == (null.public_ref,)
    assert result.visible_units == 2 and result.total_units == 2

    only_null = make_world(db_session)
    only_null.sp("a", None)
    r = run(db_session, only_null, request())
    assert [a.applicability for a in r.assessments] == [A.unresolved]


def test_a_null_single_point_does_not_suppress_or_replace_the_optimisation_value(db_session):
    world = make_world(db_session)
    sp = world.sp("sp", None, declared=declaration())
    opt = world.opt("opt", -76.3, declared=declaration())
    result = run(db_session, world, request())
    a = by_ref(result)
    assert a[opt.public_ref].energy.hartree == -76.3 and a[opt.public_ref].energy.calculation_ref == opt.public_ref
    # The optimisation answers as itself; no single point is fabricated from it, and the null one stays null.
    assert a[sp.public_ref].energy is None and a[sp.public_ref].applicability is A.unresolved
    assert {c.calculation_ref for c in result.calculations} == {sp.public_ref, opt.public_ref}


def test_an_unconverged_optimisation_is_a_labelled_value(db_session):
    world = make_world(db_session)
    bad = world.opt("opt", -77.0, converged=False, declared=declaration())
    a = by_ref(run(db_session, world, request()))[bad.public_ref]
    assert a.applicability is A.applicable and a.energy.scope.value == "unconverged_endpoint"


def test_review_status_quality_and_the_callers_floor_exclude_with_a_reason_that_names_only_visible_records(db_session):
    world = make_world(db_session)
    good = world.sp("good", -76.4, declared=declaration())
    rejected_review = world.sp("rej", -76.9, status=RecordReviewStatus.rejected)
    deprecated = world.sp("dep", -76.8, status=RecordReviewStatus.deprecated)
    unreviewed = world.sp("unrev", -76.7, status=None)
    rejected_quality = world.sp("rq", -76.6, quality=CalculationQuality.rejected)
    result = run(db_session, world, request(min_review_status=RecordReviewStatus.under_review))
    listed = {e["unit_ref"]: e["reason"] for e in result.excluded_by_review}
    assert listed == {
        rejected_review.public_ref: "terminal_review_status",
        deprecated.public_ref: "terminal_review_status",
        unreviewed.public_ref: "below_review_floor",
        rejected_quality.public_ref: "quality_not_permitted",
    }
    assert [a.unit_ref for a in result.assessments] == [good.public_ref]
    assert result.excluded_count == 4 and result.total_units == 5 and result.visible_units == 1


def test_a_stricter_quality_requirement_qualifies_and_rejected_quality_is_never_automatically_eligible(db_session):
    world = make_world(db_session)
    curated_calc = world.sp("c", -76.4, quality=CalculationQuality.curated, declared=declaration())
    raw = world.sp("r", -76.5, declared=declaration())
    rejected = world.sp("x", -77.0, quality=CalculationQuality.rejected, declared=declaration())
    default = run(db_session, world, request())
    assert {a.unit_ref for a in default.assessments} == {curated_calc.public_ref, raw.public_ref}
    assert {e["unit_ref"]: e["reason"] for e in default.excluded_by_review} == {rejected.public_ref: "quality_not_permitted"}
    strict = run(db_session, world, request(permitted_quality=frozenset({CalculationQuality.curated})))
    assert [a.unit_ref for a in strict.assessments] == [curated_calc.public_ref]


def test_an_unapproved_record_is_hidden_without_trace_under_the_curated_profile(db_session):
    world = make_world(db_session)
    approved = world.sp("a", -76.4, declared=declaration())
    hidden = world.sp("h", -76.9, status=None)
    token = curated()
    try:
        result = run(db_session, world, request())
    finally:
        reset_current_read_profile(token)
    text = json.dumps(
        {"a": [x.to_dict() for x in result.assessments], "e": result.excluded_by_review, "c": result.excluded_count,
         "t": result.total_units}
    )
    assert hidden.public_ref not in text and approved.public_ref in text
    assert (result.total_units, result.visible_units, result.excluded_count) == (1, 1, 0)
    open_ = run(db_session, world, request())
    assert open_.total_units == 2


def test_a_hidden_entry_behaves_exactly_like_a_missing_one(db_session):
    hidden = make_world(db_session, entry_status=RecordReviewStatus.under_review)
    hidden.settle()
    token = curated()
    try:
        with pytest.raises(NotFoundError) as exc_hidden:
            run(db_session, hidden, request())
        with pytest.raises(NotFoundError) as exc_missing:
            assess_entry_structures(
                db_session, entry_id=hidden.entry.id + 10_000_000, request=request(), require_snapshot=False
            )
    finally:
        reset_current_read_profile(token)
    # The same words, the same code: nothing in the refusal says which of the two it was, and no row id is named.
    assert str(exc_hidden.value) == str(exc_missing.value)
    assert str(hidden.entry.id) not in str(exc_hidden.value)


def test_explicit_members_restrict_the_population_and_an_unauthorized_ref_is_not_told_apart_from_a_missing_one(db_session):
    world = make_world(db_session)
    a = world.sp("a", -76.4, declared=declaration())
    b = world.sp("b", -76.5, declared=declaration())
    other = make_world(db_session)
    foreign = other.sp("f", -75.0, declared=declaration())
    hidden = world.sp("h", -76.9, status=None)
    only_a = run(db_session, world, request(member_refs=(a.public_ref,)))
    assert [x.unit_ref for x in only_a.assessments] == [a.public_ref]
    both = run(db_session, world, request(member_refs=(a.public_ref, b.public_ref)))
    assert len(both.assessments) == 2
    errors = []
    for ref in (foreign.public_ref, "calc_" + "z" * 26):
        with pytest.raises(NotFoundError) as exc:
            run(db_session, world, request(member_refs=(ref,)))
        errors.append((exc.value.code, exc.value.context["field"]))
    token = curated()
    try:
        with pytest.raises(NotFoundError) as exc:
            run(db_session, world, request(member_refs=(hidden.public_ref,)))
        errors.append((exc.value.code, exc.value.context["field"]))
    finally:
        reset_current_read_profile(token)
    assert set(errors) == {("unknown_structure_member_ref", "member_refs")}


def test_a_recipe_declared_at_upload_reaches_the_assessment_and_an_unreadable_one_is_withheld(db_session):
    world = make_world(db_session)
    declared = world.sp("d", -76.4, declared=declaration())
    undeclared = world.sp("u", -76.5)
    broken = world.sp("b", -76.6, declared={"version": 1, "not_a_fact": True})
    a = by_ref(run(db_session, world, request()))
    assert a[declared.public_ref].recipe.cohort_key is not None
    assert a[undeclared.public_ref].recipe.cohort_key is None
    assert a[broken.public_ref].recipe.cohort_key is None and "actual_protocol_declaration_unreadable" in a[broken.public_ref].advisory
    calcs = {c.calculation_ref: c for c in run(db_session, world, request()).calculations}
    assert calcs[declared.public_ref].declaration_state == "valid"
    assert calcs[undeclared.public_ref].declaration_state == "absent"
    assert calcs[broken.public_ref].declaration_state == "unreadable"


def test_geometry_validation_and_scf_stability_are_read_from_the_calculations_own_rows(db_session):
    from app.db.models.common import SCFStabilityStatus, ValidationStatus

    world = make_world(db_session)
    c = world.sp("c", -76.4, declared=declaration(), scf=SCFStabilityStatus.unstable, validation=ValidationStatus.fail)
    a = by_ref(run(db_session, world, request()))[c.public_ref]
    assert a.physically_eligible
    assert {"geometry_validation_heuristic_failed", "scf_wavefunction_unstable"} <= set(a.advisory)
    blocked = by_ref(run(db_session, world, request(require_stable_reference=True)))[c.public_ref]
    assert blocked.blocking == ("scf_wavefunction_unstable",)


def test_a_finding_about_a_calculation_blocks_and_only_a_same_subject_adjudication_clears_it(db_session):
    world = make_world(db_session)
    c = world.sp("c", -76.4, declared=declaration())
    bad = world.finding(calculation=c)
    assert by_ref(run(db_session, world, request()))[c.public_ref].blocking
    # An adjudication about the geometry names the finding but is about a different subject: the disproof stands.
    world.finding(
        geometry=world.geometry, kind=StructureFindingKind.adjudication,
        verdict=StructureFindingVerdict.does_not_invalidate, supersedes=bad,
    )
    assert by_ref(run(db_session, world, request()))[c.public_ref].blocking
    world.finding(
        calculation=c, kind=StructureFindingKind.adjudication,
        verdict=StructureFindingVerdict.does_not_invalidate, supersedes=bad,
    )
    assert by_ref(run(db_session, world, request()))[c.public_ref].physically_eligible


def test_nothing_is_written(db_session):
    world = make_world(db_session)
    world.sp("a", -76.4, declared=declaration())
    world.opt("b", -76.3)
    before = counts(db_session)
    run(db_session, world, request())
    run(db_session, world, minimum_request())
    assert counts(db_session) == before


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_a_population_over_the_candidate_bound_is_refused_before_anything_is_loaded(db_session, monkeypatch):
    from app.services.structure_selection import service as service_module

    world = make_world(db_session)
    for i in range(3):
        world.sp(f"c{i}", -76.0 - i, declared=declaration())
    world.sp("unreviewed", -75.0, status=None)  # visible under the default profile, hidden under curated
    with monkeypatch.context() as patched:
        patched.setattr(service_module, "load_population", lambda *a, **k: pytest.fail("loaded a refused population"))
        with pytest.raises(CodedValueError) as exc:
            run(db_session, world, request(bounds=SelectionBounds(candidates=3)))
    assert exc.value.code == "structure_selection_population_too_large"
    assert exc.value.context == {"bound": "candidates", "visible": 4, "limit": 3, "bounds_version": "1"}
    # Exactly at the limit is decided: under the curated profile the unreviewed row is not part of the world.
    token = curated()
    try:
        at_limit = run(db_session, world, request(bounds=SelectionBounds(candidates=3)))
    finally:
        reset_current_read_profile(token)
    assert at_limit.visible_units == 3


def test_the_refusal_names_visible_rows_only(db_session):
    world = make_world(db_session)
    for i in range(2):
        world.sp(f"c{i}", -76.0 - i, declared=declaration())
    for i in range(5):
        world.sp(f"h{i}", -75.0 - i, status=None)
    token = curated()
    try:
        with pytest.raises(CodedValueError) as exc:
            run(db_session, world, request(bounds=SelectionBounds(candidates=1)))
    finally:
        reset_current_read_profile(token)
    assert exc.value.context["visible"] == 2  # never 7


def test_nested_evidence_over_its_bound_is_refused_and_the_boundary_is_decided(db_session):
    world = make_world(db_session)
    world.sp("a", -76.4, declared=declaration())
    world.sp("b", -76.3, declared=declaration())
    probe = run(db_session, world, request())
    n = probe.nested_rows
    assert n > 0
    ok = run(db_session, world, request(bounds=SelectionBounds(nested_rows=n)))
    assert ok.visible_units == 2
    with pytest.raises(CodedValueError) as exc:
        run(db_session, world, request(bounds=SelectionBounds(nested_rows=n - 1)))
    assert exc.value.code == "structure_selection_evidence_too_large"
    assert exc.value.context["visible"] == n and exc.value.context["limit"] == n - 1


def test_a_required_traversal_past_the_depth_bound_is_refused_rather_than_claimed_complete(db_session):
    world = make_world(db_session)
    o1 = world.opt("o1", -76.4, declared=declaration())
    sp = world.sp("sp", -76.41, declared=declaration())
    f = world.freq("f", declared=declaration())
    o0 = world.opt("o0", -76.2, declared=declaration())
    attach_dependency(db_session, parent=o1, child=sp, role=CalculationDependencyRole.single_point_on)
    attach_dependency(db_session, parent=o0, child=o1, role=CalculationDependencyRole.optimized_from)
    d = world.determination([("geometry_optimization", o1), ("energy", sp), ("curvature", f)])
    ok = run(db_session, world, minimum_request(bounds=SelectionBounds(dependency_depth=2)))
    assert d.public_ref in by_ref(ok)
    with pytest.raises(CodedValueError) as exc:
        run(db_session, world, minimum_request(bounds=SelectionBounds(dependency_depth=1)))
    assert exc.value.code == "structure_selection_traversal_too_deep"


def test_a_dependency_cycle_is_reported_and_never_walked_forever(db_session):
    world = make_world(db_session)
    o = world.opt("o", -76.4, declared=declaration())
    sp = world.sp("sp", -76.41, declared=declaration())
    f = world.freq("f", declared=declaration())
    attach_dependency(db_session, parent=o, child=sp, role=CalculationDependencyRole.single_point_on)
    attach_dependency(db_session, parent=sp, child=o, role=CalculationDependencyRole.optimized_from)
    d = world.determination([("geometry_optimization", o), ("energy", sp), ("curvature", f)])
    a = by_ref(run(db_session, world, minimum_request()))[d.public_ref]
    assert a.applicability is A.unresolved and any(r.code == "dependency_cycle" for r in a.reasons)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_a_decision_needs_a_read_only_snapshot_unless_the_caller_says_it_accepts_what_it_has(db_session):
    world = make_world(db_session)
    world.sp("a", -76.4)
    with pytest.raises(SnapshotNotConsistentError):
        assess_entry_structures(db_session, entry_id=world.entry.id, request=request())
    result = run(db_session, world, request())
    assert result.snapshot_isolation in {"read committed", "repeatable read", "serializable"}


# ---------------------------------------------------------------------------
# Determination grain
# ---------------------------------------------------------------------------


def build_basin(
    world, *, energy=-76.4, lot=None, geometry=None, declared=None, n_freq=(100.0, 200.0), wire=True, statuses=None, qualities=None, **det
):
    declared = declaration() if declared is None else declared
    obs = det.get("observation")
    statuses = statuses or {}
    qualities = qualities or {}
    ok = RecordReviewStatus.approved
    opt = world.opt(f"o{len(world.calcs)}", energy + 0.01, declared=declared, lot=lot, geometry=geometry, observation=obs, status=statuses.get("opt", ok),
                    quality=qualities.get("opt"))
    sp = world.sp(f"s{len(world.calcs)}", energy, declared=declared, lot=lot, geometry=geometry, observation=obs, status=statuses.get("sp", ok),
                  quality=qualities.get("sp"))
    f = world.freq(f"f{len(world.calcs)}", frequencies=n_freq, declared=declared, lot=lot, geometry=geometry, observation=obs, status=statuses.get("freq", ok),
                   quality=qualities.get("freq"))
    if wire:
        attach_dependency(world.session, parent=opt, child=sp, role=CalculationDependencyRole.single_point_on)
        attach_dependency(world.session, parent=opt, child=f, role=CalculationDependencyRole.freq_on)
    d = world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", f)], evaluated=geometry, **det)
    return d, opt, sp, f


def test_a_validated_minimum_is_assessed_from_its_own_pinned_calculations(db_session):
    world = make_world(db_session)
    d, opt, sp, f = build_basin(world)
    result = run(db_session, world, minimum_request())
    a = by_ref(result)[d.public_ref]
    assert a.applicability is A.applicable and a.physically_eligible
    assert a.energy.calculation_ref == sp.public_ref and a.energy.hartree == -76.4
    assert a.claim.supported and a.claim.witness_refs == (f.public_ref,)
    assert not [x for x in a.advisory if x.startswith("source_not_linked")]
    assert result.visible_units == 1 and {c.calculation_ref for c in result.calculations} == {
        opt.public_ref, sp.public_ref, f.public_ref
    }
    assert result.determinations[0].evaluated_geometry_ref == opt.output_geometries[0].geometry.public_ref


def test_curvature_on_another_geometry_of_the_same_entry_is_not_borrowed(db_session):
    world = make_world(db_session)
    other_geometry = make_geometry(db_session)
    opt = world.opt("o", -76.41, declared=declaration())
    sp = world.sp("s", -76.4, declared=declaration())
    elsewhere = world.freq("f", declared=declaration(), geometry=other_geometry)
    d = world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", elsewhere)])
    a = by_ref(run(db_session, world, minimum_request()))[d.public_ref]
    assert a.applicability is A.unresolved and "curvature_on_other_geometry" in a.advisory
    assert not a.claim.supported


def test_several_determinations_of_one_basin_are_kept_as_separate_candidates(db_session):
    world = make_world(db_session)
    d1, *_ = build_basin(world, energy=-76.40)
    d2, *_ = build_basin(world, energy=-76.45)
    result = run(db_session, world, minimum_request())
    assert {a.unit_ref for a in result.assessments} == {d1.public_ref, d2.public_ref}
    assert all(a.physically_eligible for a in result.assessments)


def test_a_source_hidden_by_the_profile_reads_generically_and_names_nothing(db_session):
    world = make_world(db_session)
    # The energy source is below the curated floor; the determination and its other sources are visible.
    d, opt, sp, f = build_basin(world, statuses={"sp": None})
    token = curated()
    try:
        result = run(db_session, world, minimum_request())
    finally:
        reset_current_read_profile(token)
    a = by_ref(result)[d.public_ref]
    assert a.applicability is A.unresolved and [r.code for r in a.reasons] == ["required_evidence_unavailable"]
    text = json.dumps(
        {
            "a": [x.to_dict() for x in result.assessments],
            "c": [x.to_dict() for x in result.calculations],
            "d": [x.to_dict() for x in result.determinations],
        }
    )
    assert sp.public_ref not in text
    # Outside the curated profile the same unreviewed source is visible, and the caller's own floor names why.
    floor = run(db_session, world, minimum_request(min_review_status=RecordReviewStatus.approved))
    assert [r.code for r in by_ref(floor)[d.public_ref].reasons] == ["source_below_review_floor"]
    assert by_ref(run(db_session, world, minimum_request()))[d.public_ref].physically_eligible


def test_a_rejected_source_and_a_rejected_quality_source_are_not_usable_evidence(db_session):
    world = make_world(db_session)
    rejected, *_ = build_basin(world, statuses={"freq": RecordReviewStatus.rejected})
    a = by_ref(run(db_session, world, minimum_request()))[rejected.public_ref]
    assert [r.code for r in a.reasons] == ["source_terminal_review_status"]
    poor = make_world(db_session)
    d, opt, sp, f = build_basin(poor, qualities={"freq": CalculationQuality.rejected})
    b = by_ref(run(db_session, poor, minimum_request()))[d.public_ref]
    assert [r.code for r in b.reasons] == ["source_quality_not_permitted"]


def test_the_owning_observation_is_judged_at_its_own_grain_not_through_its_group(db_session):
    world = make_world(db_session)
    group = make_conformer_group(db_session, world.entry)
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=group.id, status=RecordReviewStatus.approved)
    obs = make_conformer_observation(db_session, conformer_group=group)
    d, *_ = build_basin(world, target=StructureDeterminationTargetKind.conformer_basin, observation=obs)
    open_ = run(db_session, world, minimum_request())
    assert d.public_ref in by_ref(open_)
    floor = run(db_session, world, minimum_request(min_review_status=RecordReviewStatus.approved))
    # The group is approved; the observation is not, and group approval does not approve it.
    assert floor.assessments == () and [e["reason"] for e in floor.excluded_by_review] == ["owner_below_review_floor"]
    set_review(db_session, record_type=SubmissionRecordType.conformer_observation, record_id=obs.id, status=RecordReviewStatus.approved)
    again = run(db_session, world, minimum_request(min_review_status=RecordReviewStatus.approved))
    assert d.public_ref in by_ref(again)
    result = open_.determinations[0]
    assert result.conformer_group_ref == group.public_ref and result.conformer_observation_ref == obs.public_ref


def test_an_observation_the_profile_hides_takes_its_determination_with_it(db_session):
    world = make_world(db_session)
    group = make_conformer_group(db_session, world.entry)
    obs = make_conformer_observation(db_session, conformer_group=group)
    d, *_ = build_basin(world, target=StructureDeterminationTargetKind.conformer_basin, observation=obs)
    token = curated()
    try:
        result = run(db_session, world, minimum_request())
    finally:
        reset_current_read_profile(token)
    assert result.total_units == 0 and result.assessments == () and result.excluded_count == 0
    assert d.public_ref not in json.dumps(result.excluded_by_review)


def test_a_finding_scoped_to_one_determination_does_not_touch_its_sibling(db_session):
    world = make_world(db_session)
    d1, *_ = build_basin(world, energy=-76.40)
    d2, *_ = build_basin(world, energy=-76.45)
    world.finding(determination=d1)
    a = by_ref(run(db_session, world, minimum_request()))
    assert a[d1.public_ref].blocking and a[d2.public_ref].physically_eligible


def test_a_failed_rerun_on_another_geometry_leaves_the_older_bundle_alone(db_session):
    world = make_world(db_session)
    d, *_ = build_basin(world)
    world.finding(geometry=make_geometry(db_session))
    assert by_ref(run(db_session, world, minimum_request()))[d.public_ref].physically_eligible
    world.finding(geometry=world.geometry, kind=StructureFindingKind.state_incompatibility)
    assert by_ref(run(db_session, world, minimum_request()))[d.public_ref].blocking


def test_the_energy_quantity_is_the_determinations_own(db_session):
    world = make_world(db_session)
    d, *_ = build_basin(world, quantity=StructureDeterminationQuantity.zero_kelvin_energy)
    a = by_ref(run(db_session, world, minimum_request()))[d.public_ref]
    assert a.applicability is A.incompatible and any(r.code == "determination_supplies_other_quantity" for r in a.reasons)
    none = build_basin(world, quantity=None)[0]
    assert by_ref(run(db_session, world, minimum_request()))[none.public_ref].applicability is A.unresolved
    evidence_only = StructureRequest(
        Grain.conformer, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.local_minimum
    )
    assert by_ref(run(db_session, world, evidence_only))[none.public_ref].physically_eligible


def test_fixed_geometry_and_explicit_members_apply_at_the_determination_grain(db_session):
    world = make_world(db_session)
    d1, *_ = build_basin(world, energy=-76.40)
    other_geometry = make_geometry(db_session)
    opt = world.opt("oo", -76.5, declared=declaration(), geometry=other_geometry)
    sp = world.sp("ss", -76.49, declared=declaration(), geometry=other_geometry)
    f = world.freq("ff", declared=declaration(), geometry=other_geometry)
    d2 = world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", f)], evaluated=other_geometry)
    at_first = by_ref(run(db_session, world, minimum_request(geometry_ref=world.geometry.public_ref)))
    assert at_first[d1.public_ref].physically_eligible
    assert at_first[d2.public_ref].applicability is A.incompatible
    only_second = run(db_session, world, minimum_request(member_refs=(d2.public_ref,)))
    assert [a.unit_ref for a in only_second.assessments] == [d2.public_ref]
    with pytest.raises(NotFoundError):
        run(db_session, world, minimum_request(member_refs=("sdet_" + "q" * 26,)))


def test_the_conformer_grain_lists_basins_and_geometries_and_the_ts_grain_lists_saddles(db_session):
    world = make_world(db_session)
    basin_obs = make_conformer_observation(db_session, conformer_group=make_conformer_group(db_session, world.entry))
    geom, *_ = build_basin(world)
    basin, *_ = build_basin(world, target=StructureDeterminationTargetKind.conformer_basin, observation=basin_obs)
    result = run(db_session, world, minimum_request())
    assert {a.unit_ref for a in result.assessments} == {geom.public_ref, basin.public_ref}


# ---------------------------------------------------------------------------
# Transition states
# ---------------------------------------------------------------------------


def ts_basin(world, *, frequencies, flag=None, rc=None, with_irc=None):
    opt = world.opt("o", -40.2, declared=declaration())
    sp = world.sp("s", -40.3, declared=declaration())
    f = world.freq("f", frequencies=frequencies, declared=declaration(), structural_flag=flag, reaction_coordinate_mode_index=rc)
    sources = [("geometry_optimization", opt), ("energy", sp), ("curvature", f)]
    return world.determination(sources, target=StructureDeterminationTargetKind.saddle_point), opt, sp, f


def test_a_first_order_saddle_is_supported_on_the_transition_state_entry(db_session):
    world = make_ts_world(db_session)
    d, *_ = ts_basin(world, frequencies=[-1500.0, 100.0, 200.0])
    result = run(db_session, world, saddle_request())
    a = by_ref(result)[d.public_ref]
    assert a.physically_eligible and a.claim.claim == "first_order_saddle" and a.claim.supported
    assert result.subject.kind == "transition_state_entry" and result.subject.stationary_point_kind is None


def test_extra_imaginary_modes_follow_the_owners_persisted_treatment(db_session):
    world = make_ts_world(db_session)
    flagged, *_ = ts_basin(world, frequencies=[-1500.0, -300.0, 100.0], flag=True, rc=1)
    below, *_ = ts_basin(world, frequencies=[-1500.0, -20.0, 100.0], flag=False, rc=1)
    a = by_ref(run(db_session, world, saddle_request()))
    assert a[flagged.public_ref].applicability is A.unsupported
    assert a[below.public_ref].physically_eligible
    higher = by_ref(run(db_session, world, saddle_request(validation_claim=ValidationClaim.higher_order_saddle)))
    assert higher[flagged.public_ref].physically_eligible
    assert "not_first_order_tst_suitable" in higher[flagged.public_ref].advisory


def test_connectivity_uses_the_entrys_own_irc_evidence_bound_to_the_evaluated_geometry(db_session):
    from app.db.models.transition_state import TransitionStateValidationEvidence

    # An approved entry is frozen, so the evidence is written under an entry nobody has accepted yet.
    world = make_ts_world(db_session, entry_status=None)
    d, opt, sp, f = ts_basin(world, frequencies=[-1500.0, 100.0])
    irc = world.opt("irc", None, declared=declaration())
    # Sources are pinned when their determination is created, so this one is written under that marker.
    db_session.execute(text("SELECT set_config('tckdb.structure_determination_writing', :i, true)"), {"i": str(d.id)})
    db_session.add(StructureDeterminationSource(determination_id=d.id, role=StructureSourceRole.connectivity,
                                                calculation_id=irc.id, transition_state_entry_id=world.entry.id))
    db_session.flush()
    db_session.execute(text("SELECT set_config('tckdb.structure_determination_writing', '', true)"))
    db_session.add(TransitionStateValidationEvidence(
        transition_state_entry_id=world.entry.id, kind="irc", passed=True, rationale="connects the declared wells",
        reconstruction_calculation_id=irc.id, transition_state_geometry_id=world.geometry.id,
    ))
    db_session.flush()
    db_session.refresh(d)
    result = run(db_session, world, saddle_request(require_connectivity=True))
    a = by_ref(result)[d.public_ref]
    assert a.physically_eligible and a.claim.claim == "first_order_saddle+reactive_connectivity"
    assert result.determinations[0].validation_evidence[0]["geometry_ref"] == world.geometry.public_ref
    plain = by_ref(run(db_session, world, saddle_request()))[d.public_ref]
    assert plain.claim.claim == "first_order_saddle"


def test_a_species_entry_and_a_transition_state_entry_see_only_their_own_determinations(db_session):
    species_world = make_world(db_session)
    ts_world = make_ts_world(db_session)
    sd, *_ = build_basin(species_world)
    td, *_ = ts_basin(ts_world, frequencies=[-1500.0, 100.0])
    assert [a.unit_ref for a in run(db_session, species_world, minimum_request()).assessments] == [sd.public_ref]
    assert [a.unit_ref for a in run(db_session, ts_world, saddle_request()).assessments] == [td.public_ref]
