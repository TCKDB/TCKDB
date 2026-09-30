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
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text, update
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


# ---------------------------------------------------------------------------
# #597 review: the stamp must never wait for a connection, and must not fire
# when it cannot matter.
# ---------------------------------------------------------------------------


def _small_factory(db_engine, *, size: int, timeout: float) -> tuple[sessionmaker, object]:
    engine = create_engine(
        db_engine.url, pool_size=size, max_overflow=0, pool_timeout=timeout
    )
    return sessionmaker(bind=engine, expire_on_commit=False), engine


def _app_over(main: sessionmaker) -> FastAPI:
    app = FastAPI()

    def _get_db() -> Iterator[Session]:
        session = main()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db

    @app.get("/probe")
    def _probe(user: AppUser = Depends(get_current_user)):
        return {}

    return app


def test_a_saturated_request_pool_does_not_stall_or_lose_the_stamp(
    db_engine, factory, monkeypatch
) -> None:
    """The request's pool holds one connection and the request has it, so the
    pool is full. The stamp used to draw a second connection from that same
    pool, stalling the whole ``pool_timeout`` and then losing the stamp."""
    main, main_engine = _small_factory(db_engine, size=1, timeout=6)
    monkeypatch.setattr(api_deps, "SessionLocal", main)  # as in production
    try:
        app = _app_over(main)
        with TestClient(app) as client:
            start = time.monotonic()
            resp = client.get("/probe", headers=API_KEY)
            elapsed = time.monotonic() - start
    finally:
        main_engine.dispose()
    assert resp.status_code == 200
    assert elapsed < 3, f"the request waited {elapsed:.1f}s for a connection"
    assert _last_used(factory) is not None


def test_a_full_stamp_pool_skips_the_stamp_without_waiting(
    factory, caplog
) -> None:
    """Hold every connection the production stamp engine is allowed. A
    third would need a longer wait (``STAMP_POOL_TIMEOUT_S``) or overflow;
    the request must do neither."""
    held = [api_deps.stamp_engine.connect() for _ in range(api_deps.STAMP_POOL_SIZE)]
    try:
        app = _app_over(factory)
        with TestClient(app) as client, caplog.at_level(
            logging.WARNING, logger="app.api.deps"
        ):
            start = time.monotonic()
            resp = client.get("/probe", headers=API_KEY)
            elapsed = time.monotonic() - start
    finally:
        for connection in held:
            connection.close()
    assert resp.status_code == 200
    assert elapsed < 3, f"the request waited {elapsed:.1f}s on the stamp"
    assert "could not record api_key.last_used_at" in caplog.text
    assert _last_used(factory) is None


def test_the_stamp_engine_is_the_derived_one_with_the_named_shape(db_engine) -> None:
    stamp = api_deps.stamp_engine
    assert stamp is not api_deps.engine
    # Connects exactly as the ambient engine does (this is what keeps the
    # harness's refusing engine refusing, and what points the stamp at the
    # test database here).
    assert stamp.pool._creator is db_engine.pool._creator  # type: ignore[attr-defined]
    assert stamp.pool.size() == api_deps.STAMP_POOL_SIZE  # type: ignore[attr-defined]
    assert stamp.pool._max_overflow == 0  # type: ignore[attr-defined]
    assert stamp.pool._timeout == api_deps.STAMP_POOL_TIMEOUT_S  # type: ignore[attr-defined]
    assert api_deps.StampSessionLocal.kw["bind"] is stamp


def test_the_stamp_connection_carries_the_statement_timeout(db_engine) -> None:
    expected = str(settings.db_statement_timeout_ms or 0)
    assert expected != "0", "no statement timeout configured; this test would prove nothing"
    # Several checkouts of the same pooled connection: each ends in the
    # pool's rollback-on-return, which undoes a SET that was never committed.
    seen = []
    for _ in range(3):
        with api_deps.stamp_engine.connect() as connection:
            seen.append(
                connection.execute(
                    text("SELECT setting FROM pg_settings WHERE name = 'statement_timeout'")
                ).scalar_one()
            )
    assert seen == [expected] * 3


def test_a_route_restamps_a_key_last_used_long_ago(prod_client, factory) -> None:
    old = _naive_now() - timedelta(hours=2)
    factory.reset(old)
    resp = prod_client.post(CONFORMER, json=PAYLOAD, headers=API_KEY)
    assert resp.status_code == 201, resp.text
    assert _last_used(factory) > old + timedelta(hours=1)


def test_a_request_inside_the_window_opens_no_stamp_connection(factory) -> None:
    checkouts: list[int] = []

    def _count(*_args) -> None:
        checkouts.append(1)

    event.listen(api_deps.stamp_engine, "checkout", _count)
    try:
        app = _app_over(factory)
        with TestClient(app) as client:
            factory.reset(_naive_now() - timedelta(seconds=5))
            assert client.get("/probe", headers=API_KEY).status_code == 200
            assert checkouts == [], "a request inside the window opened a connection"
            factory.reset(_naive_now() - timedelta(hours=1))
            assert client.get("/probe", headers=API_KEY).status_code == 200
            assert checkouts == [1], "the counter cannot see a stamp that is due"
    finally:
        event.remove(api_deps.stamp_engine, "checkout", _count)


@pytest.fixture
def _unusable_keys(factory, _api_test_user) -> Iterator[dict[str, str]]:
    """A revoked key, and a live key whose user is inactive. Committed, so
    removed by hand afterwards; ``api_key`` and ``app_user`` are not watched
    by the commit tripwire."""
    from app.db.models.common import AppUserRole

    suffix = f"{time.monotonic_ns()}"
    with factory() as session:
        owner = session.get(AppUser, _api_test_user)
        revoked_raw, inactive_raw = f"revoked-{suffix}", f"inactive-{suffix}"
        ghost = AppUser(
            username=f"inactive-{suffix}", role=AppUserRole.user, is_active=False
        )
        session.add(ghost)
        session.flush()
        import hashlib

        rows = [
            ApiKey(
                user_id=owner.id,
                key_hash=hashlib.sha256(revoked_raw.encode()).hexdigest(),
                revoked_at=_naive_now(),
            ),
            ApiKey(
                user_id=ghost.id,
                key_hash=hashlib.sha256(inactive_raw.encode()).hexdigest(),
            ),
        ]
        session.add_all(rows)
        session.commit()
        ids = [r.id for r in rows] + [ghost.id]
    yield {"revoked": revoked_raw, "inactive": inactive_raw}
    with factory() as session:
        session.execute(text("DELETE FROM api_key WHERE id = ANY(:i)"), {"i": ids[:2]})
        session.execute(text("DELETE FROM app_user WHERE id = :i"), {"i": ids[2]})
        session.commit()


@pytest.mark.parametrize("which", ["revoked", "inactive"])
def test_a_rejected_key_is_never_stamped(factory, _unusable_keys, which) -> None:
    import hashlib

    raw = _unusable_keys[which]
    app = _app_over(factory)
    with TestClient(app) as client:
        assert client.get("/probe", headers={"X-API-Key": raw}).status_code == 401
    with factory() as session:
        stamped = session.scalar(
            select(ApiKey.last_used_at).where(
                ApiKey.key_hash == hashlib.sha256(raw.encode()).hexdigest()
            )
        )
    assert stamped is None
