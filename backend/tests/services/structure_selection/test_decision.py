"""The structure decision over pure assessed units: cohorts, repeats, ordering, contradictions, protocol preference."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import AdminNode, RuleMatch, Tri, decide_graph
from app.services.structure_selection.assessment import assess_unit
from app.services.structure_selection.decision import ADMIN_KEY_SPEC, admin_key, decide_structures
from app.services.structure_selection.models import (
    AdminPolicy,
    CoverageRequirement,
    CurvatureFacts,
    EnergyFacts,
    Grain,
    Intent,
    Objective,
    Quantity,
    RepeatPolicy,
    ResultMode,
    StructureRequest,
    ValidationClaim,
)
from app.services.structure_selection.models import StructureOutcome as O
from app.services.structure_selection.rules import (
    RULE_ACTIVE,
    RULE_INACTIVE,
    ProtocolCandidate,
    StructureRule,
    validate_rules,
)
from tests.services.structure_selection._support import (
    G1,
    G2,
    T0,
    calc,
    declaration,
    det,
    finding_facts,
    freq,
    level,
    minimum_request,
    request,
    saddle_request,
    source,
    subject,
)

TS_SUBJECT = subject(kind="transition_state_entry", entry_ref="tse_a", stationary_point_kind=None, electronic_state_kind=None)
LOT_B = level("lot_b", basis="cc-pvqz")
DECL_B = declaration(spin_treatment={"state": "known", "value": "unrestricted"})


def run(units, req, *, calcs=(), subj=None, rules=()):
    """Assess every unit and decide, the way the service does."""
    subj = subj or subject()
    by_ref = {c.calculation_ref: c for c in (*calcs, *(u for u in units if hasattr(u, "calculation_ref")))}
    assessments = [assess_unit(u, request=req, subject=subj, calculations=by_ref) for u in units]
    return decide_structures(
        request=req, subject=subj, units=units, assessments=assessments, calculations=by_ref, rules=rules
    )


def refs(decision):
    return list(decision.selected_refs)


# ---------------------------------------------------------------------------
# Calculation grain: recorded minimum
# ---------------------------------------------------------------------------


def test_no_units_is_no_candidates():
    d = run([], request())
    assert d.outcome is O.no_candidates and d.selected_refs == () and d.cohorts == ()


def test_the_lowest_value_in_one_cohort_is_the_recorded_minimum():
    d = run([calc("c1", energy=-76.40, rank=1), calc("c2", energy=-76.45, rank=2), calc("c3", energy=-76.43, rank=3)], request())
    assert d.outcome is O.recorded_minimum and refs(d) == ["c2"]
    (cohort,) = d.cohorts
    assert [o["unit_refs"] for o in cohort["ordering"]] == [["c2"], ["c3"], ["c1"]]
    assert [o["rank"] for o in cohort["ordering"]] == [1, 2, 3]
    assert d.coverage["complete"] is False and d.coverage["authorized_population_only"] is True


def test_exact_ties_keep_every_tied_reference_and_a_near_value_is_not_a_tie():
    near = run(
        [calc("c1", energy=-76.45, rank=1), calc("c2", energy=-76.45, rank=2), calc("c3", energy=-76.4500000001, rank=3)],
        request(),
    )
    assert refs(near) == ["c3"]  # lower by 1e-10: exact comparison, no tolerance
    tied = run([calc("c1", energy=-76.45, rank=1), calc("c2", energy=-76.45, rank=2)], request())
    assert set(refs(tied)) == {"c1", "c2"} and "exact numerical tie" in tied.notes[0]


def test_a_tie_is_ordered_by_the_administrative_key_and_first_presents_one_labelled_administrative():
    older = calc("c1", energy=-76.45, rank=1, age_days=10)
    newer = calc("c2", energy=-76.45, rank=2, age_days=1)
    d = run([older, newer], request(result_mode=ResultMode.first))
    assert refs(d) == ["c2", "c1"]  # newest first under the default key
    assert d.administrative_first["ref"] == "c2" and "administrative" in d.administrative_first["basis"]
    assert run([older, newer], request()).administrative_first is None
    assert refs(run([older, newer], request(admin_policy=AdminPolicy.earliest))) == ["c1", "c2"]


def test_two_cohorts_each_have_a_minimum_and_neither_wins_by_absolute_energy():
    a = calc("a1", energy=-76.40)
    b = calc("b1", energy=-99.99, rank=2, level=LOT_B, declared=DECL_B)
    d = run([a, b], request())
    assert d.outcome is O.incomparable_alternatives and d.selected_refs == ()
    assert len(d.cohorts) == 2 and {c["minimum_refs"][0] for c in d.cohorts} == {"a1", "b1"}
    assert d.administrative_first is None
    first = run([a, b], request(result_mode=ResultMode.first))
    assert first.outcome is O.incomparable_alternatives and first.administrative_first is not None
    assert "not comparable" in first.administrative_first["basis"]


def test_a_unit_whose_cohort_is_not_established_is_reported_and_never_ordered():
    cohorted = calc("a1", energy=-76.40)
    loose = calc("a2", energy=-80.0, rank=2, declared=None)
    d = run([cohorted, loose], request())
    assert d.outcome is O.recorded_minimum and refs(d) == ["a1"]
    assert {"code": "cohort_not_established", "refs": ["a2"]} in list(d.unresolved)
    assert any("no established cohort" in n and "conditional on the one established cohort" in n for n in d.notes)
    only_loose = run([loose], request())
    assert only_loose.outcome is O.unresolved_comparability and only_loose.selected_refs == ()


def test_a_second_cohort_that_cannot_be_ordered_never_leaves_the_first_as_the_single_winner():
    """Added uncertainty must not make a stronger claim: B, lower in absolute energy, is unorderable, A is not named."""
    a1 = basin(1, -76.40, obs="cobs_1", target="conformer_basin")
    a2 = basin(2, -76.41, obs="cobs_2", target="conformer_basin")
    b1 = basin(3, -76.50, obs="cobs_1", target="conformer_basin", lot=LOT_B, declared=DECL_B)
    b2 = basin(4, -76.51, obs="cobs_2", target="conformer_basin", lot=LOT_B, declared=DECL_B)
    both = decide_dets([a1, a2, b1, b2], minimum_request())
    assert both.outcome is O.incomparable_alternatives and both.coverage["cohorts"] == 2
    # One more, disagreeing, repeat in B: B can no longer be ordered, and the outcome must not get stronger.
    b1_again = basin(5, -76.55, obs="cobs_1", target="conformer_basin", lot=LOT_B, declared=DECL_B)
    broken = decide_dets([a1, a2, b1, b2, b1_again], minimum_request())
    assert broken.outcome is O.incomparable_alternatives and broken.selected_refs == ()
    assert broken.coverage["cohorts"] == 2
    ordered = [c for c in broken.cohorts if c["minimum_hartree"] is not None]
    unordered = [c for c in broken.cohorts if c["unresolved_repeats"]]
    assert len(ordered) == 1 and len(unordered) == 1  # the one that can be ordered is listed, not crowned
    assert any(u["code"] == "repeat_determinations_disagree" for u in broken.unresolved)
    assert any("could not be ordered" in n for n in broken.notes) and "single cohort is never named the winner" in broken.basis
    # Both cohorts unorderable: nothing to list, and still no winner.
    b2_again = basin(6, -76.60, obs="cobs_2", target="conformer_basin", lot=LOT_B, declared=DECL_B)
    a1_again = basin(7, -76.30, obs="cobs_1", target="conformer_basin")
    none = decide_dets([a1, a2, b1, b2, b1_again, a1_again], minimum_request())
    assert none.outcome is O.unresolved_comparability and none.selected_refs == ()
    del b2_again


def test_first_never_names_an_administrative_winner_while_a_cohort_could_not_be_ordered():
    """An administrative first drawn only from the cohorts that could be ordered would read as the first of all of them."""
    a1 = basin(1, -76.40, obs="cobs_1", target="conformer_basin")
    a2 = basin(2, -76.41, obs="cobs_2", target="conformer_basin")
    b1 = basin(3, -76.50, obs="cobs_1", target="conformer_basin", lot=LOT_B, declared=DECL_B)
    b2 = basin(4, -76.51, obs="cobs_2", target="conformer_basin", lot=LOT_B, declared=DECL_B)
    first = minimum_request(result_mode=ResultMode.first)
    ordered = decide_dets([a1, a2, b1, b2], first)
    assert ordered.outcome is O.incomparable_alternatives and ordered.administrative_first is not None  # both ordered
    b1_again = basin(5, -76.55, obs="cobs_1", target="conformer_basin", lot=LOT_B, declared=DECL_B)
    broken = decide_dets([a1, a2, b1, b2, b1_again], first)
    assert broken.outcome is O.incomparable_alternatives and broken.selected_refs == ()
    assert broken.administrative_first is None
    assert any("could not be ordered" in n for n in broken.notes)
    assert decide_dets([a1, a2, b1, b2, b1_again], minimum_request()).administrative_first is None


def test_an_intermediate_geometry_value_is_its_own_cohort():
    free = declaration(constraints={"state": "known", "value": "unconstrained"})
    converged = calc("c1", type="opt", energy=-76.40, declared=free)
    unconverged = calc("c2", type="opt", energy=EnergyFacts(opt_final_hartree=-77.0, opt_converged=False), rank=2, declared=free)
    d = run([converged, unconverged], request())
    # The lower number of the unconverged run is not "the" recorded minimum of the converged cohort.
    assert d.outcome is O.incomparable_alternatives
    assert {tuple(c["minimum_refs"]) for c in d.cohorts} == {("c1",), ("c2",)}


def test_a_zero_kelvin_energy_is_ordered_only_among_the_same_energy_convention():
    program = calc("p1", type="composite", energy=-76.40)
    other = calc(
        "p2",
        type="composite",
        rank=2,
        energy=EnergyFacts(
            composite_assembly="rmg_composite",
            composite_electronic_hartree=-76.50,
            composite_e0_hartree=-76.48,
            composite_recipe_zpe_hartree=0.02,
        ),
    )
    d = run([program, other], request(quantity=Quantity.zero_kelvin_energy))
    assert d.outcome is O.incomparable_alternatives and len(d.cohorts) == 2
    same = calc("p3", type="composite", energy=-76.45, rank=3)
    d = run([program, same], request(quantity=Quantity.zero_kelvin_energy))
    assert d.outcome is O.recorded_minimum and refs(d) == ["p3"]


def test_a_complete_claim_needs_every_requested_member_in_one_cohort():
    good = [calc("c1", energy=-76.40), calc("c2", energy=-76.45, rank=2)]
    d = run(good, request(coverage=CoverageRequirement.all_requested_members))
    assert d.outcome is O.recorded_minimum and d.coverage["complete"] is True and refs(d) == ["c2"]
    missing = [*good, calc("c3", energy=None, rank=3)]
    d = run(missing, request(coverage=CoverageRequirement.all_requested_members))
    assert d.outcome is O.unresolved_comparability and d.selected_refs == ()
    assert d.unresolved[0] == {"code": "requested_member_not_eligible", "refs": ["c3"]}
    assert run(missing, request()).outcome is O.recorded_minimum  # the same population answers a known-values request
    split = [*good, calc("b1", energy=-90.0, rank=3, level=LOT_B, declared=DECL_B)]
    d = run(split, request(coverage=CoverageRequirement.all_requested_members))
    assert d.outcome is O.unresolved_comparability
    assert any(u["code"] == "complete_claim_not_established:multiple_cohorts" for u in d.unresolved)


def test_energy_unavailable_is_distinct_from_no_applicable_candidate():
    d = run([calc("c1", energy=None), calc("c2", energy=None, rank=2)], request())
    assert d.outcome is O.energy_unavailable and d.selected_refs == ()
    elsewhere = run([calc("c1", energy=-76.4)], request(geometry_ref=G2))
    assert elsewhere.outcome is O.no_applicable_candidate


def test_a_unit_failing_only_on_an_applicable_finding_is_an_evidence_conflict():
    blocked = calc("c1", findings=(finding_facts(subject_ref="c1", kind="identity_incompatibility"),))
    assert run([blocked], request()).outcome is O.evidence_conflict


# ---------------------------------------------------------------------------
# The administrative key
# ---------------------------------------------------------------------------


def test_the_administrative_key_directions_are_the_documented_ones():
    later = T0 + timedelta(days=1)
    ok, pending = RecordReviewStatus.approved, RecordReviewStatus.not_reviewed
    base = admin_key(AdminPolicy.default, review=ok, quality_rank=1, created_at=T0, ordinal=5)
    assert base < admin_key(AdminPolicy.default, review=pending, quality_rank=0, created_at=later, ordinal=9)  # review leads
    assert admin_key(AdminPolicy.default, review=ok, quality_rank=0, created_at=T0, ordinal=1) < base  # quality ascending
    assert admin_key(AdminPolicy.default, review=ok, quality_rank=1, created_at=later, ordinal=1) < base  # time descending
    assert admin_key(AdminPolicy.default, review=ok, quality_rank=1, created_at=T0, ordinal=9) < base  # ordinal descending
    assert admin_key(AdminPolicy.latest, review=RecordReviewStatus.rejected, quality_rank=3, created_at=later, ordinal=1) < admin_key(
        AdminPolicy.latest, review=ok, quality_rank=0, created_at=T0, ordinal=1
    )
    assert admin_key(AdminPolicy.earliest, review=ok, quality_rank=0, created_at=T0, ordinal=1) < admin_key(
        AdminPolicy.earliest, review=ok, quality_rank=0, created_at=later, ordinal=1
    )
    assert ADMIN_KEY_SPEC["version"] == "1" and "ordinal descending" in ADMIN_KEY_SPEC["default"]


def test_a_decision_echoes_the_administrative_key_and_the_boundary_of_its_coverage():
    d = run([calc("c1")], request()).to_dict()
    assert d["administrative_key"] == ADMIN_KEY_SPEC and d["decision_version"] == "1"
    assert "authorized population" in d["search_completeness"] and "exhaustive" in d["search_completeness"]


def test_the_kernel_orders_by_adapter_keys_and_refuses_a_mixed_population():
    a = AdminNode("a", 1, RecordReviewStatus.approved, T0, admin_key=(2,))
    b = AdminNode("b", 2, RecordReviewStatus.approved, T0, admin_key=(1,))
    verdict = decide_graph([a, b], [], supersedes={}, admin_policy=SelectionPolicy.default)
    assert verdict.administrative_order == ("b", "a")
    plain = AdminNode("c", 3, RecordReviewStatus.approved, T0)
    with pytest.raises(ValueError, match="every node carries an admin_key or none does"):
        decide_graph([a, plain], [], supersedes={}, admin_policy=SelectionPolicy.default)


# ---------------------------------------------------------------------------
# Determination grain
# ---------------------------------------------------------------------------


def basin(i, energy, *, geometry=G1, target="geometry", obs=None, lot=None, declared="full", imaginary=0, **dkw):
    """A determination with its own opt, sp and freq calculations; returns ``(determination, calculations)``."""
    kw = {"declared": declared, **({"level": lot} if lot is not None else {})}
    o = calc(f"o{i}", type="opt", energy=energy + 0.01, output_geometry_refs=(geometry,), rank=100 + i, **kw)
    s = calc(f"s{i}", type="sp", energy=energy, input_geometry_refs=(geometry,), rank=200 + i, **kw)
    # Imaginary modes are judged by magnitude, so a stored imaginary mode is a stiff one (well above any tau).
    modes = tuple((k + 1, -300.0 - 10.0 * k, None) for k in range(imaginary))
    curvature = CurvatureFacts(
        has_freq_result=True, n_imag=imaginary, imaginary_modes=modes, imag_freq_cm1=-300.0 if imaginary else None,
        reaction_coordinate_mode_index=1 if imaginary > 1 else None,
    )
    f = freq(f"f{i}", geometry=geometry, rank=300 + i, curvature=curvature, **kw)
    d = det(
        f"sdet_{i}",
        sources=(
            source("geometry_optimization", o.calculation_ref, geometry),
            source("energy", s.calculation_ref, geometry),
            source("curvature", f.calculation_ref, geometry),
        ),
        evaluated_geometry_ref=geometry,
        target_kind=target,
        conformer_observation_ref=obs,
        rank=i,
        key=f"k{i}",
        **dkw,
    )
    return d, [o, s, f]


def decide_dets(pairs, req, *, subj=None, rules=()):
    return run([p[0] for p in pairs], req, calcs=[c for p in pairs for c in p[1]], subj=subj, rules=rules)


def test_the_lowest_validated_basin_is_the_corpus_minimum_and_says_what_it_is_not():
    pairs = [
        basin(1, -76.40, target="conformer_basin", obs="cobs_1"),
        basin(2, -76.46, target="conformer_basin", obs="cobs_2"),
        basin(3, -76.43, target="conformer_basin", obs="cobs_3"),
    ]
    d = decide_dets(pairs, minimum_request())
    assert d.outcome is O.validated_corpus_minimum and refs(d) == ["sdet_2"]
    assert "conformational-search" in d.basis
    assert [t["target"] for t in d.cohorts[0]["ordering"]] == ["basin:cobs_2", "basin:cobs_3", "basin:cobs_1"]


def test_repeats_of_one_target_that_agree_exactly_are_all_retained():
    pairs = [basin(1, -76.40, obs="cobs_1", target="conformer_basin"), basin(2, -76.40, obs="cobs_1", target="conformer_basin")]
    d = decide_dets(pairs, minimum_request())
    assert d.outcome is O.validated_corpus_minimum and set(refs(d)) == {"sdet_1", "sdet_2"}
    assert d.cohorts[0]["target_count"] == 1 and d.cohorts[0]["ordering"][0]["unit_refs"] == ["sdet_2", "sdet_1"]


def test_repeats_that_disagree_leave_their_cohort_unordered_unless_a_representative_is_named():
    pairs = [
        basin(1, -76.45, obs="cobs_1", target="conformer_basin", created_at=T0),
        basin(2, -76.40, obs="cobs_1", target="conformer_basin", created_at=T0 + timedelta(days=1)),
        basin(3, -76.30, obs="cobs_2", target="conformer_basin"),
    ]
    d = decide_dets(pairs, minimum_request())
    assert d.outcome is O.unresolved_comparability and d.selected_refs == ()
    assert d.cohorts[0]["unresolved_repeats"][0]["target"] == "basin:cobs_1" and d.cohorts[0]["minimum_hartree"] is None
    assert any(u["code"] == "repeat_determinations_disagree" for u in d.unresolved)
    rep = decide_dets(pairs, minimum_request(repeat_policy=RepeatPolicy.administrative_representative))
    # The newer repeat (-76.40) stands for basin 1 while an older validated repeat (-76.45) is lower: the result is
    # a representative minimum, labelled as one, and the true all-values minimum is never claimed under any label.
    assert rep.outcome is O.representative_minimum and rep.outcome is not O.validated_corpus_minimum
    assert rep.representative_policy is not None and "not the minimum of all stored values" in rep.representative_policy["basis"]
    assert "NOT the minimum of all stored values" in rep.basis
    assert refs(rep) == ["sdet_2"]  # the newer repeat is the representative under the default key
    cohort = rep.cohorts[0]
    assert cohort["representative_substituted"] is True
    assert cohort["representative_minimum_hartree"] == -76.40 and "minimum_hartree" not in cohort
    entry = next(o for o in cohort["ordering"] if o["target"] == "basin:cobs_1")
    assert entry["alternate_hartree"] == [-76.45]
    assert -76.45 not in {cohort["representative_minimum_hartree"], *(o["hartree"] for o in cohort["ordering"])}
    # With nothing to substitute, the representative policy changes nothing and the ordinary outcome stands.
    quiet = decide_dets(
        [basin(1, -76.40, obs="cobs_1", target="conformer_basin"), basin(3, -76.30, obs="cobs_2", target="conformer_basin")],
        minimum_request(repeat_policy=RepeatPolicy.administrative_representative),
    )
    assert quiet.outcome is O.validated_corpus_minimum and quiet.cohorts[0]["minimum_hartree"] == -76.40
    assert "representative_minimum_hartree" not in quiet.cohorts[0]


def test_determinations_of_different_targets_are_never_merged():
    d = decide_dets([basin(1, -76.40), basin(2, -76.40, geometry=G2)], minimum_request())
    assert d.cohorts[0]["target_count"] == 2 and set(refs(d)) == {"sdet_1", "sdet_2"}


def test_a_passing_determination_does_not_hide_a_contradicting_one_of_the_same_target():
    good = basin(1, -76.40, obs="cobs_1", target="conformer_basin")
    bad = basin(2, -76.41, obs="cobs_1", target="conformer_basin", imaginary=1)  # a stored imaginary mode
    other = basin(3, -76.30, obs="cobs_2", target="conformer_basin")
    d = decide_dets([good, bad, other], minimum_request())
    assert d.contested_targets[0]["target"] == "basin:cobs_1"
    assert d.contested_targets[0]["qualifying_refs"] == ["sdet_1"] and d.contested_targets[0]["refuting_refs"] == ["sdet_2"]
    assert d.outcome is O.validated_corpus_minimum and refs(d) == ["sdet_3"]  # the contested basin certifies nothing
    assert decide_dets([good, bad], minimum_request()).outcome is O.evidence_conflict
    complete = decide_dets([good, bad, other], minimum_request(coverage=CoverageRequirement.all_requested_members))
    assert complete.outcome is O.evidence_conflict and complete.selected_refs == ()


def test_a_failed_rerun_on_another_geometry_does_not_erase_an_older_valid_bundle():
    good = basin(1, -76.40)
    failed_elsewhere = basin(2, -76.41, geometry=G2, imaginary=1)
    d = decide_dets([good, failed_elsewhere], minimum_request())
    assert d.contested_targets == () and d.outcome is O.validated_corpus_minimum and refs(d) == ["sdet_1"]


def test_qualify_evidence_returns_the_supported_determinations_and_no_number():
    req = StructureRequest(Grain.conformer, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.local_minimum)
    d = decide_dets([basin(1, -76.40), basin(2, -76.45, geometry=G2)], req)
    assert d.outcome is O.qualified_evidence and set(refs(d)) == {"sdet_1", "sdet_2"}
    assert d.cohorts == () and "not a numerical minimum" in d.basis
    assert decide_dets([basin(3, -76.4, imaginary=2)], req).outcome is O.no_applicable_candidate


def test_a_saddle_minimum_uses_the_same_ordering_at_the_saddle_grain():
    def saddle(i, energy, geometry):
        d, cs = basin(i, energy, geometry=geometry, target="saddle_point", owner_kind="transition_state_entry", entry_kind=None, imaginary=1, owner_ref="tse_a")
        return d, cs

    d = decide_dets([saddle(1, -40.20, G1), saddle(2, -40.25, G2)], saddle_request(), subj=TS_SUBJECT)
    assert d.outcome is O.validated_corpus_minimum and refs(d) == ["sdet_2"]
    assert d.cohorts[0]["ordering"][0]["target"] == f"saddle:tse_a:{G2}"


# ---------------------------------------------------------------------------
# Protocol preference
# ---------------------------------------------------------------------------


class Pref(StructureRule):
    """A test rule: the protocol with spin ``prefer`` precedes the one with spin ``yield_``."""

    def __init__(self, rule_id, *, prefer, yield_, status=RULE_ACTIVE, objective=Objective.physical_accuracy,
                 supersedes=(), reference_model=None, sha="a" * 64):
        self.rule_id, self.version = rule_id, "1"
        self.prefer, self.yield_ = prefer, yield_
        self._status = status
        self.objective = objective
        self.reference_model = reference_model
        self.supersedes = tuple(supersedes)
        self.manifest_sha256 = sha
        if status != RULE_ACTIVE:
            self.inactive_reasons = ("test: not audited",)

    @property
    def status(self):
        return self._status

    def scope(self, subj, req):
        return RuleMatch(Tri.true, ())

    def preferred_side(self, c: ProtocolCandidate):
        return RuleMatch(Tri.true if c.fact("spin_treatment")[1] == self.prefer else Tri.false, ())

    def yielding_side(self, c: ProtocolCandidate):
        return RuleMatch(Tri.true if c.fact("spin_treatment")[1] == self.yield_ else Tri.false, ())


def protocol_request(**kw):
    base = {"grain": Grain.conformer, "intent": Intent.protocol_preferred, "objective": Objective.physical_accuracy}
    return StructureRequest(**{**base, **kw})


def two_protocols():
    """Two basins, each with a restricted-spin (A) and an unrestricted-spin (B) determination; B is lower."""
    a1 = basin(1, -76.40, target="conformer_basin", obs="cobs_1")
    a2 = basin(2, -76.41, target="conformer_basin", obs="cobs_2")
    b1 = basin(3, -76.50, target="conformer_basin", obs="cobs_1", lot=LOT_B, declared=DECL_B)
    b2 = basin(4, -76.51, target="conformer_basin", obs="cobs_2", lot=LOT_B, declared=DECL_B)
    return [a1, a2, b1, b2]


def spin_ids(d):
    """Cohort id by the spin treatment its recipe establishes."""
    out = {}
    for c in d.cohorts:
        facts = {f["name"]: f["value"] for f in c["recipe_facts"]}
        out[facts["spin_treatment"]] = c["cohort_id"]
    return out


def test_with_no_active_rule_protocols_stay_unranked():
    d = decide_dets(two_protocols(), protocol_request())
    assert d.outcome is O.incomparable_alternatives and d.selected_refs == ()
    assert d.protocol["edges"] == [] and d.protocol["rules"] == [] and len(d.cohorts) == 2


def test_a_sole_complete_protocol_is_sole_eligible_not_preferred():
    d = decide_dets(two_protocols()[:2], protocol_request())
    assert d.outcome is O.sole_eligible_candidate and len(d.selected_refs) == 1 and d.selected_refs[0].startswith("coh_")


def test_a_protocol_that_does_not_cover_the_whole_requested_set_is_not_a_candidate():
    pairs = two_protocols()[:3]  # protocol B covers basin 1 only
    d = decide_dets(pairs, protocol_request())
    assert d.outcome is O.sole_eligible_candidate  # only A covers both basins
    assert d.protocol["uncovered"][0]["covers"] == 1
    assert any(u["code"] == "incomplete_protocol_coverage" for u in d.unresolved)
    # A covers basin 1 and B covers basin 2: no protocol covers both, and no mixed profile is invented.
    d = decide_dets([two_protocols()[0], two_protocols()[3]], protocol_request())
    assert d.outcome is O.unresolved_comparability and d.selected_refs == ()


def test_an_active_rule_makes_one_protocol_preferred_and_a_lower_energy_never_does():
    pairs = two_protocols()
    ids = spin_ids(decide_dets(pairs, protocol_request()))
    d = decide_dets(pairs, protocol_request(), rules=[Pref("R1", prefer="restricted", yield_="unrestricted")])
    assert d.outcome is O.policy_preferred and d.selected_refs == (ids["restricted"],)
    assert [e["label"] for e in d.protocol["edges"]] == ["audited protocol preference"]
    # The unrestricted protocol has the lower absolute total energy and still is not preferred over it.


def test_opposing_rules_are_a_policy_conflict_and_a_superseding_rule_resolves_it():
    pairs = two_protocols()
    r1 = Pref("R1", prefer="restricted", yield_="unrestricted")
    r2 = Pref("R2", prefer="unrestricted", yield_="restricted")
    d = decide_dets(pairs, protocol_request(), rules=[r1, r2])
    assert d.outcome is O.policy_conflict and d.selected_refs == ()
    r2s = Pref("R2", prefer="unrestricted", yield_="restricted", supersedes=("R1",))
    resolved = decide_dets(pairs, protocol_request(), rules=[r1, r2s])
    assert resolved.outcome is O.policy_preferred and resolved.protocol["overridden_edges"]
    first = decide_dets(pairs, protocol_request(result_mode=ResultMode.first), rules=[r1, r2])
    assert first.outcome is O.policy_conflict and first.administrative_first is None  # first never bypasses a conflict


def test_an_inactive_rule_another_objective_another_reference_model_and_apply_rules_false_make_no_edge():
    pairs = two_protocols()
    d = decide_dets(pairs, protocol_request(), rules=[Pref("R1", prefer="restricted", yield_="unrestricted", status=RULE_INACTIVE)])
    assert d.outcome is O.incomparable_alternatives and d.protocol["rule_matches"][0]["why"] == "rule_status_inactive"
    other = Pref("R1", prefer="restricted", yield_="unrestricted", objective=Objective.expected_accuracy)
    d = decide_dets(pairs, protocol_request(), rules=[other])
    assert d.protocol["rule_matches"][0]["why"] == "objective_or_reference_model_differs" and d.protocol["edges"] == []
    active = Pref("R1", prefer="restricted", yield_="unrestricted")
    off = decide_dets(pairs, protocol_request(apply_rules=False), rules=[active])
    assert off.outcome is O.incomparable_alternatives and off.protocol["rules"] == []
    fidelity = Pref("R1", prefer="restricted", yield_="unrestricted", objective=Objective.model_fidelity, reference_model="ref@1")
    d = decide_dets(pairs, protocol_request(objective=Objective.model_fidelity, reference_model="ref@2"), rules=[fidelity])
    assert d.protocol["edges"] == []  # another pinned reference model is another question
    d = decide_dets(pairs, protocol_request(objective=Objective.model_fidelity, reference_model="ref@1"), rules=[fidelity])
    assert d.outcome is O.policy_preferred


def test_an_active_rule_must_be_pinned_and_say_its_objective():
    with pytest.raises(ValueError, match="no pinned manifest digest"):
        validate_rules([Pref("R1", prefer="a", yield_="b", sha="")])
    with pytest.raises(ValueError, match="no objective"):
        validate_rules([Pref("R1", prefer="a", yield_="b", objective=None)])
    with pytest.raises(ValueError, match="no reference model"):
        validate_rules([Pref("R1", prefer="a", yield_="b", objective=Objective.model_fidelity)])
    with pytest.raises(ValueError, match="rule names must be distinct"):
        validate_rules([Pref("R1", prefer="a", yield_="b"), Pref("R1", prefer="a", yield_="b")])
    validate_rules([Pref("R1", prefer="a", yield_="b", status=RULE_INACTIVE, sha=None)])


def test_a_protocol_request_states_its_objective_and_its_reference_model():
    with pytest.raises(ValueError, match="comparison objective"):
        StructureRequest(Grain.conformer, Intent.protocol_preferred)
    with pytest.raises(ValueError, match="pinned reference_model"):
        StructureRequest(Grain.conformer, Intent.protocol_preferred, objective=Objective.model_fidelity)
    with pytest.raises(ValueError, match="belong to protocol preference"):
        StructureRequest(Grain.conformer, Intent.validated_minimum, objective=Objective.physical_accuracy)
    with pytest.raises(ValueError, match="fixed geometry"):
        StructureRequest(Grain.calculation, Intent.protocol_preferred, objective=Objective.physical_accuracy)
    with pytest.raises(ValueError, match="belongs to a model_fidelity objective"):
        StructureRequest(Grain.conformer, Intent.protocol_preferred, objective=Objective.physical_accuracy, reference_model="x")
    r = StructureRequest.from_dict(protocol_request(objective=Objective.model_fidelity, reference_model="ref@1").to_dict())
    assert r.objective is Objective.model_fidelity and r.reference_model == "ref@1"
