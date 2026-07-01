# `app/db/models/` — SQLAlchemy table definitions

Table definitions **only** — no business logic. SQLAlchemy 2.0 style
(explicit `Mapped[...]` + `mapped_column(...)`). Every model groups into
one of four buckets; keeping them separate is the core design invariant
(see [`docs/guides/system_flow.md`](../../../../docs/guides/system_flow.md) §1
and [`docs/schema_analysis.md`](../../../../docs/schema_analysis.md)):

| Bucket | Models |
|---|---|
| **Identity** (deduped) | `species`, `reaction`, `transition_state` |
| **Provenance** (append-only) | `calculation`, `geometry`, `software`, `level_of_theory`, `workflow`, `literature`, `author`, `literature_author`, `energy_correction` |
| **Result** (append-only) | `thermo`, `statmech`, `kinetics`, `transport`, `network`, `network_pdep`, `molecular_property_observation` |
| **Curation** (overlay) | `submission`, `record_review`, `record_machine_review`, `machine_review_curator_task` |
| **Platform** | `app_user`, `user_session`, `api_key`, `upload_job`, `idempotency` |

## Hard rules (from `.claude/rules/schema-rules.md`)

- **All enums live in `common.py`** — never define an enum inline.
- **New model modules must be imported in `__init__.py`** or Alembic
  won't discover them.
- **`NAMING_CONVENTION` in `db/base.py` is frozen** — it controls stable
  constraint/index names. Never touch it.
- The RDKit `mol` column type is a custom type in `db/types.py`.
- Migrations follow the phase-aware policy in
  `.claude/rules/migration-rules.md` (new revision for deployed tables).
