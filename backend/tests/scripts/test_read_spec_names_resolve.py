"""A schema class named in the read spec must exist.

``backend/docs/specs/scientific_calculation_reads.md`` is the document
``CLAUDE.md`` routes people to for calculation reads, so a class name in it that
no longer resolves costs the next reader a grep and can send them looking for a
shape that was renamed years ago.

Three had rotted before this file existed: the spec annotated fields as
``CalculationResultSPSummary`` / ``…OptSummary`` / ``…FreqSummary`` while the
real classes are ``CalculationSPResultSummary`` / ``CalculationOptResultSummary``
/ ``CalculationFreqResultSummary`` — the words transposed, which is exactly the
kind of drift a human eye slides over.

**A spec both defines and references names, and only one of those is checkable.**
This document is partly a design proposal: it declares shapes it is *asking for*
(``class CalculationsSearchResponse(BaseModel):`` and friends) alongside
references to shapes that already exist. Requiring every name to resolve would
fail on the proposals, and exempting every unresolved name would check nothing.
So the rule is: a name the spec **defines** in one of its own code blocks is
exempt; a name it merely **references** must resolve to a real class.

That is precisely the distinction that catches the failure — the three stale
names appeared only as field annotations, never as definitions.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SPEC = BACKEND_ROOT / "docs" / "specs" / "scientific_calculation_reads.md"

#: Names shaped like a Pydantic schema class. Deliberately suffix-driven rather
#: than "any CamelCase word": the spec's prose is full of capitalised ordinary
#: words, and a scan that flagged those would be abandoned within a week.
_NAME = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Summary|Read|Response|Payload|Block))\b")

#: A class the spec declares for itself, in any of its fenced blocks.
_DEFINED = re.compile(r"^\s*class\s+([A-Za-z0-9_]+)\s*[(:]", re.MULTILINE)


def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def _resolvable_names() -> set[str]:
    """Every schema class importable from ``app.schemas``."""
    import app.schemas as schemas_package

    names: set[str] = set()
    for module_info in pkgutil.walk_packages(
        schemas_package.__path__, schemas_package.__name__ + "."
    ):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:  # pragma: no cover - an unimportable module is its own bug
            continue
        names.update(vars(module))
    return names


def _referenced_but_not_defined() -> list[str]:
    text = _spec_text()
    defined = set(_DEFINED.findall(text))
    return sorted(set(_NAME.findall(text)) - defined)


def test_the_scan_finds_names_to_check() -> None:
    """Guard the guard: an empty scan would pass the real test vacuously.

    The assertion below is over whatever this returns, so a regex that stopped
    matching — or a spec that moved — would report success having checked
    nothing.
    """
    assert SPEC.is_file(), f"{SPEC} is missing; the spec moved and this guard did not"
    referenced = _referenced_but_not_defined()
    # 17 when this guard was written (33 schema-shaped names in the spec, minus
    # the 16 it declares for itself). The floor is set below that rather than at
    # it, so ordinary editing does not trip it, but a scan that broke — a moved
    # spec, a regex that stopped matching, a change to how the spec fences its
    # code — collapses well past it.
    assert len(referenced) >= 12, (
        f"Only {len(referenced)} referenced schema names found in the spec, against "
        f"17 when this guard was written. The scan has probably broken rather than "
        f"the spec having shrunk that far."
    )


@pytest.mark.parametrize("name", _referenced_but_not_defined())
def test_a_referenced_schema_name_resolves(name: str) -> None:
    """Every class the spec cites must exist somewhere under ``app.schemas``."""
    assert name in _resolvable_names(), (
        f"{SPEC.name} references '{name}', which is not a class anywhere under "
        f"app.schemas. Either it was renamed and the spec was not updated, or the "
        f"spec is proposing it — in which case declare it in a code block in the "
        f"spec, which is how this guard tells a proposal from a stale reference."
    )
