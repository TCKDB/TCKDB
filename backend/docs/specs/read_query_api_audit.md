# TCKDB Read/Query API Audit

**Original audit date:** 2026-05-13
**Status update:** 2026-05-19
**Branch:** main
**Scope:** Factual audit of the existing read/query surface in the TCKDB
backend, plus a current implementation-status update layered on top.
The original sections (§1–§16) are preserved as historical context;
the new §0 below reflects what shipped after the original audit.
No design changes, no implementation, no schema work. ARC and
`tckdb-client` are out of scope.

---

## 0. Current implementation status (2026-05-19 update)

Since the original audit on 2026-05-13, the scientific read surface
has expanded substantially. This section is the authoritative source
for "what exists today"; §12 / §13 / §14 below are preserved as the
original gap analysis and recommended order.

**Network/PDep v0 is now complete** across the network, network-solve,
and network-kinetics grains. Detail and search ship at every grain,
embedded bounded summaries cover the child tables, network-kinetics
exposes Chebyshev and PLOG payloads under explicit include tokens and
point-tabulated payloads under an explicit and capped
`include=points`, and network-kinetics review state inherits from
the parent solve. Remaining items are refinements rather than v0
blockers — see "deferred refinements" in §0.4 below.

### 0.1 Scientific surface matrix

Surfaces marked **Implemented** are public, ref-based, review-aware,
and Phase D internal-id-policy-gated. The "Endpoints" column lists
the exact mounted paths (verified against
`backend/app/api/routes/scientific/__init__.py`).

| Surface | Status | Endpoints |
|---|---|---|
| Species search | Existing | `GET /scientific/species/search` |
| Reaction search | Existing | `GET\|POST /scientific/reactions/search` |
| Reaction-entry full | Existing | `GET /scientific/reaction-entries/{id}/full` |
| Thermo search | Existing | `GET\|POST /scientific/thermo/search` |
| Thermo per species_entry | Existing | `GET /scientific/species-entries/{id}/thermo` |
| Kinetics search | Existing | `GET\|POST /scientific/kinetics/search` |
| Kinetics per reaction_entry | Existing | `GET /scientific/reaction-entries/{id}/kinetics` |
| Geometry detail | Existing | `GET /scientific/geometries/{geometry_handle}` |
| Species-calculations search | Existing | `GET\|POST /scientific/species-calculations/search` |
| Calculations detail/search | **Implemented** | `GET /scientific/calculations/{ref_or_id}`, `GET\|POST /scientific/calculations/search` |
| Calculation scan endpoint | **Implemented** | `GET /scientific/calculations/{ref_or_id}/scan` |
| Calculation IRC endpoint | **Implemented** | `GET /scientific/calculations/{ref_or_id}/irc` |
| Calculation path-search endpoint | **Implemented** | `GET /scientific/calculations/{ref_or_id}/path-search` |
| Transition states detail/search | **Implemented** | `GET /scientific/transition-states/{ref_or_id}`, `GET /scientific/transition-state-entries/{ref_or_id}`, `GET\|POST /scientific/transition-states/search` |
| Conformers detail/search | **Implemented** | `GET /scientific/conformer-groups/{ref_or_id}`, `GET /scientific/conformer-observations/{ref_or_id}`, `GET\|POST /scientific/conformers/search` |
| Statmech detail/search | **Implemented** | `GET /scientific/statmech/{ref_or_id}`, `GET\|POST /scientific/statmech/search` |
| Transport detail/search | **Implemented** | `GET /scientific/transport/{ref_or_id}`, `GET\|POST /scientific/transport/search` |
| Networks detail/search (PDep) | **Implemented** | `GET /scientific/networks/{ref_or_id}`, `GET\|POST /scientific/networks/search`, `GET /scientific/network-solves/{ref_or_id}`, `GET\|POST /scientific/network-solves/search`, `GET /scientific/network-kinetics/{ref_or_id}`, `GET\|POST /scientific/network-kinetics/search` (Chebyshev / PLOG / point payloads behind explicit include tokens; point payload capped at the public limit). Channel-grain query and paginated point retrieval deferred. |
| Reaction-full TS links | **Implemented** | embedded refs + evidence summary under `/reaction-entries/{id}/full?include=transition_states` |
| Reaction-full path summaries | **Implemented** | scan/irc/path_search summary projections under `include=scans` / `include=irc` / `include=path_search` |
| Reaction-full artifacts | **Implemented** | per-calculation grouped artifact metadata under `include=artifacts` |
| Reaction-full conformers | **Implemented** | participant-grouped conformer-group summaries under `include=conformers` |
| Network / pdep reads | **Closed for v0** | Network detail + search, `network-solves` detail + search, and `network-kinetics` detail + search all ship. Model-specific payloads (Chebyshev coefficient matrix, PLOG rows, point-tabulated triples) surface behind explicit include tokens with point payload capped at the public limit. Channel-grain query and paginated point retrieval remain open follow-ups (see `scientific_network_reads.md` §11). |
| Literature-centered query | **Implemented (v0)** | `GET /scientific/literature/{ref_or_id}` and `GET /scientific/literature/{ref_or_id}/records` — direct-link inverse query over `calculation` / `thermo` / `kinetics` / `statmech` / `transport` / `network` / `network_solve`. See `scientific_literature_reads.md`. Search endpoint deferred. |
| Energy-correction scheme / FSF query | **Implemented (v0)** | `GET /scientific/frequency-scale-factors/{ref_or_id}` + `/search` (GET/POST) and `GET /scientific/energy-correction-schemes/{ref_or_id}` + `/search` (GET/POST). Includes `used_by` inverse-link summaries (statmech for FSF; species/reaction/TS entries via `applied_energy_correction` for both). FSF and ECS are non-reviewable; documented as such. See `scientific_correction_reads.md`. |
| Applied energy-correction reads | Partial | inverse links to `applied_energy_correction` rows surface through the FSF / ECS `include=used_by` blocks; no standalone `applied_energy_correction` detail endpoint yet |
| Bulk export | Missing | no CSV/JSONL/Parquet bulk endpoint |
| RDKit substructure / similarity search | **Implemented (v0)** | `GET\|POST /scientific/species/structure-search`. Three modes (`substructure`, `similarity`, `exact`); database-side cartridge ops (`mol_from_smiles(...) @> ...`, `tanimoto_sml(morganbv_fp(...), ...)`). Returns species-entry-grain records with a `match` block carrying the algorithm and Tanimoto score. GIST-indexed `species_entry.mol` is a forward-compatible follow-up — see `scientific_structure_search.md`. |
| Standalone artifact search | **Implemented (metadata-only)** | `GET\|POST /scientific/artifacts/search`. Artifact body download remains out of scope for the scientific read surface. See `scientific_artifact_reads.md`. |
| Curator review queue per-record | Partial | the existing `/record-reviews` listing is record-grain; no curator-oriented "to-be-reviewed" surface beyond the submission-grained `/submissions/for-review` |
| Unified record provenance projection | Partial | reaction-entry `/full` is the only composite provenance read; no `/scientific/records/{type}/{id}/provenance` generic surface |

### 0.2 Original gap list — closure status

Each item below references the original §12 list. Items are tagged
**closed** (a standalone surface ships), **partially closed** (data is
reachable embedded under another surface but no dedicated endpoint
exists), **still open**, or **deferred** (recorded but not on the
priority list).

| Gap | Status | Note |
|---|---|---|
| `/scientific/calculations/search` | **closed** | `GET\|POST /scientific/calculations/search`; multi-axis filters, deterministic ordering, `include=all`. See `scientific_calculation_reads.md`. |
| `/scientific/transition-states/search` + detail | **closed** | TS-concept + TS-entry detail endpoints + GET/POST search. See `scientific_transition_state_reads.md`. |
| `/scientific/conformers/*` | **closed** | `conformer-groups` + `conformer-observations` detail + `/conformers/search`. See `scientific_conformer_reads.md`. |
| `/scientific/statmech/search` | **closed** | Detail + GET/POST search at statmech-record grain. See `scientific_statmech_reads.md`. |
| `/scientific/transport/search` | **closed** | Detail + GET/POST search at transport-record grain. See `scientific_transport_reads.md`. |
| `/scientific/networks/*` | **closed for v0** | Network detail + search, `network-solves` detail + search, and `network-kinetics` detail + search have all shipped. Model-specific payloads (Chebyshev coefficient matrix, PLOG rows, point-tabulated triples) live behind explicit include tokens; point payload capped at the public limit. Open follow-ups: channel-grain query (needs `network_channel.public_ref`) and paginated point retrieval. See `scientific_network_reads.md` §11. |
| Literature-centered query | **closed for v0** | `GET /scientific/literature/{ref_or_id}` + `GET /scientific/literature/{ref_or_id}/records` ship. Records endpoint flattens direct-link record types (`calculation`, `thermo`, `kinetics`, `statmech`, `transport`, `network`, `network_solve`) into a paginated public-ref list with review visibility and a `record_type` filter. Standalone literature search endpoint deferred. See `scientific_literature_reads.md`. |
| Substructure / similarity search using RDKit cartridge | **closed for v0** | `GET\|POST /scientific/species/structure-search` ships with substructure / similarity / exact modes backed by `mol_from_smiles(...) @>` and `tanimoto_sml(morganbv_fp(...), ...)` over the parent species' SMILES column. Reaction substructure search remains a v1 follow-up. See `scientific_structure_search.md`. |
| Artifact search/download | **partially closed (metadata-only)** | `GET\|POST /scientific/artifacts/search` ships as a metadata-only surface (filters by `artifact_kind`, `filename`/`filename_contains`, `sha256`, `has_sha256`/`has_bytes`, `bytes_min`/`bytes_max`, owning calc / LoT / software / workflow context, owner species/TS entry, conformer observation, and time range). Artifact body fetch remains out of scope for the scientific read surface. See `scientific_artifact_reads.md`. |
| Applied-correction / scale-factor search by chemistry context | **still open** | `frequency_scale_factor` is surfaced *embedded* under statmech `include=frequencies` (via the scale factor pointer) but has no standalone `/scientific/frequency-scale-factors/*` surface. `applied_energy_correction` has no scientific read surface at all. |
| `/scientific/records/{type}/{id}/provenance` unified projection | **still open** | Reaction-entry `/full` is the only composite. A generic provenance endpoint that works for any record type was not built. |
| Bulk export endpoint | **still open** | No CSV/JSONL/Parquet bulk endpoint. |
| Curator review queue beyond submissions | **deferred** | The existing `/record-reviews` listing covers the per-record path. A curator-oriented worklist was not implemented; treat as a UI / workflow concern rather than a read-API gap. |
| NEB results | **deferred** | The `calc_path_search_result` / `calc_path_search_point` tables now cover NEB-style data, and the path-search endpoint surfaces them. The original audit's "would need schema work first" is partially resolved by the existing path-search schema; if a NEB-specific projection is wanted later, it ships as a path-search detail variant. |
| `transition_state_selection` API | **deferred** | Still no ORM table; not a current schema concept. |

