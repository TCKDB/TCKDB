"""The proof that a database-enforced scientific refusal names itself.

The sibling file ``test_api_scientific_rejection_codes.py`` does this for
refusals raised in Python. This one does it for the population that is
harder to reach and, until now, told a client the least: the positions
PostgreSQL holds. Those are the *strongest* guarantees TCKDB makes --
no write path, no bulk loader and no future service can get around a
check constraint -- and every one of them used to arrive as the same
generic ``409 state_conflict``, so a client could not tell "an atom
changed element on the way across this reaction" from "some consistency
rule fired somewhere".

Two independent facts have to hold, and they fail in different ways, so
they are proved separately:

1. **PostgreSQL reports the constraint by the name the register uses.**
   Proved by violating the real constraint on the real schema and
   reading ``diag.constraint_name`` off the driver's own error. This is
   the half that a migration renaming a constraint would break, and it
   is why the provocation is a genuine failing INSERT rather than a
   hand-built exception. It also pins down a measured fact the mapping
   depends on: a NOT NULL violation (SQLSTATE 23502) reports *no*
   constraint name at all, which is why ``DatabaseConstraint`` refuses to
   accept a rejection code for a kind PostgreSQL does not name.

2. **The handler turns that name into the declared code, in the body --
   and leaves the name behind.** Proved by putting the real
   ``IntegrityError`` from (1) through the real exception handlers --
   ``register_exception_handlers`` is the production wiring -- and
   asserting ``response.json()["code"]``, not that the code appears
   somewhere in the prose. The assertion has to be on the field, because
   matching the message is exactly what passes on the broken system.

   The second half of that sentence is a **contract change made on
   2026-08-18**, and this file is where it bites hardest. Until then this
   test asserted ``body["context"]["constraint"] == name``: the register
   path published the raw constraint name, on the argument that it is the
   key into ``docs/guides/scientific_check_register.md``. Calvin ruled
   the other way, and the ruling is about what an identifier *is* rather
   than about how useful it would be -- a constraint name is internal,
   meaningless to a depositor, unstable across a rename, and a disclosure
   of schema layout, which is the same reasoning that keeps row ids out
   of bodies under DR-0028 Requirement 2. What a registered constraint
   earns is therefore a code and a sentence of its own, which is a public
   contract; the name goes to the log, and the assertion below is now
   that it is **absent**.

   An absence assertion is worthless on its own -- it passes against a
   handler that returns nothing at all -- so each one here sits beside
   the positive it guards: the status, the declared code, the declared
   sentence, and an empty ``context``. The absence is additionally
   enforced for every body in the suite by
   ``tests/error_body_observer.py``, which is what stops this rule
   drifting back into three positions.

Provoking a violation without a valid parent row
------------------------------------------------
``network_solve_energy_transfer`` needs a solve, which needs a network,
which needs a reaction. Building all of that would test the upload path
rather than the constraint. Instead each provocation fills every NOT NULL
column that has no default with a type-appropriate dummy, overrides the
columns under test, and runs inside ``SET LOCAL session_replication_role
= replica``, which suspends foreign-key triggers while leaving CHECK and
UNIQUE constraints fully armed. It is transaction-local and rolls back
with the test.

A new ``rejection_code`` in the register is not accepted until it is
named here -- see ``test_every_constraint_rejection_code_is_provoked``
in ``backend/tests/db/test_scientific_check_register.py``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.errors import register_exception_handlers
from app.scientific_checks.declarations import constraint_rejections

#: One provocation per registered constraint: the table, and the column
#: values that violate it. Everything else the row needs is filled in by
#: :func:`_violating_row`. Keyed by constraint name so the drift guard can
#: compare this against the register directly.
PROVOCATIONS: dict[str, tuple[str, dict[str, object], dict[str, object] | None]] = {
    # (table, violating values, values of a first row to insert for uniques)
    "ck_statmech_statmech_exactly_one_subject": (
        "statmech",
        {"species_entry_id": None, "transition_state_entry_id": None},
        None,
    ),
    "ck_network_solve_energy_transfer_scope_columns_agree": (
        "network_solve_energy_transfer",
        {"scope": "network_wide", "state_id": 1, "collider_species_entry_id": None},
        None,
    ),
    "ck_network_solve_reported_requires_literature": (
        "network_solve",
        {"kind": "reported", "literature_id": None},
        None,
    ),
    "ck_reaction_atom_map_pair_element_matches": (
        "reaction_atom_map_pair",
        {"element": "C", "ts_element": "N"},
        None,
    ),
    # The two uniques share a table, so each first row differs from its
    # violating row in exactly the column the *other* unique covers --
    # otherwise which of the two fires would depend on index order.
    #
    # ``element``/``ts_element`` are overridden on all four rows because
    # ``_dummy`` fills a ``character(2)`` with ``'x'``, which
    # ``ck_reaction_atom_map_pair_element_canonical`` (``c5a1f8e3d074``) now
    # refuses: an element symbol capitalises its first letter. Without the
    # override the canonicality CHECK would fire first and these provocations
    # would prove something about the wrong constraint.
    "uq_reaction_atom_map_pair_ts_atom_index": (
        "reaction_atom_map_pair",
        {"atom_map_id": 1, "ts_atom_index": 7, "structure_participant_id": 2,
         "element": "C", "ts_element": "C"},
        {"atom_map_id": 1, "ts_atom_index": 7, "structure_participant_id": 1,
         "element": "C", "ts_element": "C"},
    ),
    "uq_reaction_atom_map_pair_atom_map_id": (
        "reaction_atom_map_pair",
        {"atom_map_id": 1, "atom_index": 5, "structure_participant_id": 1,
         "ts_atom_index": 9, "element": "C", "ts_element": "C"},
        {"atom_map_id": 1, "atom_index": 5, "structure_participant_id": 1,
         "ts_atom_index": 8, "element": "C", "ts_element": "C"},
    ),
}

_NOT_NULL_COLUMNS = """
SELECT a.attname,
       format_type(a.atttypid, a.atttypmod) AS coltype,
       t.typtype,
       (SELECT e.enumlabel FROM pg_enum e
         WHERE e.enumtypid = a.atttypid ORDER BY e.enumsortorder LIMIT 1) AS enum_first
  FROM pg_attribute a
  JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_type t ON t.oid = a.atttypid
 WHERE c.relname = :table
   AND a.attnum > 0
   AND NOT a.attisdropped
   AND a.attnotnull
   AND NOT EXISTS (
       SELECT 1 FROM pg_attrdef d
        WHERE d.adrelid = c.oid AND d.adnum = a.attnum)
