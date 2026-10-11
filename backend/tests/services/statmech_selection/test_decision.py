"""The statmech decision: qualification first, then audited whole-model preference through the shared graph kernel.

Every rule here is synthetic (this release ships none). One behaviour per test, each shaped to fail under the mutation
named in the test's comment block in ``test_decision_mutations.md``-style prose: the assertion is on the thing that would
change, not on a count that would still be satisfied.
"""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import ADMIN_FIRST_BASIS, Tri
from app.services.statmech_selection.decision import (
    ADMIN_KEY_SPEC,
    LIMIT_AUTHORIZED,
    LIMIT_ELIGIBLE_ONLY,
    Selection,
    admin_key,
)
from app.services.statmech_selection.rules import RULE_INACTIVE, RULES_VERSION, default_rules, validate_rules
from app.services.statmech_selection.vocabulary import Objective, Outcome
from tests.services.statmech_selection._rules import SyntheticRule, beats, candidate, population, ref, run
from tests.services.statmech_selection._support import (
    FSF_ENTROPY,
    IMPORTED_SOURCES,
    legacy,
)


def _selection(decision):
    s = decision.selection
    return (s.scientific_ref, s.sole_eligible_ref, s.administrative_ref)


# --- qualification is the floor ----------------------------------------------------------------------------------------------


def test_the_release_ships_no_rules_so_a_comparison_has_nothing_to_stand_on():
    assert default_rules() == ()


def test_a_qualify_intent_names_the_models_that_qualify_and_selects_none():
    decision, _ = run(population("a", "b"), rules=[beats("r", "a", "b")], body={"intent": "qualify", "comparison_objective": None})
    assert decision.outcome is Outcome.qualified_models
    assert _selection(decision) == (None, None, None) and decision.selection.basis is None
    assert decision.summary.qualified_refs == (ref("a"), ref("b"))
    assert decision.protocol["edges"] == [] and decision.protocol["rules_applied"] is False  # nothing was compared


def test_nothing_to_compare_is_reported_as_it_is():
    none, _ = run([])
    assert none.outcome is Outcome.no_candidates and _selection(none) == (None, None, None)
    unqualified, _ = run([candidate("a", rank=1, recipe={"scale_application": "unknown"})])
    assert unqualified.outcome is Outcome.missing_required_data and _selection(unqualified) == (None, None, None)
    deferred, _ = run(population("a"), body={"task": "numerical_evaluation", "observable": "entropy"})
    assert deferred.outcome is Outcome.unsupported_task and _selection(deferred) == (None, None, None)


def test_a_lone_eligible_model_is_sole_not_preferred():
    decision, _ = run(population("a"), rules=[beats("r", "a", "z")])
    assert decision.outcome is Outcome.sole_eligible_candidate
    assert _selection(decision) == (None, ref("a"), None)
    assert "noncomparative" in decision.selection.basis or "not a comparative" in decision.selection.basis
    assert decision.selection.limitation == LIMIT_AUTHORIZED


def test_two_qualified_models_with_no_rule_are_incomparable_and_not_equally_accurate():
    decision, _ = run(population("a", "b"))
    assert decision.outcome is Outcome.incomparable_alternatives
    assert decision.protocol["fronts"] == [[ref("b"), ref("a")]] or decision.protocol["fronts"] == [[ref("a"), ref("b")]]
    assert decision.protocol["edges"] == [] and _selection(decision) == (None, None, None)
    assert "does not establish equal accuracy" in decision.basis


def test_a_first_alternative_is_administrative_only_and_keeps_the_outcome():
    decision, _ = run(population("a", "b"), body={"result_mode": "first"})
    assert decision.outcome is Outcome.incomparable_alternatives
    assert _selection(decision)[:2] == (None, None) and decision.selection.administrative_ref in (ref("a"), ref("b"))
    assert decision.selection.basis == ADMIN_FIRST_BASIS
    assert decision.protocol["fronts"][0][0] == decision.selection.administrative_ref


def test_the_three_references_are_mutually_exclusive_and_carry_their_basis():
    with pytest.raises(ValueError, match="mutually exclusive"):
        Selection(scientific_ref=ref("a"), sole_eligible_ref=ref("b"), basis="x", limitation="y")
    with pytest.raises(ValueError, match="basis"):
        Selection(scientific_ref=ref("a"))
    with pytest.raises(ValueError, match="belong"):
        Selection(basis="orphaned")


