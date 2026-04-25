"""Bulk-load all SDF reactions into the dev database.

Usage:
    # Fresh start: drop + recreate DB, then load all reactions
    conda run -n tckdb_env python scripts/bulk_load_reactions.py --fresh

    # Load into existing DB (skips migration, continues from where you left off)
    conda run -n tckdb_env python scripts/bulk_load_reactions.py

    # Load first N reactions only
    conda run -n tckdb_env python scripts/bulk_load_reactions.py --limit 50

    # Load specific reactions
    conda run -n tckdb_env python scripts/bulk_load_reactions.py --ids kfir_rxn_2 rmg_rxn_10

The DB is left alive after loading — connect with:
    psql -h 127.0.0.1 -p 5432 -U tckdb -d tckdb_dev
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
import argparse
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.parse_sdf_to_bundle import sdf_to_bundle, _SDF_DIR, _CSV_PATH


def get_all_rxn_ids(sdf_dir: Path = _SDF_DIR) -> list[str]:
    """Get all reaction IDs from SDF directory, sorted."""
    return sorted(
        f.stem for f in sdf_dir.glob("*.sdf")
    )


def get_rxn_ids_with_kinetics(csv_path: Path = _CSV_PATH) -> set[str]:
    """Get reaction IDs that have kinetics data in the CSV."""
    import csv
    ids = set()
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            ids.add(row["rxn"])
    return ids


def setup_db(fresh: bool = False):
    """Set up the dev database."""
    db_env = {
        **os.environ,
        "DB_USER": "tckdb",
        "DB_PASSWORD": "tckdb",
        "DB_NAME": "tckdb_dev",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
    }

    if fresh:
        print("Dropping and recreating tckdb_dev...")
        subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "tckdb", "-d", "postgres",
             "-c", "DROP DATABASE IF EXISTS tckdb_dev;"],
            env=db_env, capture_output=True,
        )
        subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "tckdb", "-d", "postgres",
             "-c", "CREATE DATABASE tckdb_dev;"],
            env=db_env, capture_output=True,
        )

    print("Running Alembic migrations...")
    result = subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
        env=db_env, capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    if result.returncode != 0:
        print(f"Migration failed:\n{result.stderr}")
        sys.exit(1)
    print("Migrations complete.")


def create_engine_and_session():
    """Create SQLAlchemy engine + session for the dev DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    url = "postgresql+psycopg://tckdb:tckdb@127.0.0.1:5432/tckdb_dev?client_encoding=utf8"
    engine = create_engine(url, echo=False)
    return engine, Session(engine)


def ensure_test_user(session) -> int:
    """Create or fetch a bulk-load user, return user ID."""
    from app.db.models.app_user import AppUser

    user = session.query(AppUser).filter_by(username="bulk_loader").first()
    if user is None:
        user = AppUser(
            username="bulk_loader",
            full_name="Bulk Load Script",
            role="admin",
        )
        session.add(user)
        session.flush()
    return user.id


def load_reactions(
    session,
    rxn_ids: list[str],
    user_id: int,
) -> dict:
    """Load reactions into the database. Returns stats."""
    from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
    from app.workflows.computed_reaction import persist_computed_reaction_upload

    stats = {
        "total": len(rxn_ids),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    t0 = time.time()

    for i, rxn_id in enumerate(rxn_ids):
        t_start = time.time()

        try:
            # Parse SDF → bundle dict
            bundle_dict = sdf_to_bundle(rxn_id)

            if not bundle_dict.get("kinetics"):
                stats["skipped"] += 1
                print(f"  [{i+1}/{stats['total']}] {rxn_id}: SKIPPED (no kinetics)")
                continue

            # Validate + create Pydantic model
            request = ComputedReactionUploadRequest(**bundle_dict)

            # Persist to DB
            result = persist_computed_reaction_upload(session, request, created_by=user_id)
            session.commit()

            dt = time.time() - t_start
            stats["success"] += 1

            if (i + 1) % 50 == 0 or i == 0:
                elapsed = time.time() - t0
                rate = stats["success"] / elapsed if elapsed > 0 else 0
                print(
                    f"  [{i+1}/{stats['total']}] {rxn_id}: OK "
                    f"({result['species_count']} sp, {len(result['kinetics_ids'])} kin) "
                    f"[{dt:.2f}s, {rate:.1f} rxn/s]"
                )

        except Exception as e:
            session.rollback()
            stats["failed"] += 1
            err_msg = f"{rxn_id}: {type(e).__name__}: {str(e)[:200]}"
            stats["errors"].append(err_msg)

            if stats["failed"] <= 10:
                print(f"  [{i+1}/{stats['total']}] {err_msg}")
            elif stats["failed"] == 11:
                print("  ... suppressing further error details ...")

    elapsed = time.time() - t0
    return {**stats, "elapsed_s": elapsed}


def print_db_stats(session):
    """Print row counts for key tables."""
    from sqlalchemy import text

    tables = [
        "species", "species_entry", "conformer_group", "conformer_observation",
        "chem_reaction", "reaction_entry", "transition_state", "transition_state_entry",
        "calculation", "geometry", "kinetics", "thermo", "thermo_nasa",
        "level_of_theory", "software", "software_release",
    ]

    print("\n--- Database Stats ---")
    for table in tables:
        try:
            count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"  {table:35s} {count:>8,}")
        except Exception:
            print(f"  {table:35s} (error)")


def main():
    parser = argparse.ArgumentParser(description="Bulk-load SDF reactions into dev DB")
    parser.add_argument("--fresh", action="store_true", help="Drop + recreate DB first")
    parser.add_argument("--limit", type=int, default=0, help="Load only first N reactions")
    parser.add_argument("--ids", nargs="+", help="Load specific reaction IDs")
    parser.add_argument("--no-migrate", action="store_true", help="Skip Alembic migration")
    args = parser.parse_args()

    # Setup DB
    if not args.no_migrate:
        setup_db(fresh=args.fresh)

    # Get reaction IDs
    if args.ids:
        rxn_ids = args.ids
    else:
        all_ids = get_all_rxn_ids()
        with_kinetics = get_rxn_ids_with_kinetics()
        rxn_ids = [r for r in all_ids if r in with_kinetics]

    if args.limit > 0:
        rxn_ids = rxn_ids[:args.limit]

    print(f"\nLoading {len(rxn_ids)} reactions into tckdb_dev...\n")

    # Connect and load
    engine, session = create_engine_and_session()

    try:
        user_id = ensure_test_user(session)
        session.commit()

        stats = load_reactions(session, rxn_ids, user_id)

        print(f"\n--- Load Complete ---")
        print(f"  Total:   {stats['total']}")
        print(f"  Success: {stats['success']}")
        print(f"  Failed:  {stats['failed']}")
        print(f"  Skipped: {stats['skipped']}")
        print(f"  Time:    {stats['elapsed_s']:.1f}s")

        if stats["errors"]:
            print(f"\n--- First 10 Errors ---")
            for err in stats["errors"][:10]:
                print(f"  {err}")

        print_db_stats(session)

    finally:
        session.close()
        engine.dispose()

    print(f"\nDB is alive at: psql -h 127.0.0.1 -p 5432 -U tckdb -d tckdb_dev")


if __name__ == "__main__":
    main()
