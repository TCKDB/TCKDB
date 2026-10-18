"""Relative conformer energy tools: ``tckdb_species_entry_relative_conformer_energies`` and
``tckdb_compare_relative_conformer_energies``.

They wrap ``POST /api/v1/scientific/species-entries/{spe_ref}/conformers/relative-energies`` and ``.../compare``.

A read, not a write: nothing is stored. The first answers "how much higher or lower in energy is each conformer group of
this species entry than the conformer I name, at this level". The second describes how two stored protocols differ on
those relative energies, against one named reference conformer.

Policy choices enforced here (in addition to server-side validation):

- The scientific choices are never defaulted by the tool: the reference conformer, the energy kind, the certificate and
  the level (exactly one of a ``lot_`` and a ``csch_`` ref) are required, and the comparison also requires both
  protocols and the pairing. Whatever else the agent states is forwarded; the rest is the server's default.
- Public refs only, each with its prefix checked before any request: ``spe_`` entry, ``cg_`` conformer groups, ``lot_``
  level of theory, ``csch_`` composite scheme. Integer ids are rejected.
- ``bounds``, ``limit``, ``offset``, pagination, ``sort`` and ``rules`` are rejected: the bounds are the server's, and the
  order is by conformer ref and is not a ranking.
- The server's response is returned unchanged. ``outcome``, ``basis`` and ``claims.statement`` carry the explanation, so
  these tools add no prose of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

PROFILES = ("exploratory", "curated")
ENERGY_KINDS = ("electronic_energy", "zero_kelvin_energy")
CERTIFICATES = ("local_minimum_any_surface", "local_minimum_on_energy_surface")
PAIRINGS = ("same_geometry", "same_basin")
COVERAGE = ("known_values", "all_requested_members")
QUALITIES = ("raw", "curated", "rejected")
REPEAT_POLICIES = ("retain_alternates", "administrative_representative")

_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"species_entry_id", "conformer_group_id", "level_of_theory_id", "composite_scheme_id", "species_id"}
)
_REJECTED_BOUND_FIELDS: frozenset[str] = frozenset(
    {"bounds", "max_candidates", "manifest_bytes", "limit", "offset", "page", "page_size", "cursor", "sort", "rules", "rank"}
)


@dataclass(frozen=True)
class RelativeEnergyTool:
    name: str
    description: str
    operation: str  # path suffix after /relative-energies
    compare: bool


_COMMON_DESCRIPTION = (
    "Read-only; nothing is stored. Requires a public species_entry_ref ('spe_'). The reference conformer "
    "(reference_conformer_ref, a 'cg_' ref), energy_kind, certificate and the level have no default and must all be stated. "
    "Returns the server response unchanged: quote 'outcome', 'basis' and 'claims.statement' (where present) verbatim, and "
    "never reword them. Members are ordered by conformer ref and the order is not a ranking; a member with no single "
    "value is absent or unresolved with a coded reason, never zero. The answer is conditional on the authorized "
    "population the caller can see. A population over the server's bounds is a coded 422, and enthalpy and Gibbs energy "
    "are refused as unsupported."
)

TOOLS: dict[str, RelativeEnergyTool] = {
    "tckdb_species_entry_relative_conformer_energies": RelativeEnergyTool(
        name="tckdb_species_entry_relative_conformer_energies",
        description=(
            "How much higher or lower in energy each conformer group of one species entry is than the conformer you name, "
            "at one level (a 'lot_' level of theory or a 'csch_' composite scheme, exactly one). Relative energy is the "
            "member minus the reference: negative is below it. Each member needs the stated curvature certificate. " + _COMMON_DESCRIPTION
        ),
        operation="",
        compare=False,
    ),
    "tckdb_compare_relative_conformer_energies": RelativeEnergyTool(
        name="tckdb_compare_relative_conformer_energies",
        description=(
            "Describe how two stored protocols (a candidate and a reference, each a level and optional recipe) differ in "
            "the relative conformer energies of one species entry, against one named reference conformer. Deviations are "
            "candidate minus reference. This is DESCRIPTIVE ONLY: it names no better protocol, ranks nothing and is not "
            "a benchmark verdict; claims.missing_evidence lists what a superiority claim would need. Every deviation "
            "depends on the reference conformer named: a different reference conformer gives different numbers. "
            "'pairing' (same_geometry or same_basin) is required. " + _COMMON_DESCRIPTION
        ),
        operation="/compare",
        compare=True,
    ),
}

TOOL_NAMES = frozenset(TOOLS)


def _ref_property(prefix: str, what: str) -> dict[str, Any]:
    return {
        "type": "string",
        "description": f"{what} Public ref. Must start with '{prefix}'.",
        "pattern": f"^{prefix}[A-Za-z0-9_-]+$",
        "minLength": len(prefix) + 1,
        "maxLength": PUBLIC_REF_MAX_LENGTH,
    }


_LEVEL_PROPERTIES: dict[str, Any] = {
    "level_of_theory_ref": _ref_property("lot_", "The level of theory compared."),
    "composite_scheme_ref": _ref_property("csch_", "The composite scheme compared (use this, not lot_, for a level bound to one)."),
}

_PROTOCOL_PROPERTY: dict[str, Any] = {
    "type": "object",
    "description": (
        "One stored protocol: {'level': {'level_of_theory_ref': 'lot_...'} or {'composite_scheme_ref': 'csch_...'}, "
        "'recipe': optional}. The level names exactly one of the two refs."
    ),
    "properties": {
        "level": {
            "type": "object",
            "properties": _LEVEL_PROPERTIES,
            "additionalProperties": False,
        },
        "recipe": {"type": "object", "description": "A requested actual recipe in the declaration's shape (version 1)."},
    },
    "required": ["level"],
    "additionalProperties": False,
}


def _input_schema(tool: RelativeEnergyTool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "species_entry_ref": _ref_property("spe_", "The species entry."),
        "reference_conformer_ref": _ref_property("cg_", "The named reference conformer group of this entry."),
        "energy_kind": {"type": "string", "enum": list(ENERGY_KINDS), "description": "Required; no default."},
        "certificate": {"type": "string", "enum": list(CERTIFICATES), "description": "The curvature evidence each member needs. Required; no default."},
        "member_refs": {
            "type": "array",
            "items": _ref_property("cg_", "A conformer group."),
            "minItems": 1,
            "maxItems": 500,
            "description": "The conformer groups compared. Omit for every visible conformer group of the entry.",
        },
        "coverage_requirement": {"type": "string", "enum": list(COVERAGE)},
        "repeat_policy": {"type": "string", "enum": list(REPEAT_POLICIES)},
        "min_review_status": {"type": "string", "description": "Optional review floor, applied on top of the profile's floor."},
        "permitted_quality": {"type": "array", "items": {"type": "string", "enum": list(QUALITIES)}, "maxItems": 3},
        "require_stable_reference": {"type": "boolean"},
        "profile": {"type": "string", "enum": list(PROFILES)},
    }
    required = ["species_entry_ref", "reference_conformer_ref", "energy_kind", "certificate"]
    if tool.compare:
        properties["candidate"] = dict(_PROTOCOL_PROPERTY, description="The protocol under description. " + _PROTOCOL_PROPERTY["description"])
        properties["reference"] = dict(_PROTOCOL_PROPERTY, description="The protocol the candidate is set against. " + _PROTOCOL_PROPERTY["description"])
        properties["pairing"] = {
            "type": "string",
            "enum": list(PAIRINGS),
            "description": "same_geometry: both values evaluated at the same geometry. same_basin: each protocol's own geometry of the basin. Required; no default.",
        }
        required += ["candidate", "reference", "pairing"]
    else:
        properties.update(_LEVEL_PROPERTIES)
        properties["recipe"] = {"type": "object", "description": "A requested actual recipe in the declaration's shape (version 1)."}
        # The level is "exactly one of lot_ / csch_": expressed as oneOf so a host can show it; the tool re-checks.
    schema: dict[str, Any] = {"type": "object", "required": required, "properties": properties, "additionalProperties": False}
    if not tool.compare:
        schema["oneOf"] = [{"required": ["level_of_theory_ref"]}, {"required": ["composite_scheme_ref"]}]
    return schema


INPUT_SCHEMAS: dict[str, dict[str, Any]] = {name: _input_schema(tool) for name, tool in TOOLS.items()}


def catalogue() -> list[dict[str, Any]]:
    """The tool entries for the MCP host's tool listing."""
    return [
        {"name": tool.name, "description": tool.description, "inputSchema": INPUT_SCHEMAS[tool.name]}
        for tool in TOOLS.values()
    ]


