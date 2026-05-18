# Scientific Network / PDep Read/Search Surface

**Status:** implemented (v0 — network grain only; network-kinetics standalone surface deferred)
**Companion to:**
- [scientific_calculation_reads.md](scientific_calculation_reads.md)
- [scientific_statmech_reads.md](scientific_statmech_reads.md)
- [scientific_transport_reads.md](scientific_transport_reads.md)

**Date:** 2026-05-18
**Scope:** Backend only. Public scientific read surface for
pressure-dependent reaction networks. ARC, `tckdb-client`, and
ingestion schemas out of scope. Two **small** schema changes:
`PublicRefMixin` added to `Network` and `NetworkSolve` (new
`public_ref` columns with prefixes `net_…` / `nsolve_…`); both
folded into the single initial migration per CLAUDE.md.

---

## 1. Purpose

Surface pressure-dependent networks as a public scientific product:

```text
What pressure-dependent networks exist?
Which species and reactions participate in each network?
What channels and states are modeled?
Which master-equation solves exist for the network?
What T/P envelope is covered?
Which PDep kinetics models (Chebyshev / PLOG / point-tabulated) are
available?
Which source calculations support the solve?
What review/trust state does each network have?
```

## 2. Endpoint list

```http
GET  /api/v1/scientific/networks/{network_ref_or_id}
GET  /api/v1/scientific/networks/search
POST /api/v1/scientific/networks/search
GET  /api/v1/scientific/network-solves/{network_solve_ref_or_id}
```

Handle prefixes: `net_…` (Network), `nsolve_…` (NetworkSolve).
Wrong-prefix refs return 422 `handle_type_mismatch`; unknown refs /
ids return 404. `/search` is registered before `/{handle}` so
FastAPI doesn't route the search path through the catch-all detail
handler.

**Deferred** to a future PR (see §11 open questions):

```http
GET/POST /api/v1/scientific/network-solves/search
GET      /api/v1/scientific/network-kinetics/{network_kinetics_ref_or_id}
GET/POST /api/v1/scientific/network-kinetics/search
```

`NetworkSolve` already has its own `nsolve_…` public ref (added by
the same PR that ships this surface), so a standalone solve detail
endpoint is a small follow-up. `NetworkKinetics` does **not** have a
public ref — adding it is a Phase A schema change that should ship
alongside the kinetics standalone surface, not before.

## 3. Schema change in this PR

Two ORM rows gained `PublicRefMixin`:

- `Network` → prefix `net`
- `NetworkSolve` → prefix `nsolve`

Mechanism (matches the pattern used by every other ref-bearing
table in the initial migration):

1. `app/db/models/network.py` and `app/db/models/network_pdep.py` —
   classes now inherit `PublicRefMixin`.
2. `app/services/public_refs.py` — `PREFIXES["Network"] = "net"`,
   `PREFIXES["NetworkSolve"] = "nsolve"`. Both are opaque-ref tables
   (not content-derived); the `make_opaque_ref` dispatch covers them
   without per-class canonicalization.
3. `alembic/versions/d861dfd60891_create_intial_schema.py` —
   added two rows to `_PUBLIC_REF_TABLES`. The existing
   `_add_public_ref_columns_and_indexes()` loop covers column
   creation, the `gen_random_uuid()` server_default fallback, and
   the `UNIQUE` index.

The dev DB must be dropped and recreated per CLAUDE.md
(`PGPASSWORD=tckdb dropdb -h 127.0.0.1 -U tckdb tckdb_dev`);
the pytest fixture rebuilds the test DB automatically.

## 4. Response fragments

Defined in [scientific_network.py](../../app/schemas/reads/scientific_network.py).

- **`NetworkCoreBlock`** — `network_id` / `network_ref`, `name`,
  `description`, **aggregate** solve-level T/P envelope
  (`solve_temperature_min_k` / `_max_k` / `solve_pressure_min_bar` /
  `_max_bar`), `created_at`, review badge. The Network row itself
  has no T/P columns; the envelope is computed cheaply from the
  child solve rows.
- **`NetworkSpeciesSummary`** — species_entry_ref + role
  (`well` / `reactant` / `product` / `bath_gas`).
- **`NetworkReactionSummary`** — reaction_entry_ref + reaction_ref +
  reversibility.
- **`NetworkStateSummary`** — composition_hash (the stable
  per-network address since `network_state` has no public_ref),
  kind, label, participant_count.
- **`NetworkChannelSummary`** — source/sink composition_hashes,
  channel kind, `has_kinetics` boolean.
- **`NetworkSolveSummary`** — `nsolve_…` ref + ME/grain/T/P metadata
  + bath-gas list (composite-PK rows are bounded) + counts for
  energy-transfer rows and source calculations + review badge.
- **`NetworkKineticsSummary`** — model_kind discriminator + T/P
  envelope + shape metadata (`chebyshev_shape` like ``"6x4"``,
  `plog_entry_count`, `point_count`). **Coefficient payloads are
  not inlined** — full Chebyshev coefficient matrix, PLOG rows,
  point triples are deferred to a future
  `/scientific/network-kinetics/{ref}` endpoint.
