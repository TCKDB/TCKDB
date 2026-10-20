"""Replay of an H298 manifest reads exactly the running versions and refuses everything else.

Replay reads recorded eligibility and re-runs the ordering. A manifest with no assessment block, a malformed or foreign
one, a manifest format this release does not read, or a rule resting on different audited bytes is refused; nothing is
read as the current semantics by default, and nothing is labelled "historical".
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.thermo_selection.manifest import ReplayError, replay_decision, replay_matches
from app.services.thermo_selection.models import (
    MANIFEST_FORMAT_VERSION,
    MANIFEST_FORMATS,
    POLICY_VERSION,
)
from tests.services.thermo_selection.test_h298_golden import _generate, _scenarios

NEW = json.loads((Path(__file__).parent / "golden" / "h298_decisions.json").read_text(encoding="utf-8"))
RULES = {name: rules for name, _, rules in _scenarios()}


def _manifest(name: str) -> dict:
    return copy.deepcopy(NEW["manifests"][name]["manifest"])


def _e1(manifest: dict) -> dict:
    return next(r for r in manifest["decision"]["rules"] if r["rule_id"] == "E1")


def test_the_premise_the_golden_holds_current_manifests_with_the_block():
    assert len(NEW["manifests"]) >= 20
    assert all("assessment_semantics" in e["manifest"] for e in NEW["manifests"].values())


@pytest.mark.parametrize("name", sorted(NEW["manifests"]))
def test_a_current_manifest_replays_to_the_decision_it_recorded(name):
    manifest = _manifest(name)
    assert replay_decision(manifest, rules=RULES[name]) == (manifest["decision"] or {"outcome": manifest["outcome"]})
    assert replay_matches(manifest, rules=RULES[name])


def test_a_manifest_built_now_records_the_structured_block():
    """Built by the code (not read from the committed golden), so dropping the block cannot hide behind a stale file."""
    manifest = _generate()["manifests"]["single"]["manifest"]
    assert manifest["assessment_semantics"] == {
        "version": 1, "evidence_rubric": {"name": "computed_thermo", "version": 1}, "source_findings": 1,
    }
    assert manifest["policy"]["version"] == POLICY_VERSION == 1


@pytest.mark.parametrize("block", [{"version": 3}, {"version": "1"}, {"version": True}, "two", {}], ids=["future", "string", "bool", "not_a_block", "empty"])
def test_a_manifest_recording_semantics_this_release_does_not_carry_is_refused(block):
    manifest = _manifest("single")
    manifest["assessment_semantics"] = block
    with pytest.raises(ReplayError):
        replay_decision(manifest, rules=RULES["single"])


def test_a_manifest_with_no_assessment_block_is_refused_not_read_as_current():
    """Kills: an absent block falling back to a default semantics (the retired pre-v2 reading)."""
    manifest = _manifest("single")
    del manifest["assessment_semantics"]
    with pytest.raises(ReplayError, match="assessment_semantics"):
        replay_decision(manifest, rules=RULES["single"])


def test_only_the_current_manifest_format_is_read():
    """Kills: the previous format (1) registered in MANIFEST_FORMATS."""
    assert {ref.version for ref in MANIFEST_FORMATS.refs()} == {MANIFEST_FORMAT_VERSION}
    for bad in (0, MANIFEST_FORMAT_VERSION + 1, None):
        manifest = _manifest("single")
        manifest["manifest_format_version"] = bad
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_decision(manifest, rules=RULES["single"])


def test_replay_refuses_a_rule_resting_on_different_audited_bytes():
    """The E1 rule's audited-manifest digest is checked at replay, as kinetics, statmech and network do.

    Kills: the digest comparison dropped from ``replay_decision`` (it used to check only ``(rule_id, version)``)."""
    name = "e1_g4_over_g3"
    manifest = _manifest(name)
    assert len(_e1(manifest)["manifest_sha256"]) == 64
    assert replay_matches(copy.deepcopy(manifest), rules=RULES[name])
    _e1(manifest)["manifest_sha256"] = "0" * 64
    with pytest.raises(ReplayError, match="different audited manifest"):
        replay_decision(manifest, rules=RULES[name])
    manifest = _manifest(name)
    del _e1(manifest)["manifest_sha256"]
    with pytest.raises(ReplayError, match="different audited manifest"):
        replay_decision(manifest, rules=RULES[name])


def test_a_rule_version_this_release_does_not_carry_is_refused():
    manifest = _manifest("e1_g4_over_g3")
    _e1(manifest)["version"] = 9
    with pytest.raises(ReplayError, match="not in the running registry"):
        replay_decision(manifest, rules=RULES["e1_g4_over_g3"])


def test_the_writer_stamps_the_registered_format_and_the_reader_refuses_a_float_bool_or_missing_one():
    from app.services.thermo_selection.models import MANIFEST_FORMAT_NAME
    from tests.services._format_cases import bad_format_versions

    manifest = _manifest("single")
    current = MANIFEST_FORMATS.current(MANIFEST_FORMAT_NAME).version
    assert manifest["manifest_format_version"] == current and type(current) is int
    for bad in bad_format_versions(current):
        tampered = {**manifest, "manifest_format_version": bad}
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_decision(tampered, rules=RULES["single"])
    missing = {k: v for k, v in manifest.items() if k != "manifest_format_version"}
    with pytest.raises(ReplayError, match="unknown manifest format"):
        replay_decision(missing, rules=RULES["single"])
