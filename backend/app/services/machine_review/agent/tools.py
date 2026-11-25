"""Tools the review loop offers the model: read-only, bounded, and limited to what the context pack shows.

``get_record`` returns the pack summary of a record the run offered, so the model can read one the size limit cut
from its context. It refuses every other pair, and a result never holds more than a summary does: no depositor
text and no id beyond ``record_type:record_ref``.

``search_guidance`` searches the guidance index the caller passes in and returns at most five entries, each under a
``source_key``. Entries of kind ``lesson`` come from a second, per-job index and are notes written by curators, not
TCKDB guidance. The registry records every key it delivers; a review may cite only those.

ASCII only: the tool schemas are runtime strings under ``app/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.llm_precheck.schemas import LLMRecordSummary
from app.services.machine_review.agent.guidance import GuidanceEntry, GuidanceIndex
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


SEARCH_GUIDANCE = "search_guidance"

#: Most results one search or one up-front guidance message returns.
GUIDANCE_RESULT_LIMIT = 5

#: Most lessons one unfiltered search returns, so TCKDB guidance keeps the remaining slots.
LESSON_RESULT_LIMIT = 3

LESSON_KIND = "lesson"

#: Longest entry text kept in a result, in characters.
GUIDANCE_TEXT_MAX_CHARS = 1000

_SEARCH_GUIDANCE_DESCRIPTION = (
    "Search TCKDB's own guidance: the sentences it shows for deposit warnings, its scientific checks and their "
    "thresholds. Results with kind 'lesson' are notes written by curators, not TCKDB's own guidance; weigh them "
    "as a colleague's advice. Returns up to 5 entries, each with a source_key. List a source_key in "
    "retrieval_sources only if a search returned it."
)

GUIDANCE_PREAMBLE = (
    "TCKDB guidance for the deposit warnings on this submission. List a source_key in retrieval_sources only if "
    "it appears here or in a later search result."
)


class GetRecordArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: str = Field(min_length=1, max_length=64)
    record_ref: str = Field(min_length=1, max_length=256)


#: Finding-category tokens; a test pins them to the review vocabulary.
FindingCategory = Literal[
    "provenance",
    "units",
    "geometry",
    "kinetics",
    "thermo",
    "statmech",
    "transport",
    "transition_state_validation",
    "calculation_parameters",
    "consistency",
    "schema_gap",
]

FINDING_CATEGORIES = get_args(FindingCategory)


class SearchGuidanceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=200)
    kind: Literal["deposit_warning", "check", "threshold", "lesson"] | None = None
    category: FindingCategory | None = None


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


def _value_group(key: str, keys: list[str]) -> str:
    """The key a value belongs with: a unit or qualifier column (``a_units``) shares its number's group (``a``)."""
    if key.startswith("scalar_"):
        return "scalar"
    roots = [other for other in keys if other != key and key.startswith(other + "_")]
    return min(roots, key=len) if roots else key


def _truncate(payload: dict[str, Any]) -> dict[str, Any]:
    """Cut ``payload`` to the byte cap: warnings first, then values a group at a time, then findings.

    A number is dropped together with its unit and qualifier columns, never apart from them.
    """
    payload = dict(payload)
    payload["truncated"] = True
    items = list(payload.get("deposit_warnings", []))
    while items and not _fits({**payload, "deposit_warnings": items}):
        items.pop()
    if items:
        payload["deposit_warnings"] = items
    else:
        payload.pop("deposit_warnings", None)
    values = dict(payload.get("values", {}))
    keys = list(values)
    while values and not _fits({**payload, "values": values}):
        last = _value_group(next(reversed(values)), keys)
        values = {k: v for k, v in values.items() if _value_group(k, keys) != last}
    if values:
        payload["values"] = values
    else:
        payload.pop("values", None)
    items = list(payload.get("deterministic_findings", []))
    while items and not _fits({**payload, "deterministic_findings": items}):
        items.pop()
    if items:
        payload["deterministic_findings"] = items
    else:
        payload.pop("deterministic_findings", None)
    if not _fits(payload):
        payload = {k: payload[k] for k in ("record_type", "record_ref", "truncated") if k in payload}
    return payload


def _cut_text(text: str) -> str:
    """The text cut at the last space within the cap (or hard when that keeps under half), marked with '...'."""
    head = text[: GUIDANCE_TEXT_MAX_CHARS - 3]
    space = head.rfind(" ")
    if space > len(head) // 2:
        head = head[:space].rstrip(",;:.")
    return head + "..."


