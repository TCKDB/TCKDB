"""Thermo select: the caller's filters, and the named eligibility defaults a request can lift.

A select call is: user filters, then requested eligibility, then the policy (none ranks methods), then the result.
These tests hold the first two steps for ``POST .../thermo/select``:

* a filter narrows the candidate pool, with the thermo search's own vocabulary and matching;
* every default is echoed (name, applied, by whose choice) and the funnel says what each step removed;
* each override admits exactly the records it should, and the candidate says which default it passed only by override;
* the read profile's floor is not a default: ``curated`` cannot be overridden;
* missing evidence (unresolved) and contradictory evidence (blocked) stay distinguishable;
* a display tie-breaker is not a preference;
* a decision made with filters and overrides replays from its manifest.
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import (
    CalculationQuality,
    PhaseKind,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.common import RecordReviewStatus as S
from app.services.thermo_selection import replay_decision, replay_matches
from app.services.thermo_selection.manifest import ReplayError
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_lot,
    make_software_release,
    make_workflow_tool_release,
    set_review,
)
from tests.services.thermo_selection._support import make_thermo, protocol, species_entry_for

EQUILIBRIUM = {"target": {"kind": "equilibrium_ensemble"}}
DEFAULTS = ["rejected_review_status", "deprecated_review_status", "evidence_hard_fail", "confirmed_blocking_findings"]


@pytest.fixture
def methane(db_session):
    return species_entry_for(db_session, "Methane")


def post(client, entry, extra=None, *, profile=None, manifest=False):
    url = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo/select" + ("/manifest" if manifest else "")
    return client.post(url, json={**EQUILIBRIUM, **(extra or {})}, params={"profile": profile} if profile else None)


def _build(session, entry, recipe, sources, status, **kw):
    thermo = make_thermo(session, entry, proto=protocol(recipe), **kw)
    for spec in sources or ():
        source(session, entry, thermo, **spec)
    if status is not None:
        set_review(session, record_type=SubmissionRecordType.thermo, record_id=thermo.id, status=status)  # last: approved is immutable
    return thermo


def g4(session, entry, *, sources=None, status=None, **kw):
    return _build(session, entry, "g4", sources, status, **kw)


def g3(session, entry, *, sources=None, status=None, **kw):
    return _build(session, entry, "g3", sources, status, **kw)


def source(
    session, entry, thermo, *, lot=None, software=None, rejected=False, calc_status=None, role=ThermoCalculationRole.opt,
    software_version=None, workflow_tool=None, workflow_tool_version=None,
):
    lot_row = make_lot(session, method=lot[0], basis=lot[1]) if lot else None
    release = (
        make_software_release(session, name=software, **({'version': software_version} if software_version else {}))
        if software
        else None
    )
    tool = (
        make_workflow_tool_release(session, name=workflow_tool, **({'version': workflow_tool_version} if workflow_tool_version else {}))
        if workflow_tool
        else None
    )
    calc = make_calculation(
        session,
        species_entry_id=entry.id,
        lot_id=lot_row.id if lot_row else None,
        software_release_id=release.id if release else None,
        workflow_tool_release_id=tool.id if tool else None,
    )
    if rejected:
        calc.quality = CalculationQuality.rejected
    attach_thermo_source_calculation(session, thermo=thermo, calculation=calc, role=role)
    if calc_status is not None:
        set_review(session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=calc_status)
    session.flush()
    return calc


def entry_of(body, name):
    return next(e for e in body["eligibility"] if e["name"] == name)


def candidate(body, thermo):
    return next(c for c in body["candidates"] if c["thermo_ref"] == thermo.public_ref)


# -- step 1: filters ---------------------------------------------------------------------------------------------


def test_a_software_filter_narrows_the_pool_with_the_browse_matching(client, db_session, methane):
    in_orca = g4(db_session, methane, age_days=3)
    source(db_session, methane, in_orca, software="orca")
    in_gaussian = g3(db_session, methane, age_days=2)
    source(db_session, methane, in_gaussian, software="gaussian")
    unsourced = g4(db_session, methane, age_days=1)

    everything = post(client, methane).json()
    assert {c["thermo_ref"] for c in everything["candidates"]} == {in_orca.public_ref, in_gaussian.public_ref, unsourced.public_ref}

    body = post(client, methane, {"filters": {"software": "orca"}}).json()
    assert [c["thermo_ref"] for c in body["candidates"]] == [in_orca.public_ref]
    assert body["outcome"] == "sole_eligible_candidate"
    assert body["filters"]["applied"] == {"software": "orca"}
    assert body["filters"]["removed"] == 2
    assert body["funnel"] == {"unit": "candidates", "considered": 3, "removed_by_filters": 2, "removed_by_floor": 0, "removed_by_eligibility": 0, "remaining": 1}


def test_a_level_of_theory_filter_takes_a_public_ref_and_keeps_any_matching_source(client, db_session, methane):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    kept = g4(db_session, methane, age_days=2)
    source(db_session, methane, kept, lot=("wb97xd", "def2tzvp"))
    source(db_session, methane, kept, lot=("b3lyp", "6-31g"), role=ThermoCalculationRole.freq)  # a second, other level
    removed = g3(db_session, methane, age_days=1)
    source(db_session, methane, removed, lot=("b3lyp", "6-31g"))

    body = post(client, methane, {"filters": {"level_of_theory_ref": lot.public_ref}}).json()
    assert [c["thermo_ref"] for c in body["candidates"]] == [kept.public_ref]
    assert body["filters"]["applied"] == {"level_of_theory_ref": lot.public_ref}


def test_an_unknown_level_of_theory_ref_keeps_nothing_and_says_how_many_it_removed(client, db_session, methane):
    g4(db_session, methane)
    body = post(client, methane, {"filters": {"level_of_theory_ref": "lot_00000000000000000000000000"}}).json()
    assert body["outcome"] == "no_applicable_candidate" and body["candidates"] == []
    assert body["filters"]["removed"] == 1


def test_a_model_kind_filter_uses_the_stored_form(client, db_session, methane):
    g4(db_session, methane)
    assert post(client, methane, {"filters": {"model_kind": "nasa"}}).json()["candidates"] == []
    kept = post(client, methane, {"filters": {"model_kind": "scalar"}}).json()
    assert len(kept["candidates"]) == 1 and kept["filters"]["removed"] == 0


def test_a_filter_the_search_does_not_offer_is_refused_not_ignored(client, db_session, methane):
    g4(db_session, methane)
    refused = post(client, methane, {"filters": {"coverage_temperature_min_k": 300.0}})
    assert refused.status_code == 422
    assert post(client, methane, {"filters": {"software": "orca", "unheard_of": 1}}).status_code == 422


def test_filters_apply_before_eligibility_so_a_removed_record_is_not_counted_as_an_eligibility_exclusion(
    client, db_session, methane
):
    g4(db_session, methane, status=S.approved, sources=[{"software": "orca"}])
    g3(db_session, methane, status=S.rejected, sources=[{"software": "gaussian"}])
    body = post(client, methane, {"filters": {"software": "orca"}}).json()
    assert body["funnel"]["removed_by_filters"] == 1 and body["funnel"]["removed_by_eligibility"] == 0
    assert entry_of(body, "rejected_review_status")["excluded"] == 0


def test_a_filter_never_ranks_the_two_that_remain_are_incomparable(client, db_session, methane):
    a = g4(db_session, methane, age_days=400)
    b = g3(db_session, methane, age_days=1)
    for t in (a, b):
        source(db_session, methane, t, software="orca")
    body = post(client, methane, {"filters": {"software": "orca"}, "result_mode": "first"}).json()
    assert body["outcome"] == "incomparable_alternatives"
    assert body["selection"]["basis"] == "administrative_first" and body["selection"]["administrative"] is True
    assert "not a claim" in body["selection"]["explanation"] or "not" in body["selection"]["explanation"]
    assert body["fronts"][0] == body["administrative_order"]  # one front: the order is display order only


# -- step 2: eligibility defaults ------------------------------------------------------------------------------


def test_every_default_is_echoed_with_its_name_whether_applied_and_by_whose_choice(client, db_session, methane):
    g4(db_session, methane)
    body = post(client, methane).json()
    assert [e["name"] for e in body["eligibility"]] == DEFAULTS
    assert all(e["applied"] is True and e["source"] == "default" for e in body["eligibility"])
    assert {e["name"]: e["override_field"] for e in body["eligibility"]} == {
        "rejected_review_status": "include_rejected",
        "deprecated_review_status": "include_deprecated",
        "evidence_hard_fail": "include_hard_failed_evidence",
        "confirmed_blocking_findings": "include_confirmed_findings",
    }
    assert body["request"]["eligibility"] == {
        "include_rejected": False, "include_deprecated": False,
        "include_hard_failed_evidence": False, "include_confirmed_findings": False,
    }


def test_include_rejected_admits_exactly_the_rejected_record_and_says_it_passed_only_by_override(
    client, db_session, methane
):
    rejected = g4(db_session, methane, status=S.rejected, age_days=400)
    deprecated = g3(db_session, methane, status=S.deprecated, age_days=300)
    approved = g3(db_session, methane, status=S.approved, age_days=1)

    default = post(client, methane).json()
    assert default["outcome"] == "sole_eligible_candidate" and default["selection"]["thermo_ref"] == approved.public_ref
    assert entry_of(default, "rejected_review_status")["excluded"] == 1
    assert entry_of(default, "deprecated_review_status")["excluded"] == 1
    assert default["funnel"]["removed_by_eligibility"] == 2

    lifted = post(client, methane, {"eligibility": {"include_rejected": True}}).json()
    assert {c["thermo_ref"] for c in lifted["candidates"]} == {rejected.public_ref, approved.public_ref}  # not the deprecated one
    echo = entry_of(lifted, "rejected_review_status")
    assert echo["applied"] is False and echo["source"] == "request" and echo["admitted_by_override"] == 1
    assert entry_of(lifted, "deprecated_review_status")["applied"] is True
    assert candidate(lifted, rejected)["passed_only_by_override"] == ["rejected_review_status"]
    assert candidate(lifted, rejected)["eligible"] is True
    assert candidate(lifted, approved)["passed_only_by_override"] == []
    assert lifted["outcome"] == "incomparable_alternatives"

    both = post(client, methane, {"eligibility": {"include_rejected": True, "include_deprecated": True}}).json()
    assert len(both["candidates"]) == 3
    assert candidate(both, deprecated)["passed_only_by_override"] == ["deprecated_review_status"]


def test_include_hard_failed_evidence_admits_a_record_whose_rubric_hard_fails_and_still_discloses_the_failure(
    client, db_session, methane
):
    failing = g4(db_session, methane, age_days=2)
    source(db_session, methane, failing, rejected=True)
    clean = g3(db_session, methane, age_days=1)

    default = post(client, methane).json()
    assert default["outcome"] == "sole_eligible_candidate" and default["selection"]["thermo_ref"] == clean.public_ref
    assert candidate(default, failing)["eligible"] is False
    assert candidate(default, failing)["blocked_by"] == ["evidence_hard_fail"]
    assert entry_of(default, "evidence_hard_fail")["excluded"] == 1

    lifted = post(client, methane, {"eligibility": {"include_hard_failed_evidence": True}}).json()
    assert lifted["outcome"] == "incomparable_alternatives"
    c = candidate(lifted, failing)
    assert c["eligible"] is True and c["passed_only_by_override"] == ["evidence_hard_fail"] and c["blocked_by"] == []
    assert any(b.startswith("evidence_hard_failed:") for b in c["blocking"])  # the failure is still on the record
    assert entry_of(lifted, "evidence_hard_fail")["admitted_by_override"] == 1
    # lifting the evidence default does not lift the review defaults
    assert entry_of(lifted, "rejected_review_status")["applied"] is True


def test_lifting_one_default_does_not_admit_a_record_that_fails_another(client, db_session, methane):
    both = g4(db_session, methane, status=S.rejected, sources=[{"rejected": True}])  # rejected status AND hard-failed evidence
    only_status_lifted = post(client, methane, {"eligibility": {"include_rejected": True}}).json()
    c = candidate(only_status_lifted, both)
    assert c["eligible"] is False and c["blocked_by"] == ["evidence_hard_fail"]
    assert c["passed_only_by_override"] == ["rejected_review_status"]
    all_lifted = post(
        client, methane, {"eligibility": {"include_rejected": True, "include_hard_failed_evidence": True}}
    ).json()
    c = candidate(all_lifted, both)
    assert c["eligible"] is True and c["passed_only_by_override"] == ["rejected_review_status", "evidence_hard_fail"]


def test_an_override_does_not_make_an_inapplicable_record_applicable(client, db_session, methane):
    wrong_phase = g4(db_session, methane, phase=PhaseKind.liquid, status=S.rejected)
    body = post(client, methane, {"eligibility": {"include_rejected": True}}).json()
    c = candidate(body, wrong_phase)
    assert c["applicability"] == "incompatible" and c["eligible"] is False
    assert body["outcome"] == "no_applicable_candidate"


def test_missing_evidence_and_contradictory_evidence_stay_distinguishable(client, db_session, methane):
    missing = make_thermo(db_session, methane, proto=protocol("g4"), phase=None)  # phase never recorded
    contradicted = g3(db_session, methane)
    source(db_session, methane, contradicted, rejected=True)
    body = post(client, methane, {"eligibility": {"include_hard_failed_evidence": True}}).json()
    m, c = candidate(body, missing), candidate(body, contradicted)
    assert m["applicability"] == "unresolved" and m["eligible"] is False and m["blocked_by"] == []
    assert c["applicability"] == "applicable" and c["blocking"] and c["passed_only_by_override"] == ["evidence_hard_fail"]
    assert missing.public_ref in body["disclosures"]["unresolved_refs"]
    assert contradicted.public_ref not in body["disclosures"]["unresolved_refs"]
    # an override lifts a contradiction the caller accepts; it can never supply a fact that was never recorded
    assert body["outcome"] == "sole_eligible_candidate" and body["selection"]["thermo_ref"] == contradicted.public_ref


def test_unknown_override_names_are_refused(client, db_session, methane):
    g4(db_session, methane)
    assert post(client, methane, {"eligibility": {"include_everything": True}}).status_code == 422
    assert post(client, methane, {"eligibility": {"include_rejected": "yes please"}}).status_code == 422


# -- the read profile's floor is not a default ------------------------------------------------------------------


def test_curated_cannot_be_overridden_by_any_eligibility_flag(client, db_session, methane):
    approved = g3(db_session, methane, status=S.approved, age_days=5)
    rejected = g4(db_session, methane, status=S.rejected, age_days=1)
    deprecated = g4(db_session, methane, status=S.deprecated, age_days=2)
    pending = g4(db_session, methane, status=S.under_review, age_days=3)
    lift_all = dict.fromkeys(("include_rejected", "include_deprecated", "include_hard_failed_evidence", "include_confirmed_findings"), True)
    resp = post(client, methane, {"eligibility": lift_all}, profile="curated")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["thermo_ref"] for c in body["candidates"]] == [approved.public_ref]
    for ref in (rejected.public_ref, deprecated.public_ref, pending.public_ref):
        assert ref not in resp.text
    for name in ("rejected_review_status", "deprecated_review_status"):
        e = entry_of(body, name)
        assert e["applied"] is True and e["source"] == "read_profile"
        assert e["excluded"] == 0 and e["admitted_by_override"] == 0  # hidden == absent: nothing to count
    assert body["funnel"]["considered"] == 1
    # without asking, the same entry is a plain default
    assert entry_of(post(client, methane, profile="curated").json(), "rejected_review_status")["source"] == "default"


def test_a_lifted_exclusion_stays_inside_what_the_profile_shows(client, db_session, methane):
    """Curated shows approved only. A hard-failed APPROVED record is within that, so lifting its exclusion is allowed."""
    approved_failing = g4(db_session, methane, status=S.approved, sources=[{"rejected": True, "calc_status": S.approved}])
    body = post(client, methane, {"eligibility": {"include_hard_failed_evidence": True}}, profile="curated").json()
    assert candidate(body, approved_failing)["passed_only_by_override"] == ["evidence_hard_fail"]
    assert entry_of(body, "evidence_hard_fail")["source"] == "request"


# -- recorded decisions replay -----------------------------------------------------------------------------------


def test_a_decision_made_with_filters_and_overrides_replays_from_its_manifest(client, db_session, methane):
    a = g4(db_session, methane, status=S.rejected, age_days=300, sources=[{"software": "orca"}])
    b = g3(db_session, methane, status=S.approved, age_days=1, sources=[{"software": "orca"}])
    g3(db_session, methane, status=S.approved, age_days=2, sources=[{"software": "gaussian"}])
    request = {"filters": {"software": "orca"}, "eligibility": {"include_rejected": True}}
    response = post(client, methane, request).json()
    manifest = post(client, methane, request, manifest=True).json()

    assert manifest["request"]["filters"] == {"software": "orca"}
    assert manifest["request"]["eligibility"]["include_rejected"] is True
    assert manifest["population"]["funnel"]["removed_by_filters"] == 1
    assert {x["thermo_ref"] for x in manifest["candidates"]} == {a.public_ref, b.public_ref}
    assert replay_matches(manifest)
    assert replay_decision(manifest)["fronts"] == response["fronts"]


def test_replay_refuses_a_manifest_whose_overrides_were_edited_after_the_fact(client, db_session, methane):
    a = g4(db_session, methane, status=S.rejected)
    g3(db_session, methane, status=S.approved)
    manifest = post(client, methane, {"eligibility": {"include_rejected": True}}, manifest=True).json()
    assert replay_matches(manifest)
    tampered = copy.deepcopy(manifest)
    tampered["request"]["eligibility"]["include_rejected"] = False  # claims the default was applied
    with pytest.raises(ReplayError, match="eligibility settings"):
        replay_decision(tampered)
    # a manifest without the settings is refused rather than read as the defaults
    stripped = copy.deepcopy(manifest)
    del stripped["request"]["eligibility"]
    with pytest.raises(ReplayError):
        replay_decision(stripped)
    assert a.public_ref in {c["thermo_ref"] for c in manifest["candidates"]}


def test_the_funnel_and_the_echo_are_the_same_in_the_response_and_the_download(client, db_session, methane):
    g4(db_session, methane, status=S.rejected)
    g3(db_session, methane, status=S.approved)
    body = post(client, methane).json()
    manifest = post(client, methane, manifest=True).json()
    funnel = manifest["population"]["funnel"]
    assert {k: funnel[k] for k in body["funnel"]} == body["funnel"]
    assert funnel["excluded_by_default"]["rejected_review_status"] == entry_of(body, "rejected_review_status")["excluded"] == 1


# -- the browse's full provenance set, matched through ONE source calculation ---------------------------------------

DFT_A = {"lot": ("b3lyp", "def2tzvp"), "software": "gaussian", "software_version": "16", "workflow_tool": "arc", "workflow_tool_version": "1.0"}
WF_B = {"lot": ("ccsd(t)-f12", "cc-pvtz-f12"), "software": "orca", "software_version": "6", "workflow_tool": "rmg", "workflow_tool_version": "2.0"}


def two_calculation_record(session, entry, **kw):
    thermo = g4(session, entry, **kw)
    source(session, entry, thermo, **DFT_A)
    source(session, entry, thermo, **WF_B, role=ThermoCalculationRole.freq)
    return thermo


@pytest.mark.parametrize(
    ("filters", "matches"),
    [
        ({"method": "b3lyp"}, True),
        ({"basis": "cc-pvtz-f12"}, True),
        ({"software_version": "6"}, True),
        ({"workflow_tool": "rmg", "workflow_tool_version": "2.0"}, True),
        ({"method_family": ["dft"]}, True),
        ({"method_family": ["composite"]}, False),
        ({"dft_rung_min": 4, "dft_rung_max": 4}, True),
        ({"dft_rung_min": 5}, False),
        # one calculation has to satisfy every field
        ({"method": "b3lyp", "software": "orca"}, False),
        ({"method": "b3lyp", "software": "gaussian"}, True),
        ({"software": "gaussian", "software_version": "6"}, False),
        ({"workflow_tool": "arc", "basis": "cc-pvtz-f12"}, False),
        ({"method_family": ["dft"], "software": "orca"}, False),
        ({"method_family": ["wavefunction"], "software": "orca"}, True),
        ({"dft_rung_min": 4, "workflow_tool": "rmg"}, False),
        ({"method_family": "wavefunction", "workflow_tool": "rmg"}, True),
    ],
)
def test_the_provenance_filters_are_matched_through_one_source_calculation(client, db_session, methane, filters, matches):
    """Kills: matching two fields through two different source calculations (the previous reading of level and software)."""
    thermo = two_calculation_record(db_session, methane)
    body = post(client, methane, {"filters": filters}).json()
    assert [c["thermo_ref"] for c in body["candidates"]] == ([thermo.public_ref] if matches else [])
    echoed = {k: (sorted([v]) if k == "method_family" and isinstance(v, str) else v) for k, v in filters.items()}
    assert body["filters"]["applied"] == {k: (sorted(v) if isinstance(v, list) else v) for k, v in echoed.items()}
    assert body["filters"]["removed"] == (0 if matches else 1)


def test_a_level_of_theory_ref_and_a_software_have_to_be_one_calculation(client, db_session, methane):
    lot_a = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    thermo = two_calculation_record(db_session, methane)
    ref = lot_a.public_ref
    assert post(client, methane, {"filters": {"level_of_theory_ref": ref, "software": "orca"}}).json()["candidates"] == []
    kept = post(client, methane, {"filters": {"level_of_theory_ref": ref, "software": "gaussian"}}).json()
    assert [c["thermo_ref"] for c in kept["candidates"]] == [thermo.public_ref]


def test_a_family_filter_keeps_a_record_whose_only_calculation_is_unclassified_for_unclassified_alone(client, db_session, methane):
    thermo = g4(db_session, methane)
    source(db_session, methane, thermo, lot=("xyg3", "def2tzvp"), software="orca")
    assert [c["thermo_ref"] for c in post(client, methane, {"filters": {"method_family": ["unclassified"]}}).json()["candidates"]] == [thermo.public_ref]
    assert post(client, methane, {"filters": {"method_family": ["dft"]}}).json()["candidates"] == []
    assert post(client, methane, {"filters": {"dft_rung_min": 1}}).json()["candidates"] == []


def test_the_scientific_origin_filter_is_matched_on_the_record(client, db_session, methane):
    from app.db.models.common import ScientificOriginKind

    computed = g4(db_session, methane, age_days=2)
    experimental = g3(db_session, methane, age_days=1, origin=ScientificOriginKind.experimental)
    body = post(client, methane, {"filters": {"scientific_origin": "experimental"}}).json()
    assert [c["thermo_ref"] for c in body["candidates"]] == [experimental.public_ref]
    assert body["filters"]["applied"] == {"scientific_origin": "experimental"} and body["filters"]["removed"] == 1
    assert [c["thermo_ref"] for c in post(client, methane, {"filters": {"scientific_origin": "computed"}}).json()["candidates"]] == [computed.public_ref]
    assert post(client, methane, {"filters": {"scientific_origin": "estimated"}}).json()["candidates"] == []
    assert post(client, methane, {"filters": {"scientific_origin": "guessed"}}).status_code == 422


@pytest.mark.parametrize("filters", [{"method_family": ["dft"]}, {"method": "b3lyp"}, {"dft_rung_min": 4}, {"software": "gaussian"}])
def test_a_hidden_calculation_cannot_make_a_candidate_pass_a_filter_under_curated(client, db_session, methane, filters):
    """Kills: select matching a filter through a calculation the curated profile hides."""
    thermo = g4(db_session, methane)
    source(db_session, methane, thermo, **DFT_A, calc_status=S.rejected)  # rejected: hidden under curated
    source(db_session, methane, thermo, **WF_B, role=ThermoCalculationRole.freq, calc_status=S.approved)
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id, status=S.approved)
    assert [c["thermo_ref"] for c in post(client, methane, {"filters": filters}).json()["candidates"]] == [thermo.public_ref]
    assert post(client, methane, {"filters": filters}, profile="curated").json()["candidates"] == []
    assert [c["thermo_ref"] for c in post(client, methane, {"filters": {"software": "orca"}}, profile="curated").json()["candidates"]] == [thermo.public_ref]


def test_a_decision_records_its_provenance_filters_and_an_earlier_manifest_format_is_refused(client, db_session, methane):
    two_calculation_record(db_session, methane)
    request = {"filters": {"method_family": ["dft"], "software": "gaussian", "scientific_origin": "computed"}}
    manifest = post(client, methane, request, manifest=True).json()
    assert manifest["request"]["filters"] == {"method_family": ["dft"], "software": "gaussian", "scientific_origin": "computed"}
    assert manifest["format"] == {"name": "thermo_selection_manifest", "version": 4}
    assert replay_matches(manifest)
    earlier = copy.deepcopy(manifest)
    earlier["format"]["version"] = 3  # level and software were matched through any, independently
    with pytest.raises(ReplayError, match="earlier format, not replayable"):
        replay_decision(earlier)
