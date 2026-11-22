"""The database lets an approved record be deleted only through the audited path.

A DELETE of an accepted record's rows passes the immutability guards only when the transaction-local setting
``tckdb.record_deletion`` names the record AND a ``record_deletion`` row for it was written in the same transaction.
Either half alone is refused, an UPDATE is refused even with both, and the audit itself is append-only.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_IMMUTABLE = "55000"


def _admin(session) -> AppUser:
    user = AppUser(username=f"guard-admin-{next_inchi_key('GA')[-6:]}", role=AppUserRole.admin)
    session.add(user)
    session.flush()
    return user


def _kinetics(session, *, approve: bool = True):
    reactant = make_species(session, smiles="C=C", inchi_key=next_inchi_key("RDGR"))
    product = make_species(session, smiles="CC", inchi_key=next_inchi_key("RDGP"))
    reaction = make_chem_reaction(session, reactants=[reactant], products=[product])
    entry = make_reaction_entry(
        session,
        reaction=reaction,
        reactant_entries=[make_species_entry(session, species=reactant)],
        product_entries=[make_species_entry(session, species=product)],
    )
    kinetics = make_kinetics(session, reaction_entry=entry)
    if approve:
        curator = AppUser(username=f"guard-curator-{kinetics.id}", role=AppUserRole.curator)
        session.add(curator)
        session.flush()
        ensure_record_review(session, record_type=SubmissionRecordType.kinetics, record_id=kinetics.id)
        set_record_review_status(
            session,
            record_type=SubmissionRecordType.kinetics,
            record_id=kinetics.id,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
    return kinetics


def _audit(session, kinetics, actor: AppUser) -> None:
    session.execute(
        text(
            "INSERT INTO record_deletion (record_type, record_id, record_public_ref, first_approved_at, reason, "
            "deleted_by, snapshot) VALUES ('kinetics', :id, :ref, now(), 'test', :by, '{}'::jsonb)"
        ),
        {"id": kinetics.id, "ref": kinetics.public_ref, "by": actor.id},
    )


def _name(session, kinetics) -> None:
    session.execute(text("SELECT set_config('tckdb.record_deletion', :v, true)"), {"v": f"kinetics:{kinetics.id}"})


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def _refused(session, sql: str, **params) -> str | None:
    with pytest.raises(DBAPIError) as raised, session.begin_nested():
        session.execute(text(sql), params)
    return _sqlstate(raised.value)


def test_a_plain_delete_of_an_approved_record_is_refused(db_session) -> None:
    kinetics = _kinetics(db_session)
    assert _refused(db_session, "DELETE FROM kinetics WHERE id = :id", id=kinetics.id) == _IMMUTABLE


def test_the_setting_without_an_audit_row_is_refused(db_session) -> None:
    kinetics = _kinetics(db_session)
    with db_session.begin_nested():
        _name(db_session, kinetics)
        assert _refused(db_session, "DELETE FROM kinetics WHERE id = :id", id=kinetics.id) == _IMMUTABLE


def test_an_audit_row_without_the_setting_is_refused(db_session) -> None:
    kinetics = _kinetics(db_session)
    _audit(db_session, kinetics, _admin(db_session))
    assert _refused(db_session, "DELETE FROM kinetics WHERE id = :id", id=kinetics.id) == _IMMUTABLE
    assert _refused(
        db_session,
        "DELETE FROM record_review WHERE record_type = 'kinetics' AND record_id = :id",
        id=kinetics.id,
    ) == _IMMUTABLE


def test_both_halves_allow_a_delete_of_that_record_only_and_never_an_update(db_session) -> None:
    kinetics = _kinetics(db_session)
    other = _kinetics(db_session)
    _audit(db_session, kinetics, _admin(db_session))
    _name(db_session, kinetics)

    assert _refused(db_session, "UPDATE kinetics SET note = 'edited' WHERE id = :id", id=kinetics.id) == _IMMUTABLE
    assert _refused(db_session, "DELETE FROM kinetics WHERE id = :id", id=other.id) == _IMMUTABLE
    assert _refused(
        db_session,
        "DELETE FROM record_review WHERE record_type = 'kinetics' AND record_id = :id",
        id=other.id,
    ) == _IMMUTABLE

    with db_session.begin_nested():
        db_session.execute(text("DELETE FROM kinetics WHERE id = :id"), {"id": kinetics.id})
        db_session.execute(
            text(
                "DELETE FROM record_review_event WHERE record_review_id IN "
                "(SELECT id FROM record_review WHERE record_type = 'kinetics' AND record_id = :id)"
            ),
            {"id": kinetics.id},
        )
        db_session.execute(
            text("DELETE FROM record_review WHERE record_type = 'kinetics' AND record_id = :id"), {"id": kinetics.id}
        )
    assert db_session.execute(text("SELECT count(*) FROM kinetics WHERE id = :id"), {"id": kinetics.id}).scalar() == 0


def test_the_audit_is_append_only_and_names_only_an_approved_record(db_session) -> None:
    kinetics = _kinetics(db_session)
    _audit(db_session, kinetics, _admin(db_session))
    assert _refused(db_session, "UPDATE record_deletion SET reason = 'rewritten'") == _IMMUTABLE
    assert _refused(db_session, "DELETE FROM record_deletion") == _IMMUTABLE

    unapproved = _kinetics(db_session, approve=False)
    with pytest.raises(DBAPIError) as raised, db_session.begin_nested():
        _audit(db_session, unapproved, _admin(db_session))
    assert _sqlstate(raised.value) == "23514"
