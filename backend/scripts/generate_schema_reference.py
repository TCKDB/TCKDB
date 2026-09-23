#!/usr/bin/env python
"""Generate docs/guides/schema_reference.md from SQLAlchemy model metadata.

Usage:
    conda run -n tckdb_env python -m scripts.generate_schema_reference
    conda run -n tckdb_env python -m scripts.generate_schema_reference --check

Writes ``docs/guides/schema_reference.md`` (repo root). ``--check`` writes
nothing and exits 1 if the committed file does not match what the models
render, printing the diff.

Why this exists
----------------
``backend/schema_spec.md`` and ``docs/schema_analysis.md`` are hand-written
prose, and ``backend/schema.dbml`` (``scripts/generate_dbml.py``) is a
generated entity diagram. None of the three is a column-level reference a
depositor can search: what does this column mean, is it nullable, what does
it default to, what are the accepted enum values. Hand-writing one would
drift the same way a hand-written ``schema.dbml`` used to -- see that
script's own docstring for the day an enum member went missing from a
document nobody was comparing against the models. This script reads the
same ``Base.metadata`` and ``Base.registry.mappers`` generate_dbml.py reads,
so it is modelled closely on it: same import bootstrap, same deterministic
sorting discipline, same ``--check`` contract.

Never invent a description
---------------------------
A column's "Meaning" cell comes from exactly two places: the column's own
``comment=`` (this codebase sets none, as of this writing, so that source is
currently always empty) and a structured bullet or definition-list entry in
the model's *own* docstring (its class docstring, its declared mixins'
docstrings, or -- last, and only for names not already found -- its module
docstring) that names the column in backticks, e.g. ``* ``foo_id`` is the
...``. An incidental backtick mention of a column name inside ordinary prose
is deliberately NOT enough -- see ``_extract_documented_columns`` -- because
attributing prose to the wrong column would be a wrong answer, and a wrong
answer is worse than an honest "not documented". The same discipline applies
to a table's **role** (identity / provenance / result / curation, see
``docs/guides/core_concepts.md``): it is read only from the class docstring
via ``_derive_role``, and printed as "role not stated on the model" the
moment that is ambiguous, rather than guessed from the table's name or its
columns. Those "not documented" / "role not stated" markers are the point of
this document: they show, honestly, where the schema is under-documented.
"""

from __future__ import annotations

import argparse
import difflib
import inspect
import re
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql

# Ensure the backend root is on sys.path so `app` is importable, exactly as
# generate_dbml.py does. This script lives at backend/scripts/, so
# parents[1] is backend/ (the source-of-truth root for `app`), and
# parents[2] is the repo root, where docs/ lives.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[0]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# E402: the `app` imports must follow the sys.path bootstrap above -- run
# directly or as `python -m scripts.generate_schema_reference` without
# PYTHONPATH, `app` is only importable once BACKEND_ROOT is on sys.path.
# Load all models so metadata is populated. This never opens a database
# connection: importing the models only builds Base.metadata in memory, and
# every render below (`compile(dialect=...)`, docstring introspection) is a
# pure, offline operation over that metadata.
import app.db.models  # noqa: F401,E402
from app.db.base import Base  # noqa: E402

OUTPUT = REPO_ROOT / "docs" / "guides" / "schema_reference.md"
GENERATOR_RELATIVE_PATH = "backend/scripts/generate_schema_reference.py"

_PG_DIALECT = postgresql.dialect()


# ---------------------------------------------------------------------------
# Deterministic ordering helpers
#
# Same rationale as generate_dbml.py: `Table.constraints` and
# `col.foreign_keys` are plain Python `set`s, whose iteration order is not
# guaranteed stable across interpreter runs, so every place one of those is
# iterated sorts first with a tiebreak key that can never itself tie.
# `Table.columns` is an ordered `ColumnCollection` (declaration order) and
# needs no such treatment. `Base.registry.mappers` is also a set, but it is
# only ever used to build a table-name-keyed dict below, and dict lookups by
# a unique key do not depend on insertion order.
# ---------------------------------------------------------------------------


def _sorted_tables(metadata) -> list:
    """Tables in a stable, name-sorted order (independent of import/declaration order)."""
    return sorted(metadata.tables.values(), key=lambda t: t.name)


def _sorted_checks(table) -> list:
    """`table.constraints` (a set) filtered to CheckConstraint, sorted deterministically."""
    matching = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    return sorted(matching, key=lambda c: (c.name or "", str(c.sqltext)))


def _sorted_foreign_keys(col) -> list:
    """`col.foreign_keys` (a set) sorted deterministically by referenced target."""
    return sorted(
        col.foreign_keys,
        key=lambda fk: (fk.column.table.name, fk.column.name),
    )


