"""Source 2: ARC runs, deposited through the ARC adapter (``tckdb_arc`` in ``tckdb-adapters``).

One entry per run folder, in the order the sources file lists them. Each folder holds ``output/output.yml`` and a
``tckdb`` block, in ``tckdb_upload.yml`` or else in ``input.yml``. The block is reused with the API URL, the API key and
the payload directory taken from the build (``arc_upload.py``); a block naming a host outside ``recorded_hosts`` stops
the build before anything is deposited.

The run folders are read, never written: payloads, sidecars and the adapter's log go to the build's work directory,
in a payload directory emptied before each run, so upload state left in a folder by an earlier upload is never read.
A digest of each folder's file list (path, size, modification time) is taken before the run and checked after it.

``rights`` is sent as every upload's ``rights`` fragment: the depositor's attestation, made at deposit (tckdb-arc 0.15.6
or later carries it; the runner refuses an adapter that does not).

Entry::

    arc_runs:
      enabled: true
      adapter_command: [conda, run, --no-capture-output, -n, arc_env, python]
      adapter_path: /path/to/tckdb-adapters
      arc_path: /path/to/ARC
      timeout_seconds: 900
      rights: {license: CC-BY-4.0, depositor_attests_right_to_license: true}
      runs_root: /path/to/runs
      recorded_hosts: [tckdb.example.org]
      runs: [set_a/run_1, set_b/run_2]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from common import REPO_ROOT, Report, Session, digest_files

SOURCE = "arc_runs"
HERE = Path(__file__).resolve().parent
RUNNER = HERE / "arc_upload.py"
CONFIG_FILES = ("tckdb_upload.yml", "input.yml")
DEFAULT_TIMEOUT_SECONDS = 900
#: Fields of a computed-reaction response kept in the build report.
RESPONSE_KEYS = (
    "submission_ref", "submission_id", "reaction_id", "reaction_entry_id", "kinetics_ids", "transition_state_entry_id",
    "thermo_ids", "species_entry_ids", "calculation_key_refs",
)


def _pyproject_version(path: Path) -> str | None:
    match = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(), flags=re.MULTILINE) if path.is_file() else None
    return match.group(1) if match else None


def adapter_versions(cfg: dict[str, Any]) -> dict[str, Any]:
    """Versions the adapter checkout declares, and the commits of the adapter and ARC checkouts."""

    def commit(path: Path) -> str | None:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
        return done.stdout.strip() or None

    adapter = Path(cfg["adapter_path"]).expanduser()
    found: dict[str, Any] = {
        "tckdb-arc": _pyproject_version(adapter / "tckdb_arc" / "pyproject.toml"),
        "tckdb-adapters-core": _pyproject_version(adapter / "tckdb_core" / "pyproject.toml"),
        "tckdb_adapters_commit": commit(adapter),
    }
    if cfg.get("arc_path"):
        found["arc_commit"] = commit(Path(cfg["arc_path"]).expanduser())
    return found


def config_file(run_dir: Path) -> Path | None:
    """The first of ``tckdb_upload.yml`` and ``input.yml`` that holds a ``tckdb`` block."""
    for name in CONFIG_FILES:
        path = run_dir / name
        if path.is_file():
            document = yaml.safe_load(path.read_text()) or {}
            if isinstance(document, dict) and document.get("tckdb"):
                return path
    return None


def recorded_host(path: Path) -> str | None:
    block = (yaml.safe_load(path.read_text()) or {}).get("tckdb") or {}
    return urlparse(str(block.get("base_url") or "")).hostname


def list_digest(run_dir: Path) -> dict[str, Any]:
    """Count and SHA-256 of the folder's file list: relative path, size and modification time of every entry."""
    outer = hashlib.sha256()
    count = 0
    for directory, subdirs, files in os.walk(run_dir):
        subdirs.sort()
        for name in sorted(files):
            path = os.path.join(directory, name)
            stat = os.lstat(path)
            outer.update(f"{os.path.relpath(path, run_dir)}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
            count += 1
    return {"files": count, "sha256": outer.hexdigest()}


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return the problems with this source's config, including a run file naming a host outside ``recorded_hosts``."""
    problems: list[str] = []
    command = cfg.get("adapter_command")
    if not isinstance(command, list) or not command or not shutil.which(str(command[0])):
        problems.append(
            f"{SOURCE}: 'adapter_command' (the command, as a list, that starts a Python able to import tckdb_arc) "
            "is required and its program must be on PATH"
        )
    adapter = Path(str(cfg.get("adapter_path") or "")).expanduser()
    if not cfg.get("adapter_path") or not (adapter / "tckdb_arc" / "tckdb_arc").is_dir():
        problems.append(f"{SOURCE}: 'adapter_path' must be a tckdb-adapters checkout (tckdb_arc/tckdb_arc not found)")
    root = Path(str(cfg.get("runs_root") or "")).expanduser()
    if not cfg.get("runs_root") or not root.is_dir():
        problems.append(f"{SOURCE}: 'runs_root' is required and must be a directory")
        return problems
    hosts = set(cfg.get("recorded_hosts") or [])
    runs = list(cfg.get("runs") or [])
    if not runs:
        problems.append(f"{SOURCE}: 'runs' lists no run folder")
    if len(set(runs)) != len(runs):
        problems.append(f"{SOURCE}: 'runs' lists a folder twice")
    for run in runs:
        run_dir = root / run
        if not (run_dir / "output" / "output.yml").is_file():
            problems.append(f"{SOURCE}: {run}: output/output.yml not found")
            continue
        path = config_file(run_dir)
        if path is None:
            problems.append(f"{SOURCE}: {run}: no tckdb block in {' or '.join(CONFIG_FILES)}")
            continue
        host = recorded_host(path)
        if host not in hosts:
            problems.append(f"{SOURCE}: {run}: {path.name} names host {host!r}, not one of recorded_hosts {sorted(hosts)}")
    return problems


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    """Count and digest of every file in the listed run folders, and per run its file count and digest."""
    root = Path(cfg["runs_root"]).expanduser()
    files: list[Path] = []
    per_run: dict[str, Any] = {}
    for run in cfg["runs"]:
        run_files = [p for p in (root / run).rglob("*") if p.is_file() and not p.is_symlink()]
        per_run[run] = digest_files(root / run, run_files)
        files += run_files
    return {**digest_files(root, files), "runs": per_run}


def _environment(cfg: dict[str, Any], key: str) -> dict[str, str]:
    """The adapter's environment: the build's ``TCKDB_*`` variables and the build interpreter's directory on ``PATH``
    removed, the packages on ``PYTHONPATH``, and the depositor key under the runner's variable."""
    adapter = Path(cfg["adapter_path"]).expanduser()
    paths = [
        adapter / "tckdb_core",
        adapter / "tckdb_arc",
        REPO_ROOT / "schemas" / "python" / "tckdb-schemas",
        REPO_ROOT / "clients" / "python" / "src",
    ]
    if cfg.get("arc_path"):
        paths.append(Path(cfg["arc_path"]).expanduser())
    env = {k: v for k, v in os.environ.items() if not k.startswith("TCKDB_")}
    own_bin = str(Path(sys.executable).parent)
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and p != own_bin)
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in paths)
    env["TCKDB_SNAPSHOT_API_KEY"] = key
    if cfg.get("rights"):
        env["TCKDB_SNAPSHOT_RIGHTS"] = json.dumps(cfg["rights"], sort_keys=True)
    return env


