"""The two request steps every ``tckdb_select_*`` tool shares: the caller's filters and the eligibility defaults.

A select call is: user filters, then requested eligibility, then the policy (there are no built-in method rankings), then
the result. A tool offers the filters of the matching search and the eligibility defaults its record kind has; the server
refuses anything else (an unknown filter is a 422, never ignored), and this module refuses it first with a teaching error.

Nothing here applies a filter or lifts a default: the tool forwards what the agent stated and returns the server's answer,
whose ``filters``, ``eligibility`` and ``funnel`` fields say what each step did. The read profile's review floor
(``curated``) is not an eligibility default and no request lifts it.
"""

from __future__ import annotations

from typing import Any

from ..errors import invalid_input

PROVENANCE_FILTERS = (
    "method", "basis", "software", "software_version", "workflow_tool", "workflow_tool_version",
)
#: thermo and kinetics: the per-entry reads' filters that are plain text. ONE source calculation has to match every
#: provenance field supplied. ``method_family`` and ``dft_rung_min`` / ``dft_rung_max`` (a classification of the method,
#: not an accuracy measure) are not text and are not offered by this tool yet.
LEVEL_FILTERS = ("model_kind", "scientific_origin", "level_of_theory_ref", *PROVENANCE_FILTERS)
#: network and statmech: the model form and the provenance fields.
MODEL_PROVENANCE_FILTERS = ("model_kind", *PROVENANCE_FILTERS)

ELIGIBILITY_ALL = ("include_rejected", "include_deprecated", "include_hard_failed_evidence", "include_confirmed_findings")
ELIGIBILITY_REVIEW = ("include_rejected", "include_deprecated")
ELIGIBILITY_REVIEW_AND_FINDINGS = ("include_rejected", "include_deprecated", "include_confirmed_findings")

_ELIGIBILITY_TEXT = {
    "include_rejected": "let a rejected record compete (never under the curated profile)",
    "include_deprecated": "let a deprecated record compete (never under the curated profile)",
    "include_hard_failed_evidence": "let a record whose evidence rubric hard-fails compete; the failure stays disclosed",
    "include_confirmed_findings": "let a record compete although a confirmed structure finding invalidates a source; the finding stays disclosed",
}


def step_properties(filters: tuple[str, ...], eligibility: tuple[str, ...]) -> dict[str, Any]:
    """The ``filters`` and ``eligibility`` input-schema properties of a tool."""
    return {
        "filters": {
            "type": "object",
            "description": (
                "Narrow the candidates by facts before eligibility, with the matching search's own filters. A filter "
                "excludes on a fact and ranks nothing; the answer's 'filters' and 'funnel' say how many it removed."
            ),
            "properties": {name: {"type": "string", "maxLength": 256} for name in filters},
            "additionalProperties": False,
        },
        "eligibility": {
            "type": "object",
            "description": (
                "Lift named eligibility defaults; every default is applied unless set true here, and the answer's "
                "'eligibility' lists each (name, applied, and whether that was the default, the request or the read profile)."
            ),
            "properties": {name: {"type": "boolean", "description": _ELIGIBILITY_TEXT[name]} for name in eligibility},
            "additionalProperties": False,
        },
    }


def forward_steps(
    args: dict[str, Any], body: dict[str, Any], *, filters: tuple[str, ...], eligibility: tuple[str, ...]
) -> None:
    """Validate the stated ``filters`` and ``eligibility`` and copy them into the request body unchanged."""
    if args.get("filters") is not None:
        value = args["filters"]
        if not isinstance(value, dict):
            raise invalid_input(f"filters must be an object; got {type(value).__name__}")
        unknown = sorted(value.keys() - set(filters))
        if unknown:
            raise invalid_input(f"unknown filter(s) {unknown!r}; this tool offers {list(filters)!r}")
        for name, item in value.items():
            if not isinstance(item, str) or not item:
                raise invalid_input(f"filters.{name} must be a non-empty string; got {item!r}")
        body["filters"] = dict(value)
    if args.get("eligibility") is not None:
        value = args["eligibility"]
        if not isinstance(value, dict):
            raise invalid_input(f"eligibility must be an object; got {type(value).__name__}")
        unknown = sorted(value.keys() - set(eligibility))
        if unknown:
            raise invalid_input(f"unknown eligibility default(s) {unknown!r}; this tool offers {list(eligibility)!r}")
        for name, item in value.items():
            if not isinstance(item, bool):
                raise invalid_input(f"eligibility.{name} must be true or false; got {item!r}")
        body["eligibility"] = dict(value)
