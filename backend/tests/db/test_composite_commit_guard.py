"""The commit guard is installed on real sessions and refuses an unfinished composite (ADR 0021, P5).

A guard that is defined but never installed passes every test that calls it directly; this one commits
through a real ``Session`` so removing the install call fails here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.db import models as _models  # noqa: F401  (importing the models installs the guard)
from app.db.composite_commit_guard import (
    PENDING_COMPOSITE_KEY,
    forget_pending_composites_on_rollback,
    refuse_unfinished_composites,
)


def test_the_guard_and_the_rollback_cleanup_are_registered_on_every_session():
    assert event.contains(Session, "before_commit", refuse_unfinished_composites)
    assert event.contains(Session, "after_soft_rollback", forget_pending_composites_on_rollback)


def test_a_real_session_cannot_commit_with_an_unfinished_composite(db_engine):
    with Session(db_engine) as session:
        session.info[PENDING_COMPOSITE_KEY] = {1: object()}
        with pytest.raises(RuntimeError, match="finalize_composite_inputs"):
            session.commit()
        session.rollback()


def test_a_real_session_commits_normally_with_nothing_pending(db_engine):
    with Session(db_engine) as session:
        session.execute(text("SELECT 1"))
        session.commit()
