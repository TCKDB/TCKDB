# Scientific Conformer Read/Search Spec

**Status:** spec, no implementation yet
**Companion to:**
- [scientific_calculation_reads.md](scientific_calculation_reads.md)
- [scientific_calculation_path_includes.md](scientific_calculation_path_includes.md)
- [scientific_transition_state_reads.md](scientific_transition_state_reads.md)

**Date:** 2026-05-18
**Scope:** Backend only. Spec only — no routes, no schemas, no schema
migrations introduced here. ARC, `tckdb-client`, and ingestion schemas
out of scope.

---

## 1. Current schema and route evidence

### 1.1 ORM tables

All conformer models live in
[`app/db/models/species.py`](../../app/db/models/species.py).

| Table | Public ref prefix | Reviewable? | Key fields |
|---|---|---|---|
| `conformer_group` | `cg_…` | yes | `species_entry_id` (FK), `label`, `note`, `representative_fingerprint_json`, `representative_coords_json` |
| `conformer_observation` | `co_…` | yes | `conformer_group_id` (FK), `assignment_scheme_id` (nullable FK), `scientific_origin` (`computed`/`experimental`/`estimated`), `note`, `torsion_fingerprint_json` |
| `conformer_selection` | **none** | **no** | `conformer_group_id` (FK), `assignment_scheme_id` (nullable FK), `selection_kind` (enum), `note` |
| `conformer_assignment_scheme` | `cas_…` | (out of scope here) | `name`, `version`, `scope`, `description`, `parameters_json`, `code_commit`, `is_default` |

Identity / shape highlights:

- **`conformer_group`** is the deduplicated basin under a single
  `species_entry`. Uniqueness is `(species_entry_id, label)`. There is
  **no direct FK from `conformer_group` to `geometry`** — the embedded
  `representative_coords_json` is a JSONB summary, not a geometry
  reference.
- **`conformer_observation`** is the provenance-bearing row. One basin
  may carry multiple observations (one per upload, parser pass, or
  assignment-scheme re-evaluation). Calculations attach here via
  nullable `Calculation.conformer_observation_id`
  ([`app/db/models/calculation.py:100-138`](../../app/db/models/calculation.py)).
- **`conformer_selection`** records curation: which conformer in the
  group is preferred for which purpose (`display_default`,
  `curator_pick`, `lowest_energy`, `benchmark_reference`,
  `preferred_for_thermo`, `preferred_for_kinetics`,
  `representative_geometry`). Selections are *not* reviewable
  themselves; `SubmissionRecordType` lists only `conformer_group` and
  `conformer_observation`.
- **`conformer_assignment_scheme`** is reference vocabulary describing
  how a clustering or labelling pass was performed. Out of scope for
  this v0 spec other than carrying its public ref on observation /
  selection summaries.

### 1.2 Legacy routes that already expose conformer data

[`backend/app/api/routes/conformers.py`](../../app/api/routes/conformers.py):

```http
GET  /api/v1/conformer-groups
GET  /api/v1/conformer-groups/{group_id}
GET  /api/v1/conformer-groups/{group_id}/selections
POST /api/v1/conformer-groups/{group_id}/selections     (curator/admin)
GET  /api/v1/conformer-observations
GET  /api/v1/conformer-observations/{observation_id}
```

Response schemas live in
[`app/schemas/entities/conformer.py`](../../app/schemas/entities/conformer.py)
(`ConformerGroupRead`, `ConformerGroupDetailRead`,
`ConformerObservationRead`, `ConformerSelectionRead`,
`ConformerAssignmentSchemeRead`). These routes are table-style: integer
ids, no review badges, no default-trust filtering, no
ref/internal-id Phase D policy.

### 1.3 What the scientific surface already exposes

- **`/scientific/species-calculations/search`** already accepts
  `include=conformers` and projects a `ConformerContextBlock` per
  calculation that has a non-null `conformer_observation_id` (see
  [`app/services/scientific_read/species_calculations_search.py`](../../app/services/scientific_read/species_calculations_search.py)).
  The block carries observation / group / scheme refs plus a compact
  torsion-fingerprint summary and the group's selection kinds.
