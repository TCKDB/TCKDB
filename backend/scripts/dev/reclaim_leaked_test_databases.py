#!/usr/bin/env python3
"""Reclaim orphaned ``tckdb_test*`` databases left behind by the test harness.

Context
-------
Until the fixture in ``backend/tests/conftest.py`` was fixed, the session
``db_engine`` fixture created its database *before* entering the ``try`` that
drops it.  Any run whose ``alembic upgrade`` failed — or that was killed
between creation and the ``try`` — leaked a fully migrated database under a
name (``tckdb_test_<pid>``) that is never reused.  On one dev machine this
accumulated ~900 databases totalling 12 GB.

The leak is closed at source, and a conservative startup sweep now reclaims
future orphans automatically.  The sweep deliberately only touches databases
carrying the harness's ownership marker, so the *pre-existing* orphans — which
predate the marker — are invisible to it and must be removed deliberately, by
a human, with this script.

Coverage, and how it is kept
----------------------------
``TEST_DB_NAME`` below is the *only* thing this script will drop, and
``_sweep_stale_test_databases`` in ``backend/tests/conftest.py`` uses the
identical pattern.  Migration tests used to build scratch names outside it
(``tckdb_et_scope_migration_*``, ``tckdb_stage2_legacy_*``,
``tckdb_exec_env_migration_*``); a run killed partway leaked those permanently
because neither reclaimer could see them.

The fix was to bring the *names* inside the pattern rather than to widen the
pattern: tests now build scratch names with ``conftest.scratch_database_name``,
so a new migration test that follows the convention is covered automatically,
whereas a widened pattern would have to be remembered.
``backend/tests/test_scratch_database_names.py`` enforces both halves — that
every test issuing ``CREATE DATABASE`` uses the helper, and that this script
and the sweep still agree on the pattern.  If you change ``TEST_DB_NAME`` here,
that test will tell you the sweep no longer matches.

Safety model
------------
This script never decides for itself what to delete.  It runs in two phases:

    plan   read-only; enumerates candidates and writes a manifest
    apply  drops exactly the databases named in that manifest, and nothing else

``apply`` re-validates every entry at execution time rather than trusting the
manifest, because the manifest is a snapshot and the server is shared:

* the database must still carry the **same OID** recorded at plan time.  A
  drop-and-recreate under the same name (pid reuse by a later pytest run)
  changes the OID, so a recycled name is skipped rather than deleted.
* ``pg_stat_activity`` must report **no live backend** on it.
* the name must match the strict isolated-test-database pattern.
* the name must not be on the protected list.
* the ``DROP`` is issued **without** ``pg_terminate_backend``: if a connection
  appears in the race window, Postgres refuses and the script reports a skip.
  The server, not this script, is the final arbiter of "in use".

Usage
-----
    # 1. Generate the manifest (read-only). Review it.
    python backend/scripts/dev/reclaim_leaked_test_databases.py plan \
        --output /tmp/leaked_test_dbs.tsv

    # 2. Dry run against the manifest (read-only) — shows what apply would do.
    python backend/scripts/dev/reclaim_leaked_test_databases.py apply \
        --manifest /tmp/leaked_test_dbs.tsv

    # 3. Actually drop, after reading the dry-run output.
    python backend/scripts/dev/reclaim_leaked_test_databases.py apply \
        --manifest /tmp/leaked_test_dbs.tsv --yes

Connection settings come from ``DB_USER``/``DB_PASSWORD``/``DB_HOST``/
``DB_PORT`` (defaulting to the local dev Postgres).  This is a **local dev**
tool: never point it at a deployed database.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

# Databases that must never be dropped, whatever a manifest says.  Belt and
# braces on top of the name pattern below.
PROTECTED = frozenset(
    {
        "postgres",
        "template0",
        "template1",
        "tckdb",
        "tckdb_dev",
        "tckdb_prod",
        # Not created by the harness and never carries its marker, so the
        # in-process sweep leaves it alone -- but this script does not read
        # markers, and the name is inside ``tckdb_test%``. It is the
        # ``DB_NAME`` of the nightly CI job
        # (``.github/workflows/backend-nightly.yml``), so on a self-hosted
        # runner ``plan`` would otherwise offer up the job's own database.
        "tckdb_test_ci",
    }
)

# The only names this script will ever drop.  Note this is *stricter* than a
# ``LIKE 'tckdb_test%'`` prefix match: it anchors both ends and restricts the
# suffix alphabet, so e.g. ``tckdb_test-prod`` or ``tckdb_testing_real`` are
# rejected rather than matched.
TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")

MANIFEST_HEADER = "# oid\tdatname\tsize_bytes"


@dataclass(frozen=True)
class Candidate:
    oid: int
    datname: str
    size_bytes: int


def _dsn(db: str = "postgres") -> str:
    user = os.environ.get("DB_USER", "tckdb")
    password = os.environ.get("DB_PASSWORD", "tckdb")
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _connect() -> psycopg.Connection:
    # The local dev cluster is SQL_ASCII; without an explicit client_encoding
    # psycopg hands back ``bytes`` for text columns instead of ``str``.  The
    # test fixtures set the same option on their SQLAlchemy URL.
    conn = psycopg.connect(_dsn(), client_encoding="utf8")
    conn.autocommit = True  # DROP DATABASE cannot run inside a transaction.
    return conn


def _human(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _is_droppable_name(datname: str) -> bool:
    return datname not in PROTECTED and TEST_DB_NAME.fullmatch(datname) is not None


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def collect_candidates(conn: psycopg.Connection, exclude: set[str]) -> list[Candidate]:
    rows = conn.execute(
        r"""
        -- ``datname`` is the ``name`` type; cast so the driver yields str.
        SELECT d.oid, d.datname::text, pg_database_size(d.datname)
        FROM pg_database d
        WHERE d.datname LIKE 'tckdb\_test%'
          AND NOT d.datistemplate
          AND NOT EXISTS (
              SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname
          )
        ORDER BY d.oid
        """
    ).fetchall()

    candidates: list[Candidate] = []
    for oid, datname, size in rows:
        if not _is_droppable_name(datname):
            print(f"  skip (name not droppable): {datname}", file=sys.stderr)
            continue
        if datname in exclude:
            print(f"  skip (excluded): {datname}", file=sys.stderr)
            continue
        candidates.append(Candidate(int(oid), datname, int(size)))
    return candidates


def cmd_plan(args: argparse.Namespace) -> int:
    exclude = set(args.exclude or [])
    with _connect() as conn:
        candidates = collect_candidates(conn, exclude)

    total = sum(c.size_bytes for c in candidates)
    lines = [MANIFEST_HEADER]
    lines += [f"{c.oid}\t{c.datname}\t{c.size_bytes}" for c in candidates]

    output = Path(args.output)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {len(candidates)} candidate(s), {_human(total)} total, to {output}")
    print("Review the manifest, then re-run with 'apply --manifest <file>'.")
    return 0


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def read_manifest(path: Path) -> list[Candidate]:
    entries: list[Candidate] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise SystemExit(f"{path}:{lineno}: expected 3 tab-separated fields, got {len(parts)}")
        oid_text, datname, size_text = parts
        if not _is_droppable_name(datname):
            raise SystemExit(
                f"{path}:{lineno}: refusing to accept manifest -- '{datname}' is not an "
                "isolated test-database name."
            )
        entries.append(Candidate(int(oid_text), datname, int(size_text)))
    return entries


def cmd_apply(args: argparse.Namespace) -> int:
    manifest = Path(args.manifest)
    entries = read_manifest(manifest)
    if not entries:
        print("Manifest is empty — nothing to do.")
        return 0

    dry_run = not args.yes
    mode = "DRY RUN" if dry_run else "APPLYING"
    print(f"{mode}: {len(entries)} database(s) from {manifest}\n")

    dropped = skipped = 0
    reclaimed = 0
    with _connect() as conn:
        for entry in entries:
            # Re-validate against the *current* server state, not the snapshot.
            row = conn.execute(
                """
                SELECT d.oid,
                       EXISTS (
                           SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname
                       )
                FROM pg_database d
                WHERE d.datname = %s
                """,
                (entry.datname,),
            ).fetchone()

            if row is None:
                print(f"  skip (already gone):   {entry.datname}")
                skipped += 1
                continue

            current_oid, in_use = int(row[0]), bool(row[1])
            if current_oid != entry.oid:
                print(
                    f"  skip (recreated since plan; oid {entry.oid} -> {current_oid}): "
                    f"{entry.datname}"
                )
                skipped += 1
                continue
            if in_use:
                print(f"  skip (live connection): {entry.datname}")
                skipped += 1
                continue

            if dry_run:
                print(f"  would drop:            {entry.datname}  ({_human(entry.size_bytes)})")
                dropped += 1
                reclaimed += entry.size_bytes
                continue

            try:
                # No pg_terminate_backend: a connection appearing in the race
                # window must win, and Postgres will refuse the drop.
                conn.execute(f'DROP DATABASE "{entry.datname}"')
            except psycopg.Error as exc:
                print(f"  skip (server refused): {entry.datname}: {exc}")
                skipped += 1
                continue

            print(f"  dropped:               {entry.datname}  ({_human(entry.size_bytes)})")
            dropped += 1
            reclaimed += entry.size_bytes

    verb = "would be dropped" if dry_run else "dropped"
    print(f"\n{dropped} {verb}, {skipped} skipped, {_human(reclaimed)} reclaimed.")
    if dry_run:
        print("Nothing was changed. Re-run with --yes to execute.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="enumerate candidates (read-only)")
    plan.add_argument("--output", required=True, help="manifest path to write")
    plan.add_argument(
        "--exclude",
        action="append",
        metavar="DBNAME",
        help="database name to leave out of the manifest (repeatable)",
    )
    plan.set_defaults(func=cmd_plan)

    apply_ = sub.add_parser("apply", help="drop databases listed in a manifest")
    apply_.add_argument("--manifest", required=True, help="manifest produced by 'plan'")
    apply_.add_argument(
        "--yes",
        action="store_true",
        help="actually drop; without this the command is a dry run",
    )
    apply_.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
