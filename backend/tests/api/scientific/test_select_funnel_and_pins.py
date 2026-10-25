"""The funnel of a select call adds up on every route, a filter that removes everything says so, and the narrowings.

Each candidate is counted once, at the first step that removed it, so ``removed_by_filters + removed_by_floor +
removed_by_eligibility + remaining`` is ``considered``; and ``remaining`` is checked against the candidates the answer
lists, so a step that drops its own count cannot hide behind the subtraction that derives ``remaining``.
"""

from __future__ import annotations

import pytest

from app.db.models.common import CalculationQuality, SubmissionRecordType
from app.db.models.common import RecordReviewStatus as S
from app.services.thermo_selection.manifest import ReplayError, replay_decision
from tests.api.scientific import test_select_filters_eligibility_kinetics as kin
from tests.api.scientific import test_select_filters_eligibility_network as net
from tests.api.scientific import test_select_filters_eligibility_statmech as sm
from tests.api.scientific import test_select_filters_eligibility_structure as st
from tests.api.scientific import test_select_filters_eligibility_thermo as th
from tests.services.network_selection._world import build_world
from tests.services.statmech_selection.test_service import build_ts_scene, restatus
from tests.services.structure_selection._support import declaration, make_world
from tests.services.thermo_selection._support import species_entry_for


@pytest.fixture
def methane(db_session):
    return species_entry_for(db_session, "Methane")


@pytest.fixture
def nworld(db_session):
    return build_world(db_session)


STEPS = ("removed_by_filters", "removed_by_floor", "removed_by_eligibility", "remaining")


def assert_adds_up(body, listed_not_blocked: int, *, considered: int, **expected) -> None:
    funnel = body["funnel"]
    assert funnel["considered"] == considered
    assert sum(funnel[k] for k in STEPS) == funnel["considered"]
    assert funnel["remaining"] == listed_not_blocked  # against the answer's own list, not the derived subtraction
    for key, value in expected.items():
        assert funnel[key] == value, (key, funnel)
    assert body["filters"]["removed"] == funnel["removed_by_filters"]
    assert_selected_are_eligible(body)


def assert_selected_are_eligible(body) -> None:
    """The answer cannot contradict itself: whatever it selects competes, and `remaining` has room for it."""
    items = body.get("units") or body.get("candidates") or []
    by_ref = {
        i.get("unit_ref") or i.get("thermo_ref") or i.get("kinetics_ref") or i.get("candidate_ref"): i for i in items
    }
    selection = body.get("selection") or {}
    refs = list(body.get("selected_refs") or [])
    refs += [selection[k] for k in ("thermo_ref", "sole_eligible_ref", "administrative_ref") if selection.get(k)]
    refs += list(selection.get("kinetics_refs") or [])
    named = [r for r in refs if r in by_ref]
    for ref in named:
        assert by_ref[ref]["eligible"] is True, f"{ref} is selected but reported not eligible"
        assert not by_ref[ref]["blocked_by"], f"{ref} is selected but blocked_by {by_ref[ref]['blocked_by']}"
    assert body["funnel"]["remaining"] >= len(named)


# -- thermo -----------------------------------------------------------------------------------------------------


def test_thermo_every_step_counts_its_own_candidates_once(client, db_session, methane):
    th.g4(db_session, methane, status=S.approved, sources=[{"software": "orca"}], age_days=5)  # remains
    th.g3(db_session, methane, status=S.approved, sources=[{"software": "gaussian"}])  # the filter
    th.g4(db_session, methane, status=S.rejected, sources=[{"software": "orca"}])  # a review default
    th.g3(db_session, methane, status=S.under_review, sources=[{"software": "orca"}])  # the caller's floor
    bad = th.g3(
        db_session, methane, status=S.approved, sources=[{"software": "orca", "rejected": True}]
    )  # a blocking default
    body = th.post(client, methane, {"filters": {"software": "orca"}, "min_review_status": "approved"}).json()
    assert_adds_up(
        body,
        len([c for c in body["candidates"] if not c["blocked_by"]]),
        considered=5,
        removed_by_filters=1,
        removed_by_floor=1,
        removed_by_eligibility=2,
        remaining=1,
    )
    assert th.candidate(body, bad)["blocked_by"] == ["evidence_hard_fail"]
    assert body["filters"]["applied"] == {"software": "orca"}  # the floor is its own step, not a filter


