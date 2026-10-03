"""``select_h298`` end to end over stored records: eligibility, E1, outcomes, the cap, and replay."""

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
from app.services.thermo_selection.rules import E1Rule
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_conformer_group,
    make_lot,
    make_species,
    make_species_entry,
    set_review,
)
from tests.services.thermo_selection._support import BY_NAME, T0, make_thermo, protocol, species_entry_for
from tests.services.thermo_selection.test_engine import LabelRule

EQUILIBRIUM = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)


@pytest.fixture
def methane(db_session):
    return species_entry_for(db_session, "Methane")


def run(session, entry, request=EQUILIBRIUM, **kw):
    return select_h298(session, species_entry_id=entry.id, request=request, **kw)


def g4(session, entry, **kw):
    kw.setdefault("proto", protocol("g4"))
    return make_thermo(session, entry, **kw)


def g3(session, entry, **kw):
    kw.setdefault("proto", protocol("g3"))
    return make_thermo(session, entry, **kw)


# -- E1 end to end --------------------------------------------------------------------------------------


def test_an_older_qualifying_g4_is_selected_over_a_newer_qualifying_g3_while_browse_still_lists_g3_first(
    db_session, methane
):
    old_g4 = g4(db_session, methane, age_days=500, status=S.approved)
    new_g3 = g3(db_session, methane, age_days=1, status=S.approved)

    browse = get_species_thermo(
        db_session, species_entry_id=methane.id, request=ThermoReadRequest(collapse=CollapseMode.first)
    )
    assert browse.records[0].thermo_id == new_g3.id  # the existing recency-based browse order, untouched

    result = run(db_session, methane)
    assert result.outcome is Outcome.policy_preferred
    assert result.selected_ref == old_g4.public_ref
    assert result.decision.fronts == ((old_g4.public_ref,), (new_g3.public_ref,))
    assert result.decision.edges[0].rule_id == "E1" and result.decision.edges[0].rule_version == "1.0.0"
    assert result.decision.administrative_order[0] == new_g3.public_ref  # administrative order alone says G3


def test_a_g4_with_an_explicit_empty_departures_list_is_required_and_a_missing_one_does_not_activate_e1(
    db_session, methane
):
    stated = g4(db_session, methane, age_days=9)
    unstated = g3(db_session, methane, proto=protocol("g3", departures=None))
    result = run(db_session, methane)
    assert result.outcome is Outcome.incomparable_alternatives
    assert not result.decision.edges
    assert set(result.decision.fronts[0]) == {stated.public_ref, unstated.public_ref}


CHALLENGERS = {
    "modified-recipe": protocol("g4", departures=[{"component": "geometry", "description": "used M06-2X"}]),
    "g4mp2": protocol("g4mp2"),
    "g4-complete": protocol("g4_complete"),
    "other-named-g3b3": protocol("other", other_name="G3B3"),
    "no-protocol": None,
}


@pytest.mark.parametrize("proto", CHALLENGERS.values(), ids=CHALLENGERS.keys())
def test_e1_does_not_activate_for_a_modified_recipe_g4mp2_g4_complete_or_missing_provenance(
    db_session, methane, proto
):
    challenger = make_thermo(db_session, methane, proto=proto, age_days=300)
    standard_g3 = g3(db_session, methane, age_days=1)
    result = run(db_session, methane)
    assert not result.decision.edges
    assert result.outcome is Outcome.incomparable_alternatives
    assert set(result.decision.fronts[0]) == {challenger.public_ref, standard_g3.public_ref}


def test_a_species_outside_the_manifest_gets_no_e1_edge(db_session):
    ethanol = make_species_entry(db_session, make_species(db_session, smiles="CCO", inchi_key="LFQSCWFLJHTTHZ-UHFFFAOYSA-N"))
    g4(db_session, ethanol, age_days=400)
    g3(db_session, ethanol, age_days=1)
    result = run(db_session, ethanol)
    assert result.outcome is Outcome.incomparable_alternatives
    assert result.decision.rule_matches[0]["why"] == "outside_rule_scope"


def test_only_singlet_methylene_is_in_e1_scope(db_session):
    singlet = species_entry_for(db_session, "Methylene")
    triplet = species_entry_for(db_session, "Methylene", multiplicity=3)
    assert singlet.id != triplet.id
    for entry in (singlet, triplet):
        g4(db_session, entry, age_days=100)
        g3(db_session, entry, age_days=1)
    assert run(db_session, singlet).outcome is Outcome.policy_preferred
    assert run(db_session, triplet).outcome is Outcome.incomparable_alternatives


def test_cyclooctatetraene_with_another_stereo_layer_in_its_inchikey_still_matches(db_session):
    m = BY_NAME["1,3,5,7-Cyclooctatetraene"]
    species = make_species(db_session, smiles=m["smiles"], inchi_key="KDUIUFJBNGTBMD-DLMDZQPMSA-N")
    entry = make_species_entry(db_session, species)
    assert species.inchi_key != m["inchikey"]
    winner = g4(db_session, entry, age_days=100)
    g3(db_session, entry, age_days=1)
    result = run(db_session, entry)
    assert result.outcome is Outcome.policy_preferred and result.selected_ref == winner.public_ref


# -- competitors ----------------------------------------------------------------------------------------


