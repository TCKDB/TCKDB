"""The preference engine: edges only from matching rules, conflicts block, fronts then administrative order."""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.thermo_selection import engine
from app.services.thermo_selection.models import NormalizedCandidate, Outcome, RuleMatch, Subject, Tri
from app.services.thermo_selection.rules import RULE_REVOKED, E1Rule, PreferenceRule
from tests.services.thermo_selection._support import cand, protocol, subject_for

E1 = E1Rule()
METHANE = subject_for("Methane")


def decide(candidates, *, policy=SelectionPolicy.default, rules=(E1,), subject=METHANE):
    return engine.decide(candidates, subject=subject, admin_policy=policy, rules=rules)


class LabelRule(PreferenceRule):
    """Test-only rule: candidates carry a label in ``recipe_version``; prefer ``wins`` labels over ``loses`` labels."""

    quantity = "formation_enthalpy_298k"
    objective = "test"

    def __init__(self, rule_id, wins, loses, *, supersedes=(), version="1", scope_state=Tri.true):
        self.rule_id, self.version, self._wins, self._loses = rule_id, version, set(wins), set(loses)
        self.supersedes = tuple(supersedes)
        self._scope = scope_state

    @staticmethod
    def _label(c):
        return ((c.protocol or {}).get("recipe") or {}).get("recipe_version")

    def scope(self, subject):
        return RuleMatch(self._scope, ("test_scope",))

    def preferred_side(self, c):
        return RuleMatch(Tri.true if self._label(c) in self._wins else Tri.false)

    def yielding_side(self, c):
        return RuleMatch(Tri.true if self._label(c) in self._loses else Tri.false)


def labelled(ref, label, **kw):
    return cand(ref, proto=protocol("g4", label=label), **kw)


# -- the headline decision ----------------------------------------------------------------------


def test_an_older_qualifying_g4_precedes_a_newer_qualifying_g3_under_every_administrative_policy():
    g4 = cand("g4", proto="g4", age_days=400, id_rank=1)
    g3 = cand("g3", proto="g3", age_days=1, id_rank=2)
    # Browse order (recency) would list the G3 first; that is the thing the rule must outrank.
    assert [c.thermo_ref for c in engine.order_within([g4, g3], SelectionPolicy.latest)] == ["g3", "g4"]
    for policy in SelectionPolicy:
        decision = decide([g3, g4], policy=policy)
        assert decision.outcome is Outcome.policy_preferred, policy
        assert decision.selected_ref == "g4"
        assert decision.fronts == (("g4",), ("g3",))
        assert [(e.preferred, e.dispreferred, e.rule_id) for e in decision.edges] == [("g4", "g3", "E1")]


def test_review_status_and_timestamps_cannot_reverse_an_accepted_preference():
    g4 = cand("g4", proto="g4", status=S.not_reviewed, age_days=900, id_rank=1)
    g3 = cand("g3", proto="g3", status=S.approved, age_days=0, id_rank=2)
    assert engine.order_within([g4, g3], SelectionPolicy.most_reviewed)[0].thermo_ref == "g3"
    decision = decide([g3, g4], policy=SelectionPolicy.most_reviewed)
    assert decision.outcome is Outcome.policy_preferred and decision.selected_ref == "g4"
    assert decision.administrative_order[0] == "g3"  # the administrative order alone puts G3 first
    assert decision.fronts[0] == ("g4",)  # but it never crosses a front


def test_a_record_with_no_protocol_stays_in_the_first_front_and_prevents_a_unique_winner():
    decision = decide([cand("g4", proto="g4", age_days=5), cand("g3", proto="g3"), cand("blank", proto=None, age_days=1)])
    assert decision.outcome is Outcome.incomparable_alternatives
    assert decision.selected_ref is None
    assert set(decision.fronts[0]) == {"g4", "blank"} and decision.fronts[1] == ("g3",)
    assert decision.administrative_first == {"thermo_ref": decision.fronts[0][0], "basis": engine.ADMIN_FIRST_BASIS}


def test_an_experimental_record_is_an_unranked_competitor_not_a_demoted_one():
    decision = decide([cand("g4", proto="g4"), cand("g3", proto="g3"), cand("exp", proto=None, origin="experimental")])
    assert decision.outcome is Outcome.incomparable_alternatives
    assert set(decision.fronts[0]) == {"g4", "exp"}


def test_a_g4_competing_with_an_unmatched_recipe_variant_has_no_unique_winner():
    decision = decide([cand("g4", proto="g4"), cand("mp2", proto="g4mp2"), cand("g3", proto="g3")])
    assert decision.outcome is Outcome.incomparable_alternatives
    assert set(decision.fronts[0]) == {"g4", "mp2"}