def test_thermo_per_default_counts_may_exceed_removed_by_eligibility_because_a_candidate_counts_under_each(
    client, db_session, methane
):
    th.g4(db_session, methane, status=S.rejected, sources=[{"rejected": True}])
    body = th.post(client, methane, {"eligibility": {"include_rejected": True}}).json()
    assert body["funnel"]["removed_by_eligibility"] == 1
    th.g3(db_session, methane, status=S.approved, sources=[{"rejected": True}])
    both = th.g4(db_session, methane, status=S.rejected, sources=[{"rejected": True}])
    body = th.post(client, methane, {"eligibility": {"include_rejected": True}}).json()
    assert th.candidate(body, both)["blocked_by"] == ["evidence_hard_fail"]


def test_thermo_only_a_competing_candidate_counts_as_admitted_by_override(client, db_session, methane):
    th.g4(
        db_session, methane, status=S.rejected, sources=[{"rejected": True}]
    )  # lifted status, still blocked by evidence
    body = th.post(client, methane, {"eligibility": {"include_rejected": True}}).json()
    assert th.entry_of(body, "rejected_review_status")["admitted_by_override"] == 0
    assert th.entry_of(body, "evidence_hard_fail")["excluded"] == 1
    assert body["funnel"]["removed_by_eligibility"] == 1


def test_thermo_filters_that_remove_everything_say_so(client, db_session, methane):
    th.g4(db_session, methane, status=S.approved)
    body = th.post(client, methane, {"filters": {"software": "orca"}}).json()
    assert body["outcome"] == "no_applicable_candidate" and body["candidates"] == []
    assert body["filters"]["note"] == "the filters removed all 1 candidates before anything was assessed"
    floored = th.post(client, methane, {"min_review_status": "approved"}).json()
    assert floored["filters"]["note"] is None  # the approved one stays


def test_thermo_a_hidden_source_calculation_never_counts_toward_a_filter_match_under_curated(
    client, db_session, methane
):
    """The curated D6 guard: an approved record whose only orca source is not reviewed matches nothing under curated."""
    kept = th.g4(db_session, methane, status=S.approved, sources=[{"software": "orca", "calc_status": S.not_reviewed}])
    exploratory = th.post(client, methane, {"filters": {"software": "orca"}}).json()
    assert [c["thermo_ref"] for c in exploratory["candidates"]] == [kept.public_ref]
    curated = th.post(client, methane, {"filters": {"software": "orca"}}, profile="curated").json()
    assert curated["candidates"] == [] and curated["filters"]["removed"] == 1
    assert curated["filters"]["note"].startswith("the filters removed all")


def test_thermo_replay_refuses_a_funnel_that_does_not_follow_from_the_candidates(client, db_session, methane):
    th.g4(db_session, methane, status=S.approved, sources=[{"rejected": True}])
    th.g3(db_session, methane, status=S.approved)
    manifest = th.post(client, methane, manifest=True).json()
    assert replay_decision(manifest)
    manifest["population"]["funnel"]["remaining"] += 1
    with pytest.raises(ReplayError, match="remaining"):
        replay_decision(manifest)
    manifest["population"]["funnel"]["remaining"] -= 1
    manifest["population"]["funnel"]["excluded_by_default"]["evidence_hard_fail"] = 0
    with pytest.raises(ReplayError, match="evidence_hard_fail"):
        replay_decision(manifest)


def test_thermo_an_earlier_manifest_format_is_refused_as_not_replayable(client, db_session, methane):
    th.g4(db_session, methane, status=S.approved)
    manifest = th.post(client, methane, manifest=True).json()
    manifest["format"]["version"] = 2
    with pytest.raises(ReplayError, match="earlier format, not replayable"):
        replay_decision(manifest)


