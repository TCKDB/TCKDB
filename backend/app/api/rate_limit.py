"""Application-level rate limiting for the public API.

This is the in-process implementation used for hosted MVP. It is a
fixed-window counter keyed by ``(bucket, identity)``: every distinct
caller gets a per-bucket counter that resets at the start of the next
window. The window size and budget are bucket-specific.

The middleware classifies each request into a route-class-aware
bucket and chooses an identity:

- ``/api/v1/auth/login`` and ``/api/v1/auth/api-keys`` → ``login``
  bucket (per-minute, IP-keyed). Tight: credential-stuffing target.
- ``/api/v1/auth/register`` → ``register`` bucket (per-hour, IP-keyed).
  Tight: account-spam target.
- Public scientific reads (GET ``/api/v1/scientific/...``, POST
  ``/api/v1/scientific/.../search``, GET ``/api/v1/workflow-tools``,
  GET ``/api/v1/workflow-tool-releases``) split by whether a
  credential is present:
  - With credential → ``auth_read`` (per-minute, credential-keyed).
  - Without credential → ``anon_read`` (per-minute, IP-keyed).
- Any other request with a credential → ``auth_write`` for mutating
  methods (POST/PUT/PATCH/DELETE) or ``auth_read`` for read methods
  on non-public-read paths. Both credential-keyed.
- Everything else anonymous → ``anon_other`` (per-minute, IP-keyed).
  Anonymous writes land here on purpose: they must not inherit the
  generous read budget.

Identity derivation honors ``settings.trusted_proxy_header`` when set
so a reverse proxy that overwrites e.g. ``X-Real-IP`` is respected.
Without that setting we use the ASGI transport peer, which is
spoof-resistant.

The store is in-process. A single worker is fine for hosted MVP;
multi-worker deployments need a shared backend (Redis) — left as
follow-up.

Tests reset the store between test runs and can flip
``settings.rate_limit_enabled`` off via monkeypatch.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.config import settings


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    """Per-(bucket, identity) sliding counter state.

    A single fixed window: when ``window_start + window_seconds`` is
    reached, the count resets to zero. The window is keyed by both the
    bucket name and the identity, so each caller has an independent
    counter per bucket.
    """

    window_start: float
    count: int


class _RateLimitStore:
    """Thread-safe fixed-window counter store.

    Single-process in-memory implementation. Each ``(bucket, identity)``
    pair gets a ``_Bucket`` entry that the middleware advances or
    resets on each request.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def hit(
        self, *, bucket: str, identity: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """Record a request and return ``(allowed, retry_after_seconds)``.

        Returns ``(True, 0)`` when the call is within budget. Returns
        ``(False, retry)`` when the call exceeds the budget, where
        ``retry`` is the integer number of seconds until the current
        window rolls over.
        """
        now = time.monotonic()
        key = (bucket, identity)
        with self._lock:
            entry = self._store.get(key)
            if entry is None or now - entry.window_start >= window_seconds:
                self._store[key] = _Bucket(window_start=now, count=1)
                return True, 0
            if entry.count >= limit:
                retry = max(1, int(window_seconds - (now - entry.window_start)))
                return False, retry
            entry.count += 1
            return True, 0

    def reset(self) -> None:
        """Drop all buckets — only used by tests."""
        with self._lock:
            self._store.clear()


_STORE = _RateLimitStore()


def reset_rate_limit_store() -> None:
    """Drop all per-caller counters.

    Pytest fixtures call this between tests so the global state stays
    deterministic across the suite.
    """
    _STORE.reset()


# ---------------------------------------------------------------------------
# Identity / bucket selection
# ---------------------------------------------------------------------------


def parse_forwarded_header(raw: str | None) -> str | None:
    """Parse a trusted-proxy header value into a single client IP.

    Handles the three header shapes deployments use:

    - ``X-Forwarded-For: client, proxy1, proxy2`` — comma-separated
      list; the leftmost entry is the original client. Take the
      first entry and strip whitespace.
    - ``X-Real-IP: 1.2.3.4`` and ``CF-Connecting-IP: 1.2.3.4`` —
      single-IP headers. The split-and-take-first behavior happens
      to be correct because no comma is present.
    - ``CloudFront-Viewer-Address: 203.0.113.10:443`` — IPv4 host
      with a port suffix. Strip the port when the value looks like
      ``a.b.c.d:port`` (exactly one colon, dotted-quad on the left).
      Raw IPv6 host:port strings are not parsed; CloudFront users on
      IPv6 should configure a clean client-IP header.

    Returns ``None`` when the input is missing or empty so callers
    can fall back to the ASGI transport peer.
    """
    if not raw:
        return None
    first = raw.split(",", 1)[0].strip()
    if not first:
        return None
    # IPv4 host:port — exactly one colon and a dotted-quad host.
    if first.count(":") == 1:
        host, _, port = first.partition(":")
        if port.isdigit() and host.count(".") == 3 and all(
            seg.isdigit() for seg in host.split(".")
        ):
            return host
    return first


def _client_ip(request: Request) -> str:
    """Return the client IP, honoring a configured trusted proxy header.

    When ``settings.trusted_proxy_header`` is unset, return the ASGI
    transport peer. That cannot be spoofed by the client but is wrong
    behind any reverse proxy. Hosted deployments should set the header
    name (e.g. ``X-Real-IP``) and ensure the proxy overwrites it.

    When the configured header is absent or empty on a given request,
    we fall through to the transport peer so a misconfigured caller
    can still be rate-limited (rather than ending up in a single
    shared "unknown" bucket).
    """
    header_name = settings.trusted_proxy_header
    if header_name:
        parsed = parse_forwarded_header(request.headers.get(header_name))
        if parsed is not None:
            return parsed
    client = request.client
    return client.host if client is not None else "unknown"


def _credential_fingerprint(request: Request) -> str | None:
    """Return a short hash of any auth credential present on the request.

    Used so two callers on one IP get distinct ``auth`` buckets. The
    hash is truncated since we only need a key, not a verifiable
    digest.
    """
    api_key = request.headers.get("x-api-key")
    if api_key:
        return "apikey:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
    cookie = request.cookies.get("tckdb_session")
    if cookie:
        return "session:" + hashlib.sha256(cookie.encode()).hexdigest()[:16]
    return None


_AUTH_LOGIN_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/api-keys",
})
_AUTH_REGISTER_PATHS = frozenset({
    "/api/v1/auth/register",
})
_HEALTH_PATHS = frozenset({
    "/api/v1/health",
    "/api/v1/health/",
})

