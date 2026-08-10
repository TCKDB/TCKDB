"""The test tree's own import hygiene.

Two properties of the layout are load-bearing for a suite that has to pass
in an arbitrary order, and both are invisible until they break:

1. **Exactly one bare ``conftest`` module.** With pytest's default
   ``prepend`` import mode, a ``conftest.py`` in a directory that is not a
   package is imported as the top-level module ``conftest``, and its
   directory is prepended to ``sys.path``. Two such directories therefore
   race for ``sys.modules["conftest"]``: the loser's helpers are simply not
   there. ``tests/db/*_migration.py``, ``tests/test_db_name_resolution.py``,
   ``tests/test_db_harness_lifecycle.py`` and
   ``tests/test_test_database_isolation.py`` all do ``from conftest import
   ...`` meaning ``tests/conftest.py``, so any sibling bare ``conftest``
   breaks roughly forty tests -- but only in the orders where a worker
   happens to import the sibling first.

2. **No duplicate module basenames outside packages.** Same mechanism, same
   silence: the second file to be imported either shadows the first or dies
   with pytest's "import file mismatch".

Both are one-line mistakes to make (add a ``conftest.py``, forget the
``__init__.py``) and cost an evening to diagnose from the symptom, which is
why they are asserted rather than documented.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent

#: Data trees, not import trees. ``tests/fixtures/pdep/`` holds Arkane input
#: decks that are ``exec()``-ed with an injected namespace, never imported as
#: modules (``backend/pyproject.toml`` excludes them from ruff for the same
#: reason).
_NOT_IMPORTED = ("fixtures",)


def _module_dirs() -> list[Path]:
    """Every directory under ``tests/`` that pytest will import modules from."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(TESTS_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        directory = Path(dirpath)
        relative = directory.relative_to(TESTS_ROOT).parts
        if any(part in _NOT_IMPORTED for part in relative):
            continue
        if any(name.endswith(".py") for name in filenames):
            found.append(directory)
    return found


def _is_package(directory: Path) -> bool:
    return (directory / "__init__.py").exists()


def test_only_the_root_conftest_is_a_bare_module() -> None:
    """Any ``conftest.py`` below ``tests/`` must live in a package."""
    offenders = [
        directory.relative_to(TESTS_ROOT).as_posix()
        for directory in _module_dirs()
        if directory != TESTS_ROOT
        and (directory / "conftest.py").exists()
        and not _is_package(directory)
    ]
    assert not offenders, (
        "these directories hold a conftest.py but are not packages, so their "
        f"fixtures are imported as the bare module 'conftest': {sorted(offenders)}. "
        "Add an __init__.py -- see tests/ops/__init__.py for why."
    )


def test_no_duplicate_module_basenames_outside_packages() -> None:
    """A module basename may repeat only when every copy is inside a package."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for directory in _module_dirs():
        for entry in sorted(directory.glob("*.py")):
            # ``conftest.py`` repeats by design and is pytest's own to place;
            # the previous test states the rule that keeps it unambiguous.
            if entry.name in ("__init__.py", "conftest.py"):
                continue
            by_name[entry.name].append(directory.relative_to(TESTS_ROOT).as_posix() or ".")

    clashes = {
        name: locations
        for name, locations in by_name.items()
        if len(locations) > 1
        and any(not _is_package(TESTS_ROOT / location) for location in locations)
    }
    assert not clashes, (
        "duplicate test module basenames in non-package directories -- pytest "
        f"imports these under one top-level name: {clashes}"
    )
