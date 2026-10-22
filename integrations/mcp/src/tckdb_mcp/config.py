"""Process-level configuration loaded from environment variables.

Constructed once via :meth:`Config.from_env` at server startup. Tests
build fresh instances rather than mutating globals — `Config` is frozen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_BASE_URL = "http://127.0.0.1:8010/api/v1"
DEFAULT_DEFAULT_LIMIT = 25
DEFAULT_MAX_LIMIT = 50
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Config:
    """Resolved MCP runtime configuration.

    The API key is held only in memory and never echoed into logs or
    tool outputs. The ``cap_limit`` helper enforces the abuse-control
    cap on every tool call so the bound is in one place.
    """

    base_url: str
    api_key: str | None
    default_limit: int
    max_limit: int
    timeout_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        """Resolve configuration from ``env`` (or ``os.environ``).

        Raises ``ValueError`` on malformed numeric env values or on
        ``default_limit > max_limit``.
        """
        source: Mapping[str, str] = env if env is not None else os.environ

        raw_base_url = source.get("TCKDB_BASE_URL")
        base_url = (raw_base_url if raw_base_url else DEFAULT_BASE_URL).rstrip("/")

        raw_api_key = source.get("TCKDB_API_KEY")
        api_key = raw_api_key if raw_api_key else None

        default_limit = _read_int(source, "TCKDB_MCP_DEFAULT_LIMIT", DEFAULT_DEFAULT_LIMIT)
        max_limit = _read_int(source, "TCKDB_MCP_MAX_LIMIT", DEFAULT_MAX_LIMIT)
        timeout_seconds = _read_float(
            source, "TCKDB_MCP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        )

        if default_limit < 1:
            raise ValueError("TCKDB_MCP_DEFAULT_LIMIT must be >= 1")
        if max_limit < 1:
            raise ValueError("TCKDB_MCP_MAX_LIMIT must be >= 1")
        if max_limit < default_limit:
            raise ValueError(
                "TCKDB_MCP_MAX_LIMIT must be >= TCKDB_MCP_DEFAULT_LIMIT "
                f"(got default={default_limit}, max={max_limit})"
            )
        if timeout_seconds <= 0:
            raise ValueError("TCKDB_MCP_TIMEOUT_SECONDS must be > 0")

        return cls(
            base_url=base_url,
            api_key=api_key,
            default_limit=default_limit,
            max_limit=max_limit,
            timeout_seconds=timeout_seconds,
        )

    def cap_limit(self, requested: int | None) -> int:
        """Return the effective ``limit``: default if unset, else min(requested, max)."""
        if requested is None:
            return self.default_limit
        if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
            raise ValueError(f"limit must be a positive integer; got {requested!r}")
        return min(requested, self.max_limit)


def _read_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer; got {raw!r}") from exc


def _read_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number; got {raw!r}") from exc


__all__ = [
    "Config",
    "DEFAULT_BASE_URL",
    "DEFAULT_DEFAULT_LIMIT",
    "DEFAULT_MAX_LIMIT",
    "DEFAULT_TIMEOUT_SECONDS",
]
