"""Name -> generator registry.

A generator is ``(Session) -> dict``. It reads the database through the ORM
and read services -- never raw NDJSON -- because that is what a reproducer
has after ``tckdb_archive.py restore`` and what the paper describes. The
rendering rules (canonical JSON, Decimal as string, no wall-clock values,
rows ordered by public ref) are in
:mod:`app.services.deposit.expected_outputs`.

Adding a generator
------------------
1. Write ``def <name>(session: Session) -> dict`` in :mod:`scripts.paper.generators`
   (or a sibling module). Order every collection by public ref. Emit no
   ``datetime.now()``-derived value.
2. Register it below. The name becomes ``expected_outputs/<name>.json`` and
   ``<name>.md`` in the deposit.
3. Add it to the manuscript correspondence table in
   ``docs/research/tckdb-phase-b-implementation-plan.md``.

Registry hook for ``hessian_reanalysis`` (work package B4b)
-----------------------------------------------------------
The spectrum-from-Hessian agreement table (``3_results.md:44``,
``SI.md:29``) is produced by a generator named ``hessian_reanalysis`` that
B4b adds. It must accept a ``Session`` and return, per transition-state entry
with a stored Hessian, the recomputed frequencies, the parsed frequencies and
the residual, ordered by the entry's public ref; refusals (entries whose
Hessian cannot be reanalysed) are counted, not dropped. Register it here as
``"hessian_reanalysis": hessian_reanalysis`` once it exists; nothing else in
the deposit builder needs to change.
"""

from __future__ import annotations

from app.services.deposit.expected_outputs import Generator
from scripts.paper import generators

GENERATORS: dict[str, Generator] = {
    "corpus_counts": generators.corpus_counts,
    "mechanism_roundtrip_counts": generators.mechanism_roundtrip_counts,
    "selected_thermo_by_species": generators.selected_thermo_by_species,
    "candidate_lineage": generators.candidate_lineage,
    "transition_state_evidence": generators.transition_state_evidence,
    "mechanism_fixture_provenance": generators.mechanism_fixture_provenance,
    # Phase C-E4 (docs/research/tckdb-phase-c-implementation-plan.md C4): the
    # review-tier external-Cp-comparison demonstration.
    "experimental_cp_comparison": generators.experimental_cp_comparison,
    "thermoml_source_provenance": generators.thermoml_source_provenance,
    # "hessian_reanalysis": added by B4b -- see the module docstring.
}

__all__ = ["GENERATORS"]
