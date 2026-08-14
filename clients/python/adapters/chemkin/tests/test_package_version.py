"""The adapter's version is written down twice, so it can disagree.

``tckdb-chemkin`` ships and versions separately from ``tckdb-client``:
it has its own ``pyproject.toml`` and its own ``__version__``. Nothing
derives one from the other -- unlike ``tckdb_client.__version__``, which
reads installed distribution metadata and therefore cannot drift -- so a
release that bumps the packaging metadata and forgets the module (or the
other way round) ships a package that misreports itself to anyone who
prints ``tckdb_chemkin.__version__``.

Noticed while bumping both for an unrelated change. One assertion is
cheaper than finding out from a bug report which of the two numbers was
the real one.
"""

from __future__ import annotations

import re
from pathlib import Path

import tckdb_chemkin

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _declared_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    assert match is not None, f"no version in {PYPROJECT}"
    return match.group(1)


def test_module_version_matches_the_packaging_metadata():
    assert tckdb_chemkin.__version__ == _declared_version()


def test_the_adapter_versions_separately_from_the_client():
    """Its own distribution, so its own number.

    If this file is ever moved under ``clients/python/pyproject.toml``'s
    packaging, this assertion is the reminder that the two version
    numbers stopped being independent.
    """
    client_pyproject = PYPROJECT.parents[2] / "pyproject.toml"
    assert client_pyproject.is_file(), client_pyproject
    assert 'name = "tckdb-client"' in client_pyproject.read_text(encoding="utf-8")
    assert 'name = "tckdb-chemkin"' in PYPROJECT.read_text(encoding="utf-8")
