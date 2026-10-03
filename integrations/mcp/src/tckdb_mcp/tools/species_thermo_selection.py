"""``tckdb_select_species_entry_thermo`` tool: method-aware H298 selection.

Wraps ``POST /api/v1/scientific/species-entries/{species_entry_ref}/thermo/select``.

A read, not a write: nothing is stored and the browse order of
``tckdb_get_species_entry_thermo`` is unchanged. The tool exists so an agent can
ask "which stored record should I use for the gas-phase formation enthalpy at
298.15 K of this species entry, and why", and be told the server's own answer.

Policy choices enforced here (in addition to server-side validation):

- Public refs only. ``species_entry_ref`` must start with ``spe_`` and a
  conformer group is named by ``conformer_group_ref`` (``cg_``). Integer-id
  fields are rejected with a teaching error.
- ``max_candidates`` is rejected: the 500-candidate cap is the server's, and
  above it the server answers ``bounded_search_exceeded``.
- The server's response is returned unchanged. ``outcome``, ``basis`` and
  ``selection`` carry the explanation, so this tool adds no prose of its own
  and does not reduce an outcome to a recommendation: an agent that reports a
  result should quote ``outcome`` and ``basis`` verbatim.
- ``temperature_k`` and ``phase`` are accepted only so that a conflicting
  value reaches the server and is refused with its own code; the tool never
  corrects them.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

TOOL_NAME = "tckdb_select_species_entry_thermo"
TOOL_DESCRIPTION = (
    "Select which stored thermo record of a species_entry to use for the gas-phase "
    "formation enthalpy at 298.15 K, with the reason. Requires a public species_entry_ref "
    "(starts with 'spe_') and a target: equilibrium_ensemble, or single_conformer with a "
    "conformer_group_ref ('cg_'). Read-only. Returns the server response unchanged: report "
    "its 'outcome' and 'basis' verbatim. 'incomparable_alternatives' and 'policy_conflict' "
    "mean nothing was scientifically selected; a 'selection' with administrative=true is a "
    "review/recency choice, not a method claim."
)

POLICIES = ("method_preferred", "default", "most_reviewed", "latest")
RESULT_MODES = ("all", "first")
PROFILES = ("exploratory", "curated")
TARGET_KINDS = ("equilibrium_ensemble", "single_conformer")

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "species_entry_ref",
        "target",
        "policy",
        "result_mode",
        "min_review_status",
        "temperature_k",
        "phase",
        "profile",
    }
)

_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"species_entry_id", "species_id", "conformer_group_id", "thermo_id"}
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["species_entry_ref", "target"],
    "properties": {
        "species_entry_ref": {
            "type": "string",
            "description": "Public species_entry ref. Must start with 'spe_'.",
            "pattern": "^spe_[A-Za-z0-9_-]+$",
            "minLength": 5,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "target": {
            "type": "object",
            "description": (
                "The thermodynamic target. 'equilibrium_ensemble' takes no group; "
                "'single_conformer' requires conformer_group_ref. A mismatch is refused by the server."
            ),
            "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "enum": list(TARGET_KINDS)},
                "conformer_group_ref": {
                    "type": "string",
                    "description": "Public conformer_group ref. Must start with 'cg_'.",
                    "maxLength": PUBLIC_REF_MAX_LENGTH,
                },
            },
            "additionalProperties": False,
        },
        "policy": {
            "type": "string",
            "enum": list(POLICIES),
            "default": "method_preferred",
            "description": (
                "method_preferred applies the registered method rules; default, most_reviewed "
                "and latest apply review/recency order only."
            ),
        },
        "result_mode": {"type": "string", "enum": list(RESULT_MODES), "default": "all"},
        "min_review_status": {
            "type": "string",
            "description": "Optional review floor, applied on top of the profile's floor.",
        },
        "temperature_k": {
            "type": "number",
            "description": "Must be 298.15 if given; any other value is refused by the server.",
        },
        "phase": {
            "type": "string",
            "description": "Must be 'gas' if given; any other value is refused by the server.",
        },
        "profile": {"type": "string", "enum": list(PROFILES), "default": "exploratory"},
    },
    "additionalProperties": False,
}


def run(
    client: TCKDBHttpClient,
    config: Config,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate inputs, POST the selection request, return the server response unchanged."""
    args = dict(arguments or {})

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use species_entry_ref / target.conformer_group_ref public handles, not integer IDs."
        )
    if "max_candidates" in args:
        raise invalid_input(
            "max_candidates is not a request field: the candidate cap is fixed by the server, "
            "which answers 'bounded_search_exceeded' above it."
        )
    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    species_entry_ref = validate_path_handle(
        args.get("species_entry_ref"), field_name="species_entry_ref", expected_prefix="spe_"
    )
    target = _validate_target(args.get("target"))

    body: dict[str, Any] = {"target": target}
    for field_name, allowed in (("policy", POLICIES), ("result_mode", RESULT_MODES)):
        if field_name in args:
            value = args[field_name]
            if value not in allowed:
                raise invalid_input(f"{field_name} must be one of {list(allowed)!r}; got {value!r}")
            body[field_name] = value
    if args.get("min_review_status") is not None:
        if not isinstance(args["min_review_status"], str):
            raise invalid_input(f"min_review_status must be a string; got {args['min_review_status']!r}")
        body["min_review_status"] = args["min_review_status"]
    if args.get("temperature_k") is not None:
        value = args["temperature_k"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise invalid_input(f"temperature_k must be a number; got {value!r}")
        body["temperature_k"] = value
    if args.get("phase") is not None:
        if not isinstance(args["phase"], str):
            raise invalid_input(f"phase must be a string; got {args['phase']!r}")
        body["phase"] = args["phase"]

    profile = args.get("profile")
    if profile is not None and profile not in PROFILES:
        raise invalid_input(f"profile must be one of {list(PROFILES)!r}; got {profile!r}")

    quoted_ref = quote(species_entry_ref, safe="")
    url = client.scientific_url(f"/scientific/species-entries/{quoted_ref}/thermo/select")
    return client.post_json(url, body, params={"profile": profile})


def _validate_target(value: Any) -> dict[str, Any]:
    if value is None:
        raise invalid_input("target is required: {'kind': 'equilibrium_ensemble'} or a single_conformer target")
    if not isinstance(value, dict):
        raise invalid_input(f"target must be an object; got {type(value).__name__}")
    unknown = sorted(value.keys() - {"kind", "conformer_group_ref"})
    if unknown:
        if any(k.endswith("_id") for k in unknown):
            raise invalid_input(
                f"integer-id fields are not accepted by the MCP: {unknown!r}. "
                "Use target.conformer_group_ref, not an integer ID."
            )
        raise invalid_input(f"unknown target field(s): {unknown!r}")
    kind = value.get("kind")
    if kind not in TARGET_KINDS:
        raise invalid_input(f"target.kind must be one of {list(TARGET_KINDS)!r}; got {kind!r}")
    out: dict[str, Any] = {"kind": kind}
    group = value.get("conformer_group_ref")
    if group is not None:
        # Only the shape of the ref is checked here. Whether a group belongs with this kind of
        # target is the server's rule, so the server's own refusal code reaches the agent.
        out["conformer_group_ref"] = validate_path_handle(
            group, field_name="target.conformer_group_ref", expected_prefix="cg_"
        )
    return out


__all__ = ["TOOL_NAME", "TOOL_DESCRIPTION", "INPUT_SCHEMA", "run"]
