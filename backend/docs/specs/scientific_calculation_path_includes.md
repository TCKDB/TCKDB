# Scientific Calculation Path-Like Include Design

**Status:** spec, not implementation
**Companion to:** [scientific_calculation_reads.md](scientific_calculation_reads.md)
**Date:** 2026-05-14
**Scope:** Backend only. ARC and `tckdb-client` out of scope. No schema
changes proposed; future schema items are explicitly flagged.

---

## 1. Current state

The scientific calculation read/search surface ships with these include
tokens implemented:

```text
results
dependencies
artifacts
input_geometries
output_geometries
geometry_validation
scf_stability
parameters
constraints
review
internal_ids
```

Three include tokens remain deferred:

```text
scan
irc
path_search
```

Plus the `all` token, which is intentionally disabled while any heavy
include token is unimplemented.

The underlying ORM tables for the deferred includes already exist and
the legacy `/api/v1/calculations/{id}/...` family already exposes them
in full, including per-point arrays. The question this spec answers is:
how much of that data should the **scientific** read surface return,
and which surface (the include token vs. a specialized endpoint)
should carry the heavy point payloads.

---

## 2. Existing schema and route evidence

### 2.1 ORM tables

**Scan** (`app/db/models/calculation.py:488-769`)

| Table | Rows-per-calc | Cardinality |
|---|---|---|
| `calc_scan_result` | 1 | `dimension`, `is_relaxed`, `zero_energy_reference_hartree`, `note` |
| `calc_scan_coordinate` | `dimension` (1–N) | `coordinate_index`, `coordinate_kind`, `atom1..4_index`, `step_count`, `step_size`, `start_value`, `end_value`, `value_unit`, `resolution_degrees`, `symmetry_number` |
| `calc_scan_point` | many (≈ ∏ step_counts) | `point_index`, `electronic_energy_hartree`, `relative_energy_kj_mol`, `geometry_id`, `note` |
| `calc_scan_point_coordinate_value` | `point_count × dimension` | `point_index`, `coordinate_index`, `coordinate_value`, `value_unit` |

**IRC** (`app/db/models/calculation.py:772-865`)

| Table | Rows-per-calc | Cardinality |
|---|---|---|
| `calc_irc_result` | 1 | `direction`, `has_forward`, `has_reverse`, `ts_point_index`, `point_count`, `zero_energy_reference_hartree`, `note` |
| `calc_irc_point` | many (typically 20–200) | `point_index`, `direction` (per-point), `is_ts`, `reaction_coordinate`, `electronic_energy_hartree`, `relative_energy_kj_mol`, `max_gradient`, `rms_gradient`, `geometry_id`, `note` |

**Path-search** (`app/db/models/calculation.py:868-978`)

| Table | Rows-per-calc | Cardinality |
|---|---|---|
| `calc_path_search_result` | 1 | `method`, `is_double_ended`, `converged`, `n_points`, `selected_ts_point_index`, `climbing_image_index`, `source_endpoint_count`, `zero_energy_reference_hartree`, `note` |
| `calc_path_search_point` | many (typically 5–30 NEB images) | `point_index`, `electronic_energy_hartree`, `relative_energy_kj_mol`, `path_coordinate`, `max_force`, `rms_force`, `max_gradient`, `rms_gradient`, `is_ts_guess`, `is_climbing_image`, `geometry_id`, `note` |

### 2.2 Legacy routes that already expose this data

`backend/app/api/routes/calculations.py` (Tier-A/B, legacy-reads gate):

- `GET /api/v1/calculations/{id}/scan-result` — result row + nested coordinates + nested points + nested coordinate-values
- `GET /api/v1/calculations/{id}/scan-coordinates` — flat coordinates list
- `GET /api/v1/calculations/{id}/scan-points` — flat points list, each with coordinate-values inlined
- `GET /api/v1/calculations/{id}/irc-result` — result + nested points
- `GET /api/v1/calculations/{id}/irc-points` — flat points list
- `GET /api/v1/calculations/{id}/path-search-result` — result + nested points
- `GET /api/v1/calculations/{id}/path-search-points` — flat points list