- **`/scientific/calculations/{handle}`** does **not** yet surface a
  conformer block. The `include=` set does not include a conformer
  token; the existing surface is calc-centric and treats conformer
  membership as data the caller can fetch via the scientific conformer
  surface this spec proposes.
- **`/reaction-entries/{id}/full`** lists `conformers` as a legal
  include token and currently returns `[]` as a placeholder
  ([`app/services/scientific_read/provenance.py`](../../app/services/scientific_read/provenance.py)) —
  it is the next `/full` shell waiting to be aligned, which is the
  load-bearing reason for designing this surface first.

### 1.4 Test factories that already exist

In [`tests/services/scientific_read/_factories.py`](../../tests/services/scientific_read/_factories.py):

```python
make_conformer_group(session, species_entry, *, label=None) -> ConformerGroup
make_conformer_observation(session, *, conformer_group, torsion_fingerprint_json=None) -> ConformerObservation
attach_conformer_selection(session, *, conformer_group, selection_kind=ConformerSelectionKind.lowest_energy) -> ConformerSelection
make_calculation_with_conformer(session, *, species_entry, conformer_observation, type=sp, lot_id=None)
```

The implementation phase will reuse these without modification — no new
test factories are required for v0.

---

## 2. Conceptual model

The three nouns and their relationship:

```text
species
   └── species_entry
          └── conformer_group           (basin identity; deduped per species_entry)
                 ├── conformer_observation   (provenance / evidence row; N per group)
                 │      └── calculation     (0..N via Calculation.conformer_observation_id)
                 └── conformer_selection    (curation; selection_kind keyed; 0..N per group)
                        └── conformer_assignment_scheme  (vocab; optional pointer)
```

Three concerns split cleanly across the rows:

| Concern | Surface | What it answers |
|---|---|---|
| Identity (what is this basin?) | `conformer_group` | "Which distinct conformers does this species have?" |
| Provenance (where did the evidence come from?) | `conformer_observation` | "Which uploads / parsers populated each basin?" |
| Curation (which one wins?) | `conformer_selection` | "Which conformer is preferred for thermo / kinetics / display?" |

This split is the same identity-vs-provenance-vs-curation pattern the
species, calculation, and transition-state surfaces already follow, so
the conformer surface should mirror their endpoint shapes.

---

## 3. Endpoint proposal

### 3.1 Detail endpoints

```http
GET /api/v1/scientific/conformer-groups/{conformer_group_ref_or_id}
GET /api/v1/scientific/conformer-observations/{conformer_observation_ref_or_id}
```

Handle prefixes: `cg_…` for groups, `co_…` for observations.
Wrong-prefix refs return 422 `handle_type_mismatch`; unknown refs / ids
return 404. Same contract as every other scientific detail surface.

`resolve_path_handle` in
[`app/services/scientific_read/handles.py`](../../app/services/scientific_read/handles.py)
needs two thin wrappers (`resolve_conformer_group_handle`,
`resolve_conformer_observation_handle`) — both reuse the existing
generic resolver, no new resolution logic.

### 3.2 Search endpoints

```http
GET  /api/v1/scientific/conformers/search
POST /api/v1/scientific/conformers/search
```

### 3.3 Search grain — **recommended: group grain**

Records returned by `/conformers/search` are
**`ScientificConformerGroupRecord`** by default (one record per
matching `conformer_group`). Observations attach as a list under
`include=observations`.

**Why:**