# -- kinetics ---------------------------------------------------------------------------------------------------


def test_kinetics_every_step_counts_its_own_candidates_once(client, db_session, world):
    kin.record(db_session, world, "a", status=S.approved, sources=[{"software": "orca"}])
    kin.record(db_session, world, "b", status=S.approved, sources=[{"software": "gaussian"}])
    kin.record(db_session, world, "c", status=S.rejected, sources=[{"software": "orca"}])
    kin.record(db_session, world, "d", status=S.under_review, sources=[{"software": "orca"}])
    body = kin.post(client, world, {"filters": {"software": "orca"}, "min_review_status": "approved"}).json()
    assert_adds_up(
        body,
        len([c for c in body["candidates"] if not c["blocked_by"]]),
        considered=4,
        removed_by_filters=1,
        removed_by_floor=1,
        removed_by_eligibility=1,
        remaining=1,
    )
    none = kin.post(client, world, {"filters": {"software": "nonesuch"}}).json()
    assert none["filters"]["note"].startswith("the filters removed all")


# -- network ----------------------------------------------------------------------------------------------------


def test_network_every_step_counts_its_own_solves_once(client, db_session, nworld):
    a_, b_, c_, d_ = "chemically_significant_eigenvalues", "modified_strong_collision", "reservoir_state", "rate_limit"
    net.solve(db_session, nworld, a_, review=S.approved, software="orca")
    net.solve(db_session, nworld, b_, review=S.approved, software="gaussian")
    net.solve(db_session, nworld, c_, review=S.rejected, software="orca")
    net.solve(db_session, nworld, d_, review=S.under_review, software="orca")
    body = client.post(
        f"/api/v1/scientific/networks/{nworld.ref}/kinetics/select",
        json={**net.question(nworld), "filters": {"software": "orca"}, "min_review_status": "approved"},
    ).json()
    assert body["funnel"]["unit"] == "solves"
    assert_adds_up(
        body,
        len(body["solves"]),
        considered=4,
        removed_by_filters=1,
        removed_by_floor=1,
        removed_by_eligibility=1,
        remaining=1,
    )
    none = client.post(
        f"/api/v1/scientific/networks/{nworld.ref}/kinetics/select",
        json={**net.question(nworld), "filters": {"software": "nonesuch"}},
    ).json()
    assert none["filters"]["note"] == "the filters removed all 4 solves before anything was assessed"


def test_network_the_recorded_eligibility_holds_only_the_flags_the_route_offers(client, db_session, nworld):
    net.solve(db_session, nworld, "chemically_significant_eigenvalues", review=S.approved)
    manifest = net.post(client, nworld, manifest=True).json()
    assert set(manifest["request"]["eligibility"]) == {"include_rejected", "include_deprecated"}


# -- statmech ---------------------------------------------------------------------------------------------------


def test_statmech_the_floor_and_the_filters_are_separate_steps_and_a_model_revisions_population_is_listed(
    client, db_session
):
    scene = sm.scene_with_software(db_session, "orca", status=S.not_reviewed)
    floored = sm.post(client, scene, {"min_review_status": "approved"}).json()
    assert floored["candidates"] == []
    assert_adds_up(
        floored, 0, considered=1, removed_by_filters=0, removed_by_floor=1, removed_by_eligibility=0, remaining=0
    )
    assert (
        floored["filters"]["note"] == "the caller's review floor removed all 1 candidates before anything was assessed"
    )
    ref = sm.post(client, scene).json()["candidates"][0]["candidate_ref"]
    explicit = sm.post(client, scene, {"population": {"kind": "model_revisions", "model_refs": [ref]}}).json()
    assert explicit["filters"]["applied"]["population"] == {
        "kind": "model_revisions",
        "model_refs": [ref],
        "release_ref": None,
    }
    other = sm.post(client, scene, {"filters": {"software": "gaussian"}}).json()
    assert_adds_up(
        other, 0, considered=1, removed_by_filters=1, removed_by_floor=0, removed_by_eligibility=0, remaining=0
    )
    assert other["filters"]["note"].startswith("the filters removed all")


