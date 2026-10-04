"""The decision manifest and its two replay levels.

Replay reproduces reasoning from captured inputs: it recomputes every applicability verdict (level one), then
edges, conflicts, fronts and the selection from those *recomputed* verdicts (level two), and compares both with what
the manifest records. These tests forge manifests the way a forger would (resealing the digest) and expect the
re-assessment, not the digest, to catch the change. The rules are SYNTHETIC test fixtures.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus
from app.services.network_selection import (
    ReplayError,
    replay_matches,
    replay_network,
    replay_network_assessment,
    replay_network_decision,
    select_network,
)
from app.services.network_selection.manifest import manifest_digest
from app.services.selection_kernel import Outcome
from tests.services.network_selection._requests import bundle_request, channel_request
from tests.services.network_selection._rules import ProtocolRule, protocol
from tests.services.network_selection._world import add_solve, fit_spec, set_review, target

A_, B_ = "chemically_significant_eigenvalues", "modified_strong_collision"
RULE = lambda **kw: ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_, **kw)  # noqa: E731


#: Names a rule, not a row.
RULE_IDS = {"rule_id", "overridden_by_rule_id"}


def select(session, request, rules=()):
    return select_network(session, request=request, rules=rules, require_snapshot=False)


def reseal(manifest: dict) -> dict:
    """What a forger does after editing: recompute the digest, so only re-assessment can object."""
    manifest["digest"] = {"algorithm": "sha256", "value": manifest_digest(manifest)}
    return manifest


def wire(manifest: dict) -> dict:
    """The manifest as a client would hold it: through JSON and back."""
    return json.loads(json.dumps(manifest))


@pytest.fixture
def scenario(db_session, world):
    """Two eligible solves (A preferred over B by the synthetic rule) and one that cannot answer the request."""
    a = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    b = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    short = add_solve(
        db_session, world, fits=[fit_spec("assoc", tmax=900.0)], protocol=protocol(A_)
    )  # request_outside_fit_support: recorded ineligible
    result = select(db_session, channel_request(world), [RULE()])
    assert result.outcome is Outcome.policy_preferred
    return result, wire(result.manifest), (a, b, short)


def test_a_selection_replays_to_the_decision_and_outcome_it_recorded(db_session, world, scenario):
    result, manifest, _ = scenario
    decision = replay_network(manifest, rules=[RULE()])
    assert decision == manifest["decision"] and decision["outcome"] == manifest["outcome"] == "policy_preferred"
    assert replay_matches(manifest, rules=[RULE()])


def test_the_manifest_is_replayable_with_no_database(db_session, world, scenario):
    _, manifest, _ = scenario
    db_session.rollback()  # every row the selection read is gone
    assert replay_network(manifest, rules=[RULE()])["selected_ref"] == manifest["decision"]["selected_ref"]


@pytest.mark.parametrize("kind", ["no_rules", "unranked", "conflict"])
def test_every_outcome_replays(db_session, world, kind):
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    if kind != "no_rules":
        add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    rules = {
        "no_rules": [],
        "unranked": [],
        "conflict": [RULE(), ProtocolRule("T-B-OVER-A", prefer=B_, yield_=A_)],
    }[kind]
    result = select(db_session, channel_request(world), rules)
    expected = {"no_rules": Outcome.sole_eligible_candidate, "unranked": Outcome.incomparable_alternatives, "conflict": Outcome.policy_conflict}
    assert result.outcome is expected[kind]
    assert replay_network(wire(result.manifest), rules=rules) == result.manifest["decision"]


def test_a_bundle_manifest_replays(db_session, world):
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in ("assoc", "elim")]
    options = {
        "fits": [fit_spec("assoc"), fit_spec("elim")],
        "solve_target": target(world, outputs=outputs),
        "product_sets": [{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
    }
    add_solve(db_session, world, protocol=protocol(A_), **options)
    add_solve(db_session, world, protocol=protocol(B_), **options)
    result = select(db_session, bundle_request(world), [RULE()])
    assert result.outcome is Outcome.policy_preferred
    manifest = wire(result.manifest)
    assert manifest["assessments"]["bundles"] and manifest["request"]["scope"] == "projected_bundle"
    assert replay_network(manifest, rules=[RULE()]) == manifest["decision"]


# -- forged documents ---------------------------------------------------------------------------------


def test_an_edit_that_leaves_the_digest_stale_is_refused_first(scenario):
    _, manifest, _ = scenario
    manifest["decision"]["basis"] = "edited"
    with pytest.raises(ReplayError, match="recorded digest"):
        replay_network(manifest, rules=[RULE()])


def test_a_mirrored_eligibility_edit_cannot_bypass_re_assessment(scenario):
    """Flip the ineligible solve's recorded verdict AND its eligibility together, reseal, and replay."""
    result, manifest, (_a, _b, short) = scenario
    forged = copy.deepcopy(manifest)
    row = next(a for a in forged["assessments"]["determinations"] if a["solve_ref"] == short.public_ref)
    assert row["physically_eligible"] is False
    fit_ref = short._fits[0].public_ref
    row.update(applicability="applicable", reasons=[], eligible_fit_refs=[fit_ref], physically_eligible=True)
    reseal(forged)
    with pytest.raises(ReplayError, match="recorded determinations assessments are not what the captured inputs give"):
        replay_network_assessment(forged)
    with pytest.raises(ReplayError):
        replay_network(forged, rules=[RULE()])
    # Level two never reads the recorded flags: from the recomputed verdicts it gives the original decision.
    assert replay_network_decision(forged, rules=[RULE()]) == manifest["decision"]