- `conformer_group` is the basin — the deduplicated identity unit
  the rest of the system treats as the "conformer". This matches the
  shape callers naturally ask about ("how many conformers does this
  species have?").
- `conformer_observation` is provenance about a basin; treating it as
  the record grain would inflate counts whenever an upload re-deposits
  the same basin under a new observation, which is the opposite of
  what dedupe gives the search caller.
- `conformer_selection` is curation metadata that *belongs* to a
  group; it is not a search record by itself.

When a caller really needs observation-grain results — e.g. "show me
every parser pass that produced a conformer for species X" — the
observation detail endpoint plus a search filter
`conformer_group_ref=…` covers the case without a second search
endpoint.

### 3.4 Species-rooted convenience endpoint

```http
GET /api/v1/scientific/species-entries/{species_entry_ref_or_id}/conformers
```

**Not** required for v0. It is exactly the response of
`/conformers/search?species_entry_ref=…` — preferring search keeps the
filter surface authoritative and avoids two ways to ask the same
question. If a future UI consumer asks for it, it ships as a thin
wrapper.

---

## 4. Response fragments

Field-level schema sketches. Public refs always present; integer ids
gated by the existing Phase D `apply_internal_ids_visibility` policy.

### 4.1 Core blocks

```python
class ConformerGroupCoreBlock(BaseModel):
    conformer_group_id: int | None = None
    conformer_group_ref: str
    label: str | None = None
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge   # always-present compact badge


class ConformerObservationCoreBlock(BaseModel):
    conformer_observation_id: int | None = None
    conformer_observation_ref: str
    conformer_group_id: int | None = None
    conformer_group_ref: str
    scientific_origin: ScientificOriginKind   # computed | experimental | estimated
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge
```

Neither block exposes `representative_coords_json` or
`torsion_fingerprint_json` by default — those JSON blobs can be large
and are not part of the bounded summary. A future
`include=fingerprints` token could surface them; deferred for v0.

### 4.2 Selection summary

```python
class ConformerSelectionSummary(BaseModel):
    # No public_ref on the underlying row, so we don't synthesize one.
    selection_id: int | None = None
    selection_kind: ConformerSelectionKind
    assignment_scheme_ref: str | None = None
    assignment_scheme_id: int | None = None
    note: str | None = None
    created_at: datetime
```

### 4.3 Assignment scheme summary

```python
class ConformerAssignmentSchemeSummary(BaseModel):
    assignment_scheme_id: int | None = None
    assignment_scheme_ref: str
    name: str
    version: str
    scope: ConformerAssignmentSchemeScope   # canonical | imported | experimental | custom
    is_default: bool
```

### 4.4 Species context (for conformer records that need to identify
their parent species)

```python
class ConformerSpeciesContext(BaseModel):
    species_id: int | None = None
    species_ref: str
    species_entry_id: int | None = None
    species_entry_ref: str
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
```

This mirrors the existing `SpeciesEntryOwnerSummary` shape used by the
calculation surface so a generic client parser can reuse code.

### 4.5 Calculation evidence summary (bounded)

```python
class ConformerCalculationEvidenceSummary(BaseModel):
    calculation_count: int
    has_opt: bool
    has_freq: bool
    has_sp: bool
    has_irc: bool             # rare on conformer obs but kept for parity
    has_path_search: bool     # ditto
    has_geometry_validation: bool
    has_scf_stability: bool
```

Same shape as `TransitionStateCalculationEvidenceSummary` so the same
parser handles both. Builder reuses `_build_evidence_summary_for_entries`-
style helper but keyed off `Calculation.conformer_observation_id ∈
{ids belonging to this group}`.

### 4.6 Geometry summary (links only)

```python
class ConformerGeometrySummary(BaseModel):
    geometry_id: int | None = None
    geometry_ref: str
    role: CalculationGeometryRole | None = None   # final / initial / etc.
    natoms: int | None = None
    geom_hash: str | None = None
```

Reused from `CalculationGeometryLinkSummary`. Returned only under
`include=geometries`. Never inlines XYZ, atom rows, or coordinate
arrays — those remain behind
`GET /scientific/geometries/{geometry_ref}`.

### 4.7 Records