def test_without_any_matching_rule_every_candidate_shares_the_first_front_and_is_not_called_equal():
    decision = decide([cand("a", proto="g4mp2"), cand("b", proto="g4_complete")])
    assert decision.outcome is Outcome.incomparable_alternatives
    assert decision.basis == engine.BASIS_INCOMPARABLE_NO_RULE
    assert len(decision.fronts) == 1 and not decision.edges


def test_a_rule_does_not_apply_outside_its_scope():
    pentane_like = subject_for("Methane", inchi_key="BKIMMITUMNQMOS-UHFFFAOYSA-N", molecular_formula="C9H20")
    decision = decide([cand("g4", proto="g4"), cand("g3", proto="g3")], subject=pentane_like)
    assert decision.outcome is Outcome.incomparable_alternatives and not decision.edges
    assert decision.rule_matches[0]["why"] == "outside_rule_scope"


def test_triplet_methylene_gets_no_e1_edge_while_singlet_does():
    pair = [cand("g4", proto="g4"), cand("g3", proto="g3")]
    assert decide(pair, subject=subject_for("Methylene")).outcome is Outcome.policy_preferred
    assert decide(pair, subject=subject_for("Methylene", multiplicity=3)).outcome is Outcome.incomparable_alternatives


def test_a_revoked_rule_is_never_applied():
    revoked = E1Rule()
    revoked.status = RULE_REVOKED
    decision = decide([cand("g4", proto="g4"), cand("g3", proto="g3")], rules=(revoked,))
    assert decision.outcome is Outcome.incomparable_alternatives
    assert decision.rule_matches[0]["why"] == "rule_status_revoked"


def test_a_rule_for_another_quantity_is_never_applied():
    other = LabelRule("Q", {"a"}, {"b"})
    other.quantity = "heat_capacity"
    decision = decide([labelled("a", "a"), labelled("b", "b")], rules=(other,))
    assert decision.outcome is Outcome.incomparable_alternatives


# -- single and empty ---------------------------------------------------------------------------


def test_one_eligible_candidate_is_a_sole_candidate_not_a_comparative_win():
    decision = decide([cand("only", proto="g3")])
    assert decision.outcome is Outcome.sole_eligible_candidate
    assert decision.selected_ref == "only" and decision.basis == engine.BASIS_SOLE


def test_no_eligible_candidate_is_no_applicable_candidate():
    decision = decide([])
    assert decision.outcome is Outcome.no_applicable_candidate and decision.selected_ref is None


def test_the_three_selection_outcomes_carry_different_explanations():
    sole = decide([cand("a")])
    preferred = decide([cand("a", proto="g4"), cand("b", proto="g3")])
    alternatives = decide([cand("a", proto="g4"), cand("b", proto=None)])
    assert len({sole.basis, preferred.basis, alternatives.basis}) == 3
    assert alternatives.administrative_first is not None
    assert preferred.administrative_first is None and sole.administrative_first is None


# -- conflicts ----------------------------------------------------------------------------------


def test_opposing_rules_block_selection_with_no_fallback():
    forward = LabelRule("R1", {"a"}, {"b"})
    reverse = LabelRule("R2", {"b"}, {"a"})
    a, b = labelled("a", "a", age_days=9), labelled("b", "b", age_days=0)
    decision = decide([a, b], rules=(forward, reverse))
    assert decision.outcome is Outcome.policy_conflict
    assert decision.selected_ref is None and decision.administrative_first is None
    assert decision.opposing_pairs == ({"between": ["a", "b"], "rules": ["R1@1", "R2@1"]},)
    assert set(decision.administrative_order) == {"a", "b"}  # alternatives stay visible
    assert len(decision.edges) == 2  # no edge dropped to make the conflict go away


def test_an_opposing_rule_that_declares_supersession_resolves_the_conflict():
    old = LabelRule("R1", {"a"}, {"b"})
    new = LabelRule("R2", {"b"}, {"a"}, supersedes=("R1",))
    decision = decide([labelled("a", "a"), labelled("b", "b")], rules=(old, new))
    assert decision.outcome is Outcome.policy_preferred and decision.selected_ref == "b"
    assert decision.overridden_edges[0]["rule_id"] == "R1" and decision.overridden_edges[0]["overridden_by_rule_id"] == "R2"


def test_newer_alone_never_resolves_opposing_rules():
    older = LabelRule("R1", {"a"}, {"b"}, version="1")
    newer = LabelRule("R2", {"b"}, {"a"}, version="9")  # a later version number is not supersession
    assert decide([labelled("a", "a"), labelled("b", "b")], rules=(older, newer)).outcome is Outcome.policy_conflict


