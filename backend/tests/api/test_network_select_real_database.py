"""The network select routes, unstubbed: the real app, its real dependencies, and committed rows.

Every other route test goes through the harness (one outer transaction, the snapshot dependency overridden). This one
stubs nothing: a network with one approved, declared solve is committed to a scratch database, the application's
session factory is pointed at it with no pool, and both routes are called as a client would. Each must answer 200, and
the manifest must say it was read in a REPEATABLE READ snapshot and replay.

Committed rows cannot be cleaned up row by row (the science tables carry append-only triggers), so this runs in a
database that is dropped whole, as ``test_kinetics_select_real_database.py`` does.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api import deps as api_deps
from app.api.app import create_app
from app.db.models.common import NetworkSolveKind, SubmissionRecordType
from app.db.models.common import RecordReviewStatus as S
from app.services.network_selection import replay_network
from app.services.record_review import ensure_record_review, set_record_review_status
from tests import conftest
from tests.conftest import scratch_database_name
from tests.services.network_selection._rules import protocol
from tests.services.network_selection._world import add_solve, build_world, fit_spec

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator:
    """A database made by ``alembic upgrade head`` alone, dropped afterwards, with no connection pool."""
    db_name = scratch_database_name("network_select_real")
    conftest._recreate_test_database(db_name)
    engine = None
    try:
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT, env=conftest._db_env(db_name), check=True, capture_output=True, text=True,
        )
        engine = create_engine(conftest._database_url(db_name), poolclass=NullPool)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        conftest._drop_test_database(db_name)


@pytest.fixture(scope="module")
def committed(scratch_engine):
    """One network with one approved, declared solve, committed."""
    with Session(scratch_engine) as session:
        world = build_world(session)
        ensure_record_review(session, record_type=SubmissionRecordType.network, record_id=world.network.id)
        set_record_review_status(
            session, record_type=SubmissionRecordType.network, record_id=world.network.id, status=S.approved, actor=world.actor
        )
        # A reported solve: a computed one must commit its state energies, which this test has no reason to build.
        solve = add_solve(
            session,
            world,
            fits=[fit_spec("assoc")],
            protocol=protocol("chemically_significant_eigenvalues"),
            review=S.approved,
            kind=NetworkSolveKind.reported,
        )
        refs = SimpleNamespace(
            network=world.ref,
            solve=solve.public_ref,
            determination=solve._dets["d_assoc"].public_ref,
            fit=solve._fits[0].public_ref,
            bath=world.ar.public_ref,
            hashes=sorted(world.hashes.values()),
        )
        session.commit()
    return refs


def question(refs):
    return {
        "scope": "single_channel",
        "channel_key": "assoc",
        "observable": "product_resolved_coefficient",
        "coefficient_basis": "kernel",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure_min_bar": 0.5,
        "pressure_max_bar": 5.0,
        "bath": {"components": [{"species_ref": refs.bath}]},
        "partition": {"retained": refs.hashes},
    }


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


@pytest.mark.parametrize("suffix", ["", "/manifest"])
def test_both_routes_answer_200_over_committed_rows_and_the_manifest_says_it_read_a_snapshot(
    real_app_client, committed, suffix
):
    url = f"/api/v1/scientific/networks/{committed.network}/kinetics/select{suffix}"
    response = real_app_client.post(url, json=question(committed))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "sole_eligible_candidate"
    if suffix:
        assert body["snapshot_isolation"] == "repeatable read"
        assert replay_network(body) == body["decision"]
    else:
        assert body["selection"]["node_ref"] == committed.determination
        assert body["selection"]["members"][0]["kinetics_refs"] == [committed.fit]
        assert body["request"]["network_ref"] == committed.network


def test_the_two_routes_agree_over_the_committed_rows(real_app_client, committed):
    base = f"/api/v1/scientific/networks/{committed.network}/kinetics/select"
    answer = real_app_client.post(base, json=question(committed)).json()
    manifest = real_app_client.post(base + "/manifest", json=question(committed)).json()
    assert answer["outcome"] == manifest["outcome"]
    assert answer["administrative_order"] == manifest["decision"]["administrative_order"]
    assert answer["disclosures"]["population"] == manifest["population"]["counts"]


def test_an_unknown_network_is_a_404_and_an_integer_a_422_through_the_real_dependency_too(real_app_client, committed):
    unknown = real_app_client.post(
        "/api/v1/scientific/networks/net_doesnotexistdoesnotexist/kinetics/select", json=question(committed)
    )
    assert unknown.status_code == 404
    integer = real_app_client.post("/api/v1/scientific/networks/1/kinetics/select", json=question(committed))
    assert integer.status_code == 422
