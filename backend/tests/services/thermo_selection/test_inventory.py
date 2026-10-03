"""The coverage inventory counts what stored records can answer, why not, and writes nothing."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, text

from app.db.models.common import RecordReviewStatus as S
from app.db.models.common import ScientificOriginKind
from app.db.models.record_review import RecordReview
from app.db.models.thermo import Thermo
from app.services.thermo_selection.inventory import h298_coverage_inventory
from tests.services.scientific_read._factories import attach_thermo_points, attach_thermo_wilhoit
from tests.services.thermo_selection._support import make_thermo, protocol, species_entry_for


@pytest.fixture
def seeded(db_session):
    """A small corpus whose inventory is known by construction. Counts are the *increment* over what exists."""
    baseline = h298_coverage_inventory(db_session)
    methane = species_entry_for(db_session, "Methane")
    propane = species_entry_for(db_session, "Propane")
    make_thermo(db_session, methane, proto=protocol("g4"), status=S.approved)                 # E1 preferred side
    make_thermo(db_session, methane, proto=protocol("g3"), status=S.approved)                 # E1 yielding side
    make_thermo(db_session, methane, proto=None)                                              # no protocol: unknown
    make_thermo(db_session, methane, proto=protocol("g4", departures=None))                   # departures unknown
    make_thermo(db_session, methane, target=None)                                             # target undeclared
    cp_only = make_thermo(db_session, methane, h298=None)
    attach_thermo_points(db_session, thermo=cp_only, temperatures_k=[298.15], cp_j_mol_k=[35.7])
    wilhoit = make_thermo(db_session, methane, h298=None)
    attach_thermo_wilhoit(db_session, thermo=wilhoit)
    make_thermo(db_session, methane, origin=ScientificOriginKind.experimental, proto=None)
    make_thermo(db_session, propane, proto=protocol("g4"))
    db_session.flush()
    return baseline


def delta(after, before, *path):
    def dig(report):
        for key in path:
            report = report.get(key, {}) if isinstance(report, dict) else {}
        return report if isinstance(report, int) else 0

    return dig(after) - dig(before)


def test_inventory_counts_applicability_and_the_reasons_behind_it(db_session, seeded):
    after = h298_coverage_inventory(db_session)
    assert delta(after, seeded, "thermo_records") == 9
    assert delta(after, seeded, "h298_applicability", "applicable") == 6
    assert delta(after, seeded, "h298_applicability", "unresolved") == 1
    assert delta(after, seeded, "h298_applicability", "unsupported") == 1
    assert delta(after, seeded, "h298_applicability", "incompatible") == 1
    assert delta(after, seeded, "h298_not_applicable_reasons", "thermodynamic_target_not_declared") == 1
    assert delta(after, seeded, "h298_not_applicable_reasons", "wilhoit_not_evaluated") == 1
    assert delta(after, seeded, "h298_not_applicable_reasons", "no_enthalpy_point_at_298_15K") == 1
    # Seven records hold a scalar and so have an answer; the one with an undeclared target is still unresolved.
    assert delta(after, seeded, "h298_answered_by_representation", "h298") == 7


def test_inventory_reports_how_many_records_each_rule_side_can_act_on_and_what_is_missing(db_session, seeded):
    after = h298_coverage_inventory(db_session)["rules"]["E1"]
    before = seeded["rules"]["E1"]
    assert delta(after, before, "scope", "true") == 9    # eight methane records and one propane record, both in the manifest
    assert after["preferred"].get("true", 0) - before["preferred"].get("true", 0) == 2  # the two G4 declarations
    assert after["yielding"].get("true", 0) - before["yielding"].get("true", 0) == 1   # the one G3
    reasons = after["unknown_or_false_reasons"]
    assert reasons.get("preferred:protocol_not_declared", 0) - before["unknown_or_false_reasons"].get(
        "preferred:protocol_not_declared", 0) >= 1
    assert reasons.get("preferred:departures_not_stated", 0) - before["unknown_or_false_reasons"].get(
        "preferred:departures_not_stated", 0) == 1


def test_inventory_output_is_json_and_states_its_own_limits(db_session, seeded):
    report = h298_coverage_inventory(db_session)
    json.dumps(report, allow_nan=False)
    assert report["candidate_cap"] == 500 and report["notes"]


def test_inventory_runs_in_a_read_only_transaction_and_writes_nothing(db_session, seeded):
    db_session.flush()
    before = (
        db_session.scalar(select(func.count()).select_from(Thermo)),
        db_session.scalar(select(func.count()).select_from(RecordReview)),
    )
    savepoint = db_session.begin_nested()  # SET LOCAL is undone by rolling the savepoint back
    try:
        db_session.execute(text("SET LOCAL transaction_read_only = on"))
        h298_coverage_inventory(db_session)  # a write here would raise ReadOnlySqlTransaction
        assert not db_session.new and not db_session.dirty and not db_session.deleted
    finally:
        savepoint.rollback()
    after = (
        db_session.scalar(select(func.count()).select_from(Thermo)),
        db_session.scalar(select(func.count()).select_from(RecordReview)),
    )
    assert before == after


def test_an_undeclared_target_is_assessed_against_itself_not_counted_as_a_mismatch(db_session):
    entry = species_entry_for(db_session, "Methane")
    before = h298_coverage_inventory(db_session)
    make_thermo(db_session, entry, target=None)
    after = h298_coverage_inventory(db_session)
    assert delta(after, before, "h298_not_applicable_reasons", "target_kind_mismatch") == 0
    assert delta(after, before, "h298_not_applicable_reasons", "thermodynamic_target_not_declared") == 1