These routes return integer ids, no public refs, no review badges, no
default-trust filtering. They are *internal* surfaces by today's
convention.

### 2.3 What `include=results` already returns

The implemented `include=results` already projects a per-type result
summary for `scan`, `irc`, and `path_search` calculation types via
`CalculationScanResultSummary`, `CalculationIRCResultSummary`,
`CalculationPathSearchResultSummary` in
[scientific_calculation.py](../../app/schemas/reads/scientific_calculation.py).
Those summaries cover the *result-row* fields only — they do **not**
contain coordinates, points, or any per-point arrays.

---

## 3. Data-size risk

Search endpoints can return up to `limit=200` records per page. If
`include=scan` returned every scan point, a single page could trivially
return:

- 200 calcs × ~1000 scan points × ~10 columns = 2M JSON values, plus
  per-point coordinate-values rows.
- IRC: 200 × ~100 points × 9 columns = 180k JSON values.
- Path-search: typically smaller (5–30 NEB images per calc), but a
  bulk page is still in the tens of thousands of rows.

This is a default-search-endpoint denial-of-service vector waiting to
happen. The detail endpoint is more bounded (one calc per request),
but a single huge scan can still produce a multi-MB response — a poor
default for a public surface.

The implemented `include=results`/`dependencies`/`parameters`/`constraints`
includes are bounded because the underlying tables are bounded per
calculation: a calc has one result row, a small dependency neighborhood,
≤ a few dozen parameters, ≤ a few dozen constraints. Scan/IRC/path-search
are different in kind because they carry *trajectories*.

---

## 4. Recommended contract

**Summary-first for the include tokens; specialized endpoints for full
trajectories.**

The include tokens `scan`, `irc`, and `path_search` should return
**summary-only** projections — bounded, cheap, safe to emit on a
search page. Full trajectory data (per-point arrays, coordinate
values, per-image gradients) lives behind dedicated endpoints under
the same scientific surface.

This mirrors the precedent set by `include=geometry_validation` /
`include=scf_stability` (singleton summaries; no atom-mapping JSON
inlined) and by `include=artifacts` (metadata only; no body bytes
or pre-signed URLs). It also matches the `include=results` shape
already implemented for the per-type result summaries.

The spec does **not** propose extending the existing `include=results`
to carry trajectories. `include=results` returns the *primary* result
row for the calc type (chosen in
`_PRIMARY_RESULT_TABLE`); `include=scan`/`include=irc`/`include=path_search`
are independent tokens that can be requested even on calcs whose primary
type is something else (e.g., a `freq` calc that nevertheless has a
linked scan calc — though in practice the scan-result row is keyed to
its own calculation row).

---

## 5. `include=scan` summary

Schema (additions to `app/schemas/reads/scientific_calculation.py`):

```python
class CalculationScanCoordinateBriefSummary(BaseModel):
    """One scan-coordinate row, projection for the include summary.

    Carries the kind + atom indices + numeric envelope. Does NOT carry
    per-point coordinate values — those live in the specialized endpoint.
    """
    coordinate_index: int
    coordinate_kind: ScanCoordinateKind
    atom_indices: list[int]                # convenience, like constraints
    step_count: int | None = None
    step_size: float | None = None
    start_value: float | None = None
    end_value: float | None = None
    value_unit: CoordinateUnit | None = None
    resolution_degrees: int | None = None
    symmetry_number: int | None = None


class CalculationScanIncludeSummary(BaseModel):
    """``include=scan`` summary projection (NOT a per-point array).

    Aggregate counts come from cheap COUNT queries, not from
    materializing the point set. ``min_energy_hartree`` /
    ``max_energy_hartree`` come from a single MIN/MAX SQL query on
    ``calc_scan_point.electronic_energy_hartree`` and are useful for
    quick "did the scan find a minimum?" checks; if profiling shows
    they cost more than a row count, they can be moved behind a
    second include token.
    """
    dimension: int
    is_relaxed: bool | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    coordinate_count: int
    point_count: int
    coordinates: list[CalculationScanCoordinateBriefSummary]
    min_energy_hartree: float | None = None
    max_energy_hartree: float | None = None
```

