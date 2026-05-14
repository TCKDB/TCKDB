"""``tckdb_search_kinetics`` tool: chemistry-first kinetics discovery.

Wraps ``POST /api/v1/scientific/kinetics/search``. Complements
:mod:`tckdb_mcp.tools.reaction_kinetics` — the entry-scoped tool requires
the agent to already know an ``rxe_*`` ref, this tool lets the agent
start from reactant/product chemistry (or a family) and have the
backend resolve reaction/reaction_entry identity.

Final tool in the read-only MVP. Mirrors the shape of
``tckdb_search_reactions`` (reaction-side discriminators, direction
modifier) and ``tckdb_search_thermo`` (temperature filter, model_kind
enum, level_of_theory ref, conservative ``["provenance"]`` default).

Policy choices enforced here (in addition to server-side validation):

- ``reaction_id`` / ``reaction_entry_id`` / ``species_id`` /
  ``species_entry_id`` / ``kinetics_id`` / ``level_of_theory_id`` /
  ``calculation_id`` integer inputs are rejected outright. Agents use
  the corresponding ``*_ref`` handles.
- ``include=internal_ids`` is rejected.
- ``limit`` is capped at ``config.max_limit`` (default 50).
- At least one identity discriminator must be supplied
  (``reactants``, ``products``, ``reaction_ref``, ``reaction_entry_ref``,
  or ``family``). ``direction`` alone is a modifier, not a search.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient

TOOL_NAME = "tckdb_search_kinetics"
TOOL_DESCRIPTION = (
    "Chemistry-first kinetics search. Find kinetics records by "
    "reactant/product SMILES, reaction family, or public ref. Read-only. "
    "At least one discriminator (reactants, products, reaction_ref, "
    "reaction_entry_ref, or family) is required. Returns the server "
    "search envelope unchanged."
)

# Mirror of the backend's ``_LEGAL_INCLUDE_TOKENS`` for kinetics_search,
# with ``internal_ids`` removed. Source:
# backend/app/services/scientific_read/kinetics_search.py.
LEGAL_INCLUDE_TOKENS = frozenset(
    {
        "provenance",
        "calculations",
        "artifacts",
        "review",
        "species",
        "transition_states",
        "path_search",
        "irc",
        "all",
    }
)

# Legal ``model_kind`` values from
# backend/app/db/models/common.py::KineticsModelKind.
LEGAL_MODEL_KINDS = frozenset({"arrhenius", "modified_arrhenius"})

# Legal ``direction`` values from
# backend/app/schemas/reads/scientific_reactions.py::ReactionDirectionQuery.
LEGAL_DIRECTIONS = frozenset({"forward", "reverse", "either"})

_DEFAULT_INCLUDE: tuple[str, ...] = ("provenance",)

_DISCRIMINATOR_FIELDS: tuple[str, ...] = (
    "reactants",
    "products",
    "reaction_ref",
    "reaction_entry_ref",
    "family",
)

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        # Reaction identity
        "reactants",
        "products",
        "direction",
        "family",
        "reaction_ref",
        "reaction_entry_ref",
        # Kinetics-specific filters
        "temperature_min",
        "temperature_max",
        "pressure",
        "model_kind",
        "level_of_theory_ref",
        "software",
        # Review filters
        "min_review_status",
        "include_rejected",
        "include_deprecated",
        # Pagination / framing
        "offset",
        "limit",
        "include",
        "collapse",
    }
)

# Integer-ID fields the MCP rejects with a teaching error. Covers
# reaction-side, species-side (composite endpoint surfaces species
# context), kinetics-side, lot-side, and calc-side ints.
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {
        "reaction_id",
        "reaction_entry_id",
        "species_id",
        "species_entry_id",
        "kinetics_id",
        "level_of_theory_id",
        "calculation_id",
    }
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
            "description": "Modifier, not a discriminator on its own.",
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
        "temperature_min": {"type": "number", "exclusiveMinimum": 0},
        "temperature_max": {"type": "number", "exclusiveMinimum": 0},
        "pressure": {"type": "number", "exclusiveMinimum": 0},
        "model_kind": {
            "type": "string",
            "enum": sorted(LEGAL_MODEL_KINDS),
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
                "Subset of legal kinetics-search include tokens. "
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
    """Validate inputs, POST the kinetics search, return the server envelope."""
    args = dict(arguments or {})

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use public refs such as reaction_ref, reaction_entry_ref, or "
            "level_of_theory_ref, not integer IDs."
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
            "Provide at least one kinetics search discriminator such as "
            "reactants/products, reaction_ref, reaction_entry_ref, or family."
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

    level_of_theory_ref = args.get("level_of_theory_ref")
    if level_of_theory_ref is not None:
        if not isinstance(level_of_theory_ref, str) or not level_of_theory_ref.startswith(
            "lot_"
        ):
            raise invalid_input(
                "level_of_theory_ref must be a string starting with 'lot_'; "
                f"got {level_of_theory_ref!r}"
            )

    direction = args.get("direction")
    if direction is not None and direction not in LEGAL_DIRECTIONS:
        raise invalid_input(
            f"direction must be one of {sorted(LEGAL_DIRECTIONS)!r}; got {direction!r}"
        )

    model_kind = args.get("model_kind")
    if model_kind is not None and model_kind not in LEGAL_MODEL_KINDS:
        raise invalid_input(
            f"model_kind must be one of {sorted(LEGAL_MODEL_KINDS)!r}; got {model_kind!r}"
        )

    tmin = _validate_positive_number(args.get("temperature_min"), "temperature_min")
    tmax = _validate_positive_number(args.get("temperature_max"), "temperature_max")
    if tmin is not None and tmax is not None and tmin > tmax:
        raise invalid_input(
            f"temperature_min ({tmin}) must be <= temperature_max ({tmax})"
        )
    pressure = _validate_positive_number(args.get("pressure"), "pressure")

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

    body: dict[str, Any] = {
        "reactants": reactants if reactants else None,
        "products": products if products else None,
        "direction": direction,
        "family": args.get("family"),
        "reaction_ref": reaction_ref,
        "reaction_entry_ref": reaction_entry_ref,
        "temperature_min": tmin,
        "temperature_max": tmax,
        "pressure": pressure,
        "model_kind": model_kind,
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
    body = {k: v for k, v in body.items() if v is not None}

    url = client.scientific_url("/scientific/kinetics/search")
    return client.post_json(url, body)


def _validate_smiles_list(value: Any, field_name: str) -> list[str] | None:
    """Reject malformed reactant/product inputs early."""
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


def _validate_positive_number(value: Any, field_name: str) -> float | None:
    """Numeric (not bool) and > 0. Returns the value as float or ``None``."""
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise invalid_input(f"{field_name} must be a number; got {value!r}")
    if value <= 0:
        raise invalid_input(
            f"{field_name} must be > 0 (scientific quantities are positive); "
            f"got {value!r}"
        )
    return float(value)


def _present_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "INPUT_SCHEMA",
    "LEGAL_INCLUDE_TOKENS",
    "LEGAL_MODEL_KINDS",
    "LEGAL_DIRECTIONS",
    "run",
]
