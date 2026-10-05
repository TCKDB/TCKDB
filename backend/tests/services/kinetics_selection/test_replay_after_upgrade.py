"""A kinetics decision manifest made BEFORE assessment semantics were versioned still replays, and says it is historical.

``golden/kinetics_manifest_pre_v2_assessment.json`` is a manifest in the exact shape the builder wrote before the
structured ``assessment_semantics`` block and the ``evidence_rubric:`` advisory existed (the two fields this change added,
and nothing else, removed from a real manifest). Replay reads recorded eligibility and re-runs the ordering; it must keep
doing so for such a manifest, label it ``pre_v2_assessment`` and historical, and refuse a manifest that records
semantics this release does not carry. Regenerate the fixture only with ``TCKDB_REGEN_KINETICS_PRE_V2=1``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from app.db.models.common import RecordReviewStatus
from app.services.kinetics_selection import ReplayError, replay_decision, replay_matches
from app.services.kinetics_selection.manifest import replay_provenance
from app.services.kinetics_selection.models import ASSESSMENT_SEMANTICS_VERSION, POLICY_VERSION
from app.services.kinetics_selection.rules import default_rules
from tests.services.kinetics_selection.test_selection import PREFERRED, YIELDING, select, with_protocol

FIXTURE = Path(__file__).parent / "golden" / "kinetics_manifest_pre_v2_assessment.json"


def _strip_to_pre_v2(manifest: dict) -> dict:
    """Remove exactly what this change added to a manifest."""
    old = copy.deepcopy(manifest)
    old.pop("assessment_semantics", None)
    for candidate in old["candidates"]:
        advisory = candidate["assessment"]["advisory"]
        candidate["assessment"]["advisory"] = [a for a in advisory if not a.startswith("evidence_rubric:")]
    return json.loads(json.dumps(old, sort_keys=True))


def test_regenerate_the_pre_v2_fixture(db_session, world):
    if os.environ.get("TCKDB_REGEN_KINETICS_PRE_V2") != "1":
        pytest.skip("fixture is committed; set TCKDB_REGEN_KINETICS_PRE_V2=1 to rewrite it")
    with_protocol(db_session, world, "good", PREFERRED, status=RecordReviewStatus.not_reviewed)
    with_protocol(db_session, world, "other", YIELDING, status=RecordReviewStatus.approved)
    manifest = select(db_session, world.entry, rules=default_rules()).manifest
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(_strip_to_pre_v2(manifest), sort_keys=True, indent=1) + "\n", encoding="utf-8")


def _old() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_has_the_pre_v2_shape():
    old = _old()
    assert "assessment_semantics" not in old
    assert old["policy"] == {"name": "kinetics_method_preferred", "version": POLICY_VERSION}
    assert old["candidates"] and all(
        not a.startswith("evidence_rubric:") for c in old["candidates"] for a in c["assessment"]["advisory"]
    )


def test_a_pre_v2_manifest_replays_to_the_decision_it_recorded_after_the_upgrade():
    old = _old()
    assert replay_matches(old)
    assert replay_decision(old) == old["decision"]


def test_the_replay_of_a_pre_v2_manifest_is_labelled_historical_and_not_a_reassessment():
    label = replay_provenance(_old())
    assert label["recorded_assessment_semantics"] == "pre_v2_assessment"
    assert label["current_assessment_semantics"] == ASSESSMENT_SEMANTICS_VERSION
    assert label["kind"] == "historical" and label["reassessed"] is False
    assert "does not reassess" in label["statement"] and "pre_v2_assessment" in label["statement"]


def test_a_manifest_made_now_carries_the_structured_block_and_is_labelled_current(db_session, world):
    with_protocol(db_session, world, "good", PREFERRED, status=RecordReviewStatus.not_reviewed)
    manifest = select(db_session, world.entry, rules=default_rules()).manifest
    assert manifest["assessment_semantics"] == {
        "version": ASSESSMENT_SEMANTICS_VERSION,
        "evidence_rubric": "computed_kinetics@2",
        "source_findings": "1",
    }
    assert replay_provenance(manifest)["kind"] == "current"
    assert manifest["policy"] == {"name": "kinetics_method_preferred", "version": POLICY_VERSION}  # procedure unchanged


def test_the_only_difference_between_the_fixture_and_a_fresh_manifest_is_what_this_change_added(db_session, world):
    with_protocol(db_session, world, "good", PREFERRED, status=RecordReviewStatus.not_reviewed)
    with_protocol(db_session, world, "other", YIELDING, status=RecordReviewStatus.approved)
    fresh = select(db_session, world.entry, rules=default_rules()).manifest
    stripped = _strip_to_pre_v2(fresh)
    assert set(stripped) == set(_old())  # the same top-level shape; refs differ per run, structure does not
    assert stripped["policy"] == _old()["policy"] and stripped["outcome"] == _old()["outcome"]


@pytest.mark.parametrize("block", [{"version": "3"}, {"version": 2}, "two", {}], ids=["future", "wrong_type", "string", "empty"])
def test_a_manifest_recording_semantics_this_release_does_not_carry_is_refused_not_silently_replayed(block):
    manifest = _old()
    manifest["assessment_semantics"] = block
    with pytest.raises(ReplayError):
        replay_decision(manifest)
    with pytest.raises(ReplayError):
        replay_provenance(manifest)
