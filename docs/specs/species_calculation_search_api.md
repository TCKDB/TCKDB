# Phase 7 — Species Calculation/Conformer Search API (design)

**Status:** Draft / pending implementation
**Owners:** Calvin
**Date:** 2026-05-10
**Builds on:** Phases 2–6 (locked); see docs/specs/read_api_mvp.md, [docs/guides/workflow_tool_scientific_reads.md](../guides/workflow_tool_scientific_reads.md).
**Code change in this phase:** **none** — design only.

---

## Purpose

Add a chemistry-first search surface for **calculations and conformer
context**, so workflow tools can answer questions like:

> *I have this species. What calculations does TCKDB know about? Give me
> the lowest-SP-energy calculation at this level of theory. Tell me
> what conformer it belongs to and how trustworthy it is.*

This is the calculation-and-conformer analogue of Phase 6's chemistry-first
thermo and kinetics search. It is **additive** — no existing endpoints
change.

---

## Scope

In scope:

- One new endpoint: `GET|POST /api/v1/scientific/species-calculations/search`
- Chemistry-first species filtering (no `species_entry_id` required)
- Calculation-type filtering (`sp`, `opt`, `freq`, `conf`, `scan`, `irc`, `path_search`)
- Direct LoT / software / workflow-tool filtering on `Calculation`
- Conformer context surfaced when present (never fabricated)
- Geometry surfaced as IDs by default; full XYZ deferred to a future include flag
- Measurable ranking: `latest`, `earliest`, `lowest_energy`, `review_rank`
- Standard collapse / pagination / review semantics from Phases 2/4/6
- Pydantic response models reusing common fragments where possible

Out of scope (v0):

- Frontend implementation
- Schema changes
- ARC/RMG-specific reuse logic
- Subjective selectors (`best`, `preferred`, `tckdb_default`, `highest_lot`)
- A general calculation-graph query language
- Replacing entry-id detail endpoints
- Full XYZ in default response (deferred to `include=geometry` or a
  dedicated geometry endpoint)
- `neb` calculations — not present in `CalculationType` enum in v0

---

## Why this is additive (not a replacement)

The existing scientific surface answers different questions:

| Endpoint | Question it answers |
|---|---|
| `species/search` | What species entries match this identity? |
| `thermo/search` | What thermo records exist for this species identity? |
| `species-entries/{id}/thermo` | Detail/follow-up: thermo for a known entry id |
| `reactions/search` | What reaction entries match these reactants/products? |
| `kinetics/search` | What kinetics records exist for this reaction identity? |
| `reaction-entries/{id}/kinetics` | Detail/follow-up: kinetics for a known entry id |
| `reaction-entries/{id}/full` | Composite provenance for a known reaction entry |
| **`species-calculations/search`** | **What calculations/geometries/conformers exist for this species identity under explicit computational filters?** |

The calculation/conformer view has different filters (LoT applies to the
calculation row directly), different ranking semantics (`lowest_energy`
is meaningful here in a way it is not for thermo/kinetics products), and
different output focus (geometry IDs and conformer context vs computed
NASA polynomials or Arrhenius parameters).

Folding this into `thermo/search` would have overloaded one endpoint
with two different ranking vocabularies and two different filter
semantics for `level_of_theory_id`. Keeping it separate keeps each
endpoint honest about what it returns and what it ranks by.

---

## Endpoint

```
GET  /api/v1/scientific/species-calculations/search
POST /api/v1/scientific/species-calculations/search
```

**Chosen name: `species-calculations/search`** rather than `conformers/search`.

Rationale: the user question *"this species at this LoT, lowest SP"* is
calculation-centered. The conformer is supplementary context for some
records (and absent for others), not the response root. Naming the
endpoint after the calculation matches how every record's primary key
is read: `calculation_id`. A future conformer-centered endpoint
(`conformers/search` or similar) can be added if a workflow really
needs to start from a conformer group identity rather than a calculation.

GET/POST follow the Phase 6 convention: GET for simple interactive
queries, POST preferred for hosted workflow tools and structured
queries. POST query-string keys are rejected with the documented
`post_search_fields_must_be_in_body` 422 (Phase 4 / Phase 6 behavior).