`ScientificCalculationRecord.scan: CalculationScanIncludeSummary | None = None`.

When not requested → omitted. When requested + no scan-result row →
`null` (singleton, like `geometry_validation` / `scf_stability` —
*not* `[]`, because there is at most one scan result per calc per
the schema PK).

---

## 6. `include=irc` summary

```python
class CalculationIRCIncludeSummary(BaseModel):
    """``include=irc`` summary projection. No per-point arrays."""
    direction: IRCDirection
    has_forward: bool
    has_reverse: bool
    ts_point_index: int | None = None
    point_count: int | None = None              # from result row
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    # Cheap aggregates from calc_irc_point:
    forward_point_count: int | None = None
    reverse_point_count: int | None = None
    has_ts_point: bool                          # true iff any point has is_ts=True
```

Forward / reverse counts come from a `GROUP BY direction` aggregate on
`calc_irc_point` — one round trip per calc. `has_ts_point` lets the
caller know whether the IRC actually resolved a TS without fetching
the point list.

`ScientificCalculationRecord.irc: CalculationIRCIncludeSummary | None = None`.

---

## 7. `include=path_search` summary

```python
class CalculationPathSearchIncludeSummary(BaseModel):
    """``include=path_search`` summary projection. No per-point arrays."""
    method: PathSearchMethod
    is_double_ended: bool | None = None
    converged: bool | None = None
    n_points: int | None = None                 # from result row
    selected_ts_point_index: int | None = None
    climbing_image_index: int | None = None
    source_endpoint_count: int | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    # Cheap aggregates from calc_path_search_point:
    point_count: int | None = None              # actual COUNT(*)
    has_ts_guess_point: bool
    has_climbing_image_point: bool
```

`n_points` (declared) and `point_count` (actual) are deliberately both
present — they should match in healthy data, and a divergence is a
useful curator signal.

`ScientificCalculationRecord.path_search: CalculationPathSearchIncludeSummary | None = None`.

---

## 8. Specialized full-data endpoints

**Status:** `/scan` and `/irc` implemented; `/path-search` pending.

Three public endpoints in this family:

```http
GET /api/v1/scientific/calculations/{calculation_ref_or_id}/scan         # implemented
GET /api/v1/scientific/calculations/{calculation_ref_or_id}/irc          # implemented
GET /api/v1/scientific/calculations/{calculation_ref_or_id}/path-search  # pending
```

The shipped scan endpoint lives in
[`backend/app/api/routes/scientific/calculation_paths.py`](../../app/api/routes/scientific/calculation_paths.py)
with its service in
[`calculation_paths.py`](../../app/services/scientific_read/calculation_paths.py)
and schemas in
[`scientific_calculation_paths.py`](../../app/schemas/reads/scientific_calculation_paths.py).
A sibling-module split was chosen over extending
`calculations.py` to keep detail/search/path-data concerns separated
as the file footprint grows; future `/irc` and `/path-search`
handlers will land alongside the scan route in the same module.

Each returns the full per-point payload for one calculation:

```python
class ScientificCalculationScanResponse(BaseModel):
    request: RequestEcho                     # echoes include flags below
    calculation_ref: str
    calculation_id: int | None = None        # policy-gated
    summary: CalculationScanIncludeSummary   # same shape as the include
    coordinates: list[CalculationScanCoordinateFullSummary]
    points: list[CalculationScanPointSummary]
    pagination: Pagination | None = None     # only when limit/offset supplied
```