- **`NetworkSourceCalculationSummary`** — compact calc projection
  (ref, type, LoT, software, workflow) keyed by role
  (`well_energy` / `barrier_energy` / `well_freq` / `barrier_freq` /
  `master_equation_run` / `fit_source`).
- **`NetworkEvidenceSummary`** — bounded counts +
  `has_chebyshev` / `has_plog` / `has_point_kinetics` booleans.
- **`AvailableNetworkSections`** — `has_*` boolean map.

`Network` carries optional `software_release_id`,
`workflow_tool_release_id`, and `literature_id` — those are
projected to summary pointers and surfaced in the default response
when populated (same pattern as statmech / transport).

## 5. Include behavior

Legal include tokens:

```text
species
reactions
states
channels
solves
kinetics
source_calculations
review
internal_ids
all
```

`include=all` expands to the eight public summary-safe tokens and
never restores `internal_ids` (Phase D policy). Coefficient
payloads, point arrays, and PLOG row tables do **not** appear under
any include — only shape metadata.

## 6. Search filters

Implemented (MVP):

```text
network_ref
species_ref
species_entry_ref
reaction_ref
reaction_entry_ref
has_species             — explicit False is meaningful
has_reactions           — explicit False is meaningful
has_states              — explicit False is meaningful
has_channels            — explicit False is meaningful
has_solves              — explicit False is meaningful
has_kinetics            — explicit False is meaningful
has_chebyshev           — explicit False is meaningful
has_plog                — explicit False is meaningful
has_point_kinetics      — explicit False is meaningful
method                  — narrows by source-calc LoT
basis                   — narrows by source-calc LoT
software                — narrows by source-calc software
software_version
workflow_tool
workflow_tool_version
temperature_min         — networks whose solves' tmax_k ≥ X
temperature_max         — networks whose solves' tmin_k ≤ X
pressure_min            — networks whose solves' pmax_bar ≥ X
pressure_max            — networks whose solves' pmin_bar ≤ X
min_review_status
include_rejected
include_deprecated
include
offset
limit
sort                    — non-None → 422 client_sort_not_supported
```

Temperature/pressure filters use **overlap** semantics (a network
matches if at least one of its solves' T/P envelope touches the
requested range). Tight-superset / contained-in semantics are
deferred.

### At-least-one-filter rule

Only `None` skips. Explicit `False` is meaningful (matches the
conformer / TS / statmech / transport surface contract).

### Default deterministic ordering

```text
review_rank ASC
created_at DESC
network_id DESC
```

## 7. Review/trust behavior

Detail endpoint:
- Never filters by review status (404 only on unknown handles).
- Always carries the compact review badge.
- `review_summary` counts the requested record only.

Search endpoint:
- Hides `rejected` / `deprecated` by default; `include_rejected` /
  `include_deprecated` opt them in.
- Every record carries the badge.
- `review_summary` counts the visible candidate set before pagination.

Reviewable record types: `network`, `network_solve` (the solve
surface inherits the badge via `fetch_review_badges`). Channels,
states, kinetics rows, and source-calc rows are not in
`SubmissionRecordType` — they inherit trust from the parent
network/solve.

## 8. Internal-ID behavior

Same Phase D policy as the rest of `/scientific/*`. Default strips
every `*_id` field; `include=internal_ids` + the
`allow_public_internal_ids` deployment flag restores them.

The composition_hash on `network_state` is **not** an `_id` field
and is always present — it's a content-derived identifier that
serves as the stable per-network address for states (which have no
`public_ref`).

## 9. Payload-size policy

Network reads never inline:

- Chebyshev coefficient matrices (under any include).
- PLOG row tables (the per-row Arrhenius parameters; only the count
  surfaces under `include=kinetics`).
- Point-tabulated (T, P, k) triples (only the count surfaces).
- Geometry coordinate payloads, XYZ text, atom rows.
- Artifact body bytes.
- Source-calc heavy include sections (results, parameters,
  geometries, scan/IRC/path-search arrays) — those remain on
  `/scientific/calculations/{ref}`.

A future `/scientific/network-kinetics/{ref}` endpoint will surface
coefficient payloads under their own include tokens with explicit
size policies.

## 10. Relationship to other surfaces

- **Calculations**: source calculations under `include=source_calculations`
  are compact projections only. Full calc detail at
  `/scientific/calculations/{ref}`.
- **Reactions**: `include=reactions` returns network-reaction links;
  each carries `reaction_ref` + `reaction_entry_ref`. Full reaction
  context at `/scientific/reactions/search` or
  `/scientific/reaction-entries/{ref}/full`.
- **Species**: `include=species` returns per-role species_entry
  refs. Full species context at the species/species-entry surfaces.
- **Kinetics**: PDep kinetics live entirely under the network
  surface in v0. Per-channel rate-coefficient retrieval and
  comparison-with-arrhenius-Kinetics is deferred to the
  network-kinetics standalone surface.

