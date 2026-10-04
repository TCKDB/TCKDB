"""``tckdb_select_network_kinetics`` tool: method-aware selection among a network's pressure-dependent solves.

Wraps ``POST /api/v1/scientific/networks/{network_ref}/kinetics/select``.

A read, not a write: nothing is stored and the browse and evaluation tools for networks are unchanged. The tool
exists so an agent can ask "which stored pressure-dependent solve, or declared bundle of outputs, should I use for
this stated gas-phase question about this network, and why", and be told the server's own answer.

Policy choices enforced here (in addition to server-side validation):

- Public refs only. ``network_ref`` must start with ``net_`` and is the only thing in the URL path; a bath species is
  a ``spe_`` ref and a reference model an ``nsolve_`` ref. States are named by composition hash. Integer-id fields
  are rejected with a teaching error, and ``channel_key`` is a body field, never a path segment.
- The question is required and is never defaulted by this tool: coefficient basis, temperature and pressure
  windows, bath and state partition, and the channel and observable (or the list of outputs). Whether the question
  is well posed (mole fractions sum to one, a single channel names no outputs, ...) is the server's rule, so the
  server's own refusal reaches the agent.
- ``max_candidates``, ``bounds``, ``limit``, ``offset`` and any pagination argument are rejected: the bounds are the
  server's, and over one of them the server refuses with 422 ``network_selection_population_too_large``. This tool
  never caps or pages the population itself.
- The server's response is returned unchanged. ``outcome``, ``basis`` and ``selection`` carry the explanation, so
  this tool adds no prose of its own and does not reduce an outcome to a recommendation: an agent that reports a
  result should quote ``outcome`` and ``basis`` verbatim.
- Arguments sent as null are dropped, never forwarded as JSON null. ``phase`` is accepted only so that a conflicting
  value reaches the server and is refused with its own code; the tool never corrects it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

TOOL_NAME = "tckdb_select_network_kinetics"
TOOL_DESCRIPTION = (
    "Select which stored pressure-dependent network solve (or declared product set of one solve) to use for one "
    "stated gas-phase rate-coefficient question, with the reason. Requires a public network_ref (starts with 'net_') "
    "and the whole question: coefficient_basis, a temperature window and a pressure window in bar, a bath (public "
    "'spe_' species refs) and a state partition (composition hashes), and either channel_key with observable or, for "
    "a bundle or the full network, a list of outputs. Read-only. Returns the server response unchanged: report its "
    "'outcome' and 'basis' verbatim. 'incomparable_alternatives' and 'policy_conflict' mean nothing was "
    "scientifically selected; 'sole_eligible_candidate' is not a comparative claim; a 'selection' with "
    "administrative=true is a review/recency choice, not a method claim. A selection names a node with every "
    "eligible fitted representation of each member, and 'disclosures' lists what did not compete."
)

SCOPES = ("single_channel", "projected_bundle", "full_network")
BASES = ("kernel", "composition_effective")
OBJECTIVES = ("physical_accuracy", "model_fidelity", "representation_fidelity")
POLICIES = ("method_preferred", "default", "most_reviewed", "latest")
MODES = ("all", "first")
PROFILES = ("exploratory", "curated")
REGIME_KINDS = ("time_independent", "initial_population_restricted")

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "network_ref", "quantity", "phase", "scope", "channel_key", "observable", "outputs", "coefficient_basis",
        "degeneracy_applied", "temperature_min_k", "temperature_max_k", "pressure_min_bar", "pressure_max_bar",
        "bath", "partition", "boundaries", "regime", "source_composition_hash", "sink_composition_hash", "objective",
        "reference_model_ref", "reference_outputs", "policy", "mode", "min_review_status", "profile",
    }
)
_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {
        "network_id", "channel_id", "state_id", "species_id", "species_entry_id", "solve_id", "network_solve_id",
        "kinetics_id", "reference_model_id",
    }
)
_REJECTED_PAGING_FIELDS: frozenset[str] = frozenset(
    {"max_candidates", "bounds", "limit", "offset", "page", "page_size", "cursor"}
)
_REQUIRED = ("network_ref", "coefficient_basis", "temperature_min_k", "temperature_max_k", "pressure_min_bar",
             "pressure_max_bar", "bath", "partition")

_HASH_LIST = {"type": "array", "items": {"type": "string", "maxLength": 128}}
_OBSERVABLE = {
    "type": "string",
    "description": "Coefficient meaning, e.g. 'product_resolved_coefficient'. Validated by the server.",
}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": list(_REQUIRED),
    "properties": {
        "network_ref": {
            "type": "string",
            "description": "Public network ref. Must start with 'net_'. The only identifier in the URL path.",
            "pattern": "^net_[A-Za-z0-9_-]+$",
            "minLength": 5,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "quantity": {"type": "string", "enum": ["rate_coefficient"]},
        "phase": {"type": "string", "description": "Must be 'gas' if given; any other value is refused by the server."},
        "scope": {"type": "string", "enum": list(SCOPES), "default": "single_channel"},
        "channel_key": {
            "type": "string",
            "maxLength": 256,
            "description": "Single-channel questions only. A body field, never a path segment.",
        },
        "observable": _OBSERVABLE,
        "outputs": {
            "type": "array",
            "description": "Bundle and full-network questions: the required outputs, each a channel and an observable.",
            "items": {
                "type": "object",
                "required": ["channel_key", "observable"],
                "properties": {"channel_key": {"type": "string", "maxLength": 256}, "observable": _OBSERVABLE},
                "additionalProperties": False,
            },
        },
        "coefficient_basis": {"type": "string", "enum": list(BASES)},
        "degeneracy_applied": {"type": "boolean"},
        "temperature_min_k": {"type": "number", "exclusiveMinimum": 0},
        "temperature_max_k": {"type": "number", "exclusiveMinimum": 0},
        "pressure_min_bar": {"type": "number", "exclusiveMinimum": 0},
        "pressure_max_bar": {"type": "number", "exclusiveMinimum": 0},
        "bath": {
            "type": "object",
            "description": (
                "One component with no mole_fraction is a specified collider; two or more, each with a mole_fraction "
                "summing to 1, is a mixture (never renormalised). Components are public 'spe_' species-entry refs."
            ),
            "required": ["components"],
            "properties": {
                "components": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {
                        "type": "object",
                        "required": ["species_ref"],
                        "properties": {
                            "species_ref": {"type": "string", "maxLength": PUBLIC_REF_MAX_LENGTH},
                            "mole_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
        },
        "partition": {
            "type": "object",
            "description": "How the observable treats the network's states, by composition hash.",
            "properties": {"retained": _HASH_LIST, "eliminated": _HASH_LIST,
                           "lumps": {"type": "array", "items": _HASH_LIST}},
            "additionalProperties": False,
        },
        "boundaries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["state", "kind"],
                "properties": {"state": {"type": "string", "maxLength": 128}, "kind": {"type": "string", "maxLength": 64}},
                "additionalProperties": False,
            },
        },
        "regime": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": list(REGIME_KINDS)}, "initial_state_hashes": _HASH_LIST},
            "additionalProperties": False,
        },
        "source_composition_hash": {"type": "string", "maxLength": 128},
        "sink_composition_hash": {"type": "string", "maxLength": 128},
        "objective": {"type": "string", "enum": list(OBJECTIVES), "default": "physical_accuracy"},
        "reference_model_ref": {
            "type": "string",
            "maxLength": PUBLIC_REF_MAX_LENGTH,
            "description": "Public network-solve ref ('nsolve_'). Required for, and only for, model_fidelity.",
        },
        "reference_outputs": {
            "type": "string",
            "maxLength": 512,
            "description": "Names the reference output set. Required for, and only for, representation_fidelity.",
        },
        "policy": {
            "type": "string",
            "enum": list(POLICIES),
            "default": "method_preferred",
            "description": (
                "method_preferred applies the registered audited rules (none is active in this release, so it "
                "ranks nothing yet); default, most_reviewed and latest apply review/recency order only."
            ),
        },
        "mode": {"type": "string", "enum": list(MODES), "default": "all"},
        "min_review_status": {"type": "string", "description": "Optional review floor, on top of the profile's floor."},
        "profile": {"type": "string", "enum": list(PROFILES), "default": "exploratory"},
    },
    "additionalProperties": False,
}


def run(client: TCKDBHttpClient, config: Config, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Validate inputs, POST the selection request, return the server response unchanged."""
    args = {k: v for k, v in (arguments or {}).items() if v is not None}

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use network_ref and the bath and reference-model public refs, not integer IDs."
        )
    rejected_paging = sorted(_REJECTED_PAGING_FIELDS & args.keys())
    if rejected_paging:
        raise invalid_input(
            f"{rejected_paging!r} is not a request field: the bounds are fixed by the server, which refuses with "
            "'network_selection_population_too_large' over one, and this tool never pages or caps the population itself."
        )
    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    network_ref = validate_path_handle(args.get("network_ref"), field_name="network_ref", expected_prefix="net_")
    body: dict[str, Any] = {
        "coefficient_basis": _enum(args, "coefficient_basis", BASES, required=True),
        "temperature_min_k": _number(args, "temperature_min_k", required=True),
        "temperature_max_k": _number(args, "temperature_max_k", required=True),
        "pressure_min_bar": _number(args, "pressure_min_bar", required=True),
        "pressure_max_bar": _number(args, "pressure_max_bar", required=True),
        "bath": _validate_bath(args.get("bath")),
        "partition": _validate_partition(args.get("partition")),
    }
    for name, allowed in (("scope", SCOPES), ("objective", OBJECTIVES), ("policy", POLICIES), ("mode", MODES)):
        if name in args:
            body[name] = _enum(args, name, allowed, required=False)
    for name in ("channel_key", "observable", "source_composition_hash", "sink_composition_hash",
                 "reference_outputs", "min_review_status", "phase"):
        if name in args:
            body[name] = _string(args, name)
    if "reference_model_ref" in args:
        body["reference_model_ref"] = validate_path_handle(
            args["reference_model_ref"], field_name="reference_model_ref", expected_prefix="nsolve_"
        )
    if "degeneracy_applied" in args:
        if not isinstance(args["degeneracy_applied"], bool):
            raise invalid_input(f"degeneracy_applied must be a boolean; got {args['degeneracy_applied']!r}")
        body["degeneracy_applied"] = args["degeneracy_applied"]
    if "quantity" in args:
        if args["quantity"] != "rate_coefficient":
            raise invalid_input(f"quantity must be 'rate_coefficient' if given; got {args['quantity']!r}")
        body["quantity"] = args["quantity"]
    if "outputs" in args:
        body["outputs"] = _validate_outputs(args["outputs"])
    if "boundaries" in args:
        body["boundaries"] = _validate_boundaries(args["boundaries"])
    if "regime" in args:
        body["regime"] = _validate_regime(args["regime"])

    profile = args.get("profile")
    if profile is not None and profile not in PROFILES:
        raise invalid_input(f"profile must be one of {list(PROFILES)!r}; got {profile!r}")

    url = client.scientific_url(f"/scientific/networks/{quote(network_ref, safe='')}/kinetics/select")
    return client.post_json(url, body, params={"profile": profile})


