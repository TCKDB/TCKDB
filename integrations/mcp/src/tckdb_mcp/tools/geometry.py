"""``tckdb_get_geometry`` tool: fetch a geometry by public ref.

Wraps ``GET /api/v1/scientific/geometries/{geometry_ref}``.

Third path-handle tool — uses the shared
:func:`tckdb_mcp.tools._path_handles.validate_path_handle` for ref
defense. The endpoint itself is the simplest in the scientific read
surface: one path handle and one optional ``include`` parameter; no
filters, no pagination, no collapse.

Policy choices enforced here (in addition to server-side validation):

- ``geometry_id`` integer inputs are rejected outright. Agents pass
  ``geometry_ref`` (``geom_*``) handles.
- ``include=internal_ids`` is rejected. The MCP never asks for DB ids.
- ``include`` defaults to ``[]`` — geometry payloads already carry the
  coordinate data; provenance/review are opt-in expansions, not the
  agent's default expectation.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

TOOL_NAME = "tckdb_get_geometry"
TOOL_DESCRIPTION = (
    "Fetch a molecular geometry payload by public geometry_ref (starts "
    "with 'geom_'). Read-only. Returns the server geometry envelope "
    "unchanged: symbols + coords plus optional review/provenance "
    "expansion when requested via include."
)

# Mirror of the backend's ``_LEGAL_INCLUDE_TOKENS`` for the geometry
# detail endpoint, with ``internal_ids`` deliberately removed. Source:
# backend/app/services/scientific_read/geometry.py.
LEGAL_INCLUDE_TOKENS = frozenset({"review", "provenance", "all"})

_ACCEPTED_FIELDS: frozenset[str] = frozenset({"geometry_ref", "include"})

# The MCP rejects any integer-id field even though the route accepts
# the integer form in the path. ``geometry_id`` would only arrive as a
# body-style field, not a path; rejecting it explicitly catches the
# common agent slip of "I have a 42, can I use it?".
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset({"geometry_id"})

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["geometry_ref"],
    "properties": {
        "geometry_ref": {
            "type": "string",
            "description": "Public geometry ref. Must start with 'geom_'.",
            "pattern": "^geom_[A-Za-z0-9_-]+$",
            "minLength": 6,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "include": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": sorted(LEGAL_INCLUDE_TOKENS),
            },
            "description": (
                "Subset of legal geometry include tokens. 'internal_ids' "
                "is not exposed. Defaults to []."
            ),
        },
    },
    "additionalProperties": False,
}


def run(
    client: TCKDBHttpClient,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate inputs, GET the geometry, return the server payload."""
    args = dict(arguments or {})

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use geometry_ref public handles, not integer IDs."
        )

    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    geometry_ref = validate_path_handle(
        args.get("geometry_ref"),
        field_name="geometry_ref",
        expected_prefix="geom_",
    )

    if "include" in args:
        include_raw = args.get("include")
        if not isinstance(include_raw, list) or not all(
            isinstance(t, str) for t in include_raw
        ):
            raise invalid_input(
                f"include must be a list of strings; got {include_raw!r}"
            )
        include = list(include_raw)
    else:
        include = []

    if "internal_ids" in include:
        raise invalid_input(
            "include=internal_ids is not exposed by the MCP; the agent-facing "
            "surface never returns integer DB ids."
        )
    illegal = [t for t in include if t not in LEGAL_INCLUDE_TOKENS]
    if illegal:
        raise invalid_input(
            f"unknown include token(s): {illegal!r}. "
            f"Legal tokens: {sorted(LEGAL_INCLUDE_TOKENS)!r}"
        )

    quoted_ref = quote(geometry_ref, safe="")
    url = client.scientific_url(f"/scientific/geometries/{quoted_ref}")
    return client.get(url, params={"include": include})


__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "INPUT_SCHEMA",
    "LEGAL_INCLUDE_TOKENS",
    "run",
]