```python
class ConformerObservationsSummary(BaseModel):
    total: int
    by_scientific_origin: dict[str, int]   # computed/experimental/estimated counts


class ScientificConformerGroupRecord(BaseModel):
    conformer_group: ConformerGroupCoreBlock
    species: ConformerSpeciesContext
    observations_summary: ConformerObservationsSummary
    selection_summary: list[ConformerSelectionSummary]   # always present, may be []
    evidence_summary: ConformerCalculationEvidenceSummary
    available_sections: AvailableConformerSections

    # Optional include blocks
    observations: list[ScientificConformerObservationRecord] | None = None
    calculations: list[ConformerCalculationSummary] | None = None
    geometries: list[ConformerGeometrySummary] | None = None
    review_history: list[ConformerReviewEntry] | None = None


class ScientificConformerObservationRecord(BaseModel):
    conformer_observation: ConformerObservationCoreBlock
    conformer_group: ConformerGroupCoreBlock     # parent context, always present
    species: ConformerSpeciesContext
    assignment_scheme: ConformerAssignmentSchemeSummary | None = None
    evidence_summary: ConformerCalculationEvidenceSummary
    available_sections: AvailableConformerSections

    # Optional include blocks
    calculations: list[ConformerCalculationSummary] | None = None
    geometries: list[ConformerGeometrySummary] | None = None
    review_history: list[ConformerReviewEntry] | None = None


class ScientificConformerGroupDetailResponse(BaseModel):
    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificConformerGroupRecord


class ScientificConformerObservationDetailResponse(BaseModel):
    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificConformerObservationRecord


class ScientificConformersSearchResponse(BaseModel):
    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificConformerGroupRecord]
    pagination: Pagination
```

`ConformerCalculationSummary` is the same compact calculation
projection (`calculation_ref`, type, quality, review, LoT/software/
workflow summaries) used by the TS surface. Reuse the existing
fragment rather than redefining it.

---

## 5. Include behavior

Legal include tokens (six public + `internal_ids`):

```text
observations
selections
calculations
geometries
review
internal_ids
all
```

Group detail (`/conformer-groups/{handle}`):

```text
include=observations    — list of ScientificConformerObservationRecord
                          per observation under the group
include=selections      — already on by default in the bounded
                          selection_summary; the include token surfaces
                          per-row note + created_at + scheme summary
                          for richer detail
include=calculations    — compact calc summaries for every calc whose
                          conformer_observation belongs to this group
include=geometries      — output-geometry links from those calcs
include=review          — record_review row history for the group
include=all             — observations + selections + calculations +
                          geometries + review (never internal_ids)
include=internal_ids    — Phase D policy gate
```

Observation detail (`/conformer-observations/{handle}`):

```text
include=calculations    — calc summaries for this observation only
include=geometries      — output-geometry links for this observation's
                          calcs
include=review          — record_review row history for the observation
include=all             — calculations + geometries + review (never
                          internal_ids)
include=observations    — silently a no-op (the observation IS the
                          record); kept legal so a generic client can
                          pass the same set everywhere
include=selections      — silently a no-op (selections belong to the
                          parent group)
include=internal_ids    — Phase D policy gate
```

Search (`/conformers/search`):

```text
include=observations    — embed observation records under each group
include=selections      — extra per-row selection detail beyond the
                          bounded selection_summary
include=calculations    — embed calc summaries on each record
include=geometries      — embed geometry links on each record
include=review          — embed review history on each record
include=all             — five public tokens above (never internal_ids)
include=internal_ids    — Phase D policy gate
```

The default response (no include) always returns the **core block +
species context + observations_summary + selection_summary +
evidence_summary + available_sections** — i.e. the cheap summary that
identifies the basin and reports curation status. Heavy lists are
opt-in via include.

---

## 6. Search filters

MVP filter set:

```text
species_ref
species_entry_ref
conformer_group_ref
conformer_observation_ref
assignment_scheme_ref          (resolves cas_… → scheme id)
selection_kind                 (ConformerSelectionKind enum)
has_selection                  (any selection row at all)
has_observations
has_calculations
has_opt
has_freq
has_sp
has_geometry_validation
has_scf_stability
scientific_origin              (filters at the observation grain — see
                                §6.1 below)
method
basis
software
software_version
workflow_tool
workflow_tool_version
min_review_status              (RecordReviewStatus)
include_rejected
include_deprecated
include
offset
limit
sort                           (rejected non-None per v0 policy)
```

