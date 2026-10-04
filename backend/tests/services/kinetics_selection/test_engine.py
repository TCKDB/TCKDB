"""The kinetics preference engine: determinations compete, edges need every representation, objectives never chain."""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.kinetics_selection.engine import (
    BASIS_OBJECTIVES_NOT_COMPOSED,
    build_candidates,
    decide,
)
from app.services.kinetics_selection.models import KineticsSubject, NormalizedKinetics
from app.services.kinetics_selection.rules import EDGE_LABEL, RULE_ACTIVE, RULE_INACTIVE, RULE_REVOKED, KineticsRule
from app.services.selection_kernel import Outcome, RuleMatch, Tri
from tests.services.kinetics_selection._support import determination, norm, request

SUBJECT = KineticsSubject("rxe_x", (), ())


class LabelRule(KineticsRule):
    """Test-only rule: records carry a label in ``protocol['method_kind']``; ``wins`` precede ``loses``."""

    def __init__(self, rule_id, wins, loses, *, objective_key="o", supersedes=(), version="1", scope_state=Tri.true,
                 status=RULE_ACTIVE, compat=None, observable="rate_coefficient"):
        self.rule_id, self.version, self.objective_key = rule_id, version, objective_key
        self.objective = f"test objective {objective_key}"
        self.supersedes = tuple(supersedes)
        self.observable = observable
        self._wins, self._loses, self._scope, self._status, self._compat = set(wins), set(loses), scope_state, status, compat
        self.inactive_reasons = ("test reason",) if status != RULE_ACTIVE else ()

    @property
    def status(self):
        return self._status

    @staticmethod
    def label(c):
        return (c.protocol or {}).get("method_kind")

    def scope(self, subject, request):
        return RuleMatch(self._scope, ("test_scope",))

    def _side(self, c, labels):
        label = self.label(c)
        if label is None:
            return RuleMatch(Tri.unknown, ("label_not_stated",))
        return RuleMatch(Tri.true if label in labels else Tri.false)

    def preferred_side(self, c):
        return self._side(c, self._wins)

    def yielding_side(self, c):
        return self._side(c, self._loses)

    def compatible(self, a, b):
        return self._compat(a, b) if self._compat else RuleMatch(Tri.true, ())


def rec(ref, det, label, rank, *, status=S.approved, age=0, **kw) -> NormalizedKinetics:
    protocol = {"version": 1, "method_kind": label} if label is not None else None
    return norm(
        ref, rank=rank, age_days=age, review_status=status, determination=determination(det),
        protocol_state="valid" if protocol else "absent", protocol=protocol, **kw,
    )


def cands(*records, policy=SelectionPolicy.default):
    return build_candidates(list(records), admin_policy=policy)


def run(candidates, rules, **request_changes):
    return decide(candidates, subject=SUBJECT, request=request(**request_changes), rules=rules)


AB = (rec("k_a", "kdet_a", "a", 1), rec("k_b", "kdet_b", "b", 2))


def test_a_scoped_preference_between_determinations_selects_the_preferred_and_labels_the_edge():
    decision = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}),))
    assert decision.outcome is Outcome.policy_preferred and decision.selected_determination_ref == "kdet_a"
    assert decision.representation_refs == ("k_a",)
    assert decision.fronts == (("kdet_a",), ("kdet_b",)) and decision.cycles == () and decision.opposing_pairs == ()
    (edge,) = decision.edges
    assert (edge["preferred"], edge["dispreferred"], edge["rule_id"]) == ("kdet_a", "kdet_b", "R1")
    assert edge["label"] == EDGE_LABEL == "expected-performance inference" and edge["objective_key"] == "o"


def test_a_supported_preference_outranks_recency_under_every_administrative_policy():
    old_a = rec("k_a", "kdet_a", "a", 1, age=500, status=S.not_reviewed)
    new_b = rec("k_b", "kdet_b", "b", 2, age=1, status=S.approved)
    for policy in SelectionPolicy:
        decision = run(cands(old_a, new_b, policy=policy), (LabelRule("R1", {"a"}, {"b"}),), admin_policy=policy)
        assert decision.outcome is Outcome.policy_preferred and decision.selected_determination_ref == "kdet_a", policy
    # The administrative order alone would have put the newer, better reviewed one first.
    assert decide(cands(old_a, new_b), subject=SUBJECT, request=request(), rules=()).administrative_order[0] == "kdet_b"


