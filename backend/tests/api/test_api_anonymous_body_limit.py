"""A caller with no valid credential may not make the API read a large body.

FastAPI reads and parses a body before authentication runs, so an unauthenticated junk POST used to be read in full
before its 401. ``AnonymousBodyLimitMiddleware`` refuses a body over ``settings.anonymous_max_body_bytes`` (413
``anonymous_body_too_large``) before reading it, unless the request carries a valid API key or session cookie.

Every refusal has a control that the same request succeeds past the middleware, so none of these can pass by refusing
everything. Credentials are committed rows (the middleware uses its own session, not the test's rolled-back one) and
are deleted again by the fixture.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.anonymous_body_limit import CODE_ANONYMOUS_BODY_TOO_LARGE
from app.api.config import settings
from app.db.models.api_key import ApiKey
from app.db.models.user_session import UserSession
from tests.api.test_api_bundle_limits import DRY_RUN, SUBMIT

CAP = 500
ROUTE = "/api/v1/jobs/thermo"  # needs a credential; a body that reaches it is parsed (not JSON: 422)
JUNK = b"{ not json " + b"x" * 2 * CAP
TEST_KEY = "test-api-key-for-tckdb"  # committed by the session fixture, valid


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _chunks(payload: bytes, size: int = 100) -> Iterator[bytes]:
    for start in range(0, len(payload), size):
        yield payload[start : start + size]


@pytest.fixture(autouse=True)
def _small_allowance(monkeypatch) -> None:
    monkeypatch.setattr(settings, "anonymous_max_body_bytes", CAP)


@pytest.fixture
def committed_credentials(db_engine, _api_test_user):
    """A revoked API key and a live session token, committed for the duration of the test."""
    revoked_key = "revoked-" + secrets.token_hex(8)
    token = "session-" + secrets.token_hex(8)
    expired = "expired-" + secrets.token_hex(8)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(db_engine) as s, s.begin():
        s.add(ApiKey(user_id=_api_test_user, key_hash=_sha(revoked_key), label="anon-limit revoked", revoked_at=now))
        s.add(UserSession(user_id=_api_test_user, token_hash=_sha(token), expires_at=now + timedelta(hours=1)))
        s.add(UserSession(user_id=_api_test_user, token_hash=_sha(expired), expires_at=now - timedelta(hours=1)))
    yield {"revoked_key": revoked_key, "session": token, "expired_session": expired}
    with Session(db_engine) as s, s.begin():
        hashes = [_sha(revoked_key), _sha(token), _sha(expired)]
        s.execute(delete(ApiKey).where(ApiKey.key_hash.in_(hashes)))
        s.execute(delete(UserSession).where(UserSession.token_hash.in_(hashes)))


def _post(client, payload=JUNK, *, chunked=False, headers=None, cookies=None, route=ROUTE):
    h = {"content-type": "application/json", **(headers or {})}
    return client.post(route, content=_chunks(payload) if chunked else payload, headers=h, cookies=cookies)


def _refused(resp) -> bool:
    return resp.status_code == 413 and resp.json()["code"] == CODE_ANONYMOUS_BODY_TOO_LARGE


# --- anonymous -------------------------------------------------------------------------------------------------


def test_anonymous_declared_oversize_is_refused_413_with_a_coded_message(client) -> None:
    resp = _post(client)
    assert _refused(resp), resp.text
    body = resp.json()
    assert body["context"] == {"max_bytes": CAP, "given_bytes": len(JUNK)}
    assert "Authenticate" in body["detail"]
    assert "id" not in body["context"]


def test_anonymous_chunked_oversize_is_refused_413(client) -> None:
    resp = _post(client, chunked=True)
    assert _refused(resp), resp.text
    assert resp.json()["context"] == {"max_bytes": CAP}  # no length declared


def test_anonymous_under_the_allowance_is_unchanged(client) -> None:
    small = b"{ not json"
    for chunked in (False, True):
        resp = _post(client, small, chunked=chunked)
        assert not _refused(resp)
        assert resp.status_code == 422  # reached the route's own parse


def test_allowance_zero_disables_it(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "anonymous_max_body_bytes", 0)
    assert not _refused(_post(client))


def test_a_get_is_never_refused(client) -> None:
    resp = client.request("GET", "/api/v1/health", content=JUNK)
    assert not _refused(resp)


def test_a_path_outside_api_is_not_refused(client) -> None:
    assert not _refused(_post(client, route="/not-api/x"))


# --- ASGI level: the body is not read ------------------------------------------------------------------------


def _drive(app, *, chunks, headers, path=ROUTE):
    """Run *app* once; return (status, body, number of receive() calls made by the app stack)."""
    sent: list[dict] = []
    pulled = 0
    queue = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)
    ]

    async def receive():
        nonlocal pulled
        pulled += 1
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers], "client": ("203.0.113.9", 1),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"), pulled


def test_anonymous_declared_oversize_does_not_read_the_body(client) -> None:
    status, body, pulled = _drive(
        client.app, chunks=[JUNK], headers=[("content-type", "application/json"), ("content-length", str(len(JUNK)))]
    )
    assert status == 413 and CODE_ANONYMOUS_BODY_TOO_LARGE.encode() in body
    assert pulled == 0


def test_anonymous_chunked_oversize_stops_reading_at_the_allowance(client) -> None:
    chunks = list(_chunks(b"x" * 100 * 50))  # 50 chunks of 100; the allowance is passed on the 6th
    status, body, pulled = _drive(client.app, chunks=chunks, headers=[("content-type", "application/json")])
    assert status == 413 and CODE_ANONYMOUS_BODY_TOO_LARGE.encode() in body
    assert pulled <= 8, pulled  # the rest of the 50 chunks was never pulled


def test_credentialed_chunked_body_is_read_to_the_end(client) -> None:
    chunks = list(_chunks(b"x" * 100 * 50))
    status, _body, pulled = _drive(
        client.app, chunks=chunks, headers=[("content-type", "application/json"), ("x-api-key", TEST_KEY)]
    )
    assert status == 422  # the route parsed it
    assert pulled >= 50


# --- credentials ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("chunked", [False, True], ids=["declared", "chunked"])
def test_a_valid_api_key_reaches_the_route(client, chunked) -> None:
    resp = _post(client, chunked=chunked, headers={"X-API-Key": TEST_KEY})
    assert not _refused(resp)
    assert resp.status_code == 422


@pytest.mark.parametrize("chunked", [False, True], ids=["declared", "chunked"])
def test_an_unknown_api_key_is_no_credential(client, chunked) -> None:
    assert _refused(_post(client, chunked=chunked, headers={"X-API-Key": "nope-" + secrets.token_hex(8)}))


@pytest.mark.parametrize("chunked", [False, True], ids=["declared", "chunked"])
def test_a_revoked_api_key_is_no_credential(client, committed_credentials, chunked) -> None:
    assert _refused(_post(client, chunked=chunked, headers={"X-API-Key": committed_credentials["revoked_key"]}))


@pytest.mark.parametrize("chunked", [False, True], ids=["declared", "chunked"])
def test_a_valid_session_cookie_reaches_the_route(client, committed_credentials, chunked) -> None:
    resp = _post(client, chunked=chunked, cookies={"tckdb_session": committed_credentials["session"]})
    assert not _refused(resp)
    assert resp.status_code == 422


def test_an_expired_or_unknown_session_is_no_credential(client, committed_credentials) -> None:
    assert _refused(_post(client, cookies={"tckdb_session": committed_credentials["expired_session"]}))
    assert _refused(_post(client, cookies={"tckdb_session": "unknown-" + secrets.token_hex(8)}))


def test_an_invalid_key_does_not_fall_back_to_a_valid_cookie(client, committed_credentials) -> None:
    resp = _post(
        client,
        headers={"X-API-Key": "nope-" + secrets.token_hex(8)},
        cookies={"tckdb_session": committed_credentials["session"]},
    )
    assert _refused(resp)  # as app.api.deps: a sent key decides alone


def test_no_credential_lookup_is_made_for_a_body_within_the_allowance(client, monkeypatch) -> None:
    from app.api import anonymous_body_limit

    calls = []

    def _spy(scope):
        calls.append(scope["path"])
        return False

    monkeypatch.setattr(anonymous_body_limit, "has_valid_credential", _spy)
    assert not _refused(_post(client, b"{ not json"))
    assert not _refused(_post(client, b"{ not json", chunked=True))
    assert calls == []
    assert _refused(_post(client))  # control: the spy is the one consulted for a large body
    assert calls == [ROUTE]


# --- agreement with the stricter caps --------------------------------------------------------------------------


@pytest.mark.parametrize("route", [DRY_RUN, SUBMIT], ids=["dry_run", "submit"])
def test_bundle_cap_keeps_its_code_for_a_credentialed_caller(client, monkeypatch, route) -> None:
    monkeypatch.setattr(settings, "bundle_max_body_bytes", CAP)
    resp = _post(client, headers={"X-API-Key": TEST_KEY}, route=route)
    assert resp.status_code == 413 and resp.json()["code"] == "bundle_too_large"


@pytest.mark.parametrize("route", [DRY_RUN, SUBMIT], ids=["dry_run", "submit"])
def test_an_anonymous_bundle_is_told_to_authenticate_first(client, monkeypatch, route) -> None:
    monkeypatch.setattr(settings, "bundle_max_body_bytes", CAP)
    assert _refused(_post(client, route=route))


EXPORT = "/api/v1/scientific/networks/net_x/kinetics/export-selected"


def test_export_keeps_its_own_cap_and_code_for_everyone(client, monkeypatch) -> None:
    from app.api import export_limits

    monkeypatch.setattr(export_limits, "MAX_BODY_BYTES", CAP)  # the class default was bound at import
    app = client.app
    # The middleware instance holds its limit from construction; drive an instance with the small limit.
    wrapped = export_limits.NetworkExportBodyLimitMiddleware(app, limit=CAP)
    for headers in ([], [("x-api-key", TEST_KEY)]):
        status, body, pulled = _drive(
            wrapped,
            chunks=[JUNK],
            headers=[("content-type", "application/json"), ("content-length", str(len(JUNK))), *headers],
            path=EXPORT,
        )
        assert status == 413 and b"network_export_body_too_large" in body


def test_export_is_not_subject_to_the_anonymous_allowance(client) -> None:
    resp = _post(client, route=EXPORT)  # anonymous, over the allowance, under the export cap
    assert not _refused(resp)


# --- the credential lookup ----------------------------------------------------------------------------------------


def test_the_credential_lookup_runs_off_the_event_loop(client, monkeypatch) -> None:
    import threading

    from app.api import anonymous_body_limit

    loop_thread = threading.get_ident()  # _drive runs the app on this thread
    seen = []

    def _spy(scope):
        seen.append(threading.get_ident())
        return True

    monkeypatch.setattr(anonymous_body_limit, "has_valid_credential", _spy)
    _drive(client.app, chunks=[JUNK], headers=[("content-type", "application/json"), ("content-length", str(len(JUNK)))])
    assert seen and seen[0] != loop_thread


class _Orig(Exception):
    def __init__(self, sqlstate):
        super().__init__("driver text that must not reach the client")
        self.sqlstate = sqlstate


def _failing(exc):
    def _raise(_scope):
        raise exc

    return _raise


@pytest.mark.parametrize("chunked", [False, True], ids=["declared", "chunked"])
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (OperationalError("SELECT secret", {}, _Orig("08006")), "database_unavailable"),
        (OperationalError("SELECT secret", {}, _Orig("57014")), "query_timeout"),
        (OSError("connection refused"), "database_unavailable"),
    ],
    ids=["unavailable", "statement_timeout", "oserror"],
)
def test_a_database_failure_during_the_lookup_is_a_coded_503(client, monkeypatch, chunked, exc, code) -> None:
    from app.api import anonymous_body_limit

    monkeypatch.setattr(anonymous_body_limit, "has_valid_credential", _failing(exc))
    resp = _post(client, chunked=chunked, headers={"X-API-Key": TEST_KEY})
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == code
    assert "secret" not in resp.text and "driver text" not in resp.text


def test_a_chunked_lookup_failure_stops_reading(client, monkeypatch) -> None:
    from app.api import anonymous_body_limit

    monkeypatch.setattr(
        anonymous_body_limit, "has_valid_credential", _failing(OperationalError("s", {}, _Orig("08006")))
    )
    status, body, pulled = _drive(
        client.app, chunks=list(_chunks(b"x" * 5000)), headers=[("content-type", "application/json")]
    )
    assert status == 503 and b"database_unavailable" in body
    assert pulled <= 8, pulled


def test_a_chunked_lookup_failure_tells_the_app_the_client_is_gone(monkeypatch) -> None:
    from app.api import anonymous_body_limit
    from app.api.anonymous_body_limit import AnonymousBodyLimitMiddleware

    monkeypatch.setattr(
        anonymous_body_limit, "has_valid_credential", _failing(OperationalError("s", {}, _Orig("08006")))
    )
    received = []

    async def inner(scope, receive, send):
        while True:
            message = await receive()
            received.append(message["type"])
            if message["type"] == "http.disconnect":
                return

    _drive(AnonymousBodyLimitMiddleware(inner), chunks=list(_chunks(b"x" * 5000)), headers=[])
    assert received[-1] == "http.disconnect"
    assert received.count("http.request") == 5  # the chunk that crossed the allowance was withheld