def _sidecars(payload_dir: Path) -> list[dict[str, Any]]:
    """The adapter's per-payload sidecars, archived copies excluded, in file-name order."""
    found = []
    for path in sorted(payload_dir.rglob("*.meta.json")):
        if "archive" in path.relative_to(payload_dir).parts:
            continue
        meta = json.loads(path.read_text())
        found.append({"file": path.relative_to(payload_dir).as_posix(), **meta})
    return found


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    root = Path(cfg["runs_root"]).expanduser()
    src = report.source(SOURCE)
    payload_root = (work_dir / "arc_payloads").resolve()
    log_root = (work_dir / "arc_logs").resolve()
    log_root.mkdir(parents=True, exist_ok=True)
    env = _environment(cfg, session.depositor_key or "")
    base_url = session.base
    runs_out: list[dict[str, Any]] = []
    for run in cfg["runs"]:
        run_dir = root / run
        config = config_file(run_dir)
        payload_dir = payload_root / run
        if payload_dir.exists():
            shutil.rmtree(payload_dir)
        payload_dir.mkdir(parents=True)
        before = list_digest(run_dir)
        log = log_root / (run.replace("/", "__") + ".log")
        done = subprocess.run(
            [
                *map(str, cfg["adapter_command"]), "-P", str(RUNNER), str(run_dir), config.name, str(payload_dir), base_url,
                str(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
            ],
            env=env, cwd=str(work_dir), capture_output=True, text=True,
        )
        text = done.stdout + "\n--- stderr ---\n" + done.stderr
        if session.depositor_key and session.depositor_key in text:
            raise RuntimeError(f"{SOURCE}: {run}: the adapter printed the API key; its output was not written")
        log.write_text(text)
        after = list_digest(run_dir)
        if after != before:
            raise RuntimeError(f"{SOURCE}: {run}: the run folder changed during the deposit ({before} -> {after})")
        if done.returncode != 0:
            raise RuntimeError(f"{SOURCE}: {run}: the adapter exited {done.returncode}; see {log}")
        payloads = []
        sidecars = _sidecars(payload_dir)
        if session.depositor_key and any(session.depositor_key in json.dumps(m) for m in sidecars):
            raise RuntimeError(f"{SOURCE}: {run}: a payload sidecar holds the API key")
        for meta in sidecars:
            where = f"{run}:{meta['file']}"
            status = meta.get("status")
            report.count(SOURCE, f"payloads_{status}")
            for warning in meta.get("warnings") or []:
                report.warn(SOURCE, where, warning if isinstance(warning, dict) else {"code": str(warning)})
            if meta.get("last_error") and status != "uploaded":
                report.refusals.append(
                    {"source": SOURCE, "where": where, "code": "adapter_" + str(status), "status": meta.get("response_status_code"),
                     "message": str(meta["last_error"])[:400]}
                )
            payloads.append(
                {
                    "file": meta["file"],
                    "payload_kind": meta.get("payload_kind"),
                    "status": status,
                    "public_refs": meta.get("public_refs") or {},
                    "response": {k: (meta.get("response_body") or {}).get(k) for k in RESPONSE_KEYS},
                    "warning_codes": sorted({(w or {}).get("code") or "no_code" for w in meta.get("warnings") or []}),
                }
            )
        report.count(SOURCE, "runs")
        runs_out.append({"run": run, "config_file": config.name, "folder_list_digest": before, "payloads": payloads})
        print(f"  [{SOURCE}] {run}: " + ", ".join(f"{p['payload_kind']} {p['status']}" for p in payloads), flush=True)
    src["runs"] = runs_out
    src["adapter_versions"] = adapter_versions(cfg)
    uploaded = sum(1 for r in runs_out for p in r["payloads"] if p["status"] == "uploaded")
    src["status"] = "deposited" if uploaded else "nothing deposited"
