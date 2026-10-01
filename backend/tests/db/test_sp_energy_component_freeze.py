"""``calc_sp_energy_component`` is frozen by the acceptance of its calculation (ADR 0021, P4).

The table is an ownership child of ``calculation``, guarded the way
``calc_sp_result`` is: once the calculation is approved, its energy parts can be
neither added, changed nor removed, and TRUNCATE is refused. Under an
unapproved calculation the rows stay editable, so the freeze is
acceptance-triggered, not a blanket write ban.

``test_accepted_science_trigger_registry.py`` derives its expectation from the
revision's own registry, so a guard deleted from both the registry and the DDL
would leave it green. Every refusal here is a write that has to fail, so
removing the trigger turns these red.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationSPEnergyComponent
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    EnergyComponentKind,
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


def _sp_with_components(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("SPCF"))
    entry = make_species_entry(db_session, species)
    calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    for kind, value in ((EnergyComponentKind.reference, -75.9), (EnergyComponentKind.correlation, -0.5)):
        db_session.add(CalculationSPEnergyComponent(calculation_id=calc.id, component=kind, value_hartree=value))
    db_session.flush()
    return calc


def _approve(db_session, calc) -> None:
    actor = AppUser(username=f"spcomp-curator-{calc.id}", role=AppUserRole.curator)
    db_session.add(actor)
    db_session.flush()
    ensure_record_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id)
    review = set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    assert review.first_approved_at is not None


def test_the_parts_of_an_approved_calculation_cannot_be_changed_removed_or_added(db_session) -> None:
    calc = _sp_with_components(db_session)
    _approve(db_session, calc)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            text("UPDATE calc_sp_energy_component SET value_hartree = -1.0 WHERE calculation_id = :c"),
            {"c": calc.id},
        )
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("DELETE FROM calc_sp_energy_component WHERE calculation_id = :c"), {"c": calc.id})
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
                "VALUES (:c, CAST('triples' AS energy_component_kind), -0.01)"
            ),
            {"c": calc.id},
        )
    kept = db_session.execute(
        text("SELECT component::text, value_hartree FROM calc_sp_energy_component WHERE calculation_id = :c"),
        {"c": calc.id},
    ).all()
    assert sorted(kept) == [("correlation", -0.5), ("reference", -75.9)]


def test_the_parts_of_an_unapproved_calculation_stay_editable(db_session) -> None:
    calc = _sp_with_components(db_session)
    db_session.execute(
        text("UPDATE calc_sp_energy_component SET value_hartree = -1.0 WHERE calculation_id = :c"),
        {"c": calc.id},
    )
    db_session.execute(text("DELETE FROM calc_sp_energy_component WHERE calculation_id = :c"), {"c": calc.id})
    assert db_session.scalar(text("SELECT count(*) FROM calc_sp_energy_component WHERE calculation_id = :c"), {"c": calc.id}) == 0


def test_truncate_is_refused(db_session) -> None:
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("TRUNCATE calc_sp_energy_component"))
