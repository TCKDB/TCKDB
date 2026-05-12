"""Application-level rate limiting for the public API.

This is the in-process implementation used for hosted MVP. It is a
fixed-window counter keyed by ``(bucket, identity)``: every distinct
caller gets a per-bucket counter that resets at the start of the next
window. The window size and budget are bucket-specific.

The middleware classifies each request into a bucket and chooses an
identity:

- ``/api/v1/auth/login`` and ``/api/v1/auth/api-keys`` → ``login``
  bucket (per-minute, IP-keyed).
- ``/api/v1/auth/register`` → ``register`` bucket (per-hour, IP-keyed).
- Any other path with an authenticated identity hint (``X-API-Key``
  header or session cookie) → ``auth`` bucket (per-minute, keyed by a
  short hash of the credential so concurrent users on one IP don't
  share a budget).
- Everything else → ``anon`` bucket (per-minute, IP-keyed).

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


_AUTH_LOGIN_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/api-keys",
}
_AUTH_REGISTER_PATHS = {
    "/api/v1/auth/register",
}


def _classify(request: Request) -> tuple[str, int, int, str]:
    """Pick (bucket_name, budget, window_seconds, identity) for *request*.

    Returns a 4-tuple even when the limiter is disabled — callers
    check the master switch separately.
    """
    path = request.url.path
    ip = _client_ip(request)
    if path in _AUTH_LOGIN_PATHS:
        return (
            "login",
            settings.rate_limit_auth_login_per_minute,
            60,
            ip,
        )
    if path in _AUTH_REGISTER_PATHS:
        return (
            "register",
            settings.rate_limit_register_per_hour,
            3600,
            ip,
        )
    credential = _credential_fingerprint(request)
    if credential is not None:
        return (
            "auth",
            settings.rate_limit_auth_per_minute,
            60,
            credential,
        )
    return (
        "anon",
        settings.rate_limit_anon_per_minute,
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
        if request.url.path in ("/api/v1/health", "/api/v1/health/"):
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
