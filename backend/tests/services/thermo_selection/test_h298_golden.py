"""The H298 selector's decisions and manifests are byte-identical to what they were before the
domain-neutral graph kernel was extracted from it.

``golden/h298_decisions.json`` was generated from the engine and manifest builder as they stood
BEFORE the extraction (regenerate only with ``TCKDB_REGEN_H298_GOLDEN=1``, and only for a change
to the H298 semantics, which is a new policy version). The matrix runs every administrative
policy over populations that exercise each engine step: edges, an unresolved opposing pair, a
superseded one, a mutual supersession, a cycle, a chain, a conflict elsewhere in the graph, an
inactive rule, an unknown scope, a single candidate, an empty population, and administrative
order among incomparable alternatives. Each decision is compared as the exact JSON the manifest
embeds, together with the manifest assembled around it and its replay.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.db.models.common import ThermoTargetKind
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.thermo_selection import engine
from app.services.thermo_selection.manifest import build_manifest, replay_decision
from app.services.thermo_selection.models import (
    Applicability,
    CandidateAssessment,
    H298Request,
    Outcome,
    Tri,
)
from app.services.thermo_selection.rules import RULE_REVOKED, E1Rule
from tests.services.thermo_selection._support import cand, protocol, subject_for
from tests.services.thermo_selection.test_engine import LabelRule

GOLDEN = Path(__file__).parent / "golden" / "h298_decisions.json"
METHANE = subject_for("Methane")


def labelled(ref, label, **kw):
    return cand(ref, proto=protocol("g4", label=label), **kw)


def _abc(**kw):
    return [labelled("a", "a", age_days=3, id_rank=3), labelled("b", "b", age_days=2, id_rank=2), labelled("c", "c", age_days=1, id_rank=1)]


def _scenarios():
    e1 = E1Rule()
    chain = (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"c"}))
    cycle = (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"c"}), LabelRule("R3", {"c"}, {"a"}))
    revoked = LabelRule("R1", {"a"}, {"b"})
    revoked.status = RULE_REVOKED
    yield "e1_g4_over_g3", [cand("g4", proto="g4", age_days=400, id_rank=1), cand("g3", proto="g3", age_days=1, id_rank=2)], (e1,)
    yield "e1_status_cannot_reverse", [
        cand("g4", proto="g4", status=S.not_reviewed, age_days=900, id_rank=1),
        cand("g3", proto="g3", status=S.approved, age_days=0, id_rank=2),
    ], (e1,)
    yield "e1_blank_blocks_unique_winner", [
        cand("g4", proto="g4", age_days=5), cand("g3", proto="g3"), cand("blank", proto=None, age_days=1)
    ], (e1,)
    yield "chain", _abc(), chain
    yield "cycle", _abc(), cycle
    yield "opposing_unresolved", _abc()[:2], (LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"}))
    yield "opposing_superseded", _abc()[:2], (
        LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"}, supersedes=("R1",)))
    yield "opposing_mutual_supersession", _abc()[:2], (
        LabelRule("R1", {"a"}, {"b"}, supersedes=("R2",)), LabelRule("R2", {"b"}, {"a"}, supersedes=("R1",)))
    yield "conflict_elsewhere", [*_abc()[:2], labelled("y", "y", id_rank=9), labelled("z", "z", id_rank=8)], (
        LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"}), LabelRule("R3", {"z"}, {"y"}))
    yield "inactive_rule", _abc()[:2], (revoked,)
    yield "unknown_scope", _abc()[:2], (LabelRule("R1", {"a"}, {"b"}, scope_state=Tri.unknown),)
    yield "outside_scope", _abc()[:2], (LabelRule("R1", {"a"}, {"b"}, scope_state=Tri.false),)
    yield "single", _abc()[:1], (e1,)
    # An INTENTIONAL change from the first generation of this golden (the kernel fix of #696 review): a superseded
    # rule is removed before conflicts are judged. Before, R1 (a>b) still "opposed" R4 (b>a) although R2 (b>a,
    # superseding R1) had already removed it, and the answer was policy_conflict; now R2 and R4 agree and b is
    # preferred. It cannot arise in live H298, which has a single rule; the scenarios above are byte-identical.
    yield "superseded_rule_removed_before_conflict", _abc()[:2], (
        LabelRule("R1", {"a"}, {"b"}), LabelRule("R2", {"b"}, {"a"}, supersedes=("R1",)), LabelRule("R4", {"b"}, {"a"}))
    # Policy coverage: the administrative policy orders candidates inside a front, and these are the populations
    # where "default", "latest" and "most_reviewed" are told apart (review status first, or recency alone).
    mixed = [
        labelled("p", "p", status=S.not_reviewed, age_days=0, id_rank=1),
        labelled("q", "q", status=S.approved, age_days=400, id_rank=2),
        labelled("r", "r", status=S.approved, age_days=30, id_rank=3),
        labelled("s", "s", status=S.not_reviewed, age_days=900, id_rank=4),
    ]
    yield "policy_matters_no_rules", mixed, ()
    yield "policy_matters_inside_a_front", mixed, (LabelRule("R1", {"p", "q", "r"}, {"s"}),)
    yield "policy_matters_with_a_single_winner_chain", mixed, (LabelRule("R1", {"q"}, {"p", "r", "s"}),)
    yield "empty", [], (e1,)
    yield "no_rules_incomparable", _abc(), ()
    yield "two_fronts_admin_order", [*_abc(), labelled("d", "d", age_days=0, id_rank=0)], (LabelRule("R1", {"a", "b"}, {"c"}),)


def _generate():
    out = {"decisions": {}, "manifests": {}}
    for name, candidates, rules in _scenarios():
        for policy in SelectionPolicy:
            decision = engine.decide(candidates, subject=METHANE, admin_policy=policy, rules=rules)
            out["decisions"][f"{name}/{policy.value}"] = decision.to_dict()
    # Manifest assembly and replay around one decision per outcome.
    for name, candidates, rules in _scenarios():
        decision = engine.decide(candidates, subject=METHANE, admin_policy=SelectionPolicy.default, rules=rules)
        rows = [
            (
                c,
                CandidateAssessment(
                    thermo_ref=c.thermo_ref,
                    applicability=Applicability.applicable,
                    reasons=(),
                    answer_representation="h298",
                    value_kj_mol=-74.6,
                    representations=({"representation": "h298", "value_kj_mol": -74.6},),
                ),
                True,
            )
            for c in candidates
        ]
        manifest = build_manifest(
            request=H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble),
            conformer_group_ref=None,
            effective_statuses=[S.approved, S.not_reviewed],
            subject=METHANE,
            total_rows=len(candidates),
            visible_candidates=len(candidates),
            cap=500,
            excluded_by_review=[],
            candidates=rows,
            decision=decision,
            outcome=decision.outcome,
        )
        entry = {"manifest": manifest}
        try:
            entry["replay"] = replay_decision(manifest, rules=rules)
        except Exception as exc:  # pragma: no cover - recorded, so a change in refusal is also caught
            entry["replay_error"] = f"{type(exc).__name__}: {exc}"
        out["manifests"][name] = entry
    return json.loads(json.dumps(out, sort_keys=True))


def test_h298_decisions_and_manifests_are_unchanged_by_the_kernel_extraction():
    generated = _generate()
    if os.environ.get("TCKDB_REGEN_H298_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(generated, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        pytest.skip("golden regenerated")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert generated["decisions"].keys() == golden["decisions"].keys()
    for key in golden["decisions"]:
        assert generated["decisions"][key] == golden["decisions"][key], key
    for key in golden["manifests"]:
        assert generated["manifests"][key] == golden["manifests"][key], key
    # Byte-identical, not merely equal as Python values.
    assert json.dumps(generated, sort_keys=True) == json.dumps(golden, sort_keys=True)


def test_the_golden_matrix_reaches_every_outcome_it_claims_to():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    seen = {d["outcome"] for d in golden["decisions"].values()}
    assert seen == {
        Outcome.policy_preferred.value,
        Outcome.incomparable_alternatives.value,
        Outcome.sole_eligible_candidate.value,
        Outcome.no_applicable_candidate.value,
        Outcome.policy_conflict.value,
    }
    assert any(d["overridden_edges"] for d in golden["decisions"].values())
    assert any(d["cycles"] for d in golden["decisions"].values())
    assert any(d["opposing_pairs"] for d in golden["decisions"].values())
    assert any(d["administrative_first"] for d in golden["decisions"].values())
    assert all(m.get("replay") == m["manifest"]["decision"] for m in golden["manifests"].values() if "replay" in m)


def test_a_manifest_made_under_the_first_policy_version_is_refused_not_re_answered():
    from app.services.thermo_selection.manifest import ReplayError
    from app.services.thermo_selection.models import POLICY_NAME, POLICY_VERSION

    assert POLICY_VERSION == "2"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    manifest = golden["manifests"]["e1_g4_over_g3"]["manifest"]
    assert manifest["policy"] == {"name": POLICY_NAME, "version": POLICY_VERSION}
    old = json.loads(json.dumps(manifest))
    old["policy"]["version"] = "1"
    with pytest.raises(ReplayError, match="this registry replays h298_method_preferred v2"):
        replay_decision(old, rules=(E1Rule(),))