def _enum(args: dict[str, Any], name: str, allowed: tuple[str, ...]) -> str:
    value = args.get(name)
    if value is None:
        raise invalid_input(f"{name} is required; there is no default")
    if value not in allowed:
        raise invalid_input(f"{name} must be one of {list(allowed)!r}; got {value!r}")
    return value


def _level(value: Any, *, field_name: str) -> dict[str, str]:
    """A level object holding exactly one of a ``lot_`` and a ``csch_`` ref, each with its prefix checked."""
    if not isinstance(value, dict):
        raise invalid_input(f"{field_name} must be an object with exactly one of level_of_theory_ref and composite_scheme_ref")
    unknown = sorted(value.keys() - {"level_of_theory_ref", "composite_scheme_ref"})
    if unknown:
        raise invalid_input(f"unknown field(s) in {field_name}: {unknown!r}")
    given = {k: v for k, v in value.items() if v is not None}
    if len(given) != 1:
        raise invalid_input(f"{field_name} must name exactly one of level_of_theory_ref and composite_scheme_ref")
    key, ref = next(iter(given.items()))
    prefix = "lot_" if key == "level_of_theory_ref" else "csch_"
    return {key: validate_path_handle(ref, field_name=f"{field_name}.{key}" if field_name != "level" else key, expected_prefix=prefix)}