### 6.1 Observation-grain filters at group grain

`scientific_origin`, `assignment_scheme_ref`, and
`conformer_observation_ref` are *observation-level* facts, but the
search records are group-grain. The semantics:

```text
A group matches scientific_origin=X iff it has at least one
observation with that origin.
A group matches assignment_scheme_ref=cas_… iff it has at least one
observation under that scheme.
A group matches conformer_observation_ref=co_… iff that observation
belongs to it.
```

This matches the way the TS search treats per-entry has_* evidence
filters at the entry-grain.

### 6.2 At-least-one-filter rule

Pure pagination / include / review knobs do not satisfy the filter
gate. A request that supplies none of the meaningful filters above
returns 422 `missing_filter`. Same code, same UX as the calculation
and TS search surfaces.

### 6.3 Default deterministic ordering

```text
review_rank ASC
selection_priority DESC          (groups with curator_pick / lowest_energy
                                   selections rank higher than unselected)
created_at DESC
conformer_group_id DESC
```

If `selection_priority` ranking adds complexity at v0, drop it and use
`review_rank, created_at DESC, id DESC` only. Document the
selection-priority sort as deferred. The TS surface already shipped
with the shorter sort; conformers may follow the same simpler default.

---

## 7. Review/trust behavior

`ConformerGroup` and `ConformerObservation` are both listed in
`SubmissionRecordType`, so they are reviewable records:

- **Detail endpoints** never filter by review status. Default-trust
  posture applies to *search* only. Each record carries the compact
  `RecordReviewBadge` on its core block.
- **Search** hides `rejected` / `deprecated` by default;
  `include_rejected=true` / `include_deprecated=true` opt them in.
  `min_review_status` narrows further. Every record carries a badge.
- **`ConformerSelection`** is NOT in `SubmissionRecordType` and has no
  review badge — it is curation, not a reviewable scientific claim.
  The selection summary surfaces `selection_kind` and timestamps;
  trust is attributed to the parent group.
- **`review_summary`** on the response envelope counts the visible
  *records* (groups) before pagination. The TS surface already follows
  this convention.

---

## 8. Internal-ID behavior

Identical to every other `/scientific/*` surface:

- Default: every `*_id` field is stripped recursively. Refs (`*_ref`)
  always present.
- `include=internal_ids` + `settings.allow_public_internal_ids=True`:
  IDs restored.
- `include=internal_ids` + policy disallows: token silently dropped
  from `request.include`; IDs stay hidden.

One subtle case: `ConformerSelection` has no `public_ref` column
today, so the selection summary surfaces an integer `selection_id`
that has no public ref sibling. Under the default Phase D policy
`selection_id` is stripped — this means selections become *anonymous*
(`selection_kind` + `created_at` only). If callers need to address
selections individually, the schema needs a `public_ref` migration on
`conformer_selection`. Flagged as open question §13.1.

---

## 9. Geometry behavior

Conformer surfaces never inline coordinates. Under
`include=geometries`, each record carries lightweight geometry-link
items:

```text
geometry_ref     (always present)
geometry_id      (policy-gated)
role             (CalculationGeometryRole — final / scan_point / …)
natoms
geom_hash
```

Geometry rows are reached through the supporting calculations
(`Calculation.conformer_observation_id` → `Calculation` →
`CalculationOutputGeometry` → `Geometry`). Output geometries only by
default — input geometries are usually less interesting for conformer
identity. A future `include=input_geometries` token could split them
if a real consumer asks; deferred for v0.

Defense-in-depth: the recursive forbidden-payload walk applied to the
TS surface should be applied here too. Forbidden keys at any depth:

```text
xyz_text, atoms, coords, symbols,
body, content, data, presigned_url, download_url,
representative_coords_json     (JSONB blob — keep out of v0 surface)
```

