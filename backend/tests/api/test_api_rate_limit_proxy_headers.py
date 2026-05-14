"""Tests for trusted-proxy header parsing in the rate limiter.

Split into two layers:

- Unit tests for :func:`app.api.rate_limit.parse_forwarded_header` cover
  every header shape (single IP, comma-list, host:port, IPv6
  bracket-form) without spinning up a request.
- Integration tests verify that ``TRUSTED_PROXY_HEADER`` actually
  changes which bucket a request lands in. Two requests with the same
  transport peer but different ``X-Forwarded-For`` clients should
  share a bucket when the header is untrusted, and end up in distinct
  buckets when it is trusted.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.config import settings
from app.api.deps import get_db, get_write_db
from app.api.rate_limit import parse_forwarded_header, reset_rate_limit_store


# ---------------------------------------------------------------------------
# Pure parser tests
# ---------------------------------------------------------------------------


def test_parse_returns_none_for_missing_value():
    assert parse_forwarded_header(None) is None
    assert parse_forwarded_header("") is None
    assert parse_forwarded_header("   ") is None


def test_parse_extracts_first_entry_from_xff_list():
    """``X-Forwarded-For`` is comma-separated; the leftmost is the client."""
    assert parse_forwarded_header("1.2.3.4, 10.0.0.1, 10.0.0.2") == "1.2.3.4"


def test_parse_strips_whitespace_around_first_entry():
    assert parse_forwarded_header("   1.2.3.4 , 10.0.0.1   ") == "1.2.3.4"


def test_parse_returns_single_ip_unchanged():
    """``X-Real-IP`` / ``CF-Connecting-IP`` carry a single literal IP."""
    assert parse_forwarded_header("1.2.3.4") == "1.2.3.4"


def test_parse_strips_ipv4_host_port():
    """``CloudFront-Viewer-Address`` includes ``:port``; strip it."""
    assert parse_forwarded_header("203.0.113.10:443") == "203.0.113.10"


def test_parse_leaves_raw_ipv6_alone():
    """Raw IPv6 has multiple colons; we don't attempt to dissect a port."""
    assert parse_forwarded_header("2001:db8::1") == "2001:db8::1"


def test_parse_leaves_non_dotted_quad_with_port_alone():
    """Hostnames with a port are passed through verbatim — only IPv4:port is stripped."""
    assert parse_forwarded_header("example.com:443") == "example.com:443"


# ---------------------------------------------------------------------------
# Integration: bucket separation via X-Forwarded-For
# ---------------------------------------------------------------------------


@pytest.fixture
def low_budget_client(db_session: Session, monkeypatch):
    """A live TestClient with a tiny anonymous budget so 429 fires fast."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_anon_read_per_minute", 2)
    reset_rate_limit_store()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    reset_rate_limit_store()


def _hit(client: TestClient, *, forwarded: str | None) -> int:
    headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
    return client.get("/api/v1/scientific/reactions/search", headers=headers).status_code


def test_spoofed_xff_is_ignored_when_header_untrusted(
    low_budget_client, monkeypatch
):
    """With no trusted header, two different spoofed clients share a bucket."""
    monkeypatch.setattr(settings, "trusted_proxy_header", None)

    # Two calls from "client A" (spoofed) and one from "client B" (spoofed).
    assert _hit(low_budget_client, forwarded="1.2.3.4") != 429
    assert _hit(low_budget_client, forwarded="1.2.3.4") != 429
    # Both calls came from the same transport peer (127.0.0.1), so the
    # third one — even though it claims a different client — must hit
    # the same bucket and be rejected.
    assert _hit(low_budget_client, forwarded="9.9.9.9") == 429


def test_trusted_xff_separates_buckets_per_client(low_budget_client, monkeypatch):
    """With ``X-Forwarded-For`` trusted, each declared client is its own bucket."""
    monkeypatch.setattr(settings, "trusted_proxy_header", "X-Forwarded-For")

    # Drain client A's bucket.
    assert _hit(low_budget_client, forwarded="1.2.3.4") != 429
    assert _hit(low_budget_client, forwarded="1.2.3.4") != 429
    assert _hit(low_budget_client, forwarded="1.2.3.4") == 429

    # Client B is unaffected — different identity, fresh budget.
    assert _hit(low_budget_client, forwarded="9.9.9.9") != 429
    assert _hit(low_budget_client, forwarded="9.9.9.9") != 429
    assert _hit(low_budget_client, forwarded="9.9.9.9") == 429


def test_trusted_single_ip_header_resolves_full_value(low_budget_client, monkeypatch):
    """``X-Real-IP`` carries one IP; the parser must use it verbatim."""
    monkeypatch.setattr(settings, "trusted_proxy_header", "X-Real-IP")

    # Drain via X-Real-IP=A
    headers_a = {"X-Real-IP": "1.2.3.4"}
    for _ in range(settings.rate_limit_anon_read_per_minute):
        r = low_budget_client.get(
            "/api/v1/scientific/reactions/search", headers=headers_a
        )
        assert r.status_code != 429
    r = low_budget_client.get(
        "/api/v1/scientific/reactions/search", headers=headers_a
    )
    assert r.status_code == 429

    # X-Real-IP=B starts fresh.
    headers_b = {"X-Real-IP": "9.9.9.9"}
    r = low_budget_client.get(
        "/api/v1/scientific/reactions/search", headers=headers_b
    )
    assert r.status_code != 429


def test_missing_configured_header_falls_back_to_transport_peer(
    low_budget_client, monkeypatch
):
    """A request that omits the trusted header still gets rate-limited.

    Without the fallback, omitting the configured header would land
    every misconfigured caller in a shared "unknown" bucket. The
    fallback uses the ASGI transport peer instead, which is always
    available.
    """
    monkeypatch.setattr(settings, "trusted_proxy_header", "X-Real-IP")

    # No X-Real-IP supplied → falls back to transport peer (127.0.0.1).
    for _ in range(settings.rate_limit_anon_read_per_minute):
        r = low_budget_client.get("/api/v1/scientific/reactions/search")
        assert r.status_code != 429
    r = low_budget_client.get("/api/v1/scientific/reactions/search")
    assert r.status_code == 429
