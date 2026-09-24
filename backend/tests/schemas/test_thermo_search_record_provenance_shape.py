"""``ThermoSearchRecord`` does not carry ``provenance``; ``ThermoRecord`` does.

Recovers a guard lost when ``backend/app/api/landing.py`` and its test were
deleted (dead-code removal, #283) — see issue #281.

A thermo search result row is shaped ``{species, thermo}``
(``ThermoSearchRecord`` in ``app/schemas/reads/scientific_thermo_search.py``);
the provenance block hangs off the ``thermo`` half
(``ThermoRecord.provenance`` in ``app/schemas/reads/scientific_thermo.py``),
never off the row itself. The landing page's JS once read
``record.provenance`` directly on a search result and got ``undefined`` on
every card — the level of theory went blank and, downstream of that, the row
was silently dropped. The bug was in a renderer that no longer exists, but
the schema shape that made it possible is unchanged and un-guarded: nothing
today fails if ``provenance`` ever migrated onto ``ThermoSearchRecord``
itself (which would make the row ambiguous about which record's provenance
it names) or disappeared from ``ThermoRecord`` (which would make it
unreachable from either shape).

**Why this one has no discovery/non-vacuity step.** The repository's most
common defect is a `for` loop over a collection that can quietly be empty
(see the sibling guard in
``test_evidence_summary_levels_of_theory_asymmetry.py`` for that pattern and
its floor). This test has no loop to empty: it names exactly the two shapes
the historical defect was about and asserts a fixed fact about each one's
``model_fields``. The closest analogous failure mode — asserting something
about a model that turned out to have no fields at all, e.g. because an
import silently resolved to a stub — is ruled out below by asserting
concrete, unrelated fields are present on both shapes, which only holds if
each name resolved to the real, fully-defined class.
"""

from __future__ import annotations

from app.schemas.reads.scientific_thermo import ThermoRecord
from app.schemas.reads.scientific_thermo_search import ThermoSearchRecord


def test_provenance_lives_on_the_thermo_record_not_the_search_row() -> None:
    # Prove these resolved to the real, fully-defined classes -- not an
    # empty stub that would make the assertions below pass for the wrong
    # reason -- by checking fields unrelated to provenance that only the
    # genuine shapes carry.
    assert {"species", "thermo"} <= set(ThermoSearchRecord.model_fields)
    assert {"thermo_id", "thermo_ref", "evidence_completeness"} <= set(ThermoRecord.model_fields)

    assert "provenance" not in ThermoSearchRecord.model_fields
    assert "provenance" in ThermoRecord.model_fields

    # The only route from a search row to provenance is through the nested
    # thermo record, and its declared type is the same ThermoRecord that
    # carries the field -- so a caller reading record.thermo.provenance
    # reaches something real.
    assert ThermoSearchRecord.model_fields["thermo"].annotation is ThermoRecord
