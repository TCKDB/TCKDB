"""The replayable manifest and its three explicit replay operations, including the tampering a checksum cannot catch.

A digest detects an accidental edit and nothing more: anyone who edits a manifest can recompute it. So the fixtures
below recompute every digest after each edit ("coordinated" tampering) and the question is whether replay still refuses,
and which operation, on the strength of the captured *inputs* rather than the recorded flags.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus
from app.services.selection_kernel.requested import funnel
from app.services.statmech_selection import manifest as manifest_module
from app.services.statmech_selection.assessment import summarize
from app.services.statmech_selection.decision import decide_statmech
from app.services.statmech_selection.manifest import (
    ReplayError,
    build_manifest,
    canonical,
    digest,
    reassess_statmech_captured_inputs,
    replay_matches,
    replay_statmech_decision,
    reproduce_statmech_ordering,
)
from app.services.statmech_selection.models import SelectionBounds
from app.services.statmech_selection.service import StatmechQualification, verdict_of
from tests.services.statmech_selection._population import _PREFERRED, candidate, population, ref
from tests.services.statmech_selection._support import EFFECTIVE, approved_calcs, assess, req, subject

EXPLORATORY = {"profile": "exploratory", "review_floor": None}
CURATED = {"profile": "curated", "review_floor": "approved"}


def make_manifest(candidates=None, profile=EXPLORATORY, request=None, statuses=EFFECTIVE, **extra) -> dict[str, Any]:
    candidates = list(candidates if candidates is not None else population("a", "b"))
    request = request or req(_PREFERRED)
    calcs = approved_calcs()
    assessments = tuple(assess(c, request, calcs, effective=statuses) for c in candidates)
    decision = decide_statmech(request=request, candidates=candidates, assessments=assessments)
    verdicts = {a.candidate_ref: verdict_of(a, c, request) for a, c in zip(assessments, candidates, strict=True)}
    qualification = StatmechQualification(
        request=request,
        subject=subject(),
        effective_statuses=tuple(sorted(statuses, key=lambda s: s.value)),
        total_candidates=len(candidates) + extra.get("hidden", 0),
        visible_candidates=len(candidates),
        excluded_by_review=extra.get("excluded", ()),
        excluded_count=len(extra.get("excluded", ())),
        nested_rows=0,
        snapshot_isolation="repeatable read",
        candidates=tuple(candidates),
        calculations=tuple(calcs.values()),
        assessments=assessments,
        summary=summarize(request, assessments),
        candidate_statuses=tuple(sorted(statuses, key=lambda s: s.value)),
        verdicts=verdicts,
        funnel=funnel(
            considered=len(candidates), removed_by_filters=0, terminal_excluded={}, verdicts=list(verdicts.values())
        ),
    )
    return build_manifest(qualification, decision, profile=dict(profile))


def retamper(manifest: dict[str, Any], edit) -> dict[str, Any]:
    """Apply ``edit`` to a copy of the manifest body and recompute EVERY digest, as a careful forger would."""
    body = copy.deepcopy({k: v for k, v in manifest.items() if k != "integrity"})
    edit(body)
    for section in ("candidates", "calculations"):
        body[section] = [{**{k: v for k, v in item.items() if k != "content_sha256"}, "content_sha256": None} for item in body[section]]
        for item in body[section]:
            inner = {k: v for k, v in item.items() if k != "content_sha256"}
            item["content_sha256"] = digest(inner)
    return {**body, "integrity": {**manifest["integrity"], "manifest_sha256": digest(body)}}


def refused(code: str, fn, *args, **kwargs) -> None:
    with pytest.raises(ReplayError) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code, caught.value


def candidate_entry(body, letter):
    return next(c for c in body["candidates"] if c["candidate_ref"] == ref(letter))


def assessment_entry(body, letter):
    return next(a for a in body["assessments"] if a["candidate_ref"] == ref(letter))


# --- the manifest itself ---------------------------------------------------------------------------------------------------


def test_a_decision_replays_from_its_manifest_alone_at_both_levels():
    manifest = make_manifest()
    assert manifest["outcome"] == "incomparable_alternatives" and manifest["decision"]["selection"]["administrative_ref"] is None
    ordering = reproduce_statmech_ordering(manifest)
    assert ordering["decision"] == manifest["decision"]
    assert ordering["label"]["reassessed"] is False and ordering["label"]["kind"] == "current"
    assert "not an endorsement" in ordering["label"]["statement"]
    full = replay_statmech_decision(manifest)
    assert full["decision"] == manifest["decision"] and full["label"]["reassessed"] is True and full["label"]["kind"] == "current"
    assert replay_matches(manifest)
    assert [a.candidate_ref for a in reassess_statmech_captured_inputs(manifest)] == [ref("a"), ref("b")]


def test_the_manifest_holds_an_ordering_block_and_no_rule_edge_or_conflict_field():
    manifest = make_manifest()
    assert set(manifest["decision"]["ordering"]) == {"fronts", "administrative_order", "admin_key"}
    assert "protocol" not in manifest["decision"] and manifest["format"] == {"name": "statmech_selection_manifest", "version": 3}
    assert replay_matches(manifest)


def test_the_manifest_names_what_it_was_made_under_and_leaks_no_database_id():
    manifest = make_manifest()
    assert manifest["versions"] == {
        "contract": 1, "assessment": 2, "normalizer": 1, "decision": 1,
        "administrative_key": 2, "finding_semantics": [1],
    }
    assert manifest["assessment_semantics"]["name"] == "statmech_selection_assessment"
    assert manifest["assessment_semantics"]["version"] == 2
    assert manifest["assessment_semantics"]["source_findings"] == {"name": "structure_source_findings", "version": 1}
    assert manifest["assessment_semantics"]["evidence_rubric"] == {"name": "statmech_qualification", "version": 2}

    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    assert not [k for k in keys(manifest) if k == "id" or k.endswith("_id")]


def test_a_manifest_over_its_bound_is_refused_whole():
    request = replace(req(_PREFERRED), bounds=SelectionBounds(manifest_bytes=200))
    with pytest.raises(CodedValueError) as caught:
        make_manifest(request=request)
    assert caught.value.code == "statmech_selection_bound_exceeded" and caught.value.context["bound"] == "manifest_bytes"
    assert make_manifest(request=replace(req(_PREFERRED), bounds=SelectionBounds(manifest_bytes=10_000_000)))


def test_a_curated_profile_redacts_what_it_could_not_have_reached_before_the_checksum():
    approved_only = frozenset({RecordReviewStatus.approved})
    leaked = ({"candidate_ref": "sm_" + "z" * 26, "review_status": "not_reviewed", "reason": "below_review_floor"},)
    manifest = make_manifest(profile=CURATED, statuses=approved_only, hidden=1, excluded=leaked)
    population_block = manifest["population"]
    assert population_block["excluded_by_review"] == [] and population_block["excluded_count"] == 0
    assert population_block["total_candidates"] == population_block["visible_candidates"] and population_block["excluded_by_review_withheld"]
    assert "z" * 26 not in canonical(manifest)
    assert replay_matches(manifest)  # the checksum was computed after the redaction


# --- checksums ---------------------------------------------------------------------------------------------------------------


def test_an_uncoordinated_edit_fails_a_checksum():
    manifest = make_manifest()
    edited = copy.deepcopy(manifest)
    edited["request"]["observable"] = "heat_capacity"
    refused("manifest_checksum_mismatch", reproduce_statmech_ordering, edited)
    refused("manifest_checksum_mismatch", replay_statmech_decision, edited)
    inner = retamper(manifest, lambda b: None)
    inner["candidates"][0]["owner_ref"] = "spe_" + "q" * 26
    inner["integrity"]["manifest_sha256"] = digest({k: v for k, v in inner.items() if k != "integrity"})
    refused("input_checksum_mismatch", reproduce_statmech_ordering, inner)


# --- tampering a checksum cannot catch ---------------------------------------------------------------------------------------


def test_a_coordinated_edit_of_a_captured_fact_is_caught_by_reassessment_not_by_the_checksums():
    manifest = make_manifest()

    def wrong_scale_purpose(body):
        sources = candidate_entry(body, "a")["declaration"]["sources"]
        next(s for s in sources if s["locator"] == "scale_factor")["scale_factor"]["kind"] = "zpe"

    tampered = retamper(manifest, wrong_scale_purpose)
    assert reproduce_statmech_ordering(tampered)  # it never reassesses: the checksums are consistent and the label says so
    refused("assessment_mismatch", reassess_statmech_captured_inputs, tampered)
    refused("assessment_mismatch", replay_statmech_decision, tampered)
    refused("assessment_mismatch", replay_matches, tampered)


def test_a_coordinated_edit_of_eligibility_is_caught_by_reassessment_even_when_every_flag_agrees():
    manifest = make_manifest([*population("a", "b"), candidate("c", rank=3, recipe={"scale_application": "unknown"})],
                             request=req({**_PREFERRED, "coverage_requirement": "eligible_population"}))
    assert manifest["decision"]["summary"]["undecided_refs"] == [ref("c")]

    def make_c_eligible(body):
        a = assessment_entry(body, "c")
        a.update(applicability="applicable", eligible=True, reasons=[])

    tampered = retamper(manifest, make_c_eligible)
    # reproducing an ordering never reassesses, but it still refuses a decision that is not what the recorded assessments give
    refused("decision_mismatch", reproduce_statmech_ordering, tampered)
    refused("assessment_mismatch", replay_statmech_decision, tampered)


def test_a_recorded_assessment_that_contradicts_itself_is_refused_by_every_operation():
    manifest = make_manifest([*population("a", "b"), candidate("c", rank=3, recipe={"scale_application": "unknown"})])
    for edit in (
        lambda b: assessment_entry(b, "c").update(eligible=True),  # flag disagrees with applicability
        lambda b: assessment_entry(b, "c").update(applicability="applicable"),  # applicability disagrees with its reasons
        lambda b: assessment_entry(b, "a").update(reasons=[{"code": "scope_incompatible", "applicability": "incompatible", "category": "known_incompatible"}]),
        lambda b: assessment_entry(b, "a")["reasons"].append({"code": "made_up_reason", "applicability": "unresolved", "category": "missing_required_data"}),
        lambda b: assessment_entry(b, "a").update(advisory=["made_up_advisory"]),
        # a registered reason with the right applicability and a category the registry does not give it
        lambda b: assessment_entry(b, "c")["reasons"][0].update(category="unsupported_model"),
        # a registered reason recorded with a verdict the registry does not give it, made self-consistent
        lambda b: assessment_entry(b, "c").update(
            reasons=[{"code": "scope_incompatible", "applicability": "unresolved", "category": "missing_required_data"}],
            applicability="unresolved",
        ),
    ):
        tampered = retamper(manifest, edit)
        refused("assessment_mismatch", reproduce_statmech_ordering, tampered)
        refused("assessment_mismatch", replay_statmech_decision, tampered)


def test_the_assessments_must_cover_exactly_the_captured_candidates_once():
    manifest = make_manifest()
    refused("assessment_mismatch", reproduce_statmech_ordering, retamper(manifest, lambda b: b["assessments"].pop()))
    refused(
        "assessment_mismatch", reproduce_statmech_ordering,
        retamper(manifest, lambda b: b["assessments"].append(copy.deepcopy(b["assessments"][0]))),
    )
    refused("assessment_mismatch", replay_statmech_decision, retamper(manifest, lambda b: b["assessments"].append(copy.deepcopy(b["assessments"][0]))))
    refused("assessment_mismatch", replay_statmech_decision, retamper(manifest, lambda b: b["assessments"][0].update(candidate_ref=ref("z"))))


def test_an_edited_outcome_or_decision_does_not_match_a_replay():
    manifest = make_manifest()
    refused("inconsistent_manifest", replay_statmech_decision, retamper(manifest, lambda b: b.update(outcome="sole_eligible_candidate")))
    swapped = retamper(manifest, lambda b: b["decision"]["ordering"]["fronts"][0].reverse())
    assert replay_matches(manifest) and not replay_matches(swapped)
    relabelled = retamper(manifest, lambda b: (b["decision"].update(outcome="sole_eligible_candidate"), b.update(outcome="sole_eligible_candidate")))
    assert not replay_matches(relabelled)


def test_replay_refuses_a_recorded_decision_it_does_not_reproduce():
    manifest = make_manifest()
    assert manifest["outcome"] == "incomparable_alternatives"

    def claim_a_sole_model(body):
        body["decision"]["outcome"] = "sole_eligible_candidate"
        body["outcome"] = "sole_eligible_candidate"
        body["decision"]["selection"]["sole_eligible_ref"] = ref("a")

    tampered = retamper(manifest, claim_a_sole_model)  # every checksum recomputed, outcome and decision agree
    refused("decision_mismatch", replay_statmech_decision, tampered)
    refused("decision_mismatch", reproduce_statmech_ordering, tampered)
    assert replay_matches(tampered) is False and replay_matches(manifest) is True
    # a tampered front is caught the same way
    no_front = retamper(manifest, lambda b: b["decision"]["ordering"].update(fronts=[]))
    refused("decision_mismatch", replay_statmech_decision, no_front)


def test_two_captured_candidates_cannot_share_a_reference_or_an_ordinal():
    manifest = make_manifest()
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b["candidates"][1].update(id_rank=b["candidates"][0]["id_rank"])))


# --- versions and labels -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["contract", "decision", "administrative_key"])
def test_a_manifest_made_under_a_version_this_release_does_not_carry_is_refused(name):
    tampered = retamper(make_manifest(), lambda b: b["versions"].update({name: 0}))
    refused("unsupported_semantic_version", reproduce_statmech_ordering, tampered)
    refused("unsupported_semantic_version", replay_statmech_decision, tampered)


def test_assessment_semantics_are_never_substituted_with_the_current_ones():
    manifest = make_manifest()
    refused("unsupported_assessment_semantics", reproduce_statmech_ordering, retamper(manifest, lambda b: b["assessment_semantics"].update(version=0)))
    refused("unsupported_assessment_semantics", reproduce_statmech_ordering, retamper(manifest, lambda b: b.pop("assessment_semantics")))
    refused("unsupported_assessment_semantics", reproduce_statmech_ordering, retamper(manifest, lambda b: b.update(assessment_semantics="x")))
    refused("unsupported_manifest_format", reproduce_statmech_ordering, retamper(manifest, lambda b: b["format"].update(version=99)))
    # The shape before the rules were removed (1) is refused like any format this release does not carry.
    refused("unsupported_manifest_format", reproduce_statmech_ordering, retamper(manifest, lambda b: b["format"].update(version=1)))
    refused("unsupported_manifest_format", replay_statmech_decision, retamper(manifest, lambda b: b["format"].update(version=1)))


@pytest.mark.parametrize("name", ["contract", "decision", "administrative_key"])
@pytest.mark.parametrize("bad", [True, 1.0])
def test_a_bool_or_float_version_is_not_the_integer_it_compares_equal_to(name, bad):
    """Kills: ``versions.get(name) != running[name]`` letting ``true`` pass for version 1."""
    manifest = make_manifest()
    tampered = retamper(manifest, lambda b: b["versions"].update({name: bad}))
    refused("unsupported_semantic_version", replay_statmech_decision, tampered)
    refused("unsupported_semantic_version", reproduce_statmech_ordering, tampered)


@pytest.mark.parametrize("name", ["assessment", "normalizer", "finding_semantics"])
def test_reassessment_needs_the_running_assessment_normalizer_and_finding_versions(name):
    manifest = make_manifest()
    tampered = retamper(manifest, lambda b: b["versions"].update({name: 0}))
    refused("unsupported_semantic_version", reassess_statmech_captured_inputs, tampered)
    assert reproduce_statmech_ordering(tampered)  # reproducing a recorded ordering does not run today's assessor


def test_an_older_supported_manifest_is_labelled_historical_and_never_reassessed(monkeypatch):
    manifest = make_manifest()
    from tckdb_schemas.fragments.version import VersionRef

    from app.versioning import VersionRegistry

    # A release that moved to semantics 3 and still reads 2 (for a published release that cites it): 2 is registered, not current.
    both = VersionRegistry("test_semantics")
    name = manifest_module.ASSESSMENT_SEMANTICS_NAME
    both.register(name, 2, VersionRef(name=name, version=2), current=False)
    both.register(name, 3, VersionRef(name=name, version=3), current=True)
    monkeypatch.setattr(manifest_module, "ASSESSMENT_VERSION", 3)
    monkeypatch.setattr(manifest_module, "ASSESSMENT_SEMANTICS", both)
    label = reproduce_statmech_ordering(manifest)["label"]
    assert label["kind"] == "historical" and label["recorded_assessment_semantics"] == 2 and label["reassessed"] is False
    assert "made under 2" in label["statement"] and "assessed under 3" in label["statement"]
    refused("historical_manifest_cannot_be_reassessed", reassess_statmech_captured_inputs, manifest)
    refused("historical_manifest_cannot_be_reassessed", replay_statmech_decision, manifest)


def test_the_writer_stamps_the_registered_format_and_every_reader_refuses_a_float_bool_or_missing_one():
    from app.services.statmech_selection.manifest import MANIFEST_FORMAT_NAME, MANIFEST_FORMATS
    from tests.services._format_cases import bad_format_manifests

    manifest = make_manifest()
    current = MANIFEST_FORMATS.current(MANIFEST_FORMAT_NAME).version
    assert manifest["format"] == {"name": MANIFEST_FORMAT_NAME, "version": current} and type(current) is int
    readers = (replay_statmech_decision, reproduce_statmech_ordering, reassess_statmech_captured_inputs)
    for tampered in bad_format_manifests(manifest, current):
        for read in readers:
            with pytest.raises(ReplayError, match="unknown manifest format"):
                read(tampered)
