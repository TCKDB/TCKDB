"""A record as the evidence checks should see it when some of its source calculations are unavailable.

Withheld == unknown. A source calculation the reader cannot open (withheld under ``curated`` because it is rejected
or deprecated, or naming no row) is treated by
a selection exactly like a calculation that was never linked: the trust evidence checks of a thermo or kinetics record
must not count it, and must not be satisfied or failed by what it holds. The checks run over the ORM record and read
``source_calculations`` and everything else by attribute, so a thin view that answers ``source_calculations`` without the
unavailable links and every other attribute from the record is all they need. The record itself is never changed.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any


class WithoutUnavailableSources:
    """``record`` with its source-calculation links to ``unavailable`` calculation refs left out."""

    def __init__(self, record: Any, unavailable: Collection[str]) -> None:
        self._record = record
        self._unavailable = frozenset(unavailable)

    @property
    def source_calculations(self) -> list[Any]:
        return [
            link
            for link in self._record.source_calculations
            if link.calculation is None or link.calculation.public_ref not in self._unavailable
        ]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._record, name)


def evidence_subject(record: Any, unavailable: Collection[str]) -> Any:
    """``record`` itself when nothing is unavailable (the exploratory path is untouched), else the view."""
    return WithoutUnavailableSources(record, unavailable) if unavailable else record