def test_an_edit_of_a_captured_input_that_leaves_the_recorded_assessment_stale_is_refused(scenario):
    _, manifest, (a, _b, _short) = scenario
    forged = copy.deepcopy(manifest)
    solve = next(s for s in forged["solves"] if s["solve_ref"] == a.public_ref)
    solve["target"]["validity"]["temperature_max_k"] = 1000.0  # the request (to 1500 K) is now outside it
    reseal(forged)
    with pytest.raises(ReplayError, match="assessments are not what the captured inputs give"):
        replay_network_assessment(forged)


def test_an_edit_that_hides_a_candidates_ineligibility_in_the_inputs_is_refused(scenario):
    """Making the short solve's fit cover the window in the captured facts while the record says otherwise."""
    _, manifest, (_a, _b, short) = scenario
    forged = copy.deepcopy(manifest)
    solve = next(s for s in forged["solves"] if s["solve_ref"] == short.public_ref)
    solve["fits"][0]["tmax_k"] = 2000.0
    reseal(forged)
    with pytest.raises(ReplayError):
        replay_network_assessment(forged)


def test_the_recorded_outcome_is_compared_as_well_as_the_decision(scenario):
    _, manifest, _ = scenario
    forged = copy.deepcopy(manifest)
    forged["outcome"] = "incomparable_alternatives"  # the decision itself is untouched
    reseal(forged)
    with pytest.raises(ReplayError, match="recomputed outcome 'policy_preferred' differs from the recorded"):
        replay_network(forged, rules=[RULE()])
    forged = copy.deepcopy(manifest)
    forged["decision"]["selected_ref"] = None
    reseal(forged)
    with pytest.raises(ReplayError, match="recomputed decision differs"):
        replay_network(forged, rules=[RULE()])


# -- versions and rules -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, value",
    [
        (("manifest_format_version",), 99),
        (("policy", "version"), "999"),
        (("policy", "name"), "kinetics_method_preferred"),
        (("policy", "assessment_version"), "999"),
        (("policy", "bounds_version"), "999"),
    ],
)
def test_an_unavailable_format_policy_assessment_or_bounds_version_refuses_replay(scenario, path, value):
    _, manifest, _ = scenario
    forged = copy.deepcopy(manifest)
    node = forged
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    reseal(forged)
    for level in (replay_network_assessment, lambda m: replay_network_decision(m, rules=[RULE()]), lambda m: replay_network(m, rules=[RULE()])):
        with pytest.raises(ReplayError):
            level(forged)


