"""``levels_of_theory`` is deliberately absent from most evidence summaries.

Recovers a guard lost when ``backend/app/api/landing.py`` and its test were
deleted (dead-code removal, #283) — see issue #281. The landing page was the
one consumer that rendered every scientific evidence-summary shape, and one
of its tests asserted, straight off the Pydantic models, that only the two
*basin-scoped* summaries (``ConformerGroupEvidenceSummary``,
``TransitionStateEntryEvidenceSummary``) carried ``levels_of_theory`` while
``StatmechEvidenceSummary`` and ``TransportEvidenceSummary`` did not. That
assertion never depended on rendering anything — it read
``model_fields`` directly — so it belongs here, beside the schemas, not in a
frontend test for a page that no longer exists.

``ConformerGroupEvidenceSummary.levels_of_theory`` and
``TransitionStateEntryEvidenceSummary.levels_of_theory`` (see their
docstrings in ``app/schemas/reads/scientific_conformer.py`` and
``scientific_transition_state.py``) exist because a coverage count cannot
say whether the calculations behind it are *comparable*: ``freq: 2`` on a
basin could be two frequencies at the same level or two at different
levels, and only ``levels_of_theory`` distinguishes them. ``Statmech`` and
``Transport`` deliberately do not carry it — their sources are keyed by role
rather than by calculation type, so a blank "at what level" section on those
cards would report a gap that is not there. A schema silently gaining or
losing the field should fail a test, not surface for the first time in a
renderer that quietly does nothing with it (or invents an empty section).

**Non-vacuity.** The original guard iterated the landing page's own list of
render functions; there is no such list beside the models, so this version
discovers every ``*EvidenceSummary`` Pydantic model under
``app.schemas.reads`` by import-and-scan instead. That discovery is the part
that could quietly find nothing — a typo'd suffix, a package that stopped
being walkable, a rename of the ``reads`` package — and a `for` loop over an
empty result would still let every assertion inside it "pass". The floor
below exists so that failure mode is impossible: measured 2026-09-24, there
are 15 such schemas, so the floor is set well under that and well over
"discovery silently found nothing".
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Final

from pydantic import BaseModel

import app.schemas.reads as reads_package

#: Schemas whose docstrings state, as the point of the shape and not an
#: oversight, that they carry per-calculation-type levels of theory.
EXPECTED_WITH_LEVELS: Final[frozenset[str]] = frozenset(
    {
        "ConformerGroupEvidenceSummary",
        "ConformerObservationEvidenceSummary",
        "TransitionStateEvidenceSummary",
        "TransitionStateEntryEvidenceSummary",
    }
)

#: Named explicitly in issue #281 as the pair the original guard pinned as
#: deliberately field-less: Statmech and Transport sources are keyed by
#: role, not calculation type, so there is no "at what level" to report.
REQUIRED_WITHOUT_LEVELS: Final[frozenset[str]] = frozenset({"StatmechEvidenceSummary", "TransportEvidenceSummary"})

#: Below the 15 measured 2026-09-24; see the module docstring's
#: "Non-vacuity" section for why this floor exists at all.
_MIN_DISCOVERED_EVIDENCE_SUMMARIES: Final[int] = 10


def _discover_evidence_summary_schemas() -> dict[str, type[BaseModel]]:
    """Every ``*EvidenceSummary`` Pydantic model defined under ``reads``.

    A class is counted once, in the module that actually defines it
    (``obj.__module__ == module.__name__``), so a re-export or subclass
    import elsewhere does not double-count or shadow the defining module.
    """
    found: dict[str, type[BaseModel]] = {}
    for module_info in pkgutil.walk_packages(reads_package.__path__, reads_package.__name__ + "."):
        module = importlib.import_module(module_info.name)
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and obj.__module__ == module.__name__
                and name.endswith("EvidenceSummary")
            ):
                found[name] = obj
    return found


def test_only_basin_scoped_evidence_summaries_carry_levels_of_theory() -> None:
    schemas = _discover_evidence_summary_schemas()

    # The trap this test exists to avoid: a `for` loop below that ran zero
    # times would make every assertion in it vacuously true. Fail loudly,
    # here, before any per-schema assertion runs.
    assert len(schemas) >= _MIN_DISCOVERED_EVIDENCE_SUMMARIES, (
        f"only discovered {len(schemas)} *EvidenceSummary schemas under "
        "app.schemas.reads -- discovery is broken, not exhaustive"
    )

    with_levels = {name for name, schema in schemas.items() if "levels_of_theory" in schema.model_fields}
    without_levels = set(schemas) - with_levels

    assert with_levels == EXPECTED_WITH_LEVELS, (
        "the set of evidence-summary schemas carrying levels_of_theory has "
        f"drifted: {with_levels ^ EXPECTED_WITH_LEVELS} changed sides"
    )
    assert REQUIRED_WITHOUT_LEVELS <= without_levels, REQUIRED_WITHOUT_LEVELS - without_levels
