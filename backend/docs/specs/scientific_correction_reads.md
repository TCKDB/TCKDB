# Scientific correction-reference reads — FSF and ECS

## Purpose

Public-ref-addressable read endpoints for the two correction-reference
tables backing TCKDB scientific records:

- ``frequency_scale_factor`` (prefix ``fsf_``)
- ``energy_correction_scheme`` (prefix ``ecs_``)

These records are reference/curation data — they parameterize statmech
and applied-correction provenance, are referenced from many scientific
records, and need a stable public handle every consumer (paper, dataset,
ARC config) can quote.

The endpoints answer:

- Which frequency scale factors / correction schemes are available?
- Which LoT / software / literature source do they derive from?
- Which statmech / species-entry / reaction-entry / transition-state
  entry records use a given factor or scheme?
- Which correction terms (atom, bond, Melius component) make up a
  given scheme?

## Endpoint list

```
GET  /api/v1/scientific/frequency-scale-factors/{frequency_scale_factor_ref_or_id}
GET  /api/v1/scientific/frequency-scale-factors/search
POST /api/v1/scientific/frequency-scale-factors/search

GET  /api/v1/scientific/energy-correction-schemes/{energy_correction_scheme_ref_or_id}
GET  /api/v1/scientific/energy-correction-schemes/search
POST /api/v1/scientific/energy-correction-schemes/search
```

Route naming follows the existing scientific-surface convention of
descriptive full-word kebab-case paths (cf. ``/transition-states``,
``/network-solves``, ``/species-entries``).

## Public-ref decisions

Both tables already carry ``PublicRefMixin`` and have content-derived
prefixes registered in [`app/services/public_refs.py`](../../app/services/public_refs.py):

| Class | Prefix | Identity |
|------|--------|----------|
| `FrequencyScaleFactor` | `fsf_` | `(level_of_theory_id, software_id, scale_kind, value, source_literature_id, workflow_tool_release_id)` |
| `EnergyCorrectionScheme` | `ecs_` | `(kind, name, level_of_theory_id, source_literature_id, version, units)` |

**No schema migration is required.** Public refs are auto-populated by
the `before_insert` listener installed in `app/services/public_refs.py`.

Child rows (`energy_correction_scheme_atom_param`, `_bond_param`,
`_component_param`, `applied_energy_correction_component`) intentionally
**do not** get standalone public refs — they are addressed only through
their parent scheme/applied row and have no standalone-detail use case.

## Frequency scale factor response model

### Core block

```python
class FrequencyScaleFactorCoreBlock(BaseModel):
    frequency_scale_factor_id: int | None     # stripped unless include=internal_ids
    frequency_scale_factor_ref: str
    scale_kind: FrequencyScaleKind            # fundamental | zpe | enthalpy | entropy | heat_capacity
    value: float
    note: str | None
    created_at: datetime
```

### Default record

```
ScientificFrequencyScaleFactorRecord
  frequency_scale_factor      (core)
  level_of_theory             (summary)
  software_release            (summary; FSF only carries software_id, not a release — version is null)
  workflow_tool_release       (summary)
  literature                  (summary)
  evidence_summary
    has_literature_source
    has_workflow_tool_source
    has_software_dimension
    statmech_usage_count
    has_statmech_usage
  available_sections          (has_used_by, has_literature)
  used_by                     (only with include=used_by)
```

### Include behavior (FSF)

| Token | Default | Effect |
|-------|---------|--------|
| `used_by` | – | Bounded inverse-link list (capped 50): statmech rows referencing the FSF first, then `applied_energy_correction` targets if budget remains. Pointers carry `record_type / record_ref / endpoint`. |
| `literature` | always populated when available | No-op affordance for explicit callers. |
| `internal_ids` | – | Restores integer IDs subject to `settings.allow_public_internal_ids`. |
| `all` | – | Expands to `used_by, literature`; **excludes** `internal_ids`. |

### Search filters (FSF)

| Filter | Status |
|--------|--------|
| `frequency_scale_factor_ref` | implemented |
| `value` | implemented (exact match) |
| `value_min` | implemented |
| `value_max` | implemented |
| `scale_kind` | implemented |
| `method` | implemented (joins LoT) |
| `basis` | implemented (joins LoT) |
| `software` | implemented (joins Software by name) |
| `software_version` | **deferred** — FSF row only carries `software_id`, not a software release |
| `literature_ref` | implemented |
| `used_by_statmech` | implemented (exists/non-exists subquery) |
| `model_kind` | **deferred** — no backing column on `frequency_scale_factor`; spec lists for forward compatibility |
| `include_rejected` / `include_deprecated` / `min_review_status` | accepted but no-ops — FSF is non-reviewable |