`torsion_fingerprint_json` is similarly excluded from the default
projection. If a fingerprints view is wanted, ship a separate
`include=fingerprints` token in a later slice with explicit size
bounds.

---

## 10. Reaction-full integration

`/reaction-entries/{id}/full?include=conformers` currently returns
`[]` ([`provenance.py`](../../app/services/scientific_read/provenance.py)).
After this surface ships, the section should be aligned the same way
the scan / IRC / path-search and artifacts sections were:

### 10.1 Recommended shape

```python
class ReactionFullSpeciesConformers(BaseModel):
    species_entry_ref: str
    species_entry_id: int | None = None
    role: ReactionRole              # reactant or product
    participant_index: int
    conformer_groups: list[ReactionFullConformerGroupItem]


class ReactionFullConformerGroupItem(BaseModel):
    conformer_group_ref: str
    conformer_group_id: int | None = None
    endpoint: str                   # /scientific/conformer-groups/{ref}
    summary: ConformerGroupCoreBlock          # core block reused
    observations_summary: ConformerObservationsSummary
    selection_summary: list[ConformerSelectionSummary]
    evidence_summary: ConformerCalculationEvidenceSummary
```

Top-level: `conformers: list[ReactionFullSpeciesConformers] | None`.

### 10.2 Reachability

Conformer groups visible in `/full` are those attached to **reactant
and product species entries** of the reaction. The TS branch already
has its own `transition_states` section; mixing conformers into the TS
section would conflate basin identity with TS candidate identity.

If a future reactant or product species has no conformer groups, that
species's `conformer_groups` list is `[]` — the species entry still
appears in the section so callers can confirm "no conformers" without
a second round-trip.

### 10.3 Anti-drift guarantee

Each per-group `summary` / `observations_summary` /
`selection_summary` / `evidence_summary` block is byte-identical to
the matching projection on the
`/scientific/conformer-groups/{ref}` detail endpoint. The same helpers
that build the conformer detail surface build the per-group block
under `/full`, the same pattern the path / artifact alignments
followed. Cross-endpoint equality tests enforce the contract.

### 10.4 Caps and payload safety

Reuse `_enforce_full_expansion_caps` with a new
`max_full_conformer_groups_public` ceiling. Per-group evidence
booleans / counts are cheap; the per-group observations summary is
bounded. Heavy lists (per-observation calc summaries, per-group
geometry links) remain available only on the per-record detail
endpoints — `/full` carries the **grouped basin summary** and the
`endpoint` hint, nothing more.

---

## 11. Implementation phases

Each phase is independently shippable.

### Phase 1 — handles + detail endpoints  ✓ implemented

1. ✓ `resolve_conformer_group_handle` /
   `resolve_conformer_observation_handle` in `handles.py`.
2. ✓ Fragment schemas in `app/schemas/reads/scientific_conformer.py`.
3. ✓ Detail services in `app/services/scientific_read/conformers.py`
   (`get_conformer_group`, `get_conformer_observation`).
4. ✓ Two routers (`cg_router`, `co_router`) mounted via
   `app/api/routes/scientific/__init__.py`.
5. ✓ Tests in
   `tests/api/scientific/test_api_scientific_conformers.py` (41 tests
   covering detail × {by ref / by id / unknown 404 / wrong-prefix 422 /
   malformed 422 / default shape / each include token / include=all /
   internal-id policy / forbidden-payload walk}).

Implementation deviations from the spec sketch worth flagging:

- **Selection summary appears in two places on the group surface.**
  The default response always carries `selection_summary` (bounded,
  always-present curation snapshot); `include=selections` populates an
  identical-content `selections` list. Both refer to the same selection
  rows; the include block exists so callers can request "give me the
  selection rich-detail explicitly" without inspecting the default
  block. Future surfaces (search records) will mirror the same pattern.
- **`include=selections` on the observation surface returns the parent
  group's selections.** This is a useful UX choice: a caller landing
  on an observation page typically wants to know whether the basin is
  curated. Documented in the service docstring.