### 0.3 Current scientific route inventory (by group)

Verified against `backend/app/api/routes/scientific/__init__.py` on
2026-05-18. Mounted under `/api/v1/scientific`.

**Species / reactions / thermo / kinetics**
- `GET /species/search` — species search (chemistry-first).
- `GET\|POST /reactions/search` — reaction-graph + entry-grain search.
- `GET /reaction-entries/{id}/kinetics` — per-reaction-entry kinetics.
- `GET /reaction-entries/{id}/full` — composite provenance read.
- `GET /species-entries/{id}/thermo` — per-species-entry thermo.
- `GET\|POST /thermo/search` — thermo record-grain search.
- `GET\|POST /kinetics/search` — kinetics record-grain search.

**Calculations**
- `GET /calculations/{ref_or_id}` — calculation detail + heavy includes (`results`, `dependencies`, `artifacts`, `input_geometries`, `output_geometries`, `geometry_validation`, `scf_stability`, `wavefunction_diagnostic`, `parameters`, `constraints`, `review`, `scan`, `irc`, `path_search`, `internal_ids`, `all`).
- `GET\|POST /calculations/search` — multi-axis chemistry/method/provenance search (≈27 filters incl. `species_entry_ref`, `transition_state_entry_ref`, `species_ref`, `transition_state_ref`, `calculation_type`, `quality`, `has_result`, `has_artifacts`, `has_input_geometry`, `has_output_geometry`, `artifact_kind`, `method`, `basis`, `lot_ref`, `lot_hash`, `software`, `software_version`, `workflow_tool`, `workflow_tool_version`, `geometry_validation_status`, `scf_stability_status`, `dependency_role`, `parent_calculation_ref`, `child_calculation_ref`, `parameter_key`/`value`, `canonical_parameter_key`/`value`, `created_before`/`after`).
- `GET /calculations/{ref_or_id}/scan` — full-data scan endpoint, paginates point array.
- `GET /calculations/{ref_or_id}/irc` — full-data IRC endpoint, paginates point array.
- `GET /calculations/{ref_or_id}/path-search` — full-data path-search endpoint, paginates point array.

**Artifacts**
- `GET\|POST /artifacts/search` — standalone artifact metadata search. Filters on artifact attributes (`artifact_kind`, `filename`/`filename_contains`, `sha256`, `has_sha256`/`has_bytes`, `bytes_min`/`bytes_max`, `created_after`/`before`) and owning-calculation provenance (`calculation_ref`, `calculation_type`, `quality`, `method`, `basis`, `software`/`version`, `workflow_tool`/`version`, `species_entry_ref`, `transition_state_entry_ref`, `conformer_observation_ref`). Metadata only — no body bytes, no presigned download URLs. See `scientific_artifact_reads.md`.

**Transition states**
- `GET /transition-states/{ref_or_id}` — TS-concept detail (`include=entries`, `include=calculations`, `include=geometries`, `include=review`, `include=all`).
- `GET /transition-state-entries/{ref_or_id}` — TS-entry detail.
- `GET\|POST /transition-states/search` — TS-entry-grain search (≈22 filters).

**Conformers**
- `GET /conformer-groups/{ref_or_id}` — conformer-group detail (`include=observations`, `selections`, `calculations`, `geometries`, `review`, `all`).
- `GET /conformer-observations/{ref_or_id}` — observation detail.
- `GET\|POST /conformers/search` — group-grain search (22 filters).

**Statmech**
- `GET /statmech/{ref_or_id}` — statmech detail (`include=source_calculations`, `torsions`, `frequencies`, `conformers`, `review`, `all`). Frequencies are a pointer to the source freq calc refs, not inlined per-mode arrays.
- `GET\|POST /statmech/search` — 16 filters incl. `model_kind`, `has_source_calculations`/`freq_calculation`/`rotor_scans`/`torsions`, method/basis/software/workflow filters.

**Transport**
- `GET /transport/{ref_or_id}` — transport detail (`include=source_calculations`, `review`, `all`).
- `GET\|POST /transport/search` — 15 filters incl. `model_kind` (maps to `scientific_origin`), `has_lj_parameters`/`dipole_moment`/`polarizability`/`rotational_relaxation`/`source_calculations`, method/basis/software/workflow filters.

**Reaction-full**
- `GET /reaction-entries/{id}/full` — composite provenance read. Sections accept `include` tokens: `species`, `kinetics`, `transition_states`, `calculations`, `path_search`, `irc`, `scans`, `conformers`, `artifacts`, `review`, `internal_ids`, `all`. Embedded sections reuse the same summary builders as the standalone scientific surfaces (anti-drift verified by cross-endpoint equality tests).

**Path-data endpoints** — see "Calculations" above.

**Provenance (legacy mountpoint)** — `reactions.router` and `provenance.router` both mount under
`/scientific`; see `app/api/routes/scientific/__init__.py` for the
authoritative wiring.

### 0.4 Recommended next priorities

Order chosen by considering the closure status above plus what is
load-bearing for downstream consumers. Each item is a *recommendation
to evaluate and document*, not a commitment.

1. **Literature-centered query.** Inverse-direction "records citing
   this paper" surface. The `literature_id` FK is already surfaced on
   thermo / kinetics / statmech / transport reads — a simple
   `GET /scientific/literature/{ref}/records` (or similar) would close
   the loop with minimal new schema.
2. **Energy-correction / frequency-scale-factor scientific reads.**
   ~~Open.~~ **Implemented (v0).** `frequency_scale_factor` and
   `energy_correction_scheme` carry public refs (`fsf_`, `ecs_`) and
   ship with `GET /scientific/frequency-scale-factors/{ref_or_id}` +
   `/search` and `GET /scientific/energy-correction-schemes/{ref_or_id}` +
   `/search`. Both expose `include=used_by` inverse-link summaries.
   See `scientific_correction_reads.md` for the include vocabulary,
   search filter matrix, and the deferred-filter list (e.g.
   `model_kind`, `software_version` on FSF; `software`,
   `used_by_thermo` on ECS).
3. **Standalone artifact search.** `/scientific/artifacts/search` over
   calculation artifact metadata (kind / uri / sha256 / bytes / owning
   calc ref). Body-fetch policy stays separate; this is metadata-only.
4. **RDKit substructure / similarity search.** The `mol` cartridge
   column is in place on `species` and `transition_state_entry`. A
   scientific surface using the cartridge would be a new capability,
   not a closure of existing gaps; treat as Phase 3+.
5. **Bulk export / contribution-bundle reads.** No bulk
   CSV/JSONL/Parquet surface exists. Useful for downstream model
   training. Treat as a separate workstream from the per-record reads.
6. **Network/PDep refinements.** The Network/PDep v0 read/search
   stack is complete (see §0.1 / §0.2). The remaining items are
   refinements, not v0 blockers:
   - **Channel-grain query surface.** `NetworkChannel` carries no
     public ref today (composite-PK row addressed by source/sink
     composition hash inside the parent network). A
     `network_channel_ref` filter and a `/scientific/network-channels`
     surface would plug in cleanly once a `nch_…`-style public ref
     ships on `NetworkChannel`.
   - **Paginated coefficient / point full-data endpoints.**
     `include=points` on the network-kinetics detail/search surfaces
     is capped at `settings.public_max_limit` with
     `points_truncated` / `point_count_total` signaling overflow.
     A dedicated paginated endpoint (analogous to
     `/calculations/{ref}/scan|irc|path-search`) would surface the
     full tabulated payload without bumping the public include cap.
   - **Independent `NetworkKinetics` reviewability.** Today
     network-kinetics review state inherits from the parent solve;
     `NetworkKinetics` is not in `SubmissionRecordType`. Promoting it
     to an independently reviewable record type would let curators
     approve/reject individual fits without affecting the whole
     solve.

   See `scientific_network_reads.md` §11 for the per-item rationale.

### 0.5 Cross-surface anti-drift patterns

The scientific surfaces shipped after the original audit converge on
the same set of architectural patterns. Document them once so future
PRs follow the same shape:

- **Shared `build_*_record` helpers are reused between detail and
  search.** Each surface exports a `build_<entity>_record(session, *,
  …, includes)` helper that the detail endpoint calls once and the
  search endpoint calls per page row. Search and detail return
  byte-identical record payloads for the same include set by
  construction. See `build_entry_record` (transition_states),
  `build_group_record` (conformers), `build_statmech_record`,
  `build_transport_record`.
- **Reaction-full embedded summaries reuse the same summary builders
  as the standalone detail endpoints.** The reaction-full sections for
  scan / IRC / path-search / artifacts / conformers each delegate to
  the same `_build_*_include_summary` / `_build_artifacts` /
  `build_group_record(includes=set())` helpers as the per-record
  scientific surfaces. No second projection path exists, so drift
  is impossible without breaking an explicit cross-endpoint equality
  test.
- **Cross-endpoint equality tests assert byte-identical summary
  blocks.** Every scientific surface that ships a search or
  reaction-full embedded section also ships a test that compares the
  embedded / search block to the standalone detail surface via dict
  equality. Examples:
  `test_full_scan_summary_matches_calc_detail_include_scan`,
  `test_full_conformer_evidence_matches_conformer_detail`,
  `test_search_record_shape_matches_detail` (statmech / transport).
- **`include=all` expands only to summary-safe public tokens.** Across
  every surface, `all` resolves to the legal include set minus
  `internal_ids`; the heavy data tokens (full point arrays, artifact
  bodies, large JSON blobs) never appear under any `include` value.
  Full data lives behind dedicated specialized endpoints
  (`/scan`, `/irc`, `/path-search`, `/geometries/{ref}`).
- **Full path point arrays live behind specialized endpoints, not
  calculation search or reaction-full.** The summary projections on
  the search / `/full` surfaces are aggregate-only; per-point arrays
  remain available only via the
  `/calculations/{ref}/scan|irc|path-search` endpoints (paginated by
  point with bounded limits).
- **Internal IDs are opt-in and policy-gated.** Every surface routes
  responses through `apply_internal_ids_visibility`; the
  `include=internal_ids` token only takes effect when
  `settings.allow_public_internal_ids` is true. Default responses
  strip every `*_id` field recursively while preserving every
  `*_ref` field.
- **Explicit `False` boolean filters are meaningful.** The
  at-least-one-filter rule on search surfaces treats only `None` as
  "not supplied"; explicit `False` (e.g. `has_torsions=false`) is a
  valid filter that selects rows *without* that evidence. The TS
  surface originally skipped `False` and was fixed in commit
  `da7c2fd`; all subsequent surfaces (conformer / statmech /
  transport) ship with the correct semantics from the start.

