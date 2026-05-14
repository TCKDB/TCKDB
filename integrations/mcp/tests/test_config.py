"""Tests for ``tckdb_mcp.config``."""

from __future__ import annotations

import pytest

from tckdb_mcp.config import (
    DEFAULT_BASE_URL,
    DEFAULT_DEFAULT_LIMIT,
    DEFAULT_MAX_LIMIT,
    DEFAULT_TIMEOUT_SECONDS,
    Config,
)


def test_defaults_with_empty_env() -> None:
    cfg = Config.from_env(env={})
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.api_key is None
    assert cfg.default_limit == DEFAULT_DEFAULT_LIMIT
    assert cfg.max_limit == DEFAULT_MAX_LIMIT
    assert cfg.timeout_seconds == DEFAULT_TIMEOUT_SECONDS


def test_api_key_is_optional() -> None:
    cfg = Config.from_env(env={})
    assert cfg.api_key is None

    cfg_with = Config.from_env(env={"TCKDB_API_KEY": "tck_abc"})
    assert cfg_with.api_key == "tck_abc"


def test_empty_api_key_is_treated_as_unset() -> None:
    """An empty-string env value is the same as 'unset'."""
    cfg = Config.from_env(env={"TCKDB_API_KEY": ""})
    assert cfg.api_key is None


def test_trailing_slash_stripped_from_base_url() -> None:
    cfg = Config.from_env(env={"TCKDB_BASE_URL": "http://host/api/v1/"})
    assert cfg.base_url == "http://host/api/v1"


def test_custom_limits_honored() -> None:
    cfg = Config.from_env(
        env={"TCKDB_MCP_DEFAULT_LIMIT": "10", "TCKDB_MCP_MAX_LIMIT": "75"}
    )
    assert cfg.default_limit == 10
    assert cfg.max_limit == 75


def test_default_limit_must_not_exceed_max_limit() -> None:
    with pytest.raises(ValueError, match="MAX_LIMIT must be >= "):
        Config.from_env(
            env={"TCKDB_MCP_DEFAULT_LIMIT": "100", "TCKDB_MCP_MAX_LIMIT": "50"}
        )


def test_non_integer_limit_raises() -> None:
    with pytest.raises(ValueError, match="must be an integer"):
        Config.from_env(env={"TCKDB_MCP_DEFAULT_LIMIT": "ten"})


def test_non_positive_timeout_raises() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        Config.from_env(env={"TCKDB_MCP_TIMEOUT_SECONDS": "0"})


def test_cap_limit_uses_default_when_unset() -> None:
    cfg = Config.from_env(env={})
    assert cfg.cap_limit(None) == DEFAULT_DEFAULT_LIMIT


def test_cap_limit_caps_at_max_limit() -> None:
    cfg = Config.from_env(env={})
    assert cfg.cap_limit(10_000) == DEFAULT_MAX_LIMIT
    assert cfg.cap_limit(5) == 5


def test_cap_limit_rejects_zero_and_negative() -> None:
    cfg = Config.from_env(env={})
    with pytest.raises(ValueError):
        cfg.cap_limit(0)
    with pytest.raises(ValueError):
        cfg.cap_limit(-1)


def test_cap_limit_rejects_bool() -> None:
    """Booleans are ints in Python; the cap must not accept them."""
    cfg = Config.from_env(env={})
    with pytest.raises(ValueError):
        cfg.cap_limit(True)  # type: ignore[arg-type]
