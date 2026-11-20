"""Bring the method and basis-set catalogue tables to the seed (:mod:`app.services.method_catalogue_seed`).

:func:`sync_catalogue` compares the database with the seed and returns one line per difference; with ``apply=True``
it also removes them. A second run is a no-op. It works on a plain SQLAlchemy ``Connection`` with Core statements
only, so the Alembic revision that creates the tables can call it without the ORM models.

Literature rows are get-or-created by the public ref a deposit citing the DOI would be given. An existing row is
adopted as it stands and never rewritten; a created row carries the Crossref fields with
``title_source = resolved_metadata``. Literature rows are never deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from app.db.models.common import (
    BasisSetRole,
    CatalogueMethodKind,
    CatalogueReferenceRole,
    LiteratureKind,
    LiteratureTitleSource,
)
from app.services.method_catalogue_seed import CatalogueSeed, SeedBasisSet, SeedCitation, SeedMethod
from app.services.public_refs import PREFIXES, literature_doi_public_ref, make_content_ref

_meta = sa.MetaData()


def _enum(enum: type[Enum], name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*(member.value for member in enum), name=name, create_type=False)


_literature = sa.Table(
    "literature",
    _meta,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("public_ref", sa.String(40)),
    sa.Column("kind", _enum(LiteratureKind, "literature_kind")),
    sa.Column("title", sa.Text),
    sa.Column("title_source", _enum(LiteratureTitleSource, "literature_title_source")),
    sa.Column("journal", sa.Text),
    sa.Column("year", sa.Integer),
    sa.Column("volume", sa.Text),
    sa.Column("issue", sa.Text),
    sa.Column("pages", sa.Text),
    sa.Column("doi", sa.Text),
)


@dataclass(frozen=True)
class _Tables:
    entry: sa.Table
    key: sa.Table
    link: sa.Table
    fk: str  # the entry FK column on the key and link tables
    prefix: str
    canonical: str  # canonical-identity label for the content-derived ref
    fields: tuple[str, ...]  # entry columns the seed sets, besides key and seed_version


def _entry_tables(name: str, kind_column: sa.Column, extra: tuple[sa.Column, ...], prefix: str) -> _Tables:
    fk = f"{name}_id"
    entry = sa.Table(
        name,
        _meta,
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("public_ref", sa.String(40)),
        sa.Column("key", sa.Text),
        kind_column,
        *extra,
        sa.Column("short_name", sa.Text),
        sa.Column("full_name", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("seed_version", sa.Integer),
        sa.Column("updated_at", sa.DateTime),
    )
    key = sa.Table(f"{name}_key", _meta, sa.Column("key", sa.Text, primary_key=True), sa.Column(fk, sa.BigInteger))
    link = sa.Table(
        f"{name}_literature",
        _meta,
        sa.Column(fk, sa.BigInteger, primary_key=True),
        sa.Column("literature_id", sa.BigInteger, primary_key=True),
        sa.Column("role", _enum(CatalogueReferenceRole, "catalogue_reference_role")),
        sa.Column("position", sa.SmallInteger),
    )
    fields = (kind_column.name, *(c.name for c in extra), "short_name", "full_name", "description")
    return _Tables(entry, key, link, fk, prefix, name, fields)


METHOD_TABLES = _entry_tables(
    "method_catalogue", sa.Column("kind", _enum(CatalogueMethodKind, "catalogue_method_kind")), (), PREFIXES["MethodCatalogue"]
)
BASIS_SET_TABLES = _entry_tables(
    "basis_set_catalogue",
    sa.Column("role", _enum(BasisSetRole, "basis_set_role")),
    (sa.Column("family", sa.Text),),
    PREFIXES["BasisSetCatalogue"],
)


def catalogue_public_ref(tables: _Tables, key: str) -> str:
    """The content-derived public ref of the catalogue entry with this identity key."""
    return make_content_ref(tables.prefix, f"{tables.canonical}:key={key}")


def _literature_ids(conn: Connection, seed: CatalogueSeed, *, apply: bool, changes: list[str]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for doi, ref in seed.references.items():
        public_ref = literature_doi_public_ref(doi)
        row = conn.execute(sa.select(_literature.c.id).where(_literature.c.public_ref == public_ref)).first()
        if row is not None:
            ids[doi] = row.id
            continue
        changes.append(f"literature: create {doi}")
        if apply:
            ids[doi] = conn.execute(
                sa.insert(_literature)
                .values(
                    public_ref=public_ref,
                    kind="article",
                    title=ref.title,
                    title_source="resolved_metadata",
                    journal=ref.journal,
                    year=ref.year,
                    volume=ref.volume,
                    issue=ref.issue,
                    pages=ref.pages,
                    doi=doi,
                )
                .returning(_literature.c.id)
            ).scalar_one()
    return ids


def _desired_fields(entry: SeedMethod | SeedBasisSet, tables: _Tables, seed_version: int) -> dict[str, object]:
    values: dict[str, object] = {name: getattr(entry, name) for name in tables.fields}
    for name, value in values.items():
        if hasattr(value, "value"):
            values[name] = value.value
    values["seed_version"] = seed_version
    return values


def _sync_entries(
    conn: Connection,
    tables: _Tables,
    entries: tuple[SeedMethod, ...] | tuple[SeedBasisSet, ...],
    seed_version: int,
    literature_ids: dict[str, int],
    *,
    apply: bool,
    changes: list[str],
) -> None:
    t = tables
    name = t.entry.name
    compared = (*t.fields, "seed_version")
    existing = {row.key: row for row in conn.execute(sa.select(t.entry)).all()}
    wanted = {e.key: e for e in entries}

    for key in sorted(set(existing) - set(wanted)):
        changes.append(f"{name}: delete {key}")
        if apply:
            conn.execute(sa.delete(t.entry).where(t.entry.c.id == existing[key].id))

    entry_ids: dict[str, int | None] = {}
    for key, entry in wanted.items():
        values = _desired_fields(entry, t, seed_version)
        row = existing.get(key)
        if row is None:
            changes.append(f"{name}: create {key}")
            entry_ids[key] = None
            if apply:
                entry_ids[key] = conn.execute(
                    sa.insert(t.entry)
                    .values(key=key, public_ref=catalogue_public_ref(t, key), **values)
                    .returning(t.entry.c.id)
                ).scalar_one()
            continue
        entry_ids[key] = row.id
        differ = [c for c in compared if _plain(getattr(row, c)) != values[c]]
        if differ:
            changes.append(f"{name}: update {key} ({', '.join(differ)})")
            if apply:
                conn.execute(
                    sa.update(t.entry).where(t.entry.c.id == row.id).values(updated_at=sa.func.now(), **values)
                )

    # Keys: every identity key of every entry, the entry's own key included.
    have_keys = {row.key: row[1] for row in conn.execute(sa.select(t.key.c.key, t.key.c[t.fk])).all()}
    want_keys = {k: entry.key for entry in entries for k in entry.keys}
    id_to_key = {v: k for k, v in entry_ids.items() if v is not None}
    for k in sorted(have_keys):
        if want_keys.get(k) is None or id_to_key.get(have_keys[k]) != want_keys[k]:
            changes.append(f"{name}_key: remove {k}")
            if apply:
                conn.execute(sa.delete(t.key).where(t.key.c.key == k))
    for k in sorted(want_keys):
        owner = want_keys[k]
        if k in have_keys and id_to_key.get(have_keys[k]) == owner:
            continue
        changes.append(f"{name}_key: add {k} -> {owner}")
        if apply:
            conn.execute(sa.insert(t.key).values(key=k, **{t.fk: entry_ids[owner]}))

    # References: the whole ordered list per entry; a difference rewrites that entry's list.
    have_links: dict[int, list[tuple[int, str, int]]] = {}
    for row in conn.execute(sa.select(t.link).order_by(t.link.c.position)).all():
        have_links.setdefault(row[0], []).append((row.literature_id, _plain(row.role), row.position))
    for key, entry in wanted.items():
        entry_id = entry_ids[key]
        want = _wanted_links(entry.references, literature_ids)
        if entry_id is not None and have_links.get(entry_id, []) == want:
            continue
        changes.append(f"{name}_literature: set {key} ({len(entry.references)} references)")
        if apply and entry_id is not None:
            conn.execute(sa.delete(t.link).where(t.link.c[t.fk] == entry_id))
            conn.execute(
                sa.insert(t.link),
                [{t.fk: entry_id, "literature_id": lit, "role": role, "position": pos} for lit, role, pos in want],
            )


def _wanted_links(citations: tuple[SeedCitation, ...], literature_ids: dict[str, int]) -> list[tuple[int, str, int]]:
    return [
        (literature_ids.get(c.doi, -1), c.role.value, position)
        for position, c in enumerate(citations, start=1)
    ]


def _plain(value: object) -> object:
    return getattr(value, "value", value)


def sync_catalogue(conn: Connection, seed: CatalogueSeed, *, apply: bool) -> list[str]:
    """Compare the catalogue tables with ``seed``; with ``apply=True`` make them equal.

    :returns: One line per difference found (before applying). Empty when the database matches the seed.
    """
    changes: list[str] = []
    literature_ids = _literature_ids(conn, seed, apply=apply, changes=changes)
    _sync_entries(
        conn,
        METHOD_TABLES,
        seed.methods,
        seed.method_seed_version,
        literature_ids,
        apply=apply,
        changes=changes,
    )
    _sync_entries(
        conn,
        BASIS_SET_TABLES,
        seed.basis_sets,
        seed.basis_set_seed_version,
        literature_ids,
        apply=apply,
        changes=changes,
    )
    return changes
