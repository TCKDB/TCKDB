"""The read-only report of what the owner-scoped statmech rubric does to a database (PR 6.6).

The report is what the owner runs on a restored backup before deploying. These tests pin that it
says the right thing on a database with one record of each owner kind and one stored review each,
that it flags a species-owned record that moved, and that it cannot write.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError

from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.statmech import Statmech
from app.services.read_only_report import print_read_only_report
from app.services.trust import COMPUTED_STATMECH_V2, build_trust_fragment, evaluate_loaded_statmech_under
from app.services.trust.statmech_owner_report import statmech_trust_owner_report
from tests.services.test_trust_statmech_species_frozen import SCENARIOS
from tests.services.test_trust_ts_statmech_owner_rubric import _make_ts_statmech, _store_review


def _seed(db_session):
    species = SCENARIOS["opt_freq_sp"](db_session)
    ts = _make_ts_statmech(db_session)
    for record in (species, ts):
        legacy = build_trust_fragment(evaluate_loaded_statmech_under(record, COMPUTED_STATMECH_V2))
        _store_review(db_session, record, legacy, {"computed_statmech_v2": "2"})
    db_session.flush()
    return species, ts


def test_the_report_names_each_transition_and_each_review_state(db_session):
    _seed(db_session)
    report = statmech_trust_owner_report(db_session)

    assert report["by_owner"] == {"species_entry": {"n": 1}, "transition_state_entry": {"n": 1}}
    assert report["unowned_statmech"] == 0
    assert report["species_entry_unchanged"] is True
    assert report["species_entry_records_compared"] == 1

    transitions = {(t["owner"], t["from"], t["from_hard_fail"], t["to"], t["to_hard_fail"]): t["count"]
                   for t in report["badge_transitions"]}
    assert len(transitions) == 2
    assert transitions[("species_entry", "well_supported", None, "well_supported", None)] == 1
    (ts_key,) = [key for key in transitions if key[0] == "transition_state_entry"]
    assert ts_key[1:3] == ("hard_failed", "species_entry_missing")
    assert ts_key[3] != "hard_failed" and ts_key[4] is None

    assert report["machine_review_currency"] == [
        {"owner": "species_entry", "state": "current", "stale_reasons": [], "count": 1},
        {
            "owner": "transition_state_entry",
            "state": "stale",
            "stale_reasons": ["context_hash_mismatch", "rubric_versions_mismatch"],
            "count": 1,
        },
    ]
    assert report["statmech_without_machine_review"] == {"species_entry": 0, "transition_state_entry": 0}
    assert report["stored_review_rows_by_owner"] == {"species_entry": 1, "transition_state_entry": 1}


def test_a_record_with_no_review_is_counted_as_unreviewed_not_as_stale(db_session):
    SCENARIOS["sparse"](db_session)
    _make_ts_statmech(db_session)
    report = statmech_trust_owner_report(db_session)
    assert report["machine_review_currency"] == []
    assert report["statmech_without_machine_review"] == {"species_entry": 1, "transition_state_entry": 1}
    assert report["stored_review_rows_by_owner"] == {"species_entry": 0, "transition_state_entry": 0}


def test_the_report_flags_a_species_record_that_moved(db_session, monkeypatch):
    """If the change ever routed a species-owned record to the TS rubric, ``species_entry_unchanged`` must say so."""
    _seed(db_session)
    import app.services.trust.evaluator as evaluator
    from app.services.trust import COMPUTED_TS_STATMECH_V1

    monkeypatch.setattr(evaluator, "statmech_rubric_for", lambda statmech: COMPUTED_TS_STATMECH_V1)
    report = statmech_trust_owner_report(db_session)
    assert report["species_entry_unchanged"] is False
    assert report["species_entry_records_compared"] == 1


def test_the_report_writes_nothing(db_session):
    _seed(db_session)
    rows_before = db_session.scalar(select(func.count()).select_from(RecordMachineReviewRow))
    statmech_before = db_session.scalar(select(func.count()).select_from(Statmech))
    db_session.flush()

    statmech_trust_owner_report(db_session)

    assert not db_session.new and not db_session.dirty and not db_session.deleted
    assert db_session.scalar(select(func.count()).select_from(RecordMachineReviewRow)) == rows_before == 2
    assert db_session.scalar(select(func.count()).select_from(Statmech)) == statmech_before == 2


def test_an_empty_database_reports_zero_everywhere(db_session):
    report = statmech_trust_owner_report(db_session)
    assert report["by_owner"] == {"species_entry": {"n": 0}, "transition_state_entry": {"n": 0}}
    assert report["badge_transitions"] == []
    assert report["machine_review_currency"] == []
    assert report["species_entry_unchanged"] is True  # nothing compared, and the count beside it says so
    assert report["species_entry_records_compared"] == 0


def test_the_script_transaction_refuses_a_write(db_engine, capsys):
    """The report runs REPEATABLE READ + READ ONLY: a write inside it is refused by the database."""
    url = db_engine.url.render_as_string(hide_password=False)

    def attempts_a_write(session):
        session.execute(text("UPDATE statmech SET note = note WHERE false"))
        return {}

    with pytest.raises((InternalError, OperationalError, ProgrammingError)) as caught:
        print_read_only_report(url, attempts_a_write)
    assert "read-only transaction" in str(caught.value)

    print_read_only_report(url, statmech_trust_owner_report)
    assert json.loads(capsys.readouterr().out)["by_owner"]["species_entry"] == {"n": 0}
