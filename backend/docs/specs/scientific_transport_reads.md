# Scientific Transport Read/Search Surface

**Status:** implemented (v0)
**Companion to:**
- [scientific_calculation_reads.md](scientific_calculation_reads.md)
- [scientific_statmech_reads.md](scientific_statmech_reads.md)
- [scientific_conformer_reads.md](scientific_conformer_reads.md)

**Date:** 2026-05-18
**Scope:** Backend only. Public scientific read surface for transport
records. ARC, `tckdb-client`, and ingestion schemas out of scope.

---

## 1. Purpose

Surface transport / collision parameters as a scientific product
instead of through the legacy integer-id CRUD routes:

```text
What transport data exists for this species_entry?
What LJ pair (sigma / epsilon-over-k), dipole, polarizability, and
rotational-relaxation values are available?
Which calculations support each transport row, and in what role
(full_transport / dipole / polarizability / supporting_geometry)?
What review / trust state does the transport row have?
```

## 2. Endpoint list

```http
GET  /api/v1/scientific/transport/{transport_ref_or_id}
GET  /api/v1/scientific/transport/search
POST /api/v1/scientific/transport/search
```

Handle prefix: `trn_…`. Wrong-prefix refs return 422
`handle_type_mismatch`; unknown refs / ids return 404. `/search` is
registered before `/{handle}` so FastAPI doesn't route the search
path through the catch-all detail handler.

## 3. Response fragments

Defined in [scientific_transport.py](../../app/schemas/reads/scientific_transport.py).

- **`TransportCoreBlock`** — direct-row metadata with explicit-unit
  scalars (`sigma_angstrom`, `epsilon_over_k_k`, `dipole_debye`,
  `polarizability_angstrom3`, `rotational_relaxation`),
  `scientific_origin`, `note`, `created_at`, review badge. The
  schema's `lj_pair_both_or_neither` constraint guarantees sigma /
  epsilon are populated together or not at all; the evidence summary
  surfaces a `has_lj_parameters` boolean tracking that pairing.
- **`TransportSpeciesContext`** — species / species-entry refs +
  chemistry context (SMILES, InChI key, charge, multiplicity).
- **`TransportSourceCalculationSummary`** — `role` (full_transport /
  dipole / polarizability / supporting_geometry) + calc ref + type +
  quality + review + LoT / software / workflow.
- **`TransportEvidenceSummary`** — bounded count + booleans:
  `has_source_calculations`, `has_lj_parameters`, `has_dipole_moment`,
  `has_polarizability`, `has_rotational_relaxation`,
  `has_literature_source`.
- **`AvailableTransportSections`** — `has_source_calculations`,
  `has_review`.

Transport carries `software_release_id`, `workflow_tool_release_id`,
and `literature_id` directly on the row; those are projected to
`SoftwareReleaseSummary`, `WorkflowToolReleaseSummary`, and
`LiteratureSummary` and surfaced in the default response — same
pattern the statmech surface uses.

## 4. Include behavior

Legal include tokens: `source_calculations`, `review`, `internal_ids`,
`assessments`, `all`.

```text
include=source_calculations  — list of compact source-calc summaries
                               keyed by role
include=review               — record_review history for the
                               transport row
include=assessments          — compact current deterministic trust plus
                               latest reproducibility assessment freshness
                               (`current`, `stale`, or `unassessed`)
include=all                  — source_calculations + review
                               (never internal_ids or assessments)
include=internal_ids         — Phase D policy gate; restores integer
                               IDs when the deployment permits
```

The default response (no include) carries the bounded fields:
transport core block + species context + software / workflow /
literature pointers when populated + evidence summary +
available_sections.

### Assessment token policy

`assessments` is legal on transport detail, broad search, and the
species-entry subresource. It is internal-tokenized everywhere, so
`include=all` never returns it. The compact block has the shared
deterministic-trust and reproducibility freshness semantics documented in
`public_assessment_summaries.md`; a missing immutable assessment is always
reported as `unassessed`, never as approval.

## 5. Search filters

Implemented (MVP):

```text
species_ref
species_entry_ref
transport_ref
model_kind                    — alias for ScientificOriginKind
                                (computed / experimental / estimated);
                                the ORM has no model_kind column,
                                see §13.1
has_source_calculations       — explicit False is meaningful
has_lj_parameters             — explicit False is meaningful
has_dipole_moment             — explicit False is meaningful
has_polarizability            — explicit False is meaningful
has_rotational_relaxation     — explicit False is meaningful
method
basis
software
software_version
workflow_tool
workflow_tool_version
min_review_status
include_rejected
include_deprecated
include
offset
limit
sort                          — non-None → 422 client_sort_not_supported
```

**Deferred** (documented for future PRs):
- Numeric range filters on the scalars
  (`sigma_angstrom_min` / `sigma_angstrom_max`, etc.). Easy SQL but
  no consumer has asked yet.