`CalculationScanPointSummary` carries `point_index`, energy fields,
optional `geometry_ref` (loaded by default; integer `geometry_id` is
policy-gated), `note`, plus the per-point coordinate-values list:

```python
class ScanPointCoordinateValueSummary(BaseModel):
    coordinate_index: int
    coordinate_value: float
    value_unit: CoordinateUnit | None = None


class CalculationScanPointSummary(BaseModel):
    point_index: int
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    geometry_id: int | None = None
    geometry_ref: str | None = None
    note: str | None = None
    coordinate_values: list[ScanPointCoordinateValueSummary]
```

IRC and path-search endpoints follow the same shape with their own
point summary types.

### 8.1 Bounded by default

Each specialized endpoint enforces the same hosted abuse-control caps
the rest of `/scientific/*` already uses:

- `limit` / `offset` on the `points` array. Default `limit=50`,
  cap = `min(MAX_LIMIT, settings.public_max_limit)` (= 200 today).
- Optional `include_geometries=true` flag (default `false`) to inline
  a lightweight `geometry_link` block (`geometry_ref`, `natoms`,
  `geom_hash`) per point. Default behavior: `geometry_ref` only —
  clients who want coordinates fetch them via
  `/scientific/geometries/{geometry_ref}`. (Decided as a single
  boolean flag for v0; if a future caller wants slot-specific
  control, the contract can grow per-slot include tokens without
  breaking this default.)
- Hard 422 on `limit` or `offset` overrun via `validate_pagination`,
  same as the scientific search endpoints.
- Client-supplied `sort=` rejected with 422
  `client_sort_not_supported`; per-resource ordering is locked
  (coordinates by `coordinate_index ASC`, points by
  `point_index ASC`, point coordinate values by `coordinate_index ASC`).

### 8.2 Path handle

`{calculation_ref_or_id}` resolves via the existing
`resolve_calculation_handle()`. Wrong-prefix → 422 `handle_type_mismatch`;
malformed → 422 `invalid_handle`; missing → 404. Same rules as the
detail endpoint.

### 8.3 404 vs 200-with-empty