def _protocol(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid_input(f"{field_name} is required and must be an object with a 'level' (and optionally a 'recipe')")
    unknown = sorted(value.keys() - {"level", "recipe"})
    if unknown:
        raise invalid_input(f"unknown field(s) in {field_name}: {unknown!r}")
    out: dict[str, Any] = {"level": _level(value.get("level"), field_name=f"{field_name}.level")}
    if value.get("recipe") is not None:
        if not isinstance(value["recipe"], dict):
            raise invalid_input(f"{field_name}.recipe must be an object; got {type(value['recipe']).__name__}")
        out["recipe"] = value["recipe"]
    return out


def run(name: str, client: TCKDBHttpClient, config: Config, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Validate inputs, POST the request, return the server response unchanged."""
    tool = TOOLS[name]
    # An argument sent as null is an argument not given.
    args = {k: v for k, v in dict(arguments or {}).items() if v is not None}

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use species_entry_ref, the conformer group refs and the level refs, not integer IDs."
        )
    rejected_bounds = sorted(_REJECTED_BOUND_FIELDS & args.keys())
    if rejected_bounds:
        raise invalid_input(
            f"{rejected_bounds!r} is not a request field: the bounds are the server's, members are ordered by conformer ref "
            "and the order is not a ranking, and a caller cannot author a sort or a rule."
        )
    unknown = sorted(args.keys() - set(INPUT_SCHEMAS[name]["properties"]))
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    ref = validate_path_handle(args.get("species_entry_ref"), field_name="species_entry_ref", expected_prefix="spe_")
    body: dict[str, Any] = {
        "reference_conformer_ref": validate_path_handle(
            args.get("reference_conformer_ref"), field_name="reference_conformer_ref", expected_prefix="cg_"
        ),
        "energy_kind": _enum(args, "energy_kind", ENERGY_KINDS),
        "certificate": _enum(args, "certificate", CERTIFICATES),
    }
    if tool.compare:
        body["candidate"] = _protocol(args.get("candidate"), field_name="candidate")
        body["reference"] = _protocol(args.get("reference"), field_name="reference")
        body["pairing"] = _enum(args, "pairing", PAIRINGS)
    else:
        body["level"] = _level(
            {k: args[k] for k in ("level_of_theory_ref", "composite_scheme_ref") if k in args}, field_name="level"
        )
        if "recipe" in args:
            if not isinstance(args["recipe"], dict):
                raise invalid_input(f"recipe must be an object; got {type(args['recipe']).__name__}")
            body["recipe"] = args["recipe"]

    if "coverage_requirement" in args:
        body["coverage_requirement"] = _enum(args, "coverage_requirement", COVERAGE)
    if "repeat_policy" in args:
        body["repeat_policy"] = _enum(args, "repeat_policy", REPEAT_POLICIES)
    if "require_stable_reference" in args:
        if not isinstance(args["require_stable_reference"], bool):
            raise invalid_input(f"require_stable_reference must be true or false; got {args['require_stable_reference']!r}")
        body["require_stable_reference"] = args["require_stable_reference"]
    if "min_review_status" in args:
        value = args["min_review_status"]
        if not isinstance(value, str) or not value or len(value) > 64:
            raise invalid_input(f"min_review_status must be a non-empty string of at most 64 characters; got {value!r}")
        body["min_review_status"] = value
    if "member_refs" in args:
        members = args["member_refs"]
        if not isinstance(members, list) or not members or len(members) > 500:
            raise invalid_input("member_refs must be a list of 1 to 500 conformer group refs")
        body["member_refs"] = [
            validate_path_handle(m, field_name="member_refs[]", expected_prefix="cg_") for m in members
        ]
    if "permitted_quality" in args:
        qualities = args["permitted_quality"]
        if not isinstance(qualities, list) or any(q not in QUALITIES for q in qualities):
            raise invalid_input(f"permitted_quality must be a list drawn from {list(QUALITIES)!r}")
        body["permitted_quality"] = list(qualities)

    profile = args.get("profile")
    if profile is not None and profile not in PROFILES:
        raise invalid_input(f"profile must be one of {list(PROFILES)!r}; got {profile!r}")

    url = client.scientific_url(
        f"/scientific/species-entries/{quote(ref, safe='')}/conformers/relative-energies{tool.operation}"
    )
    return client.post_json(url, body, params={"profile": profile})


__all__ = ["TOOLS", "TOOL_NAMES", "INPUT_SCHEMAS", "catalogue", "run"]
