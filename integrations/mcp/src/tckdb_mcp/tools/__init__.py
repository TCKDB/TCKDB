"""MCP tool implementations.

Each tool module exposes:

- ``TOOL_NAME`` — agent-facing identifier
- ``TOOL_DESCRIPTION`` — short help string
- ``INPUT_SCHEMA`` — JSON-schema description of accepted arguments
- ``run(...)`` — pure function that validates inputs, calls the HTTP
  wrapper, and returns a JSON-serializable result

Tools never import the ``mcp`` SDK; transport details live in
``tckdb_mcp.server``.
"""
