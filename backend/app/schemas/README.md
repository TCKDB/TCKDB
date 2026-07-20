# `app/schemas/` — Pydantic models

Validation and shape, split by role. The key rule: **upload schemas
carry scientific content, never database FK IDs** — identity resolution
happens in services/workflows, not here. Read schemas may expose public
refs for clients.

| Subfolder | Role |
|---|---|
| `workflows/` | **Upload payloads** — what a client POSTs to `/uploads/*`. Scientific content + lookup fragments only. |
| `entities/` | Per-entity Create / Read / Update models. |
| `reads/` | Read-side response shapes for the `/scientific` API. |
| `fragments/` | Reusable pieces shared across payloads (identity, calculation, provenance fragments). |

Conventions:
- Derived hashes are **omitted** from Create/Update schemas — computed in
  the service layer, never accepted from clients.
- Never expose internal integer PKs; use public refs (`species_…`).

See [`docs/guides/system_flow.md`](../../../docs/guides/system_flow.md)
for how a payload flows schema → workflow → service → DB, and
[`schema_spec.md`](../../schema_spec.md) for field semantics.
