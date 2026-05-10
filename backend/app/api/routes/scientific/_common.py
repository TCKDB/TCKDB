"""Shared helpers for scientific route wrappers."""

from __future__ import annotations


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