# --- audited preference ------------------------------------------------------------------------------------------------------


def test_an_active_scoped_rule_prefers_one_whole_model_over_every_alternative():
    decision, _ = run(population("a", "b", "c"), rules=[beats("r", "a", "b", "c")])
    assert decision.outcome is Outcome.policy_preferred
    assert _selection(decision) == (ref("a"), None, None)
    assert [e["dispreferred"] for e in decision.protocol["edges"]] == [ref("b"), ref("c")]
    assert decision.protocol["fronts"] == [[ref("a")], [ref("b"), ref("c")]] or decision.protocol["fronts"][0] == [ref("a")]


def test_administration_sorts_within_a_front_and_never_reverses_a_preference():
    best_review_newest = {"b": {"review": RecordReviewStatus.approved, "age_days": 400}, "a": {"review": RecordReviewStatus.not_reviewed, "age_days": 0}}
    decision, _ = run(population("a", "b", **best_review_newest), rules=[beats("r", "a", "b")])
    assert decision.outcome is Outcome.policy_preferred and decision.selection.scientific_ref == ref("a")


def test_an_unbenchmarked_competitor_stays_in_the_first_front_and_blocks_a_winner():
    decision, _ = run(population("a", "b", "c"), rules=[beats("r", "a", "b")])  # nothing says anything about c
    assert decision.outcome is Outcome.incomparable_alternatives
    assert set(decision.protocol["fronts"][0]) == {ref("a"), ref("c")} and decision.protocol["fronts"][1] == [ref("b")]
    assert _selection(decision) == (None, None, None)


@pytest.mark.parametrize("status", [RULE_INACTIVE, "revoked"])
def test_a_rule_that_is_not_active_creates_no_edge_and_says_why(status):
    decision, _ = run(population("a", "b"), rules=[beats("r", "a", "b", status=status)])
    assert decision.outcome is Outcome.incomparable_alternatives and decision.protocol["edges"] == []
    (match,) = decision.protocol["rule_matches"]
    assert match["applied"] is False and match["why"] == f"rule_status_{status}"


def test_a_rule_never_composes_across_objectives_or_reference_models():
    other = beats("r_other", "b", "a", objective=Objective.physical_accuracy)
    ok = beats("r", "a", "b")
    decision, _ = run(population("a", "b"), rules=[ok, other])
    assert decision.outcome is Outcome.policy_preferred and decision.selection.scientific_ref == ref("a")  # no conflict with the other objective
    why = {m["rule_id"]: m["why"] for m in decision.protocol["rule_matches"] if not m["applied"]}
    assert why == {"r_other": "objective_or_reference_model_differs"}
    fidelity = beats("r_f", "a", "b", objective=Objective.model_fidelity, reference_model="smd_" + "r" * 26)
    wrong_reference, _ = run(
        population("a", "b"),
        rules=[fidelity],
        body={"comparison_objective": "model_fidelity", "reference_model_ref": "smd_" + "s" * 26},  # same objective, another model
    )
    assert wrong_reference.outcome is Outcome.incomparable_alternatives
    assert wrong_reference.protocol["rule_matches"][0]["why"] == "objective_or_reference_model_differs"
    right_reference, _ = run(
        population("a", "b"),
        rules=[fidelity],
        body={"comparison_objective": "model_fidelity", "reference_model_ref": "smd_" + "r" * 26},
    )
    assert right_reference.outcome is Outcome.policy_preferred


def test_a_rule_about_another_task_or_observable_applies_to_nothing_here():
    elsewhere = beats("r", "a", "b", tasks=("canonical_model",), observables=("internal_partition",))
    decision, _ = run(population("a", "b"), rules=[elsewhere])
    assert decision.outcome is Outcome.incomparable_alternatives
    assert decision.protocol["rule_matches"][0]["why"] == "task_or_observable_differs"


def test_a_rule_whose_coverage_does_not_contain_the_requested_scope_makes_no_edge():
    """A per-temperature or per-member win is not a whole-model win: coverage is the entire requested scope or nothing."""
    partial = beats("r", "a", "b", coverage=Tri.false)
    decision, _ = run(population("a", "b"), rules=[partial])
    assert decision.outcome is Outcome.incomparable_alternatives and decision.protocol["edges"] == []
    assert decision.protocol["rule_matches"][0]["why"] == "requested_scope_not_covered"


