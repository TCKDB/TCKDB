"""Run write code for its verdict, and keep nothing (#577).

``/bundles/dry-run`` answers "would submit accept this?" by running submit
itself inside :func:`rehearsal` and rolling it back. This module is the one
place under ``app/workflows``, ``app/services`` and ``app/chemistry`` that
knows a rehearsal is happening: code being rehearsed must not be able to
tell, or it could behave differently on the dry run than on the submit it
predicts. ``tests/api/test_api_bundle_dry_run_submit_parity.py`` forbids the
"am I inside a savepoint?" checks everywhere else.

What the context manager guarantees, and what it does not
---------------------------------------------------------
Nothing written inside it survives: it opens a ``SAVEPOINT`` and rolls it
back whether the body succeeds or fails. While the body runs, these
attempts to publish its writes are refused with
:class:`RehearsalCommitRefused`:

* ``Session.commit()``, and releasing the rehearsal's own savepoint through
  the ORM (``before_commit``);
* ``Connection.commit()`` -- any DBAPI commit issued through the
  SQLAlchemy ``Connection`` the session is using (``commit`` event);
* SQL text sent through that connection that would end or publish the
  transaction: ``COMMIT``, ``END``, ``PREPARE TRANSACTION``, or ``RELEASE``
  of any savepoint not opened inside the rehearsal
  (``before_cursor_execute``).

* ``commit()`` on the raw DBAPI connection under that ``Connection``
  (#592): while the rehearsal runs, that one instance's ``commit`` is
  replaced by a refusal, and restored afterwards.

Not guarded: SQL text sent through a raw DBAPI *cursor*, and any other
connection, engine or session the rehearsed code opens for itself. Nothing
in the submit path does either, and
``tests/api/test_api_bundle_dry_run_submit_parity.py`` forbids reaching for
``dbapi_connection`` or ``driver_connection`` anywhere else under ``app/``,
which is how a raw cursor would be obtained.

It also keeps a rehearsal from hurting real writers. Its inserts take the
same locks a submit's do, so it waits at most ``lock_timeout`` for another
transaction's lock -- below the server's ``deadlock_timeout`` -- and, where
the role may, runs with a far shorter ``deadlock_timeout`` of its own. A
contended rehearsal is abandoned with :class:`RehearsalContended` rather than
becoming the reason a real submit fails; see :func:`rehearsal` for the
argument and its limits.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.exc import DBAPIError, ResourceClosedError
from sqlalchemy.orm import Session

#: Upper bound on how long a rehearsal waits for another transaction's lock.
#: The effective value is also held below half the server's
#: ``deadlock_timeout``; see :func:`rehearsal`.
LOCK_TIMEOUT_CEILING_MS = 500

#: The rehearsal's own ``deadlock_timeout``, set only where the role may.
REHEARSAL_DEADLOCK_TIMEOUT_MS = 10

#: SQLSTATEs that mean "another writer was in the way", and the word the
#: refusal publishes for each.
_CONTENDED_SQLSTATES = {"55P03": "lock_timeout", "40P01": "deadlock"}

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_SAVEPOINT = re.compile(r"^\s*SAVEPOINT\s+(\S+)", re.I)
_RELEASE = re.compile(r"^\s*RELEASE\s+(?:SAVEPOINT\s+)?(\S+)", re.I)
_ROLLBACK_TO = re.compile(
    r"^\s*ROLLBACK\s+(?:WORK\s+|TRANSACTION\s+)?TO\s+(?:SAVEPOINT\s+)?(\S+)", re.I
)
_PUBLISHING = re.compile(r"^\s*(COMMIT|END|PREPARE\s+TRANSACTION)\b", re.I)


class RehearsalCommitRefused(RuntimeError):
    """Code under rehearsal tried to publish its writes."""


class RehearsalContended(RuntimeError):
    """The rehearsal met another writer's lock and gave way.

    :param reason: ``"lock_timeout"`` or ``"deadlock"``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"rehearsal abandoned: {reason}")
        self.reason = reason


