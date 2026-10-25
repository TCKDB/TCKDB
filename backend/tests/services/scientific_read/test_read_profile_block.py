"""The ``read_profile`` block of a selection manifest (format 4): the profile, its floor, and the evidence rule it applied."""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import ProfileRecommendation, ReadProfile
from app.services.scientific_read.common import (
    check_read_profile_block,
    linked_evidence_withheld_values,
    read_profile_block,
)
from app.services.scientific_read.profile import EXPLORATORY, ResolvedReadProfile

CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)


def test_the_block_records_the_rule_the_profile_applies():
    assert linked_evidence_withheld_values(profile=CURATED) == ["deprecated", "rejected"]
    assert linked_evidence_withheld_values(profile=EXPLORATORY) == []
    assert read_profile_block(profile=CURATED) == {
        "profile": "curated",
        "review_floor": "approved",
        "linked_evidence_withheld": ["deprecated", "rejected"],
    }
    assert read_profile_block(profile=EXPLORATORY) == {
        "profile": "exploratory",
        "review_floor": None,
        "linked_evidence_withheld": [],
    }


@pytest.mark.parametrize("profile", [CURATED, EXPLORATORY], ids=["curated", "exploratory"])
def test_a_block_the_writer_wrote_checks_out(profile):
    assert check_read_profile_block(read_profile_block(profile=profile)) is None


def test_a_block_with_another_rule_than_its_profile_applies_is_refused():
    """Kills: a manifest relabelled curated while it records the exploratory rule (or the reverse), and a missing key."""
    curated = read_profile_block(profile=CURATED)
    relabelled = {**copy.deepcopy(curated), "linked_evidence_withheld": []}
    assert "linked-evidence rule" in (check_read_profile_block(relabelled) or "")
    exploratory = read_profile_block(profile=EXPLORATORY)
    assert "linked-evidence rule" in (check_read_profile_block({**exploratory, "linked_evidence_withheld": ["rejected"]}) or "")
    missing = {k: v for k, v in curated.items() if k != "linked_evidence_withheld"}
    assert check_read_profile_block(missing) is not None
    assert check_read_profile_block(None) is not None
