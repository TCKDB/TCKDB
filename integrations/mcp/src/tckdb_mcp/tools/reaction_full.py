"""``tckdb_get_reaction_entry_full`` tool: composite reaction-entry read.

Wraps ``GET /api/v1/scientific/reaction-entries/{reaction_entry_ref}/full``.

Fourth and final path-handle tool. The composite read joins species,
kinetics, transition states, calculations, and review summary into one
document. It has no pagination, no collapse, no temperature/pressure
filters — agents either get the resource or they don't.

Policy choices enforced here (in addition to server-side validation):

- ``reaction_entry_id`` / ``reaction_id`` / ``species_id`` /
  ``species_entry_id`` integer inputs are rejected outright. Agents use
  the corresponding ``*_ref`` handles.
- ``include=internal_ids`` is rejected. The MCP never asks for DB ids.
- ``include`` defaults to ``["species", "kinetics", "transition_states"]``
  — the spec-documented default, and the three sub-arrays an agent
  asking "show me everything about this reaction" almost always wants.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import validate_path_handle

TOOL_NAME = "tckdb_get_reaction_entry_full"
TOOL_DESCRIPTION = (
    "Composite scientific read for a single reaction_entry: species, "
    "kinetics, transition_states, and optional calculations / "
    "path_search / irc / scans / conformers / artifacts / review. "
    "Requires a public reaction_entry_ref (starts with 'rxe_'). "
    "Read-only. Returns the server full-response envelope unchanged."
)

# Mirror of the backend's ``_LEGAL_INCLUDE_TOKENS`` for this endpoint,
# with ``internal_ids`` deliberately removed. Source:
# backend/app/services/scientific_read/provenance.py.
LEGAL_INCLUDE_TOKENS = frozenset(
    {
        "species",
        "kinetics",
        "transition_states",
        "calculations",
        "path_search",
        "irc",
        "scans",
        "conformers",
        "artifacts",
        "review",
        "all",
    }
)

# Legal values for the ``include_review`` enum. Source:
# backend/app/schemas/reads/scientific_provenance.py::ReviewDetail.
LEGAL_INCLUDE_REVIEW = frozenset({"summary", "full"})

# Default expansion per docs/specs/mcp_readonly_integration.md §10.8.
# "Show me everything about this reaction" almost always means these
# three sub-arrays.
_DEFAULT_INCLUDE: tuple[str, ...] = ("species", "kinetics", "transition_states")

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "reaction_entry_ref",
        "min_review_status",
        "include_rejected",
        "include_deprecated",
        "include",
        "include_review",
    }
)

# Integer-ID fields the MCP rejects with a teaching error. Includes
# both reaction-side and species-side ids because the composite read
# surfaces species sub-records — an agent might reach for either.
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"reaction_entry_id", "reaction_id", "species_id", "species_entry_id"}
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
            "maxLength": 64,
        },
        "min_review_status": {"type": "string"},
        "include_rejected": {"type": "boolean"},
        "include_deprecated": {"type": "boolean"},
        "include": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": sorted(LEGAL_INCLUDE_TOKENS),
            },
            "description": (
                "Subset of legal reaction-entry-full include tokens. "
                "'internal_ids' is not exposed. Defaults to "
                "['species', 'kinetics', 'transition_states']."
            ),
        },
        "include_review": {
            "type": "string",
            "enum": sorted(LEGAL_INCLUDE_REVIEW),
            "default": "summary",
            "description": (
                "Depth of review metadata per record: 'summary' "
                "(default, counts + status) or 'full' (every review "
                "decision)."
            ),
        },
    },
    "additionalProperties": False,
}


def run(
    client: TCKDBHttpClient,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate inputs, GET the composite read, return the server envelope."""
    args = dict(arguments or {})

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use reaction_entry_ref public handles, not integer IDs."
        )

    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    reaction_entry_ref = validate_path_handle(
        args.get("reaction_entry_ref"),
        field_name="reaction_entry_ref",
        expected_prefix="rxe_",
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

    include_review = args.get("include_review")
    if include_review is not None and include_review not in LEGAL_INCLUDE_REVIEW:
        raise invalid_input(
            f"include_review must be one of {sorted(LEGAL_INCLUDE_REVIEW)!r}; "
            f"got {include_review!r}"
        )

    params: dict[str, Any] = {
        "min_review_status": args.get("min_review_status"),
        "include_rejected": args.get("include_rejected"),
        "include_deprecated": args.get("include_deprecated"),
        "include": include,
        "include_review": include_review,
    }

    quoted_ref = quote(reaction_entry_ref, safe="")
    url = client.scientific_url(
        f"/scientific/reaction-entries/{quoted_ref}/full"
    )
    return client.get(url, params=params)


__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "INPUT_SCHEMA",
    "LEGAL_INCLUDE_TOKENS",
    "LEGAL_INCLUDE_REVIEW",
    "run",
]
