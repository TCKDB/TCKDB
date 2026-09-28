#!/usr/bin/env python
"""List, and optionally delete, provenance registry rows nothing references.

Scope: ``software_release``, ``software``, ``workflow_tool_release`` and
``workflow_tool`` (issue #305). These are identity rows -- a program, or one
exact release of it. A row that no record cites asserts nothing about the
archive; it only shows up in registry listings as though something had been
run with it. The deployed playground held two such rows on 2026-09-27 (an
``Arkane`` release and a version-less ``Gaussian`` release).

**What "referenced" means is read from the database, not from this file.**
Every foreign key whose target is one of the four tables is discovered from
``pg_constraint`` at run time. A hard-coded list would have been wrong on the
day it was written: ``calculation.software_release_id`` is not the only
reference into ``software_release`` -- thermo, statmech, kinetics,
transport, network, network_solve, frequency_scale_factor,
energy_correction_scheme, execution_environment_manifest and
molecular_property_observation all carry one, and a release cited only by a
thermo row (the Arkane analysis-software case) has zero calculations and is
still very much in use. A foreign key added next year is covered without an
edit here. The discovered list is printed, so the operator can see what was
checked.

Refuses to plan at all (exit 2) if any such key is multi-column, targets a
column other than ``id``, or deletes with anything but NO ACTION/RESTRICT --
each would make "no row matches ``col = id``" a weaker statement than
"deleting this row changes nothing else", and the script would rather stop
than guess.

Parents are pruned with their orphan releases: a ``software`` row whose only
references are releases this run removes is listed too (marked
``after releases``), and at commit time it is deleted only after those
releases are gone.

Run ``--commit`` when no deposits are in flight: its single transaction
holds the row locks it takes until it ends, so an upload citing a planned
row waits for it (and then either the row is kept, or the upload's own
foreign-key check fails).

Usage::

    # Plan only -- the default. Lists what would be removed; writes nothing.
    python backend/scripts/ops/prune_orphan_provenance.py

    # Delete. One transaction; each row is re-checked as it is deleted, so a
    # row that became referenced after the plan was printed is kept and
    # reported, never removed.
    python backend/scripts/ops/prune_orphan_provenance.py --commit

    # --commit against a database whose name is not tckdb_test* also needs:
    python backend/scripts/ops/prune_orphan_provenance.py --commit --i-know-this-is-deployed

Rows are named by ``public_ref`` and content (name, version, revision,
build / git commit) -- never by primary key.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

#: Same convention as ``backfill_assumed_tau.py``: the harness default is
#: ``tckdb_test``, suffixed per branch/run. Anchored both ends.
_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")

#: Releases first: a parent can only become unreferenced once its releases go.
RELEASE_TABLES: tuple[str, ...] = ("software_release", "workflow_tool_release")
PARENT_OF: dict[str, str] = {
    "software_release": "software",
    "workflow_tool_release": "workflow_tool",
}
PARENT_TABLES: tuple[str, ...] = ("software", "workflow_tool")
TABLES: tuple[str, ...] = RELEASE_TABLES + PARENT_TABLES

#: ``pg_constraint.confdeltype``: a = NO ACTION, r = RESTRICT. Anything else
#: (CASCADE, SET NULL, SET DEFAULT) means a delete here could change another
#: row, which this script must never do.
_SAFE_DELETE_RULES = {"a", "r"}

#: How each row is described. Every column named is non-key content.
_LABEL_SQL: dict[str, str] = {
    "software_release": (
        "SELECT t.id, t.public_ref, p.name, t.version, t.revision, t.build "
        "FROM public.software_release t JOIN public.software p ON p.id = t.software_id"
    ),
    "workflow_tool_release": (
        "SELECT t.id, t.public_ref, p.name, t.version, t.git_commit, NULL "
        "FROM public.workflow_tool_release t "
        "JOIN public.workflow_tool p ON p.id = t.workflow_tool_id"
    ),
    "software": "SELECT t.id, t.public_ref, t.name, NULL, NULL, NULL FROM public.software t",
    "workflow_tool": (
        "SELECT t.id, t.public_ref, t.name, NULL, NULL, NULL FROM public.workflow_tool t"
    ),
}


class UnsafeReferenceError(RuntimeError):
    """A discovered foreign key this script cannot reason about safely."""


@dataclass(frozen=True)
class ForeignKeyRef:
    """One single-column foreign key into a pruned table."""

    constraint: str
    source_schema: str
    source_table: str
    source_column: str
    target_table: str

    def describe(self) -> str:
        return f"{self.source_table}.{self.source_column} -> {self.target_table}.id"


@dataclass(frozen=True)
class Candidate:
    """A row planned for removal. ``row_id`` is used internally only."""

    table: str
    row_id: int
    public_ref: str
    label: str
    after_releases: bool = False


@dataclass
class Plan:
    references: dict[str, list[ForeignKeyRef]]
    candidates: dict[str, list[Candidate]] = field(default_factory=dict)

    def all(self) -> list[Candidate]:
        return [c for table in TABLES for c in self.candidates.get(table, [])]


@dataclass
class CommitResult:
    removed: list[Candidate] = field(default_factory=list)
    kept: list[tuple[Candidate, str]] = field(default_factory=list)


def discover_references(session: Session) -> dict[str, list[ForeignKeyRef]]:
    """Every foreign key into the four tables, read from ``pg_constraint``.

    :raises UnsafeReferenceError: a key is multi-column, does not target
        ``id``, or has a delete rule other than NO ACTION/RESTRICT.
    """

    rows = session.execute(
        text(
            """
            SELECT c.conname,
                   src_ns.nspname,
                   src.relname,
                   tgt.relname,
                   c.confdeltype,
                   array_length(c.conkey, 1),
                   (SELECT a.attname FROM pg_attribute a
                     WHERE a.attrelid = c.conrelid AND a.attnum = c.conkey[1]),
                   (SELECT a.attname FROM pg_attribute a
                     WHERE a.attrelid = c.confrelid AND a.attnum = c.confkey[1])
            FROM pg_constraint c
            JOIN pg_class src ON src.oid = c.conrelid
            JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
            JOIN pg_class tgt ON tgt.oid = c.confrelid
            JOIN pg_namespace tgt_ns ON tgt_ns.oid = tgt.relnamespace
            WHERE c.contype = 'f'
              AND tgt_ns.nspname = 'public'
              AND tgt.relname = ANY(:tables)
            ORDER BY tgt.relname, src.relname, c.conname
            """
        ),
        {"tables": list(TABLES)},
    ).all()

    references: dict[str, list[ForeignKeyRef]] = {table: [] for table in TABLES}
    problems: list[str] = []
    for conname, src_schema, src_table, tgt_table, deltype, nkeys, src_col, tgt_col in rows:
        if nkeys != 1 or tgt_col != "id":
            problems.append(f"{conname}: multi-column or not targeting {tgt_table}.id")
            continue
        if deltype not in _SAFE_DELETE_RULES:
            problems.append(
                f"{conname}: ON DELETE rule {deltype!r} would change "
                f"{src_table} rows when a {tgt_table} row is deleted"
            )
            continue
        references[tgt_table].append(
            ForeignKeyRef(conname, src_schema, src_table, src_col, tgt_table)
        )
    if problems:
        raise UnsafeReferenceError("; ".join(problems))
    return references


def _quote(session: Session, name: str) -> str:
    return session.get_bind().dialect.identifier_preparer.quote(name)


def _unreferenced_predicate(
    session: Session,
    references: list[ForeignKeyRef],
    *,
    discounted: dict[str, str] | None = None,
) -> str:
    """``NOT EXISTS`` over every reference into ``t``.

    ``discounted`` maps a source table to a bind-parameter name holding ids
    of its rows that this run is about to delete; references *from* those
    rows are not counted (a parent whose only releases are orphans).
    """

    clauses = []
    for ref in references:
        src = f"{_quote(session, ref.source_schema)}.{_quote(session, ref.source_table)}"
        col = _quote(session, ref.source_column)
        clause = f"SELECT 1 FROM {src} s WHERE s.{col} = t.id"
        if discounted and ref.source_table in discounted:
            clause += f" AND NOT (s.id = ANY(:{discounted[ref.source_table]}))"
        clauses.append(f"NOT EXISTS ({clause})")
    return " AND ".join(clauses) if clauses else "TRUE"


def _label(table: str, name, version, extra1, extra2) -> str:
    if table in PARENT_TABLES:
        return str(name)
    if table == "software_release":
        return f"{name} version={version!r} revision={extra1!r} build={extra2!r}"
    return f"{name} version={version!r} git_commit={extra1!r}"


def _select_unreferenced(
    session: Session,
    table: str,
    references: list[ForeignKeyRef],
    *,
    discounted: dict[str, str] | None = None,
    params: dict | None = None,
    after_releases: bool = False,
) -> list[Candidate]:
    predicate = _unreferenced_predicate(session, references, discounted=discounted)
    rows = session.execute(
        text(f"{_LABEL_SQL[table]} WHERE {predicate} ORDER BY t.public_ref"),
        params or {},
    ).all()
    return [
        Candidate(table, row_id, ref, _label(table, name, v, e1, e2), after_releases)
        for row_id, ref, name, v, e1, e2 in rows
    ]


def build_plan(session: Session) -> Plan:
    """Every row in the four tables that nothing references.

    A parent qualifies when it is unreferenced outright, or when its only
    references are releases this plan removes (``after_releases=True``).
    """

    plan = Plan(references=discover_references(session))
    for table in RELEASE_TABLES:
        plan.candidates[table] = _select_unreferenced(
            session, table, plan.references[table]
        )
    for release_table in RELEASE_TABLES:
        parent = PARENT_OF[release_table]
        discounted_ids = [c.row_id for c in plan.candidates[release_table]]
        outright = _select_unreferenced(session, parent, plan.references[parent])
        with_releases = _select_unreferenced(
            session,
            parent,
            plan.references[parent],
            discounted={release_table: "discounted"},
            params={"discounted": discounted_ids},
            after_releases=True,
        )
        outright_ids = {c.row_id for c in outright}
        plan.candidates[parent] = outright + [
            c for c in with_releases if c.row_id not in outright_ids
        ]
    return plan


def commit_plan(session: Session, plan: Plan) -> CommitResult:
    """Delete the planned rows that are *still* unreferenced.

    Releases before parents. Each row is deleted in its own savepoint by a
    ``DELETE ... WHERE id = :id AND NOT EXISTS (...)`` -- the reference check
    is re-run by the statement that deletes, so a row cited since the plan
    was printed matches nothing and is kept. A reference that commits while
    the DELETE is waiting on it surfaces as a foreign-key violation; that
    savepoint is rolled back and the row is kept too. The caller commits.
    """

    result = CommitResult()
    for candidate in plan.all():
        predicate = _unreferenced_predicate(
            session, plan.references[candidate.table]
        )
        table = _quote(session, candidate.table)
        try:
            with session.begin_nested():
                deleted = session.execute(
                    text(
                        f"DELETE FROM public.{table} t "
                        f"WHERE t.id = :id AND {predicate} RETURNING t.public_ref"
                    ),
                    {"id": candidate.row_id},
                ).scalar_one_or_none()
        except IntegrityError:
            result.kept.append((candidate, "became referenced while being deleted"))
            continue
        if deleted is None:
            result.kept.append((candidate, "referenced at commit time"))
        else:
            result.removed.append(candidate)
    return result


def _print_plan(plan: Plan) -> None:
    print("Foreign keys checked (discovered from pg_constraint):")
    for table in TABLES:
        refs = plan.references[table]
        print(f"  into {table}: {len(refs)}")
        for ref in refs:
            print(f"    {ref.describe()}")
    total = len(plan.all())
    print(f"\nUnreferenced rows: {total}")
    for table in TABLES:
        for c in plan.candidates.get(table, []):
            suffix = "  (after releases)" if c.after_releases else ""
            print(f"  {table}  {c.public_ref}  {c.label}{suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "delete the rows listed. Without this, the default is a dry run. "
            "Run it when no deposits are in flight: the transaction holds its "
            "row locks until it ends, so a concurrent upload citing a planned "
            "row waits on it."
        ),
    )
    parser.add_argument(
        "--i-know-this-is-deployed",
        action="store_true",
        help="required alongside --commit on a database not named tckdb_test*",
    )
    args = parser.parse_args(argv)

    from app.api.config import settings
    from app.api.deps import SessionLocal

    if (
        args.commit
        and not _TEST_DB_NAME.match(settings.db_name)
        and not args.i_know_this_is_deployed
    ):
        print(
            "Refusing --commit: the configured database does not match "
            f"{_TEST_DB_NAME.pattern!r}. If this is intentionally a deployed "
            "database, pass --i-know-this-is-deployed as well.",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as session:
        try:
            plan = build_plan(session)
        except UnsafeReferenceError as exc:
            print(f"Refusing to plan: {exc}", file=sys.stderr)
            return 2
        _print_plan(plan)

        if not args.commit:
            print("\nDry run -- nothing was deleted. Re-run with --commit to delete.")
            return 0

        result = commit_plan(session, plan)
        session.commit()
        print(f"\nRemoved {len(result.removed)} row(s):")
        for c in result.removed:
            print(f"  {c.table}  {c.public_ref}  {c.label}")
        if result.kept:
            print(f"Kept {len(result.kept)} planned row(s):")
            for c, why in result.kept:
                print(f"  {c.table}  {c.public_ref}  {c.label}  -- {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