---

## 1. Executive Summary

TCKDB has *two parallel read surfaces*:

1. **Legacy entity routes** (`/api/v1/{entity}`): table-style CRUD reads —
   one ORM-like object per route, simple list filters, pagination. Mounted
   behind `LEGACY_READS_REQUIRE_AUTH` (the "legacy reads gate").
2. **Scientific routes** (`/api/v1/scientific/*`): chemistry-first search +
   composite reads with deterministic ranking, review/trust visibility,
   provenance blocks, and the public-handle (`*_ref`) contract. Always public.

The *legacy* surface gives broad table coverage but answers very few
scientific questions on its own — most filters are FK ids the caller has to
already know. The *scientific* surface answers real questions but covers
only a narrow slice of the schema (species, reactions, kinetics, thermo,
species-calculations, geometries, reaction-entry/full).

Major capabilities that **already exist**:

- Chemistry-first species + reactions + thermo + kinetics search (GET + POST).
- `species-calculations/search` (the most expressive surface; multi-axis
  scientific filters with conformer/geometry/validation includes).
- Composite `/scientific/reaction-entries/{id}/full` with conditional
  include sections and review summary.
- Per-record review/trust badges and rejected/deprecated default-hidden
  filtering across all scientific reads.
- Phase A–C public-handle contract (`*_ref` everywhere) with bilateral
  id↔ref reconciliation; Phase D internal-ID hiding implemented but flag-gated.
- A separate Tier-A/B `/calculations/{id}/...` family that exposes nearly
  every calculation child (results, dependencies, parameters, constraints,
  artifacts, geometry-validation, scf-stability, scan/IRC/path-search).

Major capabilities that **do not yet exist**:

- A scientific-style `calculations/search` endpoint
  (filter by method/basis/software/validation status/provenance shape).
- A scientific-style transition-state search/read.
- A scientific-style conformer search / observation read.
- A scientific-style statmech / transport read (only legacy table-style reads).
- A scientific-style network / pdep search (only `/lookup/network` +
  the legacy detail tree).
- A standalone literature-centered query
  ("find records that cite DOI X").
- Substructure / similarity search using the RDKit cartridge.
- A bulk export endpoint.
- A unified `/records/{type}/{id}/provenance` projection beyond the
  reaction-entry/full composite.

The biggest MVP gap is `calculations/search` (scientific-style) and the
fact that most scientific reads still expose integer PKs by default
because `allow_public_internal_ids` defaults to permissive in dev.

---

## 2. Repository Areas Inspected

| Area | Path | Purpose |
|---|---|---|
| Route modules | `backend/app/api/routes/` | FastAPI handlers (legacy + curation) |
| Scientific routes | `backend/app/api/routes/scientific/` | Public scientific read surface |
| Pagination helper | `backend/app/api/routes/_pagination.py` | Shared `PaginatedResponse` |
| Aggregator | `backend/app/api/router.py` | Mounts all sub-routers under `/api/v1` |
| Errors / 422 shape | `backend/app/api/errors.py` | IntegrityError → stable codes |
| Read schemas (entity) | `backend/app/schemas/entities/` | Per-table read DTOs |
| Read schemas (scientific) | `backend/app/schemas/reads/` | Search response envelopes |
| Service layer | `backend/app/services/` | Business logic, including `scientific_read/` |
| ORM models | `backend/app/db/models/` | Source of truth for tables |
| API tests | `backend/tests/api/` | Coverage evidence |
| Specs | `docs/specs/`, `backend/docs/specs/` | Pre-existing design notes |

---

## 3. Existing Route Inventory

### 3.1 Mount prefixes (from `backend/app/api/router.py`)

The aggregator mounts every router under `/api/v1`. Routers split into
three groups:

**Always public (no auth gate):**

| Mount | Router |
|---|---|
| `/health` | [health.py](../../app/api/routes/health.py) |
| `/auth` | [auth.py](../../app/api/routes/auth.py) |
| `/admin` | [admin.py](../../app/api/routes/admin.py) |
| `/jobs` | [jobs.py](../../app/api/routes/jobs.py) |
| `/lookup` | [lookup.py](../../app/api/routes/lookup.py) |
| `/scientific` | [scientific/__init__.py](../../app/api/routes/scientific/__init__.py) |
| `/uploads` | [uploads.py](../../app/api/routes/uploads.py) |
| `/bundles` | [bundles.py](../../app/api/routes/bundles.py) |
| `/submissions` | [submissions.py](../../app/api/routes/submissions.py) |
| `/record-reviews` | [record_reviews.py](../../app/api/routes/record_reviews.py) |

**Behind `LEGACY_READS_REQUIRE_AUTH` (legacy entity reads):**

| Mount | Router |
|---|---|
| `/calculations` | [calculations.py](../../app/api/routes/calculations.py) |
| `/species` | [species.py](../../app/api/routes/species.py) |
| `/species-entries` | [species.py](../../app/api/routes/species.py) (`entries_router`) |
| `/reactions` | [reactions.py](../../app/api/routes/reactions.py) |
| `/reaction-entries` | [reactions.py](../../app/api/routes/reactions.py) (`entries_router`) |
| `/kinetics` | [kinetics.py](../../app/api/routes/kinetics.py) |
| `/thermo` | [thermo.py](../../app/api/routes/thermo.py) |
| `/transition-states` | [transition_states.py](../../app/api/routes/transition_states.py) |
| `/geometries` | [geometries.py](../../app/api/routes/geometries.py) |
| `/levels-of-theory` | [levels_of_theory.py](../../app/api/routes/levels_of_theory.py) |
| `/software`, `/software-releases` | [software.py](../../app/api/routes/software.py) |
| `/literature` | [literature.py](../../app/api/routes/literature.py) |
| `/conformer-groups`, `/conformer-observations` | [conformers.py](../../app/api/routes/conformers.py) |
| `/energy-correction-schemes`, `/frequency-scale-factors`, `/applied-energy-corrections` | [energy_corrections.py](../../app/api/routes/energy_corrections.py) |
| `/workflow-tools`, `/workflow-tool-releases` | [workflow_tools.py](../../app/api/routes/workflow_tools.py) |
| `/statmech` | [statmech.py](../../app/api/routes/statmech.py) |
| `/transport` | [transport.py](../../app/api/routes/transport.py) |
| `/networks` | [networks.py](../../app/api/routes/networks.py) |

**Scientific sub-routers** (under `/api/v1/scientific/`):

`species`, `reactions`, `kinetics`, `thermo`, `provenance`,
`thermo_search`, `kinetics_search`, `species_calculations_search`,
`geometries`. See [scientific/__init__.py](../../app/api/routes/scientific/__init__.py).

### 3.2 Endpoint counts

- ~82 GET endpoints + ~8 POST search endpoints across all read-shaped routes.
- 14 public scientific GET endpoints + 4 public scientific POST search.
- The remaining reads sit behind the legacy gate or the curator gate.

(Full endpoint table → §16 Appendix.)

---

## 4. Existing Read/Query Endpoint Classification

### 4.1 Identity lookups

| Concept | Endpoint(s) | Type |
|---|---|---|
| Species | `GET /scientific/species/search`, `GET /lookup/species`, `GET /species`, `GET /species/{id}` | Scientific search + table-style reads |
| Species entry | `GET /species-entries/{id}`, plus `/species-entries/{id}/conformer-groups\|thermo\|statmech\|transport` | Table-style with hand-rolled child lists |
| Reactions | `GET\|POST /scientific/reactions/search`, `GET /lookup/reaction`, `GET /reactions`, `GET /reactions/{id}` | Scientific search + table-style |
| Reaction entries | `GET /reaction-entries/{id}`, `GET /reaction-entries/{id}/kinetics`, `GET /scientific/reaction-entries/{id}/full` | Table + composite |
| Transition states | `GET /transition-states`, `GET /transition-states/{id}`, `GET /transition-states/entries/{id}` | Table only |
| Conformers | `GET /conformer-groups`, `GET /conformer-groups/{id}`, `GET /conformer-observations`, `GET /conformer-observations/{id}` | Table only |
| Literature | `GET /literature`, `GET /literature/{id}` | Table only |
| Software / release | `GET /software`, `GET /software/{id}`, `GET /software-releases`, `GET /software-releases/{id}` | Table only |
| Workflow tool / release | `GET /workflow-tools`, `GET /workflow-tools/{id}`, `GET /workflow-tool-releases`, `GET /workflow-tool-releases/{id}` | Table only |
| Level of theory | `GET /levels-of-theory`, `GET /levels-of-theory/{id}` | Table only |
| Geometry | `GET /geometries`, `GET /geometries/{id}`, `GET /scientific/geometries/{handle}`, `GET /lookup/geometry` | Mixed (scientific detail + lookup) |

### 4.2 Scientific product reads

| Concept | Endpoint(s) | Type |
|---|---|---|
| Thermo | `GET\|POST /scientific/thermo/search`, `GET /scientific/species-entries/{id}/thermo`, `GET /thermo`, `GET /thermo/{id}`, `GET /lookup/thermo`, `GET /species-entries/{id}/thermo` | Scientific search + scientific detail + legacy table |
| Kinetics | `GET\|POST /scientific/kinetics/search`, `GET /scientific/reaction-entries/{id}/kinetics`, `GET /kinetics`, `GET /kinetics/{id}`, `GET /lookup/kinetics`, `GET /lookup/reaction-kinetics`, `GET /reaction-entries/{id}/kinetics` | Scientific search + scientific detail + legacy table |
| Statmech | `GET /statmech`, `GET /statmech/{id}`, `GET /lookup/statmech`, `GET /species-entries/{id}/statmech` | Table only — **no scientific search** |
| Transport | `GET /transport`, `GET /transport/{id}`, `GET /lookup/transport`, `GET /species-entries/{id}/transport` | Table only — **no scientific search** |
| Network / PDep | `GET /networks`, `GET /networks/{id}`, `GET /networks/{id}/solves`, `GET /networks/{id}/solves/{id}`, `GET /lookup/network` | Table-style (deeply nested), **no scientific search** |
| Applied corrections | `GET /applied-energy-corrections`, `GET /applied-energy-corrections/{id}` | Table only |
| Frequency scale factors | `GET /frequency-scale-factors`, `GET /frequency-scale-factors/{id}` | Table only |
| Energy correction schemes | `GET /energy-correction-schemes`, `GET /energy-correction-schemes/{id}` | Table only |

### 4.3 Computational provenance reads

The Tier-A/B legacy `/calculations/...` family is the main provenance surface today:

| Concept | Endpoint | Notes |
|---|---|---|
| Calculation | `GET /calculations`, `GET /calculations/{id}` | List has only pagination; detail returns `CalculationRead` |
| SP/Opt/Freq results | `GET /calculations/{id}/sp-result\|opt-result\|freq-result` | Single-result fetch |
| Scan / IRC / Path-search | `GET /calculations/{id}/scan-result\|irc-result\|path-search-result` | Single-result fetch |
| Input / output geometries | `GET /calculations/{id}/input-geometry`, `/output-geometry` | Tier-B detail |
| Dependencies | `GET /calculations/{id}/dependencies` | Directional graph slice |
| Parameters | `GET /calculations/{id}/parameters` | Vocab + values |
| Constraints | `GET /calculations/{id}/constraints` | Constraint rows |
| Artifacts | `GET /calculations/{id}/artifacts` | Metadata only |
| Geometry validation | `GET /calculations/{id}/geometry-validations` | List per calc |
| SCF stability | `GET /calculations/{id}/scf-stabilities` | List per calc |
| `/scientific/species-calculations/search` | (single endpoint) | The only scientific surface that filters across calculations |
| `/scientific/reaction-entries/{id}/full` | composite | Surfaces calculation provenance for a reaction entry |
| `/scientific/geometries/{handle}` | detail | Geometry payload + produced_by/used_as_input_by lists |
| **Scientific calculation search** | — | **Does not exist** |

NEB is **not in the schema** (no `calc_neb_image_result` ORM table).

### 4.4 Moderation / curation reads

| Concept | Endpoint | Auth |
|---|---|---|
| My submissions | `GET /submissions/mine` | session user |
| Review queue | `GET /submissions/for-review` | curator/admin |
| Submission detail | `GET /submissions/{id}` | owner or curator |
| Audit events | `GET /submissions/{id}/audit-events` | owner or curator |
| Record links | `GET /submissions/{id}/record-links` | owner or curator |
| Record reviews list | `GET /record-reviews` | authenticated |
| One record review | `GET /record-reviews/{record_type}/{record_id}` | authenticated |
| Species-entry reviews | `GET /species-entries/{id}/reviews` (per route module) | authenticated |

### 4.5 Operational reads

| Concept | Endpoint | Auth |
|---|---|---|
| Health | `GET /health` | none |
| Job status | `GET /jobs/{job_id}` | session user |
| Auth me | `GET /auth/me` | session user |
| API keys | `GET /auth/api-keys` | session user |

---

## 5. Schema-to-API Coverage Matrix

`R = read by id`, `L = list/search`, `S = scientific composite read`,
`E = embedded only via parent`, `—` = no read access at all.
Tests column lists representative test files (not exhaustive).

### 5.1 Identity

| Table | R | L | S | Response schema | Tests | Notes / gaps |
|---|---|---|---|---|---|---|
| `species` | ✓ | ✓ | ✓ | `SpeciesRead`, scientific envelope | `test_api_reads.py`, `test_api_species_search.py` | Legacy filters: smiles, inchi_key, charge, mult, kind |
| `species_entry` | ✓ | (via `/species`) | ✓ | `SpeciesEntryRead` | `test_api_reads.py`, `test_api_species_entry_*.py` | No standalone list endpoint |
| `geometry` | ✓ | ✓ | ✓ (`/scientific/geometries/{handle}`) | `GeometryRead`, `ScientificGeometryResponse` | `test_api_reads.py`, scientific tests | Lookup by `geom_hash` available |
| `geometry_atom` | E | — | E | embedded in geometry | — | No standalone surface (correct) |
| `literature` | ✓ | ✓ | — | `LiteratureRead` | `test_api_reads.py` | No "records-citing-X" surface |
| `author` | E | — | — | embedded in `LiteratureRead` | — | — |
| `literature_author` | E | — | — | embedded | — | — |
| `software` | ✓ | ✓ | — | `SoftwareRead` | `test_api_reads.py` | — |
| `software_release` | ✓ | ✓ | — | `SoftwareReleaseRead` | `test_api_reads.py` | — |
| `workflow_tool` | ✓ | ✓ | — | `WorkflowToolRead`/`Detail` | `test_api_workflow_tools.py` | — |
| `workflow_tool_release` | ✓ | ✓ | — | `WorkflowToolReleaseDetailRead` | `test_api_workflow_tools.py` | — |
| `level_of_theory` | ✓ | ✓ | — | `LevelOfTheoryRead` | `test_api_reads.py` | Filter: method, basis, dispersion, solvent, lot_hash |
| `reaction_family` | E | — | — | embedded in `ChemReactionRead` | — | No standalone read; family used as filter |
| `chem_reaction` | ✓ | ✓ | ✓ | `ChemReactionRead`, scientific envelope | `test_api_reads.py`, scientific tests | — |
| `reaction_entry` | ✓ | (via filter) | ✓ | `ReactionEntryRead`, `ScientificReactionFullResponse` | `test_api_reads.py`, `test_api_reaction_full.py` | — |
| `reaction_participant` | E | — | E | embedded | — | — |
| `reaction_entry_structure_participant` | E | — | E | embedded | — | — |
| `transition_state` | ✓ | ✓ | — | `TransitionStateRead` | `test_api_reads.py` | **No scientific search** |
| `transition_state_entry` | ✓ | — | (in `/full`) | `TransitionStateEntryRead` | — | — |
| `transition_state_selection` | — | — | — | — | — | **Table not in ORM** |
| `conformer_assignment_scheme` | — | — | — | — | — | ORM exists but no API |
| `conformer_group` | ✓ | ✓ | (in species composite) | `ConformerGroupRead`/`Detail` | `test_api_reads.py` | — |
| `conformer_observation` | ✓ | ✓ | (in species composite) | `ConformerObservationRead` | `test_api_lowest_sp_conformer.py` | — |
| `conformer_selection` | (POST create) | — | — | `ConformerSelectionRead` | `test_api_conformer_selections.py` | **No GET surface** |

### 5.2 Calculations

| Table | R | L | S | Response schema | Tests | Notes / gaps |
|---|---|---|---|---|---|---|
| `calculation` | ✓ | ✓ | (only via `species-calculations/search`) | `CalculationRead` | `test_api_reads.py`, `test_calculation_phase2_reads.py` | List has no science filters; **no `/scientific/calculations/search`** |
| `calculation_input_geometry` | (via calc) | (via calc) | — | detail read | `test_calculation_phase2_reads.py` | — |
| `calculation_output_geometry` | (via calc) | (via calc) | — | detail read | `test_calculation_phase2_reads.py` | — |
| `calculation_dependency` | (via calc) | (via calc) | (in `/full`) | `CalculationDependencyDirectionalRead` | `test_calculation_phase2_reads.py` | — |
| `calculation_parameter` | (via calc) | (via calc) | — | `CalculationParameterRead` | `test_calculation_phase2_reads.py` | **No filter by parameter key/value across calcs** |
| `calculation_parameter_vocab` | — | — | — | — | — | Internal vocab, not exposed |
| `calculation_constraint` | (via calc) | (via calc) | — | `CalculationConstraintRead` | `test_calculation_phase2_reads.py` | — |
| `calculation_artifact` | (via calc) | (via calc) | (flag in includes) | `CalculationArtifactRead` | `test_api_calculation_artifacts.py` | Metadata only; download path not in scientific surface |
| `calc_sp_result` | ✓ | — | (via species-calc) | `CalculationSPResultRead` | `test_api_reads.py` | — |
| `calc_opt_result` | ✓ | — | — | `CalculationOptResultRead` | `test_api_reads.py` | — |
| `calc_freq_result` | ✓ | — | — | `CalculationFreqResultRead` | `test_api_reads.py` | — |
| `calc_freq_mode` | E | — | — | embedded | — | — |
| `calc_geometry_validation` | (via calc) | (via calc) | (via species-calc include) | `CalculationGeometryValidationRead` | `test_calculation_phase2_reads.py` | — |
| `calc_scan_result` | ✓ | — | — | `CalculationScanResultRead` | `test_calculation_phase2_reads.py` | — |
| `calc_scan_*` (children) | E | — | — | embedded | — | — |
| `calc_irc_result` | ✓ | — | (in kinetics include) | `CalculationIRCResultRead` | `test_calculation_phase2_reads.py` | — |
| `calc_irc_point` | E | — | — | embedded | — | — |
| `calc_path_search_result` | ✓ | (via calc) | (in kinetics include) | `CalculationPathSearchResultRead` | `test_calculation_phase2_reads.py` | — |
| `calc_path_search_point` | E | — | — | embedded | — | — |
| `calc_scf_stability` | (via calc) | (via calc) | (in species-calc include) | `CalculationSCFStabilityRead` | `test_scf_stability.py` | — |
| `calc_neb_image_result` | — | — | — | — | — | **Not in ORM** |

### 5.3 Scientific products

| Table | R | L | S | Response schema | Tests | Notes / gaps |
|---|---|---|---|---|---|---|
| `thermo` | ✓ | ✓ | ✓ | `ThermoRead`, scientific envelopes | `test_api_reads.py`, `test_api_species_thermo.py` | — |
| `thermo_nasa`, `thermo_point`, `thermo_source_calculation` | E | — | E | embedded | — | — |
| `kinetics` | ✓ | ✓ | ✓ | `KineticsRead`, scientific envelopes | `test_api_reads.py`, `test_api_reaction_kinetics.py` | — |
| `kinetics_source_calculation` | E | — | E | embedded | — | — |
| `statmech` | ✓ | ✓ | — | `StatmechRead` | `test_api_reads.py`, `test_api_statmech_upload.py` | **No scientific search by species identity** |
| `statmech_*` (children) | E | — | E | embedded | — | — |
| `transport` | ✓ | ✓ | — | `TransportRead` | `test_api_reads.py`, `test_api_transport_upload.py` | **No scientific search** |
| `transport_source_calculation` | E | — | E | embedded | — | — |
| `network` | ✓ | ✓ | — | `NetworkDetailRead`, `NetworkListItemRead` | `test_api_network_reads.py` | **No scientific search; no chemistry-first filter** |
| `network_solve` | ✓ | ✓ | — | `NetworkSolveDetailRead` | `test_api_network_reads.py` | — |
| `network_state`, `network_channel` | (via network) | (via network) | — | nested reads | `test_api_network_reads.py` | — |
| `network_kinetics`/`_chebyshev`/`_plog`/`_point` | (via solve) | — | — | nested reads | `test_api_network_reads.py` | — |
| Other network children | E | — | E | embedded | — | — |
| `frequency_scale_factor` | ✓ | ✓ | ✓ scientific | `FrequencyScaleFactorRead`, `ScientificFrequencyScaleFactorRecord` | — | `/scientific/frequency-scale-factors/{ref}` + `/search` (GET/POST). `include=used_by` covers statmech + applied targets. |
| `energy_correction_scheme` | ✓ | ✓ | ✓ scientific | `EnergyCorrectionSchemeRead`, `ScientificEnergyCorrectionSchemeRecord` | — | `/scientific/energy-correction-schemes/{ref}` + `/search` (GET/POST). `include=corrections` unifies atom/bond/component params. |
| `energy_correction_scheme_*_param` | E | — | E | embedded | — | — |
| `applied_energy_correction` | ✓ | ✓ | (only via includes) | `AppliedEnergyCorrectionRead` | — | **No scientific search** |
| `applied_energy_correction_component` | E | — | E | embedded | — | — |

