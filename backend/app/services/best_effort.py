"""Run an opportunistic, write-bearing enrichment without risking the payload.

Several upload hooks are declared best-effort: parameter extraction from an
input artifact, single-point-energy reconciliation from an output log, Hessian
extraction. Each *enriches* an artifact that has already been persisted, and
each route says so — "best-effort (never abort the upload)".

A ``try``/``except`` alone does not deliver that promise once the hook writes
to the database, and the reason is easy to miss:

* **A swallowed database error leaves the transaction aborted.** PostgreSQL
  marks the (sub)transaction failed at the point of error. Catching the
  exception in Python does not undo that; the next statement fails with
  "current transaction is aborted", and if nothing else touches the session
  the poison surfaces at ``COMMIT`` — after the response has been determined.
  A catch-all with no ``ROLLBACK TO SAVEPOINT`` is therefore *worse* than no
  catch-all: it converts a loud, immediate failure into a silent one that
  lands where it destroys the payload.

* **An unflushed mutation fails somewhere else entirely.** A hook that only
  assigns to a mapped attribute has issued no SQL when it returns. Its
  ``UPDATE`` is emitted by whatever flushes next — in practice the commit —
  which is outside every guard the hook wrote. This is the exact shape of the
  2026-08-05 incident.

:func:`isolated_best_effort` addresses both, and mirrors
:func:`app.services.idempotency.write_receipt_isolated`:

1. **Flush before the savepoint opens**, so rows the caller has pending are
   INSERTed outside it and cannot be rolled back with the enrichment.
2. **Savepoint around the enrichment alone**, with a flush *inside* it so a
   deferred failure surfaces where the savepoint can still absorb it.
3. **Roll back to the savepoint on any exception**, leaving the outer
   transaction alive, the payload intact, and the session usable.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

T = TypeVar("T")


def isolated_best_effort(
    session: Session,
    work: Callable[[], T],
    *,
    what: str,
) -> T | None:
    """Run *work* inside a ``SAVEPOINT``; return its value, or ``None`` on failure.

    :param session: The caller's session. Its transaction and everything
        already written into it survive any failure of *work*.
    :param work: The enrichment. May read, write, and flush freely.
    :param what: Short description used in the log line when *work* fails,
        e.g. ``"hessian extraction for 'freq.log'"``.

    Never raises. A failure is logged at ``WARNING`` with a traceback and
    reported as ``None``; the caller continues with its canonical work.

    Note that ``None`` is not by itself a failure signal — several hooks
    legitimately return ``None`` when there is nothing to do. Callers that
    must tell the two apart should have *work* return a sentinel.
    """
    # Part 1: get the caller's pending rows out of the savepoint's blast radius.
    session.flush()

    # Part 2: the enrichment, and only the enrichment, inside the savepoint.
    savepoint = session.begin_nested()
    try:
        result = work()
        # Flush *inside* the savepoint so an unstorable value fails here,
        # where it can be absorbed, rather than at the caller's COMMIT.
        session.flush()
    except Exception as exc:
        # Undo the enrichment and nothing else. Without this the aborted
        # (sub)transaction would poison every later statement in the request.
        savepoint.rollback()
        logger.warning(
            "%s skipped after a failure confined to it (%s); the upload is "
            "unaffected and was not aborted.",
            what,
            type(exc).__name__,
            exc_info=exc,
        )
        return None

    savepoint.commit()
    return result


__all__ = ["isolated_best_effort"]