def test_the_selected_determination_returns_all_of_its_representations_together():
    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", "a", 2), rec("k_b", "kdet_b", "b", 3))
    decision = run(cands(*records), (LabelRule("R1", {"a"}, {"b"}),))
    assert decision.selected_determination_ref == "kdet_a"
    assert set(decision.representation_refs) == {"k_a1", "k_a2"} and len(decision.representation_refs) == 2


# -- every representation of a determination must stand behind the edge -----------------------------------


def test_one_unknown_representation_of_the_preferred_determination_prevents_the_edge():
    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", None, 2), rec("k_b", "kdet_b", "b", 3))
    decision = run(cands(*records), (LabelRule("R1", {"a"}, {"b"}),))
    assert decision.edges == () and decision.outcome is Outcome.incomparable_alternatives
    verdict = next(m for m in decision.rule_matches if m["rule_id"] == "R1")["determinations"]
    assert next(v for v in verdict if v["determination_ref"] == "kdet_a")["preferred"]["state"] == "unknown"


def test_one_opposing_representation_of_the_preferred_determination_prevents_the_edge():
    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", "c", 2), rec("k_b", "kdet_b", "b", 3))
    assert run(cands(*records), (LabelRule("R1", {"a"}, {"b"}),)).edges == ()


def test_one_unknown_or_other_representation_of_the_yielding_determination_prevents_the_edge():
    for odd in (None, "c"):
        records = (rec("k_a", "kdet_a", "a", 1), rec("k_b1", "kdet_b", "b", 2), rec("k_b2", "kdet_b", odd, 3))
        assert run(cands(*records), (LabelRule("R1", {"a"}, {"b"}),)).edges == (), odd


def test_alternate_fits_are_never_independent_confirmation():
    # Three fits of one determination do not outweigh one fit of another: grouping makes it one candidate.
    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", "a", 2), rec("k_a3", "kdet_a", "a", 3),
               rec("k_b", "kdet_b", "b", 4))
    decision = run(cands(*records), ())
    assert len(decision.administrative_order) == 2 and decision.outcome is Outcome.incomparable_alternatives


def test_a_pair_that_is_unknown_or_different_on_the_rest_of_the_protocol_makes_no_edge_and_is_recorded():
    def judge(state):
        return lambda a, b: RuleMatch(state, (f"pair_{state.value}",))

    for state in (Tri.unknown, Tri.false):
        decision = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}, compat=judge(state)),))
        assert decision.edges == () and decision.outcome is Outcome.incomparable_alternatives
        (check,) = decision.pair_checks
        assert check["compatible"]["state"] == state.value and (check["preferred"], check["dispreferred"]) == ("kdet_a", "kdet_b")
    ok = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}, compat=judge(Tri.true)),))
    assert len(ok.edges) == 1 and ok.pair_checks[0]["compatible"]["state"] == "true"


def test_every_pair_of_representations_must_be_compatible():
    seen = []

    def only_when_both_first(a, b):
        seen.append((a.kinetics_ref, b.kinetics_ref))
        return RuleMatch(Tri.true if (a.kinetics_ref, b.kinetics_ref) != ("k_a2", "k_b") else Tri.unknown)

    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", "a", 2), rec("k_b", "kdet_b", "b", 3))
    decision = run(cands(*records), (LabelRule("R1", {"a"}, {"b"}, compat=only_when_both_first),))
    assert decision.edges == () and sorted(seen) == [("k_a1", "k_b"), ("k_a2", "k_b")]


# -- rules that do not apply ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", [RULE_INACTIVE, RULE_REVOKED])
def test_an_inactive_or_revoked_rule_makes_no_edge_and_says_why(status):
    decision = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}, status=status),))
    assert decision.edges == () and decision.outcome is Outcome.incomparable_alternatives
    (match,) = decision.rule_matches
    assert match["applied"] is False and match["why"] == f"rule_status_{status}" and match["reasons"] == ["test reason"]


