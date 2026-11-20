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


@pytest.fixture(autouse=True)
def _real_object_store_keys(monkeypatch):
    """Pin the S3 keys the guard reads, so the ambient test env cannot decide a case."""
    from app.services import artifact_storage

    monkeypatch.setattr(artifact_storage, "S3_ACCESS_KEY", "tckdb-test-access")
    monkeypatch.setattr(artifact_storage, "S3_SECRET_KEY", "tckdb-test-secret")


def _safe_settings(mode: str) -> Settings:
    """Return a Settings instance configured for hosted-safe operation."""
    return Settings(
        deployment_mode=mode,  # type: ignore[arg-type]
        db_password="a-real-runtime-password",
        auth_allow_open_registration=False,
        expose_api_docs=False,
        expose_api_reference=True,  # hosted mode must ALLOW this; it is the whole
                                     # reason it is a separate flag from EXPOSE_API_DOCS.
                                     # Was pinned by tests/api/test_landing_page.py:2003
                                     # before that module was deleted.
        legacy_reads_require_auth=True,
        session_cookie_secure=True,
        allow_public_internal_ids=False,
        rate_limit_enabled=True,
        paging_cursor_key="k" * 40,
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
    # Upgrading must not make an existing deployment more exposed: the
    # reference-only surface defaults off, same as every other opt-in.
    assert s.expose_api_reference is False
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
        _UnsafeCase({"paging_cursor_key": None}, "PAGING_CURSOR_KEY must be set"),
        _UnsafeCase({"paging_cursor_key": "too-short"}, "PAGING_CURSOR_KEY must be set"),
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


def test_shared_private_allows_plain_http_cookies_only_when_opted_in():
    s = _safe_settings("shared_private")
    s.session_cookie_secure = False
    s.session_cookie_allow_plain_http = True
    validate_deployment_safety(s)  # does not raise


def test_hosted_public_ignores_the_plain_http_opt_in():
    s = _safe_settings("hosted_public")
    s.session_cookie_secure = False
    s.session_cookie_allow_plain_http = True
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    assert ei.value.violations == ["SESSION_COOKIE_SECURE must be true"]


# ---------------------------------------------------------------------------
# Secrets still at a template placeholder, or empty
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["shared_private", "hosted_public"])
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DB_PASSWORD", "change-me-strong-runtime-db-password"),
        ("DB_PASSWORD", ""),
        ("S3_ACCESS_KEY", "change-me-s3-access-key"),
        ("S3_ACCESS_KEY", ""),
        ("S3_SECRET_KEY", "change-me-s3-secret-key"),
        ("S3_SECRET_KEY", "  "),
        ("PAGING_CURSOR_KEY", "change-me-a-paging-cursor-key-of-40-chars"),
    ],
)
def test_hosted_modes_refuse_placeholder_secrets(monkeypatch, mode, name, value):
    from app.services import artifact_storage

    s = _safe_settings(mode)
    if name == "DB_PASSWORD":
        s.db_password = value
    elif name == "PAGING_CURSOR_KEY":
        s.paging_cursor_key = value
    else:
        monkeypatch.setattr(artifact_storage, name, value)
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    assert len(ei.value.violations) == 1, ei.value.violations
    assert ei.value.violations[0].startswith(f"{name} ")
    if value:
        # The placeholder is named by its prefix, never echoed in full.
        assert value not in str(ei.value)


def test_local_mode_ignores_placeholder_secrets(monkeypatch):
    from app.services import artifact_storage

    monkeypatch.setattr(artifact_storage, "S3_SECRET_KEY", "change-me-s3-secret-key")
    validate_deployment_safety(Settings(deployment_mode="local", db_password="change-me"))


def test_placeholder_violations_are_listed_together(monkeypatch):
    from app.services import artifact_storage

    monkeypatch.setattr(artifact_storage, "S3_ACCESS_KEY", "change-me-s3-access-key")
    monkeypatch.setattr(artifact_storage, "S3_SECRET_KEY", "change-me-s3-secret-key")
    s = _safe_settings("shared_private")
    s.db_password = "change-me-strong-runtime-db-password"
    with pytest.raises(UnsafeDeploymentConfigError) as ei:
        validate_deployment_safety(s)
    assert [v.split()[0] for v in ei.value.violations] == ["DB_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"]


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
        _UnsafeCase({"paging_cursor_key": None}, "PAGING_CURSOR_KEY must be set"),
        _UnsafeCase({"paging_cursor_key": "too-short"}, "PAGING_CURSOR_KEY must be set"),
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


def test_the_reference_is_allowed_in_hosted_mode():
    """``EXPOSE_API_REFERENCE=true`` is a hosted-safe posture, not a
    violation. It exists precisely so a hosted deployment can publish
    ReDoc/OpenAPI (self-contained, no request console) without also
    turning on Swagger UI's live "Try it out" against a public
    instance -- that is the boot-refusing case (``EXPOSE_API_DOCS``).
    Was ``tests/api/test_landing_page.py::test_the_reference_is_allowed_in_hosted_mode``
    before that module was deleted; ported here so the guard's whole
    reason for having two separate doc-exposure flags stays pinned.
    """
    s = _safe_settings("hosted_public")
    assert s.expose_api_reference is True
    validate_deployment_safety(s)  # does not raise


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