def test_unknown_coverage_or_scope_is_undecided_not_ignored():
    for rule in (beats("r", "a", "b", coverage=Tri.unknown), beats("r", "a", "b", scope=Tri.unknown)):
        decision, _ = run(population("a", "b"), rules=[rule], body={"result_mode": "first"})
        assert decision.outcome is Outcome.unresolved_comparability
        assert _selection(decision) == (None, None, None)  # an administrative reference cannot rescue it
        assert decision.protocol["undecided"][0]["code"] == "comparison_prerequisite_unknown"
    outside, _ = run(population("a", "b"), rules=[beats("r", "a", "b", scope=Tri.false)])
    assert outside.outcome is Outcome.incomparable_alternatives


def test_an_unknown_prerequisite_on_either_side_or_in_compatibility_is_undecided_never_an_edge():
    for rule in (
        SyntheticRule("r", prefer={ref("a"): Tri.true}, yield_={ref("b"): Tri.unknown}),
        SyntheticRule("r", prefer={ref("a"): Tri.unknown}, yield_={ref("b"): Tri.true}),
        beats("r", "a", "b", compat=Tri.unknown),
    ):
        decision, _ = run(population("a", "b"), rules=[rule])
        assert decision.outcome is Outcome.unresolved_comparability
        assert decision.protocol["edges"] == [] and _selection(decision) == (None, None, None)
        assert {"preferred": ref("a"), "dispreferred": ref("b")}.items() <= decision.protocol["undecided"][0].items()
    refuted, _ = run(population("a", "b"), rules=[beats("r", "a", "b", compat=Tri.false)])
    assert refuted.outcome is Outcome.incomparable_alternatives  # a refuted prerequisite is a clean "no", not undecided


def test_opposing_rules_are_a_policy_conflict_and_no_fallback_picks_one():
    decision, _ = run(population("a", "b"), rules=[beats("r1", "a", "b"), beats("r2", "b", "a")], body={"result_mode": "first"})
    assert decision.outcome is Outcome.policy_conflict
    assert _selection(decision) == (None, None, None)
    assert decision.protocol["opposing_pairs"] and decision.protocol["administrative_order"]


def test_supersession_of_rules_is_settled_before_opposition_is_judged():
    r1, r2, r4 = beats("r1", "a", "b"), beats("r2", "b", "a", supersedes=("r1",)), beats("r4", "b", "a")
    decision, _ = run(population("a", "b"), rules=[r1, r2, r4])
    assert decision.outcome is Outcome.policy_preferred and decision.selection.scientific_ref == ref("b")
    assert [o["rule_id"] for o in decision.protocol["overridden_edges"]] == ["r1"]
    assert decision.protocol["opposing_pairs"] == []


@pytest.mark.parametrize("length", [3, 4, 5])
def test_a_preference_cycle_of_any_length_is_a_policy_conflict(length):
    letters = "abcde"[:length]
    rules = [beats(f"r{i}", letters[i], letters[(i + 1) % length]) for i in range(length)]
    decision, _ = run(population(*letters), rules=rules)
    assert decision.outcome is Outcome.policy_conflict and _selection(decision) == (None, None, None)
    assert sorted(decision.protocol["cycles"][0]) == [ref(x) for x in letters]


def test_an_invalid_active_registry_is_refused_before_it_can_make_an_edge():
    with pytest.raises(ValueError, match="no pinned manifest digest"):
        run(population("a", "b"), rules=[beats("r", "a", "b", digest=None)])
    with pytest.raises(ValueError, match="no objective"):
        validate_rules([beats("r", "a", "b", objective=None)])
    with pytest.raises(ValueError, match="no reference model"):
        validate_rules([beats("r", "a", "b", objective=Objective.model_fidelity)])
    with pytest.raises(ValueError, match="distinct"):
        validate_rules([beats("r", "a", "b"), beats("r", "b", "a")])
    with pytest.raises(ValueError, match="no task or observable"):
        validate_rules([beats("r", "a", "b", tasks=())])
    assert validate_rules([beats("r", "a", "b", status=RULE_INACTIVE, digest=None)]) is None  # an inactive rule need not be pinned


# --- the population must be decided before it is compared ---------------------------------------------------------------------


def _with_undecided():
    """Two qualified models and one whose recipe never states the energy zero a full partition needs."""
    return [*population("a", "b"), candidate("c", rank=3, recipe={"scale_application": "unknown"})]


