"""``tckdb_get_reaction_entry_kinetics`` tool: entry-scoped kinetics read.

Wraps ``GET /api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics``.

This is the first path-handle tool. The reaction-entry ref goes into
the URL path, not the body, so the ref must pass both the public-ref
prefix check **and** a path-safety check before any HTTP call.

Policy choices enforced here (in addition to server-side validation):

- ``reaction_entry_id`` / ``reaction_id`` / ``level_of_theory_id``
  integer inputs are rejected outright. Agents use the corresponding
  ``*_ref`` handles.
- ``include=internal_ids`` is rejected. The MCP never asks for DB ids.
- ``limit`` is capped at ``config.max_limit`` (default 50).
- ``include`` defaults to ``["provenance"]`` — get-by-ref tools are
  cheap and provenance is almost always the agent's next question.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

TOOL_NAME = "tckdb_get_reaction_entry_kinetics"
TOOL_DESCRIPTION = (
    "Fetch kinetics records scoped to a single reaction_entry. Requires "
    "a public reaction_entry_ref (starts with 'rxe_'). Read-only. Returns "
    "the server kinetics envelope unchanged. Provenance is included by "
    "default; pass include=[] to omit."
)

# Mirror of the backend's ``_LEGAL_INCLUDE_TOKENS`` for this endpoint,
# with ``internal_ids`` deliberately removed. Source:
# backend/app/services/scientific_read/kinetics.py.
LEGAL_INCLUDE_TOKENS = frozenset(
    {
        "provenance",
        "calculations",
        "transition_states",
        "path_search",
        "irc",
        "review",
        "artifacts",
        "all",
    }
)

# Default expansion: get-by-ref tools surface provenance because the
# next agent question is almost always "where did this come from?".
_DEFAULT_INCLUDE: tuple[str, ...] = ("provenance",)

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "reaction_entry_ref",
        "temperature_min",
        "temperature_max",
        "pressure",
        "model_kind",
        "level_of_theory_ref",
        "software",
        "min_review_status",
        "include_rejected",
        "include_deprecated",
        "offset",
        "limit",
        "include",
        "collapse",
    }
)

# Integer-ID fields the MCP rejects with a teaching error. Includes
# ``level_of_theory_id`` because the kinetics endpoint accepts it
# server-side, but the MCP exposes only the ``lot_*`` ref form.
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"reaction_entry_id", "reaction_id", "level_of_theory_id"}
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reaction_entry_ref"],
    "properties": {
        "reaction_entry_ref": {
            "type": "string",
            "description": "Public reaction_entry ref. Must start with 'rxe_'.",
            "pattern": "^rxe_[A-Za-z0-9_-]+$",
            "minLength": 5,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "temperature_min": {"type": "number"},
        "temperature_max": {"type": "number"},
        "pressure": {"type": "number"},
        "model_kind": {
            "type": "string",
            "description": (
                "Kinetics model kind (e.g. 'arrhenius', 'troe'). Validated "
                "server-side against KineticsModelKind."
            ),
        },
        "level_of_theory_ref": {
            "type": "string",
            "description": "Level-of-theory public ref. Must start with 'lot_'.",
        },
        "software": {"type": "string"},
        "min_review_status": {"type": "string"},
        "include_rejected": {"type": "boolean"},
        "include_deprecated": {"type": "boolean"},
        "offset": {"type": "integer", "minimum": 0},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "Capped at TCKDB_MCP_MAX_LIMIT (default 50). Defaults to "
                "TCKDB_MCP_DEFAULT_LIMIT (default 25)."
            ),
        },
        "include": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": sorted(LEGAL_INCLUDE_TOKENS),
            },
            "description": (
                "Subset of legal reaction-entry-kinetics include tokens. "
                "'internal_ids' is not exposed. Defaults to ['provenance']."
            ),
        },
        "collapse": {
            "type": "string",
            "enum": ["all", "first"],
            "default": "all",
        },
    },
    "additionalProperties": False,
}


def run(
    client: TCKDBHttpClient,
    config: Config,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate inputs, GET the kinetics, return the server envelope."""
    args = dict(arguments or {})

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use reaction_entry_ref / level_of_theory_ref public handles, "
            "not integer IDs."
        )

    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    reaction_entry_ref = validate_path_handle(
        args.get("reaction_entry_ref"),
        field_name="reaction_entry_ref",
        expected_prefix="rxe_",
    )

    level_of_theory_ref = args.get("level_of_theory_ref")
    if level_of_theory_ref is not None:
        if not isinstance(level_of_theory_ref, str) or not level_of_theory_ref.startswith(
            "lot_"
        ):
            raise invalid_input(
                "level_of_theory_ref must be a string starting with 'lot_'; "
                f"got {level_of_theory_ref!r}"
            )

    offset = args.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise invalid_input(f"offset must be a non-negative integer; got {offset!r}")

    try:
        limit = config.cap_limit(args.get("limit"))
    except ValueError as exc:
        raise invalid_input(str(exc)) from exc

    collapse = args.get("collapse", "all")
    if collapse not in ("all", "first"):
        raise invalid_input(f"collapse must be 'all' or 'first'; got {collapse!r}")

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
        include = list(_DEFAULT_INCLUDE)

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

    if _is_temp(args.get("temperature_min"), "temperature_min"):
        pass
    if _is_temp(args.get("temperature_max"), "temperature_max"):
        pass
    pressure = args.get("pressure")
    if pressure is not None and not isinstance(pressure, (int, float)):
        raise invalid_input(f"pressure must be a number; got {pressure!r}")

    params: dict[str, Any] = {
        "temperature_min": args.get("temperature_min"),
        "temperature_max": args.get("temperature_max"),
        "pressure": args.get("pressure"),
        "model_kind": args.get("model_kind"),
        "level_of_theory_ref": level_of_theory_ref,
        "software": args.get("software"),
        "min_review_status": args.get("min_review_status"),
        "include_rejected": args.get("include_rejected"),
        "include_deprecated": args.get("include_deprecated"),
        "offset": offset,
        "limit": limit,
        "include": include,
        "collapse": collapse,
    }

    # Quote defensively even though the path-safety check already
    # rejected the problematic chars — belt and braces against future
    # ref-format changes.
    quoted_ref = quote(reaction_entry_ref, safe="")
    url = client.scientific_url(
        f"/scientific/reaction-entries/{quoted_ref}/kinetics"
    )
    return client.get(url, params=params)


def _is_temp(value: Any, field_name: str) -> bool:
    """Side-effect: raise ``invalid_input`` if the temperature value is not numeric."""
    if value is None:
        return False
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise invalid_input(f"{field_name} must be a number; got {value!r}")
    return True


__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "INPUT_SCHEMA",
    "LEGAL_INCLUDE_TOKENS",
    "run",
]
