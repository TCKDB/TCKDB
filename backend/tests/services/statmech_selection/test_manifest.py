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
from app.services.selection_kernel import Tri
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
from app.services.statmech_selection.rules import RULE_INACTIVE
from app.services.statmech_selection.service import StatmechQualification
from tests.services.statmech_selection._rules import _PREFERRED, SyntheticRule, beats, candidate, population, ref
from tests.services.statmech_selection._support import EFFECTIVE, approved_calcs, assess, req, subject

EXPLORATORY = {"profile": "exploratory", "review_floor": None}
CURATED = {"profile": "curated", "review_floor": "approved"}


def make_manifest(candidates=None, rules=(), profile=EXPLORATORY, request=None, statuses=EFFECTIVE, **extra) -> dict[str, Any]:
    candidates = list(candidates if candidates is not None else population("a", "b"))
    request = request or req(_PREFERRED)
    calcs = approved_calcs()
    assessments = tuple(assess(c, request, calcs, effective=statuses) for c in candidates)
    decision = decide_statmech(request=request, subject=subject(), candidates=candidates, assessments=assessments, rules=rules)
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
    rule = beats("r", "a", "b")
    manifest = make_manifest(rules=[rule])
    assert manifest["outcome"] == "policy_preferred" and manifest["decision"]["selection"]["scientific_ref"] == ref("a")
    ordering = reproduce_statmech_ordering(manifest, rules=[rule])
    assert ordering["decision"] == manifest["decision"]
    assert ordering["label"]["reassessed"] is False and ordering["label"]["kind"] == "current"
    assert "not an endorsement" in ordering["label"]["statement"]
    full = replay_statmech_decision(manifest, rules=[rule])
    assert full["decision"] == manifest["decision"] and full["label"]["reassessed"] is True and full["label"]["kind"] == "current"
    assert replay_matches(manifest, rules=[rule])
    assert [a.candidate_ref for a in reassess_statmech_captured_inputs(manifest)] == [ref("a"), ref("b")]


def test_the_default_registry_is_empty_and_replay_needs_no_rules():
    manifest = make_manifest()
    assert manifest["decision"]["protocol"]["rules"] == [] and manifest["outcome"] == "incomparable_alternatives"
    assert replay_matches(manifest)


def test_the_manifest_names_what_it_was_made_under_and_leaks_no_database_id():
    manifest = make_manifest()
    assert manifest["versions"] == {
        "contract": 1, "assessment": 1, "normalizer": 1, "decision": 1,
        "administrative_key": 1, "rules": 1, "finding_semantics": [1],
    }
    assert manifest["assessment_semantics"]["name"] == "statmech_selection_assessment"
    assert manifest["assessment_semantics"]["version"] == 1
    assert manifest["assessment_semantics"]["source_findings"] == {"name": "structure_source_findings", "version": 1}
    assert manifest["assessment_semantics"]["evidence_rubric"] == {"name": "statmech_qualification", "version": 1}

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
                             rules=[beats("r", "a", "b", "c")], request=req({**_PREFERRED, "coverage_requirement": "eligible_population"}))
    assert manifest["decision"]["summary"]["undecided_refs"] == [ref("c")]

    def make_c_eligible(body):
        a = assessment_entry(body, "c")
        a.update(applicability="applicable", eligible=True, reasons=[])

    tampered = retamper(manifest, make_c_eligible)
    # reproducing an ordering never reassesses, but it still refuses a decision that is not what the recorded assessments give
    refused("decision_mismatch", reproduce_statmech_ordering, tampered, rules=[beats("r", "a", "b", "c")])
    refused("assessment_mismatch", replay_statmech_decision, tampered, rules=[beats("r", "a", "b", "c")])


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
    manifest = make_manifest(rules=[beats("r", "a", "b")])
    rules = [beats("r", "a", "b")]
    refused("inconsistent_manifest", replay_statmech_decision, retamper(manifest, lambda b: b.update(outcome="sole_eligible_candidate")), rules=rules)
    swapped = retamper(manifest, lambda b: b["decision"]["selection"].update(scientific_ref=ref("b")))
    assert replay_matches(manifest, rules=rules) and not replay_matches(swapped, rules=rules)
    relabelled = retamper(manifest, lambda b: (b["decision"].update(outcome="policy_conflict"), b.update(outcome="policy_conflict")))
    assert not replay_matches(relabelled, rules=rules)


def test_replay_refuses_a_recorded_decision_it_does_not_reproduce():
    rule = beats("r", "a", "b")
    manifest = make_manifest(rules=[rule])
    assert manifest["outcome"] == "policy_preferred"

    def downgrade_to_incomparable(body):
        body["decision"]["outcome"] = "incomparable_alternatives"
        body["outcome"] = "incomparable_alternatives"
        body["decision"]["selection"] = dict.fromkeys(body["decision"]["selection"])

    tampered = retamper(manifest, downgrade_to_incomparable)  # every checksum recomputed, outcome and decision agree
    refused("decision_mismatch", replay_statmech_decision, tampered, rules=[rule])
    refused("decision_mismatch", reproduce_statmech_ordering, tampered, rules=[rule])
    assert replay_matches(tampered, rules=[rule]) is False and replay_matches(manifest, rules=[rule]) is True
    # a tampered edge list is caught the same way
    no_edges = retamper(manifest, lambda b: b["decision"]["protocol"].update(edges=[]))
    refused("decision_mismatch", replay_statmech_decision, no_edges, rules=[rule])


