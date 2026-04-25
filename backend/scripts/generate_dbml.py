#!/usr/bin/env python
"""Generate schema.dbml from SQLAlchemy model metadata.

Usage:
    conda run -n tckdb_env python -m scripts.generate_dbml

Writes schema.dbml to the repository root.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so `app` is importable
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import CheckConstraint, UniqueConstraint

# Load all models so metadata is populated
import app.db.models  # noqa: F401
from app.db.base import Base

OUTPUT = REPO_ROOT / "schema.dbml"


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
    for table in metadata.sorted_tables:
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
        fk = next(iter(col.foreign_keys))
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
    for idx in table.indexes:
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
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            col_names = [c.name for c in constraint.columns]
            if set(col_names) == pk_col_names:
                continue
            # Check if already covered by an index
            existing_index_cols = {
                frozenset(c.name for c in idx.columns) for idx in table.indexes
            }
            if frozenset(col_names) in existing_index_cols:
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
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
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
    for mapper in Base.registry.mappers:
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
    for table in metadata.sorted_tables:
        for fkc in table.foreign_key_constraints:
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
    for table in metadata.sorted_tables:
        parts.append(_render_table(table))

    # Composite FKs
    refs = _collect_composite_refs(metadata)
    if refs:
        parts.extend(refs)

    return "\n\n".join(parts) + "\n"


def main():
    dbml = generate_dbml()
    OUTPUT.write_text(dbml)
    print(f"Wrote {OUTPUT} ({len(dbml)} bytes)")


if __name__ == "__main__":
    main()
