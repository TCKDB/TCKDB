# `tckdb-client` examples

Four worked examples cover the public builder surface end to end.
Each runs offline (no `TCKDB_BASE_URL` / `TCKDB_API_KEY` required)
and prints a payload summary, emission diagnostics, an artifact
plan preview, and a truncated wire-payload sample. When both env
vars are set, the script also performs the live two-phase upload.

| Start here when…                                                                                                                                        | Example                                                                                                          |
|---------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| You have one species with `opt + freq + sp`, thermo + statmech, and attached artifacts.                                                                  | [`builder_computed_species_demo.py`](builder_computed_species_demo.py)                                           |
| You need species-side calculations, a transition state, kinetics with `SourceCalculations`, per-species thermo / statmech / transport, and artifacts.   | [`builder_computed_reaction_demo.py`](builder_computed_reaction_demo.py)                                         |
| You want an interactive walk-through of the reaction demo, cell by cell.                                                                                 | [`builder_computed_reaction_demo.ipynb`](builder_computed_reaction_demo.ipynb)                                   |
| You are writing a workflow-tool adapter and want to see workflow-shaped data mapped into builders without depending on ARC itself.                       | [`builder_arc_style_dry_run.py`](builder_arc_style_dry_run.py) / [`.ipynb`](builder_arc_style_dry_run.ipynb)     |

Run any of the scripts directly:

```bash
python clients/python/examples/builder_computed_species_demo.py
```

Or open a notebook with Jupyter:

```bash
jupyter lab clients/python/examples/builder_computed_reaction_demo.ipynb
```

## What each example demonstrates

- **Computed species demo** — the simplest end-to-end shape. One
  `Species`, one `opt + freq + sp` triple, a `Thermo`, a `Statmech`,
  a `Transport`, two attached artifacts. Use this when learning
  the builder surface for the first time.
- **Computed reaction demo** — multi-species reaction upload. Three
  `Species` (`CH3 + H → CH4`), a `TransitionState`, modified-Arrhenius
  `Kinetics` with duplicate `reactant_energy` source roles, per-species
  scientific blocks, two TS/species-side artifacts. Use this once
  the species demo makes sense.
- **Computed reaction notebook** — same flow as the `.py` reaction
  demo, split into named sections (imports → build → diagnostics
  → artifact plan → optional live upload). Ideal for interactive
  exploration.
- **ARC-style dry-run** — workflow-shaped end-to-end example. Four
  species (`CH4 + OH → CH3 + H2O`, H-abstraction), a TS, mixed
  Gaussian-opt / ORCA-SP releases, modified-Arrhenius kinetics with
  the `SourceCalculations` helper, thermo + statmech on one product,
  `Calculation.note=` annotations, two attached artifacts. Closest
  thing to a *realistic adapter* in this repository, without
  depending on any workflow-tool package. The matching notebook
  splits the same flow across twelve named cells.

## See also

- [`../docs/adapter_authoring_quickstart.md`](../docs/adapter_authoring_quickstart.md)
  — the short producer-facing path. Read this before extending the
  ARC-style dry-run into a real adapter.
- [`../docs/builder_api_mvp.md`](../docs/builder_api_mvp.md) — the
  full builder spec. Read this when the quickstart no longer
  answers your question.
- [`../docs/builder_api_stability.md`](../docs/builder_api_stability.md)
  — the public-beta surface and deprecation policy.
- [`../docs/conformer_semantic_boundary.md`](../docs/conformer_semantic_boundary.md),
  [`../docs/parser_validation_boundary.md`](../docs/parser_validation_boundary.md),
  [`../docs/calculation_note_conventions.md`](../docs/calculation_note_conventions.md)
  — the three boundary rules every adapter respects.
