# Internal ID visibility policy

> **Status:** Phase D.0 audit / Phase D.1 policy.
> Audit + policy only — no code behavior changes in this phase.
> Companion to [`docs/specs/public_identifier_policy.md`](./public_identifier_policy.md).

## Purpose

Define when integer primary keys (`*_id`) should appear in hosted
scientific read responses, when they should be hidden, how callers
request them when needed, and how the existing test/doc/example
surface needs to migrate.

Phase A added public refs to the schema and ORM. Phase B exposed
`*_ref` siblings in responses next to existing `*_id` fields.
Phase C made refs operational as inputs (path handles + query
filters). Phase D will eventually hide integer primary keys by default
in public hosted responses, exposing them only through an explicit
opt-in. This document is the policy spec for that change — Phase D.1
will implement it.

## Current state

Every scientific read response currently includes **both** an integer
primary key and its public ref for every record:

```json
{
  "species_entry_id": 31,
  "species_entry_ref": "spe_abcdef…",
  "thermo_id": 7,
  "thermo_ref": "thm_xyz123…"
}
```

- `*_ref` is the preferred hosted/public handle. Refs are stable,
  prefix-typed, and (for identity tables) cross-instance reproducible.
- `*_id` is the internal database primary key. It is retained during
  the Phase B/C compatibility window so existing callers, examples,
  and tests keep working.

Request input accepts either form (Phase C): integer PKs work
positionally, `*_ref` filters work via dedicated query/body fields,
and supplying both must agree (or 422 `<resource>_handle_conflict`).

## Public refs vs internal IDs

| Aspect | Public ref (`*_ref`) | Internal PK (`*_id`) |
|---|---|---|
| Audience | All callers | Curators / debug only (target) |
| Stability across instances | Yes (identity tables) | No |
| Prefix-typed | Yes (`spe_`, `rxe_`, `lot_`, …) | No |
| Used as path handle | Yes (Phase C) | Yes (compatibility) |
| Used as query filter | Yes (Phase C) | Yes (compatibility) |
| Echoed by request layer | When supplied | When supplied |
| Default visibility (after Phase D.1) | Always | Hidden — opt-in via `include=internal_ids` |

The principle: **refs are the hosted contract**, integer PKs are an
internal implementation detail that leaks for compatibility and may
disappear from public response shapes.

## Visibility policy

### Target default (Phase D.1)

Anonymous and ordinary authenticated callers:

- `*_ref` fields visible by default.
- `*_id` fields **hidden by default**.
- Bare integer-id arrays (`input_geometry_ids`, `output_geometry_ids`,
  `supporting_calculation_ids`) hidden by default. Their object-array
  siblings (`input_geometries`, `output_geometries`,
  `supporting_calculations`) remain visible.
