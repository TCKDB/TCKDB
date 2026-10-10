"""The H298 selection routes read in ``get_snapshot_db``'s session, and nothing else's.

``/thermo/select`` and ``/thermo/select/manifest`` run several statements (the entry's review states, its rows, their
children, the source findings) and must decide over one state. These tests hold, for the H298 routes, what
``test_network_select_snapshot_dependency.py`` holds for the network ones: the real application, no override, a pool of
one, and what the connection reports at ref resolution and at the selection; and a route handed a session that is not a
snapshot (``get_db``'s kind) fails loudly instead of answering under READ COMMITTED. The dependency itself is tested in
``test_snapshot_db_dependency.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api import deps as api_deps
from app.api.app import create_app
from app.api.routes.scientific import thermo_selection as route_module
from app.db.models.common import ThermoTargetKind
from app.services.read_snapshot import SnapshotNotConsistentError
from app.services.thermo_selection import H298Request, select_h298
from tests.services.thermo_selection._support import species_entry_for

QUESTION = {"target": {"kind": "equilibrium_ensemble"}}


def _setting(session: Session, name: str) -> str:
    return str(session.scalar(text(f"SELECT current_setting('{name}')")))


class _Stop(Exception):
    """Raised by a spy to end the request once it has seen what it came for."""


def test_a_route_given_get_dbs_kind_of_session_fails_loudly_instead_of_answering_read_committed(client, db_session):
    """Swap the dependency for a session that is not a snapshot: the selection refuses, with no 200 and no manifest."""
    entry = species_entry_for(db_session, "Methane")
    client.app.dependency_overrides[api_deps.get_snapshot_db] = lambda: db_session  # no opt-out: a plain session
    url = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo/select"
    with pytest.raises(SnapshotNotConsistentError):
        client.post(url, json=QUESTION)
    with pytest.raises(SnapshotNotConsistentError):
        client.post(url + "/manifest", json=QUESTION)


def test_the_service_refuses_a_session_that_is_not_a_snapshot_unless_the_caller_opts_out(db_session):
    """The default is to insist; only a caller that cannot hold a snapshot (the harness) says so, explicitly."""
    entry = species_entry_for(db_session, "Methane")
    request = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)
    with pytest.raises(SnapshotNotConsistentError):
        select_h298(db_session, species_entry_id=entry.id, request=request)
    assert select_h298(db_session, species_entry_id=entry.id, request=request, require_snapshot=False).manifest


def test_the_public_run_selection_opts_out_only_when_the_session_carries_the_marker(db_session):
    from app.schemas.reads.scientific_thermo_selection import ThermoSelectionRequest
    from app.services.thermo_selection.public import SNAPSHOT_OPT_OUT, run_selection

    entry = species_entry_for(db_session, "Methane")
    body = ThermoSelectionRequest.model_validate(QUESTION)
    db_session.info.pop(SNAPSHOT_OPT_OUT, None)
    with pytest.raises(SnapshotNotConsistentError):
        run_selection(db_session, species_entry_id=entry.id, body=body)
    db_session.info[SNAPSHOT_OPT_OUT] = True
    selection, _ = run_selection(db_session, species_entry_id=entry.id, body=body)
    assert selection.manifest


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

    def spy_run(session, *, species_entry_id, body):
        seen["select"] = probe(session)
        raise _Stop

    monkeypatch.setattr(route_module, "resolve_species_entry_ref", spy_resolve)
    monkeypatch.setattr(route_module, "run_selection", spy_run)
    try:
        app = create_app()
        assert api_deps.get_snapshot_db not in app.dependency_overrides  # the real dependency
        with TestClient(app) as test_client:
            for suffix in ("", "/manifest"):
                seen.clear()
                with pytest.raises(_Stop):
                    test_client.post(f"/api/v1/scientific/species-entries/spe_anything/thermo/select{suffix}", json=QUESTION)
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
