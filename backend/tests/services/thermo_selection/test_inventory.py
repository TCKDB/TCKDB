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
    make_thermo(db_session, methane, proto=protocol("g4"), status=S.approved)
    make_thermo(db_session, methane, proto=protocol("g3"), status=S.approved)
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


def test_inventory_carries_no_rule_tally(db_session, seeded):
    assert "rules" not in h298_coverage_inventory(db_session)


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


# -- a confirmed finding about a record's own source calculation (#724) ------------------------------------------------


def test_a_confirmed_finding_on_a_records_source_is_counted_as_blocking_validation_for_that_record_only(db_session):
    """Record ``blocked``'s source is invalidated; ``clean`` rests on another calculation, so exactly one record is blocked."""
    from app.db.models.common import StructureFindingKind
    from tests.services.thermo_selection.test_source_findings_h298 import Bench

    baseline = h298_coverage_inventory(db_session)
    entry = species_entry_for(db_session, "Methane")
    bench = Bench(db_session, entry)
    blocked = make_thermo(db_session, entry, proto=protocol("g4"))
    clean = make_thermo(db_session, entry, proto=protocol("g3"))
    blocked_opt, _ = bench.opt(blocked)
    bench.opt(clean)
    db_session.flush()
    before = h298_coverage_inventory(db_session)
    assert delta(before, baseline, "thermo_records") == 2
    assert delta(before, baseline, "h298_blocking_validation", "source_finding") == 0

    bench.finding(calculation=blocked_opt, kind=StructureFindingKind.identity_incompatibility)
    db_session.flush()
    db_session.expire_all()
    after = h298_coverage_inventory(db_session)
    assert delta(after, baseline, "thermo_records") == 2  # the record is still stored and still counted
    assert delta(after, baseline, "h298_blocking_validation", "source_finding") == 1  # exactly the invalidated one
