"""Boundary tests: ``tckdb_schemas`` must not import the backend.

Both a runtime check (``sys.modules`` after a fresh import in a
subprocess) and a static AST scan of every package source file.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "tckdb_schemas"

FORBIDDEN_PREFIXES = (
    "app",
    "backend",
    "fastapi",
    "sqlalchemy",
    "alembic",
    "rdkit",
    "requests",
    "httpx",
    "boto3",
    "minio",
)


def _iter_python_files() -> list[pathlib.Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def test_no_forbidden_static_imports() -> None:
    """Scan every package file for forbidden imports via AST."""

    offenders: list[tuple[pathlib.Path, str]] = []
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.split(".", 1)[0]
                    if name in FORBIDDEN_PREFIXES:
                        offenders.append((path, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                root = node.module.split(".", 1)[0]
                if root in FORBIDDEN_PREFIXES:
                    offenders.append((path, node.module))
    assert not offenders, f"forbidden imports found: {offenders}"


def test_no_forbidden_runtime_imports() -> None:
    """Import the public surface in a clean subprocess and verify
    no forbidden modules are present in ``sys.modules`` afterwards.
    """
    code = (
        "import sys\n"
        "import tckdb_schemas\n"
        "import tckdb_schemas.workflows.computed_species_upload\n"
        "import tckdb_schemas.workflows.computed_reaction_upload\n"
        "forbidden = (\n"
        "    'app', 'backend', 'fastapi', 'sqlalchemy', 'alembic',\n"
        "    'rdkit', 'requests', 'httpx', 'boto3', 'minio',\n"
        ")\n"
        "bad = [n for n in sys.modules if n.split('.', 1)[0] in forbidden]\n"
        "if bad:\n"
        "    print('FORBIDDEN:', bad)\n"
        "    raise SystemExit(1)\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"runtime isolation failed: stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
