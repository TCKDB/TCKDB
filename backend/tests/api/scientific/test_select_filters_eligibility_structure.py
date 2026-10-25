"""Structure select: the caller's filters, and the named eligibility defaults a request can lift.

``POST .../species-entries/{ref}/calculations/select`` (the calculation grain), ``.../conformers/select`` and
``.../transition-state-entries/{ref}/evidence/select``. The same contract as the other selects: filters narrow first,
every default is echoed, an override admits exactly what it names, ``curated`` cannot be overridden, a decision made
with filters and overrides replays, and an entry (a scoping parent) never excludes a unit by its own review status.
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import CalculationQuality, StructureFindingKind, SubmissionRecordType
from app.db.models.common import RecordReviewStatus as S
from app.services.structure_selection import replay_matches
from app.services.structure_selection.manifest import ReplayError, replay_structure_decision
from tests.services.scientific_read._factories import make_lot, make_software_release
from tests.services.statmech_selection.test_service import restatus
from tests.services.structure_selection._support import declaration, make_world

API = "/api/v1/scientific"
CALC_DEFAULTS = [
    "rejected_review_status", "deprecated_review_status", "confirmed_blocking_findings", "rejected_calculation_quality"
]


def calc_url(entry, *, manifest: bool = False) -> str:
    return f"{API}/species-entries/{entry.public_ref}/calculations/select" + ("/manifest" if manifest else "")


def conformer_url(entry, *, manifest: bool = False) -> str:
    return f"{API}/species-entries/{entry.public_ref}/conformers/select" + ("/manifest" if manifest else "")


def post(client, world, extra=None, *, profile=None, manifest=False):
    return client.post(
        calc_url(world.entry, manifest=manifest), json=extra or {}, params={"profile": profile} if profile else None
    )


def entry_of(body, name):
    return next(e for e in body["eligibility"] if e["name"] == name)


def unit(body, calc):
    return next(u for u in body["units"] if u["unit_ref"] == calc.public_ref)


def with_software(session, world, name, energy, status=S.approved, **kw):
    """An sp calculation that ran in ``name`` (set before the review is applied: an approved calculation is frozen)."""
    calc = world.sp(name, energy, declared=declaration(), status=status, **kw)
    calc.software_release_id = make_software_release(session, name=name).id
    session.flush()
    return calc


# -- step 1: filters ---------------------------------------------------------------------------------------------


def test_a_software_filter_narrows_the_calculations_and_the_minimum_follows(client, db_session):
    world = make_world(db_session)
    orca = with_software(db_session, world, "orca", -76.40)
    with_software(db_session, world, "gaussian", -76.90)  # lower, but it is filtered out
    world.settle()
    everything = post(client, world).json()
    assert everything["selected_refs"] != [orca.public_ref]
    body = post(client, world, {"filters": {"software": "orca"}}).json()
    assert [u["unit_ref"] for u in body["units"]] == [orca.public_ref] and body["selected_refs"] == [orca.public_ref]
    assert body["filters"]["applied"] == {"software": "orca"} and body["filters"]["removed"] == 1
    assert body["funnel"] == {
        "unit": "candidates", "considered": 2, "removed_by_filters": 1, "removed_by_floor": 0, "removed_by_eligibility": 0, "remaining": 1
    }
    assert body["request"]["filters"] == {"software": "orca"}


def test_method_and_basis_match_one_calculation_together(client, db_session):
    world = make_world(db_session)
    a = world.sp("a", -76.40, declared=declaration(), lot=make_lot(db_session, method="wb97xd", basis="def2tzvp"))
    world.sp("b", -76.90, declared=declaration(), lot=make_lot(db_session, method="wb97xd", basis="6-31g"))
    world.settle()
    body = post(client, world, {"filters": {"method": "wb97xd", "basis": "def2tzvp"}}).json()
    assert [u["unit_ref"] for u in body["units"]] == [a.public_ref]


def test_a_filter_the_search_does_not_offer_is_refused(client, db_session):
    world = make_world(db_session)
    world.sp("a", -76.40, declared=declaration())
    world.settle()
    assert post(client, world, {"filters": {"model_kind": "scalar"}}).status_code == 422
    assert post(client, world, {"filters": {"level_of_theory_ref": "lot_x"}}).status_code == 422


# -- step 2: eligibility -----------------------------------------------------------------------------------------


def test_every_default_is_echoed_per_grain(client, db_session):
    world = make_world(db_session)
    world.sp("a", -76.40, declared=declaration())
    world.settle()
    body = post(client, world).json()
    assert [e["name"] for e in body["eligibility"]] == CALC_DEFAULTS
    assert all(e["applied"] and e["source"] == "default" for e in body["eligibility"])
    conformers = client.post(conformer_url(world.entry), json={}).json()
    assert [e["name"] for e in conformers["eligibility"]] == CALC_DEFAULTS  # the quality default is echoed at every grain


def test_include_rejected_admits_exactly_the_rejected_calculation_and_says_so(client, db_session):
    world = make_world(db_session)
    ok = world.sp("ok", -76.40, declared=declaration())
    rejected = world.sp("rej", -76.90, declared=declaration(), status=S.rejected)
    deprecated = world.sp("dep", -77.50, declared=declaration(), status=S.deprecated)
    world.settle()
    default = post(client, world).json()
    assert default["selected_refs"] == [ok.public_ref]
    assert (entry_of(default, "rejected_review_status")["excluded"], entry_of(default, "deprecated_review_status")["excluded"]) == (1, 1)
    assert default["funnel"]["removed_by_eligibility"] == 2

    lifted = post(client, world, {"eligibility": {"include_rejected": True}}).json()
    assert {u["unit_ref"] for u in lifted["units"]} == {ok.public_ref, rejected.public_ref}
    assert lifted["selected_refs"] == [rejected.public_ref]
    assert unit(lifted, rejected)["passed_only_by_override"] == ["rejected_review_status"]
    assert unit(lifted, ok)["passed_only_by_override"] == []
    echo = entry_of(lifted, "rejected_review_status")
    assert (echo["applied"], echo["source"], echo["admitted_by_override"]) == (False, "request", 1)
    both = post(client, world, {"eligibility": {"include_rejected": True, "include_deprecated": True}}).json()
    assert unit(both, deprecated)["passed_only_by_override"] == ["deprecated_review_status"]
    assert both["selected_refs"] == [deprecated.public_ref]


def test_a_rejected_quality_is_lifted_by_permitting_it_and_the_echo_says_so(client, db_session):
    world = make_world(db_session)
    ok = world.sp("ok", -76.40, declared=declaration())
    bad = world.sp("bad", -76.90, declared=declaration(), quality=CalculationQuality.rejected)
    world.settle()
    default = post(client, world).json()
    assert default["selected_refs"] == [ok.public_ref]
    assert entry_of(default, "rejected_calculation_quality")["excluded"] == 1
    assert entry_of(default, "rejected_calculation_quality")["source"] == "default"

    lifted = post(client, world, {"permitted_quality": ["raw", "curated", "rejected"]}).json()
    echo = entry_of(lifted, "rejected_calculation_quality")
    assert (echo["applied"], echo["source"], echo["admitted_by_override"]) == (False, "request", 1)
    assert unit(lifted, bad)["passed_only_by_override"] == ["rejected_calculation_quality"]
    assert lifted["selected_refs"] == [bad.public_ref]
    # a quality the caller simply does not permit is the caller's own narrowing, not an eligibility default
    narrowed = post(client, world, {"permitted_quality": ["curated"]}).json()
    assert entry_of(narrowed, "rejected_calculation_quality")["applied"] is True
    assert narrowed["funnel"]["removed_by_filters"] >= 1


def test_a_confirmed_finding_excludes_by_default_and_is_still_disclosed_when_lifted(client, db_session):
    world = make_world(db_session)
    low = world.sp("low", -76.90, declared=declaration())
    high = world.sp("high", -76.40, declared=declaration())
    world.settle()
    world.finding(calculation=low, kind=StructureFindingKind.identity_incompatibility)
    db_session.flush()
    default = post(client, world).json()
    assert default["selected_refs"] == [high.public_ref]
    assert unit(default, low)["eligible"] is False and unit(default, low)["blocked_by"] == ["confirmed_blocking_findings"]
    assert entry_of(default, "confirmed_blocking_findings")["excluded"] == 1

    lifted = post(client, world, {"eligibility": {"include_confirmed_findings": True}}).json()
    u = unit(lifted, low)
    assert u["eligible"] is True and u["passed_only_by_override"] == ["confirmed_blocking_findings"]
    assert u["blocking"] == [] and any(a.startswith("finding_overridden:finding_invalidates") for a in u["advisory"])
    assert lifted["selected_refs"] == [low.public_ref]


def test_an_override_never_lifts_a_failure_of_the_claim_itself(client, db_session):
    """A null energy is not zero: no flag turns a missing fact into an answer."""
    world = make_world(db_session)
    world.sp("no_energy", None, declared=declaration())
    world.settle()
    lift = {"include_rejected": True, "include_deprecated": True, "include_confirmed_findings": True}
    body = post(client, world, {"eligibility": lift, "permitted_quality": ["raw", "curated", "rejected"]}).json()
    assert body["units"][0]["applicability"] == "unresolved" and body["selected_refs"] == []


def test_an_entrys_own_review_status_never_excludes_a_unit(client, db_session):
    world = make_world(db_session)
    a = world.sp("a", -76.40, declared=declaration(), status=S.not_reviewed)
    world.settle()
    restatus(db_session, SubmissionRecordType.species_entry, world.entry.id, S.deprecated)
    body = post(client, world).json()
    assert body["selected_refs"] == [a.public_ref] and body["disclosures"]["excluded_by_review"] == []
    floor = post(client, world, {"min_review_status": "not_reviewed"}).json()
    assert floor["selected_refs"] == [a.public_ref]


def test_curated_cannot_be_overridden(client, db_session):
    world = make_world(db_session)
    ok = world.sp("ok", -76.40, declared=declaration())
    for name, status in (("rej", S.rejected), ("dep", S.deprecated), ("new", S.not_reviewed)):
        world.sp(name, -77.0, declared=declaration(), status=status)
    world.settle()
    lift = {"include_rejected": True, "include_deprecated": True, "include_confirmed_findings": True}
    resp = post(client, world, {"eligibility": lift}, profile="curated")
    body = resp.json()
    assert [u["unit_ref"] for u in body["units"]] == [ok.public_ref]
    assert body["review"]["candidate_statuses"] == ["approved"]
    for name in ("rejected_review_status", "deprecated_review_status"):
        e = entry_of(body, name)
        assert (e["applied"], e["source"], e["excluded"], e["admitted_by_override"]) == (True, "read_profile", 0, 0)
    for name in ("rej", "dep", "new"):
        assert world.calcs[name].public_ref not in resp.text


# -- replay ------------------------------------------------------------------------------------------------------


def test_a_decision_made_with_filters_and_overrides_replays_at_both_levels(client, db_session):
    world = make_world(db_session)
    with_software(db_session, world, "orca", -76.40)
    with_software(db_session, world, "gaussian", -76.90)
    rejected = world.sp("rej", -77.0, declared=declaration(), status=S.rejected)
    rejected.software_release_id = make_software_release(db_session, name="orca").id
    world.settle()
    extra = {"filters": {"software": "orca"}, "eligibility": {"include_rejected": True}}
    response = post(client, world, extra).json()
    manifest = post(client, world, extra, manifest=True).json()
    assert manifest["request"]["filters"] == {"software": "orca"}
    assert manifest["request"]["eligibility"]["include_rejected"] is True
    assert set(manifest["verdicts"]) == {u["unit_ref"] for u in response["units"]}
    assert "rejected" in manifest["candidate_review_statuses"]
    assert replay_matches(manifest)
    assert replay_structure_decision(manifest)["selected_refs"] == response["selected_refs"]


def test_replay_refuses_a_manifest_whose_overrides_or_verdicts_were_edited(client, db_session):
    retamper = _retamper

    world = make_world(db_session)
    world.sp("ok", -76.40, declared=declaration())
    world.sp("rej", -76.90, declared=declaration(), status=S.rejected)
    world.settle()
    manifest = post(client, world, {"eligibility": {"include_rejected": True}}, manifest=True).json()
    assert replay_matches(manifest)

    def claims_the_default_was_applied(body):
        body["request"]["eligibility"]["include_rejected"] = False

    with pytest.raises(ReplayError) as caught:
        replay_structure_decision(retamper(manifest, claims_the_default_was_applied))
    assert caught.value.code == "inconsistent_manifest"

    def forges_a_verdict(body):
        for record in body["verdicts"].values():
            record["passed_only_by_override"] = []

    with pytest.raises(ReplayError) as caught:
        replay_structure_decision(retamper(manifest, forges_a_verdict))
    assert caught.value.code == "verdict_mismatch"

    stripped = copy.deepcopy(manifest)
    del stripped["request"]["eligibility"]
    with pytest.raises(Exception):
        replay_structure_decision(retamper(stripped, lambda body: None))


def _retamper(manifest, edit):
    """Apply ``edit`` to a copy of the manifest body and recompute every digest, as a careful forger would."""
    from app.services.structure_selection.manifest import digest

    body = copy.deepcopy({k: v for k, v in manifest.items() if k != "integrity"})
    edit(body)
    for section in ("calculations", "determinations"):
        for item in body[section]:
            inner = {k: v for k, v in item.items() if k != "content_sha256"}
            item["content_sha256"] = digest(inner)
    return {**body, "integrity": {**manifest["integrity"], "manifest_sha256": digest(body)}}


# -- scoping parents ---------------------------------------------------------------------------------------------


def test_an_approved_calculation_on_an_unreviewed_species_entry_is_a_candidate_under_curated(client, db_session):
    world = make_world(db_session, entry_status=None)  # the entry is not reviewed
    ok = world.sp("ok", -76.40, declared=declaration(), status=S.approved)
    world.settle()
    resp = post(client, world, profile="curated")
    assert resp.status_code == 200, resp.text
    assert [u["unit_ref"] for u in resp.json()["units"]] == [ok.public_ref]


def test_evidence_under_a_hidden_transition_state_entry_is_not_a_candidate_and_is_not_mentioned(client, db_session):
    from tests.services.structure_selection._support import make_ts_world

    world = make_ts_world(db_session, entry_status=None)  # a TS entry is a gated kind: it hides under curated
    world.sp("ev", -76.40, declared=declaration(), status=S.approved)
    world.settle()
    url = f"{API}/transition-state-entries/{world.entry.public_ref}/evidence/select"
    hidden = client.post(url, json={}, params={"profile": "curated"})
    ghost = client.post(
        f"{API}/transition-state-entries/tse_{'0' * 26}/evidence/select", json={}, params={"profile": "curated"}
    )
    assert hidden.status_code == ghost.status_code == 404
    assert world.calcs["ev"].public_ref not in hidden.text
    assert hidden.json()["code"] == ghost.json()["code"]


# -- a determination has no review of its own: nothing is copied onto it from a scoping parent -----------------


def _conformer_post(client, world, extra=None, *, profile=None, manifest=False):
    return client.post(
        conformer_url(world.entry, manifest=manifest), json=extra or {}, params={"profile": profile} if profile else None
    )


def test_a_basin_with_no_observation_has_no_review_status_of_its_own_and_replays(client, db_session):
    from tests.services.structure_selection.test_service import build_basin

    world = make_world(db_session, entry_status=S.rejected)  # the species entry is rejected: a scoping parent
    d, *_ = build_basin(world)
    body = _conformer_post(client, world).json()
    (unit_,) = body["units"]
    assert unit_["unit_ref"] == d.public_ref and unit_["review_status"] is None
    assert unit_["eligible"] is True and body["selected_refs"] == [d.public_ref]
    assert entry_of(body, "rejected_review_status")["excluded"] == 0
    manifest = _conformer_post(client, world, manifest=True).json()
    assert replay_matches(manifest)


def test_under_curated_an_unreviewed_entry_leaves_a_basin_selected_with_no_status_and_it_replays(client, db_session):
    from tests.services.structure_selection.test_service import build_basin

    world = make_world(db_session, entry_status=None)  # the entry is not reviewed
    d, *_ = build_basin(world)
    resp = _conformer_post(client, world, profile="curated")
    assert resp.status_code == 200, resp.text
    (unit_,) = resp.json()["units"]
    assert unit_["review_status"] is None and resp.json()["selected_refs"] == [d.public_ref]
    assert replay_matches(_conformer_post(client, world, profile="curated", manifest=True).json())


def _basin_in_group(db_session, *, group_status, observation_status=S.approved):
    from app.db.models.common import StructureDeterminationTargetKind
    from tests.services.scientific_read._factories import make_conformer_group, make_conformer_observation, set_review
    from tests.services.structure_selection.test_service import build_basin

    world = make_world(db_session)
    group = make_conformer_group(db_session, world.entry)
    obs = make_conformer_observation(db_session, conformer_group=group)
    d, *_ = build_basin(world, target=StructureDeterminationTargetKind.conformer_basin, observation=obs)
    set_review(db_session, record_type=SubmissionRecordType.conformer_observation, record_id=obs.id, status=observation_status)
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=group.id, status=group_status)
    return world, d, obs, group


def test_a_basin_in_a_rejected_group_is_excluded_by_the_review_default_and_admitted_by_the_override(client, db_session):
    world, d, obs, group = _basin_in_group(db_session, group_status=S.rejected)
    default = _conformer_post(client, world).json()
    assert default["units"] == [] and default["selected_refs"] == []
    assert entry_of(default, "rejected_review_status")["excluded"] == 1
    lifted = _conformer_post(client, world, {"eligibility": {"include_rejected": True}}).json()
    (u,) = lifted["units"]
    assert u["unit_ref"] == d.public_ref and u["review_status"] == "rejected"
    assert u["passed_only_by_override"] == ["rejected_review_status"] and lifted["selected_refs"] == [d.public_ref]
    assert replay_matches(_conformer_post(client, world, {"eligibility": {"include_rejected": True}}, manifest=True).json())


def test_a_basin_in_a_group_below_the_curated_floor_hides_with_the_group_and_is_not_mentioned(client, db_session):
    world, d, obs, group = _basin_in_group(db_session, group_status=S.rejected)
    resp = _conformer_post(client, world, {"eligibility": {"include_rejected": True}}, profile="curated")
    assert resp.status_code == 200
    assert resp.json()["units"] == []
    for ref in (obs.public_ref, group.public_ref, d.public_ref):
        assert ref not in resp.text
    unreviewed, d2, obs2, group2 = _basin_in_group(db_session, group_status=S.not_reviewed)
    hidden = _conformer_post(client, unreviewed, profile="curated")
    assert hidden.json()["units"] == [] and obs2.public_ref not in hidden.text and group2.public_ref not in hidden.text
