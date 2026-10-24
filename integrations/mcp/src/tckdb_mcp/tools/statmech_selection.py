"""Statmech selection tool: ``tckdb_select_statmech_models``.

Wraps ``POST /api/v1/scientific/statmech/select``.

A read, not a write: nothing is stored and the browse orders are unchanged. The tool exists so an agent can ask "which
supplied statistical model of this species or transition-state entry supports this stated use, and in what display
order?", and be told the server's own answer. No computed thermodynamic value is returned.

Policy choices enforced here (in addition to server-side validation):

- Public refs only. The target is a typed object whose ref must carry the right prefix (``spe_`` for a species entry,
  ``tse_`` for a transition-state *entry*); candidates named in ``population.model_refs`` are ``sm_``/``smd_`` refs.
  Integer-id fields are rejected with a teaching error.
- The question is never defaulted by the tool: whatever the agent states is forwarded, the rest is the server's default.
- ``bounds``, ``max_candidates``, ``manifest_bytes``, ``limit``, ``offset`` and any pagination, sort or rule field are
  rejected: the engineering bounds are the server's, a selection is over the *complete* authorized population (above a
  bound the server refuses with a coded 422), and a caller cannot author a sort. This tool never caps, pages,
  filters or chooses candidates itself.
- The server's response is returned unchanged. ``outcome``, ``basis``, ``selection`` and ``notices`` carry the
  explanation, so this tool adds no prose of its own and never reduces an outcome to a recommendation: an agent that
  reports a result should quote ``outcome`` and ``basis`` verbatim, and read every candidate's ``advisory``.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import invalid_input
from ..http_client import TCKDBHttpClient
from ._select_steps import (
    ELIGIBILITY_REVIEW_AND_FINDINGS,
    MODEL_PROVENANCE_FILTERS,
    forward_steps,
    step_properties,
)
from ._path_handles import PUBLIC_REF_MAX_LENGTH, validate_path_handle

TOOL_NAME = "tckdb_select_statmech_models"

PROFILES = ("exploratory", "curated")
INTENTS = ("qualify", "protocol_coverage")
COVERAGE = ("entire_requested_domain", "eligible_population")
ADMIN_POLICIES = ("default", "latest", "most_reviewed")
MODES = ("all", "first")
TARGET_REFS = {
    "species_entry": ("species_entry_ref", "spe_"),
    "transition_state_entry": ("transition_state_entry_ref", "tse_"),
}

_REJECTED_INTEGER_FIELDS: frozenset[str] = frozenset(
    {"species_entry_id", "transition_state_entry_id", "statmech_id", "declaration_id", "calculation_id", "conformer_group_id"}
)
_REJECTED_BOUND_FIELDS: frozenset[str] = frozenset(
    {"bounds", "max_candidates", "manifest_bytes", "limit", "offset", "page", "page_size", "cursor", "sort", "rules"}
)

TOOL_DESCRIPTION = (
    "Select among one species entry's or transition-state entry's supplied statistical-mechanics models: which of them "
    "support a stated use (intent 'qualify'), and, with intent 'protocol_coverage', the qualified whole models listed "
    "in display order. The target is {'kind': 'species_entry', 'species_entry_ref': "
    "'spe_...', 'scope': {...}, ...} or {'kind': 'transition_state_entry', 'transition_state_entry_ref': 'tse_...', "
    "'scope': {...}, ...}. Read-only. Returns the server response unchanged: report its 'outcome' and 'basis' verbatim. "
    "TCKDB ranks no model above another, so the answer is the qualified models, a sole eligible model or "
    "'incomparable_alternatives' (listed in display order, none preferred); 'evidence_conflict' and every unresolved "
    "outcome mean nothing was selected. Read each candidate's 'advisory' and the "
    "response 'notices': an imported source whose own stationary-point statement was not re-judged "
    "(imported_parameters_not_revalidated:<role>) and a literature citation with no DOI or ISBN "
    "(source_literature_unidentified:<role>) are disclosed there. The answer is conditional on the authorized population "
    "the caller can see and is never a physical-search certificate. A population over the server's bounds is a coded 422, "
    "never an answer from a prefix. No computed thermodynamic value is returned. The tool never caps, pages or chooses candidates "
    "itself."
)

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "task", "target"],
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "task": {
            "type": "string",
            "description": (
                "rotational_component, vibrational_component, zpe_component, within_basin_thermal, canonical_model or "
                "conventional_ts_transverse_model (a recognised deferred task is answered 'unsupported_task')."
            ),
            "maxLength": 64,
        },
        "observable": {"type": "string", "description": "Omit for a component task, whose observable is implied.", "maxLength": 64},
        "target": {
            "type": "object",
            "description": "A typed target union; see the tool description. Every ref is a public ref.",
        },
        "conditions": {
            "type": "object",
            "description": "temperature_domain_k {min,max}, energy_reference {zero,zpe}, environment, normalization, ...",
        },
        "population": {
            "type": "object",
            "description": (
                "{'kind': 'authorized_matching_models'} (default), {'kind': 'model_revisions', 'model_refs': ['smd_...', "
                "'sm_...']} or {'kind': 'release', 'release_ref': 'rel_...'} (not evaluated yet)."
            ),
        },
        "coverage_requirement": {"type": "string", "enum": list(COVERAGE), "default": "entire_requested_domain"},
        "administrative_policy": {"type": "string", "enum": list(ADMIN_POLICIES), "default": "default"},
        "result_mode": {"type": "string", "enum": list(MODES), "default": "all"},
        "min_review_status": {"type": "string", "description": "Optional review floor, applied on top of the profile's floor."},
        "profile": {"type": "string", "enum": list(PROFILES), "default": "exploratory"},
        **step_properties(MODEL_PROVENANCE_FILTERS, ELIGIBILITY_REVIEW_AND_FINDINGS),
    },
    "additionalProperties": False,
}

_BODY_OBJECTS = ("conditions", "population")
_BODY_ENUMS = (
    ("coverage_requirement", COVERAGE),
    ("administrative_policy", ADMIN_POLICIES),
    ("result_mode", MODES),
)


def _string(args: dict[str, Any], name: str, *, max_length: int) -> str:
    value = args[name]
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise invalid_input(f"{name} must be a non-empty string of at most {max_length} characters; got {value!r}")
    return value


def _target(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid_input("target must be an object: {'kind': ..., '<kind>_ref': ..., 'scope': {...}}")
    kind = value.get("kind")
    if kind not in TARGET_REFS:
        raise invalid_input(f"target.kind must be one of {sorted(TARGET_REFS)!r}; got {kind!r}")
    field, prefix = TARGET_REFS[kind]
    validate_path_handle(value.get(field), field_name=f"target.{field}", expected_prefix=prefix)
    if not isinstance(value.get("scope"), dict):
        raise invalid_input("target.scope must be an object naming the scope kind (atom, basin, ...)")
    return value


def run(client: TCKDBHttpClient, config: Config, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Validate inputs, POST the selection request, return the server response unchanged."""
    args = {k: v for k, v in dict(arguments or {}).items() if v is not None}

    rejected_int = sorted(_REJECTED_INTEGER_FIELDS & args.keys())
    if rejected_int:
        raise invalid_input(
            f"integer-id fields are not accepted by the MCP: {rejected_int!r}. "
            "Use the target and candidate public refs, not integer IDs."
        )
    rejected_bounds = sorted(_REJECTED_BOUND_FIELDS & args.keys())
    if rejected_bounds:
        raise invalid_input(
            f"{rejected_bounds!r} is not a request field: the engineering bounds are fixed by the server (which refuses "
            "above them with a coded 422), a selection is over the complete authorized population and never a page, and "
            "a caller cannot author a rule or a sort. This tool never caps, pages or chooses candidates itself."
        )
    unknown = sorted(args.keys() - set(INPUT_SCHEMA["properties"]))
    if unknown:
        raise invalid_input(f"unknown field(s): {unknown!r}")
    for required in INPUT_SCHEMA["required"]:
        if required not in args:
            raise invalid_input(f"{required} is required")

    body: dict[str, Any] = {"target": _target(args["target"])}
    intent = args["intent"]
    if intent not in INTENTS:
        raise invalid_input(f"intent must be one of {list(INTENTS)!r}; got {intent!r}")
    body["intent"] = intent
    body["task"] = _string(args, "task", max_length=64)
    if "observable" in args:
        body["observable"] = _string(args, "observable", max_length=64)
    for name in _BODY_OBJECTS:
        if name in args:
            if not isinstance(args[name], dict):
                raise invalid_input(f"{name} must be an object; got {type(args[name]).__name__}")
            body[name] = args[name]
    population = body.get("population")
    if isinstance(population, dict):
        for candidate in population.get("model_refs") or ():
            if not isinstance(candidate, str) or not candidate.startswith(("sm_", "smd_")):
                raise invalid_input(f"population.model_refs names public refs starting with 'sm_' or 'smd_'; got {candidate!r}")
    for name, allowed in _BODY_ENUMS:
        if name in args:
            if args[name] not in allowed:
                raise invalid_input(f"{name} must be one of {list(allowed)!r}; got {args[name]!r}")
            body[name] = args[name]
    if "min_review_status" in args:
        body["min_review_status"] = _string(args, "min_review_status", max_length=64)

    forward_steps(args, body, filters=MODEL_PROVENANCE_FILTERS, eligibility=ELIGIBILITY_REVIEW_AND_FINDINGS)

    profile = args.get("profile")
    if profile is not None and profile not in PROFILES:
        raise invalid_input(f"profile must be one of {list(PROFILES)!r}; got {profile!r}")

    url = client.scientific_url("/scientific/statmech/select")
    return client.post_json(url, body, params={"profile": profile})


__all__ = ["INPUT_SCHEMA", "TOOL_DESCRIPTION", "TOOL_NAME", "run"]