- `role`-specific source-calc filters (e.g. "show me transport rows
  with a `dipole`-role source calc"). The current `method` / `basis`
  / `software` filters narrow across the full source-calc graph.
- Literature-ref / DOI filters on the transport row.

### At-least-one-filter rule

Only `None` skips. Explicit `False` is meaningful (matches the
conformer / TS / statmech surface contract).

### Default deterministic ordering

```text
review_rank ASC
created_at DESC
transport_id DESC
```

## 6. Review/trust behavior

Detail endpoint:
- Never filters by review status (404 only on unknown handles).
- Always carries the compact review badge.
- `review_summary` counts the requested record only.

Search endpoint:
- Hides `rejected` / `deprecated` by default; `include_rejected` /
  `include_deprecated` opt them in.
- Every record carries the badge.
- `review_summary` counts the visible candidate set before pagination.

## 7. Internal-ID behavior

Same Phase D policy as the rest of `/scientific/*`. Default strips
every `*_id` field; `include=internal_ids` + the
`allow_public_internal_ids` deployment flag restores them.

## 8. Geometry / artifact behavior

Transport reads never inline geometry or artifact payloads.
Source-calc summaries carry `calculation_ref` only; XYZ coordinates
and artifact bytes remain behind the calculation / geometry detail
endpoints.

## 9. Relationship to species

Transport rows attach to `species_entry` directly. The search
surface accepts `species_ref` / `species_entry_ref` filters and the
detail surface always carries the parent species context block.

## 10. Relationship to calculations

`transport_source_calculation` is the only join table. Roles:
`full_transport` (a calc that produced LJ / dipole / polarizability
all together), `dipole`, `polarizability`, `supporting_geometry`.
Per-role filtering of source-calcs is deferred (§5).

## 11. Relationship to literature

The `transport.literature_id` FK is projected to a
`LiteratureSummary` in the default response when populated.
`has_literature_source` on the evidence summary tracks the boolean.
Literature-ref filters on the search surface are deferred.

## 12. Non-goals

```text
thermo changes
statmech changes
network / pdep reads
full geometry payloads
artifact body download
new ingestion behavior
schema redesign
ARC changes
tckdb-client changes
```

## 13. Open questions

### 13.1 No `model_kind` column on the ORM

The task spec lists `model_kind` as a filter. The ORM `transport`
row has no `model_kind` column — the closest model-class signal is
`scientific_origin` (computed / experimental / estimated). The
search-request schema keeps the `model_kind` name (matching the
spec's vocabulary) but types it as `ScientificOriginKind` and maps
the filter to `Transport.scientific_origin`. Documented in the
request docstring + the WHERE-clause builder comment. If a future
PR adds a real `model_kind` column (Lennard-Jones-12-6 vs Stockmayer,
etc.), the filter can be retypeed without breaking the URL contract.

### 13.2 Pairing constraint and `has_lj_parameters`

The schema's `lj_pair_both_or_neither` check constraint guarantees
`sigma_angstrom` and `epsilon_over_k_k` are populated together or
not at all. The filter checks `Transport.sigma_angstrom IS NOT NULL`
because either column suffices; documented in the filter helper.

### 13.3 Numeric range filters

A real consumer might ask for "all transport rows with
`sigma_angstrom` in `[3.0, 4.0]`" or "polarizability above 1.5".
The SQL is trivial (BETWEEN / >= / <=) but no consumer has asked
yet, and adding range filters multiplies the API surface area.
Deferred until a consumer asks; the existing has_* booleans cover
the "is this populated at all" case.

### 13.4 `role`-specific source-calc filters

Today `has_source_calculations` is a boolean over the whole role
set. A future PR could add `has_dipole_source_calc`,
`has_polarizability_source_calc`, etc. Out of scope for v0.

## 14. Implementation status

```text
Phase 1 — handles + detail endpoint               ✓ implemented
Phase 2 — search                                  ✓ implemented
Phase 3 — numeric range filters / role-specific   deferred
          source-calc filters
```

## 15. Test plan

Detail (20 tests):

```text
detail by ref / by id
unknown ref → 404
wrong-prefix handle → 422
malformed handle → 422
default shape
review badge present
species context present
all five scalar transport parameters present + serialized
evidence summary default (zero calcs, LJ pair from factory default)
evidence summary with source calc
available_sections present
include=source_calculations
include=review
include=all expands public tokens only
include=all does not restore internal IDs
internal-ID policy restore + silent drop
unknown include token → 422
rejected detail still returned with badge
forbidden-payload recursive walk
```

Search (32 tests):

```text
GET / POST missing filter → 422
each implemented filter happy path
explicit True + False for every has_* boolean filter
default hides rejected; include_rejected sorts last
pagination envelope correct
deterministic ordering (review_rank → created_at)
client sort rejected
GET / POST parity
POST rejects query-string fields
each include token on records
include=all + internal_ids policy
search record shape == detail record shape (anti-drift)
unknown-ref short-circuit empty
wrong-prefix ref → 422
forbidden-payload recursive walk
```
