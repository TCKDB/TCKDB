"""``tckdb_select_reaction_entry_kinetics`` tool: method-aware kinetics selection.

Wraps ``POST /api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics/select``.

A read, not a write: nothing is stored and the browse order of
``tckdb_get_reaction_entry_kinetics`` is unchanged. The tool exists so an agent can ask "which stored rate
coefficient should I use for this stated gas-phase question about this reaction entry, and why", and be told the
server's own answer.

Policy choices enforced here (in addition to server-side validation):

- Public refs only. ``reaction_entry_ref`` must start with ``rxe_``; a transition state entry, network and collider
  species are named by ``tse_``, ``net_`` and ``spc_`` refs. Integer-id fields are rejected with a teaching error.
- The question is required and is never defaulted by this tool: direction, target, coefficient basis, temperature
  window and pressure. Whether the question is well posed (a finite pressure needs a collider, mole fractions sum to
  one, ...) is the server's rule, so the server's own refusal reaches the agent.
- ``max_candidates``, ``limit``, ``offset`` and any pagination argument are rejected: the 500-candidate cap is the
  server's, and above it the server refuses with 422 ``kinetics_selection_population_too_large``. This tool never
  caps or pages the candidate population itself.
- The server's response is returned unchanged. ``outcome``, ``basis`` and ``selection`` carry the explanation, so
  this tool adds no prose of its own and does not reduce an outcome to a recommendation: an agent that reports a
  result should quote ``outcome`` and ``basis`` verbatim.
- ``phase`` is accepted only so that a conflicting value reaches the server and is refused with its own code; the
  tool never corrects it.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

TOOL_NAME = "tckdb_select_reaction_entry_kinetics"
TOOL_DESCRIPTION = (
    "Select which stored kinetics record of a reaction_entry to use for one stated gas-phase rate-coefficient "
    "question, with the reason. Requires a public reaction_entry_ref (starts with 'rxe_') and the whole question: "
    "direction (forward or reverse), target (whole_reaction, or a resolved_channel with a transition state entry or "
    "a network channel), coefficient_basis, a temperature window and a pressure (independent, high_pressure_limit "
    "or finite); a finite pressure or a composition-effective coefficient also needs a collider. Read-only. Returns "
    "the server response unchanged: report its 'outcome' and 'basis' verbatim. 'incomparable_alternatives' and "
    "'policy_conflict' mean nothing was scientifically selected; 'sole_eligible_candidate' is not a comparative "
    "claim; a 'selection' with administrative=true is a review/recency choice, not a method claim. A selection "
    "names a determination with every eligible fitted representation of it, and 'disclosures' lists the records "
    "that did not compete."
)

DIRECTIONS = ("forward", "reverse")
TARGET_KINDS = ("whole_reaction", "resolved_channel")
BASES = ("elementary_coefficient", "third_body_kernel", "composition_effective_coefficient")
PRESSURE_KINDS = ("independent", "high_pressure_limit", "finite")
POLICIES = ("method_preferred", "default", "most_reviewed", "latest")
MODES = ("all", "first")
PROFILES = ("exploratory", "curated")

_ACCEPTED_FIELDS: frozenset[str] = frozenset(
    {
        "reaction_entry_ref",
        "quantity",
        "direction",
        "target",
        "coefficient_basis",
        "temperature_min_k",
        "temperature_max_k",
        "pressure",
        "collider",
        "policy",
        "mode",
        "min_review_status",
        "phase",
        "profile",
    }
)

_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"reaction_entry_id", "reaction_id", "kinetics_id", "transition_state_entry_id", "network_id", "species_id"}
)
_REJECTED_PAGING_FIELDS: frozenset[str] = frozenset({"max_candidates", "limit", "offset", "page", "page_size", "cursor"})

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "reaction_entry_ref",
        "direction",
        "target",
        "coefficient_basis",
        "temperature_min_k",
        "temperature_max_k",
        "pressure",
    ],
    "properties": {
        "reaction_entry_ref": {
            "type": "string",
            "description": "Public reaction_entry ref. Must start with 'rxe_'.",
            "pattern": "^rxe_[A-Za-z0-9_-]+$",
            "minLength": 5,
            "maxLength": PUBLIC_REF_MAX_LENGTH,
        },
        "quantity": {
            "type": "string",
            "enum": ["rate_coefficient"],
            "description": "Must be 'rate_coefficient' if given; this endpoint answers nothing else.",
        },
        "direction": {
            "type": "string",
            "enum": list(DIRECTIONS),
            "description": "Relative to the reaction entry's stored reactant-to-product orientation.",
        },
        "target": {
            "type": "object",
            "description": (
                "The rate asked for. 'whole_reaction' names no locator. 'resolved_channel' names either a "
                "transition_state_entry_ref ('tse_') or a network_ref ('net_') together with channel_key. A "
                "mismatch is refused by the server."
            ),
            "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "enum": list(TARGET_KINDS)},
                "transition_state_entry_ref": {"type": "string", "maxLength": PUBLIC_REF_MAX_LENGTH},
                "network_ref": {"type": "string", "maxLength": PUBLIC_REF_MAX_LENGTH},
                "channel_key": {"type": "string", "maxLength": 256},
            },
            "additionalProperties": False,
        },
        "coefficient_basis": {
            "type": "string",
            "enum": list(BASES),
            "description": (
                "What the coefficient still needs: an elementary coefficient, a third-body kernel (still to be "
                "multiplied by a collider concentration), or a coefficient already evaluated for a composition."
            ),
        },
        "temperature_min_k": {"type": "number", "exclusiveMinimum": 0},
        "temperature_max_k": {"type": "number", "exclusiveMinimum": 0},
        "pressure": {
            "type": "object",
            "description": (
                "'independent', 'high_pressure_limit', or 'finite' with min_bar and max_bar in bar (equal for a "
                "point). The first two carry no bounds."
            ),
            "required": ["kind"],
            "properties": {
                "kind": {"type": "string", "enum": list(PRESSURE_KINDS)},
                "min_bar": {"type": "number", "exclusiveMinimum": 0},
                "max_bar": {"type": "number", "exclusiveMinimum": 0},
            },
            "additionalProperties": False,
        },
        "collider": {
            "type": "object",
            "description": (
                "Required for a finite pressure and for a composition-effective coefficient. One component with "
                "no mole_fraction is a specified collider; two or more, each with a mole_fraction summing to 1, "
                "is a mixture (never renormalised)."
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
                            "species_ref": {
                                "type": "string",
                                "description": "Public species ref. Must start with 'spc_'.",
                                "maxLength": PUBLIC_REF_MAX_LENGTH,
                            },
                            "mole_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
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
        "min_review_status": {
            "type": "string",
            "description": "Optional review floor, applied on top of the profile's floor.",
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
    # An argument sent as null is an argument not given: it is dropped here, never forwarded as JSON null (which the
    # server would refuse for an enum field such as ``mode`` or ``policy``).
    args = {k: v for k, v in (arguments or {}).items() if v is not None}

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use reaction_entry_ref and the target and collider public refs, not integer IDs."
        )
    rejected_paging = sorted(_REJECTED_PAGING_FIELDS & args.keys())
    if rejected_paging:
        raise invalid_input(
            f"{rejected_paging!r} is not a request field: the candidate cap is fixed by the server, which refuses "
            "with 'kinetics_selection_population_too_large' above it, and this tool never pages or caps the "
            "population itself."
        )
    unknown = sorted(args.keys() - _ACCEPTED_FIELDS)
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")

    reaction_entry_ref = validate_path_handle(
        args.get("reaction_entry_ref"), field_name="reaction_entry_ref", expected_prefix="rxe_"
    )
    body: dict[str, Any] = {
        "direction": _enum(args, "direction", DIRECTIONS, required=True),
        "target": _validate_target(args.get("target")),
        "coefficient_basis": _enum(args, "coefficient_basis", BASES, required=True),
        "temperature_min_k": _number(args, "temperature_min_k", required=True),
        "temperature_max_k": _number(args, "temperature_max_k", required=True),
        "pressure": _validate_pressure(args.get("pressure")),
    }
    if args.get("collider") is not None:
        body["collider"] = _validate_collider(args["collider"])
    for field_name, allowed in (("policy", POLICIES), ("mode", MODES)):
        if field_name in args:
            body[field_name] = _enum(args, field_name, allowed, required=False)
    if args.get("min_review_status") is not None:
        if not isinstance(args["min_review_status"], str):
            raise invalid_input(f"min_review_status must be a string; got {args['min_review_status']!r}")
        body["min_review_status"] = args["min_review_status"]
    if args.get("quantity") is not None:
        if args["quantity"] != "rate_coefficient":
            raise invalid_input(f"quantity must be 'rate_coefficient' if given; got {args['quantity']!r}")
        body["quantity"] = args["quantity"]
    if args.get("phase") is not None:
        if not isinstance(args["phase"], str):
            raise invalid_input(f"phase must be a string; got {args['phase']!r}")
        body["phase"] = args["phase"]

    profile = args.get("profile")
    if profile is not None and profile not in PROFILES:
        raise invalid_input(f"profile must be one of {list(PROFILES)!r}; got {profile!r}")

    quoted_ref = quote(reaction_entry_ref, safe="")
    url = client.scientific_url(f"/scientific/reaction-entries/{quoted_ref}/kinetics/select")
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


def _validate_target(value: Any) -> dict[str, Any]:
    if value is None:
        raise invalid_input("target is required: {'kind': 'whole_reaction'} or a resolved_channel target")
    value = _closed(value, "target", frozenset({"kind", "transition_state_entry_ref", "network_ref", "channel_key"}))
    kind = value.get("kind")
    if kind not in TARGET_KINDS:
        raise invalid_input(f"target.kind must be one of {list(TARGET_KINDS)!r}; got {kind!r}")
    out: dict[str, Any] = {"kind": kind}
    # Only the shape of each ref is checked here. Whether a locator belongs with this kind of target is the
    # server's rule, so the server's own refusal reaches the agent.
    if value.get("transition_state_entry_ref") is not None:
        out["transition_state_entry_ref"] = validate_path_handle(
            value["transition_state_entry_ref"], field_name="target.transition_state_entry_ref", expected_prefix="tse_"
        )
    if value.get("network_ref") is not None:
        out["network_ref"] = validate_path_handle(
            value["network_ref"], field_name="target.network_ref", expected_prefix="net_"
        )
    if value.get("channel_key") is not None:
        if not isinstance(value["channel_key"], str):
            raise invalid_input(f"target.channel_key must be a string; got {value['channel_key']!r}")
        out["channel_key"] = value["channel_key"]
    return out


def _validate_pressure(value: Any) -> dict[str, Any]:
    if value is None:
        raise invalid_input("pressure is required: {'kind': 'independent'}, 'high_pressure_limit' or a finite window")
    value = _closed(value, "pressure", frozenset({"kind", "min_bar", "max_bar"}))
    kind = value.get("kind")
    if kind not in PRESSURE_KINDS:
        raise invalid_input(f"pressure.kind must be one of {list(PRESSURE_KINDS)!r}; got {kind!r}")
    out: dict[str, Any] = {"kind": kind}
    for name in ("min_bar", "max_bar"):
        if value.get(name) is not None:
            out[name] = _number(value, name, required=False)
    return out


def _validate_collider(value: Any) -> dict[str, Any]:
    value = _closed(value, "collider", frozenset({"components"}))
    components = value.get("components")
    if not isinstance(components, list) or not components:
        raise invalid_input("collider.components must be a non-empty list")
    out: list[dict[str, Any]] = []
    for i, component in enumerate(components):
        component = _closed(component, f"collider.components[{i}]", frozenset({"species_ref", "mole_fraction"}))
        item: dict[str, Any] = {
            "species_ref": validate_path_handle(
                component.get("species_ref"), field_name=f"collider.components[{i}].species_ref", expected_prefix="spc_"
            )
        }
        if component.get("mole_fraction") is not None:
            item["mole_fraction"] = _number(component, "mole_fraction", required=False)
        out.append(item)
    return {"components": out}


__all__ = ["TOOL_NAME", "TOOL_DESCRIPTION", "INPUT_SCHEMA", "run"]
