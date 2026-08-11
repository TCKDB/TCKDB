"""A repair to accepted science is declared, checked column by column, recorded.

``e2c9a4f7b163`` opens the only route through ``c6f2a9d4e7b1``'s refusal, and
the whole value of it is that the database *checks* the claim rather than
accepting a justification string. So the tests below pin all four halves of
that, because any one of them missing turns the mechanism into a bypass with
paperwork:

* an accepted row **cannot** be updated with no declaration in force;
* it **can** be updated for a column the declaration names, and the change is
  recorded with enough detail to audit it afterwards;
* an update that touches a column *outside* the declared set is refused --
  including one that changes a declared column in the same statement, which is
  the case a column check that only looked at the SET list would let through;
* the declaration does not outlive its scope, in either direction: not past
  its transaction, and not past its expiry.

The declaration itself is checked on the way in as well: it must name a table
something actually guards, columns that exist, and it must come from a role
that could already have run ``ALTER TABLE ... DISABLE TRIGGER``. Otherwise the
row is a record of a permission nobody needed, which is worse than no row.

``geometry_atom.element`` is the target throughout because it is the case this
mechanism was built for -- ``b4e7c1d20f83``, the letter case of an element
symbol in a derived index column -- and because it reaches its accepted root
(``calculation``) through ``tckdb_guard_calculation_geometry``, the guard with
the most indirection between the changed row and the record that freezes it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.geometry import GeometryAtom
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    attach_geometry_atoms,
    attach_input_geometry,
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_REVISION = "e2c9a4f7b163"


def _curator(session, username: str) -> AppUser:
    actor = AppUser(username=username, role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _accepted_atom(session, tag: str):
    """An accepted calculation whose input geometry has one shouted atom."""
    actor = _curator(session, f"repair-{tag}-{uuid4().hex[:8]}")
    species = make_species(session, inchi_key=next_inchi_key(f"REP{tag}"))
    entry = make_species_entry(session, species)
    calculation = make_calculation(session, species_entry_id=entry.id)
    geometry = make_geometry(session, natoms=1, xyz_text="1\n\nCL 0.0 0.0 0.0")
    atom = attach_geometry_atoms(
        session,
        geometry=geometry,
        symbols=["CL"],
        coords=[[0.0, 0.0, 0.0]],
    )[0]
    attach_input_geometry(session, calculation=calculation, geometry=geometry)
    ensure_record_review(
        session,
        record_type=SubmissionRecordType.calculation,
        record_id=calculation.id,
    )
    review = set_record_review_status(
        session,
        record_type=SubmissionRecordType.calculation,
        record_id=calculation.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    assert review.first_approved_at is not None
    return calculation, geometry, atom


def _declare(
    session,
    *,
    table: str = "geometry_atom",
    columns: tuple[str, ...] = ("element",),
    reason: str = "canonicalise element symbol case; no coordinate changes",
    ages: tuple[str, str] | None = None,
) -> int:
    """Insert a repair declaration and return its id.

    ``ages`` overrides ``(created_at, expires_at)`` with SQL interval
    expressions relative to ``clock_timestamp()``, so a test can build a
    declaration that has already expired.
    """
    if ages is None:
        timestamps = ""
        values = ""
    else:
        timestamps = ", created_at, expires_at"
        values = f", clock_timestamp() {ages[0]}, clock_timestamp() {ages[1]}"
    return session.execute(
        text(
            "INSERT INTO accepted_science_repair "
            f"(target_table, declared_columns, alembic_revision, reason{timestamps}) "
            f"VALUES (:table, :columns, :revision, :reason{values}) RETURNING id"
        ),
        {
            "table": table,
            "columns": list(columns),
            "revision": _REVISION,
            "reason": reason,
        },
    ).scalar_one()


def _changes(session) -> list[dict]:
    rows = session.execute(
        text(
            "SELECT repair_id, record_type::text AS record_type, record_id, "
            "target_schema, target_table, row_identity, changed_columns, "
            "before_json, after_json "
            "FROM accepted_science_repair_change ORDER BY id"
        )
    ).mappings()
    return [dict(row) for row in rows]


def test_accepted_atom_refuses_an_update_with_no_declaration(db_session) -> None:
    """The regime this revision opens a route through is still closed by default."""
    _, _, atom = _accepted_atom(db_session, "NODECL")

    with pytest.raises(DBAPIError, match="is immutable"), db_session.begin_nested():
        atom.element = "Cl"
        db_session.flush()


def test_a_declaration_permits_its_column_and_records_the_change(db_session) -> None:
    calculation, geometry, atom = _accepted_atom(db_session, "PERMIT")
    repair_id = _declare(db_session)

    db_session.execute(
        text("UPDATE geometry_atom SET element = 'Cl' WHERE geometry_id = :geometry_id AND atom_index = :atom_index"),
        {"geometry_id": geometry.id, "atom_index": atom.atom_index},
    )

    stored = db_session.execute(
        text("SELECT btrim(element) FROM geometry_atom WHERE geometry_id = :geometry_id AND atom_index = :atom_index"),
        {"geometry_id": geometry.id, "atom_index": atom.atom_index},
    ).scalar_one()
    assert stored == "Cl"

    recorded = _changes(db_session)
    assert len(recorded) == 1
    change = recorded[0]
    assert change["repair_id"] == repair_id
    # The accepted record whose immutability was stood down -- reached from a
    # geometry_atom row through two joins, which is the part worth pinning.
    assert change["record_type"] == SubmissionRecordType.calculation.value
    assert change["record_id"] == calculation.id
    assert change["target_schema"] == "public"
    assert change["target_table"] == "geometry_atom"
    assert change["row_identity"] == {
        "geometry_id": geometry.id,
        "atom_index": atom.atom_index,
    }
    assert change["changed_columns"] == ["element"]
    # ``element`` is character(2), so the stored value is blank-padded. What
    # matters is that both sides are recorded verbatim rather than normalised.
    assert change["before_json"]["element"].strip() == "CL"
    assert change["after_json"]["element"].strip() == "Cl"


def test_an_undeclared_column_is_still_refused_under_a_declaration(db_session) -> None:
    _, geometry, atom = _accepted_atom(db_session, "OTHERCOL")
    _declare(db_session)

    with pytest.raises(DBAPIError, match="does not declare column"), db_session.begin_nested():
        db_session.execute(
            text("UPDATE geometry_atom SET x = 9.0 WHERE geometry_id = :geometry_id AND atom_index = :atom_index"),
            {"geometry_id": geometry.id, "atom_index": atom.atom_index},
        )
    assert _changes(db_session) == []


def test_a_declared_column_does_not_carry_an_undeclared_one(db_session) -> None:
    """The subset check is over what *changed*, not over what was declared.

    A repair that rewrites its declared column and quietly moves an atom in
    the same statement is the failure mode a permissive check would allow, and
    it is the one that matters: the declared change is the alibi.
    """
    _, geometry, atom = _accepted_atom(db_session, "SMUGGLE")
    _declare(db_session)

    with pytest.raises(DBAPIError, match="does not declare column\\(s\\) x"), db_session.begin_nested():
        db_session.execute(
            text(
                "UPDATE geometry_atom SET element = 'Cl', x = 9.0 "
                "WHERE geometry_id = :geometry_id AND atom_index = :atom_index"
            ),
            {"geometry_id": geometry.id, "atom_index": atom.atom_index},
        )

    stored = db_session.execute(
        text(
            "SELECT btrim(element), x FROM geometry_atom WHERE geometry_id = :geometry_id AND atom_index = :atom_index"
        ),
        {"geometry_id": geometry.id, "atom_index": atom.atom_index},
    ).one()
    assert stored == ("CL", 0.0)
    assert _changes(db_session) == []


def test_writing_a_declared_column_back_to_its_own_value_is_not_a_change(
    db_session,
) -> None:
    """A no-op UPDATE is permitted and records nothing.

    Recording it would put rows in the audit that assert a repair happened
    when nothing moved, which is the same dilution the change log exists to
    avoid.
    """
    _, geometry, atom = _accepted_atom(db_session, "NOOP")
    _declare(db_session)

    db_session.execute(
        text(
            "UPDATE geometry_atom SET element = element WHERE geometry_id = :geometry_id AND atom_index = :atom_index"
        ),
        {"geometry_id": geometry.id, "atom_index": atom.atom_index},
    )
    assert _changes(db_session) == []


def test_a_declaration_covers_one_table_only(db_session) -> None:
    calculation, _, _ = _accepted_atom(db_session, "ONETABLE")
    _declare(db_session)

    with pytest.raises(DBAPIError, match="is immutable"), db_session.begin_nested():
        db_session.execute(
            text("UPDATE calculation SET quality = 'curated' WHERE id = :id"),
            {"id": calculation.id},
        )


def test_insert_and_delete_stay_refused_under_a_declaration(db_session) -> None:
    """A repair may change how a value is spelled, never how many rows exist."""
    _, geometry, atom = _accepted_atom(db_session, "ROWCOUNT")
    _declare(db_session)

    with pytest.raises(DBAPIError, match="is immutable"), db_session.begin_nested():
        db_session.add(
            GeometryAtom(
                geometry_id=geometry.id,
                atom_index=atom.atom_index + 1,
                element="H",
                x=0.0,
                y=0.0,
                z=0.0,
            )
        )
        db_session.flush()

    with pytest.raises(DBAPIError, match="is immutable"), db_session.begin_nested():
        db_session.execute(
            text("DELETE FROM geometry_atom WHERE geometry_id = :geometry_id AND atom_index = :atom_index"),
            {"geometry_id": geometry.id, "atom_index": atom.atom_index},
        )


def test_an_expired_declaration_permits_nothing(db_session) -> None:
    """The wall-clock bound, independent of the transaction bound.

    It exists so a wedged transaction cannot hold the door open, and it raises
    rather than falling through to the ordinary refusal so an operator is told
    what actually happened.
    """
    _, geometry, atom = _accepted_atom(db_session, "EXPIRED")
    _declare(db_session, ages=("- interval '2 hours'", "- interval '1 hour'"))

    with pytest.raises(DBAPIError, match="expired at"), db_session.begin_nested():
        db_session.execute(
            text(
                "UPDATE geometry_atom SET element = 'Cl' WHERE geometry_id = :geometry_id AND atom_index = :atom_index"
            ),
            {"geometry_id": geometry.id, "atom_index": atom.atom_index},
        )


def test_an_expiry_beyond_a_day_is_refused(db_session) -> None:
    with pytest.raises(DBAPIError, match="expiry_window"), db_session.begin_nested():
        _declare(db_session, ages=("- interval '0 seconds'", "+ interval '48 hours'"))


def test_a_second_declaration_for_one_table_is_refused(db_session) -> None:
    """ "The declaration in force" has to be a fact, not a resolution rule."""
    _declare(db_session)

    with pytest.raises(DBAPIError, match="already declared in this transaction"), db_session.begin_nested():
        _declare(db_session, columns=("x",))


def test_a_declaration_must_name_a_guarded_table(db_session) -> None:
    """A declaration against an unguarded table records a permission nobody needed."""
    with pytest.raises(DBAPIError, match="carries no accepted-science guard"), db_session.begin_nested():
        _declare(db_session, table="app_user", columns=("username",))


def test_a_declaration_must_name_columns_the_table_has(db_session) -> None:
    with pytest.raises(DBAPIError, match="does not have"), db_session.begin_nested():
        _declare(db_session, columns=("element", "no_such_column"))


def test_a_declaration_may_not_name_a_primary_key_column(db_session) -> None:
    """The record names the changed row by its key, so the key cannot move."""
    with pytest.raises(DBAPIError, match="primary-key column\\(s\\) atom_index"), db_session.begin_nested():
        _declare(db_session, columns=("atom_index", "element"))


def test_a_declaration_must_name_a_real_table(db_session) -> None:
    with pytest.raises(DBAPIError, match="is not a table"), db_session.begin_nested():
        _declare(db_session, table="no_such_table")


def test_a_blank_reason_is_refused(db_session) -> None:
    with pytest.raises(DBAPIError, match="reason_nonblank"), db_session.begin_nested():
        _declare(db_session, reason="   ")


def test_only_an_owner_of_the_target_may_declare_a_repair(db_session) -> None:
    """The authority test that makes this a record rather than a new power.

    The runtime role of ``backend/docs/deployment/database_roles.md`` owns
    nothing, so it fails here even holding the blanket ``INSERT`` that role's
    default privileges grant. A role that *could* declare could equally have
    run ``ALTER TABLE ... DISABLE TRIGGER`` and left no trace at all.
    """
    role = f"tckdb_repair_probe_{uuid4().hex[:8]}"
    db_session.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
    db_session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
    db_session.execute(text(f'GRANT SELECT, INSERT ON public.accepted_science_repair TO "{role}"'))
    db_session.execute(text(f'GRANT USAGE, SELECT ON SEQUENCE public.accepted_science_repair_id_seq TO "{role}"'))
    try:
        db_session.execute(text(f'SET LOCAL ROLE "{role}"'))
        with pytest.raises(DBAPIError, match="only an owner of"), db_session.begin_nested():
            _declare(db_session)
    finally:
        db_session.execute(text("RESET ROLE"))


def test_the_repair_record_is_append_only_and_untruncatable(db_session) -> None:
    _, geometry, atom = _accepted_atom(db_session, "APPENDONLY")
    _declare(db_session)
    db_session.execute(
        text("UPDATE geometry_atom SET element = 'Cl' WHERE geometry_id = :geometry_id AND atom_index = :atom_index"),
        {"geometry_id": geometry.id, "atom_index": atom.atom_index},
    )
    assert len(_changes(db_session)) == 1

    for statement, message in (
        ("UPDATE accepted_science_repair SET reason = 'rewritten'", "append-only"),
        ("DELETE FROM accepted_science_repair_change", "append-only"),
        (
            "UPDATE accepted_science_repair_change SET after_json = '{}'::jsonb",
            "append-only",
        ),
        ("TRUNCATE accepted_science_repair_change", "cannot be truncated"),
        (
            "TRUNCATE accepted_science_repair, accepted_science_repair_change",
            "cannot be truncated",
        ),
    ):
        with pytest.raises(DBAPIError, match=message), db_session.begin_nested():
            db_session.execute(text(statement))


def test_a_change_row_cannot_be_forged(db_session) -> None:
    """The audit is only worth having if the audited party cannot write it.

    Every check here holds by construction when ``tckdb_repair_permits``
    appends the row, and none of them holds for a row written by hand to make
    a repair look narrower than it was.
    """
    repair_id = _declare(db_session)
    insert = text(
        "INSERT INTO accepted_science_repair_change "
        "(repair_id, record_type, record_id, target_schema, target_table, "
        " row_identity, changed_columns, before_json, after_json) "
        "VALUES (:repair_id, 'calculation', 1, 'public', :table, "
        " '{\"geometry_id\": 1}'::jsonb, :columns, '{}'::jsonb, '{}'::jsonb)"
    )

    with pytest.raises(DBAPIError, match="does not cover"), db_session.begin_nested():
        db_session.execute(
            insert,
            {"repair_id": repair_id, "table": "geometry_atom", "columns": ["x"]},
        )
    with pytest.raises(DBAPIError, match="different table"), db_session.begin_nested():
        db_session.execute(
            insert,
            {"repair_id": repair_id, "table": "geometry", "columns": ["element"]},
        )
    with pytest.raises(DBAPIError, match="names no declaration"), db_session.begin_nested():
        db_session.execute(
            insert,
            {
                "repair_id": repair_id + 10_000_000,
                "table": "geometry_atom",
                "columns": ["element"],
            },
        )


def test_a_declaration_cannot_name_another_transaction(db_session) -> None:
    """The transaction binding, on the write side.

    ``tckdb_repair_permits`` matches on ``xact_id``, so a declaration that
    could name a transaction other than its own would be a standing permission
    with a timestamp on it.
    """
    with pytest.raises(DBAPIError, match="cannot name another transaction"), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO accepted_science_repair "
                "(target_table, declared_columns, alembic_revision, reason, xact_id) "
                "VALUES ('geometry_atom', ARRAY['element'], :revision, 'forged', "
                "(pg_current_xact_id())::text::bigint + 1)"
            ),
            {"revision": _REVISION},
        )


def test_the_declaration_records_who_made_it(db_session) -> None:
    repair_id = _declare(db_session)
    row = (
        db_session.execute(
            text(
                "SELECT declared_by_role, declared_by_login, alembic_revision, reason, "
                "declared_columns, xact_id = (pg_current_xact_id())::text::bigint AS mine "
                "FROM accepted_science_repair WHERE id = :id"
            ),
            {"id": repair_id},
        )
        .mappings()
        .one()
    )
    assert row["declared_by_role"] == row["declared_by_login"]
    assert row["alembic_revision"] == _REVISION
    assert "canonicalise" in row["reason"]
    assert row["declared_columns"] == ["element"]
    assert row["mine"] is True


def test_the_runtime_role_cannot_reach_the_guard_functions_by_name(db_session) -> None:
    """``tckdb_repair_permits`` is not an escape hatch a caller can invoke.

    It returns ``true`` only after appending a change row, and the change
    row's own validation refuses a caller who does not own the target. Calling
    it directly therefore cannot launder an update -- but the update it would
    have to launder still has to pass the same function from inside the
    trigger, so this pins that the direct call is not a shortcut around the
    recording.
    """
    _declare(db_session)
    permitted = db_session.execute(
        text(
            "SELECT public.tckdb_repair_permits('calculation', 1, 'public', 'geometry_atom', NULL::jsonb, NULL::jsonb)"
        )
    ).scalar_one()
    assert permitted is False
    assert _changes(db_session) == []


def test_every_guarded_table_is_declarable(db_session) -> None:
    """A declaration must be possible for exactly the tables the regime guards.

    Read off ``pg_trigger`` rather than off a list in this file, so a table
    that joins the regime later is covered here the day it does. The check is
    that the validation's notion of "guarded" is the same set the guards are
    actually installed on -- a mismatch either way leaves a table frozen with
    no route through, or a declaration that permits nothing.
    """
    guarded = {
        row[0]
        for row in db_session.execute(
            text(
                """
                SELECT DISTINCT relation.relname
                FROM pg_trigger AS guard
                JOIN pg_proc AS guard_function ON guard_function.oid = guard.tgfoid
                JOIN pg_class AS relation ON relation.oid = guard.tgrelid
                WHERE NOT guard.tgisinternal
                  AND guard_function.proname IN (
                      'tckdb_guard_accepted_root', 'tckdb_guard_accepted_child',
                      'tckdb_guard_accepted_via_child',
                      'tckdb_guard_calculation_geometry'
                  )
                """
            )
        )
    }
    assert len(guarded) > 40

    for table in sorted(guarded):
        # Any non-key column will do; the key itself is deliberately not
        # declarable, which the test above pins.
        column = db_session.execute(
            text(
                "SELECT attribute.attname FROM pg_attribute AS attribute "
                "WHERE attribute.attrelid = format('public.%I', cast(:table as text))::regclass "
                "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM pg_index AS index_ "
                "   WHERE index_.indrelid = attribute.attrelid AND index_.indisprimary "
                "     AND attribute.attnum = ANY (index_.indkey)) "
                "ORDER BY attribute.attnum LIMIT 1"
            ),
            {"table": table},
        ).scalar_one()
        with db_session.begin_nested() as nested:
            _declare(db_session, table=table, columns=(column,))
            nested.rollback()


def test_a_role_without_insert_cannot_declare(db_session) -> None:
    """Belt to the owner check's braces: the grant matters too, where it holds."""
    role = f"tckdb_repair_nogrant_{uuid4().hex[:8]}"
    db_session.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
    db_session.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
    try:
        db_session.execute(text(f'SET LOCAL ROLE "{role}"'))
        with pytest.raises(ProgrammingError, match="permission denied"), db_session.begin_nested():
            _declare(db_session)
    finally:
        db_session.execute(text("RESET ROLE"))
