# TCKDB Trust and AI Review Read API Current Behavior

This document summarizes the backend read behavior currently available for
deterministic trust fragments and AI Review Assistant advisory results.

It is a current-state API note, not an implementation roadmap.

## Trust-Enabled Scientific Endpoints

Trust fragments are available only on these scientific read endpoints:

```text
GET /api/v1/scientific/calculations/{calculation_ref_or_id}?include=trust
GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics?include=trust
GET /api/v1/scientific/species-entries/{species_entry_id}/thermo?include=trust
```

Current implemented deterministic trust rubrics:

| Public rubric | Applies to |
| --- | --- |
| `computed_calculation_v1` | Calculation detail reads |
| `computed_kinetics_v1` | Reaction-entry kinetics reads |
| `computed_thermo_v1` | Species-entry thermo reads |

## Trust Include Behavior

Trust is opt-in. Default scientific read responses omit `trust`; callers must
request `include=trust`.

`include=all` does not include `trust`. This keeps the deterministic trust
fragment explicit even on read surfaces where `include=all` expands other
summary-safe sections.

Search/list endpoints do not expose trust fragments. Trust is currently a
detail/read-surface feature for the three endpoints listed above.

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

## AI Review Assistant Admin Read Surfaces

AI Review Assistant results are submission/admin scoped. The current read
surfaces are:

```text
GET /api/v1/submissions/{submission_id}/ai-review-summary
GET /api/v1/submissions/{submission_id}/audit-events
```

`/ai-review-summary` returns a compact latest-result card derived from the
newest `llm_precheck_recorded` submission audit event. If there is no matching
event, it returns `null`.

`/audit-events` remains the full-detail source of truth and exposes the stored
`details_json` for audit events according to the existing submission visibility
policy.

The AI Review Assistant summary follows the same submission visibility policy
as other submission reads: the creator can read their own submission, and
curators/admins can read submissions according to the existing submission
policy. Other users receive `403`.

## AI Review Assistant Boundary

AI Review Assistant output is advisory only.

AI review audit events:

- do not approve or reject submissions
- do not mutate submission status
- do not mutate scientific records
- do not alter deterministic evidence completeness
- do not alter `passed_checks`, `missing_checks`, `warning_checks`, or
  `not_applicable_checks`
- do not alter `hard_fail_reason`, `trust_status`, or public record visibility
- are not mapped into public calculation/kinetics/thermo `trust.llm_precheck`
  fragments yet

The fake/test provider may produce structured advisory results for test and
development flows, but no real model provider behavior is documented as
available here.

RAG is not documented as available behavior. Current AI Review Assistant
payloads may carry a `used_rag` flag, but no RAG implementation is provided by
the backend in the current shipped behavior.

## Non-Goals and Missing Surfaces

The current backend does not provide:

- trust fragments on search/list endpoints
- trust fragments through `include=all`
- statmech trust rubrics
- transport trust rubrics
- public scientific mapping from AI Review Assistant audit events into
  `trust.llm_precheck`
- real LLM provider behavior
- RAG behavior

Future work should keep the deterministic trust layer and AI Review Assistant
advisory layer separated unless an explicit public-trust mapping is designed,
implemented, and tested.