def test_statmech_a_transition_state_entry_still_gates_its_candidates(client, db_session):
    """Kills: ``entry_gates`` switched off. A TS entry is a gated kind; a species entry is not."""
    from app.services.statmech_selection import manifest as manifest_module

    ts = build_ts_scene(db_session)
    url = "/api/v1/scientific/statmech/select"
    assert len(client.post(url, json=ts.body).json()["candidates"]) == 1
    restatus(db_session, SubmissionRecordType.transition_state_entry, ts.entry.id, S.rejected)
    rejected = client.post(url, json=ts.body).json()
    assert rejected["candidates"] == []
    assert next(e for e in rejected["eligibility"] if e["name"] == "entry_review_status")["excluded"] == 1
    restatus(db_session, SubmissionRecordType.transition_state_entry, ts.entry.id, S.not_reviewed)
    floor = client.post(url, json={**ts.body, "min_review_status": "approved"}).json()
    assert floor["candidates"] == []
    lifted = client.post(
        url, json={**ts.body, "eligibility": {"include_rejected": True, "include_deprecated": True}}
    ).json()
    assert (
        len(lifted["candidates"]) == 1
    )  # not_reviewed passes by default; the lifts do not matter to a TS entry's own floor
    assert manifest_module.replay_matches(client.post(url + "/manifest", json=ts.body).json())


# -- structure --------------------------------------------------------------------------------------------------


def test_structure_every_step_counts_its_own_calculations_once_and_the_narrowings_are_listed(client, db_session):
    world = make_world(db_session)
    st.with_software(db_session, world, "orca", -76.40)
    st.with_software(db_session, world, "gaussian", -76.50)
    other = world.sp("pending", -76.7, declared=declaration(), status=S.under_review)
    other.software_release_id = world.calcs["orca"].software_release_id
    world.settle()
    body = st.post(client, world, {"filters": {"software": "orca"}, "min_review_status": "approved"}).json()
    assert_adds_up(
        body,
        len(body["units"]),
        considered=3,
        removed_by_filters=1,
        removed_by_floor=1,
        removed_by_eligibility=0,
        remaining=1,
    )
    narrowed = st.post(
        client, world, {"permitted_quality": ["curated"], "member_refs": [world.calcs["orca"].public_ref]}
    ).json()
    assert narrowed["filters"]["applied"] == {
        "permitted_quality": ["curated"],
        "member_refs": [world.calcs["orca"].public_ref],
    }
    assert narrowed["filters"]["removed"] == narrowed["funnel"]["removed_by_filters"]
    none = st.post(client, world, {"filters": {"software": "nonesuch"}}).json()
    assert none["filters"]["note"] == "the filters removed all 3 candidates before anything was assessed"


def _basin_with_rejected_freq(db_session):
    from tests.services.structure_selection.test_service import build_basin

    world = make_world(db_session)
    d, *_ = build_basin(world, qualities={"freq": CalculationQuality.rejected})
    return world, d


def test_structure_the_quality_default_is_echoed_and_applied_at_the_conformer_grain(client, db_session):
    world, d = _basin_with_rejected_freq(db_session)
    body = st._conformer_post(client, world).json()
    echo = st.entry_of(body, "rejected_calculation_quality")
    assert (echo["applied"], echo["source"]) == (True, "default")
    (unit,) = body["units"]
    assert unit["blocked_by"] == ["rejected_calculation_quality"] and unit["eligible"] is False
    assert echo["excluded"] == 1 and body["funnel"]["removed_by_eligibility"] == 1


def test_structure_permitting_a_rejected_quality_admits_a_basin_by_override_and_it_replays(client, db_session):
    world, d = _basin_with_rejected_freq(db_session)
    body = st._conformer_post(client, world, {"permitted_quality": ["raw", "curated", "rejected"]}).json()
    (unit,) = body["units"]
    assert unit["passed_only_by_override"] == ["rejected_calculation_quality"] and unit["eligible"] is True
    echo = st.entry_of(body, "rejected_calculation_quality")
    assert (echo["applied"], echo["source"], echo["admitted_by_override"]) == (False, "request", 1)
    manifest = st._conformer_post(
        client, world, {"permitted_quality": ["raw", "curated", "rejected"]}, manifest=True
    ).json()
    assert st.replay_matches(manifest)