def test_a_declaration_version_this_code_does_not_read_refuses_replay(scenario):
    _, manifest, (a, _b, _short) = scenario
    forged = copy.deepcopy(manifest)
    next(s for s in forged["solves"] if s["solve_ref"] == a.public_ref)["target"]["version"] = 2
    reseal(forged)
    with pytest.raises(ReplayError, match="declaration of version 2"):
        replay_network_assessment(forged)


def test_a_rule_the_running_registry_lacks_or_holds_in_another_state_refuses_replay(scenario):
    _, manifest, _ = scenario
    with pytest.raises(ReplayError, match="not in the running registry"):
        replay_network(manifest)  # the shipped registry carries no such rule
    with pytest.raises(ReplayError, match="not in the running registry"):
        replay_network(manifest, rules=[ProtocolRule("T-OTHER", prefer=A_, yield_=B_)])
    with pytest.raises(ReplayError, match="was active when this decision was made and is inactive now"):
        replay_network(manifest, rules=[RULE(status="inactive")])
    forged = copy.deepcopy(manifest)
    forged["decision"]["rules"][0]["manifest_sha256"] = "a" * 64
    reseal(forged)
    with pytest.raises(ReplayError, match="different audited manifest"):
        replay_network(forged, rules=[RULE()])
    pinned = RULE(manifest_sha256="b" * 64)
    with pytest.raises(ReplayError, match="different audited manifest"):
        replay_network(manifest, rules=[pinned])


# -- what the manifest holds ---------------------------------------------------------------------------


def test_the_manifest_carries_public_refs_content_and_no_row_ids(scenario):
    _, manifest, _ = scenario

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                assert key != "id" and (not key.endswith("_id") or key in RULE_IDS), f"{path}.{key}"
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(manifest)
    assert manifest["network"]["states"][0]["participants"]  # the exact content behind each locator, not a hash alone
    assert {s["review_status"] for s in manifest["solves"]} == {"approved"}
    assert manifest["visibility"]["read_profile"] == "exploratory"
    assert manifest["visibility"]["review_statuses_observed"] == ["approved"]
    assert "forged document" in manifest["replay_boundary"]  # said plainly: replay is not authentication
    assert manifest["digest"]["value"] == manifest_digest(manifest)


def test_the_same_inputs_give_the_same_canonical_manifest(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    first = select(db_session, channel_request(world), [RULE()]).manifest
    second = select(db_session, channel_request(world), [RULE()]).manifest
    assert first["digest"] == second["digest"] and first == second


def test_a_historic_manifest_replays_as_captured_and_a_fresh_decision_sees_the_new_review(db_session, world):
    a = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=None)
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    set_review(db_session, world, a, RecordReviewStatus.approved)
    old = select(db_session, channel_request(world), [RULE()])
    assert old.selected_ref == a._dets["d_assoc"].public_ref
    set_review(db_session, world, a, RecordReviewStatus.under_review)  # withdrawn since
    set_review(db_session, world, a, RecordReviewStatus.rejected)
    fresh = select(db_session, channel_request(world), [RULE()])
    assert fresh.outcome is Outcome.sole_eligible_candidate and fresh.selected_ref != old.selected_ref
    # The historical artifact still replays to what it captured, with the review state it observed.
    historic = wire(old.manifest)
    assert replay_network(historic, rules=[RULE()]) == historic["decision"]
    assert next(s for s in historic["solves"] if s["solve_ref"] == a.public_ref)["review_status"] == "approved"
    assert fresh.manifest["digest"] != old.manifest["digest"]


