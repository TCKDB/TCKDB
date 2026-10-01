"""Columns a record's digest ignores while they hold the value rows had before.

Two digests are built from *every* mapped column of a scientific row: the
advisory-consistency input hash (``consistency.core.snapshot``) and the
reproducibility-assessment target snapshot
(``reproducibility_rubric._mapped_columns``). A stored review or assessment is
current only while that digest is unchanged, so adding a column to such a row
would make every stored review of every existing row stale, even when the new
column changes nothing about that row.

A column listed here is left out of those digests while it holds the value
every pre-existing row implicitly had. A row that departs from it (a rate
deposited at a reference temperature other than 1 K) includes the column, so
its digest is different, which is correct: the column then changes what the
row means.
"""

from __future__ import annotations

from typing import Any

#: ``(table, column) -> the value every row had before the column existed``.
UNCHANGED_DEFAULTS: dict[tuple[str, str], Any] = {
    # kinetics.t0_k: a rate stored before the column existed was A * T**n,
    # which is T0 = 1 K (#620).
    ("kinetics", "t0_k"): 1.0,
    # level_of_theory.core_treatment: a level stored before the column existed
    # did not state it, which is NULL, and a NULL is not part of its hash
    # either (ADR 0021). A level that states frozen_core or all_electron
    # includes the column, so its digest differs, as its identity does.
    ("level_of_theory", "core_treatment"): None,
}


def is_unchanged_default(table: str, column: str, value: Any) -> bool:
    """True when ``value`` is the pre-existing default of a listed column.

    ``None`` counts: a transient row that has not been flushed has no value
    yet, and is what a row without the column amounted to.
    """
    key = (table, column)
    if key not in UNCHANGED_DEFAULTS:
        return False
    return value is None or value == UNCHANGED_DEFAULTS[key]
