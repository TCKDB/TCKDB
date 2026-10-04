"""``tckdb_export_selected_network_kinetics`` tool: serialise one verified network selection.

Wraps ``POST /api/v1/scientific/networks/{network_ref}/kinetics/export-selected``.

A read, not a write: nothing is stored. The tool takes the decision manifest an agent saved from
``tckdb_select_network_kinetics`` (the server's manifest, which this tool cannot download itself), the node it chose
and exactly one representation per member, and returns the server's serialisation, or the server's structured refusal.

Policy choices enforced here (in addition to server-side validation):

- Public refs only. ``network_ref`` must start with ``net_`` and is the only thing in the URL path. Integer-id fields
  are rejected with a teaching error, and so are rule fields: the server never takes a caller-created rule or a verdict
  it should believe.
- Nothing is defaulted. ``allow_administrative_choice`` is forwarded only when the agent gives it; the server's default
  is false, and accepting an administrative choice never bypasses a conflict, an incomplete membership or an
  unsupported serialisation. A report of an export should say whether the choice was administrative.
- The manifest is forwarded exactly as given. The server replays it and re-checks it against its own content, so a stale,
  forged or incomplete manifest is refused there, with its own code; this tool never repairs or refreshes one.
- The server's response is returned unchanged. CHEMKIN output is forward-only and carries no thermodynamics:
  ``assumptions`` and ``provenance`` say so and should be reported with it.
- Arguments sent as null are dropped, never forwarded as JSON null.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

TOOL_NAME = "tckdb_export_selected_network_kinetics"
TOOL_DESCRIPTION = (
    "Serialise one network selection you already made with tckdb_select_network_kinetics, as native JSON or "
    "forward-only CHEMKIN. Requires a public network_ref (starts with 'net_'), the saved decision manifest exactly as "
    "returned by the server, the chosen node_ref and exactly one eligible representation ref per member of that node. "
    "Read-only. The server re-checks the manifest against its own content and refuses a stale, forged or incomplete "
    "one, a node the decision does not allow, any other representation choice and any form it cannot serialise; "
    "report its refusal code verbatim. allow_administrative_choice is false unless you pass it, and even then only an "
    "unranked leading-front choice is allowed: that is not a method claim. Returns the server response unchanged."
)

FORMATS = ("native", "chemkin")
NAMING_POLICIES = ("formula", "public_ref")
PROFILES = ("exploratory", "curated")

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "network_ref", "manifest", "node_ref", "representation_refs", "format", "allow_administrative_choice",
        "energy_units", "naming_policy", "profile",
    }
)
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"network_id", "solve_id", "kinetics_id", "fit_id", "determination_id", "channel_id", "node_id"}
)
_REJECTED_RULE_FIELDS: frozenset[str] = frozenset({"rules", "rule", "rule_id", "verdicts", "assessments", "eligible"})

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["network_ref", "manifest", "node_ref", "representation_refs"],
    "properties": {
        "network_ref": {
            "type": "string",
            "description": "Public network ref. Must start with 'net_'. The only identifier in the URL path.",
            "pattern": "^net_[A-Za-z0-9_-]+$",
            "minLength": 5,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "manifest": {
            "type": "object",
            "description": "The decision manifest from the server's selection, exactly as returned. Never edited.",
        },
        "node_ref": {
            "type": "string",
            "minLength": 1,
            "maxLength": 256,
            "description": "The chosen node: a determination ref, or '<solve ref>/<product set key>' for a bundle.",
        },
        "representation_refs": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10000,
            "items": {"type": "string", "maxLength": PUBLIC_REF_MAX_LENGTH},
            "description": "Exactly one eligible fitted representation (a public ref) per member of the node.",
        },
        "format": {"type": "string", "enum": list(FORMATS), "default": "native"},
        "allow_administrative_choice": {
            "type": "boolean",
            "default": False,
            "description": (
                "Accept exporting one of several unranked leading alternatives. Never bypasses a conflict, an "
                "incomplete membership or an unsupported serialisation."
            ),
        },
        "energy_units": {"type": "string", "description": "CHEMKIN only: cal/mol, kcal/mol, j/mol, kj/mol or k."},
        "naming_policy": {"type": "string", "enum": list(NAMING_POLICIES), "default": "formula"},
        "profile": {"type": "string", "enum": list(PROFILES), "default": "exploratory"},
    },
    "additionalProperties": False,
}


def run(client: TCKDBHttpClient, config: Config, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Validate inputs, POST the export request, return the server response unchanged."""
    args = {k: v for k, v in (arguments or {}).items() if v is not None}

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use network_ref, node_ref and the representation public refs, not integer IDs."
        )
    rejected_rules = sorted(_REJECTED_RULE_FIELDS & args.keys())
    if rejected_rules:
        raise invalid_input(
            f"{rejected_rules!r} is not a request field: the server replays the manifest and applies its own rule "
            "registry, and takes no caller-created rule and no verdict it should believe."
        )
    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    network_ref = validate_path_handle(args.get("network_ref"), field_name="network_ref", expected_prefix="net_")
    manifest = args.get("manifest")
    if not isinstance(manifest, dict) or not manifest:
        raise invalid_input("manifest is required: the decision manifest exactly as the server returned it")
    node_ref = args.get("node_ref")
    if not isinstance(node_ref, str) or not node_ref:
        raise invalid_input("node_ref is required: the chosen determination ref or '<solve ref>/<product set key>'")
    refs = args.get("representation_refs")
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r for r in refs):
        raise invalid_input("representation_refs is required: a non-empty list of public fit refs, one per member")
    if any(r.isdigit() for r in refs):
        raise invalid_input("representation_refs are public refs, not integer IDs")

    body: dict[str, Any] = {"manifest": manifest, "node_ref": node_ref, "representation_refs": list(refs)}
    if "format" in args:
        if args["format"] not in FORMATS:
            raise invalid_input(f"format must be one of {list(FORMATS)!r}; got {args['format']!r}")
        body["format"] = args["format"]
    if "allow_administrative_choice" in args:
        if not isinstance(args["allow_administrative_choice"], bool):
            raise invalid_input(
                f"allow_administrative_choice must be a boolean; got {args['allow_administrative_choice']!r}"
            )
        body["allow_administrative_choice"] = args["allow_administrative_choice"]
    if "energy_units" in args:
        if not isinstance(args["energy_units"], str):
            raise invalid_input(f"energy_units must be a string; got {args['energy_units']!r}")
        body["energy_units"] = args["energy_units"]
    if "naming_policy" in args:
        if args["naming_policy"] not in NAMING_POLICIES:
            raise invalid_input(f"naming_policy must be one of {list(NAMING_POLICIES)!r}; got {args['naming_policy']!r}")
        body["naming_policy"] = args["naming_policy"]

    profile = args.get("profile")
    if profile is not None and profile not in PROFILES:
        raise invalid_input(f"profile must be one of {list(PROFILES)!r}; got {profile!r}")

    url = client.scientific_url(f"/scientific/networks/{quote(network_ref, safe='')}/kinetics/export-selected")
    return client.post_json(url, body, params={"profile": profile})


__all__ = ["TOOL_NAME", "TOOL_DESCRIPTION", "INPUT_SCHEMA", "run"]
