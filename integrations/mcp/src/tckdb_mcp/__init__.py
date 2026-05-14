"""Read-only MCP server wrapping the TCKDB scientific HTTP API.

This package is the agent-facing entrypoint to TCKDB. It exposes a closed
set of read-only tools that map 1:1 to ``/api/v1/scientific/*`` endpoints.
It never queries Postgres directly and never imports the backend.

See ``docs/specs/mcp_readonly_integration.md`` for the full design.
"""

__version__ = "0.1.0"
