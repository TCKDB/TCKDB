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
_COMPONENT = "correlation_excluding_triples"


# -- the accepted-science guard, as behaviour --------------------------------


def _seed_two_calculations(conn) -> tuple[int, int]:
    species_id = conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), 'O', 'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0, 1, "
            "CAST('unspecified' AS stereo_kind)) RETURNING id"
        )
    )
    entry_id = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id})
    ids = [
        conn.scalar(
            text("INSERT INTO calculation (type, species_entry_id) VALUES (CAST(:t AS calc_type), :e) RETURNING id"),
            {"t": calc_type, "e": entry_id},
        )
        for calc_type in ("sp", "composite")
    ]
    return ids[0], ids[1]


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


@pytest.mark.parametrize(
    "bad_row",
    [
        "(:c, 0, CAST('cardinal' AS composite_input_slot), :s, NULL)",  # a cardinal slot without its number
        "(:c, 0, CAST('value' AS composite_input_slot), :s, 3)",  # a number on a slot that has none
        "(:c, 0, CAST('value' AS composite_input_slot), :c, NULL)",  # a calculation its own input
        "(:c, -1, CAST('value' AS composite_input_slot), :s, NULL)",  # a negative position
    ],
    ids=["cardinal_without_number", "number_on_value", "own_input", "negative_position"],
)
def test_the_table_refuses_what_its_checks_name(db_session, bad_row):
    conn = db_session
    with db_session.begin_nested():
        sp_id, composite_id = _seed_two_calculations(conn)
    with pytest.raises(DBAPIError), db_session.begin_nested():
        conn.execute(
            text(
                "INSERT INTO calc_composite_input "
                "(calculation_id, term_position, slot, input_calculation_id, cardinal_number) "
                f"VALUES {bad_row}"
            ),
            {"c": composite_id, "s": sp_id},
        )


def test_the_database_refuses_a_stored_derived_component(db_session):
    """The wire and the service refuse it; the CHECK is the floor under them."""
    conn = db_session
    with db_session.begin_nested():
        sp_id, _ = _seed_two_calculations(conn)
    with pytest.raises(DBAPIError, match="component_not_derived"), db_session.begin_nested():
        conn.execute(
            text(
                "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
                "VALUES (:c, CAST(:k AS energy_component_kind), -0.3)"
            ),
            {"c": sp_id, "k": _COMPONENT},
        )
    # The other components are unaffected.
    with db_session.begin_nested():
        conn.execute(
            text(
                "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
                "VALUES (:c, CAST('triples' AS energy_component_kind), -0.01)"
            ),
            {"c": sp_id},
        )
