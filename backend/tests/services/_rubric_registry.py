"""Move the live rubric registry for one test, with ``monkeypatch`` undoing every change.

``RUBRICS`` is an ordinary :class:`~app.versioning.VersionRegistry` and has no "remove" or "bump" operation of its own,
because production code never needs one. A test that asks "what happens to every stored review when rubric X is added,
removed or bumped" reaches into its two private maps through ``monkeypatch.setitem`` / ``delitem``, so the registry is
restored when the test ends.
"""

from __future__ import annotations

from app.services.trust.models import EvidenceRubric
from app.services.trust.rubrics import RUBRICS

UNRELATED_NAME = "unrelated_future_check"


def register_unrelated(monkeypatch) -> str:
    """Add a rubric no consumer owns, standing in for the next check added."""
    rubric = EvidenceRubric(name=UNRELATED_NAME, version=1, record_type="thermo", checks=())
    monkeypatch.setitem(RUBRICS._impls, UNRELATED_NAME, {1: rubric})
    monkeypatch.setitem(RUBRICS._current, UNRELATED_NAME, 1)
    return UNRELATED_NAME


def bump(monkeypatch, name: str, to: int) -> None:
    """Make version *to* of rubric *name* current, keeping the older version registered, leaving other rubrics alone."""
    current = RUBRICS.get(name)
    bumped = EvidenceRubric(name=name, version=to, record_type=current.record_type, checks=current.checks)
    monkeypatch.setitem(RUBRICS._impls, name, {**RUBRICS._impls[name], to: bumped})
    monkeypatch.setitem(RUBRICS._current, name, to)


def remove(monkeypatch, name: str) -> None:
    """Take rubric *name* out of the registry altogether (the world before it existed)."""
    monkeypatch.delitem(RUBRICS._impls, name)
    monkeypatch.delitem(RUBRICS._current, name)
