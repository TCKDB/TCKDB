"""A kinetics record's bath gas is frozen by the approval of the record.

``a4c8e1f7b392`` puts ``kinetics_bath_gas`` under the accepted-science regime beside
``kinetics_plog``: once a kinetics record has been approved, the collider its pressure-dependent fit
was obtained for can be neither added, edited nor removed. The registry parity test compares
``pg_trigger`` with the revision's own registry, so a guard deleted from both would stay green; every
refusal below needs the trigger itself. Removing it turns the refusals red, and the unapproved case
shows the freeze is acceptance-triggered rather than a blanket write ban.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import KineticsBathGas
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _kinetics_with_bath_gas(session, tag: str):
    reactant = make_species(session, inchi_key=next_inchi_key(f"{tag}R"))
    product = make_species(session, inchi_key=next_inchi_key(f"{tag}P"))
    reaction = make_chem_reaction(session, reactants=[reactant], products=[product])
    reaction_entry = make_reaction_entry(
        session,
        reaction=reaction,
        reactant_entries=[make_species_entry(session, reactant)],
        product_entries=[make_species_entry(session, product)],
    )
    kinetics = make_kinetics(session, reaction_entry=reaction_entry)
    argon = make_species_entry(session, make_species(session, inchi_key=next_inchi_key(f"{tag}AR")))
    nitrogen = make_species_entry(session, make_species(session, inchi_key=next_inchi_key(f"{tag}N2")))
    row = KineticsBathGas(kinetics_id=kinetics.id, species_entry_id=argon.id, mole_fraction=0.6)
    session.add(row)
    session.flush()
    return kinetics, row, nitrogen


def _approve(session, kinetics) -> None:
    actor = AppUser(username=f"bath-gas-curator-{kinetics.id}", role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    ensure_record_review(session, record_type=SubmissionRecordType.kinetics, record_id=kinetics.id)
    set_record_review_status(
        session,
        record_type=SubmissionRecordType.kinetics,
        record_id=kinetics.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )


def test_bath_gas_of_approved_kinetics_cannot_be_edited_or_removed(db_session) -> None:
    kinetics, row, _ = _kinetics_with_bath_gas(db_session, "BGFRZ")
    _approve(db_session, kinetics)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        row.mole_fraction = 0.4
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()


def test_bath_gas_cannot_be_added_to_approved_kinetics(db_session) -> None:
    kinetics, _row, nitrogen = _kinetics_with_bath_gas(db_session, "BGLATE")
    _approve(db_session, kinetics)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(KineticsBathGas(kinetics_id=kinetics.id, species_entry_id=nitrogen.id, mole_fraction=0.4))
        db_session.flush()


def test_bath_gas_of_unapproved_kinetics_stays_editable(db_session) -> None:
    kinetics, row, nitrogen = _kinetics_with_bath_gas(db_session, "BGOPEN")

    row.mole_fraction = 0.4
    db_session.add(KineticsBathGas(kinetics_id=kinetics.id, species_entry_id=nitrogen.id, mole_fraction=0.6))
    db_session.flush()
    db_session.delete(row)
    db_session.flush()


def test_truncate_cannot_bypass_the_bath_gas_guard(db_session) -> None:
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("TRUNCATE kinetics_bath_gas"))


@pytest.mark.parametrize("fraction", [0.0, -0.2, 1.0000001])
def test_a_mole_fraction_outside_zero_to_one_is_refused_by_the_database(db_session, fraction: float) -> None:
    kinetics, row, nitrogen = _kinetics_with_bath_gas(db_session, "BGRNG")
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(KineticsBathGas(kinetics_id=kinetics.id, species_entry_id=nitrogen.id, mole_fraction=fraction))
        db_session.flush()
