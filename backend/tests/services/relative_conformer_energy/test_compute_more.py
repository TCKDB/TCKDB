"""More pure cases: the reasons a population-level exclusion gives, mixed-surface repeats, and unresolved completeness."""

from __future__ import annotations

from app.services.relative_conformer_energy.models import MemberStatus, RelativeEnergyOutcome
from app.services.structure_selection.models import CoverageRequirement
from tests.services.relative_conformer_energy._support import LOT_A, LOT_B, basin, compute, member, request

M = MemberStatus


def test_a_terminal_group_is_not_reported_as_below_a_floor():
    r = compute(
        request(), ["cg_a", "cg_b"], basin("cg_a"), basin("cg_b", energy=-76.5), excluded={"cg_b": "group_terminal_review_status"}
    )
    assert member(r, "cg_b").absent_reason == "group_terminal_review_status"


def test_a_group_whose_determination_the_floor_holds_out_says_so_not_that_there_is_none():
    r = compute(request(), ["cg_a", "cg_b"], basin("cg_a"), floor_excluded=frozenset({"cg_b"}))
    assert member(r, "cg_b").absent_reason == "determination_below_review_floor"
    plain = compute(request(), ["cg_a", "cg_b"], basin("cg_a"))
    assert member(plain, "cg_b").absent_reason == "no_structure_determination"


def test_repeats_certified_on_different_surfaces_are_not_all_on_the_energy_surface():
    # Two agreeing repeats of one group: one certified by a frequency at the energy level, one by a frequency elsewhere.
    r = compute(
        request(),
        ["cg_a", "cg_b"],
        basin("cg_a", energy=-76.40),
        basin("cg_b", energy=-76.41, observation="co_b1"),
        basin("cg_b", energy=-76.41, observation="co_b2", freq_lot=LOT_B),
    )
    cert = member(r, "cg_b").certificate
    assert cert is not None and cert.supported
    assert cert.on_energy_surface is False and cert.surface_level_refs == (LOT_A, LOT_B)
    certificate_a = member(r, "cg_a").certificate
    assert certificate_a is not None and certificate_a.on_energy_surface is True


def test_a_complete_answer_is_not_complete_while_a_member_is_unresolved():
    r = compute(
        request(coverage=CoverageRequirement.all_requested_members),
        ["cg_a", "cg_b"],
        basin("cg_a", energy=-76.40),
        basin("cg_b", energy=-76.40, observation="co_b1"),
        basin("cg_b", energy=-76.41, observation="co_b2"),
    )
    assert member(r, "cg_b").status is M.unresolved
    assert r.coverage["absent"] == 0 and r.coverage["unresolved"] == 1
    assert r.coverage["complete"] is False and r.outcome is RelativeEnergyOutcome.incomplete_coverage
