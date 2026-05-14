"""``tckdb_search_reactions`` tool: public, read-only reaction discovery.

Wraps ``POST /api/v1/scientific/reactions/search``. Validates inputs
locally before issuing the HTTP call so agent feedback is fast and the
backend isn't bothered with obviously invalid input.

Policy choices enforced here (in addition to server-side validation):

- ``reaction_id`` / ``reaction_entry_ref`` / ``species_id`` /
  ``species_entry_id`` integer inputs are rejected outright. Agents use
  ``reaction_ref`` / ``reaction_entry_ref`` handles.
- ``include=internal_ids`` is rejected. The MCP never asks for DB ids.
- ``limit`` is capped at ``config.max_limit`` (default 50).
- At least one identity discriminator must be supplied
  (``reactants``, ``products``, ``reaction_ref``, ``reaction_entry_ref``,
  or ``family``). Modifiers alone (``direction``, ``min_review_status``)
  are not searchable on their own.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient

TOOL_NAME = "tckdb_search_reactions"
TOOL_DESCRIPTION = (
    "Search TCKDB reactions and reaction_entries by reactant/product "
    "SMILES, direction, family, or public ref. Read-only. At least one "
    "discriminator (reactants, products, reaction_ref, reaction_entry_ref, "
    "or family) is required. Returns the server search envelope unchanged."
)

# Mirror of the backend's ``_LEGAL_INCLUDE_TOKENS`` for reactions search,
# with ``internal_ids`` deliberately removed. Source:
# backend/app/services/scientific_read/reactions.py.
LEGAL_INCLUDE_TOKENS = frozenset(
    {"kinetics", "transition_states", "species", "review", "all"}
)

# Legal values for the ``direction`` filter. Source:
# backend/app/schemas/reads/scientific_reactions.py::ReactionDirectionQuery.
# (``exact`` is explicitly rejected in v0; the enum does not include it.)
LEGAL_DIRECTIONS = frozenset({"forward", "reverse", "either"})

_DISCRIMINATOR_FIELDS: tuple[str, ...] = (
    "reactants",
    "products",
    "reaction_ref",
    "reaction_entry_ref",
    "family",
)

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "reactants",
        "products",
        "direction",
        "family",
        "reaction_ref",
        "reaction_entry_ref",
        "min_review_status",
        "include_rejected",
        "include_deprecated",
        "offset",
        "limit",
        "include",
        "collapse",
    }
)

# Integer-ID fields the MCP explicitly rejects with a teaching message.
# Includes species_*_id because agents searching reactions by participant
# species would otherwise reach for the int form.
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"reaction_id", "reaction_entry_id", "species_id", "species_entry_id"}
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reactants": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Reactant SMILES list (order preserved).",
        },
        "products": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Product SMILES list (order preserved).",
        },
        "direction": {
            "type": "string",
            "enum": sorted(LEGAL_DIRECTIONS),
            "default": "either",
        },
        "family": {"type": "string"},
        "reaction_ref": {
            "type": "string",
            "description": "Public reaction ref. Must start with 'rxn_'.",
        },
        "reaction_entry_ref": {
            "type": "string",
            "description": "Public reaction_entry ref. Must start with 'rxe_'.",
        },
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
                "Subset of legal reactions-search include tokens. "
                "'internal_ids' is not exposed."
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
    """Validate inputs, POST the search, return the server envelope."""
    args = dict(arguments or {})

    # Integer-ID rejection runs *before* the unknown-field check so the
    # agent gets a teaching error instead of a generic "unknown field".
    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use reaction_ref / reaction_entry_ref public handles, not integer IDs."
        )

    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    reactants = _validate_smiles_list(args.get("reactants"), "reactants")
    products = _validate_smiles_list(args.get("products"), "products")

    has_disc = (
        bool(reactants)
        or bool(products)
        or _present_str(args.get("reaction_ref"))
        or _present_str(args.get("reaction_entry_ref"))
        or _present_str(args.get("family"))
    )
    if not has_disc:
        raise invalid_input(
            "at least one search discriminator must be supplied: "
            f"{list(_DISCRIMINATOR_FIELDS)}"
        )

    reaction_ref = args.get("reaction_ref")
    if reaction_ref is not None:
        if not isinstance(reaction_ref, str) or not reaction_ref.startswith("rxn_"):
            raise invalid_input(
                f"reaction_ref must be a string starting with 'rxn_'; got {reaction_ref!r}"
            )

    reaction_entry_ref = args.get("reaction_entry_ref")
    if reaction_entry_ref is not None:
        if not isinstance(reaction_entry_ref, str) or not reaction_entry_ref.startswith(
            "rxe_"
        ):
            raise invalid_input(
                "reaction_entry_ref must be a string starting with 'rxe_'; "
                f"got {reaction_entry_ref!r}"
            )

    direction = args.get("direction")
    if direction is not None and direction not in LEGAL_DIRECTIONS:
        raise invalid_input(
            f"direction must be one of {sorted(LEGAL_DIRECTIONS)!r}; got {direction!r}"
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

    include_raw = args.get("include")
    if include_raw is None:
        include: list[str] = []
    else:
        if not isinstance(include_raw, list) or not all(
            isinstance(t, str) for t in include_raw
        ):
            raise invalid_input(
                f"include must be a list of strings; got {include_raw!r}"
            )
        include = list(include_raw)

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

    body: dict[str, Any] = {
        "reactants": reactants if reactants else None,
        "products": products if products else None,
        "direction": direction,
        "family": args.get("family"),
        "reaction_ref": reaction_ref,
        "reaction_entry_ref": reaction_entry_ref,
        "min_review_status": args.get("min_review_status"),
        "include_rejected": args.get("include_rejected"),
        "include_deprecated": args.get("include_deprecated"),
        "offset": offset,
        "limit": limit,
        "include": include,
        "collapse": collapse,
    }
    body = {k: v for k, v in body.items() if v is not None}

    url = client.scientific_url("/scientific/reactions/search")
    return client.post_json(url, body)


def _validate_smiles_list(value: Any, field_name: str) -> list[str] | None:
    """Reject malformed reactant/product inputs early; return a normalized list or ``None``."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise invalid_input(
            f"{field_name} must be a list of SMILES strings; got {type(value).__name__}"
        )
    if not value:
        raise invalid_input(
            f"{field_name} must not be an empty list when provided; "
            "omit the field instead."
        )
    if not all(isinstance(s, str) and s for s in value):
        raise invalid_input(
            f"{field_name} must contain non-empty SMILES strings; got {value!r}"
        )
    return list(value)


def _present_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "INPUT_SCHEMA",
    "LEGAL_INCLUDE_TOKENS",
    "LEGAL_DIRECTIONS",
    "run",
]
