"""Every ``LevelOfTheorySummary(...)`` built by the application states ``composite_scheme``.

The field defaults to ``None``, which reads as "an ordinary level". A builder that
forgets it therefore does not fail: it silently reports a bound level (CBS-QB3,
G4, ...) as ordinary. So the rule is structural and checked here, over every call
in ``app/`` (ADR 0021): the keyword must be passed explicitly, even where the
value is ``None`` by design.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"
_CLASS = "LevelOfTheorySummary"


def _calls() -> list[tuple[Path, int, bool]]:
    found = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name == _CLASS:
                found.append((path, node.lineno, any(k.arg == "composite_scheme" for k in node.keywords)))
    return found


def test_the_walk_finds_the_known_construction_sites():
    # Not vacuous: the read layer builds this summary in many places. A walk that
    # found a handful would be looking at the wrong thing.
    assert len(_calls()) >= 16


def test_every_construction_site_passes_composite_scheme():
    missing = [f"{p.relative_to(_APP.parent)}:{line}" for p, line, ok in _calls() if not ok]
    assert missing == [], f"LevelOfTheorySummary built without composite_scheme at: {missing}"
