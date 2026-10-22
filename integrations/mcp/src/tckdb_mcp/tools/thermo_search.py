"""``tckdb_search_thermo`` tool: chemistry-first thermo discovery.

Wraps ``POST /api/v1/scientific/thermo/search``. Complements
:mod:`tckdb_mcp.tools.species_thermo` — the entry-scoped tool requires
the agent to already know an ``spe_*`` ref, this tool lets the agent
start from chemistry (SMILES, InChI, formula) and have the backend
resolve the species/species_entry identity.

Policy choices enforced here (in addition to server-side validation):

- ``species_id`` / ``species_entry_id`` / ``thermo_id`` /
  ``level_of_theory_id`` / ``calculation_id`` integer inputs are
  rejected outright. Agents use the corresponding ``*_ref`` handles.
- ``include=internal_ids`` is rejected.
- ``limit`` is capped at ``config.max_limit`` (default 50).
- At least one identity discriminator must be supplied
  (``smiles``, ``inchi``, ``inchi_key``, ``formula``, ``species_ref``,
  or ``species_entry_ref``). Modifier-only requests would fan out into
  unbounded scans server-side; reject locally.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient

TOOL_NAME = "tckdb_search_thermo"
TOOL_DESCRIPTION = (
    "Chemistry-first thermo search. Find thermo records by species "
    "identity (SMILES, InChI, formula) or by public ref. Read-only. At "
    "least one identity discriminator is required. Returns the server "
    "search envelope unchanged."
)

# Mirror of the backend's ``_LEGAL_INCLUDE_TOKENS`` for thermo_search,
# with ``internal_ids`` removed. Source:
# backend/app/services/scientific_read/thermo_search.py.
LEGAL_INCLUDE_TOKENS = frozenset(
    {"provenance", "calculations", "artifacts", "review", "all"}
)

# Legal ``model_kind`` values from
# backend/app/schemas/reads/scientific_thermo.py::ThermoModelKindQuery.
LEGAL_MODEL_KINDS = frozenset({"nasa", "points", "scalar"})

# Default expansion: search tools surface ~one provenance per record;
# the entry-scoped thermo tool uses the same default. Matches the
# spec-recommended posture for thermo reads.
_DEFAULT_INCLUDE: tuple[str, ...] = ("provenance",)

_IDENTITY_DISCRIMINATORS: tuple[str, ...] = (
    "smiles",
    "inchi",
    "inchi_key",
    "formula",
    "species_ref",
    "species_entry_ref",
)

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        # Species identity
        "smiles",
        "inchi",
        "inchi_key",
        "formula",
        "charge",
        "multiplicity",
        "electronic_state_kind",
        "species_entry_kind",
        "species_ref",
        "species_entry_ref",
        # Thermo-specific filters
        "temperature_min",
        "temperature_max",
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

# Integer-ID fields the MCP rejects with a teaching error. Covers the
# species-side, thermo-side, lot-side, and calc-side ints an agent
# could plausibly reach for.
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {
        "species_id",
        "species_entry_id",
        "thermo_id",
        "level_of_theory_id",
        "calculation_id",
    }
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "smiles": {"type": "string", "description": "Species SMILES."},
        "inchi": {"type": "string"},
        "inchi_key": {"type": "string"},
        "formula": {"type": "string"},
        "charge": {"type": "integer"},
        "multiplicity": {"type": "integer", "minimum": 1},
        "electronic_state_kind": {"type": "string"},
        "species_entry_kind": {"type": "string"},
        "species_ref": {
            "type": "string",
            "description": "Public species ref. Must start with 'spc_'.",
        },
        "species_entry_ref": {
            "type": "string",
            "description": "Public species_entry ref. Must start with 'spe_'.",
        },
        "temperature_min": {"type": "number", "exclusiveMinimum": 0},
        "temperature_max": {"type": "number", "exclusiveMinimum": 0},
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
                "Subset of legal thermo-search include tokens. "
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
    """Validate inputs, POST the thermo search, return the server envelope."""
    args = dict(arguments or {})

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use public refs such as species_ref, species_entry_ref, or "
            "level_of_theory_ref, not integer IDs."
        )

    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    if not any(_present(args.get(f)) for f in _IDENTITY_DISCRIMINATORS):
        raise invalid_input(
            "Provide at least one thermo search discriminator such as "
            "smiles, inchi, inchi_key, formula, species_ref, or "
            "species_entry_ref."
        )

    species_ref = args.get("species_ref")
    if species_ref is not None:
        if not isinstance(species_ref, str) or not species_ref.startswith("spc_"):
            raise invalid_input(
                f"species_ref must be a string starting with 'spc_'; got {species_ref!r}"
            )

    species_entry_ref = args.get("species_entry_ref")
    if species_entry_ref is not None:
        if not isinstance(species_entry_ref, str) or not species_entry_ref.startswith(
            "spe_"
        ):
            raise invalid_input(
                "species_entry_ref must be a string starting with 'spe_'; "
                f"got {species_entry_ref!r}"
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

    model_kind = args.get("model_kind")
    if model_kind is not None and model_kind not in LEGAL_MODEL_KINDS:
        raise invalid_input(
            f"model_kind must be one of {sorted(LEGAL_MODEL_KINDS)!r}; got {model_kind!r}"
        )

    tmin = _validate_temperature(args.get("temperature_min"), "temperature_min")
    tmax = _validate_temperature(args.get("temperature_max"), "temperature_max")
    if tmin is not None and tmax is not None and tmin > tmax:
        raise invalid_input(
            f"temperature_min ({tmin}) must be <= temperature_max ({tmax})"
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

    body: dict[str, Any] = {
        "smiles": args.get("smiles"),
        "inchi": args.get("inchi"),
        "inchi_key": args.get("inchi_key"),
        "formula": args.get("formula"),
        "charge": args.get("charge"),
        "multiplicity": args.get("multiplicity"),
        "electronic_state_kind": args.get("electronic_state_kind"),
        "species_entry_kind": args.get("species_entry_kind"),
        "species_ref": species_ref,
        "species_entry_ref": species_entry_ref,
        "temperature_min": tmin,
        "temperature_max": tmax,
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

    url = client.scientific_url("/scientific/thermo/search")
    return client.post_json(url, body)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    return True


def _validate_temperature(value: Any, field_name: str) -> float | None:
    """Numeric (not bool) and positive (> 0 K). Returns the value or ``None``."""
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise invalid_input(f"{field_name} must be a number; got {value!r}")
    if value <= 0:
        raise invalid_input(
            f"{field_name} must be > 0 K (Kelvin temperatures are positive); "
            f"got {value!r}"
        )
    return float(value)


__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "INPUT_SCHEMA",
    "LEGAL_INCLUDE_TOKENS",
    "LEGAL_MODEL_KINDS",
    "run",
]
