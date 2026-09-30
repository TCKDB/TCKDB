"""A write route must be committed before the client is told it succeeded (#616).

``get_write_db`` used to commit in the teardown of a request-scoped yield
dependency. FastAPI runs that teardown *after* it has sent the response, so a
client could receive its 201 and read straight back before the rows existed,
and a commit that failed did so after the 201 had gone out.

``TestClient`` cannot show this. The shared ``client`` fixture overrides
``get_write_db`` with a session that is never committed, and even a
committing override is driven by an in-process transport that finishes the
whole ASGI call, teardown included, before ``post()`` returns. So these tests
run the real application under a real uvicorn server, over a socket, against a
scratch database, with the real ``get_write_db`` and separate connections for
writer and reader.

The commit is made slow on purpose (``before_commit`` sleeps). The bug is a
race, and a slow commit turns it from "intermittent on a fast machine" into
"every time": the client cannot get its response first *and* find the rows
unless the response waited for the commit.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.routing import APIRoute
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.api import deps as api_deps
from app.api.app import create_app
from app.api.deps import get_write_db
from app.db.models.common import SubmissionStatus
from app.db.models.idempotency import IdempotencyRecord
from app.db.models.species import Species
from app.db.models.submission import Submission
from tests import conftest
from tests.conftest import scratch_database_name

BACKEND_ROOT = Path(__file__).resolve().parents[2]

CONFORMER_ENDPOINT = "/api/v1/uploads/conformers"
SESSION_COOKIE = "tckdb_session"

#: Long enough that a client on loopback always wins the race against a commit
#: that is still pending, short enough not to dominate the suite.
COMMIT_DELAY_S = 0.6


def _conformer_payload(label: str, element: str = "H", multiplicity: int = 2) -> dict:
    return {
        "species_entry": {"smiles": f"[{element}]", "charge": 0, "multiplicity": multiplicity},
        "geometry": {"xyz_text": f"1\n{element} atom\n{element} 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
    }


# ---------------------------------------------------------------------------
# A real server over a scratch database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator:
    """A database made by ``alembic upgrade head`` alone, dropped afterwards.

    Committed rows cannot be cleaned up row by row (the science tables carry
    append-only triggers), so the tests that commit for real do it in a
    database that is dropped whole.
    """
    db_name = scratch_database_name("commit_before_response")
    conftest._recreate_test_database(db_name)
    engine = None
    try:
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=conftest._db_env(db_name),
            check=True,
            capture_output=True,
            text=True,
        )
        engine = api_deps.create_app_engine(
            conftest._database_url(db_name), future=True, pool_pre_ping=False
        )
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        conftest._drop_test_database(db_name)


@pytest.fixture
def commit_delay(scratch_engine) -> Iterator[dict]:
    """Point the app's session factory at the scratch database.

    ``SessionLocal`` is configured in place, so every module that imported it
    by name (the failed-upload audit, the idempotency writer) follows. The
    returned dict is the delay switch: ``commit_delay["s"] = 0.6`` makes every
    commit through that factory take that long *before* it happens.
    """
    factory = api_deps.SessionLocal
    previous = factory.kw.get("bind")
    factory.configure(bind=scratch_engine)
    state = {"s": 0.0}

    def slow_commit(_session) -> None:
        if state["s"]:
            time.sleep(state["s"])

    event.listen(factory, "before_commit", slow_commit)
    try:
        yield state
    finally:
        event.remove(factory, "before_commit", slow_commit)
        factory.configure(bind=previous)


@pytest.fixture
def base_url(commit_delay) -> Iterator[str]:
    """The real application, served by uvicorn on an ephemeral loopback port."""
    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the uvicorn test server did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _session_cookie(response: httpx.Response) -> str:
    match = re.search(rf"{SESSION_COOKIE}=([^;]+)", response.headers.get("set-cookie", ""))
    assert match, f"no session cookie in response: {response.status_code} {response.text}"
    return match.group(1)


def _register(base_url: str) -> str:
    """Create an account and return its session token (fast path: no delay)."""
    name = f"wcbr_{uuid.uuid4().hex[:12]}"
    response = httpx.post(
        f"{base_url}/api/v1/auth/register",
        json={"username": name, "password": "correct horse battery staple"},
        timeout=30,
    )
    assert response.status_code == 201, response.text
    return _session_cookie(response)


# ---------------------------------------------------------------------------
# 1. Read-your-writes
# ---------------------------------------------------------------------------


class TestReadAfterWrite:
    def test_get_immediately_after_a_201_upload_finds_it(
        self, base_url, commit_delay
    ) -> None:
        token = _register(base_url)
        headers = {"Cookie": f"{SESSION_COOKIE}={token}"}

        commit_delay["s"] = COMMIT_DELAY_S
        posted = httpx.post(
            f"{base_url}{CONFORMER_ENDPOINT}",
            json=_conformer_payload("read-after-write"),
            headers=headers,
            timeout=30,
        )
        assert posted.status_code == 201, posted.text

        # No sleep and no polling: this is the client's very next request.
        calc_ref = posted.json()["primary_calculation"]["calculation_id"]
        read = httpx.get(
            f"{base_url}/api/v1/calculations/{calc_ref}", headers=headers, timeout=30
        )
        assert read.status_code == 200, (
            "the server answered 201 before the upload was committed: "
            f"GET right after it returned {read.status_code} {read.text}"
        )

    def test_a_keyed_upload_is_replayable_the_moment_it_is_acknowledged(
        self, base_url, commit_delay
    ) -> None:
        """The receipt commits with the upload, so it exists when the 201 arrives."""
        token = _register(base_url)
        headers = {"Cookie": f"{SESSION_COOKIE}={token}", "Idempotency-Key": "wcbr-replay-key-0001"}
        payload = _conformer_payload("keyed-replay", element="Li", multiplicity=2)

        commit_delay["s"] = COMMIT_DELAY_S
        first = httpx.post(f"{base_url}{CONFORMER_ENDPOINT}", json=payload, headers=headers, timeout=30)
        assert first.status_code == 201, first.text
        commit_delay["s"] = 0.0
        second = httpx.post(f"{base_url}{CONFORMER_ENDPOINT}", json=payload, headers=headers, timeout=30)

        assert second.status_code == 201, second.text
        assert second.headers.get("Idempotency-Replayed") == "true", (
            "the retry re-executed the upload because the first request's "
            "idempotency receipt was not committed when it was acknowledged"
        )
        assert second.json() == first.json()

    def test_a_key_minted_right_after_login_is_authorised(
        self, base_url, commit_delay
    ) -> None:
        commit_delay["s"] = COMMIT_DELAY_S
        registered = httpx.post(
            f"{base_url}/api/v1/auth/register",
            json={
                "username": f"wcbr_{uuid.uuid4().hex[:12]}",
                "password": "correct horse battery staple",
            },
            timeout=30,
        )
        assert registered.status_code == 201, registered.text

        minted = httpx.post(
            f"{base_url}/api/v1/auth/api-keys",
            json={"label": "right after login"},
            headers={"Cookie": f"{SESSION_COOKIE}={_session_cookie(registered)}"},
            timeout=30,
        )
        assert minted.status_code == 201, (
            "the session the server had just handed out was not committed yet: "
            f"POST /auth/api-keys returned {minted.status_code} {minted.text}"
        )

    def test_login_then_key_mint_is_authorised(self, base_url, commit_delay) -> None:
        """The exact sequence from the issue: ``/auth/login`` then ``/auth/api-keys``."""
        name = f"wcbr_{uuid.uuid4().hex[:12]}"
        password = "correct horse battery staple"
        first = httpx.post(
            f"{base_url}/api/v1/auth/register",
            json={"username": name, "password": password},
            timeout=30,
        )
        assert first.status_code == 201, first.text

        commit_delay["s"] = COMMIT_DELAY_S
        login = httpx.post(
            f"{base_url}/api/v1/auth/login",
            json={"username": name, "password": password},
            timeout=30,
        )
        assert login.status_code == 200, login.text
        minted = httpx.post(
            f"{base_url}/api/v1/auth/api-keys",
            json={"label": "after login"},
            headers={"Cookie": f"{SESSION_COOKIE}={_session_cookie(login)}"},
            timeout=30,
        )
        assert minted.status_code == 201, (
            f"POST /auth/api-keys right after /auth/login returned "
            f"{minted.status_code} {minted.text}"
        )


# ---------------------------------------------------------------------------
# 2. A commit that fails is not a 201
# ---------------------------------------------------------------------------


@pytest.fixture
def fails_at_commit(scratch_engine) -> Iterator[None]:
    """A deferred constraint that refuses every new ``species`` row at COMMIT.

    ``INITIALLY DEFERRED`` is what makes this a *commit-time* failure: the
    INSERT succeeds, the route returns normally, and the database only says no
    when the transaction ends.
    """
    with scratch_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE FUNCTION wcbr_refuse_at_commit() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'refused at commit' USING ERRCODE = '23514';
                END $$
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE CONSTRAINT TRIGGER wcbr_refuse_species_at_commit
                AFTER INSERT ON species
                DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION wcbr_refuse_at_commit()
                """
            )
        )
    try:
        yield
    finally:
        with scratch_engine.begin() as connection:
            connection.execute(text("DROP TRIGGER wcbr_refuse_species_at_commit ON species"))
            connection.execute(text("DROP FUNCTION wcbr_refuse_at_commit()"))


