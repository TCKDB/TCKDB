"""Structure selection tools: ``tckdb_select_species_entry_calculations``, ``tckdb_select_species_entry_conformers`` and
``tckdb_select_transition_state_entry_evidence``.

They wrap ``POST /api/v1/scientific/species-entries/{spe_ref}/calculations/select``, ``.../conformers/select`` and
``POST /api/v1/scientific/transition-state-entries/{tse_ref}/evidence/select``.

A read, not a write: nothing is stored and the browse orders are unchanged. The tools exist so an agent can ask "which
stored energy, conformer basin or saddle of this entry answers this stated question, and why", and be told the
server's own answer.

Policy choices enforced here (in addition to server-side validation):

- Public refs only. The entry ref must carry the right prefix (``spe_`` or ``tse_``); the transition-state tool takes an
  *entry*, not the transition-state concept. A fixed geometry is a ``geom_`` ref, explicit members are
  ``calc_`` / ``sdet_`` refs. Integer-id fields are rejected with a teaching error.
- The question is never defaulted by the tool: whatever the agent states is forwarded, the rest is the server's default.
  ``quantity`` may be explicit null (the one null that is sent) for evidence-only qualification, which asks for no energy.
- ``bounds``, ``max_candidates``, ``manifest_bytes``, ``limit``, ``offset`` and any pagination, sort or rule field are
  rejected: the engineering bounds are the server's, a selection is over the *complete* authorized population (above a
  bound the server refuses with a coded 422), and a caller cannot author a rule or a sort. This tool never caps, pages,
  filters or chooses candidates itself.
- The server's response is returned unchanged. ``outcome``, ``basis`` and ``selected_refs`` carry the explanation, so
  these tools add no prose of their own and never reduce an outcome to a recommendation: an agent that reports a result
  should quote ``outcome`` and ``basis`` verbatim.
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
COVERAGE = ("known_values", "all_requested_members")
CLAIMS = ("local_minimum", "first_order_saddle", "higher_order_saddle", "reactive_connectivity")
QUALITIES = ("raw", "curated", "rejected")
ADMIN_POLICIES = ("default", "latest", "earliest")
MODES = ("all", "first")
OBJECTIVES = ("physical_accuracy", "expected_accuracy", "model_fidelity")
REPEAT_POLICIES = ("retain_alternates", "administrative_representative")

_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"species_entry_id", "transition_state_entry_id", "calculation_id", "geometry_id", "determination_id", "species_id"}
)
_REJECTED_BOUND_FIELDS: frozenset[str] = frozenset(
    {"bounds", "max_candidates", "manifest_bytes", "limit", "offset", "page", "page_size", "cursor", "sort", "rules"}
)


@dataclass(frozen=True)
class StructureTool:
    name: str
    description: str
    family: str  # path segment: species-entries or transition-state-entries
    operation: str  # calculations, conformers or evidence
    ref_field: str
    prefix: str
    intents: tuple[str, ...]
    default_intent: str
    connectivity: bool = False


_COMMON_DESCRIPTION = (
    "Read-only. Returns the server response unchanged: report its 'outcome' and 'basis' verbatim. Values are ordered only "
    "inside one cohort (the same established recipe, quantity and energy convention); several cohorts end as "
    "'incomparable_alternatives' and a lower absolute energy never names a method winner. 'unresolved_comparability', "
    "'evidence_conflict' and 'policy_conflict' mean nothing was selected; 'representative_minimum' is the lowest "
    "administrative representative per target, never the minimum of all stored values; the answer is conditional on the "
    "authorized population the caller can see and is never a global-search certificate. A population over the server's "
    "bounds is a coded 422, never a prefix winner. A deferred quantity (enthalpy, Gibbs energy, barrier, rate) is "
    "'structure_selection_unsupported'. The tool never caps, pages or chooses candidates itself."
)

TOOLS: dict[str, StructureTool] = {
    "tckdb_select_species_entry_calculations": StructureTool(
        name="tckdb_select_species_entry_calculations",
        description=(
            "Select among one species entry's calculations for the lowest comparable recorded energy, or an audited-rule "
            "protocol preference. Requires a public species_entry_ref (starts with 'spe_'). intent is 'recorded_minimum' "
            "(default) or 'protocol_preferred' (which also needs an objective and a fixed geometry_ref). " + _COMMON_DESCRIPTION
        ),
        family="species-entries",
        operation="calculations",
        ref_field="species_entry_ref",
        prefix="spe_",
        intents=("recorded_minimum", "protocol_preferred"),
        default_intent="recorded_minimum",
    ),
    "tckdb_select_species_entry_conformers": StructureTool(
        name="tckdb_select_species_entry_conformers",
        description=(
            "Select among one species entry's validated conformer basins: the lowest of one cohort, the basins that support "
            "a stated claim (intent 'qualify_evidence', with quantity null and a validation_claim), or a protocol "
            "preference. Requires a public species_entry_ref ('spe_'). A basin needs curvature evidence on its own "
            "geometry, judged by magnitude against the stored tau (never by counting imaginary modes); repeated "
            "determinations of one basin are alternates, not confirmation. " + _COMMON_DESCRIPTION
        ),
        family="species-entries",
        operation="conformers",
        ref_field="species_entry_ref",
        prefix="spe_",
        intents=("validated_minimum", "qualify_evidence", "protocol_preferred"),
        default_intent="validated_minimum",
    ),
    "tckdb_select_transition_state_entry_evidence": StructureTool(
        name="tckdb_select_transition_state_entry_evidence",
        description=(
            "Select among one transition state ENTRY's saddle determinations: those that support a claim (default a "
            "conventional first-order saddle), the lowest, or a protocol preference. Requires a public "
            "transition_state_entry_ref ('tse_'), not the transition-state concept ('ts_'). A higher-order "
            "characterization never implies first-order TST suitability; reactive connectivity is an additional claim "
            "(require_connectivity). Sibling entries are assessed one at a time. " + _COMMON_DESCRIPTION
        ),
        family="transition-state-entries",
        operation="evidence",
        ref_field="transition_state_entry_ref",
        prefix="tse_",
        intents=("validated_saddle", "qualify_evidence", "protocol_preferred"),
        default_intent="validated_saddle",
        connectivity=True,
    ),
}

TOOL_NAMES = frozenset(TOOLS)


def _input_schema(tool: StructureTool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        tool.ref_field: {
            "type": "string",
            "description": f"Public ref. Must start with '{tool.prefix}'.",
            "pattern": f"^{tool.prefix}[A-Za-z0-9_-]+$",
            "minLength": len(tool.prefix) + 1,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "intent": {"type": "string", "enum": list(tool.intents), "default": tool.default_intent},
        "quantity": {
            "type": ["string", "null"],
            "description": (
                "'electronic_energy' (the server default) or 'zero_kelvin_energy'. Send null only for evidence-only "
                "qualification (intent 'qualify_evidence'), which asks for no energy. A recognised deferred quantity is "
                "refused by the server."
            ),
        },
        "coverage_requirement": {"type": "string", "enum": list(COVERAGE), "default": "known_values"},
        "validation_claim": {"type": "string", "enum": list(CLAIMS)},
        "min_review_status": {"type": "string", "description": "Optional review floor, applied on top of the profile's floor."},
        "permitted_quality": {"type": "array", "items": {"type": "string", "enum": list(QUALITIES)}, "maxItems": 3},
        "geometry_ref": {"type": "string", "description": "A fixed-geometry target (public geom_ ref).", "maxLength": PUBLIC_REF_MAX_LENGTH},
        "member_refs": {
            "type": "array",
            "items": {"type": "string", "maxLength": PUBLIC_REF_MAX_LENGTH},
            "minItems": 1,
            "maxItems": 500,
            "description": "An explicit population (public refs). Omit for the complete authorized corpus.",
        },
        "recipe": {"type": "object", "description": "A requested actual recipe in the declaration's shape (version 1)."},
        "require_stable_reference": {"type": "boolean"},
        "administrative_policy": {"type": "string", "enum": list(ADMIN_POLICIES), "default": "default"},
        "result_mode": {"type": "string", "enum": list(MODES), "default": "all"},
        "apply_rules": {"type": "boolean", "default": True},
        "objective": {"type": "string", "enum": list(OBJECTIVES), "description": "Required for protocol_preferred."},
        "reference_model": {"type": "string", "maxLength": 200},
        "repeat_policy": {"type": "string", "enum": list(REPEAT_POLICIES), "default": "retain_alternates"},
        "profile": {"type": "string", "enum": list(PROFILES), "default": "exploratory"},
    }
    if tool.connectivity:
        properties["require_connectivity"] = {"type": "boolean"}
    return {"type": "object", "required": [tool.ref_field], "properties": properties, "additionalProperties": False}


INPUT_SCHEMAS: dict[str, dict[str, Any]] = {name: _input_schema(tool) for name, tool in TOOLS.items()}


def catalogue() -> list[dict[str, Any]]:
    """The tool entries for the MCP host's tool listing."""
    return [
        {"name": tool.name, "description": tool.description, "inputSchema": INPUT_SCHEMAS[tool.name]}
        for tool in TOOLS.values()
    ]