---

## Request shape

### Species identity filters (chemistry-first)

| Field | Type | Notes |
|---|---|---|
| `smiles` | string | At least one identity filter (or one of the explicit handles) is required. |
| `inchi` | string | |
| `inchi_key` | string | |
| `formula` | string | |
| `charge` | int | |
| `multiplicity` | int | |
| `electronic_state_kind` | enum | `SpeciesEntryStateKind`: `ground | excited` |
| `species_entry_kind` | enum | `StationaryPointKind`: `minimum | vdw_complex` |

Multiple identifiers AND-combine. **Inconsistent identifiers → 200 with
`records: []`** and `pagination.total: 0`, not 422 (matches Phase 6
species/thermo behavior).

### Explicit handles (optional, for callers who already have ids)

| Field | Type | Notes |
|---|---|---|
| `species_id` | int | Filters to entries belonging to this species. |
| `species_entry_id` | int | Filters to this exact entry. **Returns 404** if the entry id does not exist (matches detail-endpoint behavior). |

These are convenience handles for chained workflows or for clients
fetching follow-up calculations after a `search_thermo` call. They are
**not** required for chemistry-first usage.

### Calculation filters

| Field | Type | Notes |
|---|---|---|
| `calculation_type` | enum | `CalculationType`: `opt | freq | sp | irc | scan | path_search | conf`. (`neb` is not in the v0 enum.) |
| `level_of_theory_id` | int | Filters `Calculation.lot_id` directly. |
| `method` | string | Filters via `LevelOfTheory.method` join. |
| `basis` | string | Filters via `LevelOfTheory.basis` join. |
| `software` | string | Filters via `Software.name` (joined through `SoftwareRelease`). |
| `workflow_tool` | string | Filters via `WorkflowTool.name` (joined through `WorkflowToolRelease`). |
| `scientific_origin` | enum | Reserved for parity. Non-null values fail closed with 422 `unsupported_filter` because calculation rows have no equivalent persisted field. |
| `calculation_quality` | enum | `CalculationQuality`: `raw | curated | rejected`. **Defaults to excluding `rejected`** (mirrors review-trust posture). |

### Trust filters (shallow per D7)

| Field | Type | Default | Notes |
|---|---|---|---|
| `min_review_status` | enum | — | Filters the calculation's direct `RecordReview` row (`record_type='calculation'`). |
| `include_rejected` | bool | `false` | Opts in `review_status=rejected` records. |
| `include_deprecated` | bool | `false` | Opts in `review_status=deprecated` records. |

### Sort / collapse / pagination

