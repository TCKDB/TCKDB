"""``tckdb_search_species`` tool: public, read-only species discovery.

Wraps ``GET /api/v1/scientific/species/search``. All validation is
local-first: the tool rejects bad input before issuing an HTTP request
so agent feedback is fast and the backend isn't bothered with obviously
invalid calls.

Policy choices enforced here (in addition to server-side validation):

- ``species_id`` / ``species_entry_id`` integer inputs are rejected
  outright. Agents must use the corresponding ``*_ref`` handles.
- ``include=internal_ids`` is rejected. The MCP never asks for DB ids.
- ``limit`` is capped at ``config.max_limit`` (default 50).
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient

TOOL_NAME = "tckdb_search_species"
TOOL_DESCRIPTION = (
    "Search TCKDB species and species_entries by public identity (SMILES, "
    "InChI, formula, ref). Read-only. At least one identity field is "
    "required. Returns the server search envelope unchanged."
)

# Mirror of the backend's ``_LEGAL_INCLUDE_TOKENS`` for species search,
# with ``internal_ids`` deliberately removed. Source:
# backend/app/services/scientific_read/species.py.
LEGAL_INCLUDE_TOKENS = frozenset(
    {"thermo", "statmech", "transport", "conformers", "review", "all"}
)

_IDENTITY_FIELDS: tuple[str, ...] = (
    "smiles",
    "inchi",
    "inchi_key",
    "formula",
    "species_ref",
    "species_entry_ref",
)

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "smiles",
        "inchi",
        "inchi_key",
        "formula",
        "charge",
        "multiplicity",
        "species_ref",
        "species_entry_ref",
        "electronic_state_kind",
        "species_entry_kind",
        "min_review_status",
        "include_rejected",
        "include_deprecated",
        "offset",
        "limit",
        "include",
        "collapse",
    }
)

# Integer-ID fields the MCP explicitly rejects with a clear message.
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"species_id", "species_entry_id"}
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
        "species_ref": {
            "type": "string",
            "description": "Public species ref. Must start with 'spc_'.",
        },
        "species_entry_ref": {
            "type": "string",
            "description": "Public species_entry ref. Must start with 'spe_'.",
        },
        "electronic_state_kind": {"type": "string"},
        "species_entry_kind": {"type": "string"},
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
                "Subset of legal species-search include tokens. "
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
    """Validate inputs, GET the search, return the server envelope."""
    args = dict(arguments or {})

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}; "
            "use the corresponding *_ref handle instead."
        )

    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    if not any(_present(args.get(f)) for f in _IDENTITY_FIELDS):
        raise invalid_input(
            "at least one identity field must be supplied: "
            f"{list(_IDENTITY_FIELDS)}"
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
        "min_review_status": args.get("min_review_status"),
        "include_rejected": args.get("include_rejected"),
        "include_deprecated": args.get("include_deprecated"),
        "offset": offset,
        "limit": limit,
        "include": include,
        "collapse": collapse,
    }
    body = {k: v for k, v in body.items() if v is not None}

    url = client.scientific_url("/scientific/species/search")
    return client.get(url, params=body)


def _present(value: Any) -> bool:
    """True when ``value`` is supplied in a meaningful way for identity matching."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    return True


__all__ = [
    "TOOL_NAME",
    "TOOL_DESCRIPTION",
    "INPUT_SCHEMA",
    "LEGAL_INCLUDE_TOKENS",
    "run",
]
