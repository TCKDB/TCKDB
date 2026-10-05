"""H298 manifests made BEFORE assessment semantics were versioned replay after the upgrade, and say they are historical.

``golden/h298_decisions_pre_v2_assessment.json`` is the committed golden as it stood before this change: its manifests carry
no ``assessment_semantics`` block. Replay reads recorded eligibility and re-runs the ordering, so each must still replay to
the decision it recorded; the replay is labelled ``pre_v2_assessment`` and historical (never a reassessment); a manifest that
records semantics this release does not carry is refused. The *current* golden differs from the old one by that block alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.thermo_selection.manifest import ReplayError, replay_decision, replay_matches, replay_provenance
from app.services.thermo_selection.models import ASSESSMENT_SEMANTICS_VERSION, POLICY_VERSION
from tests.services.thermo_selection.test_h298_golden import _generate, _scenarios

GOLDEN = Path(__file__).parent / "golden"
OLD = json.loads((GOLDEN / "h298_decisions_pre_v2_assessment.json").read_text(encoding="utf-8"))
NEW = json.loads((GOLDEN / "h298_decisions.json").read_text(encoding="utf-8"))
RULES = {name: rules for name, _, rules in _scenarios()}


def test_the_premise_the_old_fixture_is_non_empty_and_has_no_block():
    assert len(OLD["manifests"]) >= 20
    assert all("assessment_semantics" not in entry["manifest"] for entry in OLD["manifests"].values())


@pytest.mark.parametrize("name", sorted(OLD["manifests"]))
def test_a_pre_v2_manifest_replays_to_the_decision_it_recorded_after_the_upgrade(name):
    manifest = OLD["manifests"][name]["manifest"]
    assert replay_decision(manifest, rules=RULES[name]) == manifest["decision"]
    assert replay_matches(manifest, rules=RULES[name])


@pytest.mark.parametrize("name", sorted(OLD["manifests"]))
def test_every_replay_of_a_pre_v2_manifest_is_labelled_historical(name):
    label = replay_provenance(OLD["manifests"][name]["manifest"])
    assert label["recorded_assessment_semantics"] == "pre_v2_assessment"
    assert label["current_assessment_semantics"] == ASSESSMENT_SEMANTICS_VERSION
    assert (label["kind"], label["reassessed"]) == ("historical", False)
    assert "does not reassess" in label["statement"]


def test_the_decision_procedure_version_did_not_move_and_the_old_decisions_are_byte_identical():
    assert POLICY_VERSION == "2"
    assert NEW["decisions"] == OLD["decisions"]
    for name, entry in OLD["manifests"].items():
        assert entry["manifest"]["policy"] == NEW["manifests"][name]["manifest"]["policy"]


def test_the_current_golden_differs_from_the_old_one_by_the_assessment_block_alone():
    for name, entry in OLD["manifests"].items():
        current = dict(NEW["manifests"][name]["manifest"])
        block = current.pop("assessment_semantics")
        assert block == {"version": "2", "evidence_rubric": "computed_thermo@2", "source_findings": "1"}
        assert current == entry["manifest"], name
        assert NEW["manifests"][name]["replay"] == entry.get("replay")


def test_a_current_manifest_is_labelled_current():
    manifest = NEW["manifests"]["e1_g4_over_g3"]["manifest"]
    assert replay_provenance(manifest)["kind"] == "current"


@pytest.mark.parametrize("block", [{"version": "3"}, {"version": 2}, "two", {}], ids=["future", "wrong_type", "string", "empty"])
def test_a_manifest_recording_semantics_this_release_does_not_carry_is_refused(block):
    manifest = json.loads(json.dumps(OLD["manifests"]["single"]["manifest"]))
    manifest["assessment_semantics"] = block
    with pytest.raises(ReplayError):
        replay_decision(manifest, rules=RULES["single"])
    with pytest.raises(ReplayError):
        replay_provenance(manifest)


def test_a_manifest_built_now_records_the_structured_block_and_the_unchanged_procedure_version():
    """Built by the code (not read from the committed golden), so dropping the block cannot hide behind a stale file."""
    manifest = _generate()["manifests"]["single"]["manifest"]
    assert manifest["assessment_semantics"] == {"version": "2", "evidence_rubric": "computed_thermo@2", "source_findings": "1"}
    assert manifest["policy"]["version"] == POLICY_VERSION == "2"
    assert replay_provenance(manifest)["kind"] == "current"
