"""``get_snapshot_db``: the session the kinetics selection routes read in.

A selection decides over one consistent state, so it needs a read-only REPEATABLE READ transaction, and that level
can only be chosen before a transaction's first statement. ``get_db``'s session is shared with every other
dependency of the request and may already have run one, so it would start READ COMMITTED. These tests hold the
halves of that:

* the dependency opens the snapshot before anything else touches its session;
* the real application, with no override, resolves the reaction entry and runs the selection on one connection that
  is REPEATABLE READ and read-only at both points, and hands the connection back clean;
* a session that is not a snapshot (``get_db``'s kind) is refused by the selection, loudly, and never answered under
  READ COMMITTED;
* the routes take the dependency (the structural half is in ``test_api_kinetics_select.py``).

They use a real engine (the suite's default session factory is bound to one outer transaction, which cannot become
a snapshot) and write nothing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api import deps as api_deps
from app.api.app import create_app
from app.api.routes.scientific import kinetics_selection as route_module
from app.services.read_snapshot import SnapshotNotConsistentError, begin_read_snapshot

QUESTION = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}


@pytest.fixture
def real_factory(db_engine):
    """The app's session factory on an engine of this test's own, with no pool.

    Not the suite's shared ``db_engine``: a connection that has been a read-only snapshot comes back from SQLAlchemy
    with the driver's explicit ``read_only=False``, which would override the ``SET SESSION CHARACTERISTICS ... READ
    ONLY`` that other tests (the object-store migrator's) rely on if it went back into the shared pool. Closing every
    connection at release keeps that out of the shared pool altogether.
    """
    engine = create_engine(db_engine.url, poolclass=NullPool)
    factory = api_deps.SessionLocal
    previous = factory.kw.get("bind")
    factory.configure(bind=engine)
    try:
        yield factory
    finally:
        factory.configure(bind=previous)
        engine.dispose()


def _setting(session: Session, name: str) -> str:
    return str(session.scalar(text(f"SELECT current_setting('{name}')")))


def test_the_dependency_yields_a_read_only_repeatable_read_session(real_factory):
    generator = api_deps.get_snapshot_db()
    session = next(generator)
    try:
        assert _setting(session, "transaction_isolation") == "repeatable read"
        assert _setting(session, "transaction_read_only") == "on"
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


def test_a_session_that_has_already_run_a_statement_is_refused_by_the_dependency(real_factory, monkeypatch):
    """The dependency insists on the snapshot: it does not quietly hand out a session that could not become one."""

    def used_session():
        session = Session(bind=real_factory.kw["bind"])
        session.scalar(text("SELECT 1"))
        return session

    monkeypatch.setattr(api_deps, "SessionLocal", used_session)
    with pytest.raises(SnapshotNotConsistentError):
        next(api_deps.get_snapshot_db())


def test_a_route_given_get_dbs_kind_of_session_fails_loudly_instead_of_answering_read_committed(client, db_session, world):
    """Swap the dependency for a session that is not a snapshot: the selection refuses, with no 200 and no manifest."""
    client.app.dependency_overrides[api_deps.get_snapshot_db] = lambda: db_session  # no opt-out: a plain session
    url = f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics/select"
    with pytest.raises(SnapshotNotConsistentError):
        client.post(url, json=QUESTION)
    with pytest.raises(SnapshotNotConsistentError):
        client.post(url + "/manifest", json=QUESTION)


class _Stop(Exception):
    """Raised by a spy to end the request once it has seen what it came for."""


def test_the_real_app_resolves_the_ref_and_runs_the_selection_on_one_repeatable_read_read_only_connection(db_engine, monkeypatch):
    """No override anywhere: the real dependency, a pool of one, and what the connection reports at each step.

    The pool holds one connection, so the connection the selection runs on is the only one there is; reading its
    backend pid at ref resolution and again at the selection proves they are the same connection, and the session
    taken from the pool afterwards proves it was handed back clean (read committed, read-write).
    """
    engine = create_engine(db_engine.url, pool_size=1, max_overflow=0, pool_timeout=5)
    factory = api_deps.SessionLocal
    previous = factory.kw.get("bind")
    factory.configure(bind=engine)
    seen: dict[str, tuple[str, str, int]] = {}

    def probe(session: Session) -> tuple[str, str, int]:
        return (
            _setting(session, "transaction_isolation"),
            _setting(session, "transaction_read_only"),
            int(session.scalar(text("SELECT pg_backend_pid()"))),
        )

    def spy_resolve(session, ref):
        seen["resolve"] = probe(session)
        return 1

    def spy_run(session, *, reaction_entry_id, body):
        seen["select"] = probe(session)
        raise _Stop

    monkeypatch.setattr(route_module, "resolve_reaction_entry_ref", spy_resolve)
    monkeypatch.setattr(route_module, "check_request_refs", lambda session, body: None)
    monkeypatch.setattr(route_module, "run_selection", spy_run)
    try:
        app = create_app()
        assert api_deps.get_snapshot_db not in app.dependency_overrides  # the real dependency
        with TestClient(app) as test_client:
            for suffix in ("", "/manifest"):
                seen.clear()
                with pytest.raises(_Stop):
                    test_client.post(f"/api/v1/scientific/reaction-entries/rxe_anything/kinetics/select{suffix}", json=QUESTION)
                assert seen["resolve"][:2] == ("repeatable read", "on"), suffix
                assert seen["select"][:2] == ("repeatable read", "on"), suffix
                assert seen["resolve"][2] == seen["select"][2], suffix  # one connection throughout
                # the one pooled connection comes back as an ordinary session, not still a snapshot
                after = factory()
                try:
                    assert probe(after)[:2] == ("read committed", "off"), suffix
                    assert probe(after)[2] == seen["select"][2], suffix  # and it is the same connection
                finally:
                    after.close()
    finally:
        factory.configure(bind=previous)
        engine.dispose()
