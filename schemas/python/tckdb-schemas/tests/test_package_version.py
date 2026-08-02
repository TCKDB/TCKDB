"""Guard against the ``__version__`` / ``pyproject.toml`` drift.

``tckdb_schemas.__init__`` used to hard-code ``__version__ = "0.10.0"`` while
``pyproject.toml`` shipped 0.14.0. Anything negotiating the wire-contract
version on ``__version__`` was told the wrong answer. The fix is to keep
exactly one version literal in the repo — the one in ``pyproject.toml`` — and
resolve ``__version__`` from installed distribution metadata.
"""

from __future__ import annotations

import pathlib
import re

import tckdb_schemas

PACKAGE_DIR = pathlib.Path(__file__).resolve().parents[1]
INIT_PY = PACKAGE_DIR / "tckdb_schemas" / "__init__.py"
PYPROJECT = PACKAGE_DIR / "pyproject.toml"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_pyproject_declares_a_semver() -> None:
    match = re.search(
        r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(), flags=re.MULTILINE
    )
    assert match is not None, "pyproject.toml must declare a project version"
    assert _SEMVER.match(match.group(1)), match.group(1)


def test_init_does_not_hard_code_a_version() -> None:
    """The only version literal lives in ``pyproject.toml``."""
    source = INIT_PY.read_text()
    hard_coded = [
        literal
        for literal in re.findall(r'"([^"]*)"', source)
        if _SEMVER.match(literal)
    ]
    assert hard_coded == [], (
        f"{INIT_PY.name} hard-codes version literal(s) {hard_coded}; resolve "
        f"__version__ from importlib.metadata instead."
    )


def test_version_is_resolved_from_distribution_metadata() -> None:
    assert isinstance(tckdb_schemas.__version__, str)
    assert tckdb_schemas.__version__
