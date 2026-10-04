"""Kinetics selection over real rows: a decision, its manifest, its replay, and what neither may leak."""

from __future__ import annotations

import copy
import json

import pytest

from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
)
from app.db.models.kinetics import KineticsSourceCalculation
from app.services.kinetics_selection import (
    ReplayError,
    replay_decision,
    replay_matches,
    select_reaction_entry_kinetics,
)
from app.services.kinetics_selection import selection as selection_module
from app.services.kinetics_selection.loader import load_population, scan_population
from app.services.kinetics_selection.rules import default_rules
from app.services.scientific_read.profile import (
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from app.services.selection_kernel import Outcome
from tests.services.kinetics_selection.test_engine import LabelRule
from tests.services.kinetics_selection.test_service import (
    REQUEST,
    declared,
    determination,
)
from tests.services.scientific_read._factories import make_calculation, make_lot

PREFERRED = {"version": 1, "method_kind": "saddle_point_tst"}
YIELDING = {"version": 1, "method_kind": "variational_tst"}
RULE = LabelRule("TEST-R1", {"saddle_point_tst"}, {"variational_tst"})


def with_protocol(session, world, key, protocol, **kw):
    """A declared record whose (stored) protocol is ``protocol``; set before approval, which freezes it."""
    det = determination(session, world, key)

    def attach(k):
        k.protocol_declaration = protocol
        session.flush()

    return declared(session, world, det, children=attach, **kw), det


def select(session, entry, request=REQUEST, rules=(RULE,)):
    return select_reaction_entry_kinetics(
        session, reaction_entry_id=entry.id, request=request, rules=rules, require_snapshot=False
    )


@pytest.fixture
def two(db_session, world):
    old_good, det_good = with_protocol(db_session, world, "good", PREFERRED, status=RecordReviewStatus.not_reviewed)
    new_other, det_other = with_protocol(db_session, world, "other", YIELDING, status=RecordReviewStatus.approved)
    return old_good, det_good, new_other, det_other


# -- a decision over real rows -----------------------------------------------------------------------------


def test_a_scoped_preference_selects_the_preferred_determination_over_a_better_reviewed_one(db_session, world, two):
    good, det_good, other, det_other = two
    result = select(db_session, world.entry)
    assert result.outcome is Outcome.policy_preferred
    assert result.selected_determination_ref == det_good.public_ref
    assert result.representation_refs == (good.public_ref,)
    # Browse order (review first) would list the approved one first; the preference outranks it.
    assert result.decision.administrative_order[0] == det_other.public_ref
    (edge,) = result.decision.edges
    assert edge["label"] == "expected-performance inference"


def test_without_an_active_rule_the_same_population_is_unranked_and_says_so(db_session, world, two):
    result = select(db_session, world.entry, rules=default_rules())
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_determination_ref is None
    assert result.decision.edges == () and {m["why"] for m in result.decision.rule_matches} == {"rule_status_inactive"}
    assert result.decision.administrative_first is not None


def test_a_record_that_does_not_state_its_protocol_competes_and_blocks_a_unique_winner(db_session, world, two):
    declared(db_session, world, determination(db_session, world, "silent"))  # no protocol at all
    result = select(db_session, world.entry)
    assert result.outcome is Outcome.incomparable_alternatives
    assert result.decision.edges  # the preference still holds between the two that state theirs
    assert len(result.decision.fronts[0]) == 2  # the silent one stays in the first front beside the winner


def test_ineligible_records_are_disclosed_and_do_not_compete(db_session, world, two):
    from app.db.models.kinetics import Kinetics

    legacy = Kinetics(reaction_entry_id=world.entry.id, scientific_origin=_computed(), a=1.0e-12, a_units=None)
    db_session.add(legacy)
    db_session.flush()
    result = select(db_session, world.entry)
    assert result.outcome is Outcome.policy_preferred
    assert result.assessment.unresolved_refs == (legacy.public_ref,)
    assert "unresolved" in result.assessment.notes[0]


def _computed():
    from app.db.models.common import ScientificOriginKind

    return ScientificOriginKind.computed


def test_selection_changes_nothing(db_session, world, two):
    from sqlalchemy import func
    from sqlalchemy import select as sql_select

    from app.db.models.kinetics import Kinetics, KineticsDetermination

    def counts():
        return (
            db_session.scalar(sql_select(func.count()).select_from(Kinetics)),
            db_session.scalar(sql_select(func.count()).select_from(KineticsDetermination)),
        )

    before = counts()
    select(db_session, world.entry)
    assert counts() == before and not db_session.new and not db_session.dirty and not db_session.deleted


def test_the_default_registry_is_used_when_none_is_given_and_a_snapshot_is_required_by_default(db_session, world, two):
    from app.services.read_snapshot import SnapshotNotConsistentError

    with pytest.raises(SnapshotNotConsistentError):
        select_reaction_entry_kinetics(db_session, reaction_entry_id=world.entry.id, request=REQUEST)
    result = select_reaction_entry_kinetics(db_session, reaction_entry_id=world.entry.id, request=REQUEST, require_snapshot=False)
    assert next(r["rule_id"] for r in result.decision.rules) == "K-XYG3-B3LYP-BARRIER"
    assert result.outcome is Outcome.incomparable_alternatives


# -- the manifest and its replay ---------------------------------------------------------------------------


def test_the_manifest_replays_without_the_database_and_reproduces_the_decision(db_session, world, two):
    result = select(db_session, world.entry)
    wire = json.loads(json.dumps(result.manifest))  # what a client would hold: plain JSON
    assert replay_matches(wire, rules=(RULE,))
    assert replay_decision(wire, rules=(RULE,)) == result.decision.to_dict()
    assert result.manifest["policy"] == {"name": "kinetics_method_preferred", "version": "1"}
    assert result.manifest["snapshot_isolation"] in {"read committed", "repeatable read", "serializable"}


def test_the_manifest_records_what_a_reader_needs_to_audit_the_decision(db_session, world, two):
    manifest = select(db_session, world.entry).manifest
    assert {"request", "subject", "population", "candidates", "determinations", "decision", "outcome", "disclosures"} <= set(manifest)
    assert manifest["request"]["direction"] == "forward" and manifest["request"]["temperature_min_k"] == 500.0
    assert manifest["population"]["assessed"] == 2 and manifest["population"]["visible_candidates"] == 2
    assert all({"assessment", "eligible", "kinetics_ref", "protocol"} <= set(c) for c in manifest["candidates"])
    assert manifest["decision"]["rules"][0]["rule_id"] == "TEST-R1"
    assert manifest["decision"]["edges"][0]["label"] == "expected-performance inference"
    assert manifest["decision"]["pair_checks"] and manifest["decision"]["rule_matches"][0]["applied"] is True


@pytest.mark.parametrize(
    "tamper,message",
    [
        (lambda m: m.update(manifest_format_version=2), "unknown manifest format"),
        (lambda m: m["policy"].update(version="2"), "this registry replays"),
        (lambda m: m["policy"].update(name="h298_method_preferred"), "this registry replays"),
        (lambda m: m["decision"]["rules"][0].update(version="9"), "not in the running registry"),
        (lambda m: m["decision"]["rules"][0].update(rule_id="OTHER"), "not in the running registry"),
        (lambda m: m["decision"]["rules"][0].update(status="inactive"), "a rule's status change is a new version"),
        (lambda m: m["decision"]["rules"][0].update(manifest_sha256="0" * 64), "different audited manifest"),
        (lambda m: m["candidates"][0].update(eligible=not m["candidates"][0]["eligible"]), "records eligible="),
        (lambda m: m["determinations"][0].update(representation_refs=["kin_x"]), "determinations are not the ones"),
    ],
    ids=["format", "policy_version", "policy_name", "rule_version", "rule_id", "rule_status", "rule_manifest", "eligibility", "determinations"],
)
def test_a_replay_refuses_a_manifest_the_running_registry_cannot_stand_behind(db_session, world, two, tamper, message):
    manifest = copy.deepcopy(select(db_session, world.entry).manifest)
    tamper(manifest)
    with pytest.raises(ReplayError, match=message):
        replay_decision(manifest, rules=(RULE,))


def test_a_tampered_candidate_changes_the_replayed_decision_so_the_match_check_fails(db_session, world, two):
    manifest = copy.deepcopy(select(db_session, world.entry).manifest)
    for c in manifest["candidates"]:
        if c["protocol"]["method_kind"] == "saddle_point_tst":
            c["protocol"]["method_kind"] = "variational_tst"  # now nothing is preferred
    assert replay_matches(manifest, rules=(RULE,)) is False
    assert replay_decision(manifest, rules=(RULE,))["outcome"] == "incomparable_alternatives"


def test_a_replayed_inactive_rule_set_reproduces_its_edge_free_decision(db_session, world, two):
    manifest = select(db_session, world.entry, rules=default_rules()).manifest
    assert replay_matches(manifest)  # default registry
    with pytest.raises(ReplayError, match="not in the running registry"):
        replay_decision(manifest, rules=(RULE,))


# -- what the manifest and the result may not carry ----------------------------------------------------------

#: Every integer a manifest may hold, by exact path: ordinals and counts, never a row id.
ALLOWED_INTEGER_PATHS = {
    "manifest_format_version",
    "subject.reactants[].charge", "subject.reactants[].multiplicity",
    "subject.products[].charge", "subject.products[].multiplicity",
    "request.max_candidates", "request.pressure", "population.kinetics_rows_for_entry",
    "population.visible_candidates", "population.assessed", "population.excluded_count",
    "candidates[].id_rank", "candidates[].arrhenius_terms", "candidates[].reactant_stoichiometries[]",
    "candidates[].product_stoichiometries[]", "candidates[].applicability.version", "candidates[].protocol.version",
    "candidates[].applicability.reaction_order", "determinations[].representation_count",
}


def _paths(value, prefix=""):
    found = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int):
        return {prefix}
    if isinstance(value, dict):
        for k, v in value.items():
            found |= _paths(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(value, list):
        for v in value:
            found |= _paths(v, f"{prefix}[]")
    return found


def test_the_manifest_holds_integers_only_at_exact_paths_and_never_a_row_id(db_session, world, two):
    manifest = select(db_session, world.entry).manifest
    paths = _paths(manifest)
    assert paths <= ALLOWED_INTEGER_PATHS, sorted(paths - ALLOWED_INTEGER_PATHS)
    assert {"candidates[].id_rank", "population.assessed"} <= paths


def test_the_manifest_names_records_by_public_ref_only(db_session, world, two):
    good, det_good, other, det_other = two
    text = json.dumps(select(db_session, world.entry).manifest)
    for row in (good, other, det_good, det_other):
        assert row.public_ref in text
    assert world.entry.public_ref in text
    for ref in (good.public_ref, other.public_ref):
        assert ref.startswith("kin_")
    for ref in (det_good.public_ref, det_other.public_ref):
        assert ref.startswith("kdet_")


def curated():
    return set_current_read_profile(
        ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
    )


def test_under_curated_a_hidden_record_never_enters_a_decision_or_its_manifest(db_session, world, two):
    good, det_good, other, det_other = two  # `good` is not reviewed: hidden under the curated floor
    token = curated()
    try:
        result = select(db_session, world.entry)
        text = json.dumps(result.manifest)
        assert good.public_ref not in text and det_good.public_ref not in text
        assert result.outcome is Outcome.sole_eligible_candidate and result.selected_determination_ref == det_other.public_ref
        assert result.manifest["population"]["kinetics_rows_for_entry"] == 1
        assert result.manifest["population"]["excluded_by_review"] == []
    finally:
        reset_current_read_profile(token)
    open_ = select(db_session, world.entry)
    assert open_.outcome is Outcome.policy_preferred and open_.manifest["population"]["kinetics_rows_for_entry"] == 2


# -- the facts a rule reads come from the record's own links ---------------------------------------------------


def test_the_energy_levels_are_the_records_own_declared_and_linked_calculations_and_nothing_borrowed(db_session, world):
    xyg3 = make_lot(db_session, method="XYG3", basis="6-311+G(3df,2p)")
    other = make_lot(db_session, method="B3LYP", basis="def2tzvp")
    declared_calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.h.id, lot_id=xyg3.id)
    linked_calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.h.id, lot_id=other.id)
    sibling_calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.h.id, lot_id=other.id)
    det = determination(db_session, world, "d")
    sibling = declared(db_session, world, determination(db_session, world, "sibling"),
                       children=lambda k: db_session.add(KineticsSourceCalculation(
                           kinetics_id=k.id, calculation_id=sibling_calc.id, role=KineticsCalculationRole.ts_energy)))

    def attach(k):
        k.protocol_declaration = {
            "version": 1, "method_kind": "saddle_point_tst",
            "supporting_calculations": [
                {"calculation_ref": declared_calc.public_ref, "purpose": "electronic_energy"},
                {"calculation_ref": linked_calc.public_ref, "purpose": "geometry"},
            ],
        }
        db_session.add(KineticsSourceCalculation(kinetics_id=k.id, calculation_id=linked_calc.id, role=KineticsCalculationRole.ts_energy))
        db_session.add(KineticsSourceCalculation(kinetics_id=k.id, calculation_id=linked_calc.id, role=KineticsCalculationRole.freq))
        db_session.flush()

    mine = declared(db_session, world, det, children=attach)
    scan = scan_population(db_session, reaction_entry_id=world.entry.id, request=REQUEST)
    candidates = {c.kinetics_ref: c for c in load_population(db_session, scan).candidates.values()}
    levels = candidates[mine.public_ref].energy_levels
    assert [(lv["source"], lv["role"], lv["method"], lv["basis"]) for lv in levels] == [
        ("protocol_declared", "electronic_energy", "XYG3", "6-311+G(3df,2p)"),  # only the electronic-energy purpose
        ("source_link", "ts_energy", "B3LYP", "def2tzvp"),  # energy roles only: the freq link is not an energy
    ]
    assert all(lv["calculation_ref"].startswith("calc_") for lv in levels)
    # The sibling's own link is read for the sibling and never for this record.
    assert [(lv["source"], lv["method"]) for lv in candidates[sibling.public_ref].energy_levels] == [("source_link", "B3LYP")]
    assert candidates[sibling.public_ref].energy_levels != levels


