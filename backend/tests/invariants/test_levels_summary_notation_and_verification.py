"""Every ``ScientificLevelsSummary(...)`` states the composite annotations, and none passes ``notation`` (ADR 0021, P7a).

Same shape as ``test_level_summary_carries_composite_scheme.py`` (#651): the fields
default to ``None``, which reads as "not a composite energy / not a legacy shape", so a
builder that forgot one would not fail, it would report an unchecked composite as
ordinary. The rule is therefore structural and checked over every call in ``app/``:

* ``composite_energy_verification`` and ``legacy_composite_shape`` are passed explicitly,
  even where the value is ``None`` by design;
* ``notation`` is **never** passed: it is a computed field derived from ``energy`` and
  ``geometry`` on every read (passing it would be silently ignored, which is the failure).

``notation`` itself must stay a computed field and never become a stored one.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.schemas.reads.scientific_common import ScientificLevelsSummary

_APP = Path(__file__).resolve().parents[2] / "app"
_CLASS = "ScientificLevelsSummary"


def _calls() -> list[tuple[Path, int, set[str], bool]]:
    """``(file, line, keywords passed, has **kwargs)`` of every construction of the class under ``app/``."""
    found = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name == _CLASS:
                found.append((path, node.lineno, {k.arg for k in node.keywords if k.arg}, any(k.arg is None for k in node.keywords)))
    return found


def _site(path: Path, line: int) -> str:
    return f"{path.relative_to(_APP.parent)}:{line}"


def test_the_walk_finds_the_known_construction_sites():
    # Not vacuous: thermo (1), kinetics (1), statmech (2: the empty early return and the derived one).
    # A walk that found fewer would be looking at the wrong thing.
    assert len(_calls()) >= 4
    assert {p.name for p, *_ in _calls()} >= {"thermo.py", "kinetics.py", "statmech.py"}


def test_every_construction_site_passes_the_verification():
    missing = [_site(p, line) for p, line, kw, _ in _calls() if "composite_energy_verification" not in kw]
    assert missing == [], f"ScientificLevelsSummary built without composite_energy_verification at: {missing}"


def test_every_construction_site_passes_the_legacy_shape():
    missing = [_site(p, line) for p, line, kw, _ in _calls() if "legacy_composite_shape" not in kw]
    assert missing == [], f"ScientificLevelsSummary built without legacy_composite_shape at: {missing}"


def test_no_construction_site_passes_notation():
    passed = [_site(p, line) for p, line, kw, _ in _calls() if "notation" in kw]
    assert passed == [], f"notation is derived on read and must not be passed: {passed}"


def test_no_construction_site_spreads_keywords():
    """A ``**kwargs`` call would hide every keyword from this walk."""
    spread = [_site(p, line) for p, line, _, star in _calls() if star]
    assert spread == [], f"ScientificLevelsSummary built with **kwargs at: {spread}"


def test_a_full_builder_keyword_set_passes_the_walk_and_a_forgetful_one_does_not():
    """The checker itself, on source it must flag and source it must not (mutation target for the walk)."""

    def keywords(source: str) -> set[str]:
        call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))
        return {k.arg for k in call.keywords if k.arg}

    good = f"{_CLASS}(composite_energy_verification=None, legacy_composite_shape=None)"
    bad = f"{_CLASS}(geometry=g, energy=e)"
    assert {"composite_energy_verification", "legacy_composite_shape"} <= keywords(good)
    assert not {"composite_energy_verification", "legacy_composite_shape"} <= keywords(bad)


def test_notation_is_a_computed_field_and_not_a_stored_one():
    assert "notation" in ScientificLevelsSummary.model_computed_fields
    assert "notation" not in ScientificLevelsSummary.model_fields