def test_mutual_supersession_is_still_a_conflict():
    r1 = LabelRule("R1", {"a"}, {"b"}, supersedes=("R2",))
    r2 = LabelRule("R2", {"b"}, {"a"}, supersedes=("R1",))
    assert decide([labelled("a", "a"), labelled("b", "b")], rules=(r1, r2)).outcome is Outcome.policy_conflict


def test_a_preference_cycle_blocks_selection_and_names_its_members():
    rules = (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"c"}), LabelRule("R3", {"c"}, {"a"}))
    decision = decide([labelled("a", "a"), labelled("b", "b"), labelled("c", "c")], rules=rules)
    assert decision.outcome is Outcome.policy_conflict
    assert decision.cycles == (("a", "b", "c"),)
    assert not decision.opposing_pairs


def test_an_acyclic_chain_from_several_rules_is_not_a_conflict():
    rules = (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"c"}))
    decision = decide([labelled("a", "a"), labelled("b", "b"), labelled("c", "c")], rules=rules)
    assert decision.outcome is Outcome.policy_preferred and decision.selected_ref == "a"
    assert decision.fronts == (("a",), ("b",), ("c",))


def test_a_conflict_elsewhere_in_the_graph_still_blocks_the_whole_request():
    rules = (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"}), LabelRule("R3", {"z"}, {"y"}))
    decision = decide([labelled("a", "a"), labelled("b", "b"), labelled("y", "y"), labelled("z", "z")], rules=rules)
    assert decision.outcome is Outcome.policy_conflict


def test_a_long_acyclic_chain_does_not_exhaust_the_stack():
    n = 600
    rules = tuple(LabelRule(f"R{i}", {f"c{i}"}, {f"c{i + 1}"}) for i in range(n - 1))
    # 600 candidates would exceed the service cap; the engine itself must still be iterative.
    decision = decide([labelled(f"c{i}", f"c{i}", id_rank=i + 1) for i in range(n)], rules=rules)
    assert decision.outcome is Outcome.policy_preferred and len(decision.fronts) == n


# -- administrative order -----------------------------------------------------------------------


def test_administrative_order_applies_within_each_front_by_the_existing_sort_key():
    # Two unranked first-front records and two second-front records, each pair differing in review and age.
    a = cand("a-new-unreviewed", proto=None, status=S.not_reviewed, age_days=1, id_rank=1)
    b = cand("b-old-approved", proto=None, status=S.approved, age_days=300, id_rank=2)
    top = cand("top", proto="g4", status=S.approved, id_rank=3)
    low = cand("low", proto="g3", status=S.approved, id_rank=4)
    by_review = decide([a, b, top, low], policy=SelectionPolicy.default)
    by_recency = decide([a, b, top, low], policy=SelectionPolicy.latest)
    # Review status first (approved before not_reviewed, then newest), or recency alone.
    assert by_review.fronts == (("top", "b-old-approved", "a-new-unreviewed"), ("low",))
    assert by_recency.fronts == (("top", "a-new-unreviewed", "b-old-approved"), ("low",))


def test_distinct_id_ranks_are_required():
    with pytest.raises(ValueError, match="id_rank"):
        engine.order_within([cand("a", id_rank=1), cand("b", id_rank=1)], SelectionPolicy.default)


def test_refs_must_be_distinct():
    with pytest.raises(ValueError, match="refs"):
        decide([cand("a", id_rank=1), cand("a", id_rank=2)])


def test_decision_is_deterministic_regardless_of_input_order():
    cs = [cand("g4", proto="g4", id_rank=1), cand("g3", proto="g3", id_rank=2), cand("x", proto=None, id_rank=3)]
    assert decide(cs).to_dict() == decide(list(reversed(cs))).to_dict()


def test_normalized_candidates_round_trip_through_their_manifest_form():
    c = cand("g4", proto=protocol("g4", departures=[{"component": "geometry", "description": "d"}]), linked=("g4",))
    assert NormalizedCandidate.from_dict(c.to_dict()) == c
    s = subject_for("Methane")
    assert Subject.from_dict(s.to_dict()) == s


def test_a_node_whose_predecessors_sit_in_two_fronts_lands_in_the_third_front():
    # a -> b, b -> c and a -> c: c has an incoming edge from the first front and one from the second.
    rules = (LabelRule("R1", {"a"}, {"b", "c"}), LabelRule("R2", {"b"}, {"c"}))
    decision = decide([labelled("a", "a"), labelled("b", "b"), labelled("c", "c")], rules=rules)
    assert decision.outcome is Outcome.policy_preferred and decision.selected_ref == "a"
    assert decision.fronts == (("a",), ("b",), ("c",))
    assert {(e.preferred, e.dispreferred) for e in decision.edges} == {("a", "b"), ("a", "c"), ("b", "c")}