### 5.4 Curation / operational

| Table | R | L | S | Response schema | Tests | Notes |
|---|---|---|---|---|---|---|
| `submission` | ✓ | ✓ (`/mine`, `/for-review`) | — | `SubmissionRead` | `test_api_submissions.py` | Curator-gated for `/for-review` |
| `submission_audit_event` | (via submission) | (via submission) | — | `SubmissionAuditEventRead` | `test_api_submissions.py` | — |
| `submission_record_link` | (via submission) | (via submission) | — | `SubmissionRecordLinkRead` | `test_api_submissions.py` | No `*_ref` sibling for the linked record |
| `record_review` | ✓ | ✓ | — | `RecordReviewRead` | `test_api_record_reviews.py` | — |
| `species_entry_review` | (POST + GET) | ✓ | — | `SpeciesEntryReviewRead` | `test_api_species_entry_reviews.py` | — |
| `upload_job` | ✓ (`/jobs/{id}`) | — | — | job-specific | `test_api_jobs_*.py` | — |
| `app_user` | (auth/me) | — | — | internal only | — | No public listing |

---

## 6. Existing Include/Expansion Behavior

Validation lives in `app/services/scientific_read/common.py:86-133`
(`validate_includes()`):
- empty/None → empty set
- `all` token expands to all legal tokens **except** `internal_ids`
  (which must be opted in explicitly)
- unknown tokens → 422 `unknown_include_token` listing legal tokens
- duplicates normalized via set
- After resolution, `filter_internal_ids_from_resolved()` in
  `app/services/scientific_read/internal_ids.py:106-123` silently drops
  `internal_ids` if `settings.allow_public_internal_ids = False`.

### 6.1 Legal tokens per endpoint

| Endpoint | Legal tokens | Default if empty |
|---|---|---|
| `/scientific/species/search` | `thermo, statmech, transport, conformers, review, internal_ids, all` | `{}` |
| `/scientific/reactions/search` | `kinetics, transition_states, species, review, internal_ids, all` | `{}` |
| `/scientific/thermo/search` | `provenance, calculations, artifacts, review, internal_ids, all` | `{}` |
| `/scientific/kinetics/search` | `provenance, calculations, artifacts, review, species, transition_states, path_search, irc, internal_ids, all` | `{}` |
| `/scientific/species-calculations/search` | `provenance, calculations, artifacts, review, conformers, geometry, validation, scf_stability, internal_ids, all` | `{}` |
| `/scientific/reaction-entries/{id}/kinetics` | `provenance, calculations, transition_states, path_search, irc, review, artifacts, internal_ids, all` | `{}` |
| `/scientific/species-entries/{id}/thermo` | `provenance, calculations, statmech, review, artifacts, internal_ids, all` | `{}` |
| `/scientific/reaction-entries/{id}/full` | `species, kinetics, transition_states, calculations, path_search, irc, scans, conformers, artifacts, review, internal_ids, all` | `{species, kinetics, transition_states}` |
| `/scientific/geometries/{handle}` | `review, provenance, internal_ids, all` | `{}` |

### 6.2 Tested behavior

- Tests in `backend/tests/api/scientific/` exercise include validation,
  rejection of unknown tokens, and the `internal_ids` opt-in flow.
- Legacy entity routes (`/api/v1/{entity}`) **do not support `include`**
  flags; they return their default Pydantic shape only.
- `request.include` is echoed back in the response post-validation, so
  callers can see that an unsupported token was dropped (e.g.,
  `internal_ids` silently filtered when `allow_public_internal_ids=False`).
- `artifacts` and `scans`/`irc`/`conformers` includes return loosely-typed
  `list[dict[str, object]]` in the `/full` endpoint
  ([scientific_provenance.py:149-173](../../app/schemas/reads/scientific_provenance.py)) —
  shape not strictly defined in v0.

---

## 7. Existing Search and Filter Coverage

Mark: **✓** supported, **✗** not supported, **~** partial / unclear.

### 7.1 Species filters

| Filter | `/scientific/species/search` | `/species` (legacy) | `/lookup/species` |
|---|---|---|---|
| smiles | ✓ | ✓ | ✓ |
| inchi | ✓ | ✗ | ✗ |
| inchi_key | ✓ | ✓ | ✗ |
| formula | ✓ | ✗ | ✗ |
| charge | ✓ | ✓ | ✓ |
| multiplicity | ✓ | ✓ | ✓ |
| electronic_state_kind | ✓ | ✗ | ✗ |
| species_entry_kind | ✓ | ✓ (`kind`) | ✗ |
| substructure | ✗ | ✗ | ✗ |
| similarity | ✗ | ✗ | ✗ |

`/scientific/species/search` requires at least one identifier or refs;
empty identifier set → 422 `missing_identifier`.

### 7.2 Reaction filters

| Filter | `/scientific/reactions/search` | `/reactions` (legacy) |
|---|---|---|
| reactants (list) | ✓ (max 32; max 2048 chars each) | ✗ |
| products (list) | ✓ | ✗ |
| direction | ✓ (`forward\|reverse\|either`; no `exact`) | ✗ |
| family | ✓ (string) | ✓ (`reaction_family_id`, `reaction_family_raw`) |
| reversible | ✗ (implicit via direction) | ✓ |
| has_kinetics | ✗ | ✗ |
| has_transition_state | ✗ | ✗ |
| has_path_search | ✗ | ✗ |
| review_status | ✓ | ✗ |

### 7.3 Thermo filters

| Filter | `/scientific/thermo/search` | `/scientific/species-entries/{id}/thermo` | `/thermo` (legacy) |
|---|---|---|---|
| species_entry_id / smiles / inchi_key | ✓ | path-only | ✓ (id only) |
| model_kind | ✓ | ✓ | ✗ |
| temperature_min / max | ✓ | ✓ | ✗ |
| level_of_theory_id / ref | ✓ | ✓ | ✗ |
| software | ✓ (string) | ✓ | ✗ (`software_release_id` only) |
| workflow_tool | ✗ | ✗ | ✗ |
| review_status | ✓ | ✓ | ✗ |
| scientific_origin | ✗ | ✗ | ✓ |
| literature_id | ✗ | ✗ | ✓ |

### 7.4 Kinetics filters

| Filter | `/scientific/kinetics/search` | `/scientific/reaction-entries/{id}/kinetics` | `/kinetics` (legacy) |
|---|---|---|---|
| reaction_entry_id | ✓ | path-only | ✓ |
| reactants/products | ✓ | ✗ | ✗ |
| temperature_min / max | ✓ | ✓ | ✗ |
| pressure | ✓ | ✓ | ✗ |
| model_kind | ✓ | ✓ | ✓ |
| has_uncertainty | ✗ | ✗ | ✗ |
| has_tunneling | ✗ | ✗ | ✗ |
| level_of_theory | ✓ | ✓ | ✗ |
| software | ✓ | ✓ | ✓ (`software_release_id` only) |
| workflow_tool | ✗ | ✗ | ✗ |
| review_status | ✓ | ✓ | ✗ |
| scientific_origin | ✗ | ✗ | ✓ |

### 7.5 Calculation filters

The legacy `/calculations` list takes only pagination. The closest scientific
surface is `/scientific/species-calculations/search`:

| Filter | `species-calculations/search` | `/calculations` (legacy) |
|---|---|---|
| owner species/species-entry | ✓ (chemistry-first or by id/ref) | ✗ |
| owner TS | ✗ | ✗ |
| calculation_type | ✓ | ✗ |
| method | ✓ | ✗ |
| basis | ✓ | ✗ |
| level_of_theory | ✓ (id or ref) | ✗ |
| software | ✓ | ✗ |
| software_version | ✗ | ✗ |
| workflow_tool | ✓ | ✗ |
| scientific_origin | ✓ | ✗ |
| calculation_quality | ✓ | ✗ |
| geometry_validation_status | ~ (returned in includes; not a filter) | ✗ |
| has_artifacts | ~ (boolean visible in includes; no filter) | ✗ |
| has_constraints | ✗ | ✗ |
| has_parameters | ✗ | ✗ |
| parameter key/value | ✗ | ✗ |
| dependency role | ✗ | ✗ |
| scan/irc/neb availability | ✗ | ✗ |

There is **no scientific search whose primary owner is a transition state
or a calculation type** — only species-rooted search exists.

### 7.6 Moderation filters

| Filter | `/submissions/...` | `/record-reviews` |
|---|---|---|
| review_status | — | ✓ (`status`) |
| submission_status | ✓ (`statuses` list) | (via submission_id) |
| submission_id | (path) | ✓ |
| created_by | ✗ | ✗ |
| approved_by | ✗ | ✗ |
| source_kind | ✗ | ✗ |
| record_type | — | ✓ |

---

## 8. Ordering and Pagination Audit

### 8.1 Pagination

`PaginatedResponse` (`backend/app/api/routes/_pagination.py`):
`items, total, skip, limit`. Used by every legacy entity list endpoint.

Scientific endpoints use a richer envelope
(`backend/app/schemas/reads/scientific_common.py:55-65`):
`offset` (≥0, default 0), `limit` (1–200, default 50),
`returned` (actual count), `total` (pre-collapse, post-filter).
Bounds enforced in `validate_pagination()` (services/scientific_read/common.py:59-83):
`offset` capped at `settings.public_max_offset`, `limit` capped at
`min(MAX_LIMIT=200, settings.public_max_limit)`. Excess → 422.

No cursor pagination. No endpoint advertises a `next` link.

### 8.2 Ordering

| Endpoint | Default sort | Client `sort=` |
|---|---|---|
| All `/scientific/*/search` | locked, deterministic per spec (e.g., `review_rank, has_entries, created_at, id` for species) | rejected with 422 `client_sort_not_supported` |
| `/scientific/reaction-entries/{id}/kinetics` | `covers_requested_range, extrapolation_distance_k, review_rank, evidence_completeness, created_at, id` | rejected |
| `/scientific/species-entries/{id}/thermo` | analogous L3 ordering | rejected |
| `/scientific/reaction-entries/{id}/full` | sub-array ordering per spec | rejected |
| Legacy `/calculations`, `/species`, `/reactions`, `/kinetics`, `/thermo`, etc. | not explicitly documented; relies on insertion order or implicit `id ASC` | not parameterized |
| `/scientific/species-calculations/search` | controlled by `ranking` enum (`default\|latest\|earliest\|review_rank\|lowest_energy`); `lowest_energy` only legal with `calculation_type=sp\|opt` | `sort=` rejected |

