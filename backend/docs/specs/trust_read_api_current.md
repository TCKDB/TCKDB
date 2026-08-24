# TCKDB Trust and AI Review Read API Current Behavior

This document summarizes the backend read behavior currently available for
deterministic scientific trust fragments and AI Review Assistant advisory
results.

It is a current-state API note, not an implementation roadmap.

## Trust-Enabled Scientific Endpoints

Trust fragments are available only on these scientific detail/read endpoints:

```text
GET /api/v1/scientific/calculations/{calculation_ref_or_id}?include=trust
GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics?include=trust
GET /api/v1/scientific/species-entries/{species_entry_id}/thermo?include=trust
GET /api/v1/scientific/species-entries/{species_entry_id}/statmech?include=trust
GET /api/v1/scientific/species-entries/{species_entry_id}/transport?include=trust
GET /api/v1/scientific/statmech/{statmech_ref_or_id}?include=trust
GET /api/v1/scientific/transport/{transport_ref_or_id}?include=trust
GET /api/v1/scientific/transition-state-entries/{transition_state_entry_ref_or_id}?include=trust
GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/full?include=trust
```

The composite reaction-entry `/full` read does not own its own rubric.
When `include=trust` is requested, it propagates trust fragments down to
the embedded records that already have a real rubric: embedded kinetics
records carry `computed_kinetics_v1`, embedded calculation summaries
under the `calculations` section carry `computed_calculation_v1`, and
embedded transition-state-entry records under the `transition_states`
section carry `computed_transition_state_v1`. No top-level
reaction-entry trust is emitted, and no top-level transition-state
*concept* trust is emitted (no aggregation rubric exists yet).

Current implemented deterministic trust rubrics:

| Public rubric | Applies to |
| --- | --- |
| `computed_calculation_v1` | Calculation detail reads |
| `computed_kinetics_v1` | Reaction-entry kinetics reads |
| `computed_thermo_v1` | Species-entry thermo reads |
| `computed_statmech_v1` | Statmech detail reads and species-entry statmech reads |
| `computed_transport_v1` | Transport detail reads and species-entry transport reads |
| `computed_transition_state_v1` | Standalone transition-state-entry detail reads |

Public rubric names are versioned. Clients should treat the full rubric string
as the stable public identifier for the scoring contract used by that response.

## Trust Include Behavior

Trust is opt-in. Default scientific read responses omit `trust`; callers must
request `include=trust`.

`include=all` intentionally does not include `trust`. This keeps the
deterministic trust fragment explicit even on read surfaces where `include=all`
expands other summary-safe sections.

Five search endpoints expose trust fragments: `/scientific/thermo/search`,
`/kinetics/search`, `/transition-states/search`, `/statmech/search` and
`/transport/search`. Each of them already declared a `trust` field on its
record shape while rejecting the token that would fill it, so the field was
one no request could ever populate — permanently `null` on three of them,
permanently absent on the other two. Every other search/list endpoint still
has no trust fragment, because its record shape declares none.

Where the token is legal on a search surface, the service loads the evidence
graph **once per page** rather than once per record, and `include=all` still
does not expand to `trust` — the graph runs from 9 chain entries on transport
to 23 on transition-states, and a caller has to ask for that by name.

One surface publishes entry records without publishing their rubric.
`GET /scientific/transition-states/{ref}?include=entries` returns
`ScientificTransitionStateEntryRecord`s, which declare `trust`, on an operation
whose vocabulary has no `trust` token — the concept has no aggregation rubric.
Those nested `trust` keys used to serialize as `null`, which read as "the entry
has no verdict" rather than "this operation cannot produce one". They are now
**absent**; the verdict is one request away on
`/scientific/transition-state-entries/{ref}?include=trust`.

Internal database ids remain hidden by default. In trust evidence payloads,
`record_id` is hidden unless `include=internal_ids` is explicitly requested and
the deployment/user policy allows internal-id exposure.

The trust fragment reports evidence completeness and review state. It is not a
claim of scientific correctness, and it does not replace curator review or
domain validation.

The public scientific `trust.llm_precheck` fragment remains disabled:

```json
{
  "enabled": false,
  "label": "not_run",
  "summary": null
}
```

This is true even when a submission has AI Review Assistant audit events.

## Endpoint Contract

### Calculation Detail

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/calculations/{calculation_ref_or_id}` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_calculation_v1` |
| Default behavior | Without `include=trust`, the response omits `record.trust`. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | `include=all` does not include trust. Calculation trust evaluates deterministic evidence completeness for the calculation record and attached provenance, not scientific correctness. |

