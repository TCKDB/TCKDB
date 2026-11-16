"""Name -> generator registry.

A generator is ``(Session) -> dict``. It reads the database through the ORM
and read services -- never raw NDJSON -- because that is what a reproducer
has after ``tckdb_archive.py restore`` and what the paper describes. The
rendering rules (canonical JSON, Decimal as string, no wall-clock values)
are in
:mod:`app.services.deposit.expected_outputs`.

Adding a generator
------------------
1. Write ``def <name>(session: Session) -> dict`` in :mod:`scripts.paper.generators`
   (or a sibling module). Order every collection by content, never by public
   ref or database id. Emit no ``datetime.now()``-derived value and no deposit
   timestamp.
2. Register it below. The name becomes ``expected_outputs/<name>.json`` and
   ``<name>.md`` in the deposit.

``hessian_reanalysis``
----------------------
The spectrum-from-Hessian agreement table (``3_results.md:44``,
``SI.md:29``) is produced by
:func:`app.services.hessian_reanalysis.hessian_reanalysis`, registered below
through :mod:`scripts.paper.snapshot_generators`. It covers every calculation
with a stored Hessian or a stored frequency list, ordered by content; a
calculation whose Hessian cannot be reanalysed is counted under its refusal
status, not dropped.
"""

from __future__ import annotations

from app.services.deposit.expected_outputs import Generator
from scripts.paper import chemkin_export, generators, snapshot_cases, snapshot_generators

GENERATORS: dict[str, Generator] = {
    "corpus_counts": generators.corpus_counts,
    "mechanism_roundtrip_counts": generators.mechanism_roundtrip_counts,
    "selected_thermo_by_species": generators.selected_thermo_by_species,
    "candidate_lineage": generators.candidate_lineage,
    "transition_state_evidence": generators.transition_state_evidence,
    "mechanism_fixture_provenance": generators.mechanism_fixture_provenance,
    # The review-tier external-Cp-comparison demonstration.
    "experimental_cp_comparison": generators.experimental_cp_comparison,
    "thermoml_source_provenance": generators.thermoml_source_provenance,
    # A Psi4 Hessian through the
    # QCSchema adapter and back, and its Psi4-vs-Gaussian measurements. Reads
    # committed fixtures; the session only hosts a rolled-back round trip.
    "qcschema_interchange": generators.qcschema_interchange,
    # The spectrum-from-Hessian check: see the module docstring.
    "hessian_reanalysis": snapshot_generators.hessian_reanalysis,
    # Demonstration snapshot (scripts/paper_snapshot): see snapshot_generators for what each answers.
    "conformer_basins": snapshot_generators.conformer_basins,
    "calculation_evidence": snapshot_generators.calculation_evidence,
    "deposit_warnings": snapshot_generators.deposit_warnings,
    "thermo_selection_outcomes": snapshot_generators.thermo_selection_outcomes,
    "structure_selection_outcomes": snapshot_generators.structure_selection_outcomes,
    "use_case_ch4_oh": snapshot_cases.use_case_ch4_oh,
    "use_case_chemkin_export": chemkin_export.use_case_chemkin_export,
    "ch3_enthalpy_of_formation": snapshot_cases.ch3_enthalpy_of_formation,
    "network_n2h3": snapshot_cases.network_n2h3,
    "chemkin_reconciliation": snapshot_cases.chemkin_reconciliation,
    "species_thermo_sources": snapshot_cases.species_thermo_sources,
    "caught_error": snapshot_cases.caught_error,
    "dataset_release": snapshot_cases.dataset_release,
}

__all__ = ["GENERATORS"]