class TestCommitFailureIsNotASuccess:
    def test_a_failed_commit_is_a_coded_error_not_a_201(
        self, base_url, fails_at_commit, scratch_engine
    ) -> None:
        token = _register(base_url)
        with Session(scratch_engine) as probe:
            failed_before = probe.scalar(
                select(func.count()).select_from(Submission).where(
                    Submission.status == SubmissionStatus.failed
                )
            )

        response = httpx.post(
            f"{base_url}{CONFORMER_ENDPOINT}",
            # A species no earlier test stored: the trigger fires on INSERT, and an
            # upload of an existing species inserts nothing.
            json=_conformer_payload("commit-fails", element="Ne", multiplicity=1),
            headers={"Cookie": f"{SESSION_COOKIE}={token}"},
            timeout=30,
        )

        assert response.status_code != 201, (
            "the client was told the upload succeeded although the database "
            f"refused it at commit: {response.text}"
        )
        assert response.status_code in (409, 422, 500, 503), response.text
        assert response.json().get("code"), f"the error is not coded: {response.text}"

        with Session(scratch_engine) as probe:
            assert probe.scalar(
                select(func.count()).select_from(Species).where(Species.smiles == "[Ne]")
            ) == 0
            # ``audit_upload_failure_at_commit`` still records the failure.
            failed_after = probe.scalar(
                select(func.count()).select_from(Submission).where(
                    Submission.status == SubmissionStatus.failed
                )
            )
        assert failed_after == failed_before + 1, (
            "a commit-time upload failure left no failed-upload audit row"
        )

    def test_a_failed_commit_leaves_no_idempotency_receipt(
        self, base_url, fails_at_commit, scratch_engine
    ) -> None:
        """Upload and receipt commit together, so they also fail together."""
        token = _register(base_url)
        key = "wcbr-failed-key-0001"

        response = httpx.post(
            f"{base_url}{CONFORMER_ENDPOINT}",
            json=_conformer_payload("keyed-fails", element="Ar", multiplicity=1),
            headers={"Cookie": f"{SESSION_COOKIE}={token}", "Idempotency-Key": key},
            timeout=30,
        )

        assert response.status_code != 201, response.text
        with Session(scratch_engine) as probe:
            receipts = probe.scalar(
                select(func.count()).select_from(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == key
                )
            )
        assert receipts == 0, "a receipt was stored for an upload that never committed"


