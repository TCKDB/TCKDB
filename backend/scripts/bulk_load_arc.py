"""Bulk-load ARC run directories into the dev database.

Usage:
    # Fresh start: drop + recreate DB, then load all ARC runs
    conda run -n tckdb_env python scripts/bulk_load_arc.py --fresh

    # Resume from where you left off (default — skips already-loaded runs)
    conda run -n tckdb_env python scripts/bulk_load_arc.py

    # Load first N runs only
    conda run -n tckdb_env python scripts/bulk_load_arc.py --limit 50

    # Load specific run directories by name
    conda run -n tckdb_env python scripts/bulk_load_arc.py --ids rmg_rxn_10626 rmg_rxn_10631

    # Custom source directory
    conda run -n tckdb_env python scripts/bulk_load_arc.py --src /other/path/to/arc/runs

    # Skip encoding Gaussian log files (faster, no raw artifact bytes stored)
    conda run -n tckdb_env python scripts/bulk_load_arc.py --no-artifacts

    # Use 4 parallel workers (each opens its own DB connection)
    conda run -n tckdb_env python scripts/bulk_load_arc.py --workers 4

    # Include freq scale factor citations from ARC's data/freq_scale_factors.yml
    conda run -n tckdb_env python scripts/bulk_load_arc.py --arc-repo /path/to/ARC

    # Clear progress tracking without dropping the DB
    conda run -n tckdb_env python scripts/bulk_load_arc.py --reset-progress

The DB is left alive after loading -- connect with:
    psql -h 127.0.0.1 -p 5432 -U tckdb -d tckdb_dev
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Force unbuffered output so prints appear immediately (even under conda run)
os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DEFAULT_ARC_DIR = Path("/mnt/Dropbox/PersonalFolders/Calvin/ZEUS_Converged")
_PROGRESS_FILE = Path(__file__).resolve().parent.parent / ".bulk_load_progress.json"


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

def load_progress() -> dict:
    """Load the progress file. Returns {completed: set, failed: set}."""
    if _PROGRESS_FILE.exists():
        data = json.loads(_PROGRESS_FILE.read_text())
        return {
            "completed": set(data.get("completed", [])),
            "failed": set(data.get("failed", [])),
            "skipped": set(data.get("skipped", [])),
        }
    return {"completed": set(), "failed": set(), "skipped": set()}


def save_progress(progress: dict):
    """Persist progress to disk."""
    data = {
        "completed": sorted(progress["completed"]),
        "failed": sorted(progress["failed"]),
        "skipped": sorted(progress["skipped"]),
    }
    _PROGRESS_FILE.write_text(json.dumps(data, indent=2))


def clear_progress():
    """Delete the progress file."""
    if _PROGRESS_FILE.exists():
        _PROGRESS_FILE.unlink()
        print(f"Progress cleared ({_PROGRESS_FILE})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_monoatomic(smiles: str | None) -> bool:
    """Check if a SMILES represents a single atom (e.g. [H], [O], [Cl])."""
    if not smiles:
        return False
    # Strip brackets and charges: [H] -> H, [O-] -> O, [CH] stays polyatomic
    inner = smiles.strip("[]")
    # Remove charge indicators
    inner = inner.rstrip("+-0123456789")
    # Single uppercase letter optionally followed by lowercase = one atom symbol
    # But reject if it contains H counts like "CH" (polyatomic)
    if len(inner) == 0:
        return False
    if len(inner) == 1 and inner.isupper():
        return True
    if len(inner) == 2 and inner[0].isupper() and inner[1].islower():
        return True
    return False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_arc_run_dirs(src: Path) -> list[Path]:
    """Find all subdirectories containing restart.yml, sorted by name.

    Uses os.scandir for speed — a single readdir syscall instead of
    stat-per-entry, which matters on network filesystems like Dropbox.
    """
    dirs = sorted(
        entry.path for entry in os.scandir(src) if entry.is_dir(follow_symlinks=False)
    )
    return [Path(d) for d in dirs]


def _run_checked(cmd: list[str], label: str, env: dict, **kwargs):
    """Run a subprocess with a visible status label."""
    print(f"  {label}...", end=" ", flush=True)
    t0 = time.time()
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, **kwargs)
    dt = time.time() - t0
    if result.returncode != 0:
        print(f"FAILED ({dt:.1f}s)")
        if result.stderr:
            print(f"    {result.stderr.strip()}")
        return result
    print(f"ok ({dt:.1f}s)")
    return result


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

    print("Setting up database...")

    if fresh:
        # Kill existing connections before dropping
        _run_checked(
            ["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "tckdb", "-d", "postgres",
             "-c", "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                   "WHERE datname = 'tckdb_dev' AND pid <> pg_backend_pid();"],
            "Terminating existing connections", db_env,
        )
        _run_checked(
            ["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "tckdb", "-d", "postgres",
             "-c", "DROP DATABASE IF EXISTS tckdb_dev;"],
            "Dropping tckdb_dev", db_env,
        )
        _run_checked(
            ["psql", "-h", "127.0.0.1", "-p", "5432", "-U", "tckdb", "-d", "postgres",
             "-c", "CREATE DATABASE tckdb_dev;"],
            "Creating tckdb_dev", db_env,
        )

    result = _run_checked(
        ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
        "Running Alembic migrations", db_env,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    if result.returncode != 0:
        sys.exit(1)
    print()


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


# ---------------------------------------------------------------------------
# Per-run worker (thread-safe, owns its own DB session)
# ---------------------------------------------------------------------------

def _process_one_run(
    run_dir: Path,
    user_id: int,
    include_artifacts: bool,
    arc_repo_dir: Path | None = None,
) -> tuple[str, str, str | None]:
    """Extract, build, and persist one ARC run in its own DB session.

    Returns ``(run_name, status, error_msg)`` where status is one of
    ``'success'``, ``'skipped'``, or ``'failed'``.
    """
    from scripts.arc_ingestion.extractor import ARCRunExtractor
    from scripts.arc_ingestion.builder import build_payload
    from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
    from app.workflows.computed_reaction import persist_computed_reaction_upload

    run_name = run_dir.name

    if not (run_dir / "restart.yml").exists():
        return (run_name, "skipped", None)

    engine, session = create_engine_and_session()
    try:
        extractor = ARCRunExtractor(run_dir, arc_repo_dir=arc_repo_dir)
        run_data = extractor.extract()

        if not run_data.reactions:
            return (run_name, "skipped", None)

        rxn = run_data.reactions[0]

        # Pre-flight: skip if any polyatomic species has empty/missing XYZ
        for label in rxn.reactant_labels + rxn.product_labels:
            sp = run_data.species.get(label)
            if sp is None:
                return (run_name, "skipped", None)
            is_monoatomic = _is_monoatomic(sp.smiles)
            has_xyz = (
                sp.xyz_file is not None
                and sp.xyz_file.exists()
                and sp.xyz_file.read_text().strip() != ""
            )
            if not has_xyz and not is_monoatomic:
                return (run_name, "skipped", None)

        payload = build_payload(run_data, run_dir, include_artifacts=include_artifacts)
        request = ComputedReactionUploadRequest(**payload)
        persist_computed_reaction_upload(session, request, created_by=user_id)
        session.commit()

        return (run_name, "success", None)

    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        return (run_name, "failed", f"{type(e).__name__}: {str(e)[:200]}")

    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Main load loop
# ---------------------------------------------------------------------------

def load_arc_runs(
    run_dirs: list[Path],
    user_id: int,
    workers: int = 1,
    include_artifacts: bool = True,
    arc_repo_dir: Path | None = None,
) -> dict:
    """Extract, build, and persist each ARC run. Returns stats."""
    progress = load_progress()
    already_done = progress["completed"] | progress["skipped"]

    # Filter out already-completed runs
    remaining = [d for d in run_dirs if d.name not in already_done]
    n_skipped_resume = len(run_dirs) - len(remaining)

    if n_skipped_resume > 0:
        print(f"  Resuming: skipping {n_skipped_resume} already-loaded runs\n")

    total_all = len(run_dirs)
    total = len(remaining)

    stats = {
        "total": total_all,
        "already_done": n_skipped_resume,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
    }

    if total == 0:
        print("  Nothing to do — all runs already loaded.")
        return {**stats, "elapsed_s": 0.0}

    bar_width = 40
    t0 = time.time()
    done_count = 0
    progress_lock = threading.Lock()

    def _progress_bar(done: int, elapsed: float, last_name: str, status: str):
        frac = done / total if total else 1
        filled = int(bar_width * frac)
        bar = "=" * filled + ">" * (1 if filled < bar_width else 0) + "." * (bar_width - filled - 1)
        pct = frac * 100

        if elapsed > 0 and done > 0:
            eta_s = elapsed / done * (total - done)
            eta = f"{eta_s / 60:.1f}m" if eta_s >= 60 else f"{eta_s:.0f}s"
        else:
            eta = "..."

        ok = stats["success"] + n_skipped_resume
        fail = stats["failed"]
        skip = stats["skipped"]
        counts = f"ok:{ok} fail:{fail} skip:{skip}"

        line = f"\r  [{bar}] {pct:5.1f}% {done}/{total}  ETA {eta}  {counts}  {status} {last_name}"
        print(f"{line:<120}", end="", file=sys.stderr, flush=True)

    def _handle_result(run_name: str, status: str, error_msg: str | None):
        nonlocal done_count
        with progress_lock:
            done_count += 1
            if status == "success":
                stats["success"] += 1
                progress["completed"].add(run_name)
            elif status == "skipped":
                stats["skipped"] += 1
                progress["skipped"].add(run_name)
            else:
                stats["failed"] += 1
                err_full = f"{run_name}: {error_msg}"
                stats["errors"].append(err_full)
                progress["failed"].add(run_name)
                if stats["failed"] <= 10:
                    print(f"\n  FAIL {err_full}", file=sys.stderr)
            save_progress(progress)
            _progress_bar(done_count, time.time() - t0, run_name, status)

    _progress_bar(0, 0, "", "...")

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_process_one_run, d, user_id, include_artifacts, arc_repo_dir): d.name
                for d in remaining
            }
            for future in as_completed(futures):
                run_name, status, error_msg = future.result()
                _handle_result(run_name, status, error_msg)
    else:
        for arc_dir in remaining:
            run_name, status, error_msg = _process_one_run(arc_dir, user_id, include_artifacts, arc_repo_dir)
            _handle_result(run_name, status, error_msg)

    # Final progress bar at 100%
    _progress_bar(total, time.time() - t0, "", "done")
    print(file=sys.stderr)  # newline after bar

    elapsed = time.time() - t0
    return {**stats, "elapsed_s": elapsed}


def print_db_stats(session):
    """Print row counts for key tables."""
    from sqlalchemy import text

    tables = [
        "species", "species_entry", "conformer_group", "conformer_observation",
        "chem_reaction", "reaction_entry", "transition_state", "transition_state_entry",
        "calculation", "geometry", "kinetics", "thermo", "thermo_nasa",
        "statmech", "level_of_theory", "software", "software_release",
    ]

    print("\n--- Database Stats ---")
    for table in tables:
        try:
            count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            print(f"  {table:35s} {count:>8,}")
        except Exception:
            print(f"  {table:35s} (error)")


def main():
    parser = argparse.ArgumentParser(description="Bulk-load ARC runs into dev DB")
    parser.add_argument("--src", type=Path, default=_DEFAULT_ARC_DIR,
                        help=f"Source directory containing ARC run folders (default: {_DEFAULT_ARC_DIR})")
    parser.add_argument("--fresh", action="store_true", help="Drop + recreate DB and clear progress")
    parser.add_argument("--limit", type=int, default=0, help="Load only first N runs")
    parser.add_argument("--ids", nargs="+", help="Load specific run directory names")
    parser.add_argument("--no-migrate", action="store_true", help="Skip Alembic migration")
    parser.add_argument("--reset-progress", action="store_true",
                        help="Clear progress tracking (re-process all runs) without dropping DB")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry previously failed runs")
    parser.add_argument("--no-artifacts", action="store_true",
                        help="Skip reading/encoding Gaussian log files (faster; metadata only)")
    parser.add_argument("--workers", type=int, default=1, metavar="N",
                        help="Number of parallel workers (each opens its own DB connection, default: 1)")
    parser.add_argument("--arc-repo", type=Path, default=None, metavar="PATH",
                        help="Path to the ARC source repository (used to read data/freq_scale_factors.yml "
                             "for frequency scale factor citations)")
    args = parser.parse_args()

    src = args.src.resolve()
    if not src.is_dir():
        print(f"Error: {src} is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Handle progress resets
    if args.fresh or args.reset_progress:
        clear_progress()

    if args.retry_failed:
        progress = load_progress()
        if progress["failed"]:
            print(f"Clearing {len(progress['failed'])} failed runs for retry...")
            progress["failed"].clear()
            save_progress(progress)

    # Setup DB
    if not args.no_migrate:
        setup_db(fresh=args.fresh)

    # Discover ARC run dirs
    if args.ids:
        run_dirs = [src / name for name in args.ids]
        missing = [d for d in run_dirs if not (d / "restart.yml").exists()]
        if missing:
            print(f"Error: missing restart.yml in: {[m.name for m in missing]}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Scanning for ARC runs...", end=" ", flush=True)
        run_dirs = get_arc_run_dirs(src)
        print(f"found {len(run_dirs)}")

    if args.limit > 0:
        run_dirs = run_dirs[:args.limit]

    # Show progress summary
    progress = load_progress()
    print(f"\nFound {len(run_dirs)} ARC runs in {src}")
    if progress["completed"] or progress["failed"] or progress["skipped"]:
        print(f"  Progress: {len(progress['completed'])} completed, "
              f"{len(progress['failed'])} failed, "
              f"{len(progress['skipped'])} skipped")
    workers = max(1, args.workers)
    include_artifacts = not args.no_artifacts
    arc_repo_dir: Path | None = args.arc_repo.resolve() if args.arc_repo else None
    if arc_repo_dir and not (arc_repo_dir / "data" / "freq_scale_factors.yml").exists():
        print(f"Warning: --arc-repo {arc_repo_dir} does not contain data/freq_scale_factors.yml; "
              f"scale factor citations will be omitted.", file=sys.stderr)
        arc_repo_dir = None
    print(f"  Workers: {workers}  Artifacts: {'yes' if include_artifacts else 'no (--no-artifacts)'}")
    if arc_repo_dir:
        print(f"  ARC repo: {arc_repo_dir} (freq scale factor citations enabled)")
    print(f"Loading into tckdb_dev...\n")

    # Create a short-lived session just for user setup and DB stats
    engine, session = create_engine_and_session()
    try:
        user_id = ensure_test_user(session)
        session.commit()
    finally:
        session.close()
        engine.dispose()

    stats = load_arc_runs(run_dirs, user_id, workers=workers, include_artifacts=include_artifacts,
                          arc_repo_dir=arc_repo_dir)

    print(f"\n--- Load Complete ---")
    print(f"  Total runs:    {stats['total']}")
    print(f"  Already done:  {stats['already_done']}")
    print(f"  New success:   {stats['success']}")
    print(f"  New failed:    {stats['failed']}")
    print(f"  New skipped:   {stats['skipped']}")
    print(f"  Time:          {stats['elapsed_s']:.1f}s")

    if stats["errors"]:
        print(f"\n--- First 10 Errors ---")
        for err in stats["errors"][:10]:
            print(f"  {err}")

    # Open a fresh session just for the stats query
    engine, session = create_engine_and_session()
    try:
        print_db_stats(session)
    finally:
        session.close()
        engine.dispose()

    print(f"\nDB is alive at: psql -h 127.0.0.1 -p 5432 -U tckdb -d tckdb_dev")
    print(f"Start the API with: conda run -n tckdb_env uvicorn app.api.app:create_app --factory --reload")


if __name__ == "__main__":
    main()