def _table_classes() -> dict[str, type]:
    """Map ``__tablename__`` -> mapped ORM class, for every mapped class with a table name."""
    classes: dict[str, type] = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        tablename = getattr(cls, "__tablename__", None)
        if tablename:
            classes[tablename] = cls
    return classes


# ---------------------------------------------------------------------------
# Role derivation -- identity / provenance / result / curation
# ---------------------------------------------------------------------------

ROLE_NOT_STATED = "role not stated on the model"

_ROLE_WORD = re.compile(r"\b(identit(?:y|ies)|provenance|result(?:s)?|curation)\b", re.IGNORECASE)
_ROLE_NORMALIZE = {
    "identity": "identity",
    "identities": "identity",
    "provenance": "provenance",
    "result": "result",
    "results": "result",
    "curation": "curation",
}
ROLE_SECTION_TITLES = {
    "curation": "Curation",
    "identity": "Identity",
    "provenance": "Provenance",
    "result": "Result",
    ROLE_NOT_STATED: "Role not stated on the model",
}
_ROLE_SORT_ORDER = {"curation": 0, "identity": 1, "provenance": 2, "result": 3, ROLE_NOT_STATED: 4}


def _derive_role(cls_doc: str | None) -> str:
    """Read a table's role from its own class docstring only.

    A single, unambiguous role word (identity/identities, provenance,
    result(s), curation) is trusted. Zero matches, or more than one
    *distinct* role word, both fall back to ``ROLE_NOT_STATED`` --
    several docstrings use these words in passing (e.g. "molecular
    identity", "the result of any one estimation") without declaring the
    table's own bucket, and there is no reliable way to tell that usage
    apart from a genuine declaration without guessing.
    """
    if not cls_doc:
        return ROLE_NOT_STATED
    hits = {_ROLE_NORMALIZE[word.lower()] for word in _ROLE_WORD.findall(cls_doc)}
    if len(hits) == 1:
        return next(iter(hits))
    return ROLE_NOT_STATED


# ---------------------------------------------------------------------------
# Purpose -- first paragraph of the model class docstring, verbatim
# ---------------------------------------------------------------------------


