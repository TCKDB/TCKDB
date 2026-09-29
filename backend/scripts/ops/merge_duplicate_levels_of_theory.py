#!/usr/bin/env python
"""Plan, and optionally merge, levels of theory split by basis spelling (#574).

Before #574, ``level_of_theory.lot_hash`` hashed basis names byte for byte,
so ``b3lyp/def2-tzvp`` (Psi4) and ``b3lyp/def2tzvp`` (Gaussian, ARC) were
two rows. Alembic revision ``38b06819f099`` re-keyed the existing rows: in
each group of rows that are now one level of theory, one row (the *holder*)
got the identity-keyed hash and every new upload resolves to it. The other
rows (the *duplicates*) kept their old hash and their calculations. This
script moves those calculations onto the holder and deletes the duplicate.

Run it after ``38b06819f099`` is applied. The group is found by recomputing
each row's hash with the application's own formula
(``calculation_resolution._level_of_theory_hash``), so this script and the
upload path cannot disagree about what one level of theory is.

**What it refuses.** A group is merged only if every one of these holds for
each duplicate row, and is reported as blocked otherwise:

* none of its calculations has ever been approved
  (``tckdb_record_is_accepted``). Repointing an approved calculation needs
  a declared accepted-science repair (ADR 0015). This script never
  declares one, so it never changes approved science;
* nothing else references it. ``frequency_scale_factor`` and
  ``energy_correction_scheme`` carry ``level_of_theory_id`` inside their own
  unique identity keys, so repointing them can collide with a row the
  holder already has. Every foreign key into ``level_of_theory`` is read
  from ``pg_constraint`` at run time; only ``calculation.lot_id`` is ever
  repointed;
* a holder exists, meaning a row whose ``lot_hash`` already equals the
  group's identity-keyed hash. If none does, ``38b06819f099`` has not run
  and the script plans nothing for that group.

**What changes on commit.** For each unblocked group, in its own savepoint:
``calculation.lot_id`` moves from each duplicate to the holder, then the
duplicate row is deleted. The holder's verbatim ``basis`` and its
``public_ref`` are not touched. The duplicate's ``public_ref`` stops
resolving. Nothing in the codebase cites a level of theory by
``public_ref`` in stored data (dataset releases cite records, not levels),
but an external bookmark of it would break.

Each repoint re-checks approval in the statement that writes, and the
accepted-science trigger is the backstop. A calculation approved after the
plan was printed makes the savepoint fail, and the group is kept and
reported.

Usage::

    # Plan only -- the default. Writes nothing.
    python backend/scripts/ops/merge_duplicate_levels_of_theory.py

    # Merge the unblocked groups. One transaction.
    python backend/scripts/ops/merge_duplicate_levels_of_theory.py --commit

    # --commit against a database not named tckdb_test* also needs:
    python backend/scripts/ops/merge_duplicate_levels_of_theory.py --commit --i-know-this-is-deployed

Rows are named by ``public_ref`` and content, never by primary key.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")

#: The only reference this script ever rewrites.
_REPOINTABLE = ("calculation", "lot_id")

_LOT_COLUMNS = (
    "method",
    "basis",
    "aux_basis",
    "cabs_basis",
    "dispersion",
    "solvent",
    "solvent_model",
    "keywords",
    "spin_treatment",
)


@dataclass(frozen=True)
class LotRow:
    row_id: int
    public_ref: str
    lot_hash: str
    label: str


@dataclass
class Duplicate:
    row: LotRow
    calculations: int = 0
    approved_calculations: int = 0
    other_references: dict[str, int] = field(default_factory=dict)

    def blockers(self) -> list[str]:
        reasons = []
        if self.approved_calculations:
            reasons.append(
                f"{self.approved_calculations} approved calculation(s); a repoint "
                "would change accepted science"
            )
        for ref, count in sorted(self.other_references.items()):
            reasons.append(f"cited by {count} {ref} row(s)")
        return reasons


@dataclass
class Group:
    identity_hash: str
    holder: LotRow | None
    duplicates: list[Duplicate]

    def blockers(self) -> list[str]:
        if self.holder is None:
            return [
                "no row holds the identity-keyed hash; apply Alembic revision "
                "38b06819f099 first"
            ]
        return [
            f"{d.row.public_ref}: {reason}"
            for d in self.duplicates
            for reason in d.blockers()
        ]


@dataclass
class Plan:
    references: list[tuple[str, str]]
    groups: list[Group]

    def mergeable(self) -> list[Group]:
        return [g for g in self.groups if not g.blockers()]

    def blocked(self) -> list[Group]:
        return [g for g in self.groups if g.blockers()]


@dataclass
class CommitResult:
    merged: list[tuple[Group, int]] = field(default_factory=list)
    kept: list[tuple[Group, str]] = field(default_factory=list)


def discover_references(session: Session) -> list[tuple[str, str]]:
    """Every ``(table, column)`` whose foreign key targets ``level_of_theory``."""
    rows = session.execute(
        text(
            """
            SELECT src.relname,
                   (SELECT a.attname FROM pg_attribute a
                     WHERE a.attrelid = c.conrelid AND a.attnum = c.conkey[1])
              FROM pg_constraint c
              JOIN pg_class src ON src.oid = c.conrelid
             WHERE c.contype = 'f'
               AND c.confrelid = 'public.level_of_theory'::regclass
             ORDER BY 1, 2
            """
        )
    ).all()
    return [(table, column) for table, column in rows]


def _quote(session: Session, name: str) -> str:
    return session.get_bind().dialect.identifier_preparer.quote(name)


def _identity_hash(mapping) -> str:
    from tckdb_schemas.fragments.refs import LevelOfTheoryRef

    from app.services.calculation_resolution import _level_of_theory_hash

    ref = LevelOfTheoryRef(**{column: mapping[column] for column in _LOT_COLUMNS})
    return _level_of_theory_hash(ref)


def _label(mapping) -> str:
    parts = [f"method={mapping['method']!r}", f"basis={mapping['basis']!r}"]
    for column in _LOT_COLUMNS[2:]:
        if mapping[column] is not None:
            parts.append(f"{column}={mapping[column]!r}")
    return " ".join(parts)


def build_plan(session: Session) -> Plan:
    """Group rows by identity-keyed hash; describe every group of two or more."""
    references = discover_references(session)
    columns = ", ".join(_LOT_COLUMNS)
    rows = session.execute(
        text(
            f"SELECT id, public_ref, lot_hash, {columns} "
            "FROM level_of_theory ORDER BY id"
        )
    ).mappings().all()

    by_hash: dict[str, list] = defaultdict(list)
    for mapping in rows:
        by_hash[_identity_hash(mapping)].append(mapping)

    groups: list[Group] = []
    for identity_hash, members in by_hash.items():
        if len(members) < 2:
            continue
        lot_rows = [
            LotRow(m["id"], m["public_ref"], m["lot_hash"], _label(m)) for m in members
        ]
        holder = next((r for r in lot_rows if r.lot_hash == identity_hash), None)
        duplicates = [
            _describe_duplicate(session, r, references)
            for r in lot_rows
            if r is not holder
        ]
        groups.append(Group(identity_hash, holder, duplicates))
    groups.sort(key=lambda g: g.holder.public_ref if g.holder else g.identity_hash)
    return Plan(references=references, groups=groups)


def _describe_duplicate(
    session: Session, row: LotRow, references: list[tuple[str, str]]
) -> Duplicate:
    duplicate = Duplicate(row=row)
    for table, column in references:
        count = session.scalar(
            text(
                f"SELECT count(*) FROM public.{_quote(session, table)} "
                f"WHERE {_quote(session, column)} = :id"
            ),
            {"id": row.row_id},
        )
        if (table, column) == _REPOINTABLE:
            duplicate.calculations = count
        elif count:
            duplicate.other_references[f"{table}.{column}"] = count
    duplicate.approved_calculations = session.scalar(
        text(
            "SELECT count(*) FROM calculation c WHERE c.lot_id = :id "
            "AND tckdb_record_is_accepted("
            "CAST('calculation' AS submission_record_type), c.id)"
        ),
        {"id": row.row_id},
    )
    return duplicate


def commit_plan(session: Session, plan: Plan) -> CommitResult:
    """Merge every unblocked group, each in its own savepoint. The caller commits.

    Each group is re-planned inside its savepoint, so a reference or an
    approval that appeared after the plan was printed keeps the group.
    """
    result = CommitResult()
    for group in plan.mergeable():
        assert group.holder is not None
        try:
            with session.begin_nested():
                fresh = [
                    _describe_duplicate(session, d.row, plan.references)
                    for d in group.duplicates
                ]
                reasons = [r for d in fresh for r in d.blockers()]
                if reasons:
                    result.kept.append((group, "; ".join(reasons)))
                    continue
                moved = 0
                for duplicate in group.duplicates:
                    moved += session.execute(
                        text(
                            "UPDATE calculation SET lot_id = :holder "
                            "WHERE lot_id = :dup AND NOT tckdb_record_is_accepted("
                            "CAST('calculation' AS submission_record_type), id)"
                        ),
                        {"holder": group.holder.row_id, "dup": duplicate.row.row_id},
                    ).rowcount
                    session.execute(
                        text("DELETE FROM level_of_theory WHERE id = :dup"),
                        {"dup": duplicate.row.row_id},
                    )
        except DBAPIError as exc:
            reason = str(exc.orig).splitlines()[0] if exc.orig else str(exc)
            result.kept.append((group, f"refused by the database: {reason}"))
            continue
        result.merged.append((group, moved))
    return result


def _print_plan(plan: Plan) -> None:
    print("Foreign keys into level_of_theory (discovered from pg_constraint):")
    for table, column in plan.references:
        note = "  (repointed)" if (table, column) == _REPOINTABLE else "  (blocks a merge)"
        print(f"  {table}.{column}{note}")
    print(f"\nDuplicate groups: {len(plan.groups)}")
    for group in plan.groups:
        holder = group.holder
        head = f"{holder.public_ref}  {holder.label}" if holder else "(no holder)"
        print(f"  holder {head}")
        for d in group.duplicates:
            print(
                f"    duplicate {d.row.public_ref}  {d.row.label}  "
                f"calculations={d.calculations}"
            )
        blockers = group.blockers()
        if blockers:
            for reason in blockers:
                print(f"    BLOCKED: {reason}")
        else:
            print("    mergeable")
    print(
        f"\nMergeable: {len(plan.mergeable())}. Blocked: {len(plan.blocked())}."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--commit",
        action="store_true",
        help="merge the unblocked groups. Without this, the default is a dry run.",
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
        plan = build_plan(session)
        _print_plan(plan)
        if not args.commit:
            print("\nDry run -- nothing was changed. Re-run with --commit to merge.")
            return 0

        result = commit_plan(session, plan)
        session.commit()
        print(f"\nMerged {len(result.merged)} group(s):")
        for group, moved in result.merged:
            assert group.holder is not None
            removed = ", ".join(d.row.public_ref for d in group.duplicates)
            print(
                f"  into {group.holder.public_ref}: {moved} calculation(s) repointed, "
                f"removed {removed}"
            )
        if result.kept:
            print(f"Kept {len(result.kept)} group(s):")
            for group, why in result.kept:
                name = group.holder.public_ref if group.holder else group.identity_hash
                print(f"  {name}  -- {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
