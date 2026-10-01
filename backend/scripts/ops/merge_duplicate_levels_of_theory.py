#!/usr/bin/env python
"""Plan, and optionally merge, levels of theory split by spelling (#574, #585, #618, #602).

The same script joins every split the identity keys close: basis spelling
(#574), method case (#585), curated method aliases such as ``wb97x-d`` /
``wb97xd``, and dispersion / solvent / solvent-model case (#618, #602).
Groups are found with the application's own hash formula, so it follows
whichever keys the running code has.

Before #574, ``level_of_theory.lot_hash`` hashed basis names byte for byte,
so ``b3lyp/def2-tzvp`` (Psi4) and ``b3lyp/def2tzvp`` (Gaussian, ARC) were
two rows. Alembic revision ``38b06819f099`` re-keyed the existing rows: in
each group of rows that are now one level of theory, one row (the *holder*)
got the identity-keyed hash and every new upload resolves to it. The other
rows (the *duplicates*) kept their old hash and their calculations. This
script moves those calculations onto the holder and turns each duplicate
into a *merged row*.

Run it after ``38b06819f099`` is applied, and again after any deploy that
migrated while the old API was still serving (an upload in that window
hashes the old way and can recreate a duplicate). Groups are found by
recomputing each row's hash with the application's own formula
(``calculation_resolution._level_of_theory_hash``), so this script and the
upload path cannot disagree about what one level of theory is.

**Merged rows are kept, not deleted.** A published release freezes, for
every calculation it cites, the calculation's ``level_of_theory_ref``
(``app/services/release/records.py``, ``calculation_provenance``) into
``release_artifact.content``. Deleting a duplicate would leave that ref
resolving to nothing. Instead the duplicate keeps its ``public_ref`` and its
old ``lot_hash``, and a ``level_of_theory_merge`` row names the holder. The read layer
resolves a merged row's ref to the holder
(``scientific_read.handles.canonical_level_of_theory_id`` and
``level_of_theory_ref_clause``). No calculation points at a merged row.

**What it refuses.** A group is merged only if every one of these holds, and
is reported as blocked otherwise:

* **No accepted science rests on a moved calculation.** Not the
  calculation's own approval only: every accepted-science record that cites
  it, directly or through other records, is found by walking foreign keys
  from ``pg_constraint`` and ownership from the accepted-science guard
  triggers (``tckdb_guard_accepted_child`` / ``_via_child``), checked with
  ``tckdb_record_is_accepted``. An approved thermo citing a statmech citing
  the calculation blocks the group. Repointing that science would need a
  declared accepted-science repair (ADR 0015); this script never declares
  one. The walk also reaches reviewable record types the database does not
  freeze (``molecular_property_observation``): an observation approved on
  the strength of a calculation blocks its group too, even though nothing
  but this script stops the calculation's level of theory changing under it
  (#591);
* **Nothing else references the duplicate.** ``frequency_scale_factor`` and
  ``energy_correction_scheme`` carry ``level_of_theory_id`` inside their own
  unique identity keys, so repointing them can collide with a row the
  holder already has. Every foreign key into ``level_of_theory`` is read at
  run time; only ``calculation.lot_id`` (repointed) and
  ``level_of_theory_merge`` (earlier merges, re-aimed at the holder) are
  handled. That includes ``thermo.energy_level_of_theory_id`` and
  ``statmech.energy_level_of_theory_id`` (#619): a declared energy level on a
  duplicate blocks the group, because this script never rewrites accepted
  science. New uploads resolve to the holder, and reads follow a merge;
* **A holder exists**: a row whose ``lot_hash`` already equals the group's
  identity-keyed hash. If none does, ``38b06819f099`` has not run.

**What changes on commit.** For each unblocked group, in its own savepoint:
``calculation.lot_id`` moves from each duplicate to the holder, any row
already merged into the duplicate is re-aimed at the holder (so a merge is
always one hop), and a ``level_of_theory_merge`` row records the duplicate
as merged into the holder. The holder's verbatim names, ``lot_hash`` and ``public_ref`` are not
touched. The group is re-planned inside the savepoint, so an approval or
reference that appeared after the plan was printed keeps the group, and the
accepted-science trigger is the backstop for the calculation's own
approval.

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

#: References into ``level_of_theory`` this script rewrites. Any other one
#: that cites a duplicate blocks its group.
_REPOINTED = {
    ("calculation", "lot_id"),
    ("level_of_theory_merge", "merged_lot_id"),
    ("level_of_theory_merge", "into_lot_id"),
    # ADR 0021: a duplicate's composite-scheme binding is moved to the holder
    # (or dropped when the holder is bound already), so a merged level is never
    # bound. Absent on a database older than ``d7a3f1b9c284``.
    ("level_of_theory_composite", "level_of_theory_id"),
}

#: Ownership columns that are also citations. ``calculation_dependency`` is
#: guarded as a child of *both* its calculations, but only the child depends
#: on the parent: arriving at a calculation through
#: ``child_calculation_id`` names a calculation it depends on, not one that
#: depends on it.
_NOT_A_CITATION = {("calculation_dependency", "child_calculation_id")}

#: Upper bound on the citation walk. Real chains are two or three hops
#: (calculation <- statmech <- thermo); the bound only stops a cycle.
_MAX_HOPS = 8

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
    # ADR 0021: part of the identity hash when set, so a regroup that left it
    # out would merge a frozen-core level into an all-electron one.
    "core_treatment",
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
    #: One line per accepted record resting on a calculation that would move.
    accepted_science: list[str] = field(default_factory=list)
    other_references: dict[str, int] = field(default_factory=dict)

    def blockers(self) -> list[str]:
        reasons = [
            f"{line}; a repoint would change accepted science"
            for line in self.accepted_science
        ]
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
                "no unmerged row holds the identity-keyed hash: revisions "
                "38b06819f099 / c8424fe82997 may not be applied, or the holder "
                "is a merged row or was left un-re-hashed (see the upgrade's "
                "'NOT re-hashed' lines); resolve by hand"
            ]
        return [
            f"{d.row.public_ref}: {reason}"
            for d in self.duplicates
            for reason in d.blockers()
        ]


@dataclass(frozen=True)
class Schema:
    """What the walk needs from the catalog, read once per run."""

    #: ``(table, column)`` for every foreign key into ``level_of_theory``.
    lot_references: list[tuple[str, str]]
    #: Tables that are accepted-science roots (``tckdb_is_accepted_science_type``).
    roots: frozenset[str]
    #: Reviewable record types that are *not* roots, such as
    #: ``molecular_property_observation``. The database does not freeze them
    #: (there is no guard trigger), but they carry a ``record_review`` row, so
    #: ``tckdb_record_is_accepted`` can say whether reviewers approved one.
    reviewable: frozenset[str]
    #: target table -> ``(source table, column)`` for every foreign key into it.
    incoming: dict[str, list[tuple[str, str]]]
    #: child table -> ``(root type, ownership column, via table, via owner column)``;
    #: ``via`` is ``None`` for a direct child.
    owners: dict[str, list[tuple[str, str, str | None, str | None]]]


@dataclass
class Plan:
    schema: Schema
    groups: list[Group]

    @property
    def references(self) -> list[tuple[str, str]]:
        return self.schema.lot_references

    def mergeable(self) -> list[Group]:
        return [g for g in self.groups if not g.blockers()]

    def blocked(self) -> list[Group]:
        return [g for g in self.groups if g.blockers()]


@dataclass
class CommitResult:
    merged: list[tuple[Group, int]] = field(default_factory=list)
    kept: list[tuple[Group, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def read_schema(session: Session) -> Schema:
    """Foreign keys, accepted-science roots and ownership, from the catalog."""
    fks = session.execute(
        text(
            """
            SELECT src.relname,
                   (SELECT a.attname FROM pg_attribute a
                     WHERE a.attrelid = c.conrelid AND a.attnum = c.conkey[1]),
                   tgt.relname
              FROM pg_constraint c
              JOIN pg_class src ON src.oid = c.conrelid
              JOIN pg_namespace ns ON ns.oid = src.relnamespace
              JOIN pg_class tgt ON tgt.oid = c.confrelid
             WHERE c.contype = 'f' AND ns.nspname = 'public'
               AND array_length(c.conkey, 1) = 1
             ORDER BY 1, 2
            """
        )
    ).all()
    roots = frozenset(
        session.scalars(
            text(
                """
                SELECT e.enumlabel
                  FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                 WHERE t.typname = 'submission_record_type'
                   AND tckdb_is_accepted_science_type(
                           CAST(e.enumlabel AS submission_record_type))
                   AND to_regclass('public.' || e.enumlabel) IS NOT NULL
                """
            )
        ).all()
    )
    reviewable = frozenset(
        session.scalars(
            text(
                """
                SELECT e.enumlabel
                  FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
                 WHERE t.typname = 'submission_record_type'
                   AND NOT tckdb_is_accepted_science_type(
                           CAST(e.enumlabel AS submission_record_type))
                   AND to_regclass('public.' || e.enumlabel) IS NOT NULL
                """
            )
        ).all()
    )
    triggers = session.execute(
        text(
            """
            SELECT t.tgrelid::regclass::text, p.proname,
                   encode(t.tgargs, 'escape')
              FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
             WHERE NOT t.tgisinternal
               AND p.proname IN ('tckdb_guard_accepted_child',
                                 'tckdb_guard_accepted_via_child')
            """
        )
    ).all()

    incoming: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for src, column, target in fks:
        incoming[target].append((src, column))
    owners: dict[str, list[tuple[str, str, str | None, str | None]]] = defaultdict(list)
    for table, function, raw in triggers:
        args = [a for a in raw.split("\\000") if a]
        if function == "tckdb_guard_accepted_child":
            root_type, *columns = args
            for column in columns:
                owners[table].append((root_type, column, None, None))
        else:
            # (root type, fk column, via table, via id column, via owner column)
            root_type, column, via_table, _via_id, via_owner = args
            owners[table].append((root_type, column, via_table, via_owner))
    return Schema(
        lot_references=[(s, c) for s, c, t in fks if t == "level_of_theory"],
        roots=roots,
        reviewable=reviewable,
        incoming=dict(incoming),
        owners=dict(owners),
    )


def _quote(session: Session, name: str) -> str:
    return session.get_bind().dialect.identifier_preparer.quote(name)


# ---------------------------------------------------------------------------
# Accepted science resting on a calculation
# ---------------------------------------------------------------------------


def _citers(
    session: Session, schema: Schema, table: str, ids: set[int]
) -> list[tuple[int, str, int]]:
    """Accepted-science records that cite rows ``ids`` of root ``table``.

    :returns: ``(cited id, citing root table, citing id)`` triples.
    """
    found: list[tuple[int, str, int]] = []
    for src, column in schema.incoming.get(table, []):
        if (src, column) in _NOT_A_CITATION:
            continue
        own = schema.owners.get(src, [])
        if any(c == column and via is None for _t, c, via, _o in own):
            # ``src`` rows arriving through their ownership column are this
            # record's own children, which do not cite it.
            others = [o for o in own if o[1] != column]
            if not others:
                continue
        else:
            others = own
        q_src, q_col = _quote(session, src), _quote(session, column)
        if src in schema.roots or src in schema.reviewable:
            rows = session.execute(
                text(f"SELECT {q_col}, id FROM public.{q_src} WHERE {q_col} = ANY(:ids)"),
                {"ids": sorted(ids)},
            ).all()
            found.extend((cited, src, citer) for cited, citer in rows)
            continue
        for root_type, owner_col, via_table, via_owner in others:
            q_owner = _quote(session, owner_col)
            if via_table is None:
                sql = (
                    f"SELECT s.{q_col}, s.{q_owner} FROM public.{q_src} s "
                    f"WHERE s.{q_col} = ANY(:ids) AND s.{q_owner} IS NOT NULL"
                )
            else:
                sql = (
                    f"SELECT s.{q_col}, v.{_quote(session, via_owner)} "
                    f"FROM public.{q_src} s "
                    f"JOIN public.{_quote(session, via_table)} v ON v.id = s.{q_owner} "
                    f"WHERE s.{q_col} = ANY(:ids)"
                )
            rows = session.execute(text(sql), {"ids": sorted(ids)}).all()
            found.extend((cited, root_type, citer) for cited, citer in rows)
    return found


def accepted_science_on(
    session: Session, schema: Schema, calculation_ids: list[int]
) -> dict[int, list[str]]:
    """For each calculation, the accepted records resting on it.

    Includes the calculation's own approval, then walks citing records
    outwards (``calculation <- statmech <- thermo``), checking each with
    ``tckdb_record_is_accepted``.

    :returns: calculation id -> human-readable lines naming public refs.
    """
    out: dict[int, list[str]] = {cid: [] for cid in calculation_ids}
    if not calculation_ids:
        return out
    # (table, id) -> the calculations it rests on
    origin: dict[tuple[str, int], set[int]] = {("calculation", c): {c} for c in calculation_ids}
    frontier: dict[str, set[int]] = {"calculation": set(calculation_ids)}
    seen: set[tuple[str, int]] = set(origin)
    for _hop in range(_MAX_HOPS):
        if not frontier:
            break
        nxt: dict[str, set[int]] = defaultdict(set)
        for table, ids in frontier.items():
            for cited, citer_table, citer_id in _citers(session, schema, table, ids):
                node = (citer_table, citer_id)
                origin.setdefault(node, set()).update(origin[(table, cited)])
                if node not in seen:
                    seen.add(node)
                    nxt[citer_table].add(citer_id)
        frontier = dict(nxt)

    for (table, record_id), calcs in sorted(origin.items()):
        accepted = session.scalar(
            text(
                "SELECT tckdb_record_is_accepted("
                "CAST(:t AS submission_record_type), :id)"
            ),
            {"t": table, "id": record_id},
        )
        if not accepted:
            continue
        ref = session.scalar(
            text(f"SELECT public_ref FROM public.{_quote(session, table)} WHERE id = :id"),
            {"id": record_id},
        )
        for cid in calcs:
            calc_ref = session.scalar(
                text("SELECT public_ref FROM calculation WHERE id = :id"), {"id": cid}
            )
            if table == "calculation" and record_id == cid:
                out[cid].append(f"calculation {calc_ref} is approved")
            else:
                out[cid].append(
                    f"calculation {calc_ref} is cited by accepted {table} {ref}"
                )
    return out


# ---------------------------------------------------------------------------
# Plan and commit
# ---------------------------------------------------------------------------


def _identity_hash(mapping) -> str:
    from tckdb_schemas.fragments.refs import LevelOfTheoryRef

    from app.services.calculation_resolution import _level_of_theory_hash

    # A mapping read from a database older than ``e5b2d8a4c613`` has no
    # ``core_treatment`` key: every level there is NULL, which is the field's default.
    ref = LevelOfTheoryRef(**{column: mapping[column] for column in _LOT_COLUMNS if column in mapping})
    return _level_of_theory_hash(ref)


def _label(mapping) -> str:
    parts = [f"method={mapping['method']!r}", f"basis={mapping['basis']!r}"]
    for column in _LOT_COLUMNS[2:]:
        if mapping.get(column) is not None:
            parts.append(f"{column}={mapping[column]!r}")
    return " ".join(parts)


def _lot_columns_present(session: Session) -> tuple[str, ...]:
    """The identity columns this database has.

    The script runs after upgrades but is also run against a database that has
    not reached ``e5b2d8a4c613`` yet (the migration tests stop at older
    revisions); ``core_treatment`` is simply absent there and every level is
    NULL for it.
    """
    present = set(
        session.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'level_of_theory'"
            )
        )
    )
    return tuple(column for column in _LOT_COLUMNS if column in present)


def build_plan(session: Session, schema: Schema | None = None) -> Plan:
    """Group unmerged rows by identity-keyed hash; describe groups of two or more."""
    schema = schema or read_schema(session)
    columns = ", ".join(_lot_columns_present(session))
    rows = session.execute(
        text(
            f"SELECT id, public_ref, lot_hash, {columns} "
            "FROM level_of_theory WHERE id NOT IN "
            "(SELECT merged_lot_id FROM level_of_theory_merge) ORDER BY id"
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
            _describe_duplicate(session, schema, r, holder) for r in lot_rows if r is not holder
        ]
        groups.append(Group(identity_hash, holder, duplicates))
    groups.sort(key=lambda g: g.holder.public_ref if g.holder else g.identity_hash)
    return Plan(schema=schema, groups=groups)


def _describe_duplicate(
    session: Session, schema: Schema, row: LotRow, holder: LotRow | None = None
) -> Duplicate:
    duplicate = Duplicate(row=row)
    if holder is not None and ("level_of_theory_composite", "level_of_theory_id") in schema.lot_references:
        # Two levels the merge would join are bound to different recipes: moving
        # or dropping one binding would pick a recipe silently. Named methods
        # cannot do this today (one key, one scheme); a declared scheme can.
        differs = session.scalar(
            text(
                "SELECT count(*) FROM level_of_theory_composite d "
                "JOIN level_of_theory_composite h ON h.level_of_theory_id = :holder "
                "WHERE d.level_of_theory_id = :dup AND d.scheme_id <> h.scheme_id"
            ),
            {"holder": holder.row_id, "dup": row.row_id},
        )
        if differs:
            duplicate.other_references["level_of_theory_composite.scheme_id (differs from the kept row)"] = differs
    for table, column in schema.lot_references:
        count = session.scalar(
            text(
                f"SELECT count(*) FROM public.{_quote(session, table)} "
                f"WHERE {_quote(session, column)} = :id"
            ),
            {"id": row.row_id},
        )
        if (table, column) == ("calculation", "lot_id"):
            duplicate.calculations = count
        elif (table, column) not in _REPOINTED and count:
            duplicate.other_references[f"{table}.{column}"] = count
    calc_ids = list(
        session.scalars(
            text("SELECT id FROM calculation WHERE lot_id = :id ORDER BY id"),
            {"id": row.row_id},
        )
    )
    on = accepted_science_on(session, schema, calc_ids)
    duplicate.accepted_science = [line for cid in calc_ids for line in on[cid]]
    return duplicate


def commit_plan(session: Session, plan: Plan) -> CommitResult:
    """Merge every unblocked group, each in its own savepoint. The caller commits."""
    result = CommitResult()
    for group in plan.mergeable():
        assert group.holder is not None
        holder_id = group.holder.row_id
        try:
            with session.begin_nested():
                fresh = [
                    _describe_duplicate(session, plan.schema, d.row, group.holder)
                    for d in group.duplicates
                ]
                reasons = [r for d in fresh for r in d.blockers()]
                if reasons:
                    result.kept.append((group, "; ".join(reasons)))
                    continue
                moved = 0
                for duplicate in group.duplicates:
                    dup_id = duplicate.row.row_id
                    moved += session.execute(
                        text(
                            "UPDATE calculation SET lot_id = :holder "
                            "WHERE lot_id = :dup AND NOT tckdb_record_is_accepted("
                            "CAST('calculation' AS submission_record_type), id)"
                        ),
                        {"holder": holder_id, "dup": dup_id},
                    ).rowcount
                    session.execute(
                        text(
                            "UPDATE level_of_theory_merge SET into_lot_id = :holder "
                            "WHERE into_lot_id = :dup"
                        ),
                        {"holder": holder_id, "dup": dup_id},
                    )
                    session.execute(
                        text(
                            "INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) "
                            "VALUES (:dup, :holder)"
                        ),
                        {"holder": holder_id, "dup": dup_id},
                    )
                    if ("level_of_theory_composite", "level_of_theory_id") in plan.schema.lot_references:
                        session.execute(
                            text(
                                "INSERT INTO level_of_theory_composite "
                                "(level_of_theory_id, scheme_id, binding_source) "
                                "SELECT :holder, scheme_id, binding_source "
                                "FROM level_of_theory_composite WHERE level_of_theory_id = :dup "
                                "ON CONFLICT (level_of_theory_id) DO NOTHING"
                            ),
                            {"holder": holder_id, "dup": dup_id},
                        )
                        session.execute(
                            text("DELETE FROM level_of_theory_composite WHERE level_of_theory_id = :dup"),
                            {"dup": dup_id},
                        )
                    left = session.scalar(
                        text("SELECT count(*) FROM calculation WHERE lot_id = :dup"),
                        {"dup": dup_id},
                    )
                    if left:
                        raise RuntimeError(
                            f"{duplicate.row.public_ref} still carries {left} "
                            "calculation(s) after the repoint"
                        )
        except (DBAPIError, RuntimeError) as exc:
            orig = getattr(exc, "orig", None)
            reason = str(orig).splitlines()[0] if orig else str(exc)
            result.kept.append((group, f"refused: {reason}"))
            continue
        result.merged.append((group, moved))
    return result


def _print_plan(plan: Plan) -> None:
    print("Foreign keys into level_of_theory (discovered from pg_constraint):")
    for table, column in plan.references:
        note = "  (rewritten)" if (table, column) in _REPOINTED else "  (blocks a merge)"
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
            merged = ", ".join(d.row.public_ref for d in group.duplicates)
            print(
                f"  into {group.holder.public_ref}: {moved} calculation(s) repointed, "
                f"{merged} kept as merged row(s) resolving to it"
            )
        if result.kept:
            print(f"Kept {len(result.kept)} group(s):")
            for group, why in result.kept:
                name = group.holder.public_ref if group.holder else group.identity_hash
                print(f"  {name}  -- {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