def test_a_manifest_over_the_snapshot_size_bound_is_refused_when_the_facts_alone_are_not(db_session, world):
    import dataclasses

    from app.services.network_selection import BOUNDS_V1, assess_network
    from app.services.network_selection.service import snapshot_size_bytes

    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    request = channel_request(world)
    facts = assess_network(db_session, request=request, require_snapshot=False)
    facts_size = snapshot_size_bytes(request, facts.network, facts.solves)
    manifest_size = len(json.dumps(select(db_session, request).manifest, sort_keys=True, separators=(",", ":")).encode())
    assert manifest_size > facts_size
    tight = dataclasses.replace(request, bounds=dataclasses.replace(BOUNDS_V1, snapshot_bytes=facts_size + 1))
    assess_network(db_session, request=tight, require_snapshot=False)  # the facts fit
    with pytest.raises(CodedValueError) as caught:
        select(db_session, tight)
    assert caught.value.code == "network_selection_snapshot_too_large"


# -- the review fixes ------------------------------------------------------------------------------------


def test_a_recorded_ungrouped_fit_list_that_the_inputs_do_not_give_is_refused(db_session, world):
    """A fit that states no determination is recorded as ungrouped; erasing that record is a forgery."""
    add_solve(db_session, world, fits=[fit_spec("assoc"), fit_spec("assoc", det=None, rep="orphan")], protocol=protocol(A_))
    result = select(db_session, channel_request(world), [RULE()])
    manifest = wire(result.manifest)
    assert manifest["assessments"]["ungrouped_fit_refs"], "the scenario must leave a fit ungrouped"
    forged = copy.deepcopy(manifest)
    forged["assessments"]["ungrouped_fit_refs"] = []
    reseal(forged)
    with pytest.raises(ReplayError, match="ungrouped fits"):
        replay_network_assessment(forged)
    with pytest.raises(ReplayError, match="ungrouped fits"):
        replay_network(forged, rules=[RULE()])


def test_decision_replay_takes_no_assessments_of_its_own(scenario):
    """Handing a decision replay forged assessments used to reproduce any winner; the parameter is gone."""
    _, manifest, _ = scenario
    with pytest.raises(TypeError):
        replay_network_decision(manifest, rules=[RULE()], assessments=manifest["assessments"])  # type: ignore[call-arg]
    # And the decision replay recomputes: forging an assessment's eligibility cannot change what it returns.
    forged = copy.deepcopy(manifest)
    for row in forged["assessments"]["determinations"]:
        row["physically_eligible"] = not row["physically_eligible"]
    reseal(forged)
    assert replay_network_decision(forged, rules=[RULE()]) == manifest["decision"]
    with pytest.raises(ReplayError):
        replay_network(forged, rules=[RULE()])


def test_the_observed_review_statuses_must_be_those_of_the_captured_solves(scenario):
    _, manifest, _ = scenario
    assert manifest["visibility"]["review_statuses_observed"]
    forged = copy.deepcopy(manifest)
    forged["visibility"]["review_statuses_observed"] = ["approved", "rejected"]
    assert forged["visibility"]["review_statuses_observed"] != manifest["visibility"]["review_statuses_observed"]
    reseal(forged)
    for level in (
        replay_network_assessment,
        lambda m: replay_network_decision(m, rules=[RULE()]),
        lambda m: replay_network(m, rules=[RULE()]),
    ):
        with pytest.raises(ReplayError, match="observed review statuses"):
            level(forged)


def test_the_same_bundle_in_another_order_is_the_same_request_with_the_same_digest(db_session, world):
    channels = ("assoc", "elim")
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in channels]
    options = {
        "fits": [fit_spec("assoc"), fit_spec("elim")],
        "solve_target": target(world, outputs=outputs),
        "product_sets": [{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
    }
    add_solve(db_session, world, protocol=protocol(A_), **options)
    forward = select(db_session, bundle_request(world, *channels), [RULE()])
    backward = select(db_session, bundle_request(world, *reversed(channels)), [RULE()])
    assert forward.manifest["request"]["outputs"] == backward.manifest["request"]["outputs"]
    assert forward.manifest["digest"] == backward.manifest["digest"]
    assert [o["channel_key"] for o in forward.manifest["request"]["outputs"]] == sorted(channels)