def _enum(args: dict[str, Any], name: str, allowed: tuple[str, ...]) -> str:
    value = args[name]
    if value not in allowed:
        raise invalid_input(f"{name} must be one of {list(allowed)!r}; got {value!r}")
    return value


def _bool(args: dict[str, Any], name: str) -> bool:
    if not isinstance(args[name], bool):
        raise invalid_input(f"{name} must be true or false; got {args[name]!r}")
    return args[name]


def _string(args: dict[str, Any], name: str, *, max_length: int) -> str:
    value = args[name]
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise invalid_input(f"{name} must be a non-empty string of at most {max_length} characters; got {value!r}")
    return value


def run(name: str, client: TCKDBHttpClient, config: Config, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Validate inputs, POST the selection request, return the server response unchanged."""
    tool = TOOLS[name]
    raw = dict(arguments or {})
    # An argument sent as null is an argument not given and is dropped, except ``quantity``: null is the way to ask for
    # no energy, so it is forwarded as JSON null.
    args = {k: v for k, v in raw.items() if v is not None or k == "quantity"}

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            f"Use {tool.ref_field} and the geometry and member public refs, not integer IDs."
        )
    rejected_bounds = sorted(_REJECTED_BOUND_FIELDS & args.keys())
    if rejected_bounds:
        raise invalid_input(
            f"{rejected_bounds!r} is not a request field: the engineering bounds are fixed by the server (which refuses "
            "above them with a coded 422), a selection is over the complete authorized population and never a page, and "
            "a caller cannot author a rule or a sort. This tool never caps, pages or chooses candidates itself."
        )
    accepted = set(INPUT_SCHEMAS[name]["properties"])
    unknown = sorted(args.keys() - accepted)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    ref = validate_path_handle(args.get(tool.ref_field), field_name=tool.ref_field, expected_prefix=tool.prefix)
    body: dict[str, Any] = {}
    if "intent" in args:
        body["intent"] = _enum(args, "intent", tool.intents)
    if "quantity" in args:
        quantity = args["quantity"]
        if quantity is not None and not isinstance(quantity, str):
            raise invalid_input(f"quantity must be a string or null; got {quantity!r}")
        body["quantity"] = quantity
    for field_name, allowed in (
        ("coverage_requirement", COVERAGE),
        ("validation_claim", CLAIMS),
        ("administrative_policy", ADMIN_POLICIES),
        ("result_mode", MODES),
        ("objective", OBJECTIVES),
        ("repeat_policy", REPEAT_POLICIES),
    ):
        if field_name in args:
            body[field_name] = _enum(args, field_name, allowed)
    for field_name in ("require_stable_reference", "apply_rules") + (("require_connectivity",) if tool.connectivity else ()):
        if field_name in args:
            body[field_name] = _bool(args, field_name)
    for field_name, limit in (("min_review_status", 64), ("reference_model", 200)):
        if field_name in args:
            body[field_name] = _string(args, field_name, max_length=limit)
    if "geometry_ref" in args:
        body["geometry_ref"] = validate_path_handle(args["geometry_ref"], field_name="geometry_ref", expected_prefix="geom_")
    if "member_refs" in args:
        members = args["member_refs"]
        if not isinstance(members, list) or not members or len(members) > 500:
            raise invalid_input("member_refs must be a list of 1 to 500 public refs")
        body["member_refs"] = [
            _string({"member": m}, "member", max_length=PUBLIC_REF_MAX_LENGTH) for m in members
        ]
    if "permitted_quality" in args:
        qualities = args["permitted_quality"]
        if not isinstance(qualities, list) or any(q not in QUALITIES for q in qualities):
            raise invalid_input(f"permitted_quality must be a list drawn from {list(QUALITIES)!r}")
        body["permitted_quality"] = list(qualities)
    if "recipe" in args:
        if not isinstance(args["recipe"], dict):
            raise invalid_input(f"recipe must be an object; got {type(args['recipe']).__name__}")
        body["recipe"] = args["recipe"]

    profile = args.get("profile")
    if profile is not None and profile not in PROFILES:
        raise invalid_input(f"profile must be one of {list(PROFILES)!r}; got {profile!r}")

    url = client.scientific_url(f"/scientific/{tool.family}/{quote(ref, safe='')}/{tool.operation}/select")
    return client.post_json(url, body, params={"profile": profile})


__all__ = ["TOOLS", "TOOL_NAMES", "INPUT_SCHEMAS", "catalogue", "run"]
