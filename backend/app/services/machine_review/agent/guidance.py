"""An in-memory guidance index: TCKDB-authored entries, each under a unique ``source_key``, searched with BM25.

The index is built by the caller from its own text and holds no database or TCKDB imports.

ASCII only.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

#: Longest ``source_key``, the cap ``retrieval_sources`` items carry.
KEY_MAX_CHARS = 64

_BM25_K1 = 1.2
_BM25_B = 0.75
_KEY_BOOST = 1000.0
_CODE_BOOST = 500.0
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class GuidanceEntry:
    source_key: str
    kind: str
    title: str
    text: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


class GuidanceIndex:
    """Entries in corpus order; ``search`` ranks them and ``entries_for_codes`` looks them up by warning code."""

    def __init__(self, entries: Iterable[GuidanceEntry]) -> None:
        self._entries: tuple[GuidanceEntry, ...] = tuple(entries)
        seen: set[str] = set()
        for entry in self._entries:
            if not entry.source_key or len(entry.source_key) > KEY_MAX_CHARS:
                raise ValueError("A guidance entry needs a source_key of 1 to 64 characters.")
            if entry.source_key in seen:
                raise ValueError("Two guidance entries share a source_key.")
            if not entry.text.strip():
                raise ValueError("A guidance entry needs text.")
            seen.add(entry.source_key)
        self._counts = [Counter(_tokens(" ".join((e.source_key, *e.codes, e.title, e.text)))) for e in self._entries]
        self._lengths = [sum(c.values()) for c in self._counts]
        self._average = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        document_frequency: Counter[str] = Counter()
        for counts in self._counts:
            document_frequency.update(counts.keys())
        total = len(self._entries)
        self._idf = {
            term: math.log(1.0 + (total - n + 0.5) / (n + 0.5)) for term, n in document_frequency.items()
        }

    @property
    def entries(self) -> tuple[GuidanceEntry, ...]:
        return self._entries

    def search(self, query: str, kind: str | None = None, limit: int = 5) -> list[GuidanceEntry]:
        """Up to ``limit`` entries, best first. An exact key or code match ranks first; zero scores are left out."""
        terms = _tokens(query)
        needle = query.strip().lower()
        scored: list[tuple[float, int]] = []
        for position, entry in enumerate(self._entries):
            if kind is not None and entry.kind != kind:
                continue
            score = self._bm25(terms, position)
            key = entry.source_key.lower()
            if needle and needle in (key, key.split(":", 1)[-1]):
                score += _KEY_BOOST
            elif needle and needle in {code.lower() for code in entry.codes}:
                score += _CODE_BOOST
            if score > 0:
                scored.append((score, position))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [self._entries[position] for _, position in scored[: max(limit, 0)]]

    def entries_for_codes(self, codes: Iterable[str], limit: int) -> list[GuidanceEntry]:
        """For each code in order, its ``warning:`` entry and then the other entries that declare it."""
        picked: dict[str, GuidanceEntry] = {}
        for code in codes:
            own = [e for e in self._entries if e.source_key == f"warning:{code}"]
            declared = [e for e in self._entries if code in e.codes and e.source_key != f"warning:{code}"]
            for entry in (*own, *declared):
                picked.setdefault(entry.source_key, entry)
        return list(picked.values())[: max(limit, 0)]

    def _bm25(self, terms: list[str], position: int) -> float:
        counts, length = self._counts[position], self._lengths[position]
        score = 0.0
        for term in set(terms):
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            norm = 1.0 - _BM25_B + _BM25_B * (length / self._average if self._average else 1.0)
            score += self._idf[term] * frequency * (_BM25_K1 + 1.0) / (frequency + _BM25_K1 * norm)
        return score


__all__ = ["KEY_MAX_CHARS", "GuidanceEntry", "GuidanceIndex"]