def test_a_decision_manifest_passes_through_the_selection_module_unchanged_by_a_registry_swap(db_session, world, two, monkeypatch):
    seen = {}
    real = selection_module.decide

    def spy(candidates, **kwargs):
        seen["rules"] = [r.rule_id for r in kwargs["rules"]]
        return real(candidates, **kwargs)

    monkeypatch.setattr(selection_module, "decide", spy)
    select_reaction_entry_kinetics(db_session, reaction_entry_id=world.entry.id, request=REQUEST, require_snapshot=False)
    assert seen["rules"] == [r.rule_id for r in default_rules()]
    select(db_session, world.entry)
    assert seen["rules"] == ["TEST-R1"]


def test_an_ineligible_record_in_a_manifest_does_not_take_part_in_its_replay(db_session, world, two):
    from app.db.models.kinetics import Kinetics

    legacy = Kinetics(reaction_entry_id=world.entry.id, scientific_origin=_computed(), a=1.0e-12, a_units=None)
    db_session.add(legacy)
    db_session.flush()
    result = select(db_session, world.entry)
    wire = json.loads(json.dumps(result.manifest))
    assert [c["eligible"] for c in wire["candidates"]].count(False) == 1
    assert replay_matches(wire, rules=(RULE,))


def test_the_manifest_lists_candidates_in_id_order_whatever_order_the_result_holds_them(db_session, world, two):
    import dataclasses

    from app.services.kinetics_selection.manifest import build_manifest

    result = select(db_session, world.entry)
    shuffled = dataclasses.replace(result.assessment, candidates=tuple(reversed(result.assessment.candidates)))
    manifest = build_manifest(shuffled, result.decision)
    ranks = [c["id_rank"] for c in manifest["candidates"]]
    assert len(ranks) == 2 and ranks == sorted(ranks)


def test_an_edited_top_level_outcome_is_not_a_match_even_when_the_decision_is_untouched(db_session, world, two):
    manifest = json.loads(json.dumps(select(db_session, world.entry).manifest))
    assert replay_matches(manifest, rules=(RULE,)) and manifest["outcome"] == "policy_preferred"
    manifest["outcome"] = "incomparable_alternatives"
    assert manifest["decision"]["outcome"] == "policy_preferred"
    assert not replay_matches(manifest, rules=(RULE,))
