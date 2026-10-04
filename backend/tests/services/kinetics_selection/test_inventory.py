"""The kinetics coverage inventory counts what stored records can answer, why not, and writes nothing."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, text

from app.db.models.common import RecordReviewStatus as S
from app.db.models.common import ScientificOriginKind, SubmissionRecordType
from app.db.models.kinetics import Kinetics, KineticsDetermination
from app.db.models.record_review import RecordReview
from app.services.kinetics_selection.inventory import kinetics_coverage_inventory, own_question
from tests.services.kinetics_selection._support import applicability, norm
from tests.services.kinetics_selection.test_service import declared, determination
from tests.services.scientific_read._factories import make_kinetics, set_review


@pytest.fixture
def seeded(db_session, world):
    """A small corpus whose inventory is known by construction. Counts are the *increment* over what exists."""
    baseline = kinetics_coverage_inventory(db_session)
    d1 = determination(db_session, world, "d1")
    declared(db_session, world, d1, status=S.approved)                       # qualifies (determination d1)
    declared(db_session, world, d1, status=S.approved)                       # a second fit of d1: qualifies, same determination
    d2 = determination(db_session, world, "d2")
    declared(db_session, world, d2, status=S.not_reviewed,                   # an experimental rate: qualifies, no rubric
             scientific_origin=ScientificOriginKind.experimental)
    d3 = determination(db_session, world, "d3")
    declared(db_session, world, d3, status=S.approved,                       # a rate of progress: not a rate coefficient
             block=applicability(observable="rate_of_progress"))
    d4 = determination(db_session, world, "d4")
    declared(db_session, world, d4, status=S.approved, tmin_k=None)           # no temperature window of its own
    d5 = determination(db_session, world, "d5")
    declared(db_session, world, d5, status=S.approved,                       # a pressure surface with no stated domain
             block=applicability(pressure_dependence="pressure_dependent"))
    legacy = make_kinetics(db_session, reaction_entry=world.entry)           # no determination, no declarations
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=legacy.id, status=S.approved)
    db_session.flush()
    return baseline


def delta(after, before, *path):
    def dig(report):
        for key in path:
            report = report.get(key, {}) if isinstance(report, dict) else {}
        return report if isinstance(report, int) else 0

    return dig(after) - dig(before)


def test_the_inventory_counts_applicability_to_each_records_own_question_and_why_not(db_session, seeded):
    after = kinetics_coverage_inventory(db_session)
    assert delta(after, seeded, "kinetics_records") == 7
    assert delta(after, seeded, "reaction_entries_with_kinetics") == 1
    assert delta(after, seeded, "qualifying_records") == 3
    assert delta(after, seeded, "unsupported_records") == 1
    assert delta(after, seeded, "unresolved_records") == 3  # no window, no pressure domain, no determination at all
    assert delta(after, seeded, "applicability_to_own_question", "incompatible") == 0
    assert delta(after, seeded, "question_not_stated_because", "temperature_window") == 1
    assert delta(after, seeded, "question_not_stated_because", "pressure") == 1
    assert delta(after, seeded, "question_not_stated_because", "determination") == 1
    assert delta(after, seeded, "question_not_stated_because", "applicability_declaration") == 1
    assert delta(after, seeded, "not_applicable_reasons", "observable_unsupported") == 1
    assert delta(after, seeded, "determinations_declared") == 5
    assert delta(after, seeded, "determinations_with_a_qualifying_record") == 2  # d1 (two fits, one determination) and d2
    assert delta(after, seeded, "reaction_entries_with_a_qualifying_record") == 1
    assert delta(after, seeded, "by_origin", "experimental") == 1
    assert delta(after, seeded, "by_review_status", "not_reviewed") == 1


def test_every_rule_is_reported_with_its_status_and_why_it_is_not_active(db_session, seeded):
    after = kinetics_coverage_inventory(db_session)
    assert "K-XYG3-B3LYP-BARRIER" in after["rules"]
    assert all(r["status"] == "inactive" and r["inactive_reasons"] for r in after["rules"].values())
    scope = after["rules"]["K-XYG3-B3LYP-BARRIER"]["scope_of_stated_questions"]
    assert sum(scope.values()) >= 3  # asked of every record that states a question; H + CH4 is not a manifest member
    assert delta(after, seeded, "rules", "K-XYG3-B3LYP-BARRIER", "scope_of_stated_questions", "true") == 0


def test_the_inventory_is_json_serialisable_and_names_no_row_id(db_session, seeded):
    text_ = json.dumps(kinetics_coverage_inventory(db_session), allow_nan=False)
    assert "_id\"" not in text_.replace("rule_id", "")


def test_the_inventory_writes_nothing(db_session, seeded):
    def counts():
        return tuple(db_session.scalar(select(func.count()).select_from(m)) for m in (Kinetics, KineticsDetermination, RecordReview))

    before = counts()
    kinetics_coverage_inventory(db_session)
    assert counts() == before


def test_a_database_that_is_read_only_still_answers(db_session, seeded):
    db_session.flush()
    db_session.execute(text("SAVEPOINT inventory_probe"))
    try:
        db_session.execute(text("SET TRANSACTION READ ONLY"))
    except Exception:  # the harness transaction already ran statements; a real run sets it first
        db_session.execute(text("ROLLBACK TO SAVEPOINT inventory_probe"))
        pytest.skip("cannot become read-only after statements; the script sets it first")
    assert kinetics_coverage_inventory(db_session)["kinetics_records"] >= 7


# -- the question a record poses is only what it states -------------------------------------------------------


def test_a_record_states_its_own_question_and_nothing_is_defaulted():
    request, missing = own_question(norm())
    assert request is not None and missing == []
    assert request.direction.value == "forward" and request.pressure.kind.value == "independent"


@pytest.mark.parametrize(
    "changes,missing",
    [
        ({"determination": None}, "determination"),
        ({"applicability_state": "absent", "applicability": None}, "applicability_declaration"),
        ({"tmin_k": None}, "temperature_window"),
        ({"tmax_k": None}, "temperature_window"),
        ({"applicability": applicability(coefficient_basis=None)}, "coefficient_basis"),
        ({"applicability": applicability(pressure_dependence=None)}, "pressure"),
        ({"applicability": applicability(pressure_dependence="fixed_pressure")}, "pressure"),  # no pressure of its own
        ({"applicability": applicability(pressure_dependence="pressure_dependent")}, "pressure"),  # no domain
        (
            {"applicability": applicability(pressure_dependence="pressure_dependent", pressure_domain_min_bar=0.1, pressure_domain_max_bar=10.0)},
            "collider",  # a finite pressure needs a collider the record neither names nor disclaims
        ),
    ],
)
def test_what_a_record_does_not_state_makes_its_question_unformable_and_is_named(changes, missing):
    request, found = own_question(norm(**changes))
    assert request is None and missing in found


def test_a_record_that_disclaims_a_collider_dependence_is_asked_about_a_probe_collider():
    surface = applicability(
        pressure_dependence="pressure_dependent", pressure_domain_min_bar=0.1, pressure_domain_max_bar=10.0,
        collider_kind="not_dependent",
    )
    request, missing = own_question(norm(applicability=surface))
    assert missing == [] and request is not None
    assert request.collider is not None and request.collider.species_refs == ("spc_inventory_probe",)
    named = applicability(
        pressure_dependence="fixed_pressure", collider_kind="specified_collider",
        colliders=[{"species_ref": "spc_n2", "mole_fraction": None}],
    )
    request, missing = own_question(norm(pressure_bar=1.0, applicability=named))
    assert missing == [] and request is not None and request.collider is not None
    assert request.collider.species_refs == ("spc_n2",) and request.pressure.min_bar == 1.0


def test_a_record_the_evidence_rubric_hard_fails_is_not_counted_as_qualifying(db_session, world, monkeypatch):
    from app.services.kinetics_selection import inventory
    from app.services.trust.models import EvidenceBadge, EvidenceEvaluation, HardFailReason

    before = kinetics_coverage_inventory(db_session)
    declared(db_session, world, determination(db_session, world, "d-fail"), status=S.approved)
    failed = EvidenceEvaluation(
        record_type="kinetics", record_id=1, rubric="computed_kinetics_v1", rubric_version="1",
        label=EvidenceBadge.hard_failed, checks={}, passed_count=0, possible_count=1, evidence_completeness=0.0,
        is_certified=False, hard_fail_reason=HardFailReason.source_calculation_hard_failed_for_required_role,
        check_results=(),
    )
    monkeypatch.setattr(inventory, "evaluate_loaded_kinetics", lambda _k: failed)
    after = kinetics_coverage_inventory(db_session)
    assert delta(after, before, "kinetics_records") == 1
    assert delta(after, before, "applicability_to_own_question", "applicable") == 1  # it answers its question...
    assert delta(after, before, "qualifying_records") == 0  # ...but is not eligible to compete
    assert delta(after, before, "blocking_validation", "evidence_hard_failed") == 1
    assert delta(after, before, "determinations_with_a_qualifying_record") == 0
