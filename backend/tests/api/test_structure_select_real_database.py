"""The structure select routes, unstubbed: the real app, its real dependencies, and committed rows.

Every other route test goes through the harness (one outer transaction, the snapshot dependency overridden), so a route
wired to the wrong session dependency passes all of them. This one stubs nothing: committed rows live in a scratch
database, the application's session factory is pointed at it through a one-connection pool, and each route is called as
a client would. What is asserted is observed from inside the request, on the connection serving it:

* at **ref resolution** and at **``run_selection``** the transaction is REPEATABLE READ and READ ONLY, on the same
  backend process (the snapshot was opened before anything else touched the session);
* once the request is over, the next pooled session on that same process is back to READ COMMITTED and writable (the
  snapshot did not leak onto the pool).

Committed rows cannot be cleaned up row by row (the science tables carry append-only triggers), so this runs in a
database that is dropped whole, as ``test_write_commit_before_response.py`` does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api import deps as api_deps
from app.api.app import create_app
from app.api.routes.scientific import structure_selection as routes
from tests import conftest
from tests.conftest import scratch_database_name
from tests.services.structure_selection._support import declaration, make_ts_world, make_world

BACKEND_ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1/scientific"
PROBE = text(
    "SELECT pg_backend_pid(), current_setting('transaction_isolation'), current_setting('transaction_read_only')"
)


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator:
    """A database made by ``alembic upgrade head`` alone, dropped afterwards, behind a ONE-connection pool.

    One connection, so a snapshot that leaked would be seen by the very next session, on the same process.
    """
    db_name = scratch_database_name("structure_select_real")
    conftest._recreate_test_database(db_name)
    engine = None
    try:
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT, env=conftest._db_env(db_name), check=True, capture_output=True, text=True,
        )
        engine = create_engine(conftest._database_url(db_name), pool_size=1, max_overflow=0)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        conftest._drop_test_database(db_name)


@pytest.fixture(scope="module")
def committed(scratch_engine):
    """One species entry with approved declared single points, and one transition state entry, committed."""
    with Session(scratch_engine) as session:
        world = make_world(session)
        world.sp("a", -76.40, declared=declaration())
        world.sp("b", -76.45, declared=declaration())
        world.settle()
        ts = make_ts_world(session)
        ts.freq("f", frequencies=(-1500.0, 100.0))
        ts.settle()
        refs = SimpleNamespace(entry=world.entry.public_ref, ts_entry=ts.entry.public_ref)
        session.commit()
    return refs


@pytest.fixture
def real_app_client(scratch_engine):
    """The real application, no dependency overridden, its session factory on the scratch database."""
    factory = api_deps.SessionLocal
    previous = factory.kw.get("bind")
    factory.configure(bind=scratch_engine)
    try:
        app = create_app()
        assert not app.dependency_overrides
        with TestClient(app) as client:
            yield client
    finally:
        factory.configure(bind=previous)


@pytest.fixture
def observed(monkeypatch):
    """Record (stage, pid, isolation, read_only) from inside the request, at each stage the route runs."""
    seen: list[tuple[str, int, str, str]] = []

    def watch(stage: str, original):
        def wrapper(session, *args, **kwargs):
            pid, isolation, read_only = session.execute(PROBE).one()
            seen.append((stage, pid, isolation, read_only))
            return original(session, *args, **kwargs)

        return wrapper

    for stage, name in (
        ("ref_resolution_species", "resolve_species_entry_ref"),
        ("ref_resolution_ts", "resolve_transition_state_entry_ref"),
        ("run_selection", "run_selection"),
    ):
        monkeypatch.setattr(routes, name, watch(stage, getattr(routes, name)))
    return seen


def _url(committed, which: str, suffix: str) -> str:
    if which == "evidence":
        return f"{API}/transition-state-entries/{committed.ts_entry}/evidence/select{suffix}"
    return f"{API}/species-entries/{committed.entry}/{which}/select{suffix}"


@pytest.mark.parametrize("suffix", ["", "/manifest"])
@pytest.mark.parametrize("which", ["calculations", "conformers", "evidence"])
def test_each_route_resolves_and_decides_inside_one_repeatable_read_read_only_snapshot(
    real_app_client, scratch_engine, committed, observed, which, suffix
):
    response = real_app_client.post(_url(committed, which, suffix), json={})
    assert response.status_code == 200, response.text

    stages = {stage for stage, *_ in observed}
    expected_resolution = "ref_resolution_ts" if which == "evidence" else "ref_resolution_species"
    assert stages == {expected_resolution, "run_selection"}, observed
    pids = {pid for _, pid, *_ in observed}
    assert len(pids) == 1, f"the stages ran on different connections: {observed}"
    for stage, _pid, isolation, read_only in observed:
        assert isolation == "repeatable read", (stage, isolation)
        assert read_only == "on", (stage, read_only)

    # The next pooled session is on the same process and the snapshot did not leak onto it.
    (pid,) = pids
    with Session(scratch_engine) as session:
        next_pid, isolation, read_only = session.execute(PROBE).one()
    assert next_pid == pid, "the one-connection pool did not hand back the request's connection"
    assert isolation == "read committed" and read_only == "off"


def test_the_manifest_says_it_was_read_in_a_snapshot_and_the_answer_names_the_committed_rows(
    real_app_client, committed
):
    manifest = real_app_client.post(_url(committed, "calculations", "/manifest"), json={}).json()
    assert manifest["snapshot_isolation"] == "repeatable read"
    answer = real_app_client.post(_url(committed, "calculations", ""), json={}).json()
    assert answer["request"]["entry_ref"] == committed.entry
    assert answer["outcome"] == manifest["outcome"]


def test_an_unknown_entry_is_a_404_through_the_real_dependency_too(real_app_client, committed):
    response = real_app_client.post(f"{API}/species-entries/spe_doesnotexistdoesnotexist/calculations/select", json={})
    assert response.status_code == 404
