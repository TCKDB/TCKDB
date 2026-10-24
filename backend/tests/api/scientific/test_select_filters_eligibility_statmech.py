"""Statmech select: the caller's filters, and the named eligibility defaults a request can lift.

The same contract as thermo and kinetics, for ``POST /scientific/statmech/select``. A candidate has no evidence rubric
of its own, so its defaults are its rejected or deprecated review status and a confirmed finding about a source; the
entry is a scoping parent, and its own review status never excludes a candidate.
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.db.models.common import (
    StatmechCalculationRole,
    StatmechTreatmentKind,
    StructureFindingKind,
    SubmissionRecordType,
)
from app.services.statmech_selection import manifest as manifest_module
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    make_calculation,
    make_software_release,
    set_review,
)
from tests.services.statmech_selection.test_request import THERMAL, make
from tests.services.statmech_selection.test_service import build, restatus

URL = "/api/v1/scientific/statmech/select"
DEFAULTS = ["rejected_review_status", "deprecated_review_status", "confirmed_blocking_findings"]


def body_for(scene, **changes) -> dict:
    defaults = {
        "target__species_entry_ref": scene.world.entry.public_ref,
        "target__scope__conformer_group_ref": scene.group.public_ref,
    }
    return make(THERMAL, **{**defaults, **changes})


def post(client, scene, extra=None, *, profile=None, manifest=False):
    body = {**body_for(scene), **(extra or {})}
    return client.post(URL + ("/manifest" if manifest else ""), json=body, params={"profile": profile} if profile else None)


def scene_with_software(db_session, name, *, status=S.approved):
    """A scene whose candidate also cites a calculation that ran in ``name`` (linked before the review is set: approved is frozen)."""
    scene = build(db_session, host_status=None)
    release = make_software_release(db_session, name=name)
    calc = make_calculation(db_session, species_entry_id=scene.world.entry.id, software_release_id=release.id)
    attach_statmech_source_calculation(db_session, statmech=scene.host, calculation=calc, role=StatmechCalculationRole.sp)
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=scene.host.id, status=status)
    return scene


def entry_of(body, name):
    return next(e for e in body["eligibility"] if e["name"] == name)


# -- step 1: filters ---------------------------------------------------------------------------------------------


def test_a_model_kind_filter_uses_the_stored_treatment(client, db_session):
    scene = build(db_session)
    scene.host.statmech_treatment = StatmechTreatmentKind.rrho
    db_session.flush()
    kept = post(client, scene, {"filters": {"model_kind": "rrho"}}).json()
    assert [c["kind"] for c in kept["candidates"]] == ["declared_model"] and kept["filters"]["removed"] == 0
    removed = post(client, scene, {"filters": {"model_kind": "rrao"}}).json()
    assert removed["candidates"] == [] and removed["filters"]["removed"] == 1
    assert removed["filters"]["applied"] == {"model_kind": "rrao"}
    assert removed["funnel"] == {
        "unit": "candidates", "considered": 1, "removed_by_filters": 1, "removed_by_eligibility": 0, "remaining": 0
    }


def test_a_software_filter_keeps_a_candidate_whose_source_calculation_ran_there(client, db_session):
    scene = scene_with_software(db_session, "orca")
    assert len(post(client, scene, {"filters": {"software": "orca"}}).json()["candidates"]) == 1
    gone = post(client, scene, {"filters": {"software": "gaussian"}}).json()
    assert gone["candidates"] == [] and gone["filters"]["removed"] == 1


def test_a_filter_the_search_does_not_offer_is_refused(client, db_session):
    scene = build(db_session)
    assert post(client, scene, {"filters": {"level_of_theory_ref": "lot_x"}}).status_code == 422
    assert post(client, scene, {"filters": {"scientific_origin": "computed"}}).status_code == 422


# -- step 2: eligibility -----------------------------------------------------------------------------------------


def test_every_default_is_echoed_and_the_evidence_default_is_not_offered(client, db_session):
    scene = build(db_session)
    body = post(client, scene).json()
    assert [e["name"] for e in body["eligibility"]] == DEFAULTS
    assert all(e["applied"] and e["source"] == "default" for e in body["eligibility"])
    assert post(client, scene, {"eligibility": {"include_hard_failed_evidence": True}}).status_code == 422


def test_include_rejected_admits_a_rejected_candidate_and_says_so(client, db_session):
    scene = build(db_session, host_status=S.rejected)
    default = post(client, scene).json()
    assert default["candidates"] == [] and default["outcome"] == "no_candidates"
    assert entry_of(default, "rejected_review_status")["excluded"] == 1
    assert default["funnel"]["removed_by_eligibility"] == 1
    assert default["population"]["excluded_by_review"][0]["reason"] == "terminal_review_status"

    lifted = post(client, scene, {"eligibility": {"include_rejected": True}}).json()
    (candidate,) = lifted["candidates"]
    assert candidate["eligible"] is True and candidate["review_status"] == "rejected"
    assert candidate["passed_only_by_override"] == ["rejected_review_status"] and candidate["blocked_by"] == []
    echo = entry_of(lifted, "rejected_review_status")
    assert (echo["applied"], echo["source"], echo["admitted_by_override"]) == (False, "request", 1)
    assert entry_of(lifted, "deprecated_review_status")["applied"] is True
    assert "rejected" in lifted["review"]["candidate_statuses"] and "rejected" not in lifted["review"]["effective_statuses"]

    deprecated = build(db_session, host_status=S.deprecated)
    assert post(client, deprecated, {"eligibility": {"include_rejected": True}}).json()["candidates"] == []
    assert len(post(client, deprecated, {"eligibility": {"include_deprecated": True}}).json()["candidates"]) == 1


def test_a_confirmed_finding_excludes_by_default_and_is_still_disclosed_when_lifted(client, db_session):
    scene = build(db_session)
    scene.world.finding(calculation=scene.freq, kind=StructureFindingKind.identity_incompatibility)
    db_session.flush()
    default = post(client, scene).json()
    (c,) = default["candidates"]
    assert c["eligible"] is False and c["blocked_by"] == ["confirmed_blocking_findings"]
    assert any(r["code"].startswith("finding_invalidates") for r in c["reasons"])
    assert entry_of(default, "confirmed_blocking_findings")["excluded"] == 1

    lifted = post(client, scene, {"eligibility": {"include_confirmed_findings": True}}).json()
    (c,) = lifted["candidates"]
    assert c["eligible"] is True and c["passed_only_by_override"] == ["confirmed_blocking_findings"]
    assert c["reasons"] == [] and any(a.startswith("finding_overridden:finding_invalidates") for a in c["advisory"])
    assert entry_of(lifted, "confirmed_blocking_findings")["admitted_by_override"] == 1


def test_an_entrys_own_review_status_never_excludes_a_candidate(client, db_session):
    scene = build(db_session)
    restatus(db_session, SubmissionRecordType.species_entry, scene.world.entry.id, S.deprecated)
    body = post(client, scene).json()
    assert len(body["candidates"]) == 1 and body["candidates"][0]["eligible"] is True
    assert body["population"]["excluded_by_review"] == []


def test_curated_cannot_be_overridden(client, db_session):
    scene = build(db_session, host_status=S.approved)
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=scene.group.id, status=S.approved)
    rejected = build(db_session, host_status=S.rejected)  # a second world: nothing of it is reachable here
    lift = {"include_rejected": True, "include_deprecated": True, "include_confirmed_findings": True}
    body = post(client, scene, {"eligibility": lift}, profile="curated").json()
    assert body["review"]["candidate_statuses"] == ["approved"]
    for name in ("rejected_review_status", "deprecated_review_status"):
        e = entry_of(body, name)
        assert (e["applied"], e["source"], e["excluded"], e["admitted_by_override"]) == (True, "read_profile", 0, 0)
    assert rejected.host.public_ref not in str(body)


# -- replay ------------------------------------------------------------------------------------------------------


def test_a_decision_made_with_filters_and_overrides_replays_at_both_levels(client, db_session):
    scene = scene_with_software(db_session, "orca", status=S.rejected)
    extra = {"filters": {"software": "orca"}, "eligibility": {"include_rejected": True}}
    manifest = post(client, scene, extra, manifest=True).json()
    assert manifest["request"]["filters"] == {"software": "orca"}
    assert manifest["request"]["eligibility"]["include_rejected"] is True
    assert "rejected" in manifest["candidate_review_statuses"]
    (verdict,) = manifest["verdicts"].values()
    assert verdict["passed_only_by_override"] == ["rejected_review_status"] and verdict["competes"] is True
    assert manifest_module.replay_matches(manifest)
    assert manifest_module.reassess_statmech_captured_inputs(manifest)


def test_replay_refuses_a_manifest_whose_overrides_were_edited_even_with_every_checksum_recomputed(client, db_session):
    from tests.services.statmech_selection.test_manifest import retamper

    scene = build(db_session, host_status=S.rejected)
    manifest = post(client, scene, {"eligibility": {"include_rejected": True}}, manifest=True).json()
    assert manifest_module.replay_matches(manifest)

    def claims_the_default_was_applied(body):
        body["request"]["eligibility"]["include_rejected"] = False

    with pytest.raises(manifest_module.ReplayError) as caught:
        manifest_module.replay_statmech_decision(retamper(manifest, claims_the_default_was_applied))
    assert caught.value.code == "inconsistent_manifest"

    def forges_a_verdict(body):
        next(iter(body["verdicts"].values()))["passed_only_by_override"] = []

    with pytest.raises(manifest_module.ReplayError) as caught:
        manifest_module.replay_statmech_decision(retamper(manifest, forges_a_verdict))
    assert caught.value.code == "verdict_mismatch"

    stripped = copy.deepcopy(manifest)
    del stripped["request"]["eligibility"]
    with pytest.raises(Exception):
        manifest_module.replay_statmech_decision(retamper(stripped, lambda b: None))
