"""``select_h298`` end to end over stored records: eligibility, outcomes, display order, the cap, and replay."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.common import (
    CalculationQuality,
    EnthalpyReferenceKind,
    PhaseKind,
    ScientificOriginKind,
    SubmissionRecordType,
    ThermoCalculationRole,
    ThermoTargetKind,
)
from app.db.models.common import (
    RecordReviewStatus as S,
)
from app.db.models.record_review import RecordReview
from app.db.models.thermo import Thermo
from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.services.scientific_read.thermo import get_species_thermo
from app.services.thermo_selection import (
    H298Request,
    Outcome,
    ReplayError,
    replay_decision,
    replay_matches,
    select_h298,
)
from app.services.thermo_selection.models import Applicability
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_conformer_group,
    make_lot,
    set_review,
)
from tests.services.thermo_selection._support import T0, make_thermo, protocol, species_entry_for

EQUILIBRIUM = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)


@pytest.fixture
def methane(db_session):
    return species_entry_for(db_session, "Methane")


def run(session, entry, request=EQUILIBRIUM, **kw):
    kw.setdefault("require_snapshot", False)  # the harness holds one outer transaction
    return select_h298(session, species_entry_id=entry.id, request=request, **kw)


def g4(session, entry, **kw):
    kw.setdefault("proto", protocol("g4"))
    return make_thermo(session, entry, **kw)


def g3(session, entry, **kw):
    kw.setdefault("proto", protocol("g3"))
    return make_thermo(session, entry, **kw)


# -- no method ranking end to end -------------------------------------------------------------------------


def test_an_older_g4_and_a_newer_g3_are_incomparable_and_listed_in_display_order_like_browse(db_session, methane):
    old_g4 = g4(db_session, methane, age_days=500, status=S.approved)
    new_g3 = g3(db_session, methane, age_days=1, status=S.approved)

    browse = get_species_thermo(
        db_session, species_entry_id=methane.id, request=ThermoReadRequest(collapse=CollapseMode.first)
    )
    assert browse.records[0].thermo_id == new_g3.id

    result = run(db_session, methane)
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert result.decision.fronts == ((new_g3.public_ref, old_g4.public_ref),)  # one front, newest deposited first
    assert result.decision.administrative_order == (new_g3.public_ref, old_g4.public_ref)
    assert set(result.decision.to_dict()) == {
        "outcome", "selected_ref", "basis", "fronts", "administrative_order", "administrative_first"}


@pytest.mark.parametrize("proto", [protocol("g4"), protocol("g4mp2"), protocol("g4", departures=None), None])
def test_no_declared_recipe_changes_the_outcome(db_session, methane, proto):
    a = make_thermo(db_session, methane, proto=proto, age_days=300)
    b = g3(db_session, methane, age_days=1)
    result = run(db_session, methane)
    assert result.outcome is Outcome.incomparable_alternatives
    assert set(result.decision.fronts[0]) == {a.public_ref, b.public_ref}


# -- competitors ----------------------------------------------------------------------------------------


def test_an_experimental_record_and_a_record_without_protocol_prevent_a_unique_method_winner(db_session, methane):
    a = g4(db_session, methane, age_days=50)
    b = g3(db_session, methane, age_days=10)
    exp = make_thermo(db_session, methane, origin=ScientificOriginKind.experimental, proto=None, age_days=3)
    blank = make_thermo(db_session, methane, proto=None, age_days=2)
    result = run(db_session, methane)
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert set(result.decision.fronts[0]) == {a.public_ref, b.public_ref, exp.public_ref, blank.public_ref}
    assert result.decision.administrative_first["basis"].startswith("first in display order")


def test_a_competitor_that_is_physically_unresolved_does_not_compete_but_is_disclosed(db_session, methane):
    a = g4(db_session, methane, age_days=50)
    b = g3(db_session, methane, age_days=10)
    unknown = g3(db_session, methane, age_days=1, phase=None)
    result = run(db_session, methane)
    assert result.outcome is Outcome.incomparable_alternatives
    assert set(result.decision.fronts[0]) == {a.public_ref, b.public_ref}
    assert result.unresolved_refs == (unknown.public_ref,)
    assert unknown.public_ref not in result.decision.administrative_order


def test_a_sole_eligible_record_is_not_a_comparative_win_and_discloses_unresolved_ones(db_session, methane):
    only = g3(db_session, methane, age_days=5)
    unresolved = make_thermo(db_session, methane, target=None, proto=None)
    result = run(db_session, methane)
    assert result.outcome is Outcome.sole_eligible_candidate and result.selected_ref == only.public_ref
    assert result.unresolved_refs == (unresolved.public_ref,)
    assert result.notes and "unresolved" in result.notes[0]


def test_nothing_eligible_is_no_applicable_candidate_with_reasons(db_session, methane):
    make_thermo(db_session, methane, phase=PhaseKind.aqueous)
    make_thermo(db_session, methane, target=None)
    result = run(db_session, methane)
    assert result.outcome is Outcome.no_applicable_candidate and result.selected_ref is None
    assert {a.applicability for a in result.assessments} == {Applicability.incompatible, Applicability.unresolved}


def test_an_entry_with_no_thermo_is_no_applicable_candidate(db_session, methane):
    result = run(db_session, methane)
    assert result.outcome is Outcome.no_applicable_candidate and result.assessments == ()


def test_an_unknown_entry_is_not_found(db_session):
    with pytest.raises(NotFoundError):
        select_h298(db_session, species_entry_id=-1, request=EQUILIBRIUM, require_snapshot=False)


# -- review floors ---------------------------------------------------------------------------------------


def test_review_floors_change_eligibility_and_the_default_floor_lists_an_under_review_record(db_session, methane):
    g4_under_review = g4(db_session, methane, age_days=100, status=S.under_review)
    g3_approved = g3(db_session, methane, age_days=1, status=S.approved)

    permissive = run(db_session, methane)
    assert permissive.outcome is Outcome.incomparable_alternatives
    assert permissive.decision.administrative_order == (g3_approved.public_ref, g4_under_review.public_ref)

    strict = run(db_session, methane, H298Request(
        target_kind=ThermoTargetKind.equilibrium_ensemble, min_review_status=S.approved))
    assert strict.outcome is Outcome.sole_eligible_candidate and strict.selected_ref == g3_approved.public_ref
    assert strict.manifest["population"]["excluded_by_review"] == [
        {"thermo_ref": g4_under_review.public_ref, "review_status": "under_review", "reason": "below_review_floor"}
    ]


def test_rejected_and_deprecated_records_are_never_selected_even_when_they_would_win(db_session, methane):
    rejected = g4(db_session, methane, age_days=300, status=S.rejected)
    deprecated = g4(db_session, methane, age_days=200, status=S.deprecated)
    kept = g3(db_session, methane, age_days=1)
    result = run(db_session, methane)
    assert result.outcome is Outcome.sole_eligible_candidate and result.selected_ref == kept.public_ref
    reasons = {e["thermo_ref"]: e["reason"] for e in result.manifest["population"]["excluded_by_review"]}
    assert reasons == {rejected.public_ref: "terminal_review_status", deprecated.public_ref: "terminal_review_status"}


# -- targets ---------------------------------------------------------------------------------------------


def test_single_conformer_and_equilibrium_requests_select_different_records(db_session, methane):
    group = make_conformer_group(db_session, methane)
    ensemble = g4(db_session, methane, age_days=3)
    conformer = g4(db_session, methane, age_days=2, target=ThermoTargetKind.single_conformer, group_id=group.id)
    as_ensemble = run(db_session, methane)
    as_conformer = run(db_session, methane, H298Request(
        target_kind=ThermoTargetKind.single_conformer, conformer_group_id=group.id))
    assert as_ensemble.selected_ref == ensemble.public_ref and as_conformer.selected_ref == conformer.public_ref
    assert as_conformer.manifest["request"]["target"] == {
        "kind": "single_conformer", "conformer_group_ref": group.public_ref}


def test_a_conformer_group_of_another_species_entry_is_refused(db_session, methane):
    other = species_entry_for(db_session, "Propane")
    group = make_conformer_group(db_session, other)
    with pytest.raises(CodedValueError) as raised:
        run(db_session, methane, H298Request(target_kind=ThermoTargetKind.single_conformer, conformer_group_id=group.id))
    assert raised.value.code == "thermo_target_group_owner_mismatch"


# -- never borrow -----------------------------------------------------------------------------------------


def test_a_records_recipe_evidence_is_only_what_it_links_not_a_siblings(db_session, methane):
    # A sibling declares G4 and links a G4 composite level; the record under test links nothing and says nothing.
    sibling = g4(db_session, methane, age_days=9)
    lot = make_lot(db_session, method="G4", basis=None)
    calc = make_calculation(db_session, species_entry_id=methane.id, lot_id=lot.id)
    attach_thermo_source_calculation(db_session, thermo=sibling, calculation=calc, role=ThermoCalculationRole.composite)
    bare = make_thermo(db_session, methane, proto=None, age_days=1)
    result = run(db_session, methane)
    by_ref = {c["thermo_ref"]: c for c in result.manifest["candidates"]}
    assert by_ref[sibling.public_ref]["linked_recipe_keys"] == ["g4"]
    assert by_ref[bare.public_ref]["linked_recipe_keys"] == [] and by_ref[bare.public_ref]["protocol_state"] == "absent"





def test_a_blocking_validation_failure_removes_a_candidate_from_the_competition(db_session, methane):
    bad = g4(db_session, methane, age_days=9)
    calc = make_calculation(db_session, species_entry_id=methane.id)
    calc.quality = CalculationQuality.rejected
    attach_thermo_source_calculation(db_session, thermo=bad, calculation=calc, role=ThermoCalculationRole.opt)
    kept = g3(db_session, methane, age_days=1)
    result = run(db_session, methane)
    assert result.outcome is Outcome.sole_eligible_candidate and result.selected_ref == kept.public_ref
    assessed = {a.thermo_ref: a for a in result.assessments}
    assert assessed[bad.public_ref].blocking and not assessed[bad.public_ref].physically_eligible





# -- the cap ----------------------------------------------------------------------------------------------


def _bulk(session, entry, n):
    rows = [
        Thermo(species_entry_id=entry.id, scientific_origin=ScientificOriginKind.computed, h298_kj_mol=-74.6 - i * 1e-3,
               enthalpy_reference_kind=EnthalpyReferenceKind.formation_298k,
               phase=PhaseKind.gas, thermodynamic_target_kind=ThermoTargetKind.equilibrium_ensemble, created_at=T0)
        for i in range(n)
    ]
    session.add_all(rows)
    session.flush()
    return rows


def test_a_population_of_501_is_refused_and_selects_nothing_from_a_prefix(db_session, methane):
    _bulk(db_session, methane, 501)
    result = run(db_session, methane)
    assert result.outcome is Outcome.bounded_search_exceeded
    assert result.selected_ref is None and result.assessments == () and result.decision is None
    assert result.manifest["population"]["visible_candidates"] == 501
    assert result.manifest["request"]["max_candidates"] == 500
    assert result.manifest["candidates"] == []


def test_a_population_of_exactly_500_is_fully_assessed(db_session, methane):
    _bulk(db_session, methane, 500)
    result = run(db_session, methane)
    assert len(result.assessments) == 500
    assert result.outcome is Outcome.incomparable_alternatives


def test_the_cap_counts_only_the_visible_population(db_session, methane):
    rows = _bulk(db_session, methane, 502)
    for row in rows[:3]:
        set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=row.id, status=S.rejected)
    result = run(db_session, methane)
    assert result.outcome is not Outcome.bounded_search_exceeded and len(result.assessments) == 499


# -- the decision manifest ----------------------------------------------------------------------------------


def _scenario(db_session, methane):
    g4(db_session, methane, age_days=500, status=S.approved)
    g3(db_session, methane, age_days=1, status=S.approved)
    make_thermo(db_session, methane, proto=None, age_days=2, origin=ScientificOriginKind.experimental)
    g3(db_session, methane, age_days=3, status=S.rejected)
    return run(db_session, methane, H298Request(
        target_kind=ThermoTargetKind.equilibrium_ensemble, admin_policy=SelectionPolicy.latest))


def test_the_decision_manifest_replays_without_the_database(db_session, methane):
    result = _scenario(db_session, methane)
    manifest = json.loads(json.dumps(result.manifest))  # JSON-ready, and nothing is lost through JSON
    assert replay_decision(manifest) == manifest["decision"]
    assert replay_matches(manifest) and replay_matches(result.manifest)


def test_the_manifest_records_inputs_and_order_with_public_refs_only(db_session, methane):
    result = _scenario(db_session, methane)
    m = result.manifest
    assert m["policy"] == {"name": "h298_selection", "version": 1}
    assert m["format"] == {"name": "thermo_selection_manifest", "version": 2}
    assert m["request"]["administrative_policy"] == "latest" and m["request"]["quantity"] == "formation_enthalpy_298k"
    assert m["decision"]["fronts"] and m["decision"]["administrative_order"]
    assert not {"rules", "rule_matches", "edges", "overridden_edges", "opposing_pairs", "cycles"} & set(m["decision"])
    assert m["subject"]["species_entry_ref"] == methane.public_ref

    forbidden = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "id" or key.endswith("_id"):
                    forbidden.append(path + key)
                walk(value, f"{path}{key}.")
        elif isinstance(node, list):
            for item in node:
                walk(item, path)

    walk(m)
    assert not forbidden
    real_refs = set(db_session.scalars(select(Thermo.public_ref).where(Thermo.species_entry_id == methane.id)))
    listed = {c["thermo_ref"] for c in m["candidates"]} | {e["thermo_ref"] for e in m["population"]["excluded_by_review"]}
    assert listed == real_refs  # every record is accounted for, by public ref, once


def test_a_manifest_whose_eligibility_flag_contradicts_its_assessment_is_refused(db_session, methane):
    manifest = json.loads(json.dumps(_scenario(db_session, methane).manifest))
    for c in manifest["candidates"]:
        if c["protocol"] and c["protocol"]["recipe"]["name"] == "g4":
            c["eligible"] = False
    with pytest.raises(ReplayError, match="physically_eligible"):
        replay_decision(manifest)


def test_a_manifest_whose_eligibility_and_assessment_were_both_edited_no_longer_replays(db_session, methane):
    tampered = json.loads(json.dumps(_scenario(db_session, methane).manifest))
    for c in tampered["candidates"]:
        if c["protocol"] and c["protocol"]["recipe"]["name"] == "g4":
            c["eligible"] = False
            c["assessment"]["physically_eligible"] = False
    assert not replay_matches(tampered)


def test_swapped_timestamps_reorder_the_one_front(db_session, methane):
    result = _scenario(db_session, methane)
    recorded = result.manifest["decision"]
    swapped = json.loads(json.dumps(result.manifest))
    for c in swapped["candidates"]:
        recipe = c["protocol"]["recipe"]["name"] if c["protocol"] else None
        c["created_at"] = {"g4": "2030-01-01T00:00:00", None: "2001-01-01T00:00:00", "g3": "2000-01-01T00:00:00"}[recipe]
    replayed = replay_decision(swapped)
    assert len(replayed["fronts"]) == 1
    assert set(replayed["fronts"][0]) == set(recorded["fronts"][0])
    assert replayed["fronts"][0] != recorded["fronts"][0]  # the order follows deposition time


def test_replay_refuses_a_policy_or_format_the_registry_does_not_carry(db_session, methane):
    manifest = json.loads(json.dumps(_scenario(db_session, methane).manifest))
    manifest["policy"]["version"] = 0
    with pytest.raises(ReplayError, match="policy"):
        replay_decision(manifest)
    manifest = json.loads(json.dumps(_scenario(db_session, methane).manifest))
    manifest["format"]["version"] = 1  # the shape before the rules were removed
    with pytest.raises(ReplayError, match="unknown manifest format"):
        replay_decision(manifest)


def test_selection_persists_nothing_and_leaves_curation_alone(db_session, methane):
    g4(db_session, methane, age_days=9, status=S.approved)
    g3(db_session, methane, age_days=1, status=S.under_review)
    db_session.flush()
    before = (
        db_session.scalar(select(func.count()).select_from(Thermo)),
        db_session.scalar(select(func.count()).select_from(RecordReview)),
    )
    run(db_session, methane)
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    after = (
        db_session.scalar(select(func.count()).select_from(Thermo)),
        db_session.scalar(select(func.count()).select_from(RecordReview)),
    )
    assert before == after


def test_unresolved_and_unsupported_candidates_are_disclosed_under_every_outcome(db_session, methane):
    from tests.services.scientific_read._factories import attach_thermo_wilhoit

    a = g4(db_session, methane, age_days=50)
    g3(db_session, methane, age_days=10)
    unresolved = g3(db_session, methane, age_days=1, phase=None)
    wilhoit = make_thermo(db_session, methane, h298=None)
    attach_thermo_wilhoit(db_session, thermo=wilhoit)

    preferred = run(db_session, methane)
    assert preferred.outcome is Outcome.incomparable_alternatives and a.public_ref in preferred.decision.fronts[0]
    assert preferred.unresolved_refs == (unresolved.public_ref,) and preferred.unsupported_refs == (wilhoit.public_ref,)
    assert preferred.notes and "1 candidate(s) are unresolved and 1 unsupported" in preferred.notes[0]
    assert preferred.manifest["disclosures"] == {
        "unresolved_refs": [unresolved.public_ref], "unsupported_refs": [wilhoit.public_ref]}

    make_thermo(db_session, methane, proto=None, age_days=2)  # one more competitor
    alternatives = run(db_session, methane)
    assert alternatives.outcome is Outcome.incomparable_alternatives
    assert alternatives.notes == preferred.notes


def test_an_outcome_with_nothing_unresolved_or_unsupported_carries_no_note(db_session, methane):
    g3(db_session, methane)
    assert run(db_session, methane).notes == ()


def test_a_siblings_declared_energy_level_is_not_borrowed_by_a_record_that_links_none(db_session, methane):
    # The sibling's G4 is linked through energy_level_of_theory_id (no source calculation at all).
    lot = make_lot(db_session, method="G4", basis=None)
    sibling = g4(db_session, methane, age_days=9, energy_lot_id=lot.id)
    bare = make_thermo(db_session, methane, proto=None, age_days=1)
    other = g3(db_session, methane, age_days=2)
    by_ref = {c["thermo_ref"]: c for c in run(db_session, methane).manifest["candidates"]}
    assert by_ref[sibling.public_ref]["linked_recipe_keys"] == ["g4"]
    assert by_ref[bare.public_ref]["linked_recipe_keys"] == []
    assert by_ref[other.public_ref]["linked_recipe_keys"] == []


def test_an_edited_top_level_outcome_is_not_a_match_even_when_the_decision_is_untouched(db_session, methane):
    manifest = json.loads(json.dumps(_scenario(db_session, methane).manifest))
    assert replay_matches(manifest)
    other = "incomparable_alternatives" if manifest["outcome"] != "incomparable_alternatives" else "sole_eligible_candidate"
    manifest["outcome"] = other
    assert manifest["decision"]["outcome"] != other
    assert not replay_matches(manifest)