- Calculation does not exist: 404 (handle resolver).
- Calculation exists but has **no scan/IRC/path-search result row**:
  404 with stable code `<kind>_result_not_found` (matches the legacy
  `/calculations/{id}/scan-result` etc. behavior; the resource genuinely
  doesn't exist for this calc).
- Calculation has the result row but zero linked points: 200 with
  `scan` summary populated and `points = []`.

(`/scan` and `/irc` both ship with this exact behavior —
`scan_result_not_found` / `irc_result_not_found` on calcs without
the matching result row, 200 with `points = []` on result rows with
no point children. `/path-search` will follow the same convention
when it ships.)

### 8.4 Why these are separate endpoints, not include flags

If we let `include=scan_full` exist alongside `include=scan`, the
scientific search endpoint would let a single request fan out to
hundreds of multi-thousand-point payloads. Splitting full point data
into a per-calc URL forces callers to make N detail-style requests if
they truly need every trajectory, and the abuse limiter can apply
per-calc bounds without the search query plan having to dynamically
estimate response size.

It also matches the `/scientific/geometries/{geometry_ref}` precedent
already in the codebase: heavy coordinate payloads live behind
per-resource detail endpoints, not behind search-include expansion.

### 8.5 Sorting

`points` are returned in `point_index ASC` (the natural acquisition
order for scans, the natural reaction-coordinate-progress order for
IRC/path-search). This is locked deterministic ordering; client `sort=`
is rejected with the standard `client_sort_not_supported` 422.

---

## 9. `include=all` policy

**Status: implemented.** `include=all` is "all summary-safe includes
only" — every public summary token returns its bounded shape, and
the token never expands to full point arrays or artifact body bytes.

Concrete contract:

- `all` resolves to every public include token in
  `_LEGAL_INCLUDE_TOKENS` minus `_INTERNAL_INCLUDE_TOKENS`
  (still excludes `internal_ids`, which always requires explicit
  opt-in via `include=all,internal_ids` and is additionally gated by
  `settings.allow_public_internal_ids`).
- `all` includes the summary projections for scan / IRC / path-search
  but **never** triggers the specialized full-data endpoints.
- Heavy point arrays remain accessible only via the dedicated
  `/scientific/calculations/{calculation_ref_or_id}/scan` /
  `/irc` / `/path-search` endpoints (and per-coordinate XYZ payloads
  via `/scientific/geometries/{geometry_ref}`).

The previous "rejecting `all` permanently would force every consumer
to enumerate the full include set themselves" rationale stands as
the design justification for why this contract was chosen over a
permanent ban — the hard ceiling on payload size is the per-include
bound, not the `all` token.

---

## 10. Implementation phases

Each phase is independently shippable. Phases 1–3 and 5 are now
complete; phase 4 (specialized full-data endpoints) is the remaining
work.

### Phase 1 — `include=scan` summary

1. Schema: `CalculationScanCoordinateBriefSummary`,
   `CalculationScanIncludeSummary`. Add
   `ScientificCalculationRecord.scan` field.
2. Service: `_build_scan_summary(session, calc_id)` — single SELECT for
   the result row, single SELECT for coordinates ordered by index, a
   `COUNT(*)` for `point_count`, optional `MIN/MAX(electronic_energy_hartree)`
   for the energy envelope.
3. Move `scan` out of `_NOT_IMPLEMENTED_INCLUDE_TOKENS`; add
   `scan → scan` to `_OMITTABLE_RECORD_KEYS`.
4. Tests: detail + search × {empty, populated, ordering, ID policy,
   combined-with-other-includes, canary-with-still-unimplemented}.

### Phase 2 — `include=irc` summary

Same shape as phase 1 with `_build_irc_summary`. Adds
`forward_point_count` / `reverse_point_count` aggregates via
`GROUP BY direction` on `calc_irc_point`.

### Phase 3 — `include=path_search` summary

Same shape as phase 1 with `_build_path_search_summary`. Surfaces
both the declared `n_points` and the actual `point_count` so a
divergence is visible to callers.

### Phase 4 — Specialized full-data endpoints

Three routes mounted under
`backend/app/api/routes/scientific/calculations.py` (or a sibling
module if the file gets too big):

```python
@router.get("/{calculation_ref_or_id}/scan")
@router.get("/{calculation_ref_or_id}/irc")
@router.get("/{calculation_ref_or_id}/path-search")
```

Each returns the matching `Scientific*Response` envelope. Pagination
uses the existing `validate_pagination`; `include=geometries` flag
opt-in for inlined geometry links.

### Phase 5 — Flip `include=all`

1. Drop `all` from the deferred-tokens list in
   `backend/docs/specs/scientific_calculation_reads.md`.
2. Reword the "Why `include=all` still 422s" rationale into a "What
   `include=all` resolves to" explainer (every public token; no
   internal_ids; never the specialized full-data endpoints).
3. Tests: `include=all` returns 200; populates every implemented
   summary section; never injects a `points` array on a search record;
   silently drops `internal_ids` per the existing policy.

### Phase 6 — Optional: search-side filters

Out of scope for this spec; recorded here so the future filter set
doesn't drift:

- `has_scan_result`, `has_irc_result`, `has_path_search_result` —
  cheap EXISTS filters, parallel to the existing `has_artifacts` etc.
- `irc_direction`, `path_search_method` — direct column filters.

These are search filters, not includes.

---

## 11. Test plan

### Per-summary-include phase

Mirror the existing test layout (scan / irc / path_search each get
their own block in both detail and search test files).

Detail tests:

```text
<kind> omitted when not requested
include=<kind> returns summary block
include=<kind> returns null when no result row exists       # singleton
include=<kind> exposes coordinate count + point count       # scan only
include=<kind> exposes forward/reverse point counts          # irc only
include=<kind> exposes declared vs actual point count        # path_search only
internal IDs hidden by default; restored under allow_internal_ids
ordering deterministic across two calls
combined with the other implemented includes
canary: include=<kind>,<UNIMPLEMENTED_CALC_INCLUDE_TOKEN> still 422
```

Search tests:

```text
include=<kind> on a matching search returns the summary block
GET/POST parity with include=<kind>
search internal-ID behavior for any *_id fields in the summary
```

### Specialized-endpoint phase

```text
detail by calculation_ref
detail by integer id when allow_public_internal_ids
404 on unknown calc
404 on calc with no <kind>-result row
200 with empty points list when result exists but no point rows
points sorted by point_index ASC
limit/offset bounded by validate_pagination
limit overrun returns 422
include=geometries returns geometry links per point
default response carries geometry_ref, never the inlined coordinates
internal-ID hiding for *_id keys
client sort= rejected with client_sort_not_supported
```

### `include=all` phase

```text
include=all returns 200
include=all populates every implemented summary include
include=all does not inject points or coordinate-values arrays
include=all silently drops internal_ids when policy disallows
include=all + internal_ids restores ids when policy allows
include=all,<unknown> still 422 unknown_include_token
```

---

## 12. Open questions

1. **Where do the specialized routes live?** Adding three more
   handlers to `backend/app/api/routes/scientific/calculations.py`
   pushes that file past 500 lines and starts to mix
   detail/search/path-data concerns. A sibling
   `calculations_paths.py` may be cleaner. Decide at implementation
   time.
2. **Should `include=geometries` on the specialized endpoints be one
   flag or three?** `include=input_geometry` / `include=output_geometry` /
   `include=points_geometries` could let a caller fetch only one slice.
   Spec assumes one boolean for v0; refine when a real consumer asks.
3. **Energy MIN/MAX in `include=scan` summary** — adds one MIN/MAX
   aggregate per scan-result. Cheap on indexed `point_index`; costlier
   on un-indexed `electronic_energy_hartree`. If profiling on a large
   instance shows the aggregate is expensive, drop it from the include
   and keep it in the specialized endpoint only.
4. **Forward / reverse counts in `include=irc` summary** — same
   profiling concern. The `GROUP BY direction` aggregate is cheap with
   an index on `(calculation_id, direction)`; without one, a future
   schema PR can add it.
5. **Should the specialized endpoints accept the same `min_review_status`
   / `include_rejected` filtering as the search surface?** The detail
   endpoint already takes a generous "show me this exact record"
   posture (no review filtering) — so the path endpoints should match.
   Documented now so the implementer doesn't accidentally inherit
   search-style filtering from copy-paste.
6. **Schema follow-up: a per-calc point-count column.** Today
   `point_count` on `calc_scan_result` doesn't exist (only
   `calc_irc_result.point_count` and `calc_path_search_result.n_points`
   do). Adding it lets the include avoid the COUNT(*) round-trip.
   Out of scope here; flagged for a future schema PR.

---

## Insight

★ Insight ─────────────────────────────────────
- The choice that matters here is the same one the spec made for
  `include=artifacts`: metadata-summary in the include, full payload
  behind a per-resource detail endpoint. Trajectories are the same
  shape as artifact bodies — bounded payloads are wire-safe; unbounded
  ones aren't.
- The `point_count` field already exists on `calc_irc_result` and
  `calc_path_search_result.n_points`, but not on `calc_scan_result` —
  scan summaries either need a `COUNT(*)` round-trip or a future
  schema column. The COUNT path is cheap enough for v0; the schema
  column is the right long-term answer once the scientific surface is
  the primary read path.
─────────────────────────────────────────────────