def _guidance_item(entry: GuidanceEntry) -> dict[str, Any]:
    text = entry.text
    if len(text) > GUIDANCE_TEXT_MAX_CHARS:
        text = _cut_text(text)
    item: dict[str, Any] = {"source_key": entry.source_key, "kind": entry.kind, "title": entry.title, "text": text}
    if entry.codes:
        item["codes"] = list(entry.codes)
    if entry.fields:
        item["fields"] = dict(entry.fields)
    return item


def _guidance_payload(entries: list[GuidanceEntry]) -> tuple[dict[str, Any], bool]:
    """The entries as a result that fits the byte cap, and whether any text or entry was cut to fit."""
    items = [_guidance_item(entry) for entry in entries]
    cut = any(len(entry.text) > GUIDANCE_TEXT_MAX_CHARS for entry in entries)
    while items and not _fits({"results": items}):
        items.pop()
        cut = True
    return {"results": items}, cut


class ToolRegistry:
    """The tools of one run, bound to the refs it offered, the summaries of its records and its guidance index."""

    def __init__(
        self,
        offered: frozenset[tuple[str, str]],
        summaries: dict[tuple[str, str], LLMRecordSummary],
        *,
        guidance: GuidanceIndex | None = None,
        guidance_seed: tuple[str, ...] = (),
        lessons: GuidanceIndex | None = None,
    ) -> None:
        self._offered = offered
        self._summaries = summaries
        self._guidance = guidance
        self._guidance_seed = guidance_seed
        self._lessons = lessons
        self._returned: set[str] = set()

    @property
    def returned_sources(self) -> frozenset[str]:
        """The ``source_key`` values this run delivered to the model."""
        return frozenset(self._returned)

    @property
    def _searchable(self) -> bool:
        return self._guidance is not None or self._lessons is not None

    def schemas(self) -> list[dict[str, Any]]:
        """The OpenAI ``tools`` list."""
        tools = [
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
        if self._searchable:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": SEARCH_GUIDANCE,
                        "description": _SEARCH_GUIDANCE_DESCRIPTION,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["deposit_warning", "check", "threshold", LESSON_KIND],
                                },
                                "category": {"type": "string", "enum": list(FINDING_CATEGORIES)},
                            },
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return tools

    def known_name(self, name: str) -> str | None:
        """``name`` if this registry offers it, else ``None``; only this is safe to store as a name."""
        offered = (GET_RECORD, SEARCH_GUIDANCE) if self._searchable else (GET_RECORD,)
        return name if name in offered else None

    def execute(self, name: str, arguments_text: str) -> ToolOutcome:
        if name == SEARCH_GUIDANCE and self._searchable:
            return self._search_guidance(arguments_text)
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

    def _deliver(self, entries: list[GuidanceEntry]) -> ToolOutcome:
        payload, cut = _guidance_payload(entries)
        self._returned.update(item["source_key"] for item in payload["results"])
        return ToolOutcome(STATUS_TRUNCATED if cut else STATUS_OK, _dump(payload))

    def _search_guidance(self, arguments_text: str) -> ToolOutcome:
        try:
            args = SearchGuidanceArgs.model_validate_json(arguments_text)
        except (ValidationError, ValueError):
            return _fixed(STATUS_INVALID_ARGUMENTS)
        limit = GUIDANCE_RESULT_LIMIT
        lessons: list[GuidanceEntry] = []
        if self._lessons is not None and args.kind in (None, LESSON_KIND):
            lessons = self._lessons.search(
                args.query,
                limit=limit if args.kind == LESSON_KIND else LESSON_RESULT_LIMIT,
                category=args.category,
            )
        guided: list[GuidanceEntry] = []
        if self._guidance is not None and args.kind != LESSON_KIND:
            guided = self._guidance.search(
                args.query, kind=args.kind, limit=limit - len(lessons), category=args.category
            )
        return self._deliver([*lessons, *guided])

    def prefetch_guidance_message(self) -> dict[str, str] | None:
        """A user message with the guidance for the seeded warning codes, for a server that takes no tools."""
        if self._guidance is None or not self._guidance_seed:
            return None
        entries = self._guidance.entries_for_codes(self._guidance_seed, GUIDANCE_RESULT_LIMIT)
        if not entries:
            return None
        outcome = self._deliver(entries)
        return {"role": "user", "content": f"{GUIDANCE_PREAMBLE}\n{outcome.content}"}


__all__ = [
    "GET_RECORD",
    "GUIDANCE_PREAMBLE",
    "LESSON_KIND",
    "LESSON_RESULT_LIMIT",
    "SEARCH_GUIDANCE",
    "TOOL_RESULT_MAX_BYTES",
    "TOOL_STATUS_CODES",
    "GetRecordArgs",
    "SearchGuidanceArgs",
    "ToolOutcome",
    "ToolRegistry",
]