def test_undecided_alternatives_prevent_a_preference_over_the_entire_requested_domain():
    decision, assessments = run(_with_undecided(), rules=[beats("r", "a", "b")])
    assert decision.summary.undecided_refs and decision.outcome is not Outcome.policy_preferred
    assert decision.outcome in (Outcome.unresolved_applicability, Outcome.missing_required_data)
    assert _selection(decision) == (None, None, None)
    assert set(decision.summary.qualified_refs) == {ref("a"), ref("b")}  # the qualified subset stays visible
    assert decision.coverage["certified_for_requested_domain"] is False
    assert decision.protocol["edges"] == []  # rules were not applied to a population that was not decided


def test_an_eligible_population_scope_prefers_among_the_eligible_and_says_so():
    decision, _ = run(_with_undecided(), rules=[beats("r", "a", "b")], body={"coverage_requirement": "eligible_population"})
    assert decision.outcome is Outcome.policy_preferred and decision.selection.scientific_ref == ref("a")
    assert decision.selection.limitation == LIMIT_ELIGIBLE_ONLY
    assert decision.coverage["certified_for_requested_domain"] is False and decision.coverage["undecided"] == 1


# --- the administrative key --------------------------------------------------------------------------------------------------


def test_the_default_key_is_own_review_then_newest_then_highest_ordinal_and_latest_ignores_review():
    approved_old = candidate("a", rank=1, review=RecordReviewStatus.approved, age_days=0)
    unreviewed_new = candidate("b", rank=2, review=RecordReviewStatus.not_reviewed, age_days=10)
    same_time_high = candidate("c", rank=9, review=RecordReviewStatus.approved, age_days=0)
    default = sorted([approved_old, unreviewed_new, same_time_high], key=lambda c: admin_key(SelectionPolicy.default, c))
    assert [c.candidate_ref for c in default] == [ref("c"), ref("a"), ref("b")]
    latest = sorted([approved_old, unreviewed_new, same_time_high], key=lambda c: admin_key(SelectionPolicy.latest, c))
    assert [c.candidate_ref for c in latest] == [ref("b"), ref("c"), ref("a")]
    assert ADMIN_KEY_SPEC["latest"] == ["created_at descending", "ordinal descending"]


def test_the_policy_orders_a_front_and_the_decision_echoes_the_key():
    decision, _ = run(
        [candidate("a", rank=1, review=RecordReviewStatus.approved), candidate("b", rank=2, review=RecordReviewStatus.not_reviewed, age_days=5)],
        body={"administrative_policy": "latest"},
    )
    assert decision.protocol["administrative_order"] == [ref("b"), ref("a")]
    assert decision.protocol["admin_key"] == ADMIN_KEY_SPEC
    assert decision.to_dict()["versions"]["rules"] == RULES_VERSION


# --- advisories travel with the decision ----------------------------------------------------------------------------------------


def test_imported_parameter_advisories_are_part_of_the_decision_and_the_notices():
    imported = candidate("a", rank=1, sources=IMPORTED_SOURCES)
    decision, _ = run([imported])
    assert decision.outcome is Outcome.sole_eligible_candidate
    assert {"imported_parameters_not_revalidated:curvature", "imported_parameters_not_revalidated:geometry"} <= set(decision.advisories[ref("a")])
    assert any("not re-judged" in n for n in decision.notices)
    assert decision.to_dict()["advisories"][ref("a")] == list(decision.advisories[ref("a")])


def test_an_unidentified_literature_source_is_disclosed_with_its_notice():
    from tests.services.statmech_selection._support import src

    loose = (
        src("geom", "geometry", None, locator="literature", literature_ref="lit_a", literature_identified=False),
        src("curv", "curvature", None, locator="literature", literature_ref="lit_b"),
        src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=FSF_ENTROPY),
    )
    decision, _ = run([candidate("a", rank=1, sources=loose)])
    assert decision.outcome is not Outcome.sole_eligible_candidate  # it meets no role, so it does not qualify
    assert decision.advisories[ref("a")] and any("no DOI or ISBN" in n for n in decision.notices)


def test_a_legacy_record_competes_as_a_whole_model_with_its_own_ref():
    decision, assessments = run([legacy(), candidate("a", rank=2)])
    assert {a.candidate_ref for a in assessments} == {"sm_a", ref("a")}
    assert decision.outcome in (Outcome.incomparable_alternatives, Outcome.sole_eligible_candidate)
