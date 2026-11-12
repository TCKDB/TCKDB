"""Every foreign key into an accepted-science root is guarded, or deliberately is not.

The guards are read from ``pg_trigger``, the foreign keys from the ORM metadata, so a table added with an ownership key
to a root and no trigger fails here. A guarded column must be NOT NULL: a column the row cannot exist without is an
ownership key, and a nullable one is a provenance pointer that must not be guarded.
"""

from __future__ import annotations

import re

from sqlalchemy import text

from app.db import models as _models  # noqa: F401
from app.db.base import Base

#: NOT NULL foreign keys into a root that cite accepted science rather than belong to it: the calculation a value was
#: derived from, a member or input record, a transition state a rate was applied to. A write may cite accepted science,
#: so these carry no ownership guard.
_CITATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("calc_composite_input", "input_calculation_id"),
        ("conformer_group_merge_pending", "conformer_observation_id"),
        ("kinetics_interpretation_assignment", "statmech_id"),
        ("kinetics_source_calculation", "calculation_id"),
        ("kinetics_tunneling_application", "transition_state_entry_id"),
        ("network_solve_channel_barrier", "transition_state_entry_id"),
        ("network_solve_source_calculation", "calculation_id"),
        ("network_solve_state_energy_source", "calculation_id"),
        ("software_version_attestation_calculation", "calculation_id"),
        ("statmech_model_member", "member_statmech_id"),
        ("statmech_source_calculation", "calculation_id"),
        ("structure_determination_source", "calculation_id"),
        ("thermo_source_calculation", "calculation_id"),
        ("transition_state_validation_energy", "source_calculation_id"),
        ("transport_source_calculation", "calculation_id"),
    }
)

#: Guarded columns that are nullable because the row has exactly one of several alternative owners.
_ALTERNATIVE_OWNERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("structure_determination", "conformer_observation_id"),
        ("structure_determination", "transition_state_entry_id"),
        ("structure_determination_source", "conformer_observation_id"),
        ("structure_determination_source", "transition_state_entry_id"),
    }
)


def _root_tables(db_session) -> set[str]:
    return set(
        db_session.scalars(
            text(
                "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid WHERE NOT t.tgisinternal AND p.proname = 'tckdb_guard_accepted_root'"
            )
        )
    )


def _guard_arguments(db_session) -> dict[str, list[list[str]]]:
    """Per table, the argument lists of its child-guard triggers."""
    rows = db_session.execute(
        text(
            "SELECT c.relname, pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_proc p ON p.oid = t.tgfoid WHERE NOT t.tgisinternal "
            "AND p.proname IN ('tckdb_guard_accepted_child', 'tckdb_guard_accepted_via_child')"
        )
    ).all()
    found: dict[str, list[list[str]]] = {}
    for table, definition in rows:
        arguments = definition[definition.rindex("(") + 1 : definition.rindex(")")]
        found.setdefault(table, []).append(re.findall(r"'([^']*)'", arguments))
    return found


def test_the_scan_found_the_guards(db_session) -> None:
    roots = _root_tables(db_session)
    guards = _guard_arguments(db_session)
    assert {"calculation", "thermo", "kinetics"} <= roots, roots
    assert len(guards) >= 40, sorted(guards)


def test_every_guarded_column_is_not_null(db_session) -> None:
    tables = Base.metadata.tables
    offenders = []
    for table, argument_lists in _guard_arguments(db_session).items():
        for arguments in argument_lists:
            for argument in arguments:
                if (
                    argument in tables[table].c
                    and tables[table].c[argument].nullable
                    and (table, argument) not in _ALTERNATIVE_OWNERS
                ):
                    offenders.append((table, argument))
    assert not offenders, f"guarded columns that are nullable (a nullable column is a citation, not ownership): {offenders}"


def test_every_ownership_key_into_a_root_is_guarded(db_session) -> None:
    roots = _root_tables(db_session)
    guards = _guard_arguments(db_session)
    unguarded = []
    for name, table in Base.metadata.tables.items():
        if name in roots:
            continue
        guarded_columns = {argument for arguments in guards.get(name, []) for argument in arguments}
        for column in table.c:
            targets = {fk.column.table.name for fk in column.foreign_keys}
            if not targets & roots or column.nullable:
                continue
            if column.name not in guarded_columns and (name, column.name) not in _CITATIONS:
                unguarded.append((name, column.name))
    assert not unguarded, f"NOT NULL foreign keys into an accepted-science root with no guard: {sorted(unguarded)}"