**Gap:** legacy list endpoints have no documented deterministic order,
which can produce inconsistent pagination. Anything relying on legacy
list ordering today is brittle.

---

## 9. Review/Trust Visibility Audit

### 9.1 Status model

`RecordReviewStatus` (`app/db/models/common.py`):
`not_reviewed | under_review | approved | rejected | deprecated`.

Five statuses; **`deprecated` is a first-class terminal state**.
Self-approval is forbidden in the service layer
(`app/services/record_review.py`).

### 9.2 In responses

| Surface | Per-record `review` badge | `review_summary` envelope | Default visibility |
|---|---|---|---|
| All `/scientific/*` reads | ✓ `RecordReviewBadge` (status, reviewed_at, reviewer_kind) | ✓ `ReviewStatusSummary` (counts per status + total) | rejected + deprecated **excluded** |
| Legacy `/{entity}` lists/details | ✗ (some entity reads echo a raw `review_status` if present on the model, but no badge) | ✗ | no filtering applied |
| `/calculations/{id}/...` Tier-A/B | ✗ | ✗ | no filtering applied |

`reviewed_by` user id is **not** exposed in the badge (only `reviewer_kind`).

### 9.3 Filtering controls

Every scientific endpoint accepts:

- `min_review_status` — return only records with rank ≥ supplied
- `include_rejected: bool = False`
- `include_deprecated: bool = False`

Rank: `approved=0, under_review=1, not_reviewed=2, deprecated=3, rejected=4`
(`scientific_common.py:37-43`).

Default visible set: `{approved, under_review, not_reviewed}`.

### 9.4 Curator surfaces

- `GET /submissions/for-review` — curator/admin only.
- `GET /submissions/mine` — caller's own submissions.
- `GET /record-reviews` — authenticated; filterable by record_type, status, submission.
- `GET /record-reviews/{record_type}/{record_id}` — fetch one review by record.

### 9.5 Submission-of-record traceability

- `record_review.submission_id` exists on the curator-facing review row.
- `SubmissionRecordLink` connects a submission to `(record_type, record_id)` pairs.
- Scientific-side:
  `CalculationProvenanceBlock.submission_id` / `submission_ref` exist
  ([scientific_species_calculations.py:245-258](../../app/schemas/reads/scientific_species_calculations.py));
  Phase D will hide `submission_id` behind `include=internal_ids`.
- **Other scientific reads do not expose `submission_id`**, so a regular
  caller cannot click from a thermo/kinetics record back to its submission.

---

## 10. Provenance Visibility Audit

### 10.1 What scientific reads expose

| Provenance dimension | thermo (search/detail) | kinetics (search/detail) | species-calculations | `/full` |
|---|---|---|---|---|
| level_of_theory (id+ref+method+basis+...) | ✓ (when `include=provenance`) | ✓ (always in provenance block) | ✓ | via children |
| software_release | ✓ | ✓ | ✓ | via children |
| workflow_tool_release | ~ (string field only) | ✓ | ✓ | via children |
| literature | ~ | ✓ | ~ | ~ |
| primary_calculation (CalculationEvidenceSummary) | ✓ | ✓ | ✓ | ✓ |
| supporting_calculations | ✓ | ✓ | ✓ (id list + ref objects) | ✓ |
| calculation dependencies | ✗ | (via TS chain) | ✗ | ✓ (TS dependency graph) |
| geometry_validation summary | ~ | ✓ | ✓ (`include=validation`) | ✓ |
| scf_stability summary | ~ | ✓ | ✓ (`include=scf_stability`) | ~ |
| input/output geometries | ✗ | (via path_search/irc) | ✓ (`include=geometry`) | ✗ |
| artifacts | metadata only (`include=artifacts`) | metadata only | metadata only | loosely-typed dicts |
| parameters / constraints | ✗ | ✗ | ✗ | ✗ |

### 10.2 What `/scientific/reaction-entries/{id}/full` returns

Conditional sections (only included when in include set):
`species`, `kinetics`, `transition_states` (with TS dependency graph),
`calculations` (lightweight summaries), `path_search`, `irc`, `scans`,
`conformers`, `artifacts`. Plus `review_records` if `include_review=full`.

The `irc`, `scans`, `conformers`, `artifacts` arrays are typed as
`list[dict[str, object]] | None` — shape not strictly defined in v0.

### 10.3 Calculation Tier-A/B endpoints

`GET /calculations/{id}` returns `CalculationRead` with calculation
metadata, results, dependencies, constraints, parameters, validation,
SCF stability, scan/IRC/path-search results — but with **no review
visibility, no rejected/deprecated filtering, and no public-handle
contract**. These are functionally internal/admin endpoints accessible
under the legacy gate.

Artifact download path (binary content) is **not** part of the
scientific surface — only metadata is exposed.

---

## 11. ID Exposure and Public Safety Audit

### 11.1 IntegrityError / 422 hygiene

`backend/app/api/errors.py:117-152`:

- Constraint name extracted from `orig.diag` is **logged server-side
  only**, never echoed in the HTTP body.
- Responses use stable codes: `unique_conflict`, `reference_conflict`,
  `state_conflict`, `integrity_conflict`.
- `NotFoundError` (404) logs the integer id server-side; the
  client-facing detail is generic.
- 422 codes are stable application codes (`invalid_handle`,
  `handle_type_mismatch`, `*_handle_conflict`, `unknown_include_token`,
  `client_sort_not_supported`, `missing_identifier`, etc.) — no raw
  database text.

### 11.2 ID exposure today (Phase B/C active)

| Surface | Integer `*_id` fields | Public `*_ref` fields |
|---|---|---|
| All `/scientific/*` reads | **Currently exposed by default** (Phase D hiding gated by `settings.allow_public_internal_ids`) | Always present alongside |
| Legacy entity routes | exposed | not present |
| Curator endpoints (`/submissions`, `/record-reviews`) | exposed (curator-facing tables; `created_by`, `approved_by`, `submission_id` etc.) | not present |
| `/calculations/{id}/...` Tier-A/B | exposed | not present |

Public-handle generation: `app/services/public_refs.py`. 28 prefix types.
Content-derived refs (LoT, species, chem_reaction, geometry, software,
software_release, workflow_tool, workflow_tool_release, literature,
conformer_assignment_scheme) are SHA256-based and cross-instance stable.
ULID-style refs (species_entry, reaction_entry, calculation, kinetics,
thermo, statmech, transport, conformer_*, transition_state*, submission)
are per-instance unique.

### 11.3 Phase D readiness

`app/services/scientific_read/internal_ids.py`:
- `should_include_internal_ids()` (line 80) gates on the
  `internal_ids` token + `settings.allow_public_internal_ids`
- `strip_internal_ids()` (lines 145-168) removes any key matching `*_id` /
  `*_ids` suffix or literal `id`/`record_id`/user-FK keys
- `request.filter` echo is preserved verbatim
- Routes already call `apply_internal_ids_visibility()` so toggling the
  setting hides IDs immediately — no further code change required

### 11.4 Bare integer arrays in current responses

`input_geometry_ids`, `output_geometry_ids`, `supporting_calculation_ids`
are bare `list[int]` fields in `species-calculations`/provenance responses.
Object-array siblings (`input_geometries`, `output_geometries`,
`supporting_calculations`) exist with refs, but the bare lists currently
expose ids unless Phase D is active.

`SubmissionRecordLink` (and `ReviewRecordEntry.record_id` in `/full`)
have **no `*_ref` sibling** for the linked record because it is
polymorphic — a future Phase D.1 needs to address this.

### 11.5 Curator-only fields in user-facing schemas

No leak found. Reviewer identity (`reviewed_by`) is reduced to
`reviewer_kind` in scientific reads; `created_by`, `approved_by`,
`rejected_by` only appear in the curator-gated `/submissions` and
`/record-reviews` schemas.

---

## 12. Missing Scientific Query Surfaces

| Missing surface | Priority | Reason |
|---|---|---|
| `/scientific/calculations/search` | **MVP** | The legacy `/calculations` list takes only pagination; today there is no way to ask "show me opt calcs at ωB97X-D/def2-TZVP that converged" without going through `species-calculations/search` (which forces a species filter) or hand-walking `/lookup/calculations`. |
| `/scientific/transition-states/search` and `/scientific/transition-states/{id}/full` | **MVP** | Today TS only appears as a child in `/full` or via the legacy table-style read. Cannot answer "find TS for reaction X with method Y." |
| `/scientific/conformers/...` (search by species + read with assignment scheme + selection) | Phase 2 | Conformer groups are exposed but only as nested children; `conformer_assignment_scheme` has no API at all; `conformer_selection` is write-only. |
| `/scientific/statmech/search` (chemistry-first) | Phase 2 | Legacy `/statmech` requires `species_entry_id` integer. No way to find statmech for a SMILES. |
| `/scientific/transport/search` (chemistry-first) | Phase 2 | Same gap as statmech. |
| `/scientific/networks/search` and `/scientific/networks/{id}/full` | Phase 2 | Network reads are deeply-nested table-style only; no chemistry-first filter, no review/trust visibility, no public-handle contract. |
| Literature-centered query (records citing DOI/ISBN, "what does this paper contribute?") | Phase 2 | `/literature/{id}` returns the paper but never the records that cite it. |
| Substructure / similarity search using RDKit cartridge | Phase 2 | The `mol` column type is in place; no endpoint uses it for substructure or similarity. |
| Artifact search/download (not just metadata) | Phase 2 | Today only artifact metadata is surfaced; there is no "fetch artifact body" endpoint in the scientific surface. |
| Applied-correction / scale-factor search by chemistry context | Later | Currently only id/scheme filters. |
| `/scientific/records/{type}/{id}/provenance` unified projection | Later | Today only the `/full` composite exists for reaction entries. A generic provenance endpoint would let any record be traced. |
| Bulk export endpoint | Later | No CSV/JSONL/Parquet bulk export exists. Useful for downstream model training but not MVP. |
| Curator review queue beyond submissions (per-record review pipeline) | Later | `/submissions/for-review` is submission-grained; per-record curation is via `/record-reviews` listing only. |
| NEB results | Not recommended | No `calc_neb_image_result` ORM table; would need schema work first. |
| `transition_state_selection` API | Not recommended | No ORM table; not a current schema concept. |

---

## 13. MVP Gap Analysis

Comparison against the target MVP surface in the prompt:

