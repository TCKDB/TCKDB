"""Create or restore a portable ``tckdb.archive.v1`` scientific archive."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

from app.api.deps import SessionLocal
from app.services.archive import ArchiveError, restore_archive, write_archive


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create an archive.")
    create.add_argument("output", type=Path)
    create.add_argument(
        "--overwrite",
        action="store_true",
        help="Atomically replace an existing output archive.",
    )

    restore = subparsers.add_parser(
        "restore",
        help="Restore into a revision-compatible freshly migrated database.",
    )
    restore.add_argument("input", type=Path)
    return parser.parse_args(argv)


def _publish_archive(temp_path: Path, output: Path, *, overwrite: bool) -> None:
    """Publish a completed archive without exposing partially written output."""
    if overwrite:
        os.replace(temp_path, output)
        return

    created = False
    try:
        with output.open("xb") as destination, temp_path.open("rb") as source:
            created = True
            shutil.copyfileobj(source, destination)
    except Exception:
        # Only unlink a path that this exclusive create opened successfully.
        if created:
            output.unlink()
        raise


def _create_archive(output: Path, *, overwrite: bool) -> None:
    if output.exists() and not overwrite:
        raise FileExistsError(f"output path {output} already exists; pass --overwrite to replace it")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp_path = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temp_path = Path(raw_temp_path)
    try:
        with SessionLocal() as session:
            manifest = write_archive(session, temp_path)
            session.rollback()  # Release the snapshot table locks promptly.
        _publish_archive(temp_path, output, overwrite=overwrite)
    finally:
        temp_path.unlink(missing_ok=True)

    revisions = ",".join(manifest["database_revisions"])
    print(f"Wrote {output}")
    print(f"Rows: {manifest['rows']['count']}  Blobs: {len(manifest['blobs'])}  Revision: {revisions}")


def _restore_archive(source: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"archive path {source} is not a file")

    with SessionLocal.begin() as session:
        report = restore_archive(session, source)
    print(f"Restored {source}")
    print(f"Rows: {report.rows_restored}  Blobs: {report.blobs_restored}  Tables: {len(report.table_row_counts)}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "create":
            _create_archive(args.output, overwrite=args.overwrite)
        else:
            _restore_archive(args.input)
    except (ArchiveError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