def _first_paragraph(doc_text: str | None) -> str:
    if not doc_text:
        return ""
    cleaned = inspect.cleandoc(doc_text)
    paragraph = cleaned.split("\n\n", 1)[0]
    return " ".join(line.strip() for line in paragraph.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Column meanings -- structured (bullet / definition-list) docstring entries
# ---------------------------------------------------------------------------

_BACKTICK_NAME = re.compile(r"``([a-zA-Z_][a-zA-Z0-9_]*)``")
# A block "belongs" to a column only when it OPENS with that column's
# backticked name (optionally several, joined by "/", ",", "+" or "and" --
# e.g. "``h298_kj_mol`` / ``s298_j_mol_k`` are the standard enthalpy ...").
# This is deliberately stricter than "the name appears somewhere in the
# block": see the module docstring's "Never invent a description" section.
_LEADING_NAMES = re.compile(
    r"^((?:``[a-zA-Z_][a-zA-Z0-9_]*``\s*(?:/|,|\+|and)?\s*)+)(.*)$",
    re.DOTALL,
)


def _extract_documented_columns(doc_text: str | None, column_names: set[str]) -> dict[str, str]:
    """Pull column meanings out of bullet (``* ``col`` ...``) and definition-list
    (a bare ``` ``col`` ``` line followed by an indented paragraph) blocks in
    *doc_text*. Only blocks whose leading token(s) are one of *column_names*,
    written in backticks, are attributed -- an incidental backtick mention of
    a column name elsewhere in a paragraph is not treated as documenting it.
    """
    if not doc_text:
        return {}
    cleaned = inspect.cleandoc(doc_text)
    lines = cleaned.splitlines()
    results: dict[str, str] = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        bullet = re.match(r"^[*-]\s+(.*)$", line)
        defterm = re.match(r"^``([a-zA-Z_][a-zA-Z0-9_]*)``\s*:?\s*$", stripped)

        if bullet:
            indent = len(line) - len(line.lstrip())
            block_lines = [bullet.group(1)]
            j = i + 1
            while j < n:
                nxt = lines[j]
                if not nxt.strip():
                    break
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent <= indent:
                    break
                block_lines.append(nxt.strip())
                j += 1
            block_text = " ".join(block_lines).strip()
            leading = _LEADING_NAMES.match(block_text)
            if leading:
                names = set(_BACKTICK_NAME.findall(leading.group(1)))
                for name in sorted(names & column_names):
                    results.setdefault(name, block_text)
            i = j
            continue

        if defterm:
            name = defterm.group(1)
            j = i + 1
            block_lines: list[str] = []
            base_indent: int | None = None
            while j < n:
                nxt = lines[j]
                if not nxt.strip():
                    if base_indent is None:
                        j += 1
                        continue
                    break
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if base_indent is None:
                    base_indent = nxt_indent
                elif nxt_indent < base_indent:
                    break
                block_lines.append(nxt.strip())
                j += 1
            block_text = " ".join(block_lines).strip()
            if name in column_names and block_text:
                results.setdefault(name, block_text)
            i = j
            continue

        i += 1
    return results


def _column_meanings(cls: type | None, column_names: set[str]) -> dict[str, str]:
    """Column meanings for *cls*, most specific source first.

    Sources, in priority order (first found wins, via ``setdefault``):
    the class's own docstring, then its own-package base classes' (mixins')
    docstrings in MRO order, then -- only for names still missing -- the
    defining module's docstring. Sources outside ``app.`` (``object``,
    SQLAlchemy's ``DeclarativeBase``) are skipped; they never name a column
    but are excluded on principle, not because a match was observed.
    """
    if cls is None:
        return {}
    docs: dict[str, str] = {}
    for base in cls.__mro__:
        if not getattr(base, "__module__", "").startswith("app."):
            continue
        for name, meaning in _extract_documented_columns(base.__doc__, column_names).items():
            docs.setdefault(name, meaning)
    module = sys.modules.get(cls.__module__)
    module_doc = getattr(module, "__doc__", None) if module is not None else None
    for name, meaning in _extract_documented_columns(module_doc, column_names).items():
        docs.setdefault(name, meaning)
    return docs


# ---------------------------------------------------------------------------
# Column rendering -- type, nullability, default, FK target, enum values
# ---------------------------------------------------------------------------


def _col_type_str(col) -> str:
    """A reader-recognisable type string: the actual PostgreSQL column type
    the model would create, compiled offline from metadata (no connection),
    with two overrides: the RDKit ``mol`` cartridge type, and enum columns
    (named by their Python enum class, whose full value list is rendered in
    its own column -- the PostgreSQL enum type name alone would not show
    the accepted values)."""
    sa_type = col.type
    type_name = type(sa_type).__name__
    if type_name == "RDKitMol":
        return "mol (RDKit cartridge structure)"

    enum_class = getattr(sa_type, "enum_class", None)
    if enum_class is not None:
        return f"{enum_class.__name__} (enum)"
    if hasattr(sa_type, "enums"):
        name = getattr(sa_type, "name", None)
        return f"{name} (enum)" if name else "enum"

    try:
        return str(sa_type.compile(dialect=_PG_DIALECT))
    except Exception:
        return type_name.lower()


def _enum_values(col) -> list[str] | None:
    sa_type = col.type
    enum_class = getattr(sa_type, "enum_class", None)
    if enum_class is not None:
        return [member.value for member in enum_class]
    if hasattr(sa_type, "enums"):
        return list(sa_type.enums)
    return None


def _default_str(col) -> str | None:
    """The default value a reader would see, from server_default first (what
    the database itself enforces), then the client-side ``default=`` (what
    the ORM supplies on insert if nothing else does). Mirrors
    generate_dbml.py's server_default handling for consistency between the
    two generated documents."""
    if col.server_default is not None:
        text = str(col.server_default.arg)
        if "now()" in text.lower():
            return "now()"
        return text.strip("'\"")
    default = col.default
    if default is not None:
        if getattr(default, "is_scalar", False):
            arg = default.arg
            value = getattr(arg, "value", arg)  # unwrap a bare enum member
            return repr(value)
        if getattr(default, "is_callable", False):
            fn = getattr(default, "arg", None)
            name = getattr(fn, "__name__", None)
            return f"{name}() (computed client-side default)" if name else "(computed client-side default)"
    return None


def _fk_str(col) -> str | None:
    if not col.foreign_keys:
        return None
    fk = _sorted_foreign_keys(col)[0]
    return f"{fk.column.table.name}.{fk.column.name}"


def _escape_cell(text: str | None) -> str:
    if not text:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _render_table_section(table, cls: type | None) -> str:
    role = _derive_role(cls.__doc__ if cls is not None else None)
    purpose = _first_paragraph(cls.__doc__ if cls is not None else None)
    column_names = {c.name for c in table.columns}
    meanings = _column_meanings(cls, column_names)

    lines = [f"### `{table.name}`", ""]
    lines.append(f"**Role:** {role}")
    lines.append("")
    lines.append(f"**Purpose:** {purpose if purpose else 'not documented'}")
    lines.append("")
    lines.append("| Column | Type | Nullable | Default | Foreign key | Enum values | Meaning |")
    lines.append("|---|---|---|---|---|---|---|")
    for col in table.columns:
        type_str = _escape_cell(_col_type_str(col))
        nullable = "yes" if col.nullable else "no"
        default = _escape_cell(_default_str(col)) or "\u2014"
        fk = _escape_cell(_fk_str(col)) or "\u2014"
        enum_values = _enum_values(col)
        enum_str = ", ".join(f"`{v}`" for v in enum_values) if enum_values else "\u2014"
        meaning = meanings.get(col.name)
        meaning_str = _escape_cell(meaning) if meaning else "not documented"
        lines.append(f"| `{col.name}` | {type_str} | {nullable} | {default} | {fk} | {enum_str} | {meaning_str} |")

    checks = _sorted_checks(table)
    if checks:
        lines.append("")
        lines.append("**Check constraints:**")
        lines.append("")
        for constraint in checks:
            name = constraint.name or "(unnamed)"
            lines.append(f"- `{name}`: `{constraint.sqltext}`")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

_HEADER = f"""# Schema reference

This document is generated from the SQLAlchemy models in `app/db/models/` by
[`{GENERATOR_RELATIVE_PATH}`](../../{GENERATOR_RELATIVE_PATH}). The models are
the source of truth; hand edits to this file are overwritten the next time it
is regenerated. Regenerate with:

```bash
conda run -n tckdb_env python scripts/generate_schema_reference.py
```

run from `backend/`, and verify with `--check` (no arguments changes nothing
and exits non-zero if this file is stale).

Tables are grouped by the role their own model class docstring states --
**identity** (deduped -- what a thing is), **provenance** (append-only -- how
it was produced), **result** (append-only -- the number), or **curation**
(overlay -- how much to trust it); see
[`core_concepts.md`](core_concepts.md). A table whose class docstring does not
say which of the four it is prints as "role not stated on the model" rather
than a guess. Each column's "Meaning" cell is pulled only from the column's
own database comment (this codebase currently sets none) or from a structured
entry in the model's own docstring that names the column; a column with
neither prints "not documented". Those markers are deliberate: they are
honest about where the schema is under-documented, rather than papering over
the gap.
"""


def generate_schema_reference() -> str:
    table_classes = _table_classes()
    grouped: dict[str, list[tuple]] = {}
    for table in _sorted_tables(Base.metadata):
        cls = table_classes.get(table.name)
        role = _derive_role(cls.__doc__ if cls is not None else None)
        grouped.setdefault(role, []).append((table, cls))

    parts = [_HEADER]
    for role in sorted(grouped, key=lambda r: _ROLE_SORT_ORDER[r]):
        parts.append(f"## {ROLE_SECTION_TITLES[role]}")
        parts.append("")
        for table, cls in sorted(grouped[role], key=lambda tc: tc[0].name):
            parts.append(_render_table_section(table, cls))

    return "\n".join(parts).rstrip("\n") + "\n"


def diff_against_committed(committed: str | None = None) -> str:
    """Return a unified diff against the committed file (empty string if in sync).

    *committed* overrides the on-disk text, the same way generate_dbml.py's
    ``diff_against_committed`` does, so the test proving this function can
    fail does not have to stale the real, tracked ``schema_reference.md`` --
    a file multiple xdist workers may read concurrently -- to prove it.
    """
    generated = generate_schema_reference()
    if committed is None:
        committed = OUTPUT.read_text() if OUTPUT.exists() else ""
    if generated == committed:
        return ""
    diff_lines = difflib.unified_diff(
        committed.splitlines(keepends=True),
        generated.splitlines(keepends=True),
        fromfile=f"{OUTPUT.name} (committed)",
        tofile=f"{OUTPUT.name} (rendered from app/db/models/)",
        n=1,
    )
    return "".join(diff_lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed file is out of date",
    )
    args = parser.parse_args(argv)

    if args.check:
        diff = diff_against_committed()
        if not diff:
            print(f"{OUTPUT} is in sync with the models.")
            return 0
        print(f"{OUTPUT} is out of date.", file=sys.stderr)
        print(diff, file=sys.stderr)
        print(
            "Regenerate with: conda run -n tckdb_env python scripts/generate_schema_reference.py",
            file=sys.stderr,
        )
        return 1

    rendered = generate_schema_reference()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered)
    print(f"Wrote {OUTPUT} ({len(rendered)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
