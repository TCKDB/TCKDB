# Scientific Calculation Read & Search Endpoints

**Status:** spec, not implementation
**Audit basis:** [read_query_api_audit.md](read_query_api_audit.md)
**Date:** 2026-05-14
**Scope:** Backend only. ARC and `tckdb-client` are out of scope. No schema
or ingestion changes; no new ORM tables.

---

## 1. Why

The audit established two facts:

1. The legacy [`/calculations/...`](../../app/api/routes/calculations.py)
   family already exposes calculation children (results, dependencies,
   parameters, constraints, artifacts, geometries, geometry-validation,
   scf-stability, scan/IRC/path-search) but does so as table-style reads
   without public handles, review badges, or default trust filtering.
2. The only scientific surface that filters across calculations today is
   [`/scientific/species-calculations/search`](../../app/api/routes/scientific/species_calculations_search.py),
   and it requires a species identifier — there is no way to ask
   "find opt calcs at ωB97X-D/def2-TZVP that converged" or
   "find calcs for transition state X."

This spec defines the next scientific surface that closes both gaps:

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/scientific/calculations/{calculation_ref_or_id}` | Calculation-as-evidence read with conditional includes. |
| `GET /api/v1/scientific/calculations/search` | Multi-axis chemistry/method/provenance search. |
| `POST /api/v1/scientific/calculations/search` | Body-variant of search for complex filter sets. |

Same contract conventions as every other `/scientific/*` endpoint:
public-handle envelope, review/trust badges, deterministic ordering,
bounded pagination, validated includes, default-hidden rejected/deprecated.

---

## 2. Naming and route conventions

The audit shows the established pattern:
[`scientific_router`](../../app/api/routes/scientific/__init__.py) mounts
sub-routers under `/api/v1/scientific`. Following the existing
`/scientific/geometries/{geometry_handle}` precedent, this spec uses
**`{calculation_ref_or_id}`** as the canonical path-parameter name. Each
sub-router also exposes its search at `/search`. Final paths:

- `GET /api/v1/scientific/calculations/{calculation_ref_or_id}`
- `GET /api/v1/scientific/calculations/search`
- `POST /api/v1/scientific/calculations/search`

New module: `backend/app/api/routes/scientific/calculations.py`.
New service module: `backend/app/services/scientific_read/calculations.py`
plus `calculations_search.py` (mirrors the existing thermo / kinetics
split between detail and search).
New schemas: `backend/app/schemas/reads/scientific_calculation.py`,
`backend/app/schemas/reads/scientific_calculation_search.py`.

---

## 3. Design principles (recap, binding)

1. Calculations are **provenance/evidence records**, not table dumps.
2. Public handles (`calculation_ref`, `species_entry_ref`,
   `transition_state_entry_ref`, `level_of_theory_ref`,
   `software_release_ref`, `workflow_tool_release_ref`,
   `geometry_ref`, `submission_ref`) are present in every record.
3. Honor `settings.allow_public_internal_ids` via the existing
   `apply_internal_ids_visibility()` helper. No new gating logic.
4. Every record carries a `RecordReviewBadge`; every response carries a
   `ReviewStatusSummary`. Rejected and deprecated are excluded by default.
5. Sorting is locked and deterministic. Client `sort=` is rejected (422
   `client_sort_not_supported`), exactly as the existing scientific
   endpoints do.
6. Pagination uses the shared `Pagination` envelope (`offset`, `limit`,
   `returned`, `total`) bounded by `settings.public_max_offset` and
   `settings.public_max_limit`.
7. Include flags are validated centrally; unknown tokens → 422
   `unknown_include_token`. The `all` token expands deterministically and
   never includes `internal_ids`.
8. **No artifact body bytes** are exposed here. Only artifact metadata.
9. Workflow-tool agnostic. No ARC-only concepts on the surface.

---

## 4. Detail endpoint: `GET /scientific/calculations/{calculation_ref_or_id}`

### 4.1 Purpose

Return one calculation as a scientific/provenance record: identity,
owner, level of theory, software release, workflow-tool release, review
badge, and an `available_sections` map describing which heavy children
exist. Heavy data is opt-in via `include=`.

### 4.2 Path parameter

`calculation_ref_or_id` (string, length 1–64).

Resolution follows the existing handle contract
([handles.py](../../app/services/scientific_read/handles.py)):

- Pure positive integer (`^[1-9]\d*$`) → SELECT by id.
  - 404 if missing (id logged server-side, generic detail in response).
- Public ref with the `calc_` prefix → SELECT by `public_ref`.
  - 404 if missing.
- Ref with wrong prefix → 422 `handle_type_mismatch`.
- Malformed string → 422 `invalid_handle`.

When `allow_public_internal_ids=False`, the integer-id form is still
accepted as a path handle (the audit confirms this is how scientific
detail endpoints already work for geometries/reaction-entries/species-entries),
but the response body strips integer ids exactly like other surfaces.

### 4.3 Default response shape

```python
class ScientificCalculationDetail(BaseModel):
    request: RequestEcho                    # filter, sort, collapse, include echo
    review_summary: ReviewStatusSummary     # always = 1×status of this record
    record: ScientificCalculationRecord     # the calculation record proper
```

`ScientificCalculationRecord` always includes:

```python
class ScientificCalculationRecord(BaseModel):
    calculation: CalculationCoreBlock          # calc_ref, type, quality, created_at, review
    owner: CalculationOwnerSummary             # species-entry or TS-entry summary
    level_of_theory: LevelOfTheorySummary | None
    software_release: SoftwareReleaseSummary | None
    workflow_tool_release: WorkflowToolReleaseSummary | None
    literature: LiteratureSummary | None       # via species/TS provenance, optional
    provenance: CalculationEvidenceSummary     # validation_status, scf_stability_status, converged
    available_sections: AvailableCalculationSections  # what include= would return
```

`CalculationCoreBlock` is reused from
[scientific_species_calculations.py:157-169](../../app/schemas/reads/scientific_species_calculations.py).

### 4.4 Allowed `include` flags

Centralized in `app/services/scientific_read/calculations.py`:

```python
_LEGAL_INCLUDE_TOKENS = {
    "results",
    "dependencies",
    "parameters",
    "constraints",
    "artifacts",
    "input_geometries",
    "output_geometries",
    "geometry_validation",
    "scf_stability",
    "scan",
    "irc",
    "path_search",
    "review",          # detailed review history (timestamped events)
    "internal_ids",    # opt-in only, gated by settings
    "all",
}
```

Validation goes through the existing `validate_includes()` helper. `all`
expands to every token **except** `internal_ids`. Unknown tokens → 422
`unknown_include_token` listing the legal set.

### 4.5 What each include adds

| Include | Adds field(s) | Backed by |
|---|---|---|
| `results` | `results: CalculationResultSummary` (one of `sp\|opt\|freq\|scan\|irc\|path_search`) | `calc_*_result` tables |
| `dependencies` | `dependencies: list[CalculationDependencySummary]` (parent + child links with role + ref) | `calculation_dependency` |
| `parameters` | `parameters: list[CalculationParameterSummary]` (raw + canonical) | `calculation_parameter` |
| `constraints` | `constraints: list[CalculationConstraintSummary]` | `calculation_constraint` |
| `artifacts` | `artifacts: list[CalculationArtifactSummary]` (metadata only — no body bytes) | `calculation_artifact` |
| `input_geometries` | `input_geometries: list[CalculationGeometryLinkSummary]` | `calculation_input_geometry` |
| `output_geometries` | `output_geometries: list[CalculationGeometryLinkSummary]` | `calculation_output_geometry` |
| `geometry_validation` | `geometry_validation: list[CalculationGeometryValidationSummary]` | `calc_geometry_validation` |
| `scf_stability` | `scf_stability: list[CalculationSCFStabilitySummary]` | `calc_scf_stability` |
| `scan` | `scan: CalculationScanSummary \| None` | `calc_scan_result` (+ children) |
| `irc` | `irc: CalculationIRCSummary \| None` | `calc_irc_result` (+ points) |
| `path_search` | `path_search: CalculationPathSearchSummary \| None` | `calc_path_search_result` (+ points) |
| `review` | `review_history: list[ReviewRecordEntry]` | `record_review` rows for this calc |
| `internal_ids` | restores integer `*_id` fields when policy permits | — |

When a section is **not** in the include set, the corresponding field is
omitted entirely from the response (FastAPI `exclude_none=True`
serialization with `model_config(extra="allow")` to keep the JSON shape
stable across opt-in expansions).

When a section **is** in the include set but the underlying table has no
rows for this calculation, the field is present with the empty value
(`[]` for arrays, `null` for nullable singletons).

### 4.6 Review/trust behavior

- The `review` badge inside `CalculationCoreBlock` is **always present**.
- `review_summary` carries `1` in the bucket matching the record's status
  and `0` elsewhere (per the existing detail-endpoint convention).
- Detail endpoints do **not** filter by review status — the caller is
  asking for one specific record and is entitled to see its current
  state. (Same as `/scientific/geometries/{handle}`.)
- An `include_review=full` style flag is **not** added here; detail-grain
  history is exposed via the dedicated `include=review` token instead.

### 4.7 Internal ID behavior

Implemented via existing `apply_internal_ids_visibility()`:

| `allow_public_internal_ids` | `include=internal_ids` requested | Response |
|---|---|---|
| `True` | yes | Integer ids present alongside refs. `request.include` echoes `internal_ids`. |
| `True` | no | Integer ids stripped (default behavior). Refs always visible. |
| `False` | yes | Integer ids stripped silently. `request.include` shows the token was dropped. |
| `False` | no | Integer ids stripped (default). |

Bare-integer arrays (`input_geometry_ids`, `output_geometry_ids`,
`supporting_calculation_ids`) inherit the same gating; their object-array
siblings (`input_geometries`, `output_geometries`,
`supporting_calculations`) carry refs and remain visible.

### 4.8 Errors

| Condition | Code | Detail |
|---|---|---|
| Integer path handle not found | 404 | generic `not_found` (id logged server-side) |
| Ref path handle not found | 404 | generic `not_found` |
| Wrong prefix on ref | 422 | `handle_type_mismatch` |
| Malformed handle | 422 | `invalid_handle` |
| Unknown include token | 422 | `unknown_include_token` (list of legal tokens) |
| Client supplied `sort=` | 422 | `client_sort_not_supported` |

---

## 5. Search endpoints

### 5.1 Purpose

Multi-axis discovery of calculations as scientific records. Closes the
"find me calcs by method/basis/quality/validation/parameter/owner" gap
the legacy `/calculations` list cannot answer (it only paginates).

### 5.2 GET vs POST

- `GET` for short query-string filters (matches the legacy ergonomics
  used by `/scientific/thermo/search`, `/scientific/kinetics/search`).
- `POST` for structured filter sets that exceed sensible URL length
  (e.g., long `reactants`/`products` lists). Same body shape as
  `CalculationsSearchRequest`. Query-string params on the POST form are
  rejected with 422 `post_search_fields_must_be_in_body` (matches the
  reactions/thermo/kinetics search policy).

### 5.3 Filter groups

Filter validation lives in
`app/schemas/reads/scientific_calculation_search.py`. Lengths use the
shared `_field_bounds.py` constants.

#### 5.3.1 Owner filters (required: at least one of these or one chemistry filter)

| Field | Type | Notes |
|---|---|---|
| `species_ref` | str (max 64) | reconciled via `reconcile_species_pair` |
| `species_entry_ref` | str (max 64) | reconciled via `reconcile_species_entry_pair` |
| `transition_state_ref` | str (max 64) | new pair: `reconcile_transition_state_pair` |
| `transition_state_entry_ref` | str (max 64) | new pair: `reconcile_transition_state_entry_pair` |
| `reaction_ref` | str (max 64) | resolves to TS-owned calcs via `reaction → reaction_entry → transition_state_entry` |
| `reaction_entry_ref` | str (max 64) | same path, narrower |
| `owner_kind` | enum `species_entry \| transition_state_entry` | post-filter on resolved owner |

Integer siblings (`species_id`, `species_entry_id`, etc.) are accepted
when `allow_public_internal_ids=True` for parity with the existing
species-calculations search.

Reconciliation pairs that already exist are reused; `transition_state_*`
pairs need new helpers in
[handles.py](../../app/services/scientific_read/handles.py) — same
pattern as the existing pairs.

#### 5.3.2 Chemistry filters (MVP-optional; require species join)

| Field | Type | MVP? |
|---|---|---|
| `smiles` | str | yes |
| `inchi_key` | str | yes |
| `formula` | str | Phase 2 — needs join |
| `reactants` | list[str] (max 32, each max 2048) | **Phase 2** — needs reaction join, mirror of reactions/search |
| `products` | list[str] (max 32, each max 2048) | **Phase 2** |
| `reaction_family` | str | Phase 2 |

Chemistry filters AND-combine with owner filters when both are supplied;
inconsistent combinations return an empty result (not 422), matching the
species-calculations search precedent.

#### 5.3.3 Calculation filters

| Field | Type | Notes |
|---|---|---|
| `calculation_type` | `CalculationType` (`opt\|freq\|sp\|irc\|scan\|path_search\|conf`) | direct column filter |
| `quality` | `CalculationQuality` (`raw\|curated\|rejected`) | rejected only with `include_rejected_quality=true` |
| `has_result` | bool | true → exists row in matching `calc_*_result` for the type |
| `has_artifacts` | bool | EXISTS subquery on `calculation_artifact` |
| `has_parameters` | bool | EXISTS on `calculation_parameter` |
| `has_constraints` | bool | EXISTS on `calculation_constraint` |
| `has_input_geometry` | bool | EXISTS on `calculation_input_geometry` |
| `has_output_geometry` | bool | EXISTS on `calculation_output_geometry` |
| `created_before` | datetime | `created_at <` |
| `created_after` | datetime | `created_at >=` |

#### 5.3.4 Level-of-theory filters

| Field | Type | Notes |
|---|---|---|
| `method` | str (max `MAX_METHOD_LENGTH`) | LoT join, exact match |
| `basis` | str (max `MAX_BASIS_LENGTH`) | LoT join, exact match |
| `aux_basis` | str | Phase 2 — needs LoT column |
| `cabs_basis` | str | Phase 2 — needs LoT column |
| `dispersion` | str | LoT join, exact match |
| `solvent` | str | LoT join |
| `solvent_model` | str | Phase 2 — needs LoT column |
| `lot_ref` | str (max 64) | reconciled via `reconcile_level_of_theory_pair` |
| `lot_hash` | str | direct LoT column |

`aux_basis`, `cabs_basis`, `solvent_model` are deferred unless the LoT
table already exposes those columns; spec leaves them as named knobs so
adding them later is not a breaking change.

#### 5.3.5 Software / workflow filters

| Field | Type | Notes |
|---|---|---|
| `software` | str | software join, exact match |
| `software_version` | str | software_release join |
| `software_release_ref` | str (max 64) | reconciled |
| `workflow_tool` | str | workflow_tool join |
| `workflow_tool_version` | str | workflow_tool_release join |
| `workflow_tool_release_ref` | str (max 64) | reconciled |

#### 5.3.6 Validation / provenance filters

| Field | Type | Notes |
|---|---|---|
| `geometry_validation_status` | `Literal["passed","warning","fail","not_present"]` | from `calc_geometry_validation`; `not_present` means no row exists |
| `scf_stability_status` | `Literal["stable","unstable","stabilized","inconclusive","not_present"]` | from `calc_scf_stability`; `not_present` means no row |
| `dependency_role` | `CalculationDependencyRole` | EXISTS on `calculation_dependency` with the given role |
| `parent_calculation_ref` | str (max 64) | restricts to calcs whose dependency points to this parent |
| `child_calculation_ref` | str (max 64) | restricts to calcs whose dependency points to this child |
| `artifact_kind` | `ArtifactKind` (`input\|output_log\|checkpoint\|formatted_checkpoint\|ancillary`) | EXISTS on `calculation_artifact` |
| `parameter_key` | str | matches `calculation_parameter.raw_key` (case-sensitive) |
| `parameter_value` | str | requires `parameter_key`; matches `raw_value` exactly |
| `canonical_parameter_key` | str | matches `calculation_parameter.canonical_key` |
| `canonical_parameter_value` | str | requires `canonical_parameter_key`; matches `canonical_value` |

Parameter-pair semantics: `parameter_value` may only appear with
`parameter_key`; same for the canonical form. Bare values without keys →
422 `parameter_value_requires_key`.

#### 5.3.7 Review filters

Identical to every other scientific endpoint:

| Field | Type | Default | Notes |
|---|---|---|---|
| `min_review_status` | `RecordReviewStatus \| None` | `None` | rank-based (approved=0 best) |
| `include_rejected` | bool | `False` | review-status-rejected |
| `include_deprecated` | bool | `False` | review-status-deprecated |
| `include_rejected_quality` | bool | `False` | orthogonal: opts in `CalculationQuality.rejected` |

Default-visible review statuses: `{approved, under_review, not_reviewed}`,
via `default_visible_statuses()`.

#### 5.3.8 Sort / collapse / include / pagination

| Field | Type | Default | Notes |
|---|---|---|---|
| `sort` | str | `None` | non-`None` → 422 `client_sort_not_supported` |
| `ranking` | enum | `default` | see §5.5 |
| `collapse` | `CollapseMode` | `all` | `first` returns ≤1 record |
| `include` | list[str] | `[]` | same legal set as detail endpoint |
| `offset` | int | `0` | bounded by `settings.public_max_offset` |
| `limit` | int | `50` | 1 ≤ limit ≤ `min(MAX_LIMIT, settings.public_max_limit)` |

### 5.4 At-least-one-filter rule

A request with no owner filter, no chemistry filter, and no LoT/software
filter is rejected with 422 `missing_filter`. This mirrors the
"identifier required" rule on species-search and prevents callers from
issuing unbounded sweeps. The detail endpoint is the path for "give me
everything about this one calc."

### 5.5 Ranking / default ordering

Locked, deterministic. No client `sort=`. The `ranking` enum is
mutually exclusive with the default ordering (same pattern as
species-calculations search).

```python
class CalculationsRanking(str, Enum):
    default = "default"        # the composite below
    latest = "latest"          # created_at DESC, id DESC
    earliest = "earliest"      # created_at ASC, id ASC
    review_rank = "review_rank"  # review_rank ASC, created_at DESC, id DESC
```

Default composite (in this order):

1. `review_rank ASC` — `approved (0) < under_review (1) < not_reviewed (2)`
   (rejected=4 / deprecated=3 are excluded by default; if the caller
   opts them in they sort last).
2. `quality_rank ASC` — `curated (0) < raw (1) < rejected (2)`
   (rejected only present with `include_rejected_quality=true`).
3. `evidence_completeness DESC` — same `EvidenceCompletenessBreakdown`
   shape used by thermo/kinetics search, with this calculation-grain
   checklist:
   - `has_primary_result` (matching `calc_*_result` row exists)
   - `has_level_of_theory`
   - `has_software_release`
   - `has_output_geometry` (where applicable to the calc type)
   - `has_geometry_validation_evidence`
   - `has_scf_stability_evidence`
   - `has_artifacts_metadata`
   - `has_dependency_link`
4. `created_at DESC`
5. `calculation_id DESC` (deterministic tie-breaker; even when the
   integer id is hidden in the response body it is still used for sort).

`evidence_completeness` is exposed in each record (not just used for
sort) so callers can read why a record outranked another.

### 5.6 Response shape

```python
class CalculationsSearchResponse(BaseModel):
    request: RequestEcho                           # filter / ranking / sort / collapse / include
    review_summary: ReviewStatusSummary            # pre-collapse counts across the candidate set
    records: list[ScientificCalculationRecord]     # same per-record shape as the detail endpoint
    pagination: Pagination                         # offset / limit / returned / total
```

The per-record shape is **the same `ScientificCalculationRecord` defined
in §4.3**. Search results may carry only the always-present fields when
no `include` is requested; `include=` adds the same conditional sections
as in detail. This means a caller can use the same client-side parsing
code for both endpoints — a property the existing scientific surface
already enforces.

`review_summary` counts cover the candidate set **before** pagination
and **before** collapse (matches existing endpoints).

`request.include` echoes the **resolved** include set (post-validation,
post-`internal_ids` policy stripping).

### 5.7 Errors

All previously-defined codes plus:

| Condition | Code |
|---|---|
| No filter supplied | 422 `missing_filter` |
| `parameter_value` without `parameter_key` | 422 `parameter_value_requires_key` |
| `canonical_parameter_value` without `canonical_parameter_key` | 422 `canonical_parameter_value_requires_key` |
| Owner ref + id pair disagree | 422 `<owner>_handle_conflict` |
| `lot_ref` + `level_of_theory_id` disagree | 422 `level_of_theory_handle_conflict` |
| Body field on POST that should be query-only | (none — body is authoritative) |
| Query-string field on POST | 422 `post_search_fields_must_be_in_body` |
| Unknown include token | 422 `unknown_include_token` |
| `sort=` supplied | 422 `client_sort_not_supported` |

---

## 6. Response fragment definitions

All fragments live in
`app/schemas/reads/scientific_calculation.py` (detail) and
`app/schemas/reads/scientific_calculation_search.py` (search request +
response envelope). Reused fragments come from `scientific_common.py`
and `scientific_species_calculations.py`.

### 6.1 New fragments

```python
class CalculationOwnerSummary(BaseModel):
    """The scientific owner of a calculation (species-entry or TS-entry).

    Exactly one of `species_entry` / `transition_state_entry` is non-null.
    `kind` mirrors that for cheap client-side branching.
    """
    kind: Literal["species_entry", "transition_state_entry"]
    species_entry: SpeciesCalculationsSpeciesContext | None = None
    transition_state_entry: TransitionStateEntrySummary | None = None


class TransitionStateEntrySummary(BaseModel):
    """Lightweight TS-entry context, parallel to
    SpeciesCalculationsSpeciesContext."""
    transition_state_id: int
    transition_state_ref: str
    transition_state_entry_id: int
    transition_state_entry_ref: str
    label: str | None = None
    reaction_entry_id: int | None = None
    reaction_entry_ref: str | None = None


class AvailableCalculationSections(BaseModel):
    """Boolean map describing what `include=` would expand for this calc.

    Cheap to compute (one EXISTS-style join per section, all aggregable in
    a single query) and useful so a client can avoid a second roundtrip
    for empty sections."""
    has_results: bool
    has_dependencies: bool
    has_parameters: bool
    has_constraints: bool
    has_artifacts: bool
    has_input_geometries: bool
    has_output_geometries: bool
    has_geometry_validation: bool
    has_scf_stability: bool
    has_scan: bool
    has_irc: bool
    has_path_search: bool


class CalculationResultSummary(BaseModel):
    """Single-result projection — exactly one of the per-type blocks set.

    Each per-type block is a thin wrapper around the existing
    CalculationSPResultRead/OptResultRead/FreqResultRead etc., reduced to
    only the fields that are publicly meaningful for evidence purposes
    (energy, converged, basis-of-truth flags). Heavy arrays (frequency
    modes, scan/IRC point arrays) are NOT exposed here — they are
    available via `include=scan|irc|path_search` instead."""
    sp: CalculationResultSPSummary | None = None
    opt: CalculationResultOptSummary | None = None
    freq: CalculationResultFreqSummary | None = None


class CalculationDependencySummary(BaseModel):
    """One edge in the calculation dependency graph (directional)."""
    role: CalculationDependencyRole
    direction: Literal["parent", "child"]
    parent_calculation_id: int
    parent_calculation_ref: str
    child_calculation_id: int
    child_calculation_ref: str


class CalculationParameterSummary(BaseModel):
    """One EAV parameter row, public projection."""
    raw_key: str
    raw_value: str
    canonical_key: str | None = None
    canonical_value: str | None = None
    section: str | None = None
    value_type: str | None = None
    unit: str | None = None
    parameter_index: int | None = None
    source: ParameterSource


class CalculationConstraintSummary(BaseModel):
    """Geometry/internal-coordinate constraint declared on the calc."""
    constraint_index: int
    kind: ConstraintKind
    atom_indices: list[int]
    target_value: float | None = None
    units: str | None = None


class CalculationArtifactSummary(BaseModel):
    """Artifact metadata. **No body bytes.** Body download is out of scope."""
    artifact_id: int                       # subject to internal-ids policy
    artifact_ref: str | None = None        # null until the artifact table
                                           # picks up a public_ref column
    kind: ArtifactKind
    filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    created_at: datetime


class CalculationGeometryLinkSummary(BaseModel):
    """One input/output geometry link, ref-bearing."""
    geometry_id: int
    geometry_ref: str
    role: CalculationGeometryRole | None = None
    output_order: int | None = None        # only meaningful for output links


class CalculationGeometryValidationSummary(BaseModel):
    """One geometry-validation row for this calc."""
    status: GeometryValidationStatus
    note: str | None = None
    created_at: datetime


class CalculationSCFStabilitySummary(BaseModel):
    """One SCF-stability row for this calc."""
    status: SCFStabilityStatusValue
    note: str | None = None
    created_at: datetime


class CalculationPathSearchSummary(BaseModel):
    """Path-search result + minimal point summary."""
    method: PathSearchMethod
    converged: bool | None = None
    npoints: int
    notes: str | None = None


class CalculationIRCSummary(BaseModel):
    """IRC result + minimal point summary."""
    direction: IRCDirection
    converged: bool | None = None
    npoints: int


class CalculationScanSummary(BaseModel):
    """Scan result + coordinate summary (no per-point coordinate arrays)."""
    coordinate_count: int
    point_count: int
    coordinates: list[ScanCoordinateSummary]


class ScanCoordinateSummary(BaseModel):
    coordinate_index: int
    kind: ScanCoordinateKind
    atom_indices: list[int]
    range_min: float | None = None
    range_max: float | None = None
    nsteps: int | None = None


class ScientificCalculationSummary(BaseModel):
    """Compact projection used inside cross-record contexts (e.g.,
    `dependencies` would reference these). Distinct from the full
    `ScientificCalculationRecord` so embedded uses don't recurse."""
    calculation_id: int
    calculation_ref: str
    calculation_type: CalculationType
    review: RecordReviewBadge
    level_of_theory: LevelOfTheorySummary | None = None


class CalculationsSearchRequest(BaseModel):
    """Service-layer request — fields = §5.3 + §5.3.8."""
    # owner filters
    species_ref: str | None = None
    species_entry_ref: str | None = None
    transition_state_ref: str | None = None
    transition_state_entry_ref: str | None = None
    reaction_ref: str | None = None
    reaction_entry_ref: str | None = None
    owner_kind: Literal["species_entry", "transition_state_entry"] | None = None

    # chemistry (MVP: smiles, inchi_key only)
    smiles: str | None = None
    inchi_key: str | None = None
    formula: str | None = None
    reactants: list[str] = Field(default_factory=list)   # Phase 2
    products: list[str] = Field(default_factory=list)    # Phase 2
    reaction_family: str | None = None                   # Phase 2

    # calculation
    calculation_type: CalculationType | None = None
    quality: CalculationQuality | None = None
    has_result: bool | None = None
    has_artifacts: bool | None = None
    has_parameters: bool | None = None
    has_constraints: bool | None = None
    has_input_geometry: bool | None = None
    has_output_geometry: bool | None = None
    created_before: datetime | None = None
    created_after: datetime | None = None

    # level of theory
    method: str | None = None
    basis: str | None = None
    aux_basis: str | None = None        # deferred
    cabs_basis: str | None = None       # deferred
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None    # deferred
    lot_ref: str | None = None
    lot_hash: str | None = None

    # software / workflow
    software: str | None = None
    software_version: str | None = None
    software_release_ref: str | None = None
    workflow_tool: str | None = None
    workflow_tool_version: str | None = None
    workflow_tool_release_ref: str | None = None

    # validation / provenance
    geometry_validation_status: GeometryValidationStatus | None = None
    scf_stability_status: SCFStabilityStatusValue | None = None
    dependency_role: CalculationDependencyRole | None = None
    parent_calculation_ref: str | None = None
    child_calculation_ref: str | None = None
    artifact_kind: ArtifactKind | None = None
    parameter_key: str | None = None
    parameter_value: str | None = None
    canonical_parameter_key: str | None = None
    canonical_parameter_value: str | None = None

    # review / quality
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False
    include_rejected_quality: bool = False

    # sort / collapse / include / pagination
    ranking: CalculationsRanking = CalculationsRanking.default
    sort: str | None = None
    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50
```

### 6.2 Reused fragments (no changes)

- `RequestEcho`, `Pagination`, `ReviewStatusSummary`, `RecordReviewBadge`,
  `LevelOfTheorySummary`, `SoftwareReleaseSummary`,
  `WorkflowToolReleaseSummary`, `LiteratureSummary`,
  `CalculationEvidenceSummary`, `EvidenceCompletenessBreakdown`,
  `CollapseMode`, `default_visible_statuses`, `status_at_or_above`,
  `ValidationSummary`, `SCFStabilitySummary`,
  `GeometryValidationStatus`, `SCFStabilityStatusValue`.
- `SpeciesCalculationsSpeciesContext`, `CalculationCoreBlock`.

---

## 7. Public-handle / internal-ID behavior (recap)

Two modes, identical to the rest of `/scientific/*`:

| Mode | Behavior |
|---|---|
| `allow_public_internal_ids = False` (target prod) | Integer ids stripped from response by `apply_internal_ids_visibility()`. Refs always present. `request.include` echoes the dropped `internal_ids` token so the caller can detect it. Path lookups by integer id continue to **work** but the body comes back ref-only. |
| `allow_public_internal_ids = True` (dev) | Integer ids present alongside refs by default; `include=internal_ids` is honored as an explicit confirmation. |

`submission_ref` (and `submission_id` when policy permits) are surfaced
under `provenance` as **optional** fields. The audit's open question
about exposing `submission_ref` on scientific records is resolved here:
**yes, but only as optional provenance metadata.** The field is always
present (possibly `null`) so callers can detect "no submission link"
without an extra include token.

---

## 8. Relationship to existing endpoints

| Existing | Relationship | Action |
|---|---|---|
| `GET /api/v1/calculations/{id}` | Tier-A/B internal table-style read; no public refs, no review filtering. | Keep as-is. New endpoint is the public/scientific projection. |
| `GET /api/v1/calculations/{id}/dependencies` | Same row source as `include=dependencies`. | Keep. Recommend deprecation once new surface stabilizes. |
| `GET /api/v1/calculations/{id}/artifacts` | Metadata-only legacy read. New endpoint's `include=artifacts` is the public mirror. Binary download stays here for now. | Keep. Binary download policy is out of scope for this spec. |
| `GET /api/v1/calculations/{id}/geometry-validations` | Legacy plural; matches new `include=geometry_validation`. | Keep. |
| `GET /api/v1/calculations/{id}/scf-stabilities` | Same; matches `include=scf_stability`. | Keep. |
| `GET /api/v1/calculations/{id}/parameters \| constraints \| input-geometry \| output-geometry \| sp-result \| opt-result \| freq-result \| scan-result \| irc-result \| path-search-result` | All mapped onto include flags. | Keep. |
| `GET /api/v1/scientific/species-calculations/search` | Stays as the species-rooted search. New endpoint is owner-agnostic and adds method/basis/validation/parameter filtering. The two are complementary, not redundant — species-calculations preserves the per-record `species` context block and `lowest_energy` ranking; the new endpoint focuses on calculation-as-evidence. | Keep both. |
| `GET /api/v1/scientific/reaction-entries/{id}/full` | The composite uses `CalculationEvidenceSummary` for embedded calcs; this spec's `ScientificCalculationSummary` is a strict superset and can replace it in a future pass. | Keep. No change required by this spec. |
| `GET /api/v1/scientific/geometries/{handle}` | Sister endpoint pattern: handle resolution, includes, abuse caps. Used as the design template here. | Keep. |

This spec **does not deprecate** any of the legacy `/calculations/...`
routes. Recommended future work: once the scientific surface is in use
by at least one external consumer, the Tier-A/B routes can be moved
behind the auth gate permanently or deprecated. That decision is
out of scope here.

---

## 9. Non-goals (binding)

The following are explicitly **out of scope** for this spec:

- Changing ingestion schemas or the `Calculation*` ORM models.
- Adding artifact body download (only metadata is exposed).
- Adding a scientific transition-state search/read.
- Adding a scientific statmech, transport, or network/pdep search.
- Adding RDKit substructure / similarity search.
- Adding a bulk export endpoint.
- Changing ARC.
- Changing `tckdb-client`.
- Designing a polymorphic `(record_type, record_id)` ref scheme.
  `submission_ref` is the only polymorphic-record handle exposed here,
  and it is concrete (always points at `submission`).

---

## 10. Test plan

All tests live under `backend/tests/api/scientific/` and mirror the
existing `test_api_*.py` style. Fixtures reuse the upload helpers in
`backend/tests/fixtures/`.

### 10.1 Detail endpoint

| Test | Asserts |
|---|---|
| `test_calc_detail_by_ref_returns_record` | path with `calc_…` ref returns a record with matching `calculation_ref` |
| `test_calc_detail_by_id_when_internal_ids_allowed` | with `allow_public_internal_ids=True`, integer path returns body containing `calculation_id` |
| `test_calc_detail_by_id_strips_ids_when_disallowed` | with `allow_public_internal_ids=False`, integer path returns body **without** `calculation_id`; `request.include` confirms the `internal_ids` token was dropped |
| `test_calc_detail_default_response_shape` | always-present fields exist: `calculation`, `owner`, `level_of_theory`, `software_release`, `workflow_tool_release`, `provenance`, `available_sections`, `review` badge |
| `test_calc_detail_owner_species_entry` | species-owned calc returns `owner.kind == "species_entry"` and a populated `owner.species_entry`; `owner.transition_state_entry is None` |
| `test_calc_detail_owner_transition_state_entry` | TS-owned calc returns `owner.kind == "transition_state_entry"` and a populated `owner.transition_state_entry` |
| `test_calc_detail_include_results` | `include=results` adds `results` with the per-type block matching `calculation_type` |
| `test_calc_detail_include_dependencies` | `include=dependencies` adds parent and child links with refs and roles |
| `test_calc_detail_include_parameters` | `include=parameters` returns raw + canonical pairs; respects `source` projection |
| `test_calc_detail_include_constraints` | `include=constraints` returns rows with atom indices + kind |
| `test_calc_detail_include_artifacts` | metadata only; never includes body bytes |
| `test_calc_detail_include_input_geometries` | links present with `geometry_ref` |
| `test_calc_detail_include_output_geometries` | links present with `geometry_ref` and `output_order` |
| `test_calc_detail_include_geometry_validation` | rows with status enum |
| `test_calc_detail_include_scf_stability` | rows with status enum |
| `test_calc_detail_include_scan` | summary block when `calculation_type=scan` |
| `test_calc_detail_include_irc` | summary block when `calculation_type=irc` |
| `test_calc_detail_include_path_search` | summary block when `calculation_type=path_search` |
| `test_calc_detail_include_all_excludes_internal_ids` | `include=all` resolves to every legal token except `internal_ids` |
| `test_calc_detail_invalid_include_returns_422` | unknown token → `unknown_include_token` |
| `test_calc_detail_handle_type_mismatch_returns_422` | non-`calc_` ref → `handle_type_mismatch` |
| `test_calc_detail_malformed_handle_returns_422` | malformed string → `invalid_handle` |
| `test_calc_detail_404_for_missing_id_or_ref` | unknown id and unknown ref both → 404; id is not echoed in detail |
| `test_calc_detail_review_badge_always_present` | every record has `calculation.review.status` set |
| `test_calc_detail_available_sections_matches_data` | `available_sections.has_<x>` is true iff `include=<x>` would return non-empty |

### 10.2 Search endpoint (GET + POST parity)

| Test | Asserts |
|---|---|
| `test_calc_search_missing_filter_returns_422` | empty request → `missing_filter` |
| `test_calc_search_by_calculation_type_only_with_owner` | filtering `calculation_type=opt` plus a species-owner filter returns matching rows |
| `test_calc_search_by_method_basis` | LoT join filter narrows correctly |
| `test_calc_search_by_software` | software join filter narrows correctly |
| `test_calc_search_by_workflow_tool` | workflow_tool join filter narrows |
| `test_calc_search_by_geometry_validation_status` | `passed/warning/fail/not_present` semantics |
| `test_calc_search_by_scf_stability_status` | `stable/unstable/stabilized/inconclusive/not_present` semantics |
| `test_calc_search_by_parameter_key_value` | EAV filter; `parameter_value` without `parameter_key` → 422 `parameter_value_requires_key` |
| `test_calc_search_by_canonical_parameter` | canonical key + value filter |
| `test_calc_search_by_artifact_kind` | only calcs with at least one artifact of the given kind |
| `test_calc_search_by_dependency_role_and_parent_ref` | restricts to calcs depending on the given parent in the given role |
| `test_calc_search_by_owner_species_entry` | resolves `species_entry_ref`; returns species-owned calcs only |
| `test_calc_search_by_owner_transition_state_entry` | resolves `transition_state_entry_ref`; returns TS-owned calcs only |
| `test_calc_search_by_owner_kind_filter` | `owner_kind=transition_state_entry` excludes species-owned calcs |
| `test_calc_search_default_excludes_rejected_and_deprecated` | rejected/deprecated review-status records absent without explicit opt-in |
| `test_calc_search_include_rejected_returns_them` | `include_rejected=true` returns them, sorted last |
| `test_calc_search_quality_filter_default_excludes_rejected_quality` | `CalculationQuality.rejected` excluded unless `include_rejected_quality=true` |
| `test_calc_search_deterministic_ordering` | identical request returns identical record order across two calls |
| `test_calc_search_pagination_bounds` | `limit > public_max_limit` → 422; `offset > public_max_offset` → 422 |
| `test_calc_search_returned_and_total_match_pagination` | `pagination.returned == len(records)`; `pagination.total >= returned`; `total` is pre-collapse |
| `test_calc_search_get_post_parity` | identical filter set via GET query and POST body returns identical response (modulo request echo) |
| `test_calc_search_post_rejects_query_string_fields` | query-string params on POST → 422 `post_search_fields_must_be_in_body` |
| `test_calc_search_client_sort_rejected` | `?sort=created_at` → 422 `client_sort_not_supported` |
| `test_calc_search_invalid_include_returns_422` | unknown token → `unknown_include_token` |
| `test_calc_search_internal_ids_hidden_when_disallowed` | `allow_public_internal_ids=False` strips ids from records and arrays |
| `test_calc_search_internal_ids_present_when_allowed` | `allow_public_internal_ids=True` + `include=internal_ids` keeps ids |
| `test_calc_search_review_summary_pre_collapse_counts` | counts reflect candidate set before pagination/collapse |

### 10.3 Cross-endpoint invariant

| Test | Asserts |
|---|---|
| `test_calc_detail_and_search_record_shape_parity` | the `record` returned by detail and a single-result `records[0]` from search with the same calculation match field-by-field |

---

## 11. Open design questions

1. **`CalculationArtifact` public ref.** The artifact table does not yet
   carry a `public_ref` column; `CalculationArtifactSummary.artifact_ref`
   is therefore typed `str | None`. Should artifacts gain a content-derived
   ref (sha256-prefix style) before this endpoint ships, so `artifact_ref`
   is always populated?
2. **`include=results` for multi-result types.** A `scan` calculation
   has both a scan result and (often) per-point sp/opt rows. Should
   `include=results` collapse to the *primary* result type for the
   calc, or list every result row? Spec assumes primary-only; the
   per-point detail is reachable via `include=scan` / `include=irc`.
3. **`reactants` / `products` chemistry filter.** Reuses the
   `/scientific/reactions/search` parsing path; whether to ship in v1
   vs Phase 2 depends on join cost. Spec marks Phase 2 to keep the v1
   query plan small.
4. **`include=review` granularity.** Should the per-record review
   history be sortable / paginated within the include, or always full
   history? Spec assumes full history (history rows for one calculation
   are bounded). Revisit if history grows large.
5. **Body-only POST guard.** `/scientific/reactions/search` and
   `/scientific/thermo/search` use the `_POST_ALLOWED_QS_KEYS` pattern;
   spec assumes the same empty allow-set here. Revisit if a single
   query-string knob (e.g., correlation id) needs to coexist with the
   body.
6. **Composite endpoint reuse.** Once this spec ships,
   `/scientific/reaction-entries/{id}/full` could replace its embedded
   `CalculationEvidenceSummary` with `ScientificCalculationSummary`
   (the strict superset). That migration is out of scope here.
7. **`available_sections` cost.** Computing the boolean map for every
   row of a search response requires a fan-out join or window query.
   If profiling shows it dominates, the spec falls back to
   `available_sections=null` on search responses (kept on detail) — but
   only after measurement.

---

## 12. Recommended implementation order

1. **Schemas first.**
   `app/schemas/reads/scientific_calculation.py` and
   `app/schemas/reads/scientific_calculation_search.py`. These are pure
   Pydantic and can be reviewed before any service or route work.
2. **Handle reconciliation pairs.** Add
   `reconcile_transition_state_pair` and
   `reconcile_transition_state_entry_pair` to
   [handles.py](../../app/services/scientific_read/handles.py). One PR.
3. **Detail endpoint, default response.** Service +
   `GET /scientific/calculations/{handle}` returning the always-present
   fields and `available_sections`. No includes wired yet. Tests:
   detail-by-ref, detail-by-id, owner branches, internal-ids policy.
4. **Detail endpoint includes (one PR per heavy include).**
   `results`, then `dependencies`, then `parameters`, then
   `constraints`, then `artifacts`, then geometries, then validation /
   scf-stability, then `scan`/`irc`/`path_search`. Each PR adds its
   include to `_LEGAL_INCLUDE_TOKENS` plus tests. Splitting like this
   keeps per-PR review scope small and lets us measure query cost
   independently.
5. **Search endpoint, owner + calculation-type filter only.** GET +
   POST. Tests for missing-filter, ordering, pagination, GET/POST
   parity. No method/basis/parameter filters yet.
6. **Search endpoint LoT/software/workflow filters.**
7. **Search endpoint validation/parameter filters.**
8. **Search endpoint chemistry filters** (`smiles`/`inchi_key` first,
   `reactants`/`products`/`reaction_family` deferred to Phase 2).
9. **Cross-endpoint shape parity test** + docs roll-up.

Each step is independently shippable behind feature flags only if
needed; the audit indicates the existing pattern is ship-as-you-go
with the include set as the public extension surface.

---

## Insight

★ Insight ─────────────────────────────────────
- The hard part of this surface is not the filter list — it is the
  `available_sections` map. It tells callers "what would this include
  return?" without making them do N round trips. Building it well
  collapses an entire class of follow-up requests; building it badly
  forces every search response to fan out over a dozen child tables.
- Re-using `ScientificCalculationRecord` in both detail and search keeps
  the client code single-shaped, but it means the `available_sections`
  cost lives in both paths. The spec's open question 7 is the lever to
  pull if the search query plan is bad.
- This endpoint is also the natural place to converge on
  `calculation_ref` as the canonical handle: the audit shows that
  almost every other scientific surface already references calculations
  by ref but each surface produces them in slightly different shapes
  (`CalculationEvidenceSummary`, `SupportingCalculationRef`,
  `CalculationCoreBlock`). A future cleanup can replace those with
  `ScientificCalculationSummary` from this spec.
─────────────────────────────────────────────────
