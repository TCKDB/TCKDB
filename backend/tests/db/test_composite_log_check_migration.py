"""Database contract for ``calc_composite_log_check``.

It records what comparing a program-run composite calculation with an attached output log concluded, at upload, so a
read never parses a log.

* the table refuses a digest that is not 64 lowercase hex digits and a repeat of ``(calculation, digest)``;
* the table is frozen once its calculation is accepted (the accepted-science guard).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationCompositeLogCheck
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    CompositeLogOutcome,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_TABLE = "calc_composite_log_check"
_SHA = "ab" * 32


# -- the accepted-science guard, as behaviour --------------------------------


def _composite_with_check(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CMPLOG"))
    entry = make_species_entry(db_session, species)
    composite = make_calculation(db_session, type=CalculationType.composite, species_entry_id=entry.id)
    row = CalculationCompositeLogCheck(
        calculation_id=composite.id, artifact_sha256=_SHA, parser_version=1, outcome=CompositeLogOutcome.confirmed
    )
    db_session.add(row)
    db_session.flush()
    return composite, row


def _approve(db_session, calc) -> None:
    actor = AppUser(username=f"composite-log-curator-{calc.id}", role=AppUserRole.curator)
    db_session.add(actor)
    db_session.flush()
    ensure_record_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id)
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )


def test_an_unaccepted_calculations_log_check_can_still_change(db_session):
    _composite, row = _composite_with_check(db_session)
    db_session.delete(row)
    db_session.flush()


def test_an_accepted_calculations_log_check_is_frozen(db_session):
    composite, row = _composite_with_check(db_session)
    _approve(db_session, composite)
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            CalculationCompositeLogCheck(
                calculation_id=composite.id, artifact_sha256="cd" * 32, parser_version=1, outcome=CompositeLogOutcome.mismatch
            )
        )
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text(f"TRUNCATE {_TABLE}"))