"""


def _dummy(coltype: str, typtype: str, enum_first: str | None) -> object:
    """A value of the right type, chosen only to satisfy NOT NULL."""
    if typtype == "e":
        return enum_first
    if coltype.startswith(("integer", "bigint", "smallint")):
        return 1
    if coltype.startswith(("numeric", "double", "real")):
        return 1
    if coltype.startswith("boolean"):
        return False
    if coltype.startswith(("timestamp", "date")):
        return "2020-01-01T00:00:00"
    if coltype.startswith("json"):
        return "{}"
    if coltype.startswith("uuid"):
        return "00000000-0000-0000-0000-000000000000"
    return "x"


def _row(session, table: str, overrides: dict[str, object]) -> dict[str, object]:
    rows = session.execute(text(_NOT_NULL_COLUMNS), {"table": table}).mappings().all()
    values: dict[str, object] = {
        row["attname"]: _dummy(row["coltype"], row["typtype"], row["enum_first"])
        for row in rows
    }
    values.update(overrides)
    return values


def _insert(session, table: str, values: dict[str, object]) -> None:
    columns = ", ".join(f'"{name}"' for name in values)
    binds = ", ".join(f":{name}" for name in values)
    session.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({binds})"), values)
    session.flush()


def _provoke(session, name: str) -> IntegrityError:
    """Violate *name* for real and hand back the driver's own error."""
    table, violating, seed = PROVOCATIONS[name]
    savepoint = session.begin_nested()
    try:
        session.execute(text("SET LOCAL session_replication_role = replica"))
        if seed is not None:
            _insert(session, table, _row(session, table, seed))
        with pytest.raises(IntegrityError) as caught:
            _insert(session, table, _row(session, table, violating))
        return caught.value
    finally:
        savepoint.rollback()