def _enum(args: dict[str, Any], name: str, allowed: tuple[str, ...], *, required: bool) -> Any:
    if args.get(name) is None:
        if required:
            raise invalid_input(f"{name} is required (one of {list(allowed)!r}); it is never defaulted")
        return None
    if args[name] not in allowed:
        raise invalid_input(f"{name} must be one of {list(allowed)!r}; got {args[name]!r}")
    return args[name]


def _number(source: dict[str, Any], name: str, *, required: bool) -> float | int | None:
    value = source.get(name)
    if value is None:
        if required:
            raise invalid_input(f"{name} is required; it is never defaulted")
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise invalid_input(f"{name} must be a number; got {value!r}")
    return value


def _string(source: dict[str, Any], name: str) -> str:
    value = source[name]
    if not isinstance(value, str):
        raise invalid_input(f"{name} must be a string; got {value!r}")
    return value


def _closed(value: Any, name: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid_input(f"{name} must be an object; got {type(value).__name__}")
    unknown = sorted(value.keys() - allowed)
    if unknown:
        if any(k.endswith("_id") for k in unknown):
            raise invalid_input(
                f"integer-id fields are not accepted by the MCP: {unknown!r}. Use public refs, not integer IDs."
            )
        raise invalid_input(f"unknown {name} field(s): {unknown!r}")
    return value


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise invalid_input(f"{name} must be a list of strings")
    return list(value)


def _validate_bath(value: Any) -> dict[str, Any]:
    if value is None:
        raise invalid_input("bath is required: {'components': [{'species_ref': 'spe_...'}]}")
    value = _closed(value, "bath", frozenset({"components"}))
    components = value.get("components")
    if not isinstance(components, list) or not components:
        raise invalid_input("bath.components must be a non-empty list")
    out: list[dict[str, Any]] = []
    for i, component in enumerate(components):
        component = _closed(component, f"bath.components[{i}]", frozenset({"species_ref", "mole_fraction"}))
        item: dict[str, Any] = {
            "species_ref": validate_path_handle(
                component.get("species_ref"), field_name=f"bath.components[{i}].species_ref", expected_prefix="spe_"
            )
        }
        if component.get("mole_fraction") is not None:
            item["mole_fraction"] = _number(component, "mole_fraction", required=False)
        out.append(item)
    return {"components": out}


def _validate_partition(value: Any) -> dict[str, Any]:
    if value is None:
        raise invalid_input("partition is required: {'retained': [<composition hash>, ...]}")
    value = _closed(value, "partition", frozenset({"retained", "eliminated", "lumps"}))
    out: dict[str, Any] = {}
    for name in ("retained", "eliminated"):
        if value.get(name) is not None:
            out[name] = _strings(value[name], f"partition.{name}")
    if value.get("lumps") is not None:
        if not isinstance(value["lumps"], list):
            raise invalid_input("partition.lumps must be a list of lists of strings")
        out["lumps"] = [_strings(lump, "partition.lumps[]") for lump in value["lumps"]]
    return out


def _validate_outputs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise invalid_input("outputs must be a list of {'channel_key', 'observable'} objects")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        item = _closed(item, f"outputs[{i}]", frozenset({"channel_key", "observable"}))
        for name in ("channel_key", "observable"):
            if not isinstance(item.get(name), str):
                raise invalid_input(f"outputs[{i}].{name} is required and must be a string")
        out.append({"channel_key": item["channel_key"], "observable": item["observable"]})
    return out


def _validate_boundaries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise invalid_input("boundaries must be a list of {'state', 'kind'} objects")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        item = _closed(item, f"boundaries[{i}]", frozenset({"state", "kind"}))
        for name in ("state", "kind"):
            if not isinstance(item.get(name), str):
                raise invalid_input(f"boundaries[{i}].{name} is required and must be a string")
        out.append({"state": item["state"], "kind": item["kind"]})
    return out


def _validate_regime(value: Any) -> dict[str, Any]:
    value = _closed(value, "regime", frozenset({"kind", "initial_state_hashes"}))
    out: dict[str, Any] = {}
    if value.get("kind") is not None:
        if value["kind"] not in REGIME_KINDS:
            raise invalid_input(f"regime.kind must be one of {list(REGIME_KINDS)!r}; got {value['kind']!r}")
        out["kind"] = value["kind"]
    if value.get("initial_state_hashes") is not None:
        out["initial_state_hashes"] = _strings(value["initial_state_hashes"], "regime.initial_state_hashes")
    return out


__all__ = ["TOOL_NAME", "TOOL_DESCRIPTION", "INPUT_SCHEMA", "run"]