# Public read-classified GET prefixes. POST searches are handled
# separately because POST is normally mutating; only the explicit
# ``.../search`` suffix on the scientific surface is read-classified.
_PUBLIC_READ_GET_PREFIXES: tuple[str, ...] = (
    "/api/v1/scientific/",
    "/api/v1/workflow-tools",
    "/api/v1/workflow-tool-releases",
)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_login_path(method: str, path: str) -> bool:
    """True for the login + API-key-issuance routes."""
    return method == "POST" and path in _AUTH_LOGIN_PATHS


def _is_register_path(method: str, path: str) -> bool:
    """True for the account registration route."""
    return method == "POST" and path in _AUTH_REGISTER_PATHS


def _is_health_path(path: str) -> bool:
    """True for the operator health-check route(s)."""
    return path in _HEALTH_PATHS


def _is_public_read_path(method: str, path: str) -> bool:
    """True when *path* is a public read or read-via-POST-search route.

    GETs on the scientific and workflow-tool surfaces are reads. POSTs
    are reads only when they target a scientific search endpoint
    (``/api/v1/scientific/.../search``); the ``/scientific/`` namespace
    is read-only by convention so this prefix+suffix test is safe.
    Mutating sub-routes added under ``/scientific/`` in the future
    would need to be excluded explicitly.
    """
    if method == "GET":
        return any(path.startswith(p) for p in _PUBLIC_READ_GET_PREFIXES)
    if method == "POST":
        return path.startswith("/api/v1/scientific/") and path.endswith("/search")
    return False


def _is_mutating_method(method: str) -> bool:
    """True for HTTP verbs that normally change server state.

    Callers must first rule out routes that are reads despite using
    POST (the scientific search endpoints).
    """
    return method in _MUTATING_METHODS


def _classify(request: Request) -> tuple[str, int, int, str]:
    """Pick ``(bucket, budget, window_seconds, identity)`` for *request*.

    Policy invariant:

    - ``auth_write`` is method-sensitive (any authenticated mutation).
    - ``anon_read``  is route-sensitive (anonymous public read).
    - ``auth_read``  is the authenticated fallback for everything not
      caught by login/register/auth_write.
    - ``anon_other`` is the anonymous fallback so anonymous mutating
      requests never inherit the read budget.

    Ordering matters: login/register are matched first because they
    are POSTs and would otherwise drop through to the mutating branch.
    Public-read classification is checked before the mutating-method
    check so the scientific ``POST .../search`` endpoints are
    correctly classified as reads.
    """
    method = request.method.upper()
    path = request.url.path
    ip = _client_ip(request)

    if _is_login_path(method, path):
        return ("login", settings.rate_limit_auth_login_per_minute, 60, ip)

    if _is_register_path(method, path):
        return ("register", settings.rate_limit_register_per_hour, 3600, ip)

    credential = _credential_fingerprint(request)

    if _is_public_read_path(method, path):
        if credential is not None:
            return (
                "auth_read",
                settings.rate_limit_auth_read_per_minute,
                60,
                credential,
            )
        return (
            "anon_read",
            settings.rate_limit_anon_read_per_minute,
            60,
            ip,
        )

    if credential is not None:
        if _is_mutating_method(method):
            return (
                "auth_write",
                settings.rate_limit_auth_write_per_minute,
                60,
                credential,
            )
        # Authenticated non-mutating, non-public-read fallback (e.g. a
        # future admin/dashboard/status GET). Generous on purpose —
        # add ``auth_other`` only when a real route class needs its
        # own budget.
        return (
            "auth_read",
            settings.rate_limit_auth_read_per_minute,
            60,
            credential,
        )

    return (
        "anon_other",
        settings.rate_limit_anon_other_per_minute,
        60,
        ip,
    )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests that exceed their bucket's fixed-window budget.

    The middleware is registered unconditionally; it short-circuits to
    a pass-through when ``settings.rate_limit_enabled`` is false so
    tests and dev environments stay fast.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)
        # Don't gate health checks — operators ping these on a loop.
        if _is_health_path(request.url.path):
            return await call_next(request)

        bucket, limit, window, identity = _classify(request)
        allowed, retry_after = _STORE.hit(
            bucket=bucket,
            identity=identity,
            limit=limit,
            window_seconds=window,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Too many requests. Retry after the current "
                        "rate-limit window expires."
                    ),
                    "code": "rate_limit_exceeded",
                    "bucket": bucket,
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
