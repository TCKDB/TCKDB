"""The deletion inventory in :mod:`app.services.record_deletion` covers every foreign key it has to.

A foreign key into a deletable record's table, or into a table one owns, is either the key a row is owned through, a
link between two rows of one record, or a citation. A new one that is none of these fails here: unclassified, it would
either make the delete fail on a foreign-key error or, worse, be silently skipped by the citation check.
"""

from __future__ import annotations

from sqlalchemy import Enum as SAEnum
from sqlalchemy import text

from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.db.models.common import SubmissionRecordType
from app.services.record_deletion import (
    CITATIONS,
    DELETABLE_RECORD_TYPES,
    INTERNAL_LINKS,
    OVERLAY_ROWS,
    OWNED_ROWS,
    POLYMORPHIC_NOT_DELETED,
    UNADDRESSABLE_ROOTS,
)

_TABLES = Base.metadata.tables


def _foreign_keys() -> set[tuple[str, frozenset[str], str]]:
    """Every foreign key in the ORM metadata, as ``(table, its columns, referred table)``."""
    return {
        (table.name, frozenset(column.name for column in constraint.columns), constraint.referred_table.name)
        for table in Base.metadata.sorted_tables
        for constraint in table.foreign_key_constraints
    }


def _guarded_tables() -> set[str]:
    owned = {owned.table for rows in OWNED_ROWS.values() for owned in rows}
    return owned | {record_type.value for record_type in DELETABLE_RECORD_TYPES}


def _classified(table: str, columns: frozenset[str], referred: str) -> bool:
    if any(table == o.table and o.owner and o.owner[1] == referred and o.owner[0] in columns for r in OWNED_ROWS.values() for o in r):
        return True
    if any((table, column, referred) in INTERNAL_LINKS for column in columns):
        return True
    return any(c.table == table and c.cites == referred and c.column in columns for c in CITATIONS)


def test_every_foreign_key_into_a_deletable_record_is_classified() -> None:
    guarded = _guarded_tables()
    keys = [key for key in _foreign_keys() if key[2] in guarded]
    assert len(keys) > 100, "the scan must see the foreign keys; a near-empty set means it is reading nothing"
    unclassified = sorted(
        f"{table}({', '.join(sorted(columns))}) -> {referred}"
        for table, columns, referred in keys
        if not _classified(table, columns, referred)
    )
    assert unclassified == []


def test_the_classifier_can_say_no() -> None:
    assert not _classified("thermo", frozenset({"species_entry_id"}), "kinetics")
    assert _classified("kinetics_source_calculation", frozenset({"calculation_id"}), "calculation")


def test_every_inventory_entry_names_a_real_column() -> None:
    names: list[tuple[str, str]] = []
    for rows in OWNED_ROWS.values():
        names.extend((owned.table, owned.owner[0]) for owned in rows if owned.owner)
    names.extend((citation.table, citation.column) for citation in CITATIONS)
    names.extend((table, column) for table, column, _referred in INTERNAL_LINKS)
    missing = sorted(f"{table}.{column}" for table, column in names if table not in _TABLES or column not in _TABLES[table].c)
    assert missing == []
    stale = sorted(
        f"{citation.table}.{citation.column} -> {citation.cites}"
        for citation in CITATIONS
        if not any(key[0] == citation.table and citation.column in key[1] and key[2] == citation.cites for key in _foreign_keys())
    )
    assert stale == []


def test_every_table_keyed_by_record_type_is_deleted_or_deliberately_kept() -> None:
    polymorphic = {
        table.name
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, SAEnum) and column.type.enum_class is SubmissionRecordType
    }
    direct_overlays = {overlay.table for overlay in OVERLAY_ROWS if overlay.parent is None}
    assert polymorphic, "the scan must find the record_type columns"
    assert polymorphic - direct_overlays - POLYMORPHIC_NOT_DELETED == set()
    assert direct_overlays <= polymorphic


def test_every_accepted_science_root_is_deletable_or_named_as_unaddressable(db_session) -> None:
    roots = set(
        db_session.scalars(
            text(
                "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_proc p ON p.oid = t.tgfoid WHERE NOT t.tgisinternal AND p.proname = 'tckdb_guard_accepted_root'"
            )
        )
    )
    assert roots
    assert roots == {record_type.value for record_type in DELETABLE_RECORD_TYPES} | set(UNADDRESSABLE_ROOTS)


def test_every_guarded_child_table_is_owned_by_its_root_or_cites_it(db_session) -> None:
    """A table the database guards as a root's child either belongs to that root here or is listed as citing it."""
    rows = db_session.execute(
        text(
            "SELECT c.relname, pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_proc p ON p.oid = t.tgfoid WHERE NOT t.tgisinternal "
            "AND p.proname IN ('tckdb_guard_accepted_child', 'tckdb_guard_accepted_via_child')"
        )
    ).all()
    assert rows
    owned = {(record_type.value, o.table) for record_type, r in OWNED_ROWS.items() for o in r}
    citing = {(c.cites, c.table) for c in CITATIONS}
    uncovered = []
    for table, definition in rows:
        root = definition[definition.rindex("(") + 1 :].split(",")[0].strip(" '")
        if root in UNADDRESSABLE_ROOTS:
            continue
        if (root, table) not in owned and (root, table) not in citing:
            uncovered.append(f"{table} guarded as a child of {root}")
    assert sorted(uncovered) == []
