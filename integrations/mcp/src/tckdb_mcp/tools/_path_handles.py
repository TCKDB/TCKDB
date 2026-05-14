"""Shared validation for public refs used in URL paths.

The MCP read tools that inject a user-provided ref into a request path
(``reaction-entries/{rxe_ref}/...``, ``species-entries/{spe_ref}/...``,
``geometries/{geom_ref}``, ...) all run the same defensive checks
before the ref reaches the URL builder:

- must be a non-empty string of the right shape,
- must carry the expected ``<prefix>_`` and a body after it,
- must not contain characters that would change the route or get
  awkwardly URL-encoded.

Each tool keeps its own ``_REJECTED_INTEGER_FIELDS`` set and integer-ID
teaching branch because the rejected fields differ per endpoint and a
generic helper there would obscure intent. Path-handle validation, by
contrast, is identical across endpoints — only the prefix changes.
"""

from __future__ import annotations

from typing import Any

from ..errors import invalid_input

PUBLIC_REF_MAX_LENGTH = 64
"""Mirrors backend ``Path(..., max_length=64)`` on every ``*_ref`` route."""

PATH_UNSAFE_CHARS: frozenset[str] = frozenset("/?#& \t\r\n")
"""Characters explicitly forbidden in ref tokens before URL-quoting.

Belt-and-braces with ``urllib.parse.quote(safe="")`` at the call site;
the whitelist gives the agent a fast, teaching error before any HTTP
attempt rather than letting the server return an opaque 422.
"""


def validate_path_handle(
    value: Any,
    *,
    field_name: str,
    expected_prefix: str,
) -> str:
    """Validate a public ref destined for a URL path.

    Returns the ref unchanged on success. Raises
    :class:`tckdb_mcp.errors.MCPToolError` (via ``invalid_input``) on
    every failure. Checks run in this order so the teaching error the
    agent sees points at the *first* thing that's wrong:

    1. ``None`` → ``"<field> is required"``
    2. non-string → ``"<field> must be a string; got <type>"``
    3. empty string → ``"<field> must not be empty"``
    4. wrong prefix → ``"<field> must start with '<prefix>'; got ..."``
    5. > 64 chars → ``"<field> exceeds 64-char maximum: ..."``
    6. body missing (e.g. bare ``"spe_"``) → ``"... has no body after the '<prefix>' prefix"``
    7. path-unsafe characters → ``"... contains path-unsafe character(s) [...]"``
    """
    if value is None:
        raise invalid_input(f"{field_name} is required")
    if not isinstance(value, str):
        raise invalid_input(
            f"{field_name} must be a string; got {type(value).__name__}"
        )
    if value == "":
        raise invalid_input(f"{field_name} must not be empty")
    if not value.startswith(expected_prefix):
        raise invalid_input(
            f"{field_name} must start with {expected_prefix!r}; got {value!r}"
        )
    if len(value) > PUBLIC_REF_MAX_LENGTH:
        raise invalid_input(
            f"{field_name} exceeds {PUBLIC_REF_MAX_LENGTH}-char maximum: {value!r}"
        )
    if len(value) <= len(expected_prefix):
        raise invalid_input(
            f"{field_name} has no body after the {expected_prefix!r} prefix"
        )
    bad = sorted({c for c in value if c in PATH_UNSAFE_CHARS})
    if bad:
        raise invalid_input(
            f"{field_name} contains path-unsafe character(s) {bad!r}; "
            "public refs are simple opaque tokens."
        )
    return value


__all__ = [
    "PUBLIC_REF_MAX_LENGTH",
    "PATH_UNSAFE_CHARS",
    "validate_path_handle",
]
