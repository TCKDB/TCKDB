"""The public layer over a manifest: the response is built from the manifest, so the two cannot disagree.

The route tests hold the HTTP contract with one-candidate scenes; a decision with edges, fronts, conflicts and undecided
pairs needs synthetic rules, so it is checked here on the manifest the decision stage records.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.selection_kernel import Tri
from app.services.statmech_selection.manifest import canonical
from app.services.statmech_selection.public import MANIFEST_PATH, build_response, run_selection
from app.services.structure_selection.public import SNAPSHOT_OPT_OUT
from tests.services.statmech_selection._rules import SyntheticRule, beats, population, ref
from tests.services.statmech_selection.test_manifest import make_manifest


def response_for(**kw):
    return build_response(SimpleNamespace(manifest=make_manifest(**kw)))


def test_edges_fronts_and_the_scientific_ref_come_from_the_recorded_decision():
    out = response_for(rules=[beats("r", "a", "b", "c")], candidates=population("a", "b", "c"))
    assert out.outcome.value == "policy_preferred" and out.selection.scientific_ref == ref("a")
    assert [(e["preferred"], e["dispreferred"]) for e in out.edges] == [(ref("a"), ref("b")), (ref("a"), ref("c"))]
    assert out.fronts == [[ref("a")], [ref("b"), ref("c")]] or out.fronts[0] == [ref("a")]
    assert out.conflicts.cycles == [] and out.undecided == []
    assert out.manifest.manifest_sha256 == out.integrity.manifest_sha256 and out.manifest.path == MANIFEST_PATH


def test_conflicts_and_undecided_pairs_are_carried_not_dropped():
    conflict = response_for(rules=[beats("r1", "a", "b"), beats("r2", "b", "a")])
    assert conflict.outcome.value == "policy_conflict" and conflict.selection.scientific_ref is None
    assert conflict.conflicts.cycles and conflict.conflicts.opposing_pairs
    undecided = response_for(rules=[SyntheticRule("r", prefer={ref("a"): Tri.true}, yield_={ref("b"): Tri.unknown})])
    assert undecided.outcome.value == "unresolved_comparability" and undecided.undecided
    superseded = response_for(rules=[beats("r1", "a", "b"), beats("r2", "b", "a", supersedes=("r1",))])
    assert [o["rule_id"] for o in superseded.conflicts.overridden_edges] == ["r1"] and superseded.selection.scientific_ref == ref("b")


def test_the_response_descriptor_matches_the_manifest_it_describes():
    manifest = make_manifest()
    out = build_response(SimpleNamespace(manifest=manifest))
    assert out.manifest.canonical_bytes == len(canonical(manifest).encode())
    assert out.integrity.manifest_sha256 == manifest["integrity"]["manifest_sha256"]
    assert [c.candidate_ref for c in out.candidates] == [a["candidate_ref"] for a in manifest["assessments"]]


def test_a_session_that_is_not_a_snapshot_is_refused_unless_a_test_harness_says_so(db_session):
    from app.schemas.reads.scientific_statmech_selection import StatmechSelectionRequest
    from app.services.read_snapshot import SnapshotNotConsistentError
    from tests.services.statmech_selection.test_request import THERMAL, make
    from tests.services.statmech_selection.test_service import build

    scene = build(db_session)
    body = StatmechSelectionRequest.model_validate(
        make(THERMAL, target__species_entry_ref=scene.world.entry.public_ref, target__scope__conformer_group_ref=scene.group.public_ref)
    )
    db_session.info.pop(SNAPSHOT_OPT_OUT, None)
    with pytest.raises(SnapshotNotConsistentError):
        run_selection(db_session, body=body)
    db_session.info[SNAPSHOT_OPT_OUT] = True
    assert run_selection(db_session, body=body).outcome.value == "qualified_models"


def test_an_owner_is_compared_by_kind_as_well_as_id_because_two_tables_can_share_a_number(monkeypatch):
    """A species entry and a transition-state entry may both have id 5; they are not the same owner."""
    from app.api.error_contract import CodedValueError
    from app.services.statmech_selection import public
    from app.services.statmech_selection.vocabulary import OwnerKind

    monkeypatch.setattr(public, "entry_visible", lambda *a, **k: True)
    with pytest.raises(CodedValueError) as caught:
        public._check_owned(
            None, field="conformer_group_ref", kind_label="conformer_group", ref="cg_x",
            owner=(OwnerKind.species_entry, 5), target=(OwnerKind.transition_state_entry, 5), statmech_id=None, profile=frozenset(),
        )
    assert caught.value.code == public.CODE_SCOPE_OWNER_MISMATCH and caught.value.context == {"field": "conformer_group_ref"}
    public._check_owned(  # the same kind and id is the target's own
        None, field="conformer_group_ref", kind_label="conformer_group", ref="cg_x",
        owner=(OwnerKind.species_entry, 5), target=(OwnerKind.species_entry, 5), statmech_id=None, profile=frozenset(),
    )