def test_an_experimental_record_and_a_record_without_protocol_prevent_a_unique_method_winner(db_session, methane):
    a = g4(db_session, methane, age_days=50)
    g3(db_session, methane, age_days=10)
    exp = make_thermo(db_session, methane, origin=ScientificOriginKind.experimental, proto=None, age_days=3)
    blank = make_thermo(db_session, methane, proto=None, age_days=2)
    result = run(db_session, methane)
    assert result.outcome is Outcome.incomparable_alternatives and result.selected_ref is None
    assert set(result.decision.fronts[0]) == {a.public_ref, exp.public_ref, blank.public_ref}
    assert result.decision.administrative_first["basis"].startswith("administrative order among unresolved")


def test_a_competitor_that_is_physically_unresolved_does_not_compete_but_is_disclosed(db_session, methane):
    a = g4(db_session, methane, age_days=50)
    b = g3(db_session, methane, age_days=10)
    unknown = g3(db_session, methane, age_days=1, phase=None)
    result = run(db_session, methane)
    assert result.outcome is Outcome.policy_preferred and result.selected_ref == a.public_ref
    assert result.unresolved_refs == (unknown.public_ref,)
    assert b.public_ref in result.decision.fronts[1]


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
        select_h298(db_session, species_entry_id=-1, request=EQUILIBRIUM)


# -- review floors ---------------------------------------------------------------------------------------


def test_review_floors_change_eligibility_and_the_default_floor_lets_an_unreviewed_g4_win(db_session, methane):
    g4_under_review = g4(db_session, methane, age_days=100, status=S.under_review)
    g3_approved = g3(db_session, methane, age_days=1, status=S.approved)

    permissive = run(db_session, methane)
    assert permissive.outcome is Outcome.policy_preferred and permissive.selected_ref == g4_under_review.public_ref

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


def test_a_linked_level_naming_another_recipe_stops_e1_for_that_record(db_session, methane):
    lot = make_lot(db_session, method="G4MP2", basis=None)
    declared_g4 = g4(db_session, methane, age_days=9, energy_lot_id=lot.id)
    g3(db_session, methane, age_days=1)
    result = run(db_session, methane)
    assert not result.decision.edges
    reasons = next(m for m in result.decision.rule_matches[0]["candidates"] if m["thermo_ref"] == declared_g4.public_ref)
    assert "linked_level_names_other_recipe:g4mp2" in reasons["preferred"]["reasons"]


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


# -- conflicts through the service ------------------------------------------------------------------------


def test_opposing_rules_block_selection_through_the_service(db_session, methane):
    a = g4(db_session, methane, proto=protocol("g4", label="a"), age_days=5)
    b = g4(db_session, methane, proto=protocol("g4", label="b"), age_days=1)
    rules = (LabelRule("T1", {"a"}, {"b"}), LabelRule("T2", {"b"}, {"a"}))
    result = run(db_session, methane, rules=rules)
    assert result.outcome is Outcome.policy_conflict and result.selected_ref is None
    assert {a.public_ref, b.public_ref} == set(result.decision.administrative_order)


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


def test_the_manifest_records_inputs_rules_comparisons_and_order_with_public_refs_only(db_session, methane):
    result = _scenario(db_session, methane)
    m = result.manifest
    assert m["policy"] == {"name": "h298_method_preferred", "version": "1"}
    assert m["request"]["administrative_policy"] == "latest" and m["request"]["quantity"] == "formation_enthalpy_298k"
    assert m["decision"]["rules"][0]["rule_id"] == "E1" and m["decision"]["rules"][0]["version"] == "1.0.0"
    assert m["decision"]["rules"][0]["manifest"]["manifest_version"] == "1.0.0"
    assert m["decision"]["edges"] and m["decision"]["fronts"] and m["decision"]["administrative_order"]
    assert m["decision"]["rule_matches"][0]["candidates"]
    assert m["subject"]["species_entry_ref"] == methane.public_ref

    forbidden = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if (key == "id" or key.endswith("_id")) and key != "rule_id":
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


def test_a_manifest_whose_eligibility_was_tampered_with_no_longer_replays(db_session, methane):
    result = _scenario(db_session, methane)
    tampered = json.loads(json.dumps(result.manifest))
    for c in tampered["candidates"]:
        if c["assessment"]["answer_representation"] and c["protocol"] and c["protocol"]["recipe"]["name"] == "g4":
            c["eligible"] = False
    assert not replay_matches(tampered)


def test_swapped_timestamps_reorder_within_a_front_but_cannot_move_a_record_across_fronts(db_session, methane):
    result = _scenario(db_session, methane)
    recorded = result.manifest["decision"]
    swapped = json.loads(json.dumps(result.manifest))
    for c in swapped["candidates"]:
        recipe = c["protocol"]["recipe"]["name"] if c["protocol"] else None
        c["created_at"] = {"g4": "2030-01-01T00:00:00", None: "2001-01-01T00:00:00", "g3": "2000-01-01T00:00:00"}[recipe]
    replayed = replay_decision(swapped)
    assert replayed["edges"] == recorded["edges"]
    assert replayed["fronts"][1] == recorded["fronts"][1]  # the G3 stays behind the G4 ...
    assert set(replayed["fronts"][0]) == set(recorded["fronts"][0])
    assert replayed["fronts"][0] != recorded["fronts"][0]  # ... while the order inside the first front follows time


def test_replay_refuses_a_rule_version_the_registry_does_not_carry(db_session, methane):
    manifest = json.loads(json.dumps(_scenario(db_session, methane).manifest))
    newer = E1Rule()
    newer.version = "2.0.0"
    with pytest.raises(ReplayError, match="E1 version 1.0.0"):
        replay_decision(manifest, rules=(newer,))
    manifest["policy"]["version"] = "0"
    with pytest.raises(ReplayError, match="policy"):
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