| Target endpoint | Status | Better existing equivalent | Recommended action |
|---|---|---|---|
| `GET\|POST /api/v1/species/search` | **exists** | `GET /api/v1/scientific/species/search` (POST not yet implemented) | Add POST body variant for parity with thermo/kinetics search |
| `GET /api/v1/species-entries/{id}/thermo` | **exists** | `GET /api/v1/scientific/species-entries/{id}/thermo` | Promote `/scientific/species-entries/{id}/thermo` as the canonical read; the legacy `/species-entries/{id}/thermo` should remain only for parity until removal |
| `GET\|POST /api/v1/reactions/search` | **exists** | `GET\|POST /api/v1/scientific/reactions/search` | None — covered |
| `GET /api/v1/reaction-entries/{id}/kinetics` | **exists** | `GET /api/v1/scientific/reaction-entries/{id}/kinetics` | None — covered |
| `GET /api/v1/reaction-entries/{id}/full` | **exists** | `GET /api/v1/scientific/reaction-entries/{id}/full` | None — covered |
| `GET /api/v1/calculations/{id}` | **exists but insufficient** | Legacy `GET /calculations/{id}` exists but returns Tier-A/B internal shape, no review visibility, no public-handle envelope | Add `GET /api/v1/scientific/calculations/{id}` returning a public-handle, review-aware projection (or extend `species-calculations/search` to address single-id reads) |
| `GET /api/v1/calculations/{id}/dependencies` | **exists but insufficient** | Legacy `GET /calculations/{id}/dependencies` exists | Promote / re-expose under `/scientific` with refs and review-status |
| `GET /api/v1/calculations/{id}/artifacts` | **exists but insufficient** | Legacy `GET /calculations/{id}/artifacts` exists (metadata only) | Same — promote with refs; decide on artifact download policy |
| `GET /api/v1/calculations/{id}/geometry-validation` | **exists but insufficient** | Legacy `GET /calculations/{id}/geometry-validations` (note: plural in current code) | Same — promote under `/scientific` |

---

## 14. Recommended Implementation Order

Based on the MVP gap analysis above, in priority order (audit only — not a
plan to execute now):

1. **Decide on Phase D default.** Flip `allow_public_internal_ids=False`
   in production once consumers are migrated to public refs. This is
   already implemented; only configuration plus consumer audit is needed.
2. **`GET /scientific/calculations/{id}`** — minimal scientific projection
   of one calculation: ref envelope, review badge, level_of_theory +
   software + workflow_tool summaries, conditional includes for
   `dependencies`, `parameters`, `constraints`, `artifacts`,
   `geometry-validation`, `scf-stability`, `scan`, `irc`, `path-search`.
3. **`GET /scientific/calculations/search`** — multi-axis chemistry
   filters (method, basis, lot, software, validation status, owner kind),
   review-aware, deterministic ordering.
4. **POST body variant of `/scientific/species/search`** — parity with
   thermo/kinetics search.
5. **`GET /scientific/transition-states/{id}` and `/scientific/transition-states/search`** — fill the TS-read gap.
6. **Chemistry-first `/scientific/statmech/search` and `/scientific/transport/search`** — these are blocking the "find me data for SMILES X" workflow without going through species lookup first.
7. **`GET /scientific/networks/search` and `/scientific/networks/{id}/full`** — bring PDep onto the public-handle contract.

---

## 15. Open Questions

1. **Phase D rollout timing.** The internal-ID hiding is implemented but
   the production default is unclear from `settings.allow_public_internal_ids`.
   Confirm what the hosted deployment uses today.
2. **Artifact download policy.** Is a binary-fetch endpoint planned under
   `/scientific/`, or is metadata-only the steady state? The existing
   tests (`test_api_calculation_artifacts.py`) cover upload + metadata
   read but appear silent on read-back of binary content.
3. **Submission visibility from scientific records.** Should regular
   thermo/kinetics scientific reads expose `submission_ref` so users can
   trace a record to its provenance bundle? Today only
   species-calculations exposes it.
4. **`SubmissionRecordLink.record_id` / `ReviewRecordEntry.record_id`
   polymorphism.** These have no `*_ref` sibling because the linked
   record's type is dynamic. Need a uniform "polymorphic record handle"
   concept before Phase D can fully strip these ids.
5. **Legacy reads gate semantics in production.** With
   `LEGACY_READS_REQUIRE_AUTH=true`, the legacy entity routes are
   available only to authenticated users. Are they still considered
   supported, or should some be deprecated in favor of `/scientific`
   equivalents?
6. **`/scientific/species-calculations/search` vs `/scientific/calculations/search`.** Do we want both, or should the
   former absorb a "no chemistry filter" mode? Current schema requires at
   least one species identifier, which prevents pure
   "find calcs by method" queries.
7. **Conformer surface scope.** Are conformer groups/observations
   intended to be primary objects in the public API, or always nested
   under species? `conformer_assignment_scheme` has no API at all today.
8. **Determinism of legacy list endpoints.** Are clients relying on the
   non-deterministic ordering of `/api/v1/{entity}` lists? Pinning order
   may be a small but useful hardening.

---

## 16. Appendix: Route Inventory Table

Read/query endpoints only. Auth column: `legacy_gate` =
`require_auth_for_legacy_reads`, `none` = always public, `session` =
`get_current_user`, `curator` = `require_curator_or_admin`.