@pytest.fixture
def envelope_client() -> TestClient:
    """A client whose single route re-raises whatever it is given.

    Built with the production ``register_exception_handlers`` so the path
    under test is the real one: handler, envelope, JSON encoder. The
    route exists only to get an exception into that path -- there is no
    real endpoint that inserts a malformed ``statmech`` row, and inventing
    one would prove something about the fake route instead.
    """
    app = FastAPI()
    holder: dict[str, BaseException] = {}

    @app.get("/boom")
    def boom() -> None:  # pragma: no cover - raises by design
        raise holder["exc"]

    register_exception_handlers(app)
    client = TestClient(app, raise_server_exceptions=False)
    client.holder = holder  # type: ignore[attr-defined]
    return client


@pytest.mark.parametrize("name", sorted(PROVOCATIONS))
def test_postgres_reports_the_constraint_the_register_names(name, db_session):
    """Fact 1: the violation arrives labelled with the registered name."""
    error = _provoke(db_session, name)
    reported = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    assert reported == name, (
        f"Provoking {name!r} produced a violation PostgreSQL labelled "
        f"{reported!r}. The handler keys on that label, so a mapping under the "
        "registered name would never be consulted. Either the provocation trips "
        "a different constraint first, or the constraint was renamed in a "
        "migration without the register following."
    )


@pytest.mark.parametrize("name", sorted(PROVOCATIONS))
def test_the_response_body_names_the_scientific_rule(name, db_session, envelope_client):
    """Fact 2: the declared code reaches ``response.json()["code"]``."""
    expected_code, expected_detail = constraint_rejections()[name]
    envelope_client.holder["exc"] = _provoke(db_session, name)

    response = envelope_client.get("/boom")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == expected_code, (
        f"expected code={expected_code!r} for {name!r}, got {body['code']!r}. "
        "A generic integrity code here means the client is back to being told "
        "only that something conflicted."
    )
    assert body["detail"] == expected_detail
    # The sanitization the handler has always promised: no driver text.
    assert "psycopg" not in response.text
    assert "INSERT INTO" not in response.text


@pytest.mark.parametrize("name", sorted(PROVOCATIONS))
def test_the_response_body_does_not_disclose_the_constraint_name(
    name, db_session, envelope_client
):
    """Fact 3, and a contract change: the name stays on this side.

    Until 2026-08-18 this file asserted the opposite -- ``body["context"]
    ["constraint"] == name`` -- and it was one of three positions the
    repository held at once. See the module docstring for the ruling.

    The positives are asserted first and deliberately: an assertion that
    a string is missing from a body passes just as well against a handler
    that produced no body, so the absence is only evidence when the thing
    it is carved out of is known to be there.
    """
    expected_code, expected_detail = constraint_rejections()[name]
    envelope_client.holder["exc"] = _provoke(db_session, name)

    response = envelope_client.get("/boom")

    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == expected_code
    assert body["detail"] == expected_detail
    assert isinstance(body["detail"], str) and body["detail"].strip()
    # And now the absence, over the whole rendered body rather than one
    # field, because a name moved to a differently-named key would still
    # be a disclosure.
    assert name not in response.text, (
        f"the raw constraint name {name!r} reached the public body. It is an "
        "internal identifier; the code above is what a client branches on and "
        "the name belongs in the log."
    )
    assert body["context"] == {}


def test_an_unregistered_constraint_still_falls_back_to_its_sqlstate(
    db_session, envelope_client
):
    """Naming some constraints must not stop the others being handled.

    The fallback is the whole reason the mapping is a lookup rather than a
    rewrite of the handler: the register covers the positions that encode
    chemistry, and the several hundred constraints that encode plumbing
    must keep arriving as the sanitized SQLSTATE bucket rather than as a
    ``KeyError`` or as raw driver text.
    """
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(text("SET LOCAL session_replication_role = replica"))
        with pytest.raises(IntegrityError) as caught:
            _insert(
                db_session,
                "species",
                _row(db_session, "species", {"charge": None}),
            )
    finally:
        savepoint.rollback()

    envelope_client.holder["exc"] = caught.value
    response = envelope_client.get("/boom")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "state_conflict"
    assert isinstance(body["detail"], str) and body["detail"].strip()
    assert body["context"] == {}
    assert "null value in column" not in response.text
