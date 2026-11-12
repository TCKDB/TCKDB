"""Database contract for ``calc_composite_input`` and the ``composite_input`` dependency role.

* the table is frozen once its composite calculation is accepted, and the cited calculation is *not* an owner
  (accepting an input freezes nothing);
* an unaccepted composite's inputs can still change.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationCompositeInput
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    CompositeInputSlot,
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

_TABLE = "calc_composite_input"


# -- the accepted-science guard, as behaviour --------------------------------


def _composite_with_input(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CMPINP"))
    entry = make_species_entry(db_session, species)
    composite = make_calculation(db_session, type=CalculationType.composite, species_entry_id=entry.id)
    sp = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    row = CalculationCompositeInput(
        calculation_id=composite.id,
        term_position=0,
        slot=CompositeInputSlot.value,
        input_calculation_id=sp.id,
    )
    db_session.add(row)
    db_session.flush()
    return composite, sp, row


def _approve(db_session, calc) -> None:
    actor = AppUser(username=f"composite-input-curator-{calc.id}", role=AppUserRole.curator)
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


def test_an_unaccepted_composites_inputs_can_still_change(db_session):
    _composite, sp, row = _composite_with_input(db_session)
    row.cardinal_number = None
    db_session.delete(row)
    db_session.flush()
    assert sp.id is not None


def test_an_accepted_composites_inputs_are_frozen(db_session):
    composite, sp, row = _composite_with_input(db_session)
    _approve(db_session, composite)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            CalculationCompositeInput(
                calculation_id=composite.id,
                term_position=1,
                slot=CompositeInputSlot.value,
                input_calculation_id=sp.id,
            )
        )
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text(f"TRUNCATE {_TABLE}"))


def test_accepting_the_cited_calculation_freezes_nothing_about_the_composite(db_session):
    """The input is a citation, not an owner: only the composite's own acceptance locks the rows."""
    _composite, sp, row = _composite_with_input(db_session)
    _approve(db_session, sp)
    db_session.delete(row)
    db_session.flush()


# -- energy_component_kind gains correlation_excluding_triples ---------------
