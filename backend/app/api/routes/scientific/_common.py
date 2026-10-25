"""Shared helpers for scientific route wrappers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from app.schemas.reads.scientific_common import CURSOR_DESCRIPTION, MAX_CURSOR_LENGTH

#: ``cursor=`` on a list route: the stable traversal. The bound and the description are the ones the request models use.
CursorQuery = Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH, description=CURSOR_DESCRIPTION)]

#: What ``offset=`` is for, said on every route that takes it.
OFFSET_DESCRIPTION = (
    "Random access ('page N of M'). Deterministic only for a static corpus: an upload or a review change between pages "
    "can skip or repeat a record. For a stable traversal follow next_cursor instead (cursor=). Cannot be combined "
    "with cursor."
)


def parse_include(values: list[str] | None) -> list[str]:
    """Normalize ``include=`` query values into a flat list of tokens.

    Supports both repeated (``?include=a&include=b``) and comma-separated
    (``?include=a,b``) encodings. Empty strings are dropped; the order of
    the first occurrence is preserved (mostly for stable OpenAPI output —
    the service deduplicates internally).
    """
    if not values:
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    for raw in values:
        for token in raw.split(","):
            t = token.strip()
            if t and t not in seen_set:
                seen_set.add(t)
                seen.append(t)
    return seen