# ---------------------------------------------------------------------------
# 3. Every write route commits before the response
# ---------------------------------------------------------------------------


def _dependants(dependant, seen=None):
    seen = set() if seen is None else seen
    for sub in dependant.dependencies:
        yield sub
        if id(sub) not in seen:
            seen.add(id(sub))
            yield from _dependants(sub, seen)


def _write_session_users() -> list[tuple[str, str, object]]:
    """``(method+path, dependency name, dependant)`` for every use of the write session."""
    app = create_app()
    found = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in _dependants(route.dependant):
            if dep.call is get_write_db:
                found.append((f"{sorted(route.methods)} {route.path}", route.name, dep))
    return found


def test_the_write_session_is_used_by_routes() -> None:
    """Guards the guard: a walk that finds nothing would pass vacuously."""
    users = _write_session_users()
    assert len(users) > 40, len(users)


def test_every_use_of_the_write_session_commits_before_the_response() -> None:
    """Each ``Depends(get_write_db)`` must say ``scope="function"``.

    The scope is the whole fix, and it is declared per use, so one route that
    forgets it commits after its response again -- and, because FastAPI caches
    a dependency per scope, gets a *second* session alongside the
    idempotency dependency's.
    """
    wrong = [
        f"{where} ({name}): scope={dep.scope!r}"
        for where, name, dep in _write_session_users()
        if dep.scope != "function"
    ]
    assert not wrong, "write session commits after the response on:\n" + "\n".join(wrong)
