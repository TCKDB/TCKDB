"""What the database calls a rule must be what the model calls it.

A constraint whose catalog name the ORM does not predict enforces the
right rule and still costs something real: ``alembic revision
--autogenerate`` diffs catalog names against ``Base.metadata`` names, so
it reads the mismatch as drift and proposes dropping and recreating a
constraint that is already correct. The next person to touch the table is
handed that diff and has to establish from scratch that it is spurious.
Six constraints were in that state before ``b7e4d1a9c026``, across three
revisions and eighteen months, and no test noticed -- which is the actual
finding here: the names were never measured against anything.

The mechanism, once, because it is not obvious and it will recur.
``NAMING_CONVENTION`` spells checks ``ck_%(table_name)s_%(constraint_name)s``.
That template interpolates ``%(constraint_name)s``, so SQLAlchemy applies
it to constraints that were given an explicit name as well as to
anonymous ones. Hand ``op.create_check_constraint`` (or
``sa.CheckConstraint(name=...)``) a name that already starts
``ck_<table>_`` and the prefix is applied a second time; over 63
characters PostgreSQL truncates and appends a hash. The ``uq``, ``ix``
and ``fk`` templates key off column names instead, so they do not do
this -- which is why the trap catches only checks, and why it looks like
nothing when you test one ``uq`` and generalise.

``test_every_check_constraint_is_named_as_the_model_predicts`` is the
guard that would have caught all six on the day each was written. It is
deliberately a whole-schema sweep rather than an assertion about the six:
the failure mode is a *new* revision repeating the mistake, and a test
enumerating today's constraints would pass through that unchanged.
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from sqlalchemy import text

import app.db.models  # noqa: F401  (populates Base.metadata)
from app.db.base import Base

#: ``ck_<table>_ck_<table>_...``, plus the truncated form where the
#: doubled prefix has been cut short and hashed. Matching the shape
#: rather than the six known names is the point: this catches the seventh.
_DOUBLED_PREFIX = re.compile(r"^ck_(.+?)_ck_\1")


def _named_check_constraints() -> set[tuple[str, str]]:
    """``(table, name)`` for every explicitly named CHECK in the ORM."""
    return {
        (table.name, str(constraint.name))
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name is not None
    }


def test_every_check_constraint_is_named_as_the_model_predicts(db_session):
    """Every CHECK the ORM declares exists in the catalog under that name.

    Stated as a set difference in both directions, and reported as two
    lists, because the two halves mean different things. A name the model
    expects and the database lacks is a constraint that is missing, named
    differently, or spelled differently. A ``ck_`` name the database has
    and the model does not expect is the residue -- typically the same
    constraint under its mis-expanded name. Seeing both at once is what
    turns "autogenerate wants to change something" into a diagnosis.
    """
    expected = _named_check_constraints()
    actual = {
        (table, name)
        for table, name in db_session.execute(
            text(
                "SELECT conrelid::regclass::text, conname"
                "  FROM pg_constraint"
                " WHERE contype = 'c'"
                "   AND connamespace = current_schema()::regnamespace"
                "   AND conname LIKE 'ck\\_%'"
            )
        ).all()
    }

    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    assert not missing and not unexpected, (
        "the database and app/db/models disagree about CHECK constraint "
        "names, so `alembic revision --autogenerate` will propose dropping "
        "and recreating constraints that are already correct.\n"
        f"  declared in the ORM, absent from the database: {missing}\n"
        f"  present in the database, not declared in the ORM: {unexpected}\n"
        "If a migration passed an already-expanded 'ck_<table>_...' name to "
        "op.create_check_constraint, pass the short name instead -- "
        "NAMING_CONVENTION adds the prefix. See b7e4d1a9c026."
    )


def test_no_constraint_carries_its_table_prefix_twice(db_session):
    """The specific shape, named so the failure explains itself.

    Redundant with the sweep above when both are green, and not when they
    are not: this one fires on the doubled prefix alone, which is the
    fingerprint of the mistake, so a reader who hits it knows what was
    done wrong without diffing two lists.
    """
    doubled = [
        f"{table}.{name}"
        for table, name in db_session.execute(
            text(
                "SELECT conrelid::regclass::text, conname"
                "  FROM pg_constraint"
                " WHERE contype = 'c'"
                "   AND connamespace = current_schema()::regnamespace"
            )
        ).all()
        if _DOUBLED_PREFIX.match(name)
    ]
    assert not doubled, (
        "these constraints carry their table prefix twice, which means a "
        "migration passed op.create_check_constraint a name that already "
        f"began 'ck_<table>_': {doubled}. Pass the short name; "
        "NAMING_CONVENTION expands it."
    )


def test_every_index_the_model_declares_exists_by_name(db_session):
    """The same guard for indexes, which the ``uq``/``ix`` templates spare.

    Included because ``statmech_torsion``'s uniqueness rule is declared as
    an ``Index`` rather than a ``UniqueConstraint`` -- so the check
    constraint sweep above says nothing about it -- and because renaming
    it in ``b7e4d1a9c026`` is exactly the kind of change that goes into a
    model and not into a migration.
    """
    expected = {
        (table.name, index.name)
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.name is not None
    }
    actual = {
        (table, name)
        for table, name in db_session.execute(
            text(
                "SELECT tablename, indexname FROM pg_indexes"
                " WHERE schemaname = current_schema()"
            )
        ).all()
    }
    missing = sorted(expected - actual)
    assert not missing, (
        "these indexes are declared in app/db/models and are not in the "
        f"database under that name: {missing}. A model-side rename needs a "
        "migration carrying ALTER INDEX ... RENAME TO."
    )
