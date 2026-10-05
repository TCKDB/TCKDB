"""The network selection routes read in ``get_snapshot_db``'s session, and nothing else's.

The routes ask for a read-only REPEATABLE READ session opened before their body runs. These tests hold that for the
network routes: the real application, no override, a pool of one, and what the connection reports at ref resolution
and at the selection; and a route handed a session that is not a snapshot (``get_db``'s kind) fails loudly instead
of answering under READ COMMITTED. The dependency itself is tested in ``test_snapshot_db_dependency.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api import deps as api_deps
from app.api.app import create_app
from app.api.routes.scientific import network_export as export_route_module
from app.api.routes.scientific import network_selection as route_module
from app.services.read_snapshot import SnapshotNotConsistentError
from tests.services.network_selection._world import build_world

QUESTION = {
    "scope": "single_channel",
    "channel_key": "assoc",
    "observable": "product_resolved_coefficient",
    "coefficient_basis": "kernel",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure_min_bar": 0.5,
    "pressure_max_bar": 5.0,
    "bath": {"components": [{"species_ref": "spe_anything"}]},
    "partition": {"retained": ["a" * 64]},
}


def _setting(session: Session, name: str) -> str:
    return str(session.scalar(text(f"SELECT current_setting('{name}')")))


class _Stop(Exception):
    """Raised by a spy to end the request once it has seen what it came for."""


def test_a_route_given_get_dbs_kind_of_session_fails_loudly_instead_of_answering_read_committed(client, db_session):
    """Swap the dependency for a session that is not a snapshot: the selection refuses, with no 200 and no manifest."""
    world = build_world(db_session)
    client.app.dependency_overrides[api_deps.get_snapshot_db] = lambda: db_session  # no opt-out: a plain session
    request = {**QUESTION, "bath": {"components": [{"species_ref": world.ar.public_ref}]}}
    url = f"/api/v1/scientific/networks/{world.ref}/kinetics/select"
    with pytest.raises(SnapshotNotConsistentError):
        client.post(url, json=request)
    with pytest.raises(SnapshotNotConsistentError):
        client.post(url + "/manifest", json=request)


def test_the_real_app_resolves_the_ref_and_runs_the_selection_on_one_repeatable_read_read_only_connection(
    db_engine, monkeypatch
):
    """No override anywhere: the real dependency, a pool of one, and what the connection reports at each step."""
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

    def spy_run(session, *, network_ref, body):
        seen["select"] = probe(session)
        raise _Stop

    monkeypatch.setattr(route_module, "resolve_network_ref", spy_resolve)
    monkeypatch.setattr(route_module, "check_request_refs", lambda session, body: None)
    monkeypatch.setattr(route_module, "run_selection", spy_run)
    try:
        app = create_app()
        assert api_deps.get_snapshot_db not in app.dependency_overrides  # the real dependency
        with TestClient(app) as test_client:
            for suffix in ("", "/manifest"):
                seen.clear()
                with pytest.raises(_Stop):
                    test_client.post(f"/api/v1/scientific/networks/net_anything/kinetics/select{suffix}", json=QUESTION)
                assert seen["resolve"][:2] == ("repeatable read", "on"), suffix
                assert seen["select"][:2] == ("repeatable read", "on"), suffix
                assert seen["resolve"][2] == seen["select"][2], suffix  # one connection throughout
                after = factory()
                try:
                    assert probe(after)[:2] == ("read committed", "off"), suffix  # handed back clean
                    assert probe(after)[2] == seen["select"][2], suffix
                finally:
                    after.close()
    finally:
        factory.configure(bind=previous)
        engine.dispose()


def test_the_export_route_reads_in_the_snapshot_and_hands_the_connection_back_clean(db_engine, monkeypatch):
    """The export route runs ref resolution and the verification on one REPEATABLE READ, read-only connection."""
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

    def spy_export(session, **kwargs):
        seen["export"] = probe(session)
        raise _Stop

    monkeypatch.setattr(export_route_module, "resolve_network_ref", spy_resolve)
    monkeypatch.setattr(export_route_module, "export_selected", spy_export)
    body = {"manifest": {}, "node_ref": "nkdet_x", "representation_refs": ["nkin_x"]}
    try:
        app = create_app()
        assert api_deps.get_snapshot_db not in app.dependency_overrides
        with TestClient(app) as test_client:
            with pytest.raises(_Stop):
                test_client.post("/api/v1/scientific/networks/net_anything/kinetics/export-selected", json=body)
            assert seen["resolve"][:2] == ("repeatable read", "on")
            assert seen["export"][:2] == ("repeatable read", "on")
            assert seen["resolve"][2] == seen["export"][2]
            after = factory()
            try:
                assert probe(after)[:2] == ("read committed", "off")
            finally:
                after.close()
    finally:
        factory.configure(bind=previous)
        engine.dispose()


def test_the_export_route_given_get_dbs_kind_of_session_fails_loudly(client, db_session):
    """A valid manifest, but a session that is not a snapshot: the verification refuses instead of reading it."""
    import json

    from app.services.network_selection import select_network
    from tests.services.network_selection._requests import channel_request
    from tests.services.network_selection._rules import protocol
    from tests.services.network_selection._world import add_solve, fit_spec

    world = build_world(db_session)
    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol("chemically_significant_eigenvalues"))
    manifest = json.loads(
        json.dumps(select_network(db_session, request=channel_request(world), require_snapshot=False).manifest)
    )
    client.app.dependency_overrides[api_deps.get_snapshot_db] = lambda: db_session  # no opt-out: a plain session
    body = {
        "manifest": manifest,
        "node_ref": solve._dets["d_assoc"].public_ref,
        "representation_refs": [solve._fits[0].public_ref],
    }
    with pytest.raises(SnapshotNotConsistentError):
        client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/export-selected", json=body)