- Request echo (`request.filter`) echoes whatever the caller actually
  supplied — see [Request echo](#request-echo-behavior).

Opt-in via `include=internal_ids`:

- Integer `*_id` fields and bare integer-id arrays are restored.
- The token participates in the existing `include=` vocabulary (the
  request echo will surface `"include": [..., "internal_ids", ...]`).

### Caller-context matrix (recommended)

Scientific read routes today depend only on `get_db()`; **there is no
`current_user` dependency in scope**, so the v0 implementation must
apply policy uniformly to all callers via a server setting or a
per-request explicit opt-in.

| Context | Default | `include=internal_ids` allowed? |
|---|---|---|
| Anonymous (hosted prod) | refs only | **No** by default. May be enabled via a server setting (`allow_public_internal_ids=false` recommended). |
| Authenticated user | refs only | Same as anonymous, unless `current_user` is added later. |
| Curator / admin (future) | refs only | Yes (once auth is wired through `/scientific/*`). |
| Local / dev | refs only | Yes — `allow_public_internal_ids=true` for ergonomics. |
| Internal test harness | refs only | Yes — tests that need IDs opt in explicitly. |

> **Implementation note:** scientific read routes do not currently
> have `current_user` dependencies. Phase D.1 should land the
> visibility mechanism uniformly first (one server setting or
> `include=` rule), then layer user-context gating later if/when
> read-side auth is added. The mechanism must not regress the
> existing route signatures.

## Affected endpoints

All eight `/api/v1/scientific/*` read endpoints (response shape only —
inputs and route paths do not change):

| Path | Methods | Response model |
|---|---|---|
| `/species/search` | GET | `ScientificSpeciesSearchResponse` |
| `/reactions/search` | GET, POST | `ScientificReactionSearchResponse` |
| `/thermo/search` | GET, POST | `ScientificThermoSearchResponse` |
| `/kinetics/search` | GET, POST | `ScientificKineticsSearchResponse` |
| `/species-calculations/search` | GET, POST | `ScientificSpeciesCalculationsSearchResponse` |
| `/species-entries/{species_entry_id}/thermo` | GET | `ScientificSpeciesThermoResponse` |
| `/reaction-entries/{reaction_entry_id}/kinetics` | GET | `ScientificReactionKineticsResponse` |
| `/reaction-entries/{reaction_entry_id}/full` | GET | `ScientificReactionFullResponse` |

For each, Phase D.1 must:

- Add `"internal_ids"` to `_LEGAL_INCLUDE_TOKENS`.
- Strip `*_id` fields from the response payload when `internal_ids` is
  not in the resolved include set.
- Strip bare integer-id arrays under the same condition.
- Leave every `*_ref` field visible.
- Leave every input/echo field untouched (route still accepts
  integer path handles and `*_id` query filters).

## Field inventory

Phase B + C guarantees: every integer field below has a matching
public-ref sibling on the same Pydantic model, **except** for two
naming quirks called out under "Notes".

### Top-level envelope fields

| Field | Matching ref | Endpoint(s) | Hide by default? |
|---|---|---|---|
| `species_entry_id` | `species_entry_ref` | `ScientificSpeciesThermoResponse` | Yes |
| `reaction_entry_id` | `reaction_entry_ref` | `ScientificReactionKineticsResponse` | Yes |

### Shared fragments (`scientific_common.py`)

| Field | Matching ref | Class | Hide by default? |
|---|---|---|---|
| `level_of_theory_id` | `level_of_theory_ref` | `LevelOfTheorySummary` | Yes |
| `software_release_id` | `software_release_ref` | `SoftwareReleaseSummary` | Yes |
| `workflow_tool_release_id` | `workflow_tool_release_ref` | `WorkflowToolReleaseSummary` | Yes |
| `id` | `literature_ref` | `LiteratureSummary` | Yes¹ |
| `calculation_id` | `calculation_ref` | `ValidationSummary`, `SCFStabilitySummary`, `CalculationEvidenceSummary`, `PathSearchSummary` | Yes |

¹ `LiteratureSummary.id` is the integer literature PK; the ref sibling
is `literature_ref`. The field is named `id` rather than
`literature_id` for historical reasons. Phase D.1 must hide
`LiteratureSummary.id` even though the field name lacks the `_id`
suffix.

### Species / species_entry (`scientific_species.py`)

| Field | Matching ref | Class | Hide by default? |
|---|---|---|---|
| `species_id` | `species_ref` | `SpeciesScientificRecord` | Yes |
| `species_entry_id` | `species_entry_ref` | `SpeciesEntryScientificRecord` | Yes |

### Reactions (`scientific_reactions.py`)

| Field | Matching ref | Class | Hide by default? |
|---|---|---|---|
| `reaction_id` | `reaction_ref` | `ReactionScientificRecord` | Yes |
| `reaction_entry_id` | `reaction_entry_ref` | `ReactionScientificRecord` | Yes |
| `species_entry_id` | `species_entry_ref` | `ReactionParticipantSummary` | Yes |

### Thermo (`scientific_thermo.py` + `scientific_thermo_search.py`)

| Field | Matching ref | Class | Hide by default? |
|---|---|---|---|
| `thermo_id` | `thermo_ref` | `ThermoRecord` | Yes |
| `statmech_id` | `statmech_ref` | `ThermoProvenance` | Yes |
| `freq_calculation_id` | `freq_calculation_ref` | `ThermoProvenance` | Yes |
| `sp_calculation_id` | `sp_calculation_ref` | `ThermoProvenance` | Yes |
| `species_id` | `species_ref` | `ThermoSearchSpeciesContext` | Yes |
| `species_entry_id` | `species_entry_ref` | `ThermoSearchSpeciesContext` | Yes |

### Kinetics (`scientific_kinetics.py` + `scientific_kinetics_search.py`)

| Field | Matching ref | Class | Hide by default? |
|---|---|---|---|
| `kinetics_id` | `kinetics_ref` | `KineticsRecord` | Yes |
| `transition_state_entry_id` | `transition_state_entry_ref` | `KineticsProvenance` | Yes |
| `ts_opt_calculation_id` | `ts_opt_calculation_ref` | `KineticsProvenance` | Yes |
| `ts_freq_calculation_id` | `ts_freq_calculation_ref` | `KineticsProvenance` | Yes |
| `ts_sp_calculation_id` | `ts_sp_calculation_ref` | `KineticsProvenance` | Yes |
| `reaction_id` | `reaction_ref` | `KineticsSearchReactionContext` | Yes |
| `reaction_entry_id` | `reaction_entry_ref` | `KineticsSearchReactionContext` | Yes |

### Species-calculations (`scientific_species_calculations.py`)

| Field | Matching ref | Class | Hide by default? |
|---|---|---|---|
| `species_id` | `species_ref` | `SpeciesCalculationsSpeciesContext` | Yes |
| `species_entry_id` | `species_entry_ref` | `SpeciesCalculationsSpeciesContext` | Yes |
| `calculation_id` | `calculation_ref` | `CalculationCoreBlock`, `SupportingCalculationRef` | Yes |
| `conformer_observation_id` | `conformer_observation_ref` | `ConformerContextBlock` | Yes |
| `conformer_group_id` | `conformer_group_ref` | `ConformerContextBlock` | Yes |
| `conformer_assignment_scheme_id` | `conformer_assignment_scheme_ref` | `ConformerContextBlock` | Yes |
| `primary_output_geometry_id` | `primary_output_geometry_ref` | `GeometryBlock` | Yes |
| `geometry_id` | `geometry_ref` | `GeometryRef` (inside object arrays) | Yes |
| `submission_id` | `submission_ref` | `CalculationProvenanceBlock` | Yes² |
| `input_geometry_ids` | object array `input_geometries` | `GeometryBlock` | Yes (bare array hidden, object array kept) |
| `output_geometry_ids` | object array `output_geometries` | `GeometryBlock` | Yes (bare array hidden, object array kept) |
| `supporting_calculation_ids` | object array `supporting_calculations` | `CalculationProvenanceBlock` | Yes (bare array hidden, object array kept) |

² `submission_id` exposure is currently always `None` in v0
responses — the field is structurally present but unpopulated.
Phase D.1 still strips it from the public shape; see
[Open questions](#open-questions) for whether submission internals
should also be gated by `include=internal_ids`.

### Composite /full document (`scientific_provenance.py`)

| Field | Matching ref | Class | Hide by default? |
|---|---|---|---|
| `id` | `reaction_entry_ref` | `ReactionEntrySummary` | Yes³ |
| `reaction_id` | `reaction_ref` | `ReactionEntrySummary` | Yes |
| `species_entry_id` | `species_entry_ref` | `ReactionFullSpeciesParticipant` | Yes |
| `calculation_id` | `calculation_ref` | `TransitionStateCalculationSlot` | Yes |
| `parent_calculation_id` | `parent_calculation_ref` | `TransitionStateDependency` | Yes |
| `child_calculation_id` | `child_calculation_ref` | `TransitionStateDependency` | Yes |
| `transition_state_entry_id` | `transition_state_entry_ref` | `TransitionStateInFull` | Yes |
| `record_id` (inside `ReviewRecordEntry`) | (no ref sibling) | `ReviewRecordEntry` | No⁴ |

³ Same quirk as `LiteratureSummary.id`: the top-level reaction-entry
summary in `/full` exposes `id`, not `reaction_entry_id`. The ref
sibling is `reaction_entry_ref`. Phase D.1 must hide the `id` key on
this model.

⁴ `ReviewRecordEntry.record_id` (audit-array entry, only present with
`include_review=full`) is **polymorphic** across record types and has
no current ref sibling. See
[Open questions](#open-questions) — the spec defers a decision to
Phase D.1 implementation. Recommended: also gate behind
`include=internal_ids` since these are diagnostic audit entries.

## Bare integer arrays

Three legacy bare-id arrays exist in `GeometryBlock` and
`CalculationProvenanceBlock`. Each has a parallel object array with
ref-bearing entries (added in Phase B):

| Bare integer array | Object array sibling |
|---|---|
| `input_geometry_ids: list[int]` | `input_geometries: list[GeometryRef]` |
| `output_geometry_ids: list[int]` | `output_geometries: list[GeometryRef]` |
| `supporting_calculation_ids: list[int]` | `supporting_calculations: list[SupportingCalculationRef]` |

**Phase D default response:**

- Object arrays present (ref + id per element).
- Bare integer arrays omitted.

**With `include=internal_ids`:**

- Both forms present (object arrays + bare integer arrays).
- This preserves byte-for-byte compatibility for callers that haven't
  migrated yet, when the server explicitly allows internal IDs.

The object-array form is already the canonical shape for new callers;
the bare arrays are pure compatibility surface.

## Caller contexts

Read endpoints currently take no user-context dependency. Phase D.1
should choose **one of two** mechanisms, both server-side:

1. **Server setting** (recommended for v0):
   `ALLOW_PUBLIC_INTERNAL_IDS: bool = False` in app settings.
   - In hosted production: `False`. `include=internal_ids` is silently
     dropped from the resolved include set; the response is refs-only.
     The token is *not* an error to supply — it just doesn't take
     effect.
   - In local/dev: `True`. The token does what it says.

2. **Token + explicit reject** (recommended once auth lands on reads):
   The token is parsed normally and validated against a context check
   (`should_include_internal_ids(...)`); unauthorized callers get
   `403 internal_ids_not_allowed`.

> **Decision deferred to Phase D.1:** Start with mechanism (1). It is
> the smallest possible change. Mechanism (2) can replace (1) when
> read routes gain auth without disturbing the public include
> vocabulary.

## `include=internal_ids` behavior

### Token name

Use `internal_ids` — a single multi-word include token that fits the
existing include vocabulary (`provenance`, `calculations`,
`transition_states`, `path_search`, `review`, `artifacts`, …). Do
**not** add a separate `include_internal_ids=true` query parameter;
that would proliferate request-parameter shapes without benefit.

### Semantics

For each search/detail endpoint:

```text
internal_ids ∈ resolved include set + allowed by context
  → response keeps every *_id field and bare integer array

internal_ids ∈ resolved include set + NOT allowed by context
  → token silently dropped from resolved include set
     (or 403 if auth is wired through and we choose strict mode)

internal_ids ∉ resolved include set
  → response omits every *_id field and bare integer array
```

### Interaction with `include=all`

`include=all` expands to *all legal tokens except `all` itself* in the
existing helper (`validate_includes`). Phase D.1 must decide whether
`all` includes `internal_ids`:

- **Recommended:** `internal_ids` is **excluded** from the `all`
  expansion. Callers must request it explicitly. This protects the
  hosted public contract from "I asked for everything" being a leak
  vector.

### Request echo behavior

`request.filter` and `request.include` already echo what the caller
supplied — Phase D.1 preserves that:

- If the caller supplied `species_entry_id=31`, the echo carries
  `"species_entry_id": 31`.
- If the caller supplied `species_entry_ref="spe_…"`, the echo
  carries `"species_entry_ref": "spe_…"`.
- Phase D.1 does **not** add resolved internal IDs to
  `request.filter`. Echoes mirror inputs, not resolution outcomes.

`request.include` echoes the post-validation include set. If
`internal_ids` was supplied and silently dropped (production policy),
it does **not** appear in the echo — that signals to the caller that
the token had no effect.

## Route behavior

Path handles continue to accept integer PKs (`/.../{42}/thermo`) and
public refs (`/.../spe_.../thermo`). Phase D **does not** restrict
input — only output. Specifically:

- Integer path handles: still accepted. Documented as
  compatibility-only; public examples use refs.
- Integer query filters (`level_of_theory_id`, `species_id`, etc.):
  still accepted. Conflict resolution against `*_ref` is unchanged
  (Phase C contract).
- ID/ref conflict 422s (`<resource>_handle_conflict`): unchanged.

## `tckdb-client` implications

The client already uses refs throughout (Phase C.1). For Phase D.1:

- **No new method signatures.** The existing `include: list[str] | None`
  parameter on every search method already accepts arbitrary include
  tokens. Callers opt in via `include=["…", "internal_ids"]`.
- **Examples & README:** continue to lead with refs. Phase D.1 should
  add a short "asking for internal IDs" subsection demonstrating
  `include=["internal_ids"]` for the compatibility/debugging case.
- **No major-version bump required** unless we decide to remove ID
  fields from public response shapes outright (we're not). Bumping
  to `0.9.0` is appropriate for the doc/example refresh accompanying
  Phase D.1.

## Documentation implications

Phase D.1 must update:

- [`clients/python/README.md`](../../clients/python/README.md)
  — note that responses ship refs-only by default; show `include=
  ["internal_ids"]` as the compatibility/debug opt-in.
- [`clients/python/examples/scientific_reads.py`](../../clients/python/examples/scientific_reads.py)
  — example's pretty-printers already handle `*_id` missing
  gracefully via `_ref_id()` (Phase C.1). Add a CLI flag
  `--include-internal-ids` that injects the token into every call.
- [`docs/guides/workflow_tool_scientific_reads.md`](../guides/workflow_tool_scientific_reads.md)
  — make explicit that integer IDs are opt-in via `include=internal_ids`.
- [`docs/guides/scientific_read_demo_data.md`](../guides/scientific_read_demo_data.md)
  — example queries should not assume integer IDs in responses.
- [`docs/specs/read_api_mvp.md`](./read_api_mvp.md) — add a Phase D
  section to the include-vocabulary table.
- [`docs/specs/species_calculation_search_api.md`](./species_calculation_search_api.md)
  — note Phase D shape for the bare integer arrays.
- [`docs/specs/public_identifier_policy.md`](./public_identifier_policy.md)
  — add a cross-reference to this doc.

## Test impact

Snapshot of test files that currently assert on integer `*_id` fields
in scientific responses. Phase D.1 must triage each into one of three
buckets: **(M)** mechanical update to assert on refs instead; **(C)**
add `include=["internal_ids"]` to keep id assertions valid as a
compatibility regression; **(K)** keep as-is (service-layer test that
exercises an internal helper, not a public response shape).

### Backend service tests (`backend/tests/services/scientific_read/`)

| File | Current ID dependency | Phase D.1 action |
|---|---|---|
| `test_phase_b_refs.py` | Asserts both `*_id` and `*_ref` present and matching per model | **K** — Phase B regression. Continue asserting ref/id pairs (this is the canonical contract). Add `include=["internal_ids"]` to its calls so the responses still carry both. |
| `test_phase_c_handles.py` | None (handle-parsing unit tests) | **K** — no change needed. |
| `test_search_species.py` | Asserts integer `species_id`, `species_entry_id` in records | **M** — switch to `species_ref` / `species_entry_ref`. |
| `test_search_reactions.py` | Asserts `reaction_id`, `reaction_entry_id`, `species_entry_id` | **M**. |
| `test_search_thermo.py` | Asserts `thermo_id`, `species_entry_id` | **M**. |
| `test_search_kinetics.py` | Asserts `kinetics_id`, `reaction_entry_id` | **M**. |
| `test_search_species_calculations.py` | Asserts `calculation_id`, `species_id`, `species_entry_id`, `geometry_id`, `conformer_observation_id` across many rows | **M** + bulk find/replace; the largest delta. |
| `test_get_species_thermo.py` | Asserts `thermo_id`, `species_entry_id` | **M**. |
| `test_get_reaction_kinetics.py` | Asserts `kinetics_id`, `reaction_entry_id`, TS-chain `calculation_id` | **M**. |
| `test_get_reaction_full.py` | Asserts `reaction_entry_id` (`id`), `species_entry_id`, `transition_state_entry_id`, `calculation_id` across all sections | **M** + use `reaction_entry_ref` / `transition_state_entry_ref` etc. |

### Backend API tests (`backend/tests/api/scientific/`)

| File | Current ID dependency | Phase D.1 action |
|---|---|---|
| `test_api_phase_c_refs.py` | Asserts both integer and ref forms (Phase C acceptance) | **K** — Phase C contract. May need `include=["internal_ids"]` on a subset to keep the id-form assertions; most assertions already prefer refs. |
| `test_api_species_search.py` | Asserts `species_id` etc. in response | **M**. |
| `test_api_reaction_search.py` | Asserts `reaction_id`, `reaction_entry_id` | **M**. |
| `test_api_species_thermo.py` | Asserts `species_entry_id` in envelope and records | **M**. |
| `test_api_reaction_kinetics.py` | Asserts `reaction_entry_id` in envelope | **M**. |
| `test_api_species_calculations_search.py` | Asserts `calculation_id`, `species_id`, `geometry_id`, `conformer_observation_id` | **M** — largest API delta. |
| `test_api_thermo_search.py` | Asserts `thermo_id`, `species_entry_id` | **M**. |
| `test_api_kinetics_search.py` | Asserts `kinetics_id`, `reaction_entry_id` | **M**. |
| `test_api_reaction_full.py` | Asserts `reaction_entry_id`, `species_entry_id`, `transition_state_entry_id`, `calculation_id` | **M**. |
| (new file) `test_api_phase_d_internal_ids.py` | n/a | **C** — new file. Verifies: (1) default response omits integer ids; (2) `include=internal_ids` restores them when allowed; (3) `include=internal_ids` is silently dropped (no 4xx) in the hosted default; (4) `include=all` does **not** include `internal_ids`. |

### Client tests (`clients/python/tests/`)

| File | Current ID dependency | Phase D.1 action |
|---|---|---|
| `test_phase_c_refs.py` | Asserts both forms (client serialization only) | **K** — these are request-shape tests, not response-shape tests. No change. |
| `test_examples_scientific_reads.py` | Asserts refs come first in printer output; pretty-printers already handle missing ids via `_ref_id()` fallback | **K** — extend smoke test to cover the `--include-internal-ids` CLI flag path. |
| `test_scientific.py` | Reads integer IDs as fixture inputs (not response assertions) | **K**. |
| `test_scientific_search.py` | Asserts on refs and integer IDs in response surfaces | **M** — drop id assertions unless explicitly testing `include=internal_ids`. |

**Estimated total change:** ~12 test files mechanically updated (**M**),
3 files left untouched (**K**), 1–2 new test files added (**C**).

## Response-shape transition

Three candidate mechanisms for omitting `*_id` fields from public
responses:

### (a) `model_dump(exclude=…)` at the route boundary  *— recommended*

The route builds the response model normally. Just before returning,
it calls `response.model_dump(exclude=EXCLUDE_INTERNAL_IDS)` (a
recursive exclude spec) and returns a `JSONResponse` or plain dict.

- ✅ Smallest schema delta (zero — keeps Pydantic models untouched).
- ✅ Centralized exclude set; one helper per response type.
- ❌ FastAPI's `response_model=…` decorator is bypassed at runtime
  (callers return a raw dict). OpenAPI docs still show the full
  schema, which is acceptable — the schema *can* include `*_id`,
  it just doesn't by default. We can mark `*_id` fields as
  `Optional` if we want OpenAPI to advertise that.

### (b) `response_model_exclude` per call

FastAPI supports `@router.get(..., response_model_exclude={...})` and
the runtime-version `response_model_exclude_unset`. We could set this
dynamically via `Response.headers` manipulation or a wrapper.

- ✅ Stays within the FastAPI response-model contract.
- ❌ Dynamic per-request exclusion is awkward — not designed for
  context-dependent exclude sets.

### (c) Paired "public" vs "internal" response schemas

Separate Pydantic classes (e.g. `ThermoRecordPublic`,
`ThermoRecordInternal`); route picks at runtime.

- ✅ Cleanest type signature; OpenAPI cleanly reflects the public
  shape by default.
- ❌ Doubles the schema maintenance burden. Every Phase B/C
  follow-up touches twice as many classes. Strongly discouraged for
  v0.

**Decision for Phase D.1:** Strategy (a). Add a module-level
`INTERNAL_ID_EXCLUDE` spec per response shape (or a shared visitor),
and a tiny helper:

```python
def strip_internal_ids(payload: BaseModel) -> dict[str, Any]:
    """Render the response with internal *_id fields stripped."""
    return payload.model_dump(exclude=INTERNAL_ID_EXCLUDE)
```

Returned from route handlers via a thin wrapper that consults
`should_include_internal_ids(resolved_includes, settings)`.

If OpenAPI consumers complain, revisit (c) for the next major version.

## Phase D.1 implementation plan

1. **Settings.** Add `ALLOW_PUBLIC_INTERNAL_IDS: bool = False` to
   `app.api.config.settings` (or wherever app settings live). Default
   `False` in production; tests/dev override to `True`.

2. **Include vocabulary.** Add `"internal_ids"` to
   `_LEGAL_INCLUDE_TOKENS` on each of the eight scientific services.
   Update `validate_includes()` (or its callers) so `include=all`
   does **not** expand to include `internal_ids`.

3. **Context helper.** In
   `backend/app/services/scientific_read/common.py`, add:

   ```python
   def should_include_internal_ids(
       resolved_includes: set[str],
       *,
       settings: Settings,
   ) -> bool:
       """Return True iff the response should carry *_id fields."""
       return "internal_ids" in resolved_includes and (
           settings.allow_public_internal_ids
       )
   ```

4. **Per-response exclude specs.** For each response model, define
   a constant nested-exclude dict spelling out every field to strip.
   Centralize per-shape in a module like
   `app/services/scientific_read/internal_ids.py`.

5. **Route boundary.** Each route handler wraps its return:

   ```python
   payload = search_thermo(session, request)
   if should_include_internal_ids(includes, settings=...):
       return payload  # full shape
   return JSONResponse(strip_internal_ids(payload))
   ```

   Routes that already build Pydantic models can either return the
   model (full shape) or the stripped dict.

6. **Bare-array stripping.** The exclude spec for `GeometryBlock`
   and `CalculationProvenanceBlock` includes the bare-list fields
   alongside the per-element `*_id` keys; object-array siblings stay.

7. **Tests.**
   - Add `test_api_phase_d_internal_ids.py`: asserts default shape
     omits `*_id` and bare arrays; `include=internal_ids` restores
     them when allowed; `include=all` does not include
     `internal_ids`; request echo never leaks resolved IDs.
   - Update the **M**-bucket tests to assert on `*_ref` (mechanical
     rename).
   - For **K**-bucket Phase B/C regression tests, pass
     `include=["internal_ids"]` so they keep exercising the dual-form
     contract.

8. **Client docs.**
   - Add a short "Internal IDs are opt-in" subsection to
     `clients/python/README.md`.
   - Extend `examples/scientific_reads.py` with an
     `--include-internal-ids` flag that injects the token into every
     call.

9. **Spec docs.** Cross-link this policy doc from
   `public_identifier_policy.md` and the phase roadmap; update the
   include-vocabulary table in `read_api_mvp.md`.

10. **Client version bump.** `0.8.x` → `0.9.0` (doc and example
    refresh; no signature changes).

## Non-goals

- Removing integer ID fields from the schema, ORM, or database.
- Hiding integer IDs from request inputs (path handles, query
  filters).
- Changing public-ref generation, format, or stability semantics.
- Introducing user-context-aware read routes (deferred until read
  auth lands).
- Changing ranking, filter, collapse, review, evidence, or provenance
  semantics.
- Introducing ARC/RMG/frontend-specific policy.
- Changing the `tckdb-client` method surface.

## Open questions

| # | Question | Recommendation (deferred to D.1 if blocking) |
|---|---|---|
| 1 | Should anonymous hosted users ever be allowed `include=internal_ids`? | No. `ALLOW_PUBLIC_INTERNAL_IDS=False` in production. |
| 2 | Should authenticated non-curator users see internal IDs? | Not by default. Curator role required once read auth exists. |
| 3 | Should local/dev mode expose IDs by default (no token required)? | No — keep the default refs-only everywhere so dev mirrors prod. Use the include token in dev too. |
| 4 | Should OpenAPI mark `*_id` fields as `Optional` after Phase D? | Yes — they may legitimately be absent in the default response. |
| 5 | Should integer route handles (`/.../{42}/thermo`) remain documented? | No — keep them functional but stop showing them in public docs. |
| 6 | Is a `tckdb-client` major version bump required for Phase D.1? | No — minor bump (`0.9.0`). Signatures unchanged. |
| 7 | Should `internal_ids` also gate `ReviewRecordEntry.record_id` (audit array, only with `include_review=full`)? | Yes — same gating. It is a raw PK, polymorphic, with no ref. |
| 8 | Should `internal_ids` also gate `submission_id` once we start populating it? | Yes — submissions are an internal/curator concept; default response should expose `submission_ref` only. |
| 9 | What status code do we return when an unauthorized caller supplies `include=internal_ids` in production? | Silent drop in v0 (mechanism 1); `403 internal_ids_not_allowed` once mechanism 2 lands. |
| 10 | Do bare-array forms ever surface anywhere else (e.g. in JSON arrays inside `provenance` not in this inventory)? | Not in the v0 schema — this audit is exhaustive across the eight read shapes. Re-audit if new endpoints land. |

---

*See also: [`docs/specs/public_identifier_policy.md`](./public_identifier_policy.md).*
