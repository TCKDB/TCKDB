"""``api_key.last_used_at`` is recorded on every route, without locking.

#587: ``authenticate_api_key`` used to set ``last_used_at`` on the session
``get_current_user`` authenticates in. On a write route that session never
commits, so the stamp was lost; on a route that flushes it, the UPDATE's row
lock was held for the whole request, queueing requests on one key.

These tests run in the production topology -- authentication on a session of
its own that is never committed, the stamp on another -- against the
session-wide test key (committed once by ``_api_test_user``). The stamp is
written for real, so each test resets the column before and after.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps as api_deps
from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_current_user, get_db, get_write_db
from app.db.models.api_key import ApiKey
from app.db.models.app_user import AppUser
from tests.conftest import _TEST_API_KEY_HASH

API_KEY = {"X-API-Key": "test-api-key-for-tckdb"}
CONFORMER = "/api/v1/uploads/conformers"
PAYLOAD = {
    "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
    "calculation": {
        "type": "sp",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    },
    "label": "last-used-at",
}


def _naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def factory(db_engine, _api_test_user, monkeypatch) -> Iterator[sessionmaker]:
    """Real connections; the ambient factory (the stamp's) is bound to them."""
    real = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(api_deps, "SessionLocal", real)

    def _reset(value: datetime | None = None) -> None:
        with real() as session:
            session.execute(
                update(ApiKey)
                .where(ApiKey.key_hash == _TEST_API_KEY_HASH)
                .values(last_used_at=value)
            )
            session.commit()

    _reset()
    real.reset = _reset  # type: ignore[attr-defined]
    yield real
    _reset()


def _last_used(factory: sessionmaker) -> datetime | None:
    """Read from a brand-new session: only what was committed."""
    with factory() as session:
        return session.scalar(
            select(ApiKey.last_used_at).where(ApiKey.key_hash == _TEST_API_KEY_HASH)
        )


def _key_id(factory: sessionmaker) -> int:
    with factory() as session:
        return session.scalar(
            select(ApiKey.id).where(ApiKey.key_hash == _TEST_API_KEY_HASH)
        )


@pytest.fixture
def prod_client(factory) -> Iterator[TestClient]:
    """Real auth dependency; ``get_db`` as production wires it (fresh session
    per request, never committed); a write session that always rolls back."""

    def _get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def _rolled_back_write_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.rollback()
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_write_db] = _rolled_back_write_db
    with TestClient(app) as client:
        yield client


def test_a_write_route_records_last_used_at(prod_client, factory) -> None:
    assert _last_used(factory) is None
    resp = prod_client.post(CONFORMER, json=PAYLOAD, headers=API_KEY)
    assert resp.status_code == 201, resp.text
    assert _last_used(factory) is not None


def test_a_read_route_records_last_used_at(prod_client, factory, monkeypatch) -> None:
    monkeypatch.setattr(settings, "legacy_reads_require_auth", True)
    assert _last_used(factory) is None
    assert prod_client.get("/api/v1/thermo", headers=API_KEY).status_code == 200
    assert _last_used(factory) is not None


def test_an_optional_auth_route_records_last_used_at(factory) -> None:
    """``get_optional_current_user`` shares the same authentication path."""
    app = FastAPI()

    def _get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db

    @app.get("/probe")
    def _probe(user: AppUser | None = Depends(api_deps.get_optional_current_user)):
        return {"user": user is not None}

    with TestClient(app) as client:
        assert client.get("/probe", headers=API_KEY).json() == {"user": True}
    assert _last_used(factory) is not None


def test_an_invalid_key_records_nothing(prod_client, factory) -> None:
    resp = prod_client.post(CONFORMER, json=PAYLOAD, headers={"X-API-Key": "nope"})
    assert resp.status_code == 401
    assert _last_used(factory) is None


def test_a_recent_stamp_is_not_rewritten_but_a_stale_one_is(factory) -> None:
    from app.services.auth import API_KEY_LAST_USED_THROTTLE, record_api_key_use

    key_id = _key_id(factory)
    first = _naive_now() - timedelta(hours=2)
    factory.reset(first)

    within = first + API_KEY_LAST_USED_THROTTLE - timedelta(seconds=1)
    assert record_api_key_use(factory, key_id, now=within) is False
    assert _last_used(factory) == first

    after = first + API_KEY_LAST_USED_THROTTLE + timedelta(seconds=1)
    assert record_api_key_use(factory, key_id, now=after) is True
    assert _last_used(factory) == after


def test_a_locked_key_row_is_skipped_not_waited_for(db_engine, factory) -> None:
    """Something else holds the key row (a revocation, say). The stamp must
    return at once instead of queueing behind it."""
    from app.services.auth import record_api_key_use

    key_id = _key_id(factory)
    holder = db_engine.connect()
    try:
        holder.execute(
            text("SELECT id FROM api_key WHERE id = :i FOR UPDATE"), {"i": key_id}
        )
        outcome: list[bool] = []
        worker = threading.Thread(
            target=lambda: outcome.append(record_api_key_use(factory, key_id)),
            daemon=True,
        )
        worker.start()
        worker.join(5)
        blocked = worker.is_alive()
    finally:
        holder.rollback()  # releases the lock, so a blocked worker can finish
        holder.close()
    worker.join(10)
    assert not blocked, "the stamp waited for the row lock instead of skipping it"
    assert outcome == [False]
    assert _last_used(factory) is None


def test_two_requests_on_one_key_do_not_block_each_other(factory) -> None:
    """Request 1 authenticates, queries, and stays in flight. Request 2 on the
    same key must complete meanwhile. Before the fix, request 1's flushed
    ``last_used_at`` UPDATE held the row for its whole life, and request 2's
    own authentication queued behind it."""
    in_flight = threading.Event()
    release = threading.Event()
    app = FastAPI()

    def _get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db

    @app.get("/slow")
    def _slow(
        user: AppUser = Depends(get_current_user), session: Session = Depends(get_db)
    ):
        session.execute(select(AppUser.id).limit(1))  # ORM query: autoflushes
        in_flight.set()
        release.wait(30)
        return {}

    @app.get("/fast")
    def _fast(
        user: AppUser = Depends(get_current_user), session: Session = Depends(get_db)
    ):
        session.execute(select(AppUser.id).limit(1))  # ORM query: autoflushes
        return {}

    with TestClient(app) as client:
        first = threading.Thread(
            target=lambda: client.get("/slow", headers=API_KEY), daemon=True
        )
        first.start()
        try:
            assert in_flight.wait(10)
            result: list[int] = []
            second = threading.Thread(
                target=lambda: result.append(client.get("/fast", headers=API_KEY).status_code),
                daemon=True,
            )
            second.start()
            second.join(5)
            stuck = second.is_alive()
        finally:
            release.set()
        first.join(10)
        second.join(10)
    assert not stuck, "request 2 waited for request 1's row lock"
    assert result == [200]


def test_a_failed_stamp_never_fails_the_request(
    prod_client, factory, monkeypatch, caplog
) -> None:
    def _boom(*_args, **_kwargs):
        raise OperationalError("UPDATE api_key", {}, Exception("database says no"))

    monkeypatch.setattr(api_deps, "record_api_key_use", _boom)
    with caplog.at_level(logging.WARNING, logger="app.api.deps"):
        resp = prod_client.post(CONFORMER, json=PAYLOAD, headers=API_KEY)
    assert resp.status_code == 201, resp.text
    assert "could not record api_key.last_used_at" in caplog.text
    assert _last_used(factory) is None
