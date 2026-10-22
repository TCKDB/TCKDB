"""``tckdb_health`` tool: probe the configured TCKDB API."""

from __future__ import annotations

from typing import Any

from ..errors import invalid_input
from ..http_client import TCKDBHttpClient

TOOL_NAME = "tckdb_health"
TOOL_DESCRIPTION = (
    "Check whether the configured TCKDB API is reachable. Calls GET /health "
    "(root-mounted, outside /api/v1). Returns {'status': 'ok', ...} on "
    "success or raises an MCP error envelope on failure. Takes no arguments."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def run(
    client: TCKDBHttpClient,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the health endpoint and normalize the response."""
    if arguments:
        raise invalid_input(
            f"tckdb_health takes no arguments; got: {sorted(arguments)!r}"
        )
    body = client.get(client.health_url())
    if isinstance(body, dict):
        status = body.get("status", "ok")
        extras = {k: v for k, v in body.items() if k != "status"}
        return {"status": status, **extras}
    return {"status": "ok"}


__all__ = ["TOOL_NAME", "TOOL_DESCRIPTION", "INPUT_SCHEMA", "run"]