- **`include=observations` on the observation surface is silently
  dropped.** The token flows through `validate_includes` (legal), but
  the observation record schema has no `observations` field, so it
  produces no extra payload. Matches the TS surface's `entries` token.
- **Geometry reachability is output-geometries only.** A future
  `include=input_geometries` could split them; deferred until a real
  consumer asks. Documented in §9 of this spec.
- **`representative_fingerprint_json`, `representative_coords_json`,
  `torsion_fingerprint_json` are all excluded from the default
  surface and from `include=all`.** A future `include=fingerprints`
  token can surface them with explicit size bounds (open question
  §13.3 below).

### Phase 2 — search  ✓ implemented

1. ✓ `app/services/scientific_read/conformers_search.py` mirrors the
   transition-state search service.
2. ✓ Request / response schemas in
   `app/schemas/reads/scientific_conformer_search.py`.
3. ✓ GET + POST `/conformers/search` wired via the new `search_router`
   under `/conformers` (registered before the `cg_router` for OpenAPI
   ordering; no path collisions since the prefixes differ).
4. ✓ 42 tests in
   `tests/api/scientific/test_api_scientific_conformers.py` covering
   each implemented filter, empty-filter 422 (GET + POST), GET / POST
   parity, POST rejects query-string fields, pagination envelope,
   deterministic ordering, client-sort rejection, include behavior
   (each token + `all` + internal-id policy), cross-endpoint record-
   shape parity with `/conformer-groups/{ref}`, unknown-ref empty
   short-circuit, wrong-prefix 422, and the recursive forbidden-payload
   walk.

The detail service was refactored to expose
`build_group_record(session, *, cg, cg_badge, includes)` as the
shared per-record materializer — the search service calls it for each
page row so the search and detail surfaces produce the same record
shape by construction.

Implementation deviations from the spec sketch worth flagging:

- **`has_*` bool filters treat explicit ``False`` as meaningful.**
  Bool filter fields default to ``None``; only ``None`` skips the
  at-least-one-filter check. This means `has_selection=false` (groups
  without any selection) and friends are first-class filters — useful
  for "show me uncurated basins" queries. The TS search has a latent
  bug here (it skips ``False``); fixing that is a separate PR.
- **`selection_kind` + `has_selection=False` is treated as
  "no row of *that kind*".** Combining them is unusual but the
  semantics are documented in the service: the EXISTS clause is
  negated when `has_selection=False` is supplied alongside
  `selection_kind` / `assignment_scheme_ref`.
- **Default sort is `review_rank,created_at,id`.** Selection-priority
  sorting is documented as deferred (open question §13.2).

### Phase 3 — reaction-full integration

1. Extend `provenance.py` `_build_conformers_section` to emit the
   grouped summary shape from §10.
2. Add `max_full_conformer_groups_public` setting.
3. Cross-endpoint equality tests against the conformer detail
   endpoint (same anti-drift pattern as TS / scan / IRC / path-search /
   artifacts).

### Phase 4 (deferred) — fingerprints / coords-json projection

If a downstream consumer asks for `representative_coords_json` or
`torsion_fingerprint_json`, ship behind a new `include=fingerprints`
token with explicit size bounds. Out of scope for the initial slice.

### Phase 5 (deferred) — selection writes / curation

`POST /conformer-groups/{handle}/selections` already exists on the
legacy table-style surface for curators / admins. A scientific
write-side mirror is not in scope here.

---

## 12. Test plan

Detail tests (both group and observation surfaces, mirroring the TS
test layout):

```text
detail by ref / by integer id
unknown ref → 404
wrong-prefix handle → 422
malformed handle → 422

default response shape:
  core block + species context + observations_summary +
  selection_summary + evidence_summary + available_sections

review badge present on the core block
review_summary present on the envelope
species context present and matches the underlying species_entry

include=observations              (group detail only)
include=selections                (group detail only)
include=calculations              (count matches evidence_summary.calculation_count)
include=geometries                (refs only; never inlines XYZ)
include=review                    (record_review history)
include=all                       (every public token; never internal_ids)
include=all + internal_ids        (restores ids if policy permits)

rejected/deprecated detail records still returned with badge

forbidden-payload walk:
  no representative_coords_json / torsion_fingerprint_json inlining
  no xyz_text / atoms / coords / symbols leak
  no body / content / data / download URL leak
```

