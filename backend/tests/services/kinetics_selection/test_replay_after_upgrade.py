"""A kinetics decision manifest made BEFORE assessment semantics were versioned still replays, and says it is historical.

``golden/kinetics_manifest_pre_v2_assessment.json`` is a manifest in the exact shape the builder wrote before the
structured ``assessment_semantics`` block and the ``evidence_rubric:`` advisory existed (the two fields this change added,
and nothing else, removed from a real manifest). Replay reads recorded eligibility and re-runs the ordering; it must keep
doing so for such a manifest, label it ``pre_v2_assessment`` and historical, and refuse a manifest that records
semantics this release does not carry. Regenerate the fixture only with ``TCKDB_REGEN_KINETICS_PRE_V2=1``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.chemistry.kinetics_rules.xyg3_barrier_manifest import MANIFEST_0_3_0_PATH, MANIFEST_PATH
from app.db.models.common import RecordReviewStatus
from app.services.kinetics_selection import ReplayError, replay_decision, replay_matches
from app.services.kinetics_selection.manifest import replay_provenance
from app.services.kinetics_selection.models import ASSESSMENT_SEMANTICS_VERSION, POLICY_VERSION
from app.services.kinetics_selection.rules import (
    XYG3_MANIFEST_SHA256,
    XYG3_RULE_VERSION_PINS,
    default_rules,
    replay_rules,
)
from tests.services.kinetics_selection.test_selection import PREFERRED, YIELDING, select, with_protocol

FIXTURE = Path(__file__).parent / "golden" / "kinetics_manifest_pre_v2_assessment.json"
#: The same fixture as committed before the XYG3 manifest moved to 0.4.0: it records rule K-XYG3-B3LYP-BARRIER 0.2.0 over
#: manifest 0.3.0. Never regenerate it; it stands for a decision recorded before that release.
OLD_RULE_FIXTURE = Path(__file__).parent / "golden" / "kinetics_manifest_pre_v2_assessment_rule_0_2_0.json"


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


# -- a decision recorded under an earlier version of the XYG3 rule still replays ------------------------------------


def _xyg3_entry(manifest: dict) -> dict:
    return next(r for r in manifest["decision"]["rules"] if r["rule_id"] == "K-XYG3-B3LYP-BARRIER")


def test_a_decision_recorded_under_rule_0_2_0_still_replays_and_names_its_manifest():
    old = json.loads(OLD_RULE_FIXTURE.read_text(encoding="utf-8"))
    entry = _xyg3_entry(old)
    assert entry["version"] == "0.2.0"
    assert entry["manifest_sha256"] == "1ce59bde6133851d0b92324e6a563f9d7b43c5e2813226fdc91dfcfef1aedd96"
    assert replay_matches(old)
    assert replay_decision(old) == old["decision"]


def test_the_earlier_rule_version_is_for_replay_only_and_the_live_registry_carries_the_current_one():
    live = {(r.rule_id, r.version) for r in default_rules()}
    assert ("K-XYG3-B3LYP-BARRIER", "0.3.0") in live and ("K-XYG3-B3LYP-BARRIER", "0.2.0") not in live
    replayable = {(r.rule_id, r.version): r for r in replay_rules()}
    assert ("K-XYG3-B3LYP-BARRIER", "0.2.0") in replayable and live <= set(replayable)
    assert replayable[("K-XYG3-B3LYP-BARRIER", "0.2.0")].status == "inactive"


def test_a_version_that_never_existed_is_still_refused_naming_it():
    old = json.loads(OLD_RULE_FIXTURE.read_text(encoding="utf-8"))
    _xyg3_entry(old)["version"] = "0.1.9"
    with pytest.raises(ReplayError, match="0.1.9"):
        replay_decision(old)


def test_the_old_rule_refuses_a_manifest_that_is_not_the_one_it_recorded():
    old = json.loads(OLD_RULE_FIXTURE.read_text(encoding="utf-8"))
    _xyg3_entry(old)["manifest_sha256"] = XYG3_MANIFEST_SHA256  # 0.2.0 does not rest on the current manifest
    with pytest.raises(ReplayError, match="different audited manifest"):
        replay_decision(old)


def test_each_xyg3_rule_version_is_tied_to_one_manifest_digest_and_moving_a_pin_needs_a_new_version():
    """The ledger is written out here, so regenerating a golden or editing a pin together with its test cannot hide a move."""
    ledger = {
        "0.2.0": "1ce59bde6133851d0b92324e6a563f9d7b43c5e2813226fdc91dfcfef1aedd96",
        "0.3.0": "e7f37fa3b726f632c3a264d8a62e216945eff5c8036fcf6afef7dfcafd976989",
    }
    assert XYG3_RULE_VERSION_PINS == ledger
    assert hashlib.sha256(MANIFEST_0_3_0_PATH.read_bytes()).hexdigest() == ledger["0.2.0"]
    assert hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest() == ledger["0.3.0"]
    for rule in replay_rules():
        if rule.rule_id == "K-XYG3-B3LYP-BARRIER":
            assert rule.describe()["manifest_sha256"] == ledger[rule.version], rule.version
