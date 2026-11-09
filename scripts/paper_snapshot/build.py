"""Build the paper's demonstration snapshot on an empty local TCKDB instance.

    python scripts/paper_snapshot/build.py --sources scripts/paper_snapshot/sources.example.yml

Steps, in order:

1. refuse unless the instance is reachable and empty;
2. register a depositor through the public route and mint its API key;
3. deposit each configured source through its public route
   (source 1 through the client builders, source 4 through the CHEMKIN adapter;
   sources 2 and 3 are stubs that run only when configured);
4. create a curator account (operator CLI) and approve the submissions through
   ``POST /submissions/{id}/approve``, recording that this is a build-script
   demonstration and not an independent review;
5. export the archive (``backend/scripts/tckdb_archive.py``) and the expected
   outputs (``backend/scripts/paper/generate_expected_outputs.py``);
6. write ``build_report.json`` (every warning and refusal grouped by code) and
   ``snapshot_record.json`` (what was built, from which commit, with what).

Everything is deposited through HTTP. The only non-HTTP steps are the curator
account creation (no public route grants the role) and the two exports, which
read the instance's database the way a restore-and-regenerate reproducer does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PACKAGE_PATHS = [
    REPO_ROOT / "schemas" / "python" / "tckdb-schemas",
    REPO_ROOT / "clients" / "python" / "src",
    REPO_ROOT / "clients" / "python" / "adapters" / "chemkin",
    REPO_ROOT / "backend",
]
# Prefer this checkout's packages over any editable install that points at another checkout.
for _path in reversed([HERE, *PACKAGE_PATHS]):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import yaml  # noqa: E402

import curation  # noqa: E402
import export  # noqa: E402
import source_arc  # noqa: E402
import source_chemkin  # noqa: E402
import source_e2  # noqa: E402
import source_pdep  # noqa: E402
from common import Report, Session  # noqa: E402

#: The generators run for the snapshot. The registry holds more (ThermoML, the QCSchema round trip) that belong to
#: other deposits; the QCSchema one also writes inside a rolled-back savepoint, which a snapshot instance should not see.
EXPECTED_OUTPUTS = (
    "corpus_counts",
    "conformer_basins",
    "calculation_evidence",
    "deposit_warnings",
    "hessian_reanalysis",
    "thermo_selection_outcomes",
    "structure_selection_outcomes",
    "mechanism_roundtrip_counts",
    "mechanism_fixture_provenance",
)
#: Registered but not run: they would be empty. ``selected_thermo_by_species`` reads dataset-release selections (the
#: snapshot publishes no release); ``candidate_lineage`` needs a species entry with several thermo candidates and
#: ``transition_state_evidence`` needs transition states, which only the ARC runs (source 2) bring. Add them back
#: when the sources that feed them exist.
NOT_RUN_UNTIL_FED = ("selected_thermo_by_species", "candidate_lineage", "transition_state_evidence")

#: Config keys that hold a path on the builder's machine. They are never written to ``snapshot_record.json``.
PATH_KEYS = frozenset({"root", "fixture_dir", "adapter_path", "runs_root", "arkane_dir"})
TOP_LEVEL_KEYS = frozenset({"instance", "sources", "accounts", "expected_outputs"})

#: Deposit order (brief section 1): E2, formic acid, ARC runs, network.
SOURCES = (
    ("e2_conformers", source_e2),
    ("chemkin_formic_acid", source_chemkin),
    ("arc_runs", source_arc),
    ("pdep_network", source_pdep),
)


def load_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text()) or {}
    config.setdefault("instance", {})
    config.setdefault("sources", {})
    return config


def registered_generators() -> set[str]:
    """Names in the generator registry, read as text so validation needs no database or app import."""
    text = (REPO_ROOT / "backend" / "scripts" / "paper" / "registry.py").read_text()
    return set(re.findall(r'^\s+"([a-z0-9_]+)":\s', text, flags=re.MULTILINE))


def validate_config(config: dict[str, Any]) -> list[str]:
    """Check the whole config, enabled stub sources included, before anything is deposited."""
    problems = [f"unknown top-level key {k!r}" for k in sorted(set(config) - TOP_LEVEL_KEYS)]
    known = {name for name, _ in SOURCES}
    sources = config.get("sources") or {}
    problems += [f"unknown source {k!r}; known: {sorted(known)}" for k in sorted(set(sources) - known)]
    for name, module in SOURCES:
        entry = sources.get(name)
        if entry and entry.get("enabled", True):
            problems += module.validate(entry)
    if not any(e and e.get("enabled", True) for e in sources.values()):
        problems.append("no source is enabled; there is nothing to build")
    wanted = config.get("expected_outputs")
    if wanted:
        problems += [f"unknown generator {n!r}" for n in wanted if n not in registered_generators()]
    return problems


def database_env(instance: dict[str, Any]) -> dict[str, str]:
    db = instance.get("database") or {}
    s3 = instance.get("s3") or {}
    env = {
        "DB_USER": str(db.get("user", os.environ.get("DB_USER", "tckdb"))),
        "DB_PASSWORD": str(db.get("password", os.environ.get("DB_PASSWORD", "tckdb"))),
        "DB_HOST": str(db.get("host", os.environ.get("DB_HOST", "127.0.0.1"))),
        "DB_PORT": str(db.get("port", os.environ.get("DB_PORT", "5432"))),
        "DB_NAME": str(db.get("name", os.environ.get("DB_NAME", "tckdb_dev"))),
    }
    for key, default in (
        ("endpoint_url", "http://localhost:9000"),
        ("access_key", "tckdb"),
        ("secret_key", "tckdb_secret"),
        ("bucket", "tckdb-artifacts"),
    ):
        env["S3_" + key.upper()] = str(s3.get(key, os.environ.get("S3_" + key.upper(), default)))
    return env


def assert_empty(env: dict[str, str]) -> None:
    import psycopg

    with psycopg.connect(
        host=env["DB_HOST"], port=env["DB_PORT"], user=env["DB_USER"], password=env["DB_PASSWORD"], dbname=env["DB_NAME"]
    ) as conn:
        counts = {t: conn.execute(f"select count(*) from {t}").fetchone()[0] for t in ("submission", "species", "app_user")}
    if counts["submission"] or counts["species"]:
        raise SystemExit(f"instance {env['DB_NAME']} is not empty ({counts}); the snapshot is built from an empty instance")
    if counts["app_user"]:
        print(f"note: {counts['app_user']} account(s) already exist; they will be reused by name or the build will stop", file=sys.stderr)


def git_commit() -> dict[str, Any]:
    def run(*argv: str) -> str:
        return subprocess.run(["git", *argv], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()

    return {"commit": run("rev-parse", "HEAD"), "dirty_tracked_files": len(run("status", "--porcelain", "--untracked-files=no").splitlines())}


def package_versions() -> dict[str, Any]:
    """Versions declared by this checkout's pyproject files, and whether the build imported them from here.

    Installed-distribution metadata can describe another checkout (an editable install made elsewhere), so the
    declared version comes from the files and the import location is checked separately.
    """
    import tckdb_chemkin
    import tckdb_client
    import tckdb_schemas

    declared = {}
    for name, path in (
        ("tckdb-client", REPO_ROOT / "clients" / "python" / "pyproject.toml"),
        ("tckdb-chemkin", REPO_ROOT / "clients" / "python" / "adapters" / "chemkin" / "pyproject.toml"),
        ("tckdb-schemas", REPO_ROOT / "schemas" / "python" / "tckdb-schemas" / "pyproject.toml"),
    ):
        match = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(), flags=re.MULTILINE)
        declared[name] = match.group(1) if match else "unknown"
    here = all(
        REPO_ROOT in Path(module.__file__).resolve().parents for module in (tckdb_client, tckdb_chemkin, tckdb_schemas)
    )
    return {"declared_by_this_checkout": declared, "imported_from_this_checkout": here}


def source_records(config: dict[str, Any], report: Report) -> dict[str, Any]:
    """Per source: its options without any path on this machine, and a digest of the files it read."""
    records: dict[str, Any] = {}
    for name, module in SOURCES:
        entry = (config["sources"] or {}).get(name)
        if not entry:
            records[name] = {"status": report.source(name)["status"]}
            continue
        records[name] = {
            "status": report.source(name)["status"],
            "options": {k: v for k, v in entry.items() if k not in PATH_KEYS},
            "input_files": module.describe_inputs(entry) if entry.get("enabled", True) else {},
        }
        if "literature" in report.source(name):
            records[name]["literature"] = report.source(name)["literature"]
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", type=Path, required=True, help="sources config (see sources.example.yml)")
    parser.add_argument("--out-dir", type=Path, default=None, help="where the snapshot is written (default: <work-dir>/snapshot)")
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / ".paper_snapshot_work")
    parser.add_argument("--skip-approval", action="store_true")
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--allow-nonempty", action="store_true", help="skip the empty-instance check (reruns only)")
    args = parser.parse_args(argv)

    config = load_config(args.sources)
    problems = validate_config(config)
    if problems:
        print("The sources config is not valid; nothing was deposited:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    instance = config["instance"]
    base_url = instance.get("api_url", "http://127.0.0.1:8010")
    out_dir = (args.out_dir or args.work_dir / "snapshot").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    db_env = database_env(instance)
    accounts = config.get("accounts") or {}
    depositor = {"username": "snapshot_depositor", "password": "snapshot-depositor-pass", "email": "depositor@snapshot.invalid",
                 "full_name": "Snapshot build depositor", **(accounts.get("depositor") or {})}
    curator = {"username": "snapshot_curator", "password": "snapshot-curator-pass", "email": "curator@snapshot.invalid",
               "full_name": "Snapshot build curator (script)", **(accounts.get("curator") or {})}

    session = Session(base_url)
    status = session.health()
    print(f"instance {session.base}: status={status.get('status')} revision={status['components']['database']['alembic_revision']}")
    if not args.allow_nonempty:
        assert_empty(db_env)

    report = Report()
    started = time.time()
    session.register_depositor(**depositor)

    approval: dict[str, Any] = {"performed": False}
    exports: dict[str, Any] = {}
    try:
        for name, module in SOURCES:
            entry = (config["sources"] or {}).get(name)
            if not entry:
                report.source(name)["status"] = "skipped: no entry in the sources config"
                print(f"[{name}] skipped (no config entry)")
                continue
            if not entry.get("enabled", True):
                report.source(name)["status"] = "skipped: enabled is false"
                print(f"[{name}] skipped (enabled: false)")
                continue
            print(f"[{name}] depositing ...")
            module.deposit(session, entry, report, args.work_dir)
            print(f"[{name}] {report.source(name)['status']}: {report.source(name)['counts']}")

        if not args.skip_approval:
            print("[curation] creating curator and approving submissions ...")
            created = curation.create_curator(db_env, curator["username"], curator["password"], curator["email"], curator["full_name"])
            outcome = curation.approve_all(session, curator["username"], curator["password"], report)
            approval = {"performed": True, "curator_account": created, **outcome,
                        "statement": "approval was done by the build script for a demonstration; it is not an independent review"}
            print(f"[curation] approved {outcome['approved_submissions']} submissions; none pending")

        if not args.skip_export:
            env = export.subprocess_env(db_env, PACKAGE_PATHS)
            archive = out_dir / "tckdb.archive"
            print("[export] archive ...")
            written = export.create_archive(archive, env).strip().splitlines()
            exports["archive_summary"] = written[-1] if written else ""
            verified = export.verify_archive(archive, env).strip().splitlines()
            exports["archive_verify"] = [ln for ln in verified if not ln.startswith("Checked ")]
            print("[export] expected outputs ...")
            wanted = list(config.get("expected_outputs") or EXPECTED_OUTPUTS)
            printed = export.expected_outputs(out_dir / "expected_outputs", env, only=wanted).splitlines()
            exports["expected_outputs"] = [ln for ln in printed if re.match(r"^[0-9a-f]{64}  ", ln)]
            exports["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()

    finally:
        # Written whether or not the build finished: a failed approval still leaves the refusals that explain it.
        report.write(out_dir / "build_report.json")
    record = {
        "snapshot": "TCKDB paper demonstration snapshot",
        "source_commit": git_commit(),
        "packages": package_versions(),
        "sources": source_records(config, report),
        "approval": approval,
        "exports": exports,
        "built_at_unix": int(started),
        "wall_seconds": round(time.time() - started, 1),
    }
    (out_dir / "snapshot_record.json").write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n")
    print(f"done in {record['wall_seconds']} s; wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
