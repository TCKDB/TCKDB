"""``calc_scf_stability`` is frozen by its own calculation, not by the one it cites.

The accepted-science rule: a child is guarded by the accepted root that owns its
*scientific meaning*, and cross-domain provenance foreign keys are not
ownership. ``calc_scf_stability`` is guarded on the ``NOT NULL``
``calculation_id`` that owns the row, and not on the nullable
``source_calculation_id``, which is the calculation the stability analysis was
*read from*. This file exists to keep the second guard out.

``tckdb_guard_accepted_child`` makes no distinction between the columns it is
given: every non-NULL value in every argument column is looked up in
``record_review`` and, if ever approved, the write is refused. Guarding both
columns would make naming an approved calculation as the source of a
stability analysis itself refusable, which is backwards:
accepted science exists precisely to be pointed at.

The identically shaped ``network_solve_state_energy.source_calculation_id`` is
excluded for the same reason ``thermo_source_calculation.calculation_id`` is.

A defect here would live in a trigger *argument*, not a trigger name. Only
writing the row and demanding it land catches it, which is what
``test_provenance_pointer_at_accepted_science_is_allowed`` does.

The tests below pin both halves, because a guard removed too broadly is as
wrong as one registered too broadly:

* the ownership guard on ``calculation_id`` still refuses insert, update and
  delete under an approved calculation;
* the provenance pointer in ``source_calculation_id`` is not a guarded column,
  on insert or on update, whatever the cited calculation's review state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationSCFStability
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    SCFStabilityStatus,
    SubmissionRecordType,
)
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _curator(session, username: str) -> AppUser:
    actor = AppUser(username=username, role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _approve(session, *, record_id: int, actor: AppUser) -> None:
    ensure_record_review(
        session,
        record_type=SubmissionRecordType.calculation,
        record_id=record_id,
    )
    review = set_record_review_status(
        session,
        record_type=SubmissionRecordType.calculation,
        record_id=record_id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    assert review.first_approved_at is not None


def _calculation(session, tag: str):
    species = make_species(session, inchi_key=next_inchi_key(tag))
    entry = make_species_entry(session, species)
    return make_calculation(session, species_entry_id=entry.id)


def _stability(**kwargs) -> CalculationSCFStability:
    return CalculationSCFStability(status=SCFStabilityStatus.stable, **kwargs)


def test_provenance_pointer_at_accepted_science_is_allowed(db_session) -> None:
    """An unapproved calculation may cite an approved one as its source.

    This is the behaviour the removed registry entry denied. The owning
    calculation is untouched by review; only the calculation named in
    ``source_calculation_id`` is approved, and that must not make the row
    unwritable.
    """

    actor = _curator(db_session, "scf-provenance-curator")
    owner = _calculation(db_session, "SCFOWNER")
    source = _calculation(db_session, "SCFSOURCE")
    _approve(db_session, record_id=source.id, actor=actor)

    db_session.add(
        _stability(
            calculation_id=owner.id,
            source_calculation_id=source.id,
            note="read from an accepted calculation",
        )
    )
    db_session.flush()

    stored = db_session.get(CalculationSCFStability, owner.id)
    assert stored is not None
    assert stored.source_calculation_id == source.id


def test_provenance_pointer_may_be_set_by_update(db_session) -> None:
    """The pointer is equally unguarded when it arrives on an UPDATE.

    ``tckdb_guard_accepted_child`` reads OLD *and* NEW for every argument
    column, so a registered column refuses the update that introduces the
    reference as well as the insert that carries it. Both directions are
    pinned so a partial re-registration cannot pass.
    """

    actor = _curator(db_session, "scf-provenance-update-curator")
    owner = _calculation(db_session, "SCFUPDOWNER")
    source = _calculation(db_session, "SCFUPDSOURCE")
    _approve(db_session, record_id=source.id, actor=actor)

    row = _stability(calculation_id=owner.id)
    db_session.add(row)
    db_session.flush()

    row.source_calculation_id = source.id
    db_session.flush()
    db_session.expire(row)
    assert row.source_calculation_id == source.id


def test_pointer_free_insert_is_not_the_only_thing_allowed(db_session) -> None:
    """The NULL-pointer insert succeeds.

    Kept beside the test above so the pair reads as the comparison that
    identified the defect: identical rows, differing only in whether they
    named their source, and only the one that named it was refused.
    """

    owner = _calculation(db_session, "SCFNULLPTR")
    db_session.add(_stability(calculation_id=owner.id))
    db_session.flush()

    stored = db_session.get(CalculationSCFStability, owner.id)
    assert stored is not None
    assert stored.source_calculation_id is None


def test_stability_of_accepted_calculation_cannot_be_inserted(db_session) -> None:
    """The ownership guard survives: no new evidence under an approved root."""

    actor = _curator(db_session, "scf-owner-insert-curator")
    owner = _calculation(db_session, "SCFOWNINS")
    _approve(db_session, record_id=owner.id, actor=actor)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(_stability(calculation_id=owner.id))
        db_session.flush()


def test_stability_of_accepted_calculation_cannot_be_updated_or_deleted(
    db_session,
) -> None:
    """The ownership guard survives on UPDATE and DELETE as well."""

    actor = _curator(db_session, "scf-owner-mutate-curator")
    owner = _calculation(db_session, "SCFOWNMUT")
    row = _stability(calculation_id=owner.id, note="before approval")
    db_session.add(row)
    db_session.flush()
    _approve(db_session, record_id=owner.id, actor=actor)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        row.note = "after approval"
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()


def test_scf_stability_guard_names_only_the_ownership_column(db_session) -> None:
    """The installed trigger's arguments, read from the database itself.

    Asserted against a literal rather than against any revision's registry:
    a registry-derived expectation shrinks along with the reality it is
    supposed to constrain, which is how the extra argument survived review in
    the first place. ``tgargs`` is a NUL-separated bytea of the C-string
    arguments, so the trailing empty element is stripped.
    """

    arguments = db_session.execute(
        text(
            """
            SELECT trigger.tgargs
            FROM pg_trigger AS trigger
            JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
            WHERE NOT trigger.tgisinternal
              AND relation.relname = 'calc_scf_stability'
              AND trigger.tgfoid = 'public.tckdb_guard_accepted_child'::regproc
            """
        )
    ).scalar_one()

    assert bytes(arguments).decode("utf-8").split("\x00")[:-1] == [
        "calculation",
        "calculation_id",
    ]
