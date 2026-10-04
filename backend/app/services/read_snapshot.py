"""A read-only REPEATABLE READ snapshot for a read that must see one consistent state.

A selection reads a population (its review states, then its rows, then their children and the references they
name) over several statements. Under the default READ COMMITTED isolation a commit between two of them can
change what the next one sees: a record approved halfway through, a row superseded between the count and the
load. A selection that is to be *replayable* must decide over one state, so it runs in one snapshot.

The isolation level can only be chosen before the transaction's first statement, so this helper does it when
the session has not started one, and otherwise *checks* what is in force and refuses a weaker level unless the
caller says it accepts it (a test that holds one outer transaction does). A route gets the guarantee from a
session opened for the purpose; this module is how a service insists on it.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

_SNAPSHOT_LEVELS = frozenset({"repeatable read", "serializable"})


class SnapshotNotConsistentError(RuntimeError):
    """The session's transaction is not a read-only snapshot and the caller did not accept that."""


def begin_read_snapshot(session: Session, *, require: bool = True) -> str:
    """Make the session's transaction a read-only REPEATABLE READ snapshot, or report what is in force.

    :param require: Refuse (``SnapshotNotConsistentError``) when the transaction is already running under a
        weaker isolation level, or is not read-only, and so cannot become a snapshot.
    :returns: The isolation level in force, lower-case (``"repeatable read"``).
    """
    if not session.in_transaction():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
    level = str(session.scalar(text("SELECT current_setting('transaction_isolation')")))
    read_only = session.scalar(text("SELECT current_setting('transaction_read_only')")) == "on"
    if require and (level not in _SNAPSHOT_LEVELS or not read_only):
        raise SnapshotNotConsistentError(
            f"this read needs a read-only REPEATABLE READ snapshot; the transaction is {level}"
            f"{'' if read_only else ' and not read-only'}, and an isolation level cannot be changed after a statement has run"
        )
    return level
