"""Replay of a kinetics decision manifest reads exactly the running versions and refuses everything else.

Only the current versions exist. A manifest recording another assessment semantics, a missing or malformed block, a
manifest format this release does not read is refused, never replayed under today's rules and never labelled "historical".
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import RecordReviewStatus
from app.services.kinetics_selection import ReplayError, replay_decision, replay_matches
from app.services.kinetics_selection.manifest import MANIFEST_FORMAT_VERSION, MANIFEST_FORMATS
from app.services.kinetics_selection.models import (
    ASSESSMENT_SEMANTICS_NAME,
    ASSESSMENT_SEMANTICS_VERSION,
    POLICY_VERSION,
)
from tests.services.kinetics_selection.test_selection import PREFERRED, YIELDING, select, with_protocol


def _fresh(db_session, world) -> dict:
    with_protocol(db_session, world, "good", PREFERRED, status=RecordReviewStatus.not_reviewed)
    with_protocol(db_session, world, "other", YIELDING, status=RecordReviewStatus.approved)
    return select(db_session, world.entry).manifest


def test_a_manifest_made_now_carries_the_block_and_replays_to_its_recorded_decision(db_session, world):
    manifest = _fresh(db_session, world)
    assert manifest["assessment_semantics"] == {
        "name": ASSESSMENT_SEMANTICS_NAME,
        "version": ASSESSMENT_SEMANTICS_VERSION,
        "evidence_rubric": {"name": "computed_kinetics", "version": 1},
        "source_findings": {"name": "structure_source_findings", "version": 1},
    }
    assert manifest["policy"] == {"name": "kinetics_selection", "version": POLICY_VERSION}
    assert replay_matches(manifest)
    assert replay_decision(manifest) == manifest["decision"]


@pytest.mark.parametrize("block", [{"version": 3}, {"version": "1"}, {"version": True}, "two", {}], ids=["future", "string", "bool", "not_a_block", "empty"])
def test_a_manifest_recording_semantics_this_release_does_not_carry_is_refused(db_session, world, block):
    manifest = _fresh(db_session, world)
    manifest["assessment_semantics"] = block
    with pytest.raises(ReplayError):
        replay_decision(manifest)


def test_a_manifest_with_no_assessment_block_is_refused_not_read_as_current(db_session, world):
    """Kills: an absent block falling back to a default semantics (the retired pre-v2 reading)."""
    manifest = _fresh(db_session, world)
    del manifest["assessment_semantics"]
    with pytest.raises(ReplayError, match="assessment_semantics"):
        replay_decision(manifest)


def test_only_the_current_manifest_format_is_read(db_session, world):
    """Kills: the previous format (1) registered in MANIFEST_FORMATS."""
    assert {ref.version for ref in MANIFEST_FORMATS.refs()} == {MANIFEST_FORMAT_VERSION}
    manifest = _fresh(db_session, world)
    for bad in (0, MANIFEST_FORMAT_VERSION + 1, None):
        candidate = {**manifest, "format": {**manifest["format"], "version": bad}}
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_decision(candidate)


@pytest.mark.parametrize("bad", [True, 1.0])
def test_a_policy_version_that_equals_the_integer_but_is_not_it_is_refused(db_session, world, bad):
    """Kills: ``manifest["policy"] != {"name": ..., "version": 1}`` letting ``true`` pass (``True == 1``)."""
    manifest = _fresh(db_session, world)
    manifest["policy"]["version"] = bad
    with pytest.raises(ReplayError, match="policy"):
        replay_decision(manifest)


def test_the_writer_stamps_the_registered_format_and_the_reader_refuses_a_float_bool_or_missing_one(db_session, world):
    from app.services.kinetics_selection.manifest import MANIFEST_FORMAT_NAME
    from tests.services._format_cases import bad_format_manifests

    manifest = _fresh(db_session, world)
    current = MANIFEST_FORMATS.current(MANIFEST_FORMAT_NAME).version
    assert manifest["format"] == {"name": MANIFEST_FORMAT_NAME, "version": current} and type(current) is int
    for tampered in bad_format_manifests(manifest, current):
        with pytest.raises(ReplayError, match="unknown manifest format"):
            replay_decision(tampered)
