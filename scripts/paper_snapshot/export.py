"""Exports from the finished instance: the archive and the expected outputs.

Both are produced by the repository's own scripts, run as subprocesses against
the instance's database (``backend/scripts/tckdb_archive.py`` and
``backend/scripts/paper/generate_expected_outputs.py``). Nothing is rewritten
here. The generators read the database through the ORM, as they would after
``tckdb_archive.py restore`` into a fresh instance.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from common import REPO_ROOT


def _run(argv: list[str], env: dict[str, str]) -> str:
    done = subprocess.run(argv, cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed ({done.returncode}):\n{done.stdout}\n{done.stderr}")
    return done.stdout


def subprocess_env(db_env: dict[str, str], package_paths: list[Path]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(db_env)
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in package_paths)
    return env


def create_archive(out: Path, env: dict[str, str]) -> str:
    return _run([sys.executable, "scripts/tckdb_archive.py", "create", str(out), "--overwrite"], env)


def verify_archive(path: Path, env: dict[str, str]) -> str:
    return _run([sys.executable, "scripts/tckdb_archive.py", "verify", str(path)], env)


def restore_archive(path: Path, env: dict[str, str]) -> str:
    return _run([sys.executable, "scripts/tckdb_archive.py", "restore", str(path)], env)


def expected_outputs(out_dir: Path, env: dict[str, str], only: list[str] | None = None) -> str:
    argv = [sys.executable, "scripts/paper/generate_expected_outputs.py", "--output-dir", str(out_dir)]
    for name in only or []:
        argv += ["--only", name]
    out_dir.mkdir(parents=True, exist_ok=True)
    return _run(argv, env)
