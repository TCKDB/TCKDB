"""``get_snapshot_db``: the session the kinetics selection routes read in.

A selection decides over one consistent state, so it needs a read-only REPEATABLE READ transaction, and that level
can only be chosen before a transaction's first statement. ``get_db``'s session is shared with every other
dependency of the request and may already have run one, so it would start READ COMMITTED and the selection would
fail with ``SnapshotNotConsistentError`` (a 500). These tests hold the three halves of that:

* the dependency opens the snapshot before anything else touches its session;
* a session that was not opened that way is refused by the service, not silently answered;
* the routes take the dependency (the structural half is in ``test_api_kinetics_select.py``).

They use a real engine (the suite's default session factory is bound to one outer transaction, which cannot become
a snapshot) and write nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api import deps as api_deps
from app.services.kinetics_selection.public import SNAPSHOT_FLAG
from app.services.read_snapshot import SnapshotNotConsistentError, begin_read_snapshot


@pytest.fixture
def real_factory(db_engine):
    factory = api_deps.SessionLocal
    previous = factory.kw.get("bind")
    factory.configure(bind=db_engine)
    try:
        yield factory
    finally:
        factory.configure(bind=previous)


def _setting(session: Session, name: str) -> str:
    return str(session.scalar(text(f"SELECT current_setting('{name}')")))


def test_the_dependency_yields_a_read_only_repeatable_read_session_flagged_as_a_snapshot(real_factory):
    generator = api_deps.get_snapshot_db()
    session = next(generator)
    try:
        assert _setting(session, "transaction_isolation") == "repeatable read"
        assert _setting(session, "transaction_read_only") == "on"
        assert session.info[SNAPSHOT_FLAG] is True
        with pytest.raises(DBAPIError, match="read-only transaction"):
            session.execute(text("CREATE TEMP TABLE snapshot_write_probe (i int)"))
    finally:
        generator.close()


def test_the_dependency_closes_its_session(real_factory):
    generator = api_deps.get_snapshot_db()
    session = next(generator)
    generator.close()
    assert not session.in_transaction()


def test_the_default_session_cannot_become_a_snapshot_once_a_statement_has_run(real_factory):
    """The failure the dependency exists to avoid: ``get_db``'s session after another dependency used it."""
    session = real_factory()
    try:
        session.scalar(text("SELECT 1"))  # what authentication does on the shared session
        assert _setting(session, "transaction_isolation") == "read committed"
        with pytest.raises(SnapshotNotConsistentError, match="cannot be changed after a statement has run"):
            begin_read_snapshot(session, require=True)
    finally:
        session.close()


def test_a_route_session_that_says_it_is_a_snapshot_but_is_not_is_refused_not_answered(client, db_session, world):
    """With ``get_db``'s kind of session the route would answer 500, never a silently inconsistent selection."""
    db_session.info[SNAPSHOT_FLAG] = True  # the harness session is READ COMMITTED and writable
    url = f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics/select"
    request = {
        "direction": "forward",
        "target": {"kind": "whole_reaction"},
        "coefficient_basis": "elementary_coefficient",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure": {"kind": "independent"},
    }
    with pytest.raises(SnapshotNotConsistentError):
        client.post(url, json=request)


def test_a_session_that_has_already_run_a_statement_is_refused_by_the_dependency(real_factory, monkeypatch):
    """The dependency insists on the snapshot: it does not quietly hand out a session that could not become one."""

    def used_session():
        session = Session(bind=real_factory.kw["bind"])
        session.scalar(text("SELECT 1"))
        return session

    monkeypatch.setattr(api_deps, "SessionLocal", used_session)
    with pytest.raises(SnapshotNotConsistentError):
        next(api_deps.get_snapshot_db())
