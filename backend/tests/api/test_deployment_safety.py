"""Tests for the hosted-deployment startup safety guard.

Covers :func:`app.api.startup_checks.validate_deployment_safety` and the
integration point in :func:`app.api.app.create_app`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.api.config import Settings
from app.api.startup_checks import (
    UnsafeDeploymentConfigError,
    validate_deployment_safety,
)


def _safe_settings(mode: str) -> Settings:
    """Return a Settings instance configured for hosted-safe operation."""
    return Settings(
        deployment_mode=mode,  # type: ignore[arg-type]
        auth_allow_open_registration=False,
        expose_api_docs=False,
        legacy_reads_require_auth=True,
        session_cookie_secure=True,
        allow_public_internal_ids=False,
        rate_limit_enabled=True,
        cors_allow_origins=[],
        db_statement_timeout_ms=30_000,
    )


# ---------------------------------------------------------------------------
# local mode is a no-op
# ---------------------------------------------------------------------------


def test_local_mode_allows_developer_defaults():
    s = Settings(deployment_mode="local")
    # The factory default is the local-dev posture — every unsafe-for-hosted
    # flag is at its dev-friendly value. The guard must not complain.
    assert s.auth_allow_open_registration is True
    assert s.expose_api_docs is True
    validate_deployment_safety(s)  # does not raise


# ---------------------------------------------------------------------------
# shared_private rejects each unsafe setting
# ---------------------------------------------------------------------------


@dataclass
class _UnsafeCase:
    overrides: dict
    expected_substr: str


@pytest.mark.parametrize(
    "case",
    [
        _UnsafeCase({"auth_allow_open_registration": True}, "AUTH_ALLOW_OPEN_REGISTRATION must be false"),
        _UnsafeCase({"expose_api_docs": True}, "EXPOSE_API_DOCS must be false"),
        _UnsafeCase({"legacy_reads_require_auth": False}, "LEGACY_READS_REQUIRE_AUTH must be true"),
        _UnsafeCase({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE must be true"),
        _UnsafeCase({"allow_public_internal_ids": True}, "ALLOW_PUBLIC_INTERNAL_IDS must be false"),
        _UnsafeCase({"rate_limit_enabled": False}, "RATE_LIMIT_ENABLED must be true"),
    ],
    ids=lambda c: next(iter(c.overrides)),
)
def test_shared_private_rejects_unsafe_setting(case: _UnsafeCase):
    s = _safe_settings("shared_private")
    for k, v in case.overrides.items():
        setattr(s, k, v)
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    assert case.expected_substr in str(ei.value)
    assert ei.value.mode == "shared_private"


def test_shared_private_accepts_safe_settings():
    validate_deployment_safety(_safe_settings("shared_private"))


# ---------------------------------------------------------------------------
# hosted_public mirrors shared_private and adds CORS wildcard rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [
        _UnsafeCase({"auth_allow_open_registration": True}, "AUTH_ALLOW_OPEN_REGISTRATION must be false"),
        _UnsafeCase({"expose_api_docs": True}, "EXPOSE_API_DOCS must be false"),
        _UnsafeCase({"legacy_reads_require_auth": False}, "LEGACY_READS_REQUIRE_AUTH must be true"),
        _UnsafeCase({"session_cookie_secure": False}, "SESSION_COOKIE_SECURE must be true"),
        _UnsafeCase({"allow_public_internal_ids": True}, "ALLOW_PUBLIC_INTERNAL_IDS must be false"),
        _UnsafeCase({"rate_limit_enabled": False}, "RATE_LIMIT_ENABLED must be true"),
    ],
    ids=lambda c: next(iter(c.overrides)),
)
def test_hosted_public_rejects_unsafe_setting(case: _UnsafeCase):
    s = _safe_settings("hosted_public")
    for k, v in case.overrides.items():
        setattr(s, k, v)
    with pytest.raises(UnsafeDeploymentConfigError):
        validate_deployment_safety(s)


def test_hosted_public_accepts_safe_settings():
    validate_deployment_safety(_safe_settings("hosted_public"))


def test_hosted_public_accepts_explicit_cors_allow_list():
    s = _safe_settings("hosted_public")
    s.cors_allow_origins = ["https://app.tckdb.example.org"]
    validate_deployment_safety(s)


def test_hosted_public_rejects_wildcard_cors():
    s = _safe_settings("hosted_public")
    s.cors_allow_origins = ["*"]
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    assert 'CORS_ALLOW_ORIGINS must not contain "*"' in str(ei.value)


def test_shared_private_rejects_wildcard_cors():
    s = _safe_settings("shared_private")
    s.cors_allow_origins = ["*"]
    with pytest.raises(UnsafeDeploymentConfigError):
        validate_deployment_safety(s)


# ---------------------------------------------------------------------------
# DB statement timeout: only the affirmatively-disabled value is rejected
# ---------------------------------------------------------------------------


def test_zero_statement_timeout_is_rejected():
    s = _safe_settings("hosted_public")
    s.db_statement_timeout_ms = 0
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    assert "DB_STATEMENT_TIMEOUT_MS" in str(ei.value)


def test_none_statement_timeout_is_allowed():
    # Operators are encouraged to set statement_timeout at the role
    # level instead — leaving the app-level value unset is acceptable.
    s = _safe_settings("hosted_public")
    s.db_statement_timeout_ms = None
    validate_deployment_safety(s)


# ---------------------------------------------------------------------------
# Error message lists every violation at once
# ---------------------------------------------------------------------------


def test_error_lists_all_violations_at_once():
    s = _safe_settings("hosted_public")
    s.auth_allow_open_registration = True
    s.expose_api_docs = True
    s.legacy_reads_require_auth = False
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    msg = str(ei.value)
    assert "AUTH_ALLOW_OPEN_REGISTRATION must be false" in msg
    assert "EXPOSE_API_DOCS must be false" in msg
    assert "LEGACY_READS_REQUIRE_AUTH must be true" in msg
    # Three distinct violations should appear as three bullet lines.
    assert len(ei.value.violations) == 3


def test_error_does_not_dump_secrets():
    s = _safe_settings("hosted_public")
    s.auth_allow_open_registration = True
    s.db_password = "super-secret-password"
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    assert "super-secret-password" not in str(ei.value)


# ---------------------------------------------------------------------------
# Integration: create_app() invokes the guard
# ---------------------------------------------------------------------------


def test_create_app_fails_when_hosted_mode_is_unsafe(monkeypatch):
    from app.api import config as config_module
    from app.api.app import create_app

    # Flip the live settings object create_app() reads. Tests run with
    # the local-dev posture (open registration, exposed docs, ...), so
    # switching only the mode is enough to provoke multiple violations.
    monkeypatch.setattr(config_module.settings, "deployment_mode", "hosted_public")
    with pytest.raises(UnsafeDeploymentConfigError):
        create_app()
