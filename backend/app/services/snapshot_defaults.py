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
    # thermo.thermodynamic_target_kind / target_conformer_group_id /
    # protocol_declaration: a thermo row stored before the columns existed
    # declared no target and no protocol, which is NULL. A record that states
    # one includes the column, so its digest differs, as its meaning does: the
    # declaration is part of what the row claims. Adding the columns restales
    # no stored review of any existing row.
    ("thermo", "thermodynamic_target_kind"): None,
    ("thermo", "target_conformer_group_id"): None,
    ("thermo", "protocol_declaration"): None,
    # kinetics.determination_id / representation_role / applicability_declaration /
    # protocol_declaration: a kinetics row stored before the columns existed declared no
    # determination, no role, no applicability and no protocol, which is NULL. A record that
    # states one includes the column, so its digest differs, as its meaning does: unlike the
    # thermo declarations these are *not* kept out of the consistency hash, so a stored
    # advisory finding goes stale when a record's declared meaning changes. The linked
    # determination's own content is added to both snapshots only when a record has one
    # (``consistency.kinetics`` and ``reproducibility_rubric``), so a legacy record gains no
    # key. Adding the columns restales no stored review or assessment of any existing row.
    ("kinetics", "determination_id"): None,
    ("kinetics", "representation_role"): None,
    ("kinetics", "applicability_declaration"): None,
    ("kinetics", "protocol_declaration"): None,
    # kinetics.energy_level_of_theory_id: a kinetics row stored before the column existed
    # declared no energy level, which is NULL. A record that declares one includes the
    # column in the reproducibility snapshot (it is part of what the row claims); the
    # consistency hash leaves it out (``KINETICS_HASH_EXCLUDED_COLUMNS``). Adding the
    # column restales no stored review or assessment of any existing row.
    ("kinetics", "energy_level_of_theory_id"): None,
    # network_solve.target_declaration / protocol_declaration / validation_declaration and
    # network_kinetics.determination_id / representation_role / representation_declaration:
    # a solve or fit stored before the columns existed declared nothing, which is NULL. One that
    # states a declaration includes the column, so its digest differs, as its meaning does. The
    # solve's determinations (and which fits belong to them) are added to the reproducibility
    # snapshot only when it has some (``reproducibility_rubric``), so a legacy solve gains no key.
    ("network_solve", "target_declaration"): None,
    ("network_solve", "protocol_declaration"): None,
    ("network_solve", "validation_declaration"): None,
    ("network_kinetics", "determination_id"): None,
    ("network_kinetics", "representation_role"): None,
    ("network_kinetics", "representation_declaration"): None,
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
