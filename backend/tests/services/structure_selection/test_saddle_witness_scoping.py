"""Which curvature evidence speaks for a declared saddle: the paired scope cases the council asked for (J3).

Neither "the latest frequency result decides" nor "every attached frequency result must contradict" is chemically
sufficient. What a saddle claim rests on is the frequency or Hessian evidence of its *declared target and role bundle*:
the geometry the determination evaluates, a usable source, and the owner's persisted treatment. These cases pair each
scope question with its counter-case, through the task-aware structure assessor (the scoped consumer; the legacy trust
badge no longer issues a frequency verdict because it cannot pin a target).
"""

from __future__ import annotations

from datetime import datetime

from app.db.models.common import CalculationQuality, StructureDeterminationTargetKind, StructureFindingKind
from app.db.models.common import StructureFindingVerdict as V
from app.services.selection_kernel import Applicability as A
from app.services.structure_selection import assess_entry_structures
from app.services.structure_selection.assessment import CURVATURE_SIDE
from tests.services.scientific_read._factories import attach_freq_result, make_geometry
from tests.services.structure_selection._support import declaration, make_ts_world, saddle_request

SADDLE = StructureDeterminationTargetKind.saddle_point
K = StructureFindingKind


def assess(session, world, **request):
    world.settle()
    result = assess_entry_structures(
        session, entry_id=world.entry.id, request=saddle_request(**request), require_snapshot=False
    )
    return {a.unit_ref: a for a in result.assessments}


def bundle(world, curvature, *, geometry=None):
    """A saddle determination at ``geometry`` (default the world's) whose curvature sources are ``curvature``."""
    opt = world.opt("o", -40.2, declared=declaration(), geometry=geometry)
    sp = world.sp("s", -40.3, declared=declaration(), geometry=geometry)
    sources = [("geometry_optimization", opt), ("energy", sp), *[("curvature", f) for f in curvature]]
    return world.determination(sources, target=SADDLE, evaluated=geometry)


def freq(world, name, frequencies, *, geometry=None, **kw):
    return world.freq(name, frequencies=frequencies, geometry=geometry, declared=declaration(), **kw)


# --- an unrelated rerun --------------------------------------------------------------------------------


def test_a_failed_rerun_on_another_geometry_does_not_touch_an_older_coherent_bundle(db_session):
    world = make_ts_world(db_session)
    good = freq(world, "good", [-1500.0, 100.0])
    d = bundle(world, [good])
    other = make_geometry(db_session)
    freq(world, "rerun_elsewhere", [100.0, 200.0], geometry=other)  # no imaginary mode, but at another geometry
    assert assess(db_session, world)[d.public_ref].physically_eligible


def test_the_same_rerun_named_as_a_source_but_bound_to_another_geometry_is_disclosed_and_ignored(db_session):
    world = make_ts_world(db_session)
    good = freq(world, "good", [-1500.0, 100.0])
    elsewhere = freq(world, "elsewhere", [100.0, 200.0], geometry=make_geometry(db_session))
    d = bundle(world, [good, elsewhere])
    a = assess(db_session, world)[d.public_ref]
    assert a.physically_eligible and "curvature_on_other_geometry" in a.advisory


def test_the_counter_case_the_same_zero_mode_result_on_the_evaluated_geometry_is_not_ignored(db_session):
    world = make_ts_world(db_session)
    bad = freq(world, "bad", [100.0, 200.0])  # no imaginary mode ON the evaluated geometry
    d = bundle(world, [bad])
    a = assess(db_session, world)[d.public_ref]
    assert not a.physically_eligible and "curvature_contradicts_claim" in a.blocking


# --- same-target disproof and disagreement -------------------------------------------------------------


def test_an_older_pass_cannot_rescue_a_same_target_disproof_and_the_disagreement_stays_unresolved(db_session):
    world = make_ts_world(db_session)
    older_pass = freq(world, "older", [-1500.0, 100.0])
    newer_disproof = freq(world, "newer", [100.0, 200.0])
    older_pass.created_at, newer_disproof.created_at = datetime(2026, 1, 1), datetime(2026, 6, 1)
    db_session.flush()
    d = bundle(world, [older_pass, newer_disproof])
    a = assess(db_session, world)[d.public_ref]
    assert not a.physically_eligible and a.applicability is A.unresolved
    assert "curvature_witnesses_disagree" in {r.code for r in a.reasons}
    assert a.claim is not None and not a.claim.supported  # not certified either


def test_the_order_of_the_two_same_target_results_does_not_change_the_verdict(db_session):
    world = make_ts_world(db_session)
    a_pass = freq(world, "p", [-1500.0, 100.0])
    a_disproof = freq(world, "q", [100.0, 200.0])
    a_pass.created_at, a_disproof.created_at = datetime(2026, 6, 1), datetime(2026, 1, 1)  # now the pass is the later one
    db_session.flush()
    d = bundle(world, [a_pass, a_disproof])
    a = assess(db_session, world)[d.public_ref]
    assert a.applicability is A.unresolved and not a.physically_eligible