Search tests (mirroring TS search test set):

```text
GET / POST missing filter → 422
each implemented filter — happy path + an unmatched case
short-circuit on unknown well-formed refs
422 on wrong-prefix refs
default hides rejected/deprecated; include_rejected surfaces them
pagination envelope correctness
deterministic ordering
client sort rejected
GET / POST parity
include behavior on records (calculations, geometries, observations,
selections, review, all)
internal-ID hiding / restoring
recursive forbidden-payload walk
```

Cross-endpoint equality (anti-drift):

```text
search record core block == group detail record core block
group detail evidence_summary == observation detail evidence_summary
  (when restricted to the observation's id) — only if reuse helper
  guarantees identity; otherwise document the boundary
```

Reaction-full conformer section tests (Phase 3):

```text
/full?include=conformers returns one entry per reactant/product species
each entry's conformer_groups list is empty for species with no groups
per-group summary block byte-identical to /scientific/conformer-groups/{ref}
endpoint hint matches the live detail URL
no XYZ / atoms / coords leakage
include=conformers respects max_full_conformer_groups_public cap
```

---

## 13. Open questions

### 13.1 `conformer_selection` has no `public_ref`

Selections are addressed today only by integer id. Under the Phase D
default policy the selection_id is stripped, so the public surface
serializes anonymous selections (kind + scheme summary + timestamps).
That's fine for read-only consumption, but if a curator UI ever needs
to address a selection (edit, delete, link to), the schema needs a
new `public_ref` column on `conformer_selection`. Out of scope for
this spec; flagged for a future migration PR.

### 13.2 Selection priority ranking

A search ordering that prefers groups with explicit selections
(`curator_pick`, `lowest_energy`, `preferred_for_thermo`) is the
useful default for a "what should I use?" caller. Computing the rank
cheaply requires either a denormalized column on `conformer_group` or
a cached aggregate. v0 can ship without it (sort by
`review_rank, created_at desc, id desc`); the prioritized sort is a
follow-up.

### 13.3 Fingerprint / coords-json exposure

Both `conformer_group.representative_coords_json` and
`conformer_observation.torsion_fingerprint_json` are useful for
clustering / comparison tooling but can be unbounded. Ship the
default surface without them; add `include=fingerprints` if a real
consumer asks. The forbidden-payload walk explicitly excludes those
keys at every depth so a future implementer cannot accidentally leak
them by reusing a helper.

### 13.4 Calculation-detail conformer block

Should
`/scientific/calculations/{handle}?include=conformer` (singular)
surface a per-calc conformer-context block byte-identical to the one
`species-calculations/search` already produces? Probably yes — it
closes the loop for "what conformer does this calc represent?". This
is a one-line addition on the calculation detail surface and can ride
in Phase 1 if scope allows; otherwise Phase 2.

### 13.5 Observation-grain search

Some downstream consumers may want a flat "every observation matching
filter X" view — closer to the legacy `/conformer-observations` list.
Today, `/conformers/search` returns groups with optional
`include=observations`. If observation-grain becomes load-bearing for
a UI, the answer is a sibling endpoint
(`/conformer-observations/search`) rather than re-graining the
existing one — keeps the v0 contract stable.

### 13.6 Reaction-full conformer reach

§10 proposes reaching conformer groups via reactant / product species
entries. Should we also surface conformer evidence under the TS
section? Today `transition_state_entry` does not own a conformer
group (the conformer concept attaches to `species_entry`, not
`transition_state_entry`). The TS section therefore stays
conformer-free; the conformers section lives at the reaction-full
top level alongside `species` / `transition_states`. Documenting the
reach explicitly avoids future ambiguity.
