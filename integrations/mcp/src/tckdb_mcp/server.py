"""MCP server entrypoint for the read-only TCKDB integration.

Transport is stdio (MCP default). The ``mcp`` SDK is imported lazily
inside :func:`_run_stdio` so unit tests can exercise tool dispatch
without requiring the SDK to be installed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from .config import Config
from .errors import MCPToolError
from .http_client import TCKDBHttpClient
from .tools import geometry as geometry_tool
from .tools import health as health_tool
from .tools import kinetics_search as kinetics_search_tool
from .tools import reaction_full as reaction_full_tool
from .tools import reaction_kinetics as reaction_kinetics_tool
from .tools import reactions as reactions_tool
from .tools import species as species_tool
from .tools import species_thermo as species_thermo_tool
from .tools import thermo_search as thermo_search_tool

logger = logging.getLogger("tckdb_mcp")


def list_tools_payload() -> list[dict[str, Any]]:
    """Tool catalogue exposed to the MCP host. Closed set."""
    return [
        {
            "name": health_tool.TOOL_NAME,
            "description": health_tool.TOOL_DESCRIPTION,
            "inputSchema": health_tool.INPUT_SCHEMA,
        },
        {
            "name": species_tool.TOOL_NAME,
            "description": species_tool.TOOL_DESCRIPTION,
            "inputSchema": species_tool.INPUT_SCHEMA,
        },
        {
            "name": reactions_tool.TOOL_NAME,
            "description": reactions_tool.TOOL_DESCRIPTION,
            "inputSchema": reactions_tool.INPUT_SCHEMA,
        },
        {
            "name": reaction_kinetics_tool.TOOL_NAME,
            "description": reaction_kinetics_tool.TOOL_DESCRIPTION,
            "inputSchema": reaction_kinetics_tool.INPUT_SCHEMA,
        },
        {
            "name": species_thermo_tool.TOOL_NAME,
            "description": species_thermo_tool.TOOL_DESCRIPTION,
            "inputSchema": species_thermo_tool.INPUT_SCHEMA,
        },
        {
            "name": geometry_tool.TOOL_NAME,
            "description": geometry_tool.TOOL_DESCRIPTION,
            "inputSchema": geometry_tool.INPUT_SCHEMA,
        },
        {
            "name": reaction_full_tool.TOOL_NAME,
            "description": reaction_full_tool.TOOL_DESCRIPTION,
            "inputSchema": reaction_full_tool.INPUT_SCHEMA,
        },
        {
            "name": thermo_search_tool.TOOL_NAME,
            "description": thermo_search_tool.TOOL_DESCRIPTION,
            "inputSchema": thermo_search_tool.INPUT_SCHEMA,
        },
        {
            "name": kinetics_search_tool.TOOL_NAME,
            "description": kinetics_search_tool.TOOL_DESCRIPTION,
            "inputSchema": kinetics_search_tool.INPUT_SCHEMA,
        },
    ]


def dispatch_tool(
    name: str,
    arguments: dict[str, Any] | None,
    client: TCKDBHttpClient,
    config: Config,
) -> dict[str, Any]:
    """Route a tool call to its implementation.

    Returns the tool's JSON-serializable result. Raises
    :class:`MCPToolError` for tool errors; the caller (stdio loop or
    test) is responsible for rendering it.
    """
    if name == health_tool.TOOL_NAME:
        return health_tool.run(client, arguments)
    if name == species_tool.TOOL_NAME:
        return species_tool.run(client, config, arguments)
    if name == reactions_tool.TOOL_NAME:
        return reactions_tool.run(client, config, arguments)
    if name == reaction_kinetics_tool.TOOL_NAME:
        return reaction_kinetics_tool.run(client, config, arguments)
    if name == species_thermo_tool.TOOL_NAME:
        return species_thermo_tool.run(client, config, arguments)
    if name == geometry_tool.TOOL_NAME:
        return geometry_tool.run(client, arguments)
    if name == reaction_full_tool.TOOL_NAME:
        return reaction_full_tool.run(client, arguments)
    if name == thermo_search_tool.TOOL_NAME:
        return thermo_search_tool.run(client, config, arguments)
    if name == kinetics_search_tool.TOOL_NAME:
        return kinetics_search_tool.run(client, config, arguments)
    raise MCPToolError(
        "invalid_input",
        f"unknown tool: {name!r}; "
        f"available: {[t['name'] for t in list_tools_payload()]!r}",
        http_status=422,
    )


def _render_tool_result(result: dict[str, Any] | MCPToolError) -> str:
    if isinstance(result, MCPToolError):
        return json.dumps({"error": result.to_payload()})
    return json.dumps(result)


async def _run_stdio() -> None:
    """Start the MCP server on stdio and serve tools until shutdown."""
    # Lazy imports: keep the ``mcp`` SDK out of the test import path.
    from mcp.server import Server
    import mcp.types as types
    from mcp.server.stdio import stdio_server

    config = Config.from_env()
    client = TCKDBHttpClient(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout_seconds=config.timeout_seconds,
    )

    server = Server("tckdb-mcp")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=entry["name"],
                description=entry["description"],
                inputSchema=entry["inputSchema"],
            )
            for entry in list_tools_payload()
        ]

    @server.call_tool()
    async def _call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        try:
            result = dispatch_tool(name, arguments, client, config)
            payload = _render_tool_result(result)
        except MCPToolError as err:
            payload = _render_tool_result(err)
        return [types.TextContent(type="text", text=payload)]

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        client.close()


def main() -> None:
    """Console-script entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s tckdb-mcp: %(message)s",
    )
    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()


__all__ = ["dispatch_tool", "list_tools_payload", "main"]
