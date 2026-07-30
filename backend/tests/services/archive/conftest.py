"""Sequence-state containment for the archive tests.

``restore_archive`` finishes by repairing every primary-key sequence to
``max(id)`` of the rows it just inserted (``_repair_sequences`` in
`app/services/archive/core.py`). That is correct in production, where a
restore targets a genuinely empty database and commits.

Under pytest it is a cross-file isolation leak. PostgreSQL sequences live
outside MVCC: ``setval`` is **not** undone when the surrounding
transaction rolls back (see the PostgreSQL docs on ``setval`` —
"because sequences are non-transactional, changes made by setval are not
undone if the transaction rolls back"). So although every archive test
rolls its rows back, the rewound sequence counters survive into the rest
of the pytest session.

The damage is only visible when several test files share one database.
Many suites in this repo legitimately use the session-scoped
``db_engine`` fixture and commit (``tests/services/test_calculation_resolution.py``
and ~40 others), so the shared test database really does hold committed
rows at low ids. A rewound sequence then hands those ids out a second
time and the next insert dies on a duplicate-key violation, e.g.::

    duplicate key value violates unique constraint "pk_species"
    DETAIL:  Key (id)=(2) already exists.

Every archive test passes on its own, which is why CI — one fresh
database per gate job — never saw this.

The fixture below makes the archive tests clean up after themselves: it
snapshots every user sequence before the test and restores the exact
``(last_value, is_called)`` pair afterwards, so the tests leave sequence
state exactly as they found it. Sequence writes are non-transactional in
both directions, so the restore is effective whether it lands before or
after the test transaction's rollback, and it uses its own connection so
an aborted test transaction cannot prevent cleanup.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import Engine, text

# Fixtures that imply the test touches the shared test database. Tests that
# use none of these (the pure registry/codec checks) need no containment and
# must not pay for a database round trip.
_DB_FIXTURES = frozenset({"db_session", "db_conn", "db_engine", "client"})

_SequenceState = dict[str, tuple[int, bool]]


def _user_sequences(connection) -> list[str]:
    """Return every non-system sequence as a quoted ``schema.name``."""
    return list(
        connection.scalars(
            text(
                """
                SELECT quote_ident(schemaname) || '.' || quote_ident(sequencename)
                FROM pg_sequences
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY schemaname, sequencename
                """
            )
        )
    )


def _snapshot_sequences(engine: Engine) -> _SequenceState:
    with engine.connect() as connection:
        names = _user_sequences(connection)
        if not names:
            return {}
        # One round trip instead of one per sequence: selecting from a sequence
        # relation yields its current (last_value, is_called).
        union = " UNION ALL ".join(
            f"SELECT {index} AS ord, last_value, is_called FROM {name}"
            for index, name in enumerate(names)
        )
        return {
            names[row.ord]: (row.last_value, row.is_called)
            for row in connection.execute(text(union))
        }


def _restore_sequences(engine: Engine, snapshot: _SequenceState) -> None:
    if not snapshot:
        return
    with engine.begin() as connection:
        for name, (last_value, is_called) in snapshot.items():
            connection.execute(
                text("SELECT setval(CAST(:sequence AS regclass), :value, :is_called)"),
                {"sequence": name, "value": last_value, "is_called": is_called},
            )


@pytest.fixture(autouse=True)
def _preserve_sequence_state(request) -> Iterator[None]:
    """Leave PostgreSQL sequence counters as the archive test found them.

    Autouse rather than opt-in: any archive test may call
    ``restore_archive``, and forgetting the guard reintroduces a failure
    that only reproduces when test files are combined — exactly the class
    of bug per-job-fresh-database CI cannot catch.
    """
    if not _DB_FIXTURES & set(request.fixturenames):
        yield
        return

    engine: Engine = request.getfixturevalue("db_engine")
    # Resolve the session-scoped API user first: it commits an app_user and an
    # api_key row, advancing those sequences. Snapshotting before that commit
    # would restore counters to a value that re-issues the committed user's id.
    request.getfixturevalue("_api_test_user")

    snapshot = _snapshot_sequences(engine)
    try:
        yield
    finally:
        _restore_sequences(engine, snapshot)