### Reaction-Entry Kinetics

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_kinetics_v1` |
| Default behavior | Without `include=trust`, each kinetics record omits `trust`. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | Trust is computed for returned kinetics records only. `GET`/`POST /scientific/kinetics/search` also accept `include=trust`, at `records[*].kinetics.trust` — the field is one level down, inside the `kinetics` wrapper. `include=all` does not expand to trust on either surface. |

### Species-Entry Thermo

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/species-entries/{species_entry_id}/thermo` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_thermo_v1` |
| Default behavior | Without `include=trust`, each thermo record omits `trust`. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | Trust is computed for returned thermo records only. `GET`/`POST /scientific/thermo/search` also accept `include=trust`, at `records[*].thermo.trust` — the field is one level down, inside the `thermo` wrapper. `include=all` does not expand to trust on either surface. |

### Statmech Detail

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/statmech/{statmech_ref_or_id}` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_statmech_v1` |
| Default behavior | Without `include=trust`, the response omits `record.trust`. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | `include=all` does not include trust. `GET`/`POST /scientific/statmech/search` also accept `include=trust`, at `records[*].trust`; the search path loads the 19-entry evidence graph once per page. |

### Transport Detail

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/transport/{transport_ref_or_id}` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_transport_v1` |
| Default behavior | Without `include=trust`, the response omits `record.trust`. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | `include=all` does not include trust. `GET`/`POST /scientific/transport/search` also accept `include=trust`, at `records[*].trust`; the search path loads the 9-entry evidence graph once per page. |

### Species-Entry Statmech

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/species-entries/{species_entry_id}/statmech` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_statmech_v1` (per returned record) |
| Default behavior | Without `include=trust`, each statmech record omits `trust`. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | `include=all` does not include trust. A species-entry-scoped subresource read (mirrors species-entry thermo), not a broad search; it reuses the statmech record projection and deterministic ordering. `/scientific/statmech/search` exposes trust under the same token. |

### Species-Entry Transport

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/species-entries/{species_entry_id}/transport` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_transport_v1` (per returned record) |
| Default behavior | Without `include=trust`, each transport record omits `trust`. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | `include=all` does not include trust. A species-entry-scoped subresource read (mirrors species-entry thermo), not a broad search; it reuses the transport record projection and deterministic ordering. `/scientific/transport/search` exposes trust under the same token. |

### Transition-State Entry Detail (standalone)

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/transition-state-entries/{transition_state_entry_ref_or_id}` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` |
| Rubric used | `computed_transition_state_v1` |
| Default behavior | Without `include=trust`, the response omits `record.trust` and is byte-identical to its pre-trust shape. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. |
| Notes/limitations | `include=all` does not include trust. Trust is wired to the two **entry-grained** surfaces: this one and `GET`/`POST /scientific/transition-states/search`, where it sits at `records[*].trust`. The parent **TS-concept** detail (`/scientific/transition-states/{ref}`, including its embedded `entries`) still rejects the token with 422 `unknown_include_token` and never populates `trust` — a concept is a collection of entries evaluated at different levels of theory, and one verdict for the collection would be an aggregation rather than a reading. The search path loads the 23-entry evidence graph once per page, never once per record. Frequency policy is status-aware: `optimized`/`validated` TS entries with `n_imag` in `{0, >1}` hard-fail; `guess`-stage entries with the same signal only lower evidence completeness. IRC and path-search evidence are additive (missing is never a hard fail in v1). TS-entry trust is **also** propagated into the composite `/reaction-entries/{id}/full` read under `transition_states[*].trust` (same `computed_transition_state_v1` fragment) — see the Reaction-Entry Full row below. |

### Reaction-Entry Full (composite)

| Field | Behavior |
| --- | --- |
| Path | `GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/full` |
| Include syntax | `?include=trust`; may be combined with allowed include tokens such as `?include=trust,internal_ids` and section tokens like `?include=calculations,trust` |
| Rubric used | Embedded kinetics records carry `computed_kinetics_v1`; embedded calculation summaries carry `computed_calculation_v1`; embedded transition-state-entry records (`transition_states[*]`) carry `computed_transition_state_v1`. No top-level reaction-entry rubric exists, and no top-level transition-state *concept* rubric exists. |
| Default behavior | Without `include=trust`, every embedded kinetics record, calculation summary, and transition-state-entry record omits `trust`. The default response shape is unchanged from the pre-propagation behavior. |
| Internal IDs | `trust.evidence.record_id` is hidden by default and is exposed only when `include=internal_ids` is requested and allowed. The policy applies recursively to every embedded trust block. |
| Notes/limitations | `include=all` does not include trust. Trust is only attached to embedded records that already have a real deterministic rubric (kinetics, calculations, transition-state entries); conformer, path-search, IRC, scan, artifact, and review-records sections do not gain trust here, and neither does the top-level reaction entry. `trust.llm_precheck` on every embedded trust block ships disabled (`{enabled:false, label:"not_run"}`). |

## Common Trust Fragment Shape

The same public trust envelope is used across the trust-enabled scientific read
surfaces. Values vary by record and rubric, but the shape is:

```json
{
  "trust": {
    "review_status": "not_reviewed",
    "trust_status": "...",
    "evidence": {
      "record_type": "kinetics",
      "record_id": null,
      "rubric": "computed_kinetics_v1",
      "rubric_version": 1,
      "label": "well_supported",
      "checks": {},
      "passed_count": 0,
      "possible_count": 0,
      "evidence_completeness": 0.0,
      "is_certified": false
    },
    "llm_precheck": {
      "enabled": false,
      "label": "not_run",
      "summary": null
    },
    "is_certified": false
  }
}
```

`record_id` is shown as `null` here to illustrate the hidden-default public
contract. In actual default responses the field may be omitted; it is present
only when internal ids are requested and allowed. Public rubric names carry the
version suffix, such as `_v1`; `rubric_version` is the numeric version field
currently returned by the backend.

## AI Review Assistant Admin Read Surfaces

AI Review Assistant results are submission/admin scoped. The current read
surfaces are:

```text
GET /api/v1/submissions/{submission_id}/ai-review-summary               -- submission visibility
GET /api/v1/submissions/{submission_id}/audit-events                    -- submission visibility
GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection -- admin only
```

`/ai-review-summary` returns a compact latest-result card derived from the
newest `llm_precheck_recorded` submission audit event. If there is no matching
event, it returns `null`.

`/audit-events` exposes the full stored audit details, including
`details_json`, according to the existing submission visibility policy.

`/admin/.../machine-review-inspection` is an **admin-only** raw diagnostic
view. It projects a submission's `llm_precheck_recorded` events onto the
records linked to that submission and returns per-record latest summaries plus
`unmapped_findings_count`, `mapping_warnings`, `parse_warnings`, and
`source_audit_event_ids`. It is read-only, persists nothing, and emits its own
admin-only schema — **not** a public `TrustFragment`. It is a private debugging
surface for deciding whether to expose machine review publicly later; it is
**not** public scientific trust and **not** a curator workflow. Full contract:
`admin_machine_review_inspection.md`. The full layering map is
`provisional_machine_review.md` §0.

The first two endpoints follow the same submission visibility policy as other
submission reads: the creator can read their own submission, and
curators/admins can read submissions according to the existing submission
policy. Other users receive `403`. The inspection endpoint is stricter —
`require_admin`: anonymous callers get `401`, and normal users *and curators*
get `403`. Curators are deliberately not granted inspection access yet.

## AI Review Assistant Boundary

AI Review Assistant output is advisory only.

AI review audit events:

- are submission/admin scoped
- do not approve or reject submissions
- do not mutate submission status
- do not mutate scientific records
- do not alter deterministic evidence completeness
- do not alter `checks` (the check-name → outcome map)
- do not alter `hard_fail_reason`, `trust_status`, or public record visibility
- are not mapped into public scientific `trust.llm_precheck` fragments

The fake/test provider may produce structured advisory results for test and
development flows, but no real model provider behavior is documented as
available here.

RAG is not documented as available behavior. Current AI Review Assistant
payloads may carry a `used_rag` flag, but no RAG implementation is provided by
the backend in the current shipped behavior.

## Non-Goals and Missing Surfaces

The current backend does not provide:

- trust fragments on the broad search/list endpoints that do **not** declare a `trust` field (calculations, species-calculations, conformers, networks, network-kinetics, network-solves, artifacts, structure, energy-correction-schemes, frequency-scale-factors). The five that do — thermo, kinetics, transition-states, statmech and transport — accept `include=trust`; `include=all` reaches it on none of them.
- trust fragments through `include=all`
- transport trust rubrics
- experimental trust rubrics
- real LLM provider behavior
- RAG behavior
- record-level mapping from AI Review Assistant audit events into public
  scientific `trust.llm_precheck` fragments
- a public `trust.machine_review` fragment (none exists; the admin
  machine-review inspection endpoint is private diagnostics, not public trust,
  and must not be inferred as a public record-level state)

Future work should keep the deterministic trust layer and AI Review Assistant
advisory layer separated unless an explicit public-trust mapping is designed,
implemented, and tested.
