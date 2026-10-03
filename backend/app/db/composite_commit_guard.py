"""Refuse to commit an assembled composite whose inputs were never written (ADR 0021, P5).

``app.services.composite_input_resolution`` stores an assembled composite's result
when the calculation is created and writes its inputs later, once every
calculation of the request exists (``finalize_composite_inputs``). A workflow
that forgot that call would leave a composite energy with no recipe slots behind
it and a total nobody checked. Every workflow is held to the call by a structural
test (``tests/invariants/test_composite_p5_invariants.py``); this guard is the
runtime backstop, and it lives here, outside the packages the dry run rehearses,
because it must ask whether it is inside a savepoint: a savepoint's release
(``begin_nested().commit()``, which the best-effort enrichment hooks use
mid-request) dispatches ``before_commit`` too, and only the outermost commit is
the point of no return. It changes no branch of the rehearsed code: it only ever
raises on a programming error, never on data.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

#: ``session.info`` key holding ``{composite calculation id: pending composite}``.
PENDING_COMPOSITE_KEY = "pending_composite_inputs"


def refuse_unfinished_composites(session: Session) -> None:
    """``before_commit``: raise if an assembled composite still has no inputs written."""
    if session.in_nested_transaction():
        return
    pending = session.info.get(PENDING_COMPOSITE_KEY)
    if pending:
        raise RuntimeError(
            f"{len(pending)} assembled composite calculation(s) were persisted but their inputs were "
            "never written: a workflow that persists calculations must call finalize_composite_inputs "
            "once they all exist. Refusing to commit evidence-free composites."
        )


def forget_pending_composites_on_rollback(session: Session, previous_transaction: Any) -> None:
    """``after_soft_rollback``: a rolled-back request leaves no pending composite behind.

    The pending entries name calculations that the rollback just undid. Left in
    ``session.info`` they survive into the next transaction of the same session and
    make the guard above refuse an unrelated commit (a worker recording a job's
    failure, say). Only the root transaction's rollback clears them: a savepoint
    rolled back mid-request (a best-effort enrichment) undoes none of the composite's
    own rows.
    """
    if getattr(previous_transaction, "parent", None) is None:
        session.info.pop(PENDING_COMPOSITE_KEY, None)


def install_composite_commit_guard() -> None:
    """Register the guard and the rollback clean-up on every session. Idempotent."""
    if not event.contains(Session, "before_commit", refuse_unfinished_composites):
        event.listen(Session, "before_commit", refuse_unfinished_composites)
    if not event.contains(Session, "after_soft_rollback", forget_pending_composites_on_rollback):
        event.listen(Session, "after_soft_rollback", forget_pending_composites_on_rollback)
