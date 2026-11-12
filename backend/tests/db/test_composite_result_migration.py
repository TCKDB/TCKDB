"""Database contract for ``calc_composite_result`` and ``calc_composite_term``.

The two tables are frozen once their calculation has been accepted, like ``calc_sp_result``; the behaviour is held
here, because a guard can be declared and never fire.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationCompositeResult, CalculationCompositeTerm
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    CompositeAssembly,
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

# -- the accepted-science guard, as behaviour -------------------------------


def _accepted_composite(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CMPFRZ"))
    entry = make_species_entry(db_session, species)
    calc = make_calculation(db_session, type=CalculationType.composite, species_entry_id=entry.id)
    result = CalculationCompositeResult(
        calculation_id=calc.id, assembly=CompositeAssembly.program_run, electronic_energy_hartree=-76.0
    )
    term = CalculationCompositeTerm(calculation_id=calc.id, term_position=0, value_hartree=-76.0)
    db_session.add_all([result, term])
    db_session.flush()
    return calc, result, term


def _approve(db_session, calc) -> None:
    actor = AppUser(username="composite-curator", role=AppUserRole.curator)
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


def test_an_unaccepted_calculations_composite_rows_can_still_change(db_session) -> None:
    _calc, result, term = _accepted_composite(db_session)
    result.electronic_energy_hartree = -76.5
    term.value_hartree = -76.5
    db_session.flush()


def test_an_accepted_calculations_composite_rows_are_frozen(db_session) -> None:
    calc, result, term = _accepted_composite(db_session)
    _approve(db_session, calc)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        result.electronic_energy_hartree = -1.0
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        term.value_hartree = -1.0
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(CalculationCompositeTerm(calculation_id=calc.id, term_position=1, value_hartree=-0.5))
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(term)
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("TRUNCATE calc_composite_term"))
