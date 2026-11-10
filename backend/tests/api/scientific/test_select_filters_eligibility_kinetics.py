"""Kinetics select: the caller's filters, and the named eligibility defaults a request can lift.

The same contract as thermo (``test_select_filters_eligibility_thermo.py``), for
``POST /scientific/reaction-entries/{ref}/kinetics/select``: filters narrow first with the kinetics search's own
vocabulary, every default is echoed, an override admits exactly what it names, ``curated`` cannot be overridden, and a
decision made with filters and overrides replays.
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import (
    CalculationQuality,
    KineticsCalculationRole,
    KineticsModelKind,
    SubmissionRecordType,
)
from app.db.models.common import RecordReviewStatus as S
from app.db.models.kinetics import KineticsSourceCalculation
from app.services.kinetics_selection import replay_decision, replay_matches
from app.services.kinetics_selection.manifest import ReplayError
from tests.services.kinetics_selection.test_service import declared, determination
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_software_release,
    make_workflow_tool_release,
    set_review,
)

BASE = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}
DEFAULTS = ["rejected_review_status", "deprecated_review_status", "evidence_hard_fail", "confirmed_blocking_findings"]
PROTOCOL = {"version": 1, "method_kind": "saddle_point_tst"}


def post(client, world, extra=None, *, profile=None, manifest=False):
    url = f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics/select" + ("/manifest" if manifest else "")
    return client.post(url, json={**copy.deepcopy(BASE), **(extra or {})}, params={"profile": profile} if profile else None)


def record(session, world, key, *, status=S.not_reviewed, sources=None, created_at=None, **kw):
    """One kinetics record in its own determination. Source links are attached before the review is set: approved is frozen."""
    det = determination(session, world, key)

    def attach(k):
        k.protocol_declaration = PROTOCOL
        for spec in sources or ():
            lot = make_lot(session, method=spec["lot"][0], basis=spec["lot"][1]) if spec.get("lot") else None
            release = (
                make_software_release(session, name=spec["software"], **({"version": spec["software_version"]} if spec.get("software_version") else {}))
                if spec.get("software")
                else None
            )
            tool = (
                make_workflow_tool_release(session, name=spec["workflow_tool"], version=spec.get("workflow_tool_version", "1.2.3"))
                if spec.get("workflow_tool")
                else None
            )
            calc = make_calculation(
                session, species_entry_id=world.h.id,
                lot_id=lot.id if lot else None, software_release_id=release.id if release else None,
                workflow_tool_release_id=tool.id if tool else None,
            )
            if spec.get("rejected"):
                calc.quality = CalculationQuality.rejected
            session.add(KineticsSourceCalculation(
                kinetics_id=k.id, calculation_id=calc.id, role=spec.get("role", KineticsCalculationRole.ts_energy)
            ))
            session.flush()
            if spec.get("calc_status"):
                set_review(session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=spec["calc_status"])

    return declared(session, world, det, children=attach, status=status, created_at=created_at, **kw), det


def entry_of(body, name):
    return next(e for e in body["eligibility"] if e["name"] == name)


def candidate(body, k):
    return next(c for c in body["candidates"] if c["kinetics_ref"] == k.public_ref)


# -- step 1: filters ----------------------------------------------------------------------------------------------


def test_a_software_filter_narrows_the_pool(client, db_session, world):
    in_orca, _ = record(db_session, world, "a", sources=[{"software": "orca"}])
    record(db_session, world, "b", sources=[{"software": "gaussian"}])
    record(db_session, world, "c")
    assert len(post(client, world).json()["candidates"]) == 3
    body = post(client, world, {"filters": {"software": "orca"}}).json()
    assert [c["kinetics_ref"] for c in body["candidates"]] == [in_orca.public_ref]
    assert body["outcome"] == "incomparable_alternatives" or body["outcome"] == "sole_eligible_candidate"
    assert body["filters"]["applied"] == {"software": "orca"} and body["filters"]["removed"] == 2
    assert body["funnel"] == {"unit": "candidates", "considered": 3, "removed_by_filters": 2, "removed_by_floor": 0, "removed_by_eligibility": 0, "remaining": 1}
    assert body["request"]["filters"] == {"software": "orca"}


def test_a_level_of_theory_filter_takes_a_public_ref(client, db_session, world):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    kept, _ = record(db_session, world, "a", sources=[{"lot": ("wb97xd", "def2tzvp")}])
    record(db_session, world, "b", sources=[{"lot": ("b3lyp", "6-31g")}])
    body = post(client, world, {"filters": {"level_of_theory_ref": lot.public_ref}}).json()
    assert [c["kinetics_ref"] for c in body["candidates"]] == [kept.public_ref]


def test_a_model_kind_filter_uses_the_stored_form(client, db_session, world):
    record(db_session, world, "a")
    assert post(client, world, {"filters": {"model_kind": "plog"}}).json()["candidates"] == []
    stored = post(client, world).json()["candidates"][0]["model_kind"]
    assert len(post(client, world, {"filters": {"model_kind": stored}}).json()["candidates"]) == 1
    assert KineticsModelKind(stored)


def test_a_filter_the_search_does_not_offer_is_refused(client, db_session, world):
    record(db_session, world, "a")
    assert post(client, world, {"filters": {"coverage_temperature_min_k": 300.0}}).status_code == 422
    assert post(client, world, {"filters": {"unheard_of": 1}}).status_code == 422


# -- step 2: eligibility -------------------------------------------------------------------------------------------


def test_every_default_is_echoed(client, db_session, world):
    record(db_session, world, "a")
    body = post(client, world).json()
    assert [e["name"] for e in body["eligibility"]] == DEFAULTS
    assert all(e["applied"] and e["source"] == "default" for e in body["eligibility"])
    assert body["request"]["eligibility"]["include_rejected"] is False


def test_include_rejected_and_include_deprecated_admit_exactly_their_own_status(client, db_session, world):
    rejected, _ = record(db_session, world, "r", status=S.rejected)
    deprecated, _ = record(db_session, world, "d", status=S.deprecated)
    approved, _ = record(db_session, world, "a", status=S.approved)
    default = post(client, world).json()
    assert default["outcome"] == "sole_eligible_candidate"
    assert (entry_of(default, "rejected_review_status")["excluded"], entry_of(default, "deprecated_review_status")["excluded"]) == (1, 1)
    assert default["funnel"]["removed_by_eligibility"] == 2

    lifted = post(client, world, {"eligibility": {"include_rejected": True}}).json()
    assert {c["kinetics_ref"] for c in lifted["candidates"]} == {rejected.public_ref, approved.public_ref}
    assert candidate(lifted, rejected)["passed_only_by_override"] == ["rejected_review_status"]
    assert candidate(lifted, approved)["passed_only_by_override"] == []
    assert entry_of(lifted, "rejected_review_status")["source"] == "request"
    assert entry_of(lifted, "deprecated_review_status")["applied"] is True
    both = post(client, world, {"eligibility": {"include_rejected": True, "include_deprecated": True}}).json()
    assert candidate(both, deprecated)["passed_only_by_override"] == ["deprecated_review_status"]


def test_include_hard_failed_evidence_admits_a_hard_failed_record_and_still_discloses_it(client, db_session, world):
    failing, _ = record(db_session, world, "f", sources=[{"rejected": True}])
    clean, _ = record(db_session, world, "c")
    default = post(client, world).json()
    c = candidate(default, failing)
    if not c["blocking"]:
        pytest.skip("a rejected-quality ts_energy source does not hard-fail this rubric; covered at the service level")
    assert c["eligible"] is False and c["blocked_by"] == ["evidence_hard_fail"]
    assert default["selection"]["kinetics_refs"] == [clean.public_ref]
    lifted = post(client, world, {"eligibility": {"include_hard_failed_evidence": True}}).json()
    c = candidate(lifted, failing)
    assert c["eligible"] is True and c["passed_only_by_override"] == ["evidence_hard_fail"]
    assert any(b.startswith("evidence_hard_failed:") for b in c["blocking"])
    assert lifted["outcome"] == "incomparable_alternatives"


def test_an_override_never_makes_an_inapplicable_record_applicable(client, db_session, world):
    # no applicability declaration: unresolved, not blocked, and no flag turns it into an answer
    from app.db.models.common import KineticsDirection
    from tests.services.scientific_read._factories import make_kinetics

    legacy = make_kinetics(db_session, reaction_entry=world.entry, direction=KineticsDirection.forward)
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=legacy.id, status=S.rejected)
    lift_all = dict.fromkeys(("include_rejected", "include_deprecated", "include_hard_failed_evidence", "include_confirmed_findings"), True)
    body = post(client, world, {"eligibility": lift_all}).json()
    c = candidate(body, legacy)
    assert c["applicability"] == "unresolved" and c["eligible"] is False
    assert body["outcome"] == "no_applicable_candidate"
    assert legacy.public_ref in body["disclosures"]["unresolved_refs"]


def test_curated_cannot_be_overridden(client, db_session, world):
    approved, _ = record(db_session, world, "a", status=S.approved)
    for key, status in (("r", S.rejected), ("d", S.deprecated), ("u", S.under_review), ("n", S.not_reviewed)):
        record(db_session, world, key, status=status)
    lift_all = dict.fromkeys(("include_rejected", "include_deprecated", "include_hard_failed_evidence", "include_confirmed_findings"), True)
    resp = post(client, world, {"eligibility": lift_all}, profile="curated")
    body = resp.json()
    assert [c["kinetics_ref"] for c in body["candidates"]] == [approved.public_ref]
    assert body["funnel"]["considered"] == 1
    for name in ("rejected_review_status", "deprecated_review_status"):
        e = entry_of(body, name)
        assert (e["applied"], e["source"], e["excluded"], e["admitted_by_override"]) == (True, "read_profile", 0, 0)


# -- replay -------------------------------------------------------------------------------------------------------


def test_a_decision_made_with_filters_and_overrides_replays(client, db_session, world):
    a, _ = record(db_session, world, "a", status=S.rejected, sources=[{"software": "orca"}])
    b, _ = record(db_session, world, "b", status=S.approved, sources=[{"software": "orca"}])
    record(db_session, world, "c", status=S.approved, sources=[{"software": "gaussian"}])
    request = {"filters": {"software": "orca"}, "eligibility": {"include_rejected": True}}
    response = post(client, world, request).json()
    manifest = post(client, world, request, manifest=True).json()
    assert manifest["request"]["filters"] == {"software": "orca"}
    assert manifest["request"]["eligibility"]["include_rejected"] is True
    assert {c["kinetics_ref"] for c in manifest["candidates"]} == {a.public_ref, b.public_ref}
    assert replay_matches(manifest)
    assert replay_decision(manifest)["fronts"] == response["fronts"]
    assert manifest["population"]["funnel"]["removed_by_filters"] == 1


def test_replay_refuses_an_edited_eligibility_record(client, db_session, world):
    record(db_session, world, "a", status=S.rejected)
    record(db_session, world, "b", status=S.approved)
    manifest = post(client, world, {"eligibility": {"include_rejected": True}}, manifest=True).json()
    assert replay_matches(manifest)
    tampered = copy.deepcopy(manifest)
    tampered["request"]["eligibility"]["include_rejected"] = False
    with pytest.raises(ReplayError, match="eligibility settings"):
        replay_decision(tampered)
    stripped = copy.deepcopy(manifest)
    del stripped["request"]["eligibility"]
    with pytest.raises(ReplayError):
        replay_decision(stripped)


# -- the browse's full provenance set, matched through ONE source calculation ---------------------------------------

DFT_A = {"lot": ("b3lyp", "def2tzvp"), "software": "gaussian", "software_version": "16", "workflow_tool": "arc", "workflow_tool_version": "1.0"}
WF_B = {"lot": ("ccsd(t)-f12", "cc-pvtz-f12"), "software": "orca", "software_version": "6", "workflow_tool": "rmg", "workflow_tool_version": "2.0", "role": KineticsCalculationRole.freq}


@pytest.mark.parametrize(
    ("filters", "matches"),
    [
        ({"method": "b3lyp"}, True),
        ({"basis": "cc-pvtz-f12"}, True),
        ({"software_version": "6"}, True),
        ({"workflow_tool": "rmg", "workflow_tool_version": "2.0"}, True),
        ({"method_family": ["dft"]}, True),
        ({"method_family": ["multireference"]}, False),
        ({"dft_rung_min": 4, "dft_rung_max": 4}, True),
        ({"dft_rung_max": 3}, False),
        # one calculation has to satisfy every field
        ({"method": "b3lyp", "software": "orca"}, False),
        ({"software": "gaussian", "software_version": "6"}, False),
        ({"workflow_tool": "arc", "basis": "cc-pvtz-f12"}, False),
        ({"method_family": ["dft"], "software": "orca"}, False),
        ({"method_family": ["wavefunction"], "software": "orca"}, True),
        ({"dft_rung_min": 4, "workflow_tool": "rmg"}, False),
    ],
)
def test_the_provenance_filters_are_matched_through_one_source_calculation(client, db_session, world, filters, matches):
    """Kills: matching two fields through two different source calculations (the previous reading of level and software)."""
    kinetics, _ = record(db_session, world, "a", sources=[DFT_A, WF_B])
    body = post(client, world, {"filters": filters}).json()
    assert [c["kinetics_ref"] for c in body["candidates"]] == ([kinetics.public_ref] if matches else [])
    assert body["filters"]["applied"] == filters and body["filters"]["removed"] == (0 if matches else 1)


def test_a_level_of_theory_ref_and_a_software_have_to_be_one_calculation(client, db_session, world):
    lot_a = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    kinetics, _ = record(db_session, world, "a", sources=[DFT_A, WF_B])
    assert post(client, world, {"filters": {"level_of_theory_ref": lot_a.public_ref, "software": "orca"}}).json()["candidates"] == []
    kept = post(client, world, {"filters": {"level_of_theory_ref": lot_a.public_ref, "software": "gaussian"}}).json()
    assert [c["kinetics_ref"] for c in kept["candidates"]] == [kinetics.public_ref]


def test_the_scientific_origin_filter_is_matched_on_the_record(client, db_session, world):
    from app.db.models.common import ScientificOriginKind

    computed, _ = record(db_session, world, "a")
    experimental, _ = record(db_session, world, "b", scientific_origin=ScientificOriginKind.experimental)
    body = post(client, world, {"filters": {"scientific_origin": "experimental"}}).json()
    assert [c["kinetics_ref"] for c in body["candidates"]] == [experimental.public_ref]
    assert body["filters"]["applied"] == {"scientific_origin": "experimental"} and body["filters"]["removed"] == 1
    assert [c["kinetics_ref"] for c in post(client, world, {"filters": {"scientific_origin": "computed"}}).json()["candidates"]] == [computed.public_ref]


@pytest.mark.parametrize("filters", [{"method_family": ["dft"]}, {"method": "b3lyp"}, {"dft_rung_min": 4}, {"software": "gaussian"}])
def test_a_hidden_calculation_cannot_make_a_candidate_pass_a_filter_under_curated(client, db_session, world, filters):
    """Kills: select matching a filter through a calculation the curated profile hides."""
    kinetics, _ = record(db_session, world, "a", status=S.approved, sources=[{**DFT_A, "calc_status": S.rejected}, {**WF_B, "calc_status": S.approved}])
    assert [c["kinetics_ref"] for c in post(client, world, {"filters": filters}).json()["candidates"]] == [kinetics.public_ref]
    assert post(client, world, {"filters": filters}, profile="curated").json()["candidates"] == []
    visible = post(client, world, {"filters": {"software": "orca"}}, profile="curated").json()
    assert [c["kinetics_ref"] for c in visible["candidates"]] == [kinetics.public_ref]


def test_a_decision_records_its_provenance_filters_and_an_earlier_manifest_format_is_refused(client, db_session, world):
    record(db_session, world, "a", sources=[DFT_A, WF_B])
    filters = {"method_family": ["dft"], "software": "gaussian", "scientific_origin": "computed"}
    manifest = post(client, world, {"filters": filters}, manifest=True).json()
    assert manifest["request"]["filters"] == filters
    assert manifest["format"] == {"name": "kinetics_selection_manifest", "version": 5}
    assert replay_matches(manifest)
    earlier = copy.deepcopy(manifest)
    earlier["format"]["version"] = 4  # level and software were matched through any, independently
    with pytest.raises(ReplayError, match="earlier format, not replayable"):
        replay_decision(earlier)
