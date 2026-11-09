"""Create, run and tear down a throw-away local TCKDB instance for a snapshot build.

An instance is a database (``tckdb_test_snapshot_<name>``), an object-store
bucket (``tckdb-test-snapshot-<name>``) and an API process on a local port.
Nothing here touches any other database or bucket, and ``drop`` refuses names
that do not carry the ``tckdb_test_`` prefix.

    python scripts/paper_snapshot/instance.py up   --name a --port 8101
    python scripts/paper_snapshot/instance.py down --name a [--drop]
    python scripts/paper_snapshot/instance.py restore --name c --archive snapshot/tckdb.archive

``up`` needs the compose stack's Postgres (``docker compose up -d db``) and an
S3-compatible store reachable at ``S3_ENDPOINT_URL`` (default
``http://localhost:9000``, credentials ``S3_ACCESS_KEY``/``S3_SECRET_KEY``).
State (pid, log, ports) lives under ``--work-dir``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
PACKAGE_PATHS = [
    REPO_ROOT / "schemas" / "python" / "tckdb-schemas",
    REPO_ROOT / "clients" / "python" / "src",
    REPO_ROOT / "clients" / "python" / "adapters" / "chemkin",
    BACKEND,
]


def _db_env(name: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        DB_USER=os.environ.get("DB_USER", "tckdb"),
        DB_PASSWORD=os.environ.get("DB_PASSWORD", "tckdb"),
        DB_HOST=os.environ.get("DB_HOST", "127.0.0.1"),
        DB_PORT=os.environ.get("DB_PORT", "5432"),
        DB_NAME=f"tckdb_test_snapshot_{name}",
        S3_BUCKET=f"tckdb-test-snapshot-{name}",
        S3_ENDPOINT_URL=os.environ.get("S3_ENDPOINT_URL", "http://localhost:9000"),
        S3_ACCESS_KEY=os.environ.get("S3_ACCESS_KEY", "tckdb"),
        S3_SECRET_KEY=os.environ.get("S3_SECRET_KEY", "tckdb_secret"),
        # The build is one client making thousands of calls; the fixed per-minute budgets would only slow it.
        RATE_LIMIT_ENABLED="false",
        PYTHONPATH=os.pathsep.join(str(p) for p in PACKAGE_PATHS),
    )
    return env


def _psql_connect(env: dict[str, str], dbname: str = "postgres"):
    import psycopg

    return psycopg.connect(
        host=env["DB_HOST"], port=env["DB_PORT"], user=env["DB_USER"], password=env["DB_PASSWORD"],
        dbname=dbname, autocommit=True,
    )


def _s3(env: dict[str, str]):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=env["S3_ENDPOINT_URL"],
        aws_access_key_id=env["S3_ACCESS_KEY"],
        aws_secret_access_key=env["S3_SECRET_KEY"],
        region_name="us-east-1",
    )


def up(name: str, port: int, work_dir: Path) -> None:
    env = _db_env(name)
    dbname = env["DB_NAME"]
    with _psql_connect(env) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone()
        if exists:
            raise SystemExit(f"database {dbname} already exists; run 'down --drop' first")
        conn.execute(f'create database "{dbname}"')
    migrated = subprocess.run(["alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True, text=True)
    if migrated.returncode != 0:
        raise SystemExit(f"alembic upgrade head failed:\n{migrated.stdout}\n{migrated.stderr}")
    _s3(env).create_bucket(Bucket=env["S3_BUCKET"])
    work_dir.mkdir(parents=True, exist_ok=True)
    log = (work_dir / f"api_{name}.log").open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    (work_dir / f"instance_{name}.json").write_text(json.dumps({"pid": proc.pid, "port": port, "db": dbname}))
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/status", timeout=2) as response:
                if response.status == 200:
                    print(f"instance {name}: http://127.0.0.1:{port}  db={dbname}  bucket={env['S3_BUCKET']}")
                    return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"API did not come up; see {work_dir / f'api_{name}.log'}")


def down(name: str, work_dir: Path, drop: bool) -> None:
    env = _db_env(name)
    state = work_dir / f"instance_{name}.json"
    if state.is_file():
        pid = json.loads(state.read_text())["pid"]
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        state.unlink()
    if drop:
        dbname = env["DB_NAME"]
        if not dbname.startswith("tckdb_test_"):
            raise SystemExit(f"refusing to drop {dbname}: not a tckdb_test_* database")
        with _psql_connect(env) as conn:
            conn.execute(f'drop database if exists "{dbname}" with (force)')
        client = _s3(env)
        bucket = env["S3_BUCKET"]
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if keys:
                    client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            client.delete_bucket(Bucket=bucket)
        except client.exceptions.NoSuchBucket:
            pass


def restore(name: str, archive: Path) -> None:
    """Restore an archive into the freshly migrated, empty instance ``name`` with the repository's own script."""
    done = subprocess.run(
        [sys.executable, "scripts/tckdb_archive.py", "restore", str(archive.resolve())],
        cwd=BACKEND, env=_db_env(name), capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise SystemExit(f"restore failed:\n{done.stdout}\n{done.stderr}")
    print(done.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("up", "down", "restore"):
        p = sub.add_parser(command)
        p.add_argument("--name", required=True, help="suffix: database tckdb_test_snapshot_<name>")
        p.add_argument("--work-dir", type=Path, default=REPO_ROOT / ".paper_snapshot_work")
        if command == "up":
            p.add_argument("--port", type=int, required=True)
        elif command == "restore":
            p.add_argument("--archive", type=Path, required=True)
        else:
            p.add_argument("--drop", action="store_true", help="also drop the database and empty the bucket")
    args = parser.parse_args(argv)
    if args.command == "up":
        up(args.name, args.port, args.work_dir)
    elif args.command == "restore":
        restore(args.name, args.archive)
    else:
        down(args.name, args.work_dir, args.drop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