def test_the_population_and_the_review_profile_must_be_self_consistent():
    manifest = make_manifest()
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b["population"].update(visible_candidates=5)))
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b["candidates"].pop()))
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b.update(read_profile=CURATED)))
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b.update(read_profile={"profile": "exploratory", "review_floor": "approved"})))
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b.update(read_profile={"profile": "other", "review_floor": None})))
    # each half of the profile check stands on its own: a curated label with no floor changes no status, and a narrowed
    # status set under an exploratory profile changes no candidate
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b.update(read_profile={"profile": "curated", "review_floor": None})))
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b.update(effective_review_statuses=["approved"])))
    # a curated relabel with an unapproved candidate captured
    curated = make_manifest(profile=CURATED, statuses=frozenset({RecordReviewStatus.approved}))
    flipped = retamper(curated, lambda b: candidate_entry(b, "a").update(review_status="not_reviewed"))
    refused("inconsistent_manifest", reproduce_statmech_ordering, flipped)
    quietly = retamper(curated, lambda b: b["calculations"][0].update(review_status="not_reviewed"))
    refused("inconsistent_manifest", reproduce_statmech_ordering, quietly)
    wider = retamper(curated, lambda b: b.update(effective_review_statuses=["approved", "not_reviewed"]))
    refused("inconsistent_manifest", reproduce_statmech_ordering, wider)


def test_two_captured_candidates_cannot_share_a_reference_or_an_ordinal():
    manifest = make_manifest()
    refused("inconsistent_manifest", reproduce_statmech_ordering, retamper(manifest, lambda b: b["candidates"][1].update(id_rank=b["candidates"][0]["id_rank"])))


# --- versions and labels -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["contract", "decision", "administrative_key", "rules"])
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
    # Another format (no earlier one is kept): refused like any format this release does not carry.
    refused("unsupported_manifest_format", reproduce_statmech_ordering, retamper(manifest, lambda b: b["format"].update(version=2)))
    refused("unsupported_manifest_format", replay_statmech_decision, retamper(manifest, lambda b: b["format"].update(version=2)))


@pytest.mark.parametrize("name", ["contract", "decision", "administrative_key", "rules"])
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

    # A release that moved to semantics 2 and still reads 1 (for a published release that cites it): 1 is registered, not current.
    both = VersionRegistry("test_semantics")
    name = manifest_module.ASSESSMENT_SEMANTICS_NAME
    both.register(name, 1, VersionRef(name=name, version=1), current=False)
    both.register(name, 2, VersionRef(name=name, version=2), current=True)
    monkeypatch.setattr(manifest_module, "ASSESSMENT_VERSION", 2)
    monkeypatch.setattr(manifest_module, "ASSESSMENT_SEMANTICS", both)
    label = reproduce_statmech_ordering(manifest)["label"]
    assert label["kind"] == "historical" and label["recorded_assessment_semantics"] == 1 and label["reassessed"] is False
    assert "made under 1" in label["statement"] and "assessed under 2" in label["statement"]
    refused("historical_manifest_cannot_be_reassessed", reassess_statmech_captured_inputs, manifest)
    refused("historical_manifest_cannot_be_reassessed", replay_statmech_decision, manifest)


# --- rules --------------------------------------------------------------------------------------------------------------------


def test_replay_refuses_a_rule_that_is_gone_changed_status_or_rests_on_another_manifest():
    rule = beats("r", "a", "b")
    manifest = make_manifest(rules=[rule])
    refused("rule_unavailable", replay_statmech_decision, manifest)
    refused("rule_unavailable", reproduce_statmech_ordering, manifest, rules=[beats("other", "a", "b")])
    refused("rule_status_changed", replay_statmech_decision, manifest, rules=[beats("r", "a", "b", status=RULE_INACTIVE)])
    refused("rule_manifest_changed", replay_statmech_decision, manifest, rules=[beats("r", "a", "b", digest="cd" * 32)])
    assert replay_matches(manifest, rules=[rule])


def test_an_inactive_rule_is_recorded_with_its_reasons_and_replays_inactive():
    rule = beats("r", "a", "b", status=RULE_INACTIVE, digest=None)
    manifest = make_manifest(rules=[rule])
    entry = manifest["decision"]["protocol"]["rules"][0]
    assert entry["status"] == "inactive" and entry["inactive_reasons"] and manifest["outcome"] == "incomparable_alternatives"
    assert replay_matches(manifest, rules=[rule])


def test_an_unresolved_comparability_and_a_conflict_replay_to_the_same_outcome():
    undecided = SyntheticRule("r", prefer={ref("a"): Tri.true}, yield_={ref("b"): Tri.unknown})
    manifest = make_manifest(rules=[undecided])
    assert manifest["outcome"] == "unresolved_comparability" and replay_matches(manifest, rules=[undecided])
    conflict = [beats("r1", "a", "b"), beats("r2", "b", "a")]
    manifest = make_manifest(rules=conflict)
    assert manifest["outcome"] == "policy_conflict" and replay_matches(manifest, rules=conflict)


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