| Module | Method | Full path | Handler | Auth | Response | Service / source |
|---|---|---|---|---|---|---|
| health | GET | `/api/v1/health` | `health` | none | dict | inline |
| auth | GET | `/api/v1/auth/me` | `me` | session | `MeResponse` | inline |
| auth | GET | `/api/v1/auth/api-keys` | `list_keys` | session | `list[ApiKeyMetadata]` | inline |
| jobs | GET | `/api/v1/jobs/{job_id}` | `get_job_status` | session | `JobStatusResponse` | direct query |
| lookup | GET | `/api/v1/lookup/species` | `lookup_species` | none | `LookupResponse` | `canonical_species_identity` |
| lookup | GET | `/api/v1/lookup/calculations` | `lookup_calculations` | none | `LookupResponse` | direct query |
| lookup | GET | `/api/v1/lookup/thermo` | `lookup_thermo` | none | `LookupResponse` | direct query |
| lookup | GET | `/api/v1/lookup/species-calculation` | `lookup_species_calculation` | none | `LookupResponse` | combined |
| lookup | GET | `/api/v1/lookup/reaction` | `lookup_reaction` | none | `LookupResponse` | identity + stoichiometry hash |
| lookup | GET | `/api/v1/lookup/kinetics` | `lookup_kinetics` | none | `LookupResponse` | direct query |
| lookup | GET | `/api/v1/lookup/reaction-kinetics` | `lookup_reaction_kinetics` | none | `LookupResponse` | combined |
| lookup | GET | `/api/v1/lookup/geometry` | `lookup_geometry` | none | `LookupResponse` | by `geom_hash` |
| lookup | GET | `/api/v1/lookup/statmech` | `lookup_statmech` | none | `LookupResponse` | direct query |
| lookup | GET | `/api/v1/lookup/transport` | `lookup_transport` | none | `LookupResponse` | direct query |
| lookup | GET | `/api/v1/lookup/network` | `lookup_network` | none | `LookupResponse` | contains-all on `network_species` |
| calculations | GET | `/api/v1/calculations` | `list_calculations` | legacy_gate | `PaginatedResponse[CalculationRead]` | direct query |
| calculations | GET | `/api/v1/calculations/{id}` | `get_calculation` | legacy_gate | `CalculationRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/sp-result` | (Tier-B) | legacy_gate | `CalculationSPResultRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/opt-result` | (Tier-B) | legacy_gate | `CalculationOptResultRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/freq-result` | (Tier-B) | legacy_gate | `CalculationFreqResultRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/scan-result` | (Tier-B) | legacy_gate | `CalculationScanResultRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/irc-result` | (Tier-B) | legacy_gate | `CalculationIRCResultRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/path-search-result` | (Tier-B) | legacy_gate | `CalculationPathSearchResultRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/input-geometry` | (Tier-B) | legacy_gate | `CalculationInputGeometryDetailRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/output-geometry` | (Tier-B) | legacy_gate | `CalculationOutputGeometryDetailRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/dependencies` | (Tier-B) | legacy_gate | `CalculationDependencyDirectionalRead` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/parameters` | (Tier-B) | legacy_gate | `list[CalculationParameterRead]` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/constraints` | (Tier-B) | legacy_gate | `list[CalculationConstraintRead]` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/artifacts` | `list_artifacts` | legacy_gate | `list[CalculationArtifactRead]` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/geometry-validations` | (Tier-B) | legacy_gate | `list[CalculationGeometryValidationRead]` | direct query |
| calculations | GET | `/api/v1/calculations/{id}/scf-stabilities` | (Tier-B) | legacy_gate | `list[CalculationSCFStabilityRead]` | direct query |
| species | GET | `/api/v1/species` | `list_species` | legacy_gate | `PaginatedResponse[SpeciesRead]` | direct query |
| species | GET | `/api/v1/species/{id}` | `get_species` | legacy_gate | `SpeciesRead` | direct query |
| species | GET | `/api/v1/species-entries/{id}` | `get_species_entry` | legacy_gate | `SpeciesEntryRead` | direct query + conformer rollup |
| species | GET | `/api/v1/species-entries/{id}/conformer-groups` | (entries_router) | legacy_gate | `list[ConformerGroupRead]` | direct query |
| species | GET | `/api/v1/species-entries/{id}/thermo` | (entries_router) | legacy_gate | `list[ThermoRead]` | direct query |
| species | GET | `/api/v1/species-entries/{id}/statmech` | (entries_router) | legacy_gate | `list[StatmechRead]` | direct query |
| species | GET | `/api/v1/species-entries/{id}/transport` | (entries_router) | legacy_gate | `list[TransportRead]` | direct query |
| reactions | GET | `/api/v1/reactions` | `list_reactions` | legacy_gate | `PaginatedResponse[ChemReactionRead]` | direct query |
| reactions | GET | `/api/v1/reactions/{id}` | `get_reaction` | legacy_gate | `ChemReactionRead` | direct query |
| reactions | GET | `/api/v1/reaction-entries/{id}` | `get_reaction_entry` | legacy_gate | `ReactionEntryRead` | direct query |
| reactions | GET | `/api/v1/reaction-entries/{id}/kinetics` | `list_kinetics_for_entry` | legacy_gate | `list[KineticsRead]` | direct query |
| kinetics | GET | `/api/v1/kinetics` | `list_kinetics` | legacy_gate | `PaginatedResponse[KineticsRead]` | direct query |
| kinetics | GET | `/api/v1/kinetics/{id}` | `get_kinetics` | legacy_gate | `KineticsRead` | direct query |
| thermo | GET | `/api/v1/thermo` | `list_thermo` | legacy_gate | `PaginatedResponse[ThermoRead]` | direct query |
| thermo | GET | `/api/v1/thermo/{id}` | `get_thermo` | legacy_gate | `ThermoRead` | direct query |
| geometries | GET | `/api/v1/geometries` | `list_geometries` | legacy_gate | `PaginatedResponse[GeometryRead]` | direct query |
| geometries | GET | `/api/v1/geometries/{id}` | `get_geometry` | legacy_gate | `GeometryRead` | direct query |
| transition_states | GET | `/api/v1/transition-states` | `list_transition_states` | legacy_gate | `PaginatedResponse[TransitionStateRead]` | direct query |
| transition_states | GET | `/api/v1/transition-states/{id}` | `get_transition_state` | legacy_gate | `TransitionStateRead` | direct query |
| transition_states | GET | `/api/v1/transition-states/entries/{id}` | `get_transition_state_entry` | legacy_gate | `TransitionStateEntryRead` | direct query |
| conformers | GET | `/api/v1/conformer-groups` | `list_conformer_groups` | legacy_gate | `PaginatedResponse[ConformerGroupRead]` | direct query |
| conformers | GET | `/api/v1/conformer-groups/{id}` | `get_conformer_group` | legacy_gate | `ConformerGroupDetailRead` | direct query |
| conformers | GET | `/api/v1/conformer-observations` | `list_conformer_observations` | legacy_gate | `PaginatedResponse[ConformerObservationRead]` | direct query |
| conformers | GET | `/api/v1/conformer-observations/{id}` | `get_conformer_observation` | legacy_gate | `ConformerObservationRead` | direct query |
| energy_corrections | GET | `/api/v1/energy-correction-schemes` | `list_energy_correction_schemes` | legacy_gate | `PaginatedResponse[EnergyCorrectionSchemeRead]` | direct query |
| energy_corrections | GET | `/api/v1/energy-correction-schemes/{id}` | `get_energy_correction_scheme` | legacy_gate | `EnergyCorrectionSchemeRead` | direct query |
| energy_corrections | GET | `/api/v1/frequency-scale-factors` | `list_frequency_scale_factors` | legacy_gate | `PaginatedResponse[FrequencyScaleFactorRead]` | direct query |
| energy_corrections | GET | `/api/v1/frequency-scale-factors/{id}` | `get_frequency_scale_factor` | legacy_gate | `FrequencyScaleFactorRead` | direct query |
| energy_corrections | GET | `/api/v1/applied-energy-corrections` | `list_applied_energy_corrections` | legacy_gate | `PaginatedResponse[AppliedEnergyCorrectionRead]` | direct query |
| energy_corrections | GET | `/api/v1/applied-energy-corrections/{id}` | `get_applied_energy_correction` | legacy_gate | `AppliedEnergyCorrectionRead` | direct query |
| software | GET | `/api/v1/software` | `list_software` | legacy_gate | `PaginatedResponse[SoftwareRead]` | direct query |
| software | GET | `/api/v1/software/{id}` | `get_software` | legacy_gate | `SoftwareRead` | direct query |
| software | GET | `/api/v1/software-releases` | `list_software_releases` | legacy_gate | `PaginatedResponse[SoftwareReleaseRead]` | direct query |
| software | GET | `/api/v1/software-releases/{id}` | `get_software_release` | legacy_gate | `SoftwareReleaseRead` | direct query |
| levels_of_theory | GET | `/api/v1/levels-of-theory` | `list_levels_of_theory` | legacy_gate | `PaginatedResponse[LevelOfTheoryRead]` | direct query |
| levels_of_theory | GET | `/api/v1/levels-of-theory/{id}` | `get_level_of_theory` | legacy_gate | `LevelOfTheoryRead` | direct query |
| literature | GET | `/api/v1/literature` | `list_literature` | legacy_gate | `PaginatedResponse[LiteratureRead]` | direct query |
| literature | GET | `/api/v1/literature/{id}` | `get_literature` | legacy_gate | `LiteratureRead` | direct query |
| workflow_tools | GET | `/api/v1/workflow-tools` | `list_workflow_tools` | legacy_gate | `PaginatedResponse[WorkflowToolRead]` | direct query |
| workflow_tools | GET | `/api/v1/workflow-tools/{id}` | `get_workflow_tool` | legacy_gate | `WorkflowToolDetailRead` | direct query |
| workflow_tools | GET | `/api/v1/workflow-tool-releases` | `list_workflow_tool_releases` | legacy_gate | `PaginatedResponse[WorkflowToolReleaseRead]` | direct query |
| workflow_tools | GET | `/api/v1/workflow-tool-releases/{id}` | `get_workflow_tool_release` | legacy_gate | `WorkflowToolReleaseDetailRead` | direct query |
| statmech | GET | `/api/v1/statmech` | `list_statmech` | legacy_gate | `PaginatedResponse[StatmechRead]` | direct query |
| statmech | GET | `/api/v1/statmech/{id}` | `get_statmech` | legacy_gate | `StatmechRead` | direct query |
| transport | GET | `/api/v1/transport` | `list_transport` | legacy_gate | `PaginatedResponse[TransportRead]` | direct query |
| transport | GET | `/api/v1/transport/{id}` | `get_transport` | legacy_gate | `TransportRead` | direct query |
| networks | GET | `/api/v1/networks` | `list_networks` | legacy_gate | `PaginatedResponse[NetworkListItemRead]` | direct query |
| networks | GET | `/api/v1/networks/{id}` | `get_network_detail` | legacy_gate | `NetworkDetailRead` | direct query |
| networks | GET | `/api/v1/networks/{id}/solves` | `list_network_solves` | legacy_gate | `PaginatedResponse[NetworkSolveListItemRead]` | direct query |
| networks | GET | `/api/v1/networks/{id}/solves/{id}` | `get_network_solve_detail` | legacy_gate | `NetworkSolveDetailRead` | direct query |
| networks | GET | `/api/v1/networks/{id}/states/{id}` | (nested) | legacy_gate | `NetworkStateRead` | direct query |
| networks | GET | `/api/v1/networks/{id}/channels/{id}` | (nested) | legacy_gate | `NetworkChannelRead` | direct query |
| networks | GET | `/api/v1/networks/{id}/solves/{id}/kinetics` | (nested) | legacy_gate | `list[NetworkKineticsRead]` | direct query |
| submissions | GET | `/api/v1/submissions/mine` | `list_mine` | session | `list[SubmissionRead]` | `list_my_submissions` |
| submissions | GET | `/api/v1/submissions/for-review` | `list_for_review` | curator | `list[SubmissionRead]` | `list_submissions_for_review` |
| submissions | GET | `/api/v1/submissions/{id}` | `get_submission` | session+perm | `SubmissionRead` | `get_submission` |
| submissions | GET | `/api/v1/submissions/{id}/audit-events` | `list_audit_events` | session+perm | `list[SubmissionAuditEventRead]` | `list_audit_events` |
| submissions | GET | `/api/v1/submissions/{id}/record-links` | `list_record_links` | session+perm | `list[SubmissionRecordLinkRead]` | `list_record_links` |
| record_reviews | GET | `/api/v1/record-reviews` | `list_reviews` | session | `list[RecordReviewRead]` | `list_record_reviews` |
| record_reviews | GET | `/api/v1/record-reviews/{record_type}/{record_id}` | `read_review` | session | `RecordReviewRead` | `get_record_review` |
| scientific/species | GET | `/api/v1/scientific/species/search` | `species_search` | none | `ScientificSpeciesSearchResponse` | `search_species` |
| scientific/reactions | GET | `/api/v1/scientific/reactions/search` | `reaction_search_get` | none | `ScientificReactionSearchResponse` | `search_reactions` |
| scientific/reactions | POST | `/api/v1/scientific/reactions/search` | `reaction_search_post` | none | `ScientificReactionSearchResponse` | `search_reactions` |
| scientific/kinetics | GET | `/api/v1/scientific/reaction-entries/{id}/kinetics` | `reaction_kinetics` | none | `ScientificReactionKineticsResponse` | `get_reaction_kinetics` |
| scientific/thermo | GET | `/api/v1/scientific/species-entries/{id}/thermo` | `species_thermo` | none | `ScientificSpeciesThermoResponse` | `get_species_thermo` |
| scientific/kinetics_search | GET | `/api/v1/scientific/kinetics/search` | `kinetics_search_get` | none | `ScientificKineticsSearchResponse` | `search_kinetics` |
| scientific/kinetics_search | POST | `/api/v1/scientific/kinetics/search` | `kinetics_search_post` | none | `ScientificKineticsSearchResponse` | `search_kinetics` |
| scientific/thermo_search | GET | `/api/v1/scientific/thermo/search` | `thermo_search_get` | none | `ScientificThermoSearchResponse` | `search_thermo` |
| scientific/thermo_search | POST | `/api/v1/scientific/thermo/search` | `thermo_search_post` | none | `ScientificThermoSearchResponse` | `search_thermo` |
| scientific/species_calculations_search | GET | `/api/v1/scientific/species-calculations/search` | `species_calculations_search_get` | none | `ScientificSpeciesCalculationsSearchResponse` | `search_species_calculations` |
| scientific/species_calculations_search | POST | `/api/v1/scientific/species-calculations/search` | `species_calculations_search_post` | none | `ScientificSpeciesCalculationsSearchResponse` | `search_species_calculations` |
| scientific/geometries | GET | `/api/v1/scientific/geometries/{geometry_handle}` | `scientific_geometry_detail` | none | `ScientificGeometryResponse` | `get_geometry` |
| scientific/provenance | GET | `/api/v1/scientific/reaction-entries/{id}/full` | `reaction_full` | none | `ScientificReactionFullResponse` | `get_reaction_full` |

---

## Insight

★ Insight ─────────────────────────────────────
- TCKDB has reached the architectural pattern of "two parallel surfaces"
  that many maturing APIs land in: a low-level CRUD/table layer for
  internal/admin work and a higher-level scientific layer with deterministic
  ordering, public handles, and review/trust visibility. The interesting
  question for v1 is not "how do we add more endpoints" but "can the
  legacy gate be flipped off entirely once `/scientific/calculations/...`
  exists?"
- The schema-to-API coverage table makes a real trade-off visible: ~36%
  of tables are intentionally read-only via parents (children, junction
  tables, embedded value lists). This is the right call — exposing them
  directly would invite N+1 and would dilute the public ref namespace.
- The biggest *quietly-load-bearing* dependency is `public_refs.py`'s
  prefix registry: every new scientific endpoint added in the future must
  pick a prefix and register it there before it can claim Phase D
  compliance.
─────────────────────────────────────────────────
