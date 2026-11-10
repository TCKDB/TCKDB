"""Replay of an H298 manifest reads exactly the running versions and refuses everything else.

Replay reads recorded eligibility and re-runs the ordering. A manifest with no assessment block, a malformed or foreign
one, a manifest format this release does not read is refused; nothing is
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
from tests.services.thermo_selection.test_h298_golden import _generate

NEW = json.loads((Path(__file__).parent / "golden" / "h298_decisions.json").read_text(encoding="utf-8"))


def _manifest(name: str) -> dict:
    return copy.deepcopy(NEW["manifests"][name]["manifest"])


def test_the_premise_the_golden_holds_current_manifests_with_the_block():
    assert len(NEW["manifests"]) >= 8
    assert all("assessment_semantics" in e["manifest"] for e in NEW["manifests"].values())


@pytest.mark.parametrize("name", sorted(NEW["manifests"]))
def test_a_current_manifest_replays_to_the_decision_it_recorded(name):
    manifest = _manifest(name)
    assert replay_decision(manifest) == (manifest["decision"] or {"outcome": manifest["outcome"]})
    assert replay_matches(manifest)


def test_a_manifest_built_now_records_the_structured_block():
    """Built by the code (not read from the committed golden), so dropping the block cannot hide behind a stale file."""
    manifest = _generate()["manifests"]["single"]["manifest"]
    assert manifest["assessment_semantics"] == {
        "name": "thermo_selection_assessment", "version": 2, "evidence_rubric": {"name": "computed_thermo", "version": 1},
        "source_findings": {"name": "structure_source_findings", "version": 1},
    }
    assert manifest["policy"]["version"] == POLICY_VERSION == 1
    assert manifest["format"]["version"] == MANIFEST_FORMAT_VERSION == 5


@pytest.mark.parametrize("block", [{"version": 3}, {"version": "1"}, {"version": True}, "two", {}], ids=["future", "string", "bool", "not_a_block", "empty"])
def test_a_manifest_recording_semantics_this_release_does_not_carry_is_refused(block):
    manifest = _manifest("single")
    manifest["assessment_semantics"] = block
    with pytest.raises(ReplayError):
        replay_decision(manifest)


def test_a_manifest_with_no_assessment_block_is_refused_not_read_as_current():
    """Kills: an absent block falling back to a default semantics (the retired pre-v2 reading)."""
    manifest = _manifest("single")
    del manifest["assessment_semantics"]
    with pytest.raises(ReplayError, match="assessment_semantics"):
        replay_decision(manifest)


def test_only_the_current_manifest_format_is_read():
    """Kills: the previous format (1) registered in MANIFEST_FORMATS."""
    assert {ref.version for ref in MANIFEST_FORMATS.refs()} == {MANIFEST_FORMAT_VERSION}
    for bad in (0, MANIFEST_FORMAT_VERSION + 1, None):
        manifest = _manifest("single")
        manifest["format"]["version"] = bad
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_decision(manifest)


@pytest.mark.parametrize("bad", [True, 1.0])
def test_a_policy_version_that_equals_the_integer_but_is_not_it_is_refused(bad):
    """Kills: ``manifest["policy"] != {"name": ..., "version": 1}`` letting ``true`` pass (``True == 1``)."""
    manifest = _manifest("single")
    manifest["policy"]["version"] = bad
    with pytest.raises(ReplayError, match="policy"):
        replay_decision(manifest)


def test_the_writer_stamps_the_registered_format_and_the_reader_refuses_a_float_bool_or_missing_one():
    from app.services.thermo_selection.models import MANIFEST_FORMAT_NAME
    from tests.services._format_cases import bad_format_manifests

    manifest = _manifest("single")
    current = MANIFEST_FORMATS.current(MANIFEST_FORMAT_NAME).version
    assert manifest["format"] == {"name": MANIFEST_FORMAT_NAME, "version": current} and type(current) is int
    for tampered in bad_format_manifests(manifest, current):
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_decision(tampered)
