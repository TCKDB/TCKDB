"""Selection against real rows, the replayable manifest, and both levels of replay.

Replay is judged by what it refuses: every tamper below is something a careless or hostile editor could do to a
manifest, and each must end in a ``ReplayError`` or a replay that does not match, never in a quiet pass.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.services.structure_selection import (
    Grain,
    Intent,
    ReplayError,
    SelectionBounds,
    StructureRequest,
    replay_matches,
    replay_structure_assessment,
    replay_structure_decision,
    select_entry_structures,
)
from app.services.structure_selection.bounds import check_manifest_size
from app.services.structure_selection.manifest import canonical, digest
from app.services.structure_selection.models import Objective, ResultMode
from app.services.structure_selection.models import StructureOutcome as O
from tests.services.scientific_read._factories import make_geometry, make_lot, set_review
from tests.services.structure_selection._support import (
    curated,
    declaration,
    make_world,
    minimum_request,
    request,
    reset_current_read_profile,
)
from tests.services.structure_selection.test_decision import Pref
from tests.services.structure_selection.test_service import build_basin, counts

DECL_B = declaration(spin_treatment={"state": "known", "value": "unrestricted"})


def select(session, world, req, **kw):
    world.settle()
    return select_entry_structures(session, entry_id=world.entry.id, request=req, require_snapshot=False, **kw)


def reseal(manifest):
    """What an editor who knows the scheme does: recompute every checksum after the edit."""
    for section in ("calculations", "determinations"):
        for raw in manifest[section]:
            raw.pop("content_sha256", None)
            raw["content_sha256"] = digest(raw)
    manifest.pop("integrity", None)
    manifest["integrity"] = {"algorithm": "sha256", "manifest_sha256": digest(manifest), "note": "resealed"}
    return manifest


def keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from keys(v)


@pytest.fixture
def calc_selection(db_session):
    world = make_world(db_session)
    world.sp("a", -76.40, declared=declaration())
    world.sp("b", -76.45, declared=declaration(), status=None)
    world.sp("c", None, declared=declaration())
    return world, select(db_session, world, request())


@pytest.fixture
def basin_selection(db_session):
    world = make_world(db_session)
    build_basin(world, energy=-76.40, geometry=make_geometry(db_session))
    build_basin(world, energy=-76.45, geometry=make_geometry(db_session))
    return world, select(db_session, world, minimum_request())


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_a_calculation_selection_reports_the_minimum_and_its_replayable_manifest(db_session, calc_selection):
    world, result = calc_selection
    assert result.outcome is O.recorded_minimum and result.selected_refs == (world.calcs["b"].public_ref,)
    assert result.manifest["outcome"] == "recorded_minimum"
    assert result.manifest["request"]["intent"] == "recorded_minimum"
    assert result.manifest["versions"] == {
        "assessment": "1", "normalizer": "1", "decision": "1", "administrative_key": "1", "rules": "1",
        "finding_semantics": [1],
    }
    assert result.manifest["read_profile"] == {"profile": "exploratory", "review_floor": None}
    assert result.manifest["snapshot_isolation"]
    assert replay_matches(result.manifest)


def test_a_basin_selection_replays_to_the_same_decision(db_session, basin_selection):
    world, result = basin_selection
    assert result.outcome is O.validated_corpus_minimum
    replayed = replay_structure_decision(result.manifest)
    assert replayed == json.loads(canonical(result.decision.to_dict()))
    assert [a.unit_ref for a in replay_structure_assessment(result.manifest)] == [a.unit_ref for a in result.assessment.assessments]


def test_selecting_writes_nothing(db_session):
    world = make_world(db_session)
    build_basin(world, energy=-76.40)
    world.settle()
    before = counts(db_session)
    select_entry_structures(db_session, entry_id=world.entry.id, request=minimum_request(), require_snapshot=False)
    assert counts(db_session) == before


def test_the_manifest_names_records_by_public_ref_and_never_by_database_id(basin_selection):
    _, result = basin_selection
    found = {k for k in keys(result.manifest) if k == "id" or (k.endswith("_id") and k != "cohort_id")}
    assert found == set()


def test_a_population_of_nothing_is_no_candidates(db_session):
    world = make_world(db_session)
    result = select(db_session, world, minimum_request())
    assert result.outcome is O.no_candidates and result.selected_refs == ()
    assert replay_matches(result.manifest)


def test_a_manifest_over_its_bound_is_refused_whole(db_session):
    world = make_world(db_session)
    build_basin(world, energy=-76.40)
    small = minimum_request(bounds=SelectionBounds(manifest_bytes=2_000))
    with pytest.raises(CodedValueError) as exc:
        select(db_session, world, small)
    assert exc.value.code == "structure_selection_manifest_too_large"
    assert exc.value.context["limit"] == 2_000 and exc.value.context["size"] > 2_000
    # The same population is decided under the endpoint bound.
    assert select(db_session, world, minimum_request()).outcome is O.validated_corpus_minimum


def test_an_exploratory_manifest_lists_what_the_callers_own_floor_excluded(db_session):
    world = make_world(db_session)
    world.sp("ok", -76.4, declared=declaration())
    pending = world.sp("pending", -76.9, declared=declaration(), status=None)
    result = select(db_session, world, request(min_review_status=RecordReviewStatus.approved))
    population = result.manifest["population"]
    assert population["excluded_by_review_withheld"] is False and population["excluded_count"] == 1
    assert [e["unit_ref"] for e in population["excluded_by_review"]] == [pending.public_ref]
    assert replay_matches(result.manifest)


def test_the_manifest_bound_is_inclusive_at_the_limit_and_refuses_one_byte_over():
    bounds = SelectionBounds(manifest_bytes=1_000)
    check_manifest_size(bounds, 999)
    check_manifest_size(bounds, 1_000)  # the limit is the largest manifest that is returned
    with pytest.raises(CodedValueError) as exc:
        check_manifest_size(bounds, 1_001)
    assert exc.value.code == "structure_selection_manifest_too_large" and exc.value.context["size"] == 1_001


def test_the_manifest_says_its_digests_are_checksums_not_signatures(basin_selection):
    _, result = basin_selection
    note = result.manifest["integrity"]["note"]
    assert "not signatures" in note and result.manifest["integrity"]["algorithm"] == "sha256"
    assert all(len(c["content_sha256"]) == 64 for c in result.manifest["calculations"])


def test_replay_needs_no_database_and_keeps_the_statuses_it_captured(db_session, calc_selection):
    world, result = calc_selection
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=world.calcs["b"].id,
        status=RecordReviewStatus.rejected,
    )
    live = select(db_session, world, request())
    assert live.selected_refs == (world.calcs["a"].public_ref,)  # the live decision moved on
    assert replay_matches(result.manifest)  # the historical artifact did not
    assert replay_structure_decision(result.manifest)["selected_refs"] == [world.calcs["b"].public_ref]


# ---------------------------------------------------------------------------
# Tampering
# ---------------------------------------------------------------------------


def test_an_edit_that_leaves_the_checksum_alone_is_refused(calc_selection):
    _, result = calc_selection
    edited = copy.deepcopy(result.manifest)
    edited["outcome"] = "recorded_minimum "
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(edited)
    assert exc.value.code == "manifest_checksum_mismatch"


def test_a_resealed_edit_of_the_recorded_outcome_or_decision_does_not_match(calc_selection):
    _, result = calc_selection
    top = reseal(copy.deepcopy(result.manifest))
    top["outcome"] = "sole_eligible_candidate"
    top = reseal(top)
    assert replay_matches(top) is False
    decision = copy.deepcopy(result.manifest)
    decision["decision"]["selected_refs"] = [*reversed(decision["decision"]["selected_refs"]), "calc_forged"]
    assert replay_matches(reseal(decision)) is False


def test_a_recorded_eligibility_flag_is_never_trusted(calc_selection):
    world, result = calc_selection
    ineligible = world.calcs["c"].public_ref
    forged = copy.deepcopy(result.manifest)
    for a in forged["assessments"]:
        if a["unit_ref"] == ineligible:
            a["applicability"], a["physically_eligible"], a["reasons"] = "applicable", True, []
    with pytest.raises(ReplayError) as exc:
        replay_structure_assessment(reseal(forged))
    assert exc.value.code == "assessment_mismatch" and ineligible in str(exc.value)


def test_a_forged_input_is_caught_by_its_checksum_and_a_resealed_one_by_the_recomputed_assessment(calc_selection):
    world, result = calc_selection
    target = world.calcs["a"].public_ref
    forged = copy.deepcopy(result.manifest)
    forged_calc = next(c for c in forged["calculations"] if c["calculation_ref"] == target)
    forged_calc["energy"]["sp_electronic_hartree"] = -99.0
    forged["integrity"] = {"algorithm": "sha256", "manifest_sha256": digest({k: v for k, v in forged.items() if k != "integrity"}), "note": "x"}
    with pytest.raises(ReplayError) as exc:
        replay_structure_assessment(forged)
    assert exc.value.code == "input_checksum_mismatch"
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(forged))
    assert exc.value.code == "assessment_mismatch"


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("manifest_format_version",), 2, "unsupported_manifest_format"),
        (("versions", "assessment"), "2", "unsupported_semantic_version"),
        (("versions", "normalizer"), "9", "unsupported_semantic_version"),
        (("versions", "decision"), "2", "unsupported_semantic_version"),
        (("versions", "administrative_key"), "2", "unsupported_semantic_version"),
        (("versions", "finding_semantics"), [1, 2], "unsupported_semantic_version"),
    ],
)
def test_a_version_this_release_does_not_carry_refuses_the_replay(calc_selection, path, value, code):
    _, result = calc_selection
    edited = copy.deepcopy(result.manifest)
    node = edited
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(edited))
    assert exc.value.code == code


def test_a_manifest_that_contradicts_its_own_profile_or_floor_is_refused(db_session):
    world = make_world(db_session)
    pending = world.sp("a", -76.4, declared=declaration(), status=None)
    approved = world.sp("b", -76.3, declared=declaration())
    token = curated()
    try:
        curated_result = select(db_session, world, request())
    finally:
        reset_current_read_profile(token)
    assert curated_result.manifest["read_profile"] == {"profile": "curated", "review_floor": "approved"}
    population = curated_result.manifest["population"]
    # Under a profile floor nothing below it is listed or counted (the redaction precedes the checksum, so it verifies).
    assert population["excluded_by_review_withheld"] is True and population["excluded_by_review"] == []
    assert population["excluded_count"] == 0 and population["total_units"] == population["visible_units"]
    assert [a.unit_ref for a in curated_result.assessment.assessments] == [approved.public_ref]
    assert pending.public_ref not in json.dumps(curated_result.manifest)
    assert replay_matches(curated_result.manifest)
    # Relabel the manifest as exploratory while it keeps a curated status set: the two disagree.
    edited = copy.deepcopy(curated_result.manifest)
    edited["read_profile"] = {"profile": "exploratory", "review_floor": None}
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(edited))
    assert exc.value.code == "inconsistent_manifest"
    # Widen the effective statuses under a curated profile: curated means approved only.
    widened = copy.deepcopy(curated_result.manifest)
    widened["effective_review_statuses"] = ["approved", "not_reviewed", "under_review"]
    with pytest.raises(ReplayError):
        replay_structure_decision(reseal(widened))
    # A unit whose status the request could not see.
    smuggled = copy.deepcopy(curated_result.manifest)
    smuggled["calculations"][0]["review_status"] = "not_reviewed"
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(smuggled))
    assert exc.value.code == "inconsistent_manifest"


def _exploratory_basin_manifest(db_session):
    """A determination-grain manifest whose source calculation is not reviewed (visible only to the default profile)."""
    world = make_world(db_session)
    build_basin(world, energy=-76.40, geometry=make_geometry(db_session), statuses={"sp": None})
    return select(db_session, world, minimum_request()).manifest


def _relabel_curated(manifest):
    manifest["read_profile"] = {"profile": "curated", "review_floor": "approved"}
    manifest["effective_review_statuses"] = ["approved"]
    return manifest


def test_a_determination_grain_manifest_relabelled_curated_is_refused_while_its_sources_are_not_approved(db_session):
    manifest = _exploratory_basin_manifest(db_session)
    assert replay_matches(manifest)
    assert any(c["review_status"] == "not_reviewed" for c in manifest["calculations"])
    relabelled = _relabel_curated(copy.deepcopy(manifest))
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(relabelled))
    assert exc.value.code == "inconsistent_manifest"
    assert replay_matches(reseal(copy.deepcopy(manifest)))  # resealing alone changes nothing; the relabel does


def test_a_genuinely_curated_determination_manifest_with_a_source_flipped_is_refused(db_session):
    world = make_world(db_session)
    build_basin(world, energy=-76.40, geometry=make_geometry(db_session))
    token = curated()
    try:
        genuine = select(db_session, world, minimum_request()).manifest
    finally:
        reset_current_read_profile(token)
    assert genuine["read_profile"]["profile"] == "curated" and replay_matches(genuine)
    assert all(c["review_status"] == "approved" for c in genuine["calculations"])
    flipped = copy.deepcopy(genuine)
    flipped["calculations"][0]["review_status"] = "not_reviewed"
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(flipped))
    assert exc.value.code == "inconsistent_manifest"
    determination = copy.deepcopy(genuine)
    determination["determinations"][0]["review_status"] = "under_review"
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(determination))
    assert exc.value.code == "inconsistent_manifest"


def test_a_forged_duplicate_assessment_is_refused_not_shadowed(basin_selection):
    _, result = basin_selection
    forged = copy.deepcopy(result.manifest)
    real = forged["assessments"][0]
    bogus = {**copy.deepcopy(real), "applicability": "applicable", "physically_eligible": True, "reasons": [], "blocking": []}
    bogus["advisory"] = ["forged"]
    forged["assessments"].insert(0, bogus)  # before the real one, so a last-wins dict would never compare it
    with pytest.raises(ReplayError) as exc:
        replay_structure_assessment(reseal(forged))
    assert exc.value.code == "assessment_mismatch" and "more than one recorded assessment" in str(exc.value)
    missing = copy.deepcopy(result.manifest)
    missing["assessments"].pop()
    with pytest.raises(ReplayError) as exc:
        replay_structure_assessment(reseal(missing))
    assert exc.value.code == "assessment_mismatch"


def test_the_recorded_visible_unit_count_must_be_the_number_of_recorded_units(basin_selection):
    _, result = basin_selection
    forged = copy.deepcopy(result.manifest)
    forged["population"]["visible_units"] += 1
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(forged))
    assert exc.value.code == "inconsistent_manifest" and "visible-unit count" in str(exc.value)


def test_an_unreadable_manifest_is_refused_not_crashed(calc_selection):
    _, result = calc_selection
    broken = copy.deepcopy(result.manifest)
    del broken["request"]["grain"]
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(broken))
    assert exc.value.code == "unreadable_manifest"


# ---------------------------------------------------------------------------
# Rules in the manifest
# ---------------------------------------------------------------------------


@pytest.fixture
def protocol_selection(db_session):
    world = make_world(db_session)
    other_lot = make_lot(db_session)
    build_basin(world, energy=-76.40)
    build_basin(world, energy=-76.50, lot=other_lot, declared=DECL_B)
    rule = Pref("R1", prefer="restricted", yield_="unrestricted")
    req = StructureRequest(Grain.conformer, Intent.protocol_preferred, objective=Objective.physical_accuracy, result_mode=ResultMode.all)
    return rule, select(db_session, world, req, rules=[rule])


def test_a_protocol_decision_replays_with_the_registry_that_made_it(protocol_selection):
    rule, result = protocol_selection
    assert result.outcome is O.policy_preferred
    assert replay_matches(result.manifest, rules=[rule])


def test_replay_refuses_a_rule_that_is_gone_changed_or_repinned(protocol_selection):
    rule, result = protocol_selection
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(result.manifest)
    assert exc.value.code == "rule_unavailable"
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(result.manifest, rules=[Pref("R1", prefer="restricted", yield_="unrestricted", status="inactive")])
    assert exc.value.code == "rule_status_changed"
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(result.manifest, rules=[Pref("R1", prefer="restricted", yield_="unrestricted", sha="b" * 64)])
    assert exc.value.code == "rule_manifest_changed"
    # A replay under a rule that says the opposite does not reproduce the decision.
    flipped = Pref("R1", prefer="unrestricted", yield_="restricted")
    assert replay_matches(result.manifest, rules=[flipped]) is False