Default sort: `scale_kind,value,id` ascending. Client-supplied `sort=` is
rejected per the v0 sort policy (422 `client_sort_not_supported`).

## Energy correction scheme response model

### Core block

```python
class EnergyCorrectionSchemeCoreBlock(BaseModel):
    energy_correction_scheme_id: int | None
    energy_correction_scheme_ref: str
    name: str
    scheme_kind: EnergyCorrectionSchemeKind   # atom_energy | atom_hf | atom_thermal | soc | bac_petersson | bac_melius | isodesmic | other
    version: str | None
    units: EnergyUnit | None
    note: str | None
    created_at: datetime
```

### Default record

```
ScientificEnergyCorrectionSchemeRecord
  energy_correction_scheme    (core)
  level_of_theory             (summary)
  literature                  (summary)
  evidence_summary
    atom_param_count
    bond_param_count
    component_param_count
    has_corrections
    applied_usage_count
    has_applied_usage
    has_literature_source
  available_sections          (has_corrections, has_used_by, has_literature)
  corrections                 (only with include=corrections)
  used_by                     (only with include=used_by)
```

### Include behavior (ECS)

| Token | Default | Effect |
|-------|---------|--------|
| `corrections` | – | Unifies the three child tables into a flat list of `EnergyCorrectionTermSummary` rows. `correction_kind ∈ {atom, bond, component}`; for `component`, `component_kind` carries the Melius sub-type. |
| `used_by` | – | Bounded inverse-link list (capped 50): `applied_energy_correction` rows with `scheme_id = self`, resolved to their target species/reaction/transition-state-entry public ref. |
| `literature` | always populated when available | No-op affordance for explicit callers. |
| `internal_ids` | – | Restores integer IDs subject to `settings.allow_public_internal_ids`. |
| `all` | – | Expands to `corrections, used_by, literature`; **excludes** `internal_ids`. |

### Search filters (ECS)

| Filter | Status |
|--------|--------|
| `energy_correction_scheme_ref` | implemented |
| `name` | implemented (exact match) |
| `version` | implemented |
| `scheme_kind` | implemented |
| `method` | implemented (joins LoT) |
| `basis` | implemented (joins LoT) |
| `literature_ref` | implemented |
| `has_corrections` | implemented (OR-exists across atom/bond/component child tables) |
| `used_by_calculation` | implemented (exists `applied_energy_correction` with `source_calculation_id is not null`) |
| `software` / `software_version` | **deferred** — ECS row has no software dimension |
| `used_by_thermo` | **deferred** — no direct or indirect relationship from ECS to `thermo`; the application-layer link runs through `applied_energy_correction` against species/reaction/TS entries, not thermo records |
| `include_rejected` / `include_deprecated` / `min_review_status` | accepted but no-ops — ECS is non-reviewable |

Default sort: `scheme_kind,name,version,id` ascending. Client-supplied
`sort=` is rejected per the v0 sort policy.

## Review/trust behavior

Both `frequency_scale_factor` and `energy_correction_scheme` are
**non-reviewable**: neither appears in
[`SubmissionRecordType`](../../app/db/models/common.py), so there is no
`record_review` row pointing at them and no submission/approval flow.

For shape parity with the rest of the scientific surface the responses
still carry an empty `ReviewStatusSummary` block. Detail responses do
**not** include a per-row review badge inside the core block (no fake
`not_reviewed` placeholder), and the `review` include token is
intentionally *not* listed as legal — calling
`include=review` returns 422 `unknown_include_token`. Search responses
accept `include_rejected` / `include_deprecated` / `min_review_status`
filter fields for shape parity with the other endpoints, but they are
no-ops.

If these record types become reviewable in the future, the appropriate
change is to (1) add them to `SubmissionRecordType`, (2) extend
`_LEGAL_INCLUDE_TOKENS` with `review`, and (3) repopulate the core
block with a `RecordReviewBadge`.

## Internal-ID behavior

Standard Phase D policy:

