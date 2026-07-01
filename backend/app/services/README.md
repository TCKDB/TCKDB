# `app/services/` — reusable business logic

The workhorse layer: entity resolution, deduplication, provenance
lookup, review, and read-time selection. Workflows and routes call
these; services never call routes. ~39 top-level modules + 4 subpackages.

## Naming conventions

- **`*_resolution.py`** (15 of them) — the core "resolve or create"
  services. Each takes scientific content and returns a persisted row,
  deduplicating identity and attaching provenance. `species_resolution`,
  `calculation_resolution`, `conformer_resolution`,
  `reaction_resolution`, `geometry_resolution`, `software_resolution`,
  `{kinetics,statmech,thermo,transport,network,literature,…}_resolution`.
  This is where identity dedup actually happens — read one (start with
  `species_resolution.py`) to understand the pattern.
- **`upload_submission.py`** — opens/closes submissions around every
  write; `upload_reconciliation.py` — non-blocking upload warnings.
- **`record_review.py`** — the human review state machine + `apply_review_policy()`.

## Subpackages (curation & reads)

| Package | Role |
|---|---|
| `trust/` | Deterministic, read-time trust fragment from evidence |
| `machine_review/` | Optional AI re-review; append-only verdicts, never mutates science |
| `llm_precheck/` | Optional submission-level advisory feedback |
| `scientific_read/` | Read-side query + **product selection sort** (e.g. `thermo.py` chooses which record to return) |

See [`docs/guides/system_flow.md`](../../../docs/guides/system_flow.md)
for how these fit into the upload→read lifecycle.
