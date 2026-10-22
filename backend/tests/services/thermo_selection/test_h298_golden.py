"""The H298 selector's decisions and manifests are byte-identical to the committed golden.

``golden/h298_decisions.json`` is regenerated only with ``TCKDB_REGEN_H298_GOLDEN=1``, and only for a change to the
H298 semantics. It was regenerated when the method-preference rules were removed. The scenario names are the ones the
rule-era golden used and the candidate populations are unchanged, so a reviewer diffing the golden sees each
key move in place: a decision that used to carry an edge (9 of them E1's, 21 a synthetic R1-R4 test rule) or a
``policy_conflict`` is now ``incomparable_alternatives`` in one front, while sole, empty and already-incomparable
populations are unchanged. The names that mention a rule describe the population, not a rule that still exists.
The matrix runs every administrative policy over those populations. Each decision is compared as the exact JSON
the manifest embeds, together with the manifest assembled around it and its replay.
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
from app.services.thermo_selection.manifest import ReplayError, build_manifest, replay_decision
from app.services.thermo_selection.models import (
    Applicability,
    CandidateAssessment,
    H298Request,
    Outcome,
    Subject,
)
from tests.services.thermo_selection._support import cand, protocol

GOLDEN = Path(__file__).parent / "golden" / "h298_decisions.json"
METHANE = Subject(
    species_entry_ref="sp_Methane", inchi_key="VNWKTOKETHGBQD-UHFFFAOYSA-N", molecular_formula="CH4", charge=0,
    multiplicity=1, isotope_key=None, electronic_state_kind="ground", entry_kind="minimum",
)


def labelled(ref, label, **kw):
    return cand(ref, proto=protocol("g4", label=label), **kw)


def _abc():
    return [labelled("a", "a", age_days=3, id_rank=3), labelled("b", "b", age_days=2, id_rank=2), labelled("c", "c", age_days=1, id_rank=1)]


def _scenarios():
    g4g3 = [cand("g4", proto="g4", age_days=400, id_rank=1), cand("g3", proto="g3", age_days=1, id_rank=2)]
    status_vs_age = [
        cand("g4", proto="g4", status=S.not_reviewed, age_days=900, id_rank=1),
        cand("g3", proto="g3", status=S.approved, age_days=0, id_rank=2),
    ]
    blank = [cand("g4", proto="g4", age_days=5), cand("g3", proto="g3"), cand("blank", proto=None, age_days=1)]
    mixed = [
        labelled("p", "p", status=S.not_reviewed, age_days=0, id_rank=1),
        labelled("q", "q", status=S.approved, age_days=400, id_rank=2),
        labelled("r", "r", status=S.approved, age_days=30, id_rank=3),
        labelled("s", "s", status=S.not_reviewed, age_days=900, id_rank=4),
    ]
    yield "e1_g4_over_g3", g4g3
    yield "e1_status_cannot_reverse", status_vs_age
    yield "e1_blank_blocks_unique_winner", blank
    yield "chain", _abc()
    yield "cycle", _abc()
    yield "opposing_unresolved", _abc()[:2]
    yield "opposing_superseded", _abc()[:2]
    yield "opposing_mutual_supersession", _abc()[:2]
    yield "conflict_elsewhere", [*_abc()[:2], labelled("y", "y", id_rank=9), labelled("z", "z", id_rank=8)]
    yield "inactive_rule", _abc()[:2]
    yield "unknown_scope", _abc()[:2]
    yield "outside_scope", _abc()[:2]
    yield "single", _abc()[:1]
    yield "superseded_rule_removed_before_conflict", _abc()[:2]
    yield "policy_matters_no_rules", mixed
    yield "policy_matters_inside_a_front", mixed
    yield "policy_matters_with_a_single_winner_chain", mixed
    yield "empty", []
    yield "no_rules_incomparable", _abc()
    yield "two_fronts_admin_order", [*_abc(), labelled("d", "d", age_days=0, id_rank=0)]
    # New with the browse order: an under_review record ranks equal to a not_reviewed one, so recency decides between them.
    yield "under_review_ties_not_reviewed", [
        labelled("u", "u", status=S.under_review, age_days=1, id_rank=1),
        labelled("n", "n", status=S.not_reviewed, age_days=5, id_rank=2),
        labelled("m", "m", status=S.approved, age_days=200, id_rank=3),
    ]


def _generate():
    out = {"decisions": {}, "manifests": {}}
    for name, candidates in _scenarios():
        for policy in SelectionPolicy:
            out["decisions"][f"{name}/{policy.value}"] = engine.decide(candidates, admin_policy=policy).to_dict()
    for name, candidates in _scenarios():
        decision = engine.decide(candidates, admin_policy=SelectionPolicy.default)
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
            entry["replay"] = replay_decision(manifest)
        except Exception as exc:  # pragma: no cover - recorded, so a change in refusal is also caught
            entry["replay_error"] = f"{type(exc).__name__}: {exc}"
        out["manifests"][name] = entry
    return json.loads(json.dumps(out, sort_keys=True))


def test_h298_decisions_and_manifests_match_the_golden():
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
    assert json.dumps(generated, sort_keys=True) == json.dumps(golden, sort_keys=True)


def test_the_golden_matrix_reaches_every_outcome_the_engine_can_give():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    seen = {d["outcome"] for d in golden["decisions"].values()}
    assert len(golden["decisions"]) == 63  # 21 scenarios, three administrative policies each
    assert seen == {
        Outcome.incomparable_alternatives.value,
        Outcome.sole_eligible_candidate.value,
        Outcome.no_applicable_candidate.value,
    }
    assert all(len(d["fronts"]) <= 1 for d in golden["decisions"].values())
    assert any(d["administrative_first"] for d in golden["decisions"].values())
    assert all(m.get("replay") == m["manifest"]["decision"] for m in golden["manifests"].values())


def test_a_manifest_made_under_the_old_format_or_policy_is_refused_not_re_answered():
    from app.services.thermo_selection.models import POLICY_NAME, POLICY_VERSION

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    manifest = golden["manifests"]["e1_g4_over_g3"]["manifest"]
    assert manifest["format"] == {"name": "thermo_selection_manifest", "version": 2}
    assert manifest["policy"] == {"name": POLICY_NAME, "version": POLICY_VERSION}
    old_format = json.loads(json.dumps(manifest))
    old_format["format"]["version"] = 1
    with pytest.raises(ReplayError, match="unknown manifest format"):
        replay_decision(old_format)
    old_policy = json.loads(json.dumps(manifest))
    old_policy["policy"] = {"name": "h298_method_preferred", "version": 1}
    with pytest.raises(ReplayError, match="this registry replays h298_selection version 1"):
        replay_decision(old_policy)
