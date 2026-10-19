"""``live_findings``: a correction carries a replacement verdict, and nothing else about it changed.

Two kinds of test. The *differential* ones hold the behaviour for every case without a verdict-carrying correction
identical to the function as it stood on main, by running a verbatim copy of that function (``_main_live_findings``) over
the same inputs. The *semantic* ones pin what a correction means now.
"""

from __future__ import annotations

import itertools

import pytest

from app.services.structure_selection.assessment import live_findings
from app.services.structure_selection.models import SUPPORTED_FINDING_VERSIONS, FindingFacts
from tests.services.structure_selection._support import finding_facts

KINDS = ("identity_incompatibility", "state_incompatibility", "path_incompatibility", "contradictory_characterization")


def _main_live_findings(findings):
    """The function as it stood on main before corrections carried a verdict, copied verbatim."""
    by_ref = {f.finding_ref: f for f in findings}
    settled: set[str] = set()
    for f in findings:
        if f.supersedes_ref is None or f.kind != "adjudication" or f.authority != "authorized_adjudication":
            continue
        if f.semantic_version not in SUPPORTED_FINDING_VERSIONS:
            continue
        target = by_ref.get(f.supersedes_ref)
        if target is None or (target.scope, target.subject_ref) != (f.scope, f.subject_ref):
            continue
        settled.add(target.finding_ref)
    return [f for f in findings if f.finding_ref not in settled]


def _worlds_without_a_verdict_carrying_correction() -> list[tuple[FindingFacts, ...]]:
    """Every finding-set shape that has no correction ruling ``invalidates`` or ``unresolved``, including withdrawals."""
    worlds: list[tuple[FindingFacts, ...]] = [()]
    for kind, verdict, authority, version in itertools.product(
        KINDS + ("role_invalidation", "a_kind_this_release_cannot_read"),
        ("invalidates", "does_not_invalidate", "unresolved"),
        ("authorized_adjudication", "producer_assertion"),
        (1, 2),
    ):
        role = "energy" if kind == "role_invalidation" else None
        base = finding_facts("sfnd_a", kind=kind, verdict=verdict, authority=authority, version=version, role=role)
        worlds.append((base,))
        # Withdrawn by a same-subject authorized adjudication, by one about another subject, by an unreadable one, by a
        # producer's, and by one naming a finding that is not loaded.
        for label, change in (
            ("same", {}),
            ("other_subject", {"subject_ref": "calc_other"}),
            ("unreadable", {"version": 2}),
            ("producer", {"authority": "producer_assertion"}),
            ("missing_target", {"supersedes": "sfnd_missing"}),
        ):
            adjudication = finding_facts(
                "sfnd_b", kind="adjudication", verdict="does_not_invalidate", supersedes=change.pop("supersedes", "sfnd_a"), **change
            )
            worlds.append((base, adjudication))
            worlds.append((adjudication, base))  # input order must not matter to the answer
    # Several independent findings, one of them withdrawn.
    a = finding_facts("sfnd_a", kind="identity_incompatibility")
    b = finding_facts("sfnd_b", kind="role_invalidation", role="energy")
    c = finding_facts("sfnd_c", kind="adjudication", verdict="does_not_invalidate", supersedes="sfnd_a")
    worlds.append((a, b, c))
    return worlds


def test_every_world_without_a_verdict_carrying_correction_reads_exactly_as_on_main():
    worlds = _worlds_without_a_verdict_carrying_correction()
    assert len(worlds) > 200  # the product above is not accidentally empty
    for world in worlds:
        assert live_findings(world) == _main_live_findings(world), world


def test_the_differential_can_fail():
    """A reference that disagreed with the function would be seen: the corrected shapes differ from main."""
    a = finding_facts("sfnd_a")
    c = finding_facts("sfnd_c", kind="adjudication", verdict="unresolved", supersedes="sfnd_a")
    assert live_findings((a, c)) != _main_live_findings((a, c))


def _chain(*verdicts: str, kind: str = "identity_incompatibility", role: str | None = None) -> tuple[FindingFacts, ...]:
    root = finding_facts("sfnd_0", kind=kind, verdict="invalidates", role=role)
    out = [root]
    for i, verdict in enumerate(verdicts, start=1):
        out.append(finding_facts(f"sfnd_{i}", kind="adjudication", verdict=verdict, supersedes=f"sfnd_{i - 1}"))
    return tuple(out)


def test_the_head_of_a_chain_is_the_only_ruling_in_force():
    live = live_findings(_chain("does_not_invalidate", "invalidates", "unresolved"))
    assert [f.finding_ref for f in live] == ["sfnd_3"]


def test_a_head_that_invalidates_or_is_unresolved_reads_as_the_claim_the_chain_started_from():
    for verdict in ("invalidates", "unresolved"):
        (head,) = live_findings(_chain("does_not_invalidate", verdict))
        assert (head.kind, head.verdict, head.role, head.authority) == (
            "identity_incompatibility", verdict, None, "authorized_adjudication"
        )
    (role_head,) = live_findings(_chain("invalidates", kind="role_invalidation", role="curvature"))
    assert (role_head.kind, role_head.role) == ("role_invalidation", "curvature")


def test_a_head_that_withdraws_is_returned_as_it_is_and_has_no_effect():
    (head,) = live_findings(_chain("does_not_invalidate"))
    assert head.kind == "adjudication" and head.verdict == "does_not_invalidate"


def test_a_correction_about_another_subject_or_an_unreadable_one_does_not_replace_the_ruling():
    root = finding_facts("sfnd_0")
    elsewhere = finding_facts("sfnd_1", kind="adjudication", verdict="invalidates", supersedes="sfnd_0", subject_ref="calc_x")
    newer = finding_facts("sfnd_2", kind="adjudication", verdict="unresolved", supersedes="sfnd_0", version=2)
    assert [f.finding_ref for f in live_findings((root, elsewhere, newer))] == ["sfnd_0", "sfnd_1", "sfnd_2"]


def test_each_role_has_its_own_chain():
    energy = finding_facts("sfnd_e", kind="role_invalidation", role="energy")
    curvature = finding_facts("sfnd_c", kind="role_invalidation", role="curvature")
    withdrawn = finding_facts("sfnd_x", kind="adjudication", verdict="unresolved", supersedes="sfnd_e")
    live = {f.finding_ref: f for f in live_findings((energy, curvature, withdrawn))}
    assert set(live) == {"sfnd_c", "sfnd_x"}
    assert live["sfnd_x"].role == "energy" and live["sfnd_x"].verdict == "unresolved"
    assert live["sfnd_c"].role == "curvature" and live["sfnd_c"].verdict == "invalidates"


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0)])
def test_the_answer_does_not_depend_on_input_order(order):
    chain = _chain("does_not_invalidate", "invalidates")
    assert live_findings(tuple(chain[i] for i in order)) == live_findings(chain)
