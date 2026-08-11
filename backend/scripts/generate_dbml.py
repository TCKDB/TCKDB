#!/usr/bin/env python
"""Generate schema.dbml from SQLAlchemy model metadata.

Usage:
    conda run -n tckdb_env python -m scripts.generate_dbml
    conda run -n tckdb_env python -m scripts.generate_dbml --check

Writes backend/schema.dbml. ``--check`` writes nothing and exits 1 if the
committed file does not match what the models render, printing the diff.

Why ``--check`` exists
----------------------
Nothing verified that the committed document described the live models.
``tests/scripts/test_generate_dbml.py`` tested this generator's *rendering
logic* -- given a table with these constraints, does it emit the right lines --
which a stale file passes trivially, because it is never read. So
``HessianDetectionContext.reclaim_restore`` was added to
``app/db/models/common.py`` and was absent from ``backend/schema.dbml`` for a
full day, and the document said, confidently, that the enum did not have that
member. A schema document that can be wrong without anything noticing is worse
than no document: people trust it.

``tests/scripts/test_generate_dbml.py::test_committed_schema_dbml_is_in_sync``
is what makes the check a gate rather than a habit.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint

# Ensure repo root is on sys.path so `app` is importable
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# E402: the `app` imports must follow the sys.path bootstrap above — run
# directly or as `python -m scripts.generate_dbml` without PYTHONPATH,
# `app` is only importable once REPO_ROOT is on sys.path.
# Load all models so metadata is populated
import app.db.models  # noqa: F401,E402
from app.db.base import Base  # noqa: E402

OUTPUT = REPO_ROOT / "schema.dbml"


# ---------------------------------------------------------------------------
# Deterministic ordering helpers
#
# SQLAlchemy exposes `Table.indexes`, `Table.constraints`, and
# `Table.foreign_key_constraints` as plain Python `set`s, whose iteration
# order is not guaranteed stable across interpreter runs (hash randomization
# / insertion-order artifacts of reflection). Every place we iterate one of
# these must sort first, with a tiebreak key that can never itself tie, so
# regenerating on an unchanged schema always produces byte-identical output.
# `Table.columns` is an ordered `ColumnCollection` (declaration order) and
# does not need this treatment.
# ---------------------------------------------------------------------------


def _sorted_tables(metadata) -> list:
    """Tables in a stable, name-sorted order (independent of import/declaration order)."""
    return sorted(metadata.tables.values(), key=lambda t: t.name)


def _sorted_indexes(table) -> list:
    """`table.indexes` (a set) sorted by name, then by column list as a tiebreak."""
    return sorted(
        table.indexes,
        key=lambda idx: (idx.name or "", tuple(c.name for c in idx.columns)),
    )


def _sorted_constraints(table, constraint_type) -> list:
    """`table.constraints` (a set) filtered to `constraint_type`, sorted deterministically."""
    matching = [c for c in table.constraints if isinstance(c, constraint_type)]
    if constraint_type is CheckConstraint:
        return sorted(matching, key=lambda c: (c.name or "", str(c.sqltext)))
    return sorted(
        matching,
        key=lambda c: (c.name or "", tuple(col.name for col in c.columns)),
    )


def _sorted_foreign_key_constraints(table) -> list:
    """`table.foreign_key_constraints` (a set) sorted deterministically."""
    return sorted(
        table.foreign_key_constraints,
        key=lambda fkc: (
            fkc.name or "",
            tuple(c.name for c in fkc.columns),
            tuple(e.column.table.name for e in fkc.elements),
            tuple(e.column.name for e in fkc.elements),
        ),
    )


def _sorted_foreign_keys(col) -> list:
    """`col.foreign_keys` (a set) sorted deterministically by referenced target."""
    return sorted(
        col.foreign_keys,
        key=lambda fk: (fk.column.table.name, fk.column.name),
    )


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

_SA_TYPE_MAP = {
    "BIGINT": "bigint",
    "INTEGER": "int",
    "SMALLINT": "smallint",
    "DOUBLE PRECISION": "double",
    "DOUBLE_PRECISION": "double",
    "FLOAT": "double",
    "BOOLEAN": "boolean",
    "TEXT": "text",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "DATETIME": "timestamp",
}


def _col_type_str(col) -> str:
    """Map a SQLAlchemy column type to a DBML type string."""
    sa_type = col.type

    # Custom RDKit mol type
    type_name = type(sa_type).__name__
    if type_name == "RDKitMol":
        return "mol"

    # PostgreSQL enum (or SA Enum wrapper)
    if hasattr(sa_type, "enums") or hasattr(sa_type, "enum_class"):
        enum_class = getattr(sa_type, "enum_class", None)
        if enum_class is not None:
            return enum_class.__name__
        name = getattr(sa_type, "name", None)
        if name:
            return name
        return "text"

    compile_name = type(sa_type).__name__.upper()

    # CHAR / VARCHAR with length
    if compile_name in ("CHAR", "VARCHAR"):
        length = getattr(sa_type, "length", None)
        if length:
            return f"varchar({length})"
        return "text"

    # DateTime → timestamp
    if compile_name in ("DATETIME", "TIMESTAMP"):
        return "timestamp"

    # Date (not DateTime)
    if compile_name == "DATE":
        return "date"

    # Standard type mapping
    for key, dbml in _SA_TYPE_MAP.items():
        if key in compile_name:
            return dbml

    # Fallback
    return compile_name.lower()


# ---------------------------------------------------------------------------
# Enum extraction
# ---------------------------------------------------------------------------


def _collect_enums(metadata) -> dict[str, list[str]]:
    """Collect all enum types referenced by columns in the metadata."""
    enums: dict[str, list[str]] = {}
    for table in _sorted_tables(metadata):
        for col in table.columns:
            sa_type = col.type
            enum_class = getattr(sa_type, "enum_class", None)
            if enum_class is not None:
                name = enum_class.__name__
                if name not in enums:
                    enums[name] = [member.value for member in enum_class]
            elif hasattr(sa_type, "enums") and hasattr(sa_type, "name"):
                name = sa_type.name
                if name and name not in enums:
                    enums[name] = list(sa_type.enums)
    return enums


# ---------------------------------------------------------------------------
# Column rendering
# ---------------------------------------------------------------------------


def _col_attributes(col, pk_col_names: set[str], table) -> str:
    """Build the DBML attribute list for a column."""
    attrs = []

    # PK — only for single-column PKs
    if col.name in pk_col_names and len(pk_col_names) == 1:
        attrs.append("pk")

    if not col.nullable:
        attrs.append("not null")

    # Inline FK ref
    if col.foreign_keys:
        fk = _sorted_foreign_keys(col)[0]
        target = f"{fk.column.table.name}.{fk.column.name}"
        attrs.append(f"ref: > {target}")

    # Default
    if col.server_default is not None:
        default_text = str(col.server_default.arg)
        if "now()" in default_text.lower():
            attrs.append("default: 'now'")
        else:
            cleaned = default_text.strip("'\"")
            attrs.append(f"default: '{cleaned}'")

    # Skip inline unique — these are rendered in the indexes block instead

    if attrs:
        return f" [{', '.join(attrs)}]"
    return ""


def _render_column(col, pk_col_names: set[str], table) -> str:
    type_str = _col_type_str(col)
    attr_str = _col_attributes(col, pk_col_names, table)
    return f"  {col.name} {type_str}{attr_str}"


# ---------------------------------------------------------------------------
# Index rendering
# ---------------------------------------------------------------------------


def _render_indexes(table, pk_col_names: set[str]) -> list[str]:
    """Render non-PK indexes as DBML index entries."""
    lines = []
    for idx in _sorted_indexes(table):
        col_names = [c.name for c in idx.columns]
        if set(col_names) == pk_col_names:
            continue
        if len(col_names) == 1:
            expr = col_names[0]
        else:
            expr = f"({', '.join(col_names)})"
        attrs = []
        if idx.unique:
            attrs.append("unique")
        # Use explicit name if it doesn't follow the naming convention exactly
        idx_name = idx.name
        if idx_name:
            attrs.append(f"name: '{idx_name}'")
        attr_str = f" [{', '.join(attrs)}]" if attrs else ""
        lines.append(f"    {expr}{attr_str}")

    # Also render UniqueConstraints that aren't already covered by indexes
    for constraint in _sorted_constraints(table, UniqueConstraint):
        col_names = [c.name for c in constraint.columns]
        if set(col_names) == pk_col_names:
            continue
        # Skip only if a *unique* index already renders this exact column set.
        # Matching on columns alone loses the constraint: a non-unique index
        # over the same columns is a lookup aid, not a uniqueness guarantee, so
        # suppressing against one drops the guarantee from the document without
        # dropping it from the database. That is what happened to
        # ``uq_record_review_record`` once ``ix_record_review_record_lookup``
        # was added over ``(record_type, record_id)``.
        unique_index_cols = {
            frozenset(c.name for c in idx.columns)
            for idx in table.indexes
            if idx.unique
        }
        if frozenset(col_names) in unique_index_cols:
            continue
        if len(col_names) == 1:
            expr = col_names[0]
        else:
            expr = f"({', '.join(col_names)})"
        attrs = ["unique"]
        if constraint.name:
            attrs.append(f"name: '{constraint.name}'")
        lines.append(f"    {expr} [{', '.join(attrs)}]")

    return lines


# ---------------------------------------------------------------------------
# Check constraint rendering
# ---------------------------------------------------------------------------


def _render_checks(table) -> list[str]:
    """Render check constraints as DBML check entries."""
    lines = []
    for constraint in _sorted_constraints(table, CheckConstraint):
        expr = str(constraint.sqltext)
        name = constraint.name or ""
        lines.append(f"    `{expr}` [name: '{name}']")
    return lines


# ---------------------------------------------------------------------------
# Composite PK rendering
# ---------------------------------------------------------------------------


def _render_composite_pk(pk_col_names: set[str]) -> str | None:
    """Render a composite PK as a DBML index entry."""
    if len(pk_col_names) > 1:
        cols = ", ".join(sorted(pk_col_names))
        return f"    ({cols}) [pk]"
    return None


# ---------------------------------------------------------------------------
# Note rendering
# ---------------------------------------------------------------------------

# Map table names to docstrings from the ORM model classes
_TABLE_NOTES: dict[str, str] = {}


def _collect_table_notes():
    """Collect docstrings from ORM model classes keyed by table name."""
    # `Base.registry.mappers` is a set; sort by tablename for determinism
    # (harmless here since each tablename maps to a single class, but keeps
    # the collection order stable and cheap to reason about).
    mappers = sorted(
        Base.registry.mappers,
        key=lambda m: getattr(m.class_, "__tablename__", "") or "",
    )
    for mapper in mappers:
        cls = mapper.class_
        tablename = getattr(cls, "__tablename__", None)
        if tablename and cls.__doc__:
            # Take the first line of the docstring
            first_line = cls.__doc__.strip().split("\n")[0].strip()
            if first_line:
                _TABLE_NOTES[tablename] = first_line


# ---------------------------------------------------------------------------
# Composite FK rendering (standalone Refs)
# ---------------------------------------------------------------------------


def _collect_composite_refs(metadata) -> list[str]:
    """Collect composite foreign keys that need standalone Ref lines."""
    refs = []
    for table in _sorted_tables(metadata):
        for fkc in _sorted_foreign_key_constraints(table):
            if len(fkc.columns) > 1:
                local_cols = ", ".join(c.name for c in fkc.columns)
                referred_cols = ", ".join(
                    e.column.name for e in fkc.elements
                )
                referred_table = fkc.referred_table.name
                name = fkc.name or ""
                refs.append(
                    f"Ref {name}: {table.name}.({local_cols}) > "
                    f"{referred_table}.({referred_cols})"
                )
    return refs


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _render_table(table) -> str:
    pk_constraint = table.primary_key
    pk_col_names = {c.name for c in pk_constraint.columns}

    lines = [f"Table {table.name} {{"]

    # Columns
    for col in table.columns:
        lines.append(_render_column(col, pk_col_names, table))

    # Indexes block
    index_lines = _render_indexes(table, pk_col_names)
    composite_pk = _render_composite_pk(pk_col_names)
    if composite_pk or index_lines:
        lines.append("")
        lines.append("  indexes {")
        if composite_pk:
            lines.append(composite_pk)
        lines.extend(index_lines)
        lines.append("  }")

    # Checks block
    check_lines = _render_checks(table)
    if check_lines:
        lines.append("")
        lines.append("  checks {")
        lines.extend(check_lines)
        lines.append("  }")

    # Note
    note = _TABLE_NOTES.get(table.name)
    if note:
        escaped = note.replace("'", "\\'")
        lines.append("")
        lines.append(f"  Note: '{escaped}'")

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def generate_dbml() -> str:
    _collect_table_notes()
    metadata = Base.metadata

    parts: list[str] = []

    # Enums
    enums = _collect_enums(metadata)
    for name, values in sorted(enums.items()):
        block = [f"enum {name} {{"]
        for v in values:
            block.append(f"  {v}")
        block.append("}")
        parts.append("\n".join(block))

    # Tables
    for table in _sorted_tables(metadata):
        parts.append(_render_table(table))

    # Composite FKs
    refs = _collect_composite_refs(metadata)
    if refs:
        parts.extend(refs)

    return "\n\n".join(parts) + "\n"


#: A DBML line that carries only prose. ``Note:`` lines are the first line of
#: an ORM class docstring, so rewording a docstring moves this document without
#: any schema having changed. They are still checked -- a stale note is a wrong
#: note -- but they are reported *separately*, because a gate whose failures
#: are indistinguishable from cosmetic churn is one people learn to satisfy by
#: regenerating without reading the diff, and that is how a real drift lands
#: unnoticed. Same lesson as the line-number anchors in the scientific check
#: register.
def _is_prose_line(line: str) -> bool:
    return line.lstrip().startswith("Note:")


def diff_against_committed(committed: str | None = None) -> tuple[str, bool]:
    """Return (rendered diff, structural) for the committed file.

    ``structural`` is False when every differing line is prose, which lets a
    caller say "the schema is unchanged; only a description moved".

    *committed* overrides the on-disk text. That exists so the test proving
    this function can fail does not have to stale the real ``schema.dbml`` to
    do it: writing to a tracked file from a test that eight xdist workers may
    run at once is a way to corrupt a working tree, and a crash between the
    write and the restore would leave the document wrong -- which is the
    condition being guarded against.
    """
    generated = generate_dbml()
    if committed is None:
        committed = OUTPUT.read_text() if OUTPUT.exists() else ""
    if generated == committed:
        return "", False

    diff_lines = list(
        difflib.unified_diff(
            committed.splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=f"{OUTPUT.name} (committed)",
            tofile=f"{OUTPUT.name} (rendered from app/db/models/)",
            n=1,
        )
    )
    changed = [
        line
        for line in diff_lines
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    structural = any(not _is_prose_line(line[1:]) for line in changed)
    return "".join(diff_lines), structural


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed file is out of date",
    )
    args = parser.parse_args(argv)

    if args.check:
        diff, structural = diff_against_committed()
        if not diff:
            print(f"{OUTPUT} is in sync with the models.")
            return 0
        kind = "schema" if structural else "table descriptions only"
        print(f"{OUTPUT} is out of date ({kind}).", file=sys.stderr)
        print(diff, file=sys.stderr)
        print(
            "Regenerate with: conda run -n tckdb_env python scripts/generate_dbml.py",
            file=sys.stderr,
        )
        return 1

    dbml = generate_dbml()
    OUTPUT.write_text(dbml)
    print(f"Wrote {OUTPUT} ({len(dbml)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