- Default responses strip every `*_id` / `*_ids` key plus the literal
  ID keys enumerated in `app/services/scientific_read/internal_ids.py`.
- `include=internal_ids` is silently dropped when
  `settings.allow_public_internal_ids` is `False`; when allowed, IDs
  are restored.
- `include=all` does **not** expand to `internal_ids`; callers wanting
  the legacy id-bearing shape must request `include=all,internal_ids`.

## Used-by behavior

| Token target | FSF | ECS |
|--------------|-----|-----|
| `statmech` | direct FK on `statmech.frequency_scale_factor_id` | — |
| `species_entry` / `reaction_entry` / `transition_state_entry` | via `applied_energy_correction.frequency_scale_factor_id` | via `applied_energy_correction.scheme_id` |

The endpoint never inlines the full target record. Each usage entry
carries `(record_type, record_ref, endpoint)` so callers follow the
endpoint URL for the full body. Lists are capped at 50 entries per
response to keep payloads bounded.

## Non-goals

- No correction-write or applied-correction-write endpoints.
- No bulk export.
- No inline full target-record expansion.
- No standalone public refs for child parameter rows.
- No RDKit substructure search.
- No schema redesign beyond confirming existing public-ref mixins.
- No ARC or `tckdb-client` changes.

## Implementation status

| Layer | Location |
|-------|----------|
| Routes | [`app/api/routes/scientific/corrections.py`](../../app/api/routes/scientific/corrections.py) |
| Detail service (FSF) | [`app/services/scientific_read/frequency_scale_factors.py`](../../app/services/scientific_read/frequency_scale_factors.py) |
| Search service (FSF) | [`app/services/scientific_read/frequency_scale_factors_search.py`](../../app/services/scientific_read/frequency_scale_factors_search.py) |
| Detail service (ECS) | [`app/services/scientific_read/energy_correction_schemes.py`](../../app/services/scientific_read/energy_correction_schemes.py) |
| Search service (ECS) | [`app/services/scientific_read/energy_correction_schemes_search.py`](../../app/services/scientific_read/energy_correction_schemes_search.py) |
| Schemas (FSF) | [`app/schemas/reads/scientific_frequency_scale_factor.py`](../../app/schemas/reads/scientific_frequency_scale_factor.py), `…_search.py` |
| Schemas (ECS) | [`app/schemas/reads/scientific_energy_correction_scheme.py`](../../app/schemas/reads/scientific_energy_correction_scheme.py), `…_search.py` |
| Handle resolvers | [`app/services/scientific_read/handles.py`](../../app/services/scientific_read/handles.py) — `resolve_frequency_scale_factor_handle`, `resolve_energy_correction_scheme_handle` |

## Test plan

- Tests live in [`tests/api/scientific/test_api_scientific_corrections.py`](../../tests/api/scientific/test_api_scientific_corrections.py).
- Factories live in [`tests/services/scientific_read/_factories.py`](../../tests/services/scientific_read/_factories.py): `make_frequency_scale_factor`, `make_energy_correction_scheme`, `attach_ecs_atom_param`, `attach_ecs_bond_param`, `attach_ecs_component_param`, `make_applied_energy_correction`, `make_literature`, `make_software`, `make_workflow_tool_release`.

Coverage matrix (each tested for both FSF and ECS):

- detail by ref / by id
- unknown ref → 404, wrong prefix → 422, malformed → 422
- default response shape, `*_id` stripped by default
- include tokens (`used_by`, `corrections`, `literature`, `all`)
- `include=all` excludes `internal_ids`
- `include=all,internal_ids` obeys policy
- search missing filter → 422
- search by ref / name / version / scheme_kind / value / value range / method / basis / software / literature_ref
- `used_by_statmech` / `has_corrections` true and false
- GET/POST parity, client sort rejected
- pagination envelope
- search record shape matches detail
- recursive forbidden-payload walk (`body`, `content`, `data`, `presigned_url`, `download_url`, `coordinates`, `geometry`)

## Open questions

- Should FSF carry an explicit `software_release` linkage instead of
  raw `software_id`? Today the row's `software_id` records the vendor
  axis; per-release granularity would require a schema change.
- Should `used_by_thermo` become a real filter by joining
  `applied_energy_correction` to species-entry → thermo? Held as
  deferred until a real consumer asks for it.
- If FSF/ECS become reviewable, do they participate in the existing
  submission flow or get a curator-only path? Out of scope here.