def discard_unflushed_writes(session: Session) -> None:
    """Drop changes the session holds but has not yet sent.

    A leftover safeguard. Until #587 ``authenticate_api_key`` set
    ``api_key.last_used_at`` on the session a dry run reads with; the first
    query flushed it, and the row lock that UPDATE took was held for the whole
    rehearsal, so a second dry run on the same key queued behind the first
    (#577 review, F2). The stamp is now written on its own connection, so
    nothing of the kind is pending any more, but a dry-run session never
    commits, so discarding whatever a future dependency leaves unflushed
    costs nothing and keeps that failure from returning.
    """
    for obj in list(session.new) + list(session.deleted):
        session.expunge(obj)
    for obj in list(session.dirty):
        session.expire(obj)


def _statements(sql: str) -> Iterator[str]:
    for part in _COMMENTS.sub(" ", sql).split(";"):
        if part.strip():
            yield part


def _savepoint_name(raw: str) -> str:
    """The name PostgreSQL would resolve: quoted as written, else folded to lower case."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].replace('""', '"')
    return raw.lower()


def _most_recent(stack: list[str], name: str) -> int | None:
    """Index of the newest savepoint called ``name``, as PostgreSQL picks it."""
    for index in range(len(stack) - 1, -1, -1):
        if stack[index] == name:
            return index
    return None


@contextmanager
def rehearsal(session: Session) -> Iterator[None]:
    """Run the body inside a savepoint that is always rolled back.

    Why the lock settings are what they are. A rehearsal's inserts take the
    locks a submit's would, so a dry run and a real submit touching the same
    rows can block each other or deadlock, and PostgreSQL's deadlock
    detector aborts whichever *waiter* checks first. Two cases:

    * The rehearsal waits first, the submit second. The rehearsal checks for
      a deadlock once, ``deadlock_timeout`` after it began waiting, when
      there was none yet; the submit checks ``deadlock_timeout`` after it
      began, and by then it would find one. So the rehearsal must have given
      up before that: its ``lock_timeout`` is held under the server's
      ``deadlock_timeout``, and it always does.
    * The submit waits first, the rehearsal second. The rehearsal's own
      check finds the cycle ``REHEARSAL_DEADLOCK_TIMEOUT_MS`` after it began
      waiting, long before the submit's does -- where the role may set
      ``deadlock_timeout``, which needs a superuser. The role that ships
      does not have that: ``.env.selfhosted.example`` sets
      ``DB_USER=tckdb_app``, and ``docs/deployment/production_checklist.md``
      and ``docs/deployment/database_roles.md`` require a non-superuser API
      role. So the path that runs in deployment is the second one: the
      rehearsal keeps the server's ``deadlock_timeout``, and the window in
      which the submit can still lose is ``lock_timeout`` wide. That path
      was measured over 108 forced collisions (#592) and the real submit won
      every one. The superuser branch is kept for a deployment that runs the
      API as one; it is not what is tested against the shipped role.

    What this cannot fix is plain blocking in the other direction: a submit
    that needs a row the rehearsal has just inserted waits until the
    rehearsal ends. That is bounded by the rehearsal's own duration.

    :raises RehearsalContended: when the body met a lock timeout or was
        chosen as a deadlock victim.
    """
    discard_unflushed_writes(session)
    connection = session.connection()
    # The savepoints open on the connection, oldest first, mirroring
    # PostgreSQL's own stack. ``stack[0]`` is the rehearsal's; a name can
    # appear more than once (PostgreSQL allows it, and a RELEASE or
    # ROLLBACK TO addresses the *newest* one), so identity is the position,
    # never the name (#592).
    stack: list[str] = []
    # Set when a refusal fired below the ORM. SQLAlchemy may by then believe
    # the transaction is over, and return the connection to the pool without
    # rolling it back -- still holding the rehearsal's writes, for the next
    # request on that connection to see or commit. See ``finally``.
    below_orm: list[bool] = []

    def _refuse(what: str) -> RehearsalCommitRefused:
        return RehearsalCommitRefused(
            f"a rehearsal tried to {what}; its writes must be rolled back"
        )

    def _guard_sql(_conn, _cursor, statement, _params, _context, _many):
        for stmt in _statements(statement):
            if match := _SAVEPOINT.match(stmt):
                stack.append(_savepoint_name(match.group(1)))
            elif match := _RELEASE.match(stmt):
                index = _most_recent(stack, _savepoint_name(match.group(1)))
                if not index:  # unknown, or the rehearsal's own at position 0
                    below_orm.append(True)
                    raise _refuse(f"release a savepoint it did not open ({stmt.strip()!r})")
                del stack[index:]  # releases it and everything opened after it
            elif match := _ROLLBACK_TO.match(stmt):
                index = _most_recent(stack, _savepoint_name(match.group(1)))
                if index is not None:
                    del stack[index + 1 :]  # the target stays; later ones are gone
            elif _PUBLISHING.match(stmt):
                below_orm.append(True)
                raise _refuse(f"send {stmt.strip()!r}")

    def _guard_connection_commit(_conn) -> None:
        below_orm.append(True)
        raise _refuse("commit its connection")

    # The driver's own connection object. Its ``commit`` is shadowed on this
    # one instance -- the class is untouched, so no other connection is
    # affected -- and the shadow is removed in ``finally``. SQLAlchemy's own
    # commit reaches this method only after the ``commit`` event above has
    # already refused, so the two never disagree about what is refused.
    raw_connection = connection.connection.dbapi_connection

    def _guard_raw_commit(*_args, **_kwargs) -> None:
        below_orm.append(True)
        raise _refuse("commit the raw DBAPI connection")

    event.listen(connection, "before_cursor_execute", _guard_sql)
    event.listen(connection, "commit", _guard_connection_commit)
    if raw_connection is not None:
        raw_connection.commit = _guard_raw_commit
    savepoint = session.begin_nested()

    def _guard_session_commit(target: Session) -> None:
        # ``before_commit`` fires for every SAVEPOINT release too, and
        # submit releases one of its own inside this one
        # (``_append_import_audit``); that is harmless. Releasing *this*
        # savepoint, or committing the transaction around it, is not: a
        # ``Session.commit()`` releases every savepoint on its way to the
        # root, innermost first, so refusing when this one's turn comes stops
        # it before the rehearsal's writes leave the savepoint.
        if target.get_nested_transaction() is not savepoint and target.in_nested_transaction():
            return
        raise _refuse("commit the session")

    event.listen(session, "before_commit", _guard_session_commit)
    try:
        # Emits the SAVEPOINT now, so the guard records it as the
        # rehearsal's own before anything inside can open another.
        rehearsal_connection = session.connection()
        deadlock_ms = rehearsal_connection.exec_driver_sql(
            "SELECT setting::int FROM pg_settings WHERE name = 'deadlock_timeout'"
        ).scalar_one()
        lock_ms = max(1, min(LOCK_TIMEOUT_CEILING_MS, deadlock_ms // 2))
        rehearsal_connection.exec_driver_sql(f"SET LOCAL lock_timeout = {int(lock_ms)}")
        if rehearsal_connection.exec_driver_sql(
            "SELECT current_setting('is_superuser') = 'on'"
        ).scalar_one():
            rehearsal_connection.exec_driver_sql(
                f"SET LOCAL deadlock_timeout = {int(REHEARSAL_DEADLOCK_TIMEOUT_MS)}"
            )
        yield
    except DBAPIError as exc:
        reason = _CONTENDED_SQLSTATES.get(getattr(exc.orig, "sqlstate", None) or "")
        if reason is None:
            raise
        raise RehearsalContended(reason) from exc
    finally:
        # The listeners and the raw-commit shadow live on per-request
        # objects, but a raise from the rollback below must not leave them
        # attached: they are removed in an inner ``finally`` (#592).
        try:
            if below_orm:
                # Discard the physical connection: the server aborts its open
                # transaction when it goes, whatever SQLAlchemy believes about
                # it, so no pooled connection can carry these writes onward.
                connection.invalidate()
            else:
                try:
                    savepoint.rollback()
                except ResourceClosedError:
                    # Already rolled back: SQLAlchemy does that itself when the
                    # ORM-level release of it is refused above. It cannot have
                    # been released instead -- that is what the guards refuse.
                    pass
        finally:
            if raw_connection is not None:
                raw_connection.__dict__.pop("commit", None)
            event.remove(session, "before_commit", _guard_session_commit)
            event.remove(connection, "commit", _guard_connection_commit)
            event.remove(connection, "before_cursor_execute", _guard_sql)


__all__ = [
    "LOCK_TIMEOUT_CEILING_MS",
    "REHEARSAL_DEADLOCK_TIMEOUT_MS",
    "RehearsalCommitRefused",
    "RehearsalContended",
    "discard_unflushed_writes",
    "rehearsal",
]
