"""Tools the review loop offers the model: read-only, bounded, and limited to what the context pack shows.

``get_record`` returns the pack summary of a record the run offered, so the model can read one the size limit cut
from its context. It refuses every other pair, and a result never holds more than a summary does: no depositor
text and no id beyond ``record_type:record_ref``.

ASCII only: the tool schemas are runtime strings under ``app/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.llm_precheck.schemas import LLMRecordSummary
from app.services.machine_review.providers.prompt import compact

#: Largest tool result, in bytes of JSON text.
TOOL_RESULT_MAX_BYTES = 4096

STATUS_OK = "ok"
STATUS_REFUSED = "refused"
STATUS_NOT_AVAILABLE = "not_available"
STATUS_TRUNCATED = "truncated"
STATUS_INVALID_ARGUMENTS = "invalid_arguments"
STATUS_UNKNOWN_TOOL = "unknown_tool"
TOOL_STATUS_CODES = (
    STATUS_OK,
    STATUS_REFUSED,
    STATUS_NOT_AVAILABLE,
    STATUS_TRUNCATED,
    STATUS_INVALID_ARGUMENTS,
    STATUS_UNKNOWN_TOOL,
)

GET_RECORD = "get_record"

_GET_RECORD_DESCRIPTION = (
    "Read the stored summary of one record from offered_record_refs. Use it for a record whose summary is "
    "missing from records. Only pairs listed in offered_record_refs can be read."
)


class GetRecordArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: str = Field(min_length=1, max_length=64)
    record_ref: str = Field(min_length=1, max_length=256)


@dataclass(frozen=True)
class ToolOutcome:
    status_code: str
    content: str


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fixed(code: str) -> ToolOutcome:
    return ToolOutcome(code, _dump({"error": code}))


def _fits(value: dict[str, Any]) -> bool:
    return len(_dump(value).encode()) <= TOOL_RESULT_MAX_BYTES


def _truncate(payload: dict[str, Any]) -> dict[str, Any]:
    """Cut ``payload`` to the byte cap: trailing values first, then warnings, then findings."""
    payload = dict(payload)
    payload["truncated"] = True
    values = dict(payload.get("values", {}))
    while values and not _fits({**payload, "values": values}):
        values.popitem()
    if values:
        payload["values"] = values
    else:
        payload.pop("values", None)
    for key in ("deposit_warnings", "deterministic_findings"):
        items = list(payload.get(key, []))
        while items and not _fits({**payload, key: items}):
            items.pop()
        if items:
            payload[key] = items
        else:
            payload.pop(key, None)
    if not _fits(payload):
        payload = {k: payload[k] for k in ("record_type", "record_ref", "truncated") if k in payload}
    return payload


class ToolRegistry:
    """The tools of one run, bound to the refs it offered and the summaries of its records."""

    def __init__(
        self, offered: frozenset[tuple[str, str]], summaries: dict[tuple[str, str], LLMRecordSummary]
    ) -> None:
        self._offered = offered
        self._summaries = summaries

    def schemas(self) -> list[dict[str, Any]]:
        """The OpenAI ``tools`` list."""
        return [
            {
                "type": "function",
                "function": {
                    "name": GET_RECORD,
                    "description": _GET_RECORD_DESCRIPTION,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "record_type": {"type": "string"},
                            "record_ref": {"type": "string"},
                        },
                        "required": ["record_type", "record_ref"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def execute(self, name: str, arguments_text: str) -> ToolOutcome:
        if name != GET_RECORD:
            return _fixed(STATUS_UNKNOWN_TOOL)
        try:
            args = GetRecordArgs.model_validate_json(arguments_text)
        except (ValidationError, ValueError):
            return _fixed(STATUS_INVALID_ARGUMENTS)
        pair = (args.record_type, args.record_ref)
        if pair not in self._offered:
            return _fixed(STATUS_REFUSED)
        summary = self._summaries.get(pair)
        if summary is None:
            return _fixed(STATUS_NOT_AVAILABLE)
        payload = compact(summary.model_dump(mode="json"))
        if _fits(payload):
            return ToolOutcome(STATUS_OK, _dump(payload))
        return ToolOutcome(STATUS_TRUNCATED, _dump(_truncate(payload)))


__all__ = [
    "GET_RECORD",
    "TOOL_RESULT_MAX_BYTES",
    "TOOL_STATUS_CODES",
    "GetRecordArgs",
    "ToolOutcome",
    "ToolRegistry",
]