# --- an unusable result that would otherwise "rescue" -------------------------------------------------


def test_a_rejected_result_with_one_imaginary_mode_does_not_rescue_a_usable_disproof(db_session):
    world = make_ts_world(db_session)
    rejected_pass = freq(world, "rejected", [-1500.0, 100.0], quality=CalculationQuality.rejected)
    usable_disproof = freq(world, "usable", [100.0, 200.0])
    d = bundle(world, [rejected_pass, usable_disproof])
    a = assess(db_session, world)[d.public_ref]
    # the disproof stands on its own: the unusable "pass" is neither a witness for nor against anything
    assert not a.physically_eligible and "curvature_contradicts_claim" in a.blocking
    assert "curvature_witnesses_disagree" not in {r.code for r in a.reasons}


def test_the_counter_case_a_usable_pass_with_a_rejected_disproof_is_supported(db_session):
    world = make_ts_world(db_session)
    usable_pass = freq(world, "usable", [-1500.0, 100.0])
    rejected_disproof = freq(world, "rejected", [100.0, 200.0], quality=CalculationQuality.rejected)
    d = bundle(world, [usable_pass, rejected_disproof])
    assert assess(db_session, world)[d.public_ref].physically_eligible


# --- characterisation stored on optimisation (and composite) jobs --------------------------------------


def test_a_frequency_result_stored_on_the_optimisation_supports_the_saddle_on_its_output_geometry(db_session):
    world = make_ts_world(db_session)
    opt = world.opt("o", -40.2, declared=declaration())
    attach_freq_result(db_session, calculation=opt, frequencies_cm1=[-1500.0, 100.0])
    sp = world.sp("s", -40.3, declared=declaration())
    d = world.determination(
        [("geometry_optimization", opt), ("energy", sp), ("curvature", opt)], target=SADDLE
    )
    a = assess(db_session, world)[d.public_ref]
    assert a.physically_eligible and a.claim.supported and opt.public_ref in a.claim.witness_refs


def test_the_same_result_on_an_optimisation_whose_output_is_another_geometry_is_not_evidence_for_this_one(db_session):
    world = make_ts_world(db_session)
    opt = world.opt("o", -40.2, declared=declaration(), geometry=make_geometry(db_session))
    attach_freq_result(db_session, calculation=opt, frequencies_cm1=[-1500.0, 100.0])
    sp = world.sp("s", -40.3, declared=declaration())
    d = world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", opt)], target=SADDLE)
    a = assess(db_session, world)[d.public_ref]
    assert not a.physically_eligible and not (a.claim and a.claim.supported)


def test_a_stored_hessian_is_a_witness_that_exists_but_is_not_evaluated_so_unresolved_never_a_certificate(db_session):
    world = make_ts_world(db_session)
    opt = world.opt("o", -40.2, declared=declaration())
    world.hessian(opt)
    sp = world.sp("s", -40.3, declared=declaration())
    d = world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", opt)], target=SADDLE)
    a = assess(db_session, world)[d.public_ref]
    assert a.applicability is A.unresolved and not a.physically_eligible
    assert "hessian_curvature_not_evaluated" in {r.code for r in a.reasons}


def test_optimisation_and_composite_jobs_read_curvature_on_their_output_geometry():
    assert CURVATURE_SIDE["opt"] == "output" and CURVATURE_SIDE["composite"] == "output" and CURVATURE_SIDE["freq"] == "input"


# --- adjudication --------------------------------------------------------------------------------------


def test_an_authorized_adjudication_settles_a_curvature_disproof_for_fresh_decisions(db_session):
    world = make_ts_world(db_session)
    witness = freq(world, "f", [-1500.0, 100.0])
    d = bundle(world, [witness])
    disproof = world.finding(calculation=witness, kind=K.contradictory_characterization)
    blocked = assess(db_session, world)[d.public_ref]
    assert not blocked.physically_eligible and any("contradictory_characterization" in b for b in blocked.blocking)
    world.finding(calculation=witness, kind=K.adjudication, verdict=V.does_not_invalidate, supersedes=disproof)
    assert assess(db_session, world)[d.public_ref].physically_eligible
    db_session.refresh(disproof)
    assert disproof.verdict is V.invalidates  # history preserved


def test_a_finding_about_the_unrelated_rerun_does_not_invalidate_the_bundle(db_session):
    world = make_ts_world(db_session)
    good = freq(world, "good", [-1500.0, 100.0])
    d = bundle(world, [good])
    rerun = freq(world, "rerun", [100.0, 200.0], geometry=make_geometry(db_session))
    world.finding(calculation=rerun, kind=K.contradictory_characterization)
    assert assess(db_session, world)[d.public_ref].physically_eligible
