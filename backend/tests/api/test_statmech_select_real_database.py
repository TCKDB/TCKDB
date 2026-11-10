"""The statmech select routes, unstubbed: the real app, its real dependencies, and committed rows.

Every other route test goes through the harness (one outer transaction, the snapshot dependency overridden), so a route
wired to the wrong session dependency passes all of them. This one stubs nothing: committed rows live in a scratch
database, the application's session factory is pointed at it through a one-connection pool, and each route is called as
a client would. What is asserted is observed from inside the request, on the connection serving it:

* at **ref checking**, at **target resolution** and at **``run_selection``** the transaction is REPEATABLE READ and READ
  ONLY, on the same backend process (the snapshot was opened before anything else touched the session);
* once the request is over, the next pooled session on that same process is back to READ COMMITTED and writable (the
  snapshot did not leak onto the pool).

Committed rows cannot be cleaned up row by row (the science tables carry append-only triggers), so this runs in a
database that is dropped whole, as ``test_structure_select_real_database.py`` does.
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
from app.api.routes.scientific import statmech_selection as routes
from app.services.statmech_selection import manifest as manifest_module
from app.services.statmech_selection import public
from tests import conftest
from tests.conftest import scratch_database_name
from tests.services.statmech_selection.test_request import THERMAL, make
from tests.services.statmech_selection.test_service import build

BACKEND_ROOT = Path(__file__).resolve().parents[2]
URL = "/api/v1/scientific/statmech/select"
PROBE = text(
    "SELECT pg_backend_pid(), current_setting('transaction_isolation'), current_setting('transaction_read_only')"
)


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator:
    """A database made by ``alembic upgrade head`` alone, dropped afterwards, behind a ONE-connection pool.

    One connection, so a snapshot that leaked would be seen by the very next session, on the same process.
    """
    db_name = scratch_database_name("statmech_select_real")
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
    """One species entry with an approved declared basin model, committed."""
    with Session(scratch_engine) as session:
        scene = build(session)
        body = make(
            THERMAL,
            target__species_entry_ref=scene.world.entry.public_ref,
            target__scope__conformer_group_ref=scene.group.public_ref,
        )
        refs = SimpleNamespace(entry=scene.world.entry.public_ref, body=body)
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

    for stage, module, name in (
        ("ref_check", public, "check_request_refs"),
        ("target_resolution", public, "resolve_target"),
        ("run_selection", routes, "run_selection"),
    ):
        monkeypatch.setattr(module, name, watch(stage, getattr(module, name)))
    return seen


@pytest.mark.parametrize("suffix", ["", "/manifest"])
@pytest.mark.parametrize("intent", [{"intent": "qualify"}, {"intent": "protocol_coverage"}])
def test_each_route_checks_resolves_and_decides_inside_one_repeatable_read_read_only_snapshot(
    real_app_client, scratch_engine, committed, observed, suffix, intent
):
    response = real_app_client.post(URL + suffix, json={**committed.body, **intent})
    assert response.status_code == 200, response.text

    assert {stage for stage, *_ in observed} == {"ref_check", "target_resolution", "run_selection"}, observed
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


def test_the_manifest_says_it_was_read_in_a_snapshot_and_replays_from_committed_rows(real_app_client, committed):
    manifest = real_app_client.post(URL + "/manifest", json=committed.body).json()
    assert manifest["snapshot_isolation"] == "repeatable read"
    assert manifest_module.replay_matches(manifest)
    answer = real_app_client.post(URL, json=committed.body).json()
    assert answer["snapshot_isolation"] == "repeatable read"
    assert answer["request"]["target"]["owner_ref"] == committed.entry
    assert answer["outcome"] == manifest["outcome"] and answer["integrity"] == manifest["integrity"]


def test_an_unknown_target_is_a_404_through_the_real_dependency_too(real_app_client, committed):
    body = {**committed.body, "target": {**committed.body["target"], "species_entry_ref": "spe_" + "a" * 26}}
    response = real_app_client.post(URL, json=body)
    assert response.status_code == 404
