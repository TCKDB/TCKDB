"""The kinetics ordering engine: determinations are the unit, nothing ranks one above another."""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.kinetics_selection.engine import build_candidates, decide
from app.services.kinetics_selection.models import NormalizedKinetics
from app.services.selection_kernel import ADMIN_FIRST_BASIS, Outcome
from tests.services.kinetics_selection._support import determination, norm


def rec(ref, det, label, rank, *, status=S.approved, age=0, **kw) -> NormalizedKinetics:
    protocol = {"version": 1, "method_kind": label} if label is not None else None
    return norm(
        ref, rank=rank, age_days=age, review_status=status, determination=determination(det),
        protocol_state="valid" if protocol else "absent", protocol=protocol, **kw,
    )


def cands(*records, policy=SelectionPolicy.default):
    return build_candidates(list(records), admin_policy=policy)


def run(candidates, policy=SelectionPolicy.default):
    return decide(candidates, admin_policy=policy)


AB = (rec("k_a", "kdet_a", "a", 1), rec("k_b", "kdet_b", "b", 2))


def test_several_determinations_are_incomparable_in_one_front_whatever_their_protocols():
    decision = run(cands(*AB))
    assert decision.outcome is Outcome.incomparable_alternatives and decision.selected_determination_ref is None
    assert len(decision.fronts) == 1 and set(decision.fronts[0]) == {"kdet_a", "kdet_b"}
    assert decision.administrative_first["basis"] == ADMIN_FIRST_BASIS  # type: ignore[index]
    assert "not a claim" in ADMIN_FIRST_BASIS


def test_the_decision_document_holds_no_rule_edge_or_conflict_field():
    assert set(run(cands(*AB)).to_dict()) == {
        "outcome", "selected_determination_ref", "representation_refs", "basis", "fronts", "administrative_order",
        "administrative_first",
    }


def test_the_front_is_display_order_review_rank_then_newest_and_under_review_ties_not_reviewed():
    approved_old = rec("k_a", "kdet_a", "a", 1, age=500, status=S.approved)
    under_new = rec("k_b", "kdet_b", "b", 2, age=1, status=S.under_review)
    unreviewed_mid = rec("k_c", "kdet_c", "c", 3, age=50, status=S.not_reviewed)
    decision = run(cands(approved_old, unreviewed_mid, under_new))
    assert decision.fronts == (("kdet_a", "kdet_b", "kdet_c"),)
    latest = run(cands(approved_old, unreviewed_mid, under_new, policy=SelectionPolicy.latest), SelectionPolicy.latest)
    assert latest.fronts == (("kdet_b", "kdet_c", "kdet_a"),)


def test_the_selected_determination_returns_all_of_its_representations_together():
    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", "a", 2))
    decision = run(cands(*records))
    assert decision.outcome is Outcome.sole_eligible_candidate and decision.selected_determination_ref == "kdet_a"
    assert set(decision.representation_refs) == {"k_a1", "k_a2"} and len(decision.representation_refs) == 2


def test_alternate_fits_are_never_independent_confirmation():
    # Three fits of one determination do not outweigh one fit of another: grouping makes it one candidate.
    records = (rec("k_a1", "kdet_a", "a", 1), rec("k_a2", "kdet_a", "a", 2), rec("k_a3", "kdet_a", "a", 3),
               rec("k_b", "kdet_b", "b", 4))
    decision = run(cands(*records))
    assert len(decision.administrative_order) == 2 and decision.outcome is Outcome.incomparable_alternatives


def test_a_single_determination_is_sole_and_an_empty_population_is_none():
    sole = run(cands(AB[0]))
    assert sole.outcome is Outcome.sole_eligible_candidate and sole.selected_determination_ref == "kdet_a"
    none = run([])
    assert none.outcome is Outcome.no_applicable_candidate and none.representation_refs == ()


def test_repeated_determinations_are_refused():
    twin = cands(*AB)
    with pytest.raises(ValueError, match="determination refs must be distinct"):
        decide(twin + twin, admin_policy=SelectionPolicy.default)


def test_building_candidates_refuses_a_record_without_a_determination():
    with pytest.raises(ValueError, match="no declared determination"):
        build_candidates([norm(determination=None, representation_role=None)], admin_policy=SelectionPolicy.default)