## 11. Open questions

### 11.1 `NetworkKinetics` has no public_ref

`network_kinetics`, `network_kinetics_chebyshev`,
`network_kinetics_plog`, `network_kinetics_point`,
`network_state`, `network_channel`, `network_state_participant`,
`network_solve_bath_gas`, `network_solve_energy_transfer`,
`network_solve_source_calculation`, `network_species`,
`network_reaction` — **none** of these child tables have public_refs.
v0 ships them as embedded summaries only. A future PR can add
`PublicRefMixin` to `NetworkKinetics` (prefix candidate: `nkin`)
and ship the standalone surface; the other child tables are
composite-PK rows that are naturally embedded.

### 11.2 Standalone `/scientific/network-kinetics/{ref}` endpoint

When the above public_ref lands, the standalone surface should
expose:

- Default: kinetics core block + channel context + solve context +
  T/P envelope.
- `include=coefficients`: for `model_kind=chebyshev` returns the
  coefficient matrix (small, bounded by n_temperature × n_pressure,
  already JSONB on the row); for `model_kind=plog` returns the
  pressure-specific Arrhenius rows; for `model_kind=tabulated`
  returns the bounded T/P/k triples *if* the count is below a cap,
  otherwise links to a paginated specialized endpoint.
- `include=source_calculations`: per-channel source-calc summaries
  (the solve's source calc graph filtered to this channel's role).

### 11.3 Tight T/P envelope filters

Today the temperature/pressure filters use overlap semantics. A
future PR could add `temperature_min_within=X,Y` /
`temperature_covers=X,Y` semantics (matching the kinetics search's
`covers_requested_range` flag).

### 11.4 Species participant filters at state grain

`network_state` participants are addressed by `species_entry_id`
inside the state. A future filter `state_contains_species_ref=…`
could narrow to networks containing a specific basin or
bimolecular-set composition. Out of scope for v0.

### 11.5 NetworkSolve standalone detail endpoint  ✓ implemented

`GET /scientific/network-solves/{network_solve_ref_or_id}` ships
alongside this surface (handle prefix `nsolve_…`). Default response
carries the solve core block + parent-network context + bounded
evidence + available_sections summaries. Include tokens:
`bath_gas`, `energy_transfer`, `source_calculations`, `kinetics`,
`review`, `internal_ids`, `all`. The `kinetics` include surfaces
the same shape-metadata-only projection as the network detail
surface (no coefficient payloads). Anti-drift cross-endpoint test
asserts the per-solve kinetics block on this surface is dict-equal
to the kinetics block embedded under
`/networks/{ref}?include=kinetics`.

A standalone search endpoint (`GET/POST /network-solves/search`)
remains deferred — callers can filter solves via the network
search's `has_solves` / T-P envelope filters today.

## 12. Implementation status

```text
Phase 1 — schema (PublicRefMixin on Network, NetworkSolve)  ✓ implemented
Phase 2 — network detail endpoint                            ✓ implemented
Phase 3 — network search                                     ✓ implemented
Phase 4 — network-solve standalone detail                    ✓ implemented
Phase 5 — network-kinetics public_ref + standalone surface   deferred
Phase 6 — coefficient/point full-data endpoints              deferred
```

## 13. Test plan

Detail (24 tests):

```text
detail by ref / by id
unknown ref → 404
wrong-prefix handle → 422
malformed handle → 422
default shape
review badge present
evidence summary populated
solve envelope on core block
each include token (species, reactions, states, channels, solves,
                    kinetics, source_calculations, review)
include=all expands public tokens only
include=all does not restore internal IDs
internal-ID policy restore + silent drop
unknown include token → 422
rejected detail still returned with badge
forbidden-payload recursive walk (asserts ``coefficients`` never
                                  appears under any include)
```

Search (33 tests):

```text
GET / POST missing filter → 422
each identity filter happy path
each has_* boolean true and false
method/basis filter
software/version filter
workflow_tool/version filter
temperature_min/max range filter
pressure_min/max range filter
default hides rejected
include_rejected sorts last
pagination envelope
deterministic ordering
client sort rejected
GET / POST parity
POST rejects query-string fields
include=all + internal_ids policy
search record shape == detail record shape (anti-drift)
unknown-ref short-circuit
wrong-prefix ref → 422
forbidden-payload walk
```

## 14. Anti-drift consistency

The network surface follows the same patterns as the prior
scientific surfaces:

- Shared `build_network_record(session, *, n, badge, includes)`
  helper between detail and search — search records are
  byte-identical to detail records for the same include set,
  enforced by a cross-endpoint equality test.
- `include=all` resolves to the eight public tokens only; never
  expands `internal_ids`.
- Explicit `False` boolean filters are meaningful; only `None`
  skips the at-least-one-filter check.
- Phase D internal-id policy applies recursively to every `*_id`
  field through `apply_internal_ids_visibility`.
- Heavy data (coefficient matrices, point arrays, PLOG row tables)
  never appears under any `include` token — only shape metadata.