def test_structure_a_ts_evidence_selection_echoes_the_quality_default_too(client, db_session):
    from tests.services.structure_selection._support import make_ts_world

    world = make_ts_world(db_session)
    world.sp("a", -76.4, declared=declaration())
    world.settle()
    body = client.post(f"{st.API}/transition-state-entries/{world.entry.public_ref}/evidence/select", json={}).json()
    assert "rejected_calculation_quality" in [e["name"] for e in body["eligibility"]]


# -- a source in a role the request does not need cannot block a unit the assessment selects ------------------


def _qualify_evidence(client, url, extra=None):
    body = {"intent": "qualify_evidence", "quantity": None, "validation_claim": extra or "local_minimum"}
    return client.post(url, json=body).json()


def test_a_rejected_quality_energy_source_does_not_block_a_conformer_basin_when_no_energy_is_asked_for(
    client, db_session
):
    from tests.services.structure_selection.test_service import build_basin

    world = make_world(db_session)
    d, *_ = build_basin(world, qualities={"sp": CalculationQuality.rejected})  # the energy source is rejected
    body = _qualify_evidence(client, st.conformer_url(world.entry))
    (unit_,) = body["units"]
    assert unit_["unit_ref"] == d.public_ref and unit_["applicability"] == "applicable"
    assert unit_["eligible"] is True and unit_["blocked_by"] == []
    assert body["selected_refs"] == [d.public_ref]
    assert_adds_up(body, 1, considered=1, removed_by_eligibility=0, remaining=1)
    assert st.entry_of(body, "rejected_calculation_quality")["excluded"] == 0
    # asked for the energy, the same unit rests on a source the default kept out: not applicable, so not selected
    asked = st._conformer_post(client, world).json()
    assert asked["units"][0]["eligible"] is False and asked["selected_refs"] == []
    assert_adds_up(asked, 0, considered=1, removed_by_eligibility=1, remaining=0)


def test_a_rejected_quality_energy_source_does_not_block_a_saddle_when_no_energy_is_asked_for(client, db_session):
    from app.db.models.common import StructureDeterminationTargetKind
    from tests.services.structure_selection._support import make_ts_world

    world = make_ts_world(db_session)
    freq = world.freq("f", frequencies=(-1500.0, 100.0), reaction_coordinate_mode_index=1, structural_flag=False)
    opt = world.opt("o", -76.0)
    sp = world.sp("e", -76.0, quality=CalculationQuality.rejected)
    d = world.determination(
        [("geometry_optimization", opt), ("curvature", freq), ("energy", sp)],
        target=StructureDeterminationTargetKind.saddle_point,
        quantity=None,
    )
    url = f"{st.API}/transition-state-entries/{world.entry.public_ref}/evidence/select"
    body = _qualify_evidence(client, url, "first_order_saddle")
    (unit_,) = body["units"]
    assert unit_["unit_ref"] == d.public_ref and unit_["applicability"] == "applicable"
    assert unit_["eligible"] is True and unit_["blocked_by"] == [] and body["selected_refs"] == [d.public_ref]
    assert_adds_up(body, 1, considered=1, removed_by_eligibility=0, remaining=1)


def test_the_note_names_what_removed_the_units(client, db_session, methane):
    th.g4(db_session, methane, status=S.not_reviewed)
    floored = th.post(client, methane, {"min_review_status": "approved"}).json()
    assert (
        floored["filters"]["note"] == "the caller's review floor removed all 1 candidates before anything was assessed"
    )
    both = th.post(client, methane, {"min_review_status": "approved", "filters": {"software": "orca"}}).json()
    assert both["filters"]["note"] == "the filters removed all 1 candidates before anything was assessed"