| Field | Type | Default | Notes |
|---|---|---|---|
| `ranking` | enum | `default` | See [Ranking semantics](#ranking-semantics). |
| `collapse` | enum | `all` | `all | first` per Phase 2.1 spec. |
| `include` | list[str] | `[]` | See [Include tokens](#include-tokens). |
| `offset` | int | `0` | L5 pagination. |
| `limit` | int | `50` | Max 200. |

`sort` (free-form client sort) is **not accepted in v0** — same as every
other scientific endpoint. Sending it returns 422
`client_sort_not_supported`.

---

## Ranking semantics

This is the first scientific endpoint where an explicit measurable
ranking is appropriate, because individual calculations carry energies
and dates that order linearly.

### Allowed v0 ranking values

```
default        — review_rank ASC, created_at DESC, id DESC
latest         — created_at DESC, id DESC
earliest       — created_at ASC, id ASC
review_rank    — review_rank ASC, created_at DESC, id DESC  (same as default but explicit)
lowest_energy  — energy_hartree ASC NULLS LAST, review_rank ASC, created_at DESC, id DESC
```

`lowest_energy` is **only legal when `calculation_type` is `sp` or
`opt` and both an exact `species_entry_ref` and exact
`level_of_theory_ref` are supplied**. Those constraints make the energy
candidate set physically comparable, while the calculation-type restriction
selects an unambiguous row-level scalar:

- `calculation_type=sp` → ranks by `calc_sp_result.electronic_energy_hartree ASC`
- `calculation_type=opt` → ranks by `calc_opt_result.final_energy_hartree ASC`

For any other `calculation_type` (or for an unfiltered `calculation_type`),
`ranking=lowest_energy` returns:

```

If either exact comparability ref is absent, the endpoint returns:

```
422 Unprocessable Entity
{ "code": "unsafe_lowest_energy_comparison",
  "detail": "ranking=lowest_energy requires exact species_entry_ref and level_of_theory_ref filters." }
```
422 Unprocessable Entity
{ "code": "unsupported_ranking_for_calculation_type",
  "detail": "ranking=lowest_energy requires calculation_type=sp or calculation_type=opt." }
```

`scan`, `irc`, `path_search`, and `conf` energies live on per-image/per-point
result rows, not on the calculation itself. Per-point energy ranking is
deferred to a future point-level endpoint.

### Forbidden selectors (D3 carry-over)

```
best            — not measurable
preferred       — not measurable
tckdb_default   — hidden policy
highest_lot     — LoT quality is not a total order
```

Sending any of these as `ranking=` returns 422 `unknown_ranking`.

### Default sort

When `ranking` is omitted or `ranking=default`:

```sql
ORDER BY
  review_rank ASC,        -- L2 mapping
  created_at DESC,
  id DESC                 -- final deterministic tie-break
```

When `ranking=lowest_energy` (only with `calculation_type=sp|opt`):

```sql
ORDER BY
  energy_hartree ASC NULLS LAST,
  review_rank ASC,
  created_at DESC,
  id DESC
```

Where `energy_hartree` is the appropriate per-result-type column.

`NULLS LAST` is important: a calculation with an energy column populated
should always rank above one with a null energy when ranking by energy.

---

## Level-of-theory filtering semantics

**LoT filters target the calculation row's *direct* LoT.** This is
different from `thermo/search` and `kinetics/search`, which filter the
LoT of a *primary source calculation* (per Phase 2.3 spec).

Here, the searched object is the calculation itself, so direct
filtering is honest and unambiguous:

- `level_of_theory_id=12` matches `Calculation.lot_id == 12`
- `method='wb97xd'` matches `LevelOfTheory.method == 'wb97xd'` joined via `Calculation.lot_id`
- `basis='def2tzvp'` matches `LevelOfTheory.basis == 'def2tzvp'` joined via `Calculation.lot_id`
- `software='gaussian'` matches `Software.name == 'gaussian'` joined via `Calculation.software_release_id`
- `workflow_tool='arc'` matches `WorkflowTool.name == 'arc'` joined via `Calculation.workflow_tool_release_id`

Calculations with a null `lot_id` (or null `software_release_id`) are
excluded when the corresponding filter is supplied — there is no LoT
to compare against.

---

## Conformer context

When a calculation row has `Calculation.conformer_observation_id` set,
the response includes a `conformer` block. When that field is null, the
`conformer` block is **explicitly `null`** — the service does **not**
fabricate conformer associations.

The `conformer` block carries the documented columns from the existing
schema:

```json
{
  "conformer_observation_id": 44,
  "conformer_group_id": 7,
  "conformer_assignment_scheme_id": 3,
  "conformer_group_label": "gauche",
  "torsion_fingerprint_json": { "...summary or null..." },
  "selection_kinds": ["display_default", "lowest_energy"]
}
```

`selection_kinds` is the list of `ConformerSelectionKind` values
attached to this conformer group via `ConformerSelection`. It is purely
informational — it does **not** drive ranking or filtering at this
endpoint. Workflows that want to filter to "selected" conformers can do
so client-side (or add an explicit filter in a future phase).

`torsion_fingerprint_json` may be returned as a compact summary in v0
(e.g. `{"present": true}`) to avoid dumping large fingerprint payloads
by default. The full JSON would be exposed via `include=conformers`.

---

## Geometry behavior

By default the response includes geometry **IDs and metadata only** —
not full XYZ payloads:

```json
{
  "primary_output_geometry_id": 500,
  "input_geometry_ids": [499],
  "output_geometry_ids": [500],
  "primary_output_geometry_role": "final"
}
```

`primary_output_geometry_id` is the `CalculationOutputGeometry` row
with `role=final` (per `CalculationGeometryRole`); if no `final` row
exists it falls back to the most recent non-`final` role, or `null` if
no output geometries exist.

Full XYZ is **deferred** in v0. Two paths are open for a future phase:

1. Add `include=geometry` that embeds the geometry payload inline.
2. Direct callers to a dedicated geometry-detail endpoint via the IDs
   already in the response.

This spec recommends path 2 (dedicated detail endpoint), since geometry
payloads can be large enough to swamp a search response. v0
implementation should accept `include=geometry` as a **legal token** so
adding the embedding behavior later doesn't break clients.

---

## Include tokens

Legal v0 include tokens for this endpoint:

```
provenance       — provenance_summary block (LoT + software + workflow tool, etc.)
calculations     — embed CalculationEvidenceSummary equivalents for related calcs (deferred — accepted but no-op in v0 unless a clear use case lands)
artifacts        — list artifact ids/names for this calculation
review           — per-record review badge (already returned by default; this token is a no-op at the data-shape level, accepted for vocabulary consistency)
conformers       — full conformer block (not just summary); includes torsion_fingerprint_json
geometry         — accepted but deferred in v0 (returns IDs only by default; future phase may embed full XYZ)
validation       — geometry_validation summary block
scf_stability    — scf_stability summary block
internal_ids     — Phase D opt-in: restore integer *_id fields and the bare integer-id arrays (input_geometry_ids, output_geometry_ids, supporting_calculation_ids) when the deployment allows it
all              — every legal token above **except** internal_ids
```

Unknown tokens → 422 `unknown_include_token` with the legal list (Phase 4 convention).
Known but illegal-elsewhere tokens (e.g. `kinetics`, `transition_states`) → 422 same code.

**Phase D bare-array policy.** By default the response carries the
ref-bearing object arrays (`input_geometries`, `output_geometries`,
`supporting_calculations`) only. The legacy bare integer-id arrays
(`input_geometry_ids`, `output_geometry_ids`,
`supporting_calculation_ids`) are restored only when
`include=internal_ids` is allowed by the deployment. See
[`docs/specs/internal_ids_visibility_policy.md`](./internal_ids_visibility_policy.md).

---

## Trust / review behavior

`Calculation` is in the `SubmissionRecordType` enum (`'calculation'`),
so `RecordReview` rows can be attached to calculations. Default trust
posture (Phase 2.1 D5):

```
include approved, under_review, not_reviewed
exclude rejected     (override: include_rejected=true)
exclude deprecated   (override: include_deprecated=true)
```

`min_review_status` is shallow per D7: it filters the calculation's
direct review badge only. Source-chain calculations (e.g. `freq`
calculation paired with an `opt`) are **not** filtered transitively.

If no `RecordReview` row exists for a calculation, its review status
defaults to `not_reviewed` (consistent with the existing
`fetch_review_badges` helper).

`calculation_quality` (the `CalculationQuality` enum on the calculation
row itself) is **separate** from review status:

- `calculation_quality=raw` — calculation as ingested
- `calculation_quality=curated` — calculation flagged as curated
- `calculation_quality=rejected` — calculation deliberately marked as not usable

Default behavior: `quality=rejected` calculations are **excluded**;
they can be opted back in via `include_rejected_quality=true` (separate
from review's `include_rejected`). This rule is documented separately
because quality and review are distinct concepts in the schema.

---

## Evidence and trust fields surfaced per record

Workflow tools should be able to make reuse decisions from the response
without fetching extra detail. Each record exposes:

- `review` — direct `RecordReviewBadge`
- `geometry_validation` — `ValidationSummary` (Phase 2.3 vocabulary: `passed | warning | fail | not_present`)
- `scf_stability` — `SCFStabilitySummary` (Phase 2.3 vocabulary: `stable | unstable | stabilized | inconclusive | not_present`)
- `level_of_theory` — `LevelOfTheorySummary`
- `software_release` — `SoftwareReleaseSummary`
- `workflow_tool_release` — `WorkflowToolReleaseSummary` (when populated)
- `calculation_quality` — string from `CalculationQuality` enum
- `artifacts_available` — boolean (true if any `CalculationArtifact` rows exist for this calc)

The endpoint **does not** compute "good enough" or "reusable". It
returns the evidence; the workflow's adapter applies its own policy.

---

## Response shape

Standard scientific envelope:

```json
{
  "request": {
    "filter": {...},
    "ranking": "lowest_energy",
    "sort": "energy_hartree,review_rank,created_at,id",
    "collapse": "first",
    "include": ["provenance", "conformers", "review"]
  },
  "review_summary": {
    "approved": 0,
    "under_review": 0,
    "not_reviewed": 1,
    "deprecated": 0,
    "rejected": 0,
    "total": 1
  },
  "records": [ /* see per-record below */ ],
  "pagination": {
    "offset": 0,
    "limit": 50,
    "returned": 1,
    "total": 1
  }
}
```

### Per-record shape

```json
{
  "species": {
    "species_id": 12,
    "species_entry_id": 31,
    "canonical_smiles": "CCO",
    "inchi_key": "...",
    "charge": 0,
    "multiplicity": 1,
    "species_entry_kind": "minimum",
    "electronic_state_kind": "ground"
  },
  "calculation": {
    "calculation_id": 900,
    "calculation_type": "sp",
    "calculation_quality": "raw",
    "created_at": "2026-04-12T10:33:00Z",
    "review": { "status": "not_reviewed", "reviewed_at": null, "reviewer_kind": null }
  },
  "energy": {
    "energy_hartree": -154.123456,
    "energy_kind": "electronic_energy"
  },
  "level_of_theory": {
    "level_of_theory_id": 8,
    "method": "wb97xd",
    "basis": "def2tzvp",
    "dispersion": null,
    "solvent": null,
    "label": "wb97xd/def2tzvp"
  },
  "software_release": {
    "software_release_id": 4,
    "software": "Gaussian",
    "version": "16"
  },
  "workflow_tool_release": null,
  "conformer": {
    "conformer_observation_id": 44,
    "conformer_group_id": 7,
    "conformer_assignment_scheme_id": 3,
    "conformer_group_label": "gauche",
    "torsion_fingerprint_json": { "present": true },
    "selection_kinds": ["display_default"]
  },
  "geometry": {
    "primary_output_geometry_id": 500,
    "primary_output_geometry_role": "final",
    "input_geometry_ids": [499],
    "output_geometry_ids": [500]
  },
  "validation": {
    "geometry_validation": { "status": "passed", "calculation_id": 900 },
    "scf_stability": { "status": "stable", "calculation_id": 900 }
  },
  "provenance": {
    "supporting_calculation_ids": [],
    "submission_id": 41,
    "artifacts_available": true
  }
}
```

`energy` is present only when the calculation has a populated energy
result (SP `electronic_energy_hartree` for `sp`, opt `final_energy_hartree`
for `opt`). For other calculation types the `energy` key is `null` —
omitted-entirely is also acceptable; v0 implementation should pick one
and stick with it (recommend `null` so the JSON shape is stable).

`workflow_tool_release` and `conformer` are `null` when not present —
keys are always emitted so clients can rely on a stable shape (matches
Phase 2.2 provenance contract for kinetics).

`provenance.supporting_calculation_ids` is the list of calculation IDs
that point to this calculation via `CalculationDependency.parent_calculation_id`
— useful when the returned calculation is the parent (e.g. an opt)
that has freq/sp children.

---

## Error model

| Trigger | Status | Code |
|---|---|---|
| `species_entry_id` supplied but not found | 404 | (NotFoundError) |
| `species_id` supplied but not found | 404 | (NotFoundError) |
| Chemistry filters supplied but no match | 200 | (empty `records`) |
| No identifier supplied | 422 | `missing_identifier` |
| Unknown `calculation_type` | 422 | (FastAPI enum validation) |
| Unknown `ranking` value | 422 | `unknown_ranking` |
| `ranking=lowest_energy` with non-{sp,opt} `calculation_type` (or no calculation_type) | 422 | `unsupported_ranking_for_calculation_type` |
| `ranking=lowest_energy` without exact `species_entry_ref` and `level_of_theory_ref` | 422 | `unsafe_lowest_energy_comparison` |
| Unknown `include` token | 422 | `unknown_include_token` |
| `sort=` supplied | 422 | `client_sort_not_supported` |
| `temperature_min > temperature_max` | n/a — endpoint has no temperature filters in v0 |
| `offset < 0` or `limit > 200` | 422 | `invalid_pagination` |
| POST with query-string keys | 422 | `post_search_fields_must_be_in_body` |

All errors use the existing `ValueError → 422`, `NotFoundError → 404`
exception handlers from `app/api/errors.py`. No new handler types needed.

---

## Test plan

### Service-layer tests

```
search by SMILES returns calculations for matching species_entries
search by species_entry_id (handle path)
species_entry_id not found → NotFoundError
search returns 200 empty for unmatched chemistry
calculation_type=sp returns only SP calculations
calculation_type=opt returns only opt calculations
ranking=lowest_energy with exact species_entry_ref + level_of_theory_ref + calculation_type=sp orders by calc_sp_result.electronic_energy_hartree ASC
ranking=lowest_energy with exact species_entry_ref + level_of_theory_ref + calculation_type=opt orders by calc_opt_result.final_energy_hartree ASC
ranking=lowest_energy with calculation_type=freq → 422 unsupported_ranking_for_calculation_type
ranking=lowest_energy without calculation_type → 422 unsupported_ranking_for_calculation_type
ranking=lowest_energy without both exact comparability refs → 422 unsafe_lowest_energy_comparison
ranking=latest orders by created_at DESC
ranking=earliest orders by created_at ASC
NULLS LAST: a calc with null energy ranks below a calc with populated energy under lowest_energy
collapse=first preserves plural records array; pagination.total reflects the pre-collapse count and pagination.post_collapse_total reflects the collapsed count
level_of_theory_id filters Calculation.lot_id directly
method/basis filter via LoT join on the calculation row
software filter via SoftwareRelease.software join
workflow_tool filter via WorkflowToolRelease.workflow_tool join
conformer block populated when Calculation.conformer_observation_id is set
conformer block is null when calculation has no observation
geometry IDs returned by default; XYZ not embedded
geometry primary_output_geometry_id resolves to role=final when present
geometry_validation status uses Phase 2.3 vocabulary (passed | warning | fail | not_present)
scf_stability status uses Phase 2.3 vocabulary (stable | unstable | stabilized | inconclusive | not_present)
calculation_quality=rejected excluded by default; include_rejected_quality=true opts in
min_review_status filters calculation review only (shallow per D7)
default excludes review_status=rejected and =deprecated
sort= rejected (422 client_sort_not_supported)
unknown include token rejected (422)
known but illegal include token (e.g. kinetics) rejected (422)
sort is deterministic across two identical calls
```

### API tests

```
GET /scientific/species-calculations/search returns 200 with envelope
POST /scientific/species-calculations/search returns 200 with body
POST rejects query-string keys (422 post_search_fields_must_be_in_body)
GET with smiles + calculation_type=sp + ranking=lowest_energy + collapse=first returns 1 record
404 on unknown species_entry_id
empty records on unmatched chemistry
OpenAPI exposes /api/v1/scientific/species-calculations/search with both GET and POST
endpoint tagged 'scientific'
```

### Client tests (Phase 7 implementation phase)

```
client.search_species_calculations(smiles=..., calculation_type='sp', ranking='lowest_energy', collapse='first')
client defaults to POST
client supports method='GET'
client serializes ranking and calculation_type
client surfaces 422 unsupported_ranking_for_calculation_type
client preserves auth header
```

---

## Open questions for the implementation phase

These do not block design acceptance — they're calls to make at
implementation time and to record in the implementation report.

1. **Default `calculation_quality` exclusion vs review's `rejected`.**
   Two independent "rejected" flags exist (review status + quality).
   Recommend documenting both inclusively in the response so a client
   can distinguish them; recommend defaulting to excluding both. Open
   question: do we want a single combined `include_rejected_anything`
   convenience flag, or keep the two flags separate? (Spec recommends
   keeping separate for clarity.)

2. **`torsion_fingerprint_json` size in default response.** v0 spec
   says return a compact summary; future implementations might want a
   formal "fingerprint summary" model (e.g. just the mode counts). For
   now, return `{"present": true | false}` and let `include=conformers`
   supply the full JSON.

3. **`provenance.supporting_calculation_ids` cost.** Walking
   `CalculationDependency` for every record could be a query hot spot.
   Recommend bulk-loading dependency rows in one query keyed by the
   set of returned calculation IDs (same pattern Phase 3 uses for
   review badges). If profiling shows this is hot, gate behind
   `include=provenance`.

4. **Service module naming.** The composed-search files in Phase 6 are
   `thermo_search.py` / `kinetics_search.py`. Recommend
   `species_calculations_search.py` for parallelism. The route file
   should be `species_calculations_search.py` under
   `backend/app/api/routes/scientific/`.

5. **`include=geometry` deferral mechanic.** The spec says accept
   `geometry` as a legal token but only return IDs + metadata. The
   implementation phase should decide whether sending `include=geometry`
   today returns a 200 with no extra payload (silent acceptance), or a
   422 saying "geometry embedding is deferred — use the geometry detail
   endpoint with the IDs in the response." Spec recommends silent
   acceptance + a doc note, so adding the embedding later is purely
   additive.

6. **Cross-endpoint thermo→calculation chaining.** A common workflow
   chain after Phase 6 will be: `search_thermo` → take
   `species_entry_id` → `search_species_calculations` to inspect the
   underlying SP/opt that the thermo was built from. This works
   naturally with the design; the implementation should add a worked
   example to the workflow guide once the endpoint ships.

7. **`neb` calculation type.** The prompt mentioned `neb` but the v0
   `CalculationType` enum only has `path_search` (which covers the
   GSM/NEB family per `PathSearchMethod`). v0 spec uses the real enum;
   if `neb` is added as a top-level calculation type later, the
   `lowest_energy` ranking rule must be revisited (NEB images carry
   per-image energies, not a single calculation-level energy).

---

## Non-goals (v0)

- This endpoint is **not** a full calculation-graph query language.
- It does **not** replace the `GET /calculations/{id}` detail endpoint.
- It does **not** expose arbitrary SQL.
- It does **not** decide which calculation a workflow should reuse.
- It does **not** define "highest level of theory."
- It does **not** embed full geometry payloads in v0 (IDs and metadata only).
- It does **not** add `neb` to the calculation type vocabulary (deferred to schema work).
- It does **not** filter conformers by `selection_kind` (informational only in v0).

---

## Implementation phase preview (not for this design)

When this is implemented, the work will mirror Phase 6:

| Layer | File | Purpose |
|---|---|---|
| Read schema | `backend/app/schemas/reads/scientific_species_calculations.py` | Request + response models |
| Service | `backend/app/services/scientific_read/species_calculations_search.py` | Compose existing helpers |
| Route | `backend/app/api/routes/scientific/species_calculations_search.py` | GET + POST wrappers |
| Tests | `backend/tests/services/scientific_read/test_search_species_calculations.py` | Service-layer behavior |
| Tests | `backend/tests/api/scientific/test_api_species_calculations_search.py` | API wiring + OpenAPI |
| Client | `clients/python/src/tckdb_client/client.py` | New `client.search_species_calculations(...)` method |
| Client tests | `clients/python/tests/test_scientific_search.py` (extend) | Request construction + error surfacing |
| Doc | `docs/guides/workflow_tool_scientific_reads.md` | New section: chemistry-first species calculation search |

Estimated scope is comparable to Phase 6 (~1500 lines of code + tests
total), since the calculation-row joins are denser than the
thermo/kinetics ones but the composition pattern is established.
