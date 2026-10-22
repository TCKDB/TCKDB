"""The statmech decision: qualification first, then the qualified models in display order.

TCKDB ranks no model above another: one qualified model is a sole eligible candidate, several are incomparable
alternatives in one front. One behaviour per test: the assertion is on the thing that would change, not on a count that
would still be satisfied.
"""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import ADMIN_FIRST_BASIS
from app.services.statmech_selection.decision import (
    ADMIN_KEY_SPEC,
    LIMIT_AUTHORIZED,
    LIMIT_ELIGIBLE_ONLY,
    Selection,
    admin_key,
)
from app.services.statmech_selection.vocabulary import Outcome
from tests.services.statmech_selection._population import candidate, population, ref, run
from tests.services.statmech_selection._support import (
    FSF_ENTROPY,
    IMPORTED_SOURCES,
    legacy,
)


def _selection(decision):
    s = decision.selection
    return (None, s.sole_eligible_ref, s.administrative_ref)


# --- qualification is the floor ----------------------------------------------------------------------------------------------


def test_the_decision_vocabulary_has_no_preference_outcome_and_no_scientific_ref():
    assert not {"policy_preferred", "policy_conflict", "unresolved_comparability"} & {o.value for o in Outcome}
    assert "scientific_ref" not in Selection().to_dict()


def test_a_qualify_intent_names_the_models_that_qualify_and_selects_none():
    decision, _ = run(population("a", "b"), body={"intent": "qualify"})
    assert decision.outcome is Outcome.qualified_models
    assert _selection(decision) == (None, None, None) and decision.selection.basis is None
    assert decision.summary.qualified_refs == (ref("a"), ref("b"))
    assert decision.ordering["fronts"] == []  # nothing was listed


def test_nothing_to_compare_is_reported_as_it_is():
    none, _ = run([])
    assert none.outcome is Outcome.no_candidates and _selection(none) == (None, None, None)
    unqualified, _ = run([candidate("a", rank=1, recipe={"scale_application": "unknown"})])
    assert unqualified.outcome is Outcome.missing_required_data and _selection(unqualified) == (None, None, None)
    deferred, _ = run(population("a"), body={"task": "numerical_evaluation", "observable": "entropy"})
    assert deferred.outcome is Outcome.unsupported_task and _selection(deferred) == (None, None, None)


def test_a_lone_eligible_model_is_sole_not_preferred():
    decision, _ = run(population("a"))
    assert decision.outcome is Outcome.sole_eligible_candidate
    assert _selection(decision) == (None, ref("a"), None)
    assert "noncomparative" in decision.selection.basis or "not a comparative" in decision.selection.basis
    assert decision.selection.limitation == LIMIT_AUTHORIZED


def test_two_qualified_models_are_incomparable_and_nothing_is_preferred():
    decision, _ = run(population("a", "b"))
    assert decision.outcome is Outcome.incomparable_alternatives
    assert decision.ordering["fronts"] == [[ref("b"), ref("a")]] or decision.ordering["fronts"] == [[ref("a"), ref("b")]]
    assert _selection(decision) == (None, None, None)
    assert "no ranking between methods" in decision.basis and "none is preferred" in decision.basis
    assert set(decision.ordering) == {"fronts", "administrative_order", "admin_key"}


def test_a_first_alternative_is_administrative_only_and_keeps_the_outcome():
    decision, _ = run(population("a", "b"), body={"result_mode": "first"})
    assert decision.outcome is Outcome.incomparable_alternatives
    assert _selection(decision)[:2] == (None, None) and decision.selection.administrative_ref in (ref("a"), ref("b"))
    assert decision.selection.basis == ADMIN_FIRST_BASIS
    assert decision.ordering["fronts"][0][0] == decision.selection.administrative_ref


def test_the_two_references_are_mutually_exclusive_and_carry_their_basis():
    with pytest.raises(ValueError, match="mutually exclusive"):
        Selection(administrative_ref=ref("a"), sole_eligible_ref=ref("b"), basis="x", limitation="y")
    with pytest.raises(ValueError, match="basis"):
        Selection(sole_eligible_ref=ref("a"))
    with pytest.raises(ValueError, match="belong"):
        Selection(basis="orphaned")


# --- the population must be decided before it is compared ---------------------------------------------------------------------


def _with_undecided():
    """Two qualified models and one whose recipe never states the energy zero a full partition needs."""
    return [*population("a", "b"), candidate("c", rank=3, recipe={"scale_application": "unknown"})]


def test_undecided_alternatives_prevent_a_listing_over_the_entire_requested_domain():
    decision, assessments = run(_with_undecided())
    assert decision.summary.undecided_refs and decision.outcome is not Outcome.incomparable_alternatives
    assert decision.outcome in (Outcome.unresolved_applicability, Outcome.missing_required_data)
    assert _selection(decision) == (None, None, None)
    assert set(decision.summary.qualified_refs) == {ref("a"), ref("b")}  # the qualified subset stays visible
    assert decision.coverage["certified_for_requested_domain"] is False
    assert decision.ordering["fronts"] == []  # a population that was not decided is not listed


def test_an_eligible_population_scope_lists_among_the_eligible_and_says_so():
    decision, _ = run(_with_undecided(), body={"coverage_requirement": "eligible_population", "result_mode": "first"})
    assert decision.outcome is Outcome.incomparable_alternatives and decision.selection.administrative_ref in (ref("a"), ref("b"))
    assert decision.selection.limitation == LIMIT_ELIGIBLE_ONLY
    assert decision.coverage["certified_for_requested_domain"] is False and decision.coverage["undecided"] == 1


# --- the administrative key --------------------------------------------------------------------------------------------------


def test_the_default_key_is_own_review_then_newest_then_highest_ordinal_and_latest_ignores_review():
    approved_old = candidate("a", rank=1, review=RecordReviewStatus.approved, age_days=0)
    unreviewed_new = candidate("b", rank=2, review=RecordReviewStatus.not_reviewed, age_days=10)
    same_time_high = candidate("c", rank=9, review=RecordReviewStatus.approved, age_days=0)
    default = sorted([approved_old, unreviewed_new, same_time_high], key=lambda c: admin_key(SelectionPolicy.default, c))
    assert [c.candidate_ref for c in default] == [ref("c"), ref("a"), ref("b")]
    under_review = candidate("d", rank=3, review=RecordReviewStatus.under_review, age_days=10)
    assert admin_key(SelectionPolicy.default, under_review)[0] == admin_key(SelectionPolicy.default, unreviewed_new)[0]
    assert admin_key(SelectionPolicy.default, approved_old)[0] < admin_key(SelectionPolicy.default, under_review)[0]
    latest = sorted([approved_old, unreviewed_new, same_time_high], key=lambda c: admin_key(SelectionPolicy.latest, c))
    assert [c.candidate_ref for c in latest] == [ref("b"), ref("c"), ref("a")]
    assert ADMIN_KEY_SPEC["latest"] == ["created_at descending", "ordinal descending"]


def test_the_policy_orders_a_front_and_the_decision_echoes_the_key():
    decision, _ = run(
        [candidate("a", rank=1, review=RecordReviewStatus.approved), candidate("b", rank=2, review=RecordReviewStatus.not_reviewed, age_days=5)],
        body={"administrative_policy": "latest"},
    )
    assert decision.ordering["administrative_order"] == [ref("b"), ref("a")]
    assert decision.ordering["admin_key"] == ADMIN_KEY_SPEC
    assert "rules" not in decision.to_dict()["versions"]


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
