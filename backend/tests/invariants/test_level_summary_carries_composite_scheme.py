"""Every ``LevelOfTheorySummary(...)`` built by the application states ``composite_scheme`` and ``core_treatment``.

The field defaults to ``None``, which reads as "an ordinary level". A builder that
forgets it therefore does not fail: it silently reports a bound level (CBS-QB3,
G4, ...) as ordinary. So the rule is structural and checked here, over every call
in ``app/`` (ADR 0021): the keyword must be passed explicitly, even where the
value is ``None`` by design.

``core_treatment`` (frozen-core / all-electron, P4) is the same shape of risk:
it defaults to ``None``, which reads as "the producer did not say", so a builder
that forgets it silently reports a level that states ``frozen_core`` as one that
does not. It joins the rule rather than getting a default that is only safe when
nobody forgets.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"
_CLASS = "LevelOfTheorySummary"


def _calls(keyword: str = "composite_scheme") -> list[tuple[Path, int, bool]]:
    found = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name == _CLASS:
                found.append((path, node.lineno, any(k.arg == keyword for k in node.keywords)))
    return found


def test_the_walk_finds_the_known_construction_sites():
    # Not vacuous: the read layer builds this summary in many places. A walk that
    # found a handful would be looking at the wrong thing.
    assert len(_calls()) >= 16


def test_every_construction_site_passes_composite_scheme():
    missing = [f"{p.relative_to(_APP.parent)}:{line}" for p, line, ok in _calls() if not ok]
    assert missing == [], f"LevelOfTheorySummary built without composite_scheme at: {missing}"


def test_every_construction_site_passes_core_treatment():
    missing = [f"{p.relative_to(_APP.parent)}:{line}" for p, line, ok in _calls("core_treatment") if not ok]
    assert missing == [], f"LevelOfTheorySummary built without core_treatment at: {missing}"


def test_the_core_treatment_walk_sees_the_same_sites_as_the_composite_scheme_walk():
    # Not vacuous: a keyword typo in the walk would make "every site passes it" true of nothing.
    assert [(p, line) for p, line, _ in _calls("core_treatment")] == [(p, line) for p, line, _ in _calls()]


#: The identity parts the notation of a record's levels writes (``app.chemistry.level_label``). Each defaults to
#: ``None``, which reads as "not stated", so a builder that forgot one would silently drop it from every notation
#: built from its summary. Passed explicitly at every site, even as ``None`` by design (P7a).
_NOTATION_PARTS = ("aux_basis", "cabs_basis", "solvent_model", "spin_treatment")


def test_every_construction_site_passes_every_part_the_notation_writes():
    sites = _calls()
    assert len(sites) >= 16
    for keyword in _NOTATION_PARTS:
        missing = [f"{p.relative_to(_APP.parent)}:{line}" for p, line, ok in _calls(keyword) if not ok]
        assert missing == [], f"LevelOfTheorySummary built without {keyword} at: {missing}"
