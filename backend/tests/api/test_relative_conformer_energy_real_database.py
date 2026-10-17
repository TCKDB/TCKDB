"""The relative conformer energy routes, unstubbed: the real app, its real dependencies, and committed rows.

Every other route test goes through the harness (one outer transaction, the snapshot dependency overridden), so a route
wired to the wrong session dependency passes all of them. This one stubs nothing: committed rows live in a scratch
database, the application's session factory is pointed at it through a one-connection pool, and each route is called as
a client would. What is asserted is observed from inside the request, on the connection serving it:

* at **ref resolution** and at **the service call** the transaction is REPEATABLE READ and READ ONLY, on the same backend
  process;
* once the request is over, the next pooled session on that same process is back to READ COMMITTED and writable.

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
from app.db.models.common import StructureDeterminationTargetKind
from app.services.relative_conformer_energy import public as public_module
from tests import conftest
from tests.conftest import scratch_database_name
from tests.services.scientific_read._factories import make_conformer_group, make_conformer_observation
from tests.services.structure_selection._support import make_world
from tests.services.structure_selection.test_service import build_basin

BACKEND_ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1/scientific"
PROBE = text(
    "SELECT pg_backend_pid(), current_setting('transaction_isolation'), current_setting('transaction_read_only')"
)


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator:
    """A database made by ``alembic upgrade head`` alone, dropped afterwards, behind a ONE-connection pool."""
    db_name = scratch_database_name("relative_energy_real")
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
    """One species entry with two conformer groups that each have a basin determination, committed."""
    with Session(scratch_engine) as session:
        world = make_world(session)
        groups = []
        for energy in (-76.400, -76.401):
            group = make_conformer_group(session, world.entry)
            obs = make_conformer_observation(session, conformer_group=group)
            build_basin(world, energy=energy, observation=obs, target=StructureDeterminationTargetKind.conformer_basin)
            groups.append(group.public_ref)
        world.settle()
        refs = SimpleNamespace(entry=world.entry.public_ref, groups=groups, lot=world.lot.public_ref)
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

    def watch(stage: str, name: str):
        original = getattr(public_module, name)

        def wrapper(session, *args, **kwargs):
            pid, isolation, read_only = session.execute(PROBE).one()
            seen.append((stage, pid, isolation, read_only))
            return original(session, *args, **kwargs)

        monkeypatch.setattr(public_module, name, wrapper)

    watch("ref_resolution", "resolve_species_entry_ref")
    watch("service", "run_relative_energies")
    return seen


def _payload(committed) -> dict:
    return {
        "reference_conformer_ref": committed.groups[0],
        "energy_kind": "electronic_energy",
        "level": {"level_of_theory_ref": committed.lot},
        "certificate": "local_minimum_any_surface",
    }


@pytest.mark.parametrize("suffix", ["", "/manifest"])
def test_each_route_resolves_and_answers_inside_one_repeatable_read_read_only_snapshot(
    real_app_client, scratch_engine, committed, observed, suffix
):
    response = real_app_client.post(
        f"{API}/species-entries/{committed.entry}/conformers/relative-energies{suffix}", json=_payload(committed)
    )
    assert response.status_code == 200, response.text
    stages = {stage for stage, *_ in observed}
    assert stages == {"ref_resolution", "service"}, observed
    pids = {pid for _, pid, *_ in observed}
    assert len(pids) == 1, f"the stages ran on different connections: {observed}"
    for stage, _pid, isolation, read_only in observed:
        assert isolation == "repeatable read", (stage, isolation)
        assert read_only == "on", (stage, read_only)
    (pid,) = pids
    with Session(scratch_engine) as session:
        next_pid, isolation, read_only = session.execute(PROBE).one()
    assert next_pid == pid, "the one-connection pool did not hand back the request's connection"
    assert isolation == "read committed" and read_only == "off"


def test_the_answer_and_the_manifest_say_they_were_read_in_a_snapshot_and_name_the_committed_rows(real_app_client, committed):
    base = f"{API}/species-entries/{committed.entry}/conformers/relative-energies"
    manifest = real_app_client.post(base + "/manifest", json=_payload(committed)).json()
    answer = real_app_client.post(base, json=_payload(committed)).json()
    assert manifest["snapshot_isolation"] == answer["snapshot_isolation"] == "repeatable read"
    assert answer["outcome"] == manifest["outcome"] == "relative_energies"
    assert {m["conformer_ref"] for m in answer["members"]} == set(committed.groups)


def test_an_unknown_entry_is_a_404_through_the_real_dependency_too(real_app_client, committed):
    response = real_app_client.post(
        f"{API}/species-entries/spe_{'0' * 26}/conformers/relative-energies", json=_payload(committed)
    )
    assert response.status_code == 404