def test_an_out_of_scope_or_unknown_scope_rule_makes_no_edge():
    for state, why in ((Tri.false, "outside_rule_scope"), (Tri.unknown, "scope_unknown")):
        decision = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}, scope_state=state),))
        assert decision.edges == () and decision.rule_matches[0]["why"] == why


def test_a_rule_for_another_observable_is_not_applied():
    decision = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}, observable="rate_of_progress"),))
    assert decision.edges == () and decision.rule_matches[0]["why"] == "rule_observable_rate_of_progress"


def test_the_administrative_policies_apply_no_rule_at_all():
    decision = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}),), apply_rules=False)
    assert decision.rules_applied is False and decision.rules == () and decision.rule_matches == () and decision.edges == ()
    assert decision.outcome is Outcome.incomparable_alternatives
    assert decision.administrative_first == {"determination_ref": decision.administrative_order[0],
                                             "basis": decision.administrative_first["basis"]}
    assert "not a claim that the record is method-superior" in decision.administrative_first["basis"]


def test_the_shipped_registry_applies_nothing_so_a_population_is_unranked():
    from app.services.kinetics_selection.rules import default_rules

    decision = run(cands(*AB), default_rules())
    assert decision.edges == () and decision.outcome is Outcome.incomparable_alternatives
    assert all(m["applied"] is False and m["why"] == "rule_status_inactive" for m in decision.rule_matches)
    assert len(decision.rule_matches) == 4 and all(r["status"] == "inactive" for r in decision.rules)


# -- objectives, conflicts, fronts -----------------------------------------------------------------------


def three(a, b, c):
    return (rec("k_a", "kdet_a", a, 1), rec("k_b", "kdet_b", b, 2), rec("k_c", "kdet_c", c, 3))


def test_edges_under_one_objective_compose_along_a_path():
    rules = (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"c"}))
    decision = run(cands(*three("a", "b", "c")), rules)
    assert decision.outcome is Outcome.policy_preferred and decision.selected_determination_ref == "kdet_a"
    assert decision.fronts == (("kdet_a",), ("kdet_b",), ("kdet_c",)) and decision.unused_edges == ()


def test_edges_under_different_objectives_are_not_chained_into_a_whole_rate_preference():
    # A barrier-only edge a > b and a tunneling-only edge b > c must not make a > c.
    rules = (LabelRule("R1", {"a"}, {"b"}, objective_key="barrier"), LabelRule("R2", {"b"}, {"c"}, objective_key="tunneling"))
    decision = run(cands(*three("a", "b", "c")), rules)
    assert decision.outcome is Outcome.incomparable_alternatives and decision.selected_determination_ref is None
    assert decision.edges == () and decision.basis == BASIS_OBJECTIVES_NOT_COMPOSED
    assert {(e["preferred"], e["dispreferred"], e["objective_key"]) for e in decision.unused_edges} == {
        ("kdet_a", "kdet_b", "barrier"), ("kdet_b", "kdet_c", "tunneling")}
    assert len(decision.fronts) == 1 and set(decision.fronts[0]) == {"kdet_a", "kdet_b", "kdet_c"}


def test_edges_under_one_objective_from_two_rules_still_count_when_another_objective_made_none():
    rules = (LabelRule("R1", {"a"}, {"b"}, objective_key="barrier"), LabelRule("R2", {"x"}, {"y"}, objective_key="tunneling"))
    decision = run(cands(*three("a", "b", "c")), rules)
    assert len(decision.edges) == 1 and decision.unused_edges == ()


def test_opposing_rules_and_cycles_are_a_conflict_with_no_selection_and_no_fallback():
    opposing = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"})))
    assert opposing.outcome is Outcome.policy_conflict and opposing.selected_determination_ref is None
    assert opposing.opposing_pairs and len(opposing.edges) == 2 and opposing.administrative_first is None
    cycle = run(cands(*three("a", "b", "c")), (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"c"}), LabelRule("R3", {"c"}, {"a"})))
    assert cycle.outcome is Outcome.policy_conflict and cycle.cycles == (("kdet_a", "kdet_b", "kdet_c"),)


def test_a_supersession_resolves_a_conflict_and_a_third_agreeing_rule_does_not_revive_it():
    resolved = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"}, supersedes=("R1",))))
    assert resolved.outcome is Outcome.policy_preferred and resolved.selected_determination_ref == "kdet_b"
    third = run(cands(*AB), (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"}, supersedes=("R1",)),
                             LabelRule("R4", {"b"}, {"a"})))
    assert third.outcome is Outcome.policy_preferred and third.opposing_pairs == ()


def test_an_applicable_competitor_with_unknown_method_evidence_prevents_a_unique_winner():
    decision = run(cands(*three("a", "b", None)), (LabelRule("R1", {"a"}, {"b"}),))
    assert decision.outcome is Outcome.incomparable_alternatives
    assert set(decision.fronts[0]) == {"kdet_a", "kdet_c"} and decision.fronts[1] == ("kdet_b",)


def test_a_single_determination_is_sole_not_preferred_and_an_empty_population_is_none():
    sole = run(cands(AB[0]), (LabelRule("R1", {"a"}, {"b"}),))
    assert sole.outcome is Outcome.sole_eligible_candidate and sole.selected_determination_ref == "kdet_a"
    none = run([], (LabelRule("R1", {"a"}, {"b"}),))
    assert none.outcome is Outcome.no_applicable_candidate and none.representation_refs == ()


def test_administrative_order_sorts_inside_a_front_and_never_across_fronts():
    old_a = rec("k_a", "kdet_a", "a", 1, age=100)
    fresh_b = rec("k_b", "kdet_b", "b", 2, age=0)
    fresh_c = rec("k_c", "kdet_c", "c", 3, age=0)
    decision = run(cands(old_a, fresh_b, fresh_c, policy=SelectionPolicy.latest), (LabelRule("R1", {"a"}, {"b"}),),
                   admin_policy=SelectionPolicy.latest)
    assert decision.administrative_order[0] != "kdet_a"  # recency alone would put a newer one first
    assert decision.fronts[0] == ("kdet_c", "kdet_a") or decision.fronts[0] == ("kdet_a", "kdet_c")
    assert decision.fronts[1] == ("kdet_b",)


def test_repeated_determinations_or_rule_ids_are_refused():
    twin = cands(*AB)
    with pytest.raises(ValueError, match="determination refs must be distinct"):
        decide(twin + twin, subject=SUBJECT, request=request(), rules=())
    with pytest.raises(ValueError, match="rule ids must be distinct"):
        run(twin, (LabelRule("R1", {"a"}, {"b"}), LabelRule("R1", {"b"}, {"a"})))


def test_building_candidates_refuses_a_record_without_a_determination():
    with pytest.raises(ValueError, match="no declared determination"):
        build_candidates([norm(determination=None, representation_role=None)], admin_policy=SelectionPolicy.default)


@pytest.mark.parametrize("odd_first", [True, False], ids=["odd_ranked_first", "odd_ranked_last"])
def test_a_side_is_judged_on_every_representation_whatever_their_order(odd_first):
    # The representation that fails the side may be the representative or any other; the edge needs all of them.
    for odd in (None, "c"):
        a_ranks = (2, 1) if odd_first else (1, 2)
        for preferred_odd in (True, False):
            if preferred_odd:
                records = (rec("k_a1", "kdet_a", "a", a_ranks[0]), rec("k_a2", "kdet_a", odd, a_ranks[1]), rec("k_b", "kdet_b", "b", 3))
            else:
                records = (rec("k_a", "kdet_a", "a", 3), rec("k_b1", "kdet_b", "b", a_ranks[0]), rec("k_b2", "kdet_b", odd, a_ranks[1]))
            assert run(cands(*records), (LabelRule("R1", {"a"}, {"b"}),)).edges == (), (odd, preferred_odd, odd_first)


def test_a_refuted_representation_makes_the_side_false_even_beside_an_unknown_one():
    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", "c", 2), rec("k_a3", "kdet_a", None, 3), rec("k_b", "kdet_b", "b", 4))
    decision = run(cands(*records), (LabelRule("R1", {"a"}, {"b"}),))
    (verdicts,) = [m["determinations"] for m in decision.rule_matches]
    assert next(v for v in verdicts if v["determination_ref"] == "kdet_a")["preferred"]["state"] == "false"
