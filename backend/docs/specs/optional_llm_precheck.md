# Optional LLM Precheck / AI Review Assistant

**Status:** draft spec - optional plumbing exists, persistence design only
**Date:** 2026-05-27
**Scope:** TCKDB backend only. No real LLM calls, persistence tables,
migrations, RAG, ARC changes, or `tckdb-client` changes.
**Audience:** TCKDB backend maintainers, deployment admins, future
precheck implementers.

---

## 1. Core Principle

> **TCKDB works fully with AI Review Assistant off.**

AI Review Assistant is an optional advisory layer. It is not part of the
deterministic trust/evidence layer, upload validity, moderation, approval,
rejection, read availability, or scientific correctness.

Optional precheck plumbing was introduced in commit
`0087c35 Add optional LLM precheck plumbing`. The current implementation
has disabled and fake providers, context-builder scaffolding, service
orchestration, and failure conversion to `failed_to_review`. It has no
upload workflow wiring, read API changes, persistence, real providers, or
RAG.

The default mode is:

```text
AI Review Assistant: Off
```

When off:

- uploads still work
- read APIs still work
- deterministic trust/evidence still works
- submission moderation still works
- no API key is required
- no local model is required
- no extra Docker service is required

---

## 2. Current Trust Contract

The current public trust contract remains unchanged:

- `trust` is opt-in via `include=trust`
- `include=all` intentionally excludes `trust`
- default responses omit `trust`
- search/list endpoints do not expose `trust`
- `llm_precheck` is always disabled/not_run until this optional layer is
  implemented and enabled
- `record_id` is hidden unless `include=internal_ids` is requested and
  allowed
- `trust` means evidence completeness, not scientific correctness

Current trust-enabled endpoints:

```text
GET /api/v1/scientific/calculations/{calculation_ref_or_id}?include=trust
GET /api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics?include=trust
GET /api/v1/scientific/species-entries/{species_entry_id}/thermo?include=trust
```

The deterministic trust evaluator remains the source of:

- `evidence_completeness`
- `passed_checks`
- `missing_checks`
- `warning_checks`
- `not_applicable_checks`
- `hard_fail_reason`
- `trust_status`

The LLM may consume these outputs as context, but it must not alter them.
The public `trust.llm_precheck` field remains advisory.

---

## 3. User-Facing Modes

The user-facing configuration model is:

```text
AI Review Assistant: Off / Cloud / Local
```

Provider jargon should not be the primary user-facing concept.

| Mode | User meaning | Requirements |
|---|---|---|
| `Off` | Default, free, no model, no API key. | No API key, local model, or Docker service. |
| `Cloud` | Easiest optional mode. Uses an external model API. | API key or gateway credentials. No extra Docker service. |
| `Local` | Advanced/private optional mode. Uses a local model server. | Base URL/model configuration and possibly extra compute or optional container setup. |

Internal mapping:

| User mode | Internal enabled flag | Internal provider |
|---|---:|---|
| `Off` | `LLM_PRECHECK_ENABLED=false` | `disabled` |
| `Cloud` | `LLM_PRECHECK_ENABLED=true` | `online_api` |
| `Local` | `LLM_PRECHECK_ENABLED=true` | `local_http` |
| Test-only | `LLM_PRECHECK_ENABLED=true` | `fake_test` |

`fake_test` is developer/test-only and must not be presented as a normal
user-facing mode.

---

## 4. LLM Boundaries

The LLM must not:

- approve submissions
- reject submissions
- mutate scientific records
- compute evidence completeness
- change kinetics, thermo, statmech, transport, geometry, or calculation
  values
- rewrite species or reaction identity
- hide data
- be required for upload success
- be required for read success
- be required for deterministic trust evaluation

The LLM may:

- summarize upload evidence
- summarize deterministic trust/evidence output
- highlight missing provenance
- flag suspicious inconsistencies
- produce curator-facing warnings
- suggest records that deserve attention

An LLM result is an advisory precheck note. It is never a verdict.

---

## 5. Non-Goals

These are explicitly out of scope for this spec and the MVP:

- real online provider implementation
- local model implementation
- RAG or vector database
- fine-tuning
- automatic approval/rejection
- scientific correctness certification
- frontend curator UI
- upload schema redesign
- ARC-specific behavior
- new trust rubrics
- search/list trust
- database tables or migrations

---

## 6. Proposed Backend Structure

Future implementation should live under:

```text
backend/app/services/llm_precheck/
  __init__.py
  schemas.py
  interface.py
  providers.py
  context_builder.py
  service.py
```

Conceptual responsibilities:

| Module | Responsibility |
|---|---|
| `schemas.py` | Internal Pydantic or typed result/context schemas. |
| `interface.py` | Provider protocol and common provider errors. |
| `providers.py` | Provider factory and provider-mode selection. No provider-specific MVP implementation required by this spec. |
| `context_builder.py` | Compact, structured submission context construction. |
| `service.py` | Orchestration, error handling, persistence, audit event creation. |

Core concepts:

- `LLMPrecheckProvider`
- `LLMPrecheckContext`
- `LLMPrecheckResult`
- `LLMFinding`
- `LLMPrecheckService`

Provider interface:

```python
class LLMPrecheckProvider(Protocol):
    """Provider interface for optional LLM-based submission prechecks."""

    def review_submission(
        self,
        context: LLMPrecheckContext,
    ) -> LLMPrecheckResult:
        """Return a structured advisory precheck result for a submission."""
```

Use sync or async according to backend conventions at implementation time.

---

## 7. Provider Modes

Internal provider modes:

```text
disabled
fake_test
online_api
local_http
```

Optional future concrete providers may include:

- `openai`
- `anthropic`
- `ollama`
- `vllm`
- `llama.cpp`
- `institutional_gateway`

This spec does not define provider-specific request formats, SDKs, model
names, retry semantics, billing behavior, or streaming.

---

## 8. Configuration

Prefer one user-facing setting:

```text
AI_REVIEW_ASSISTANT_MODE=off|cloud|local
```

The backend should derive internal behavior from this mode:

```text
AI_REVIEW_ASSISTANT_MODE=off   -> LLM_PRECHECK_ENABLED=false, provider=disabled
AI_REVIEW_ASSISTANT_MODE=cloud -> LLM_PRECHECK_ENABLED=true,  provider=online_api
AI_REVIEW_ASSISTANT_MODE=local -> LLM_PRECHECK_ENABLED=true,  provider=local_http
```

Suggested internal/admin settings:

```text
AI_REVIEW_ASSISTANT_MODE=off
LLM_PRECHECK_ENABLED=false
LLM_PRECHECK_PROVIDER=disabled
LLM_PRECHECK_MODEL=
LLM_PRECHECK_API_KEY_ENV=
LLM_PRECHECK_BASE_URL=
LLM_PRECHECK_TIMEOUT_SECONDS=30
LLM_PRECHECK_MAX_INPUT_TOKENS=6000
LLM_PRECHECK_MAX_OUTPUT_TOKENS=1200
LLM_PRECHECK_INCLUDE_ARTIFACT_TEXT=false
LLM_PRECHECK_INCLUDE_COORDINATES=false
LLM_PRECHECK_STORE_FULL_CONTEXT=false
```

Configuration validation:

- `Off` must require no API key, model, base URL, or extra service.
- `Cloud` must validate that required API-key configuration is present,
  preferably via `LLM_PRECHECK_API_KEY_ENV` naming an environment variable
  that contains the secret.
- `Local` must validate the required base URL and model configuration.
- Invalid mode values should fail application startup with a clear settings
  error.
- A deployment-level disable must override provider settings and force
  `disabled`.

---

## 9. Docker and Deployment Behavior

| Mode | Docker/deployment behavior |
|---|---|
| `Off` | No extra Docker service. Default compose stack stays unchanged. |
| `Cloud` | No extra Docker service. Backend makes an outbound API call if enabled and configured. |
| `Local` | Optional extra local model service may be used. If Docker is used, it must be behind an optional compose profile. Do not add a local LLM service to the default compose stack. |

User-facing docs should hide Docker/provider complexity behind:

```text
AI Review Assistant: Off / Cloud / Local
```

Normal users should not need to understand Docker profiles unless they
choose `Local`.

---

## 10. Context Builder

The context builder should send compact structured summaries, not raw logs
by default.

Include:

- submission metadata
- submission linked records
- record types and ids
- deterministic trust/evidence evaluations
- missing checks
- warning checks
- hard-fail reasons
- source calculation role summaries
- geometry validation summaries
- artifact kind summaries
- calculation parameter summaries
- temperature/unit summaries

Avoid by default:

- full Gaussian logs
- full ORCA logs
- full artifacts
- huge coordinate blocks
- private notes
- secrets or environment variables
- API keys

Optional payload expansion flags:

- `LLM_PRECHECK_INCLUDE_ARTIFACT_TEXT=false`
- `LLM_PRECHECK_INCLUDE_COORDINATES=false`
- `LLM_PRECHECK_STORE_FULL_CONTEXT=false`

Even when those flags are enabled in a controlled deployment, context size
limits and redaction still apply.

Context too large behavior:

- Prefer compacting summaries before failing.
- If the compacted context still exceeds `LLM_PRECHECK_MAX_INPUT_TOKENS`,
  return/persist `label=failed_to_review` with a summary indicating that
  the context was too large.
- Do not fail the upload.

---

## 11. Structured Output Schema

Desired provider result shape:

```json
{
  "label": "not_run | pass | warning | needs_attention | failed_to_review",
  "summary": "Short curator-facing summary.",
  "findings": [
    {
      "severity": "info | warning | critical",
      "category": "provenance | units | geometry | kinetics | thermo | statmech | calculation_parameters | consistency",
      "record_type": "kinetics",
      "record_id": 123,
      "message": "No IRC source calculation is linked.",
      "evidence_keys": [
        "missing_checks.irc_evidence_present"
      ]
    }
  ],
  "model": "provider/model-name",
  "used_rag": false
}
```

Labels:

| Label | Meaning |
|---|---|
| `not_run` | Assistant disabled or intentionally skipped. |
| `pass` | No notable advisory concerns found. Does not mean approved. |
| `warning` | Advisory issues found that may deserve curator review. |
| `needs_attention` | Strong advisory signal that a curator should inspect the submission. |
| `failed_to_review` | Provider timeout/error, malformed output, context too large, or other precheck failure. |

Model output must be schema-validated before persistence. Malformed output
becomes:

```text
label=failed_to_review
```

Malformed output must not fail the upload.

The result schema should also enforce:

- `summary` length bounds
- known enum values only
- bounded number of findings
- bounded message length per finding
- no provider-supplied mutation payloads
- `used_rag=false` for MVP

---

## 12. Persistence

Persistence is intentionally deferred until the advisory result vocabulary
is reconciled with the deployed submission schema. The existing
`SubmissionPrecheckLabel` enum only supports:

```text
passed
flagged
```

The optional AI Review Assistant result vocabulary is:

```text
not_run
pass
warning
needs_attention
failed_to_review
```

Persistence must preserve these rules:

- LLM precheck is advisory only.
- LLM precheck must not approve submissions.
- LLM precheck must not reject submissions.
- LLM precheck must not mutate `submission.status`.
- LLM precheck must not mutate scientific records.
- LLM precheck must not compute evidence completeness.
- LLM failure must not fail an otherwise valid upload.
- Off mode must require no persistence writes.

The existing `mark_precheck_result` helper is not appropriate for the AI
Review Assistant because it mutates submission moderation status. Future
persistence wiring must use a new advisory-only path or refactor that
helper before reuse.

### Option A: Expand `submission.llm_precheck_label`

Expand the existing submission enum-backed column to support:

```text
not_run
pass
warning
needs_attention
failed_to_review
```

Current related columns:

```text
submission.llm_precheck_label
submission.llm_precheck_summary
submission.llm_precheck_model
submission.llm_precheck_at
```

Pros:

- Simple query and filter by the latest precheck label.
- Easy admin dashboard badge.
- Uses existing submission columns.

Cons:

- Requires an enum migration.
- Overloads existing field semantics because the old labels are
  `passed`/`flagged`, while the advisory vocabulary is
  `pass`/`warning`/`needs_attention`.
- May require backfill or mapping.
- Risks confusing an advisory label with moderation status.

Label mapping should be avoided unless backward compatibility requires it.
If mapping is required, it is lossy:

```text
passed  -> pass
flagged -> warning or needs_attention
```

`flagged` cannot be deterministically mapped without knowing whether the
old result was a mild warning or a stronger curator-attention signal.

### Option B: Audit-Event-Only Persistence

Store the full structured advisory result in:

```text
submission_audit_event.details_json
```

with an event kind such as:

```text
llm_precheck_completed
llm_precheck_failed
```

If those event kinds do not exist, there are three choices:

1. Reuse the closest existing precheck event kind only if its name does not
   misrepresent the advisory result. The current
   `llm_precheck_passed`/`llm_precheck_flagged` kinds are too narrow for
   `not_run`, `needs_attention`, and `failed_to_review`.
2. Add new audit event kinds in a small Alembic revision when persistence
   is implemented.
3. Defer audit persistence until a migration is justified.

Suggested audit event detail shape:

```json
{
  "kind": "llm_precheck_completed",
  "label": "warning",
  "summary": "Two provenance gaps found.",
  "model": "provider/model-name",
  "used_rag": false,
  "findings": [],
  "context_summary": {
    "record_count": 3,
    "record_types": ["calculation", "kinetics"],
    "included_artifact_text": false,
    "included_coordinates": false
  }
}
```

Pros:

- Append-only.
- No schema change if a suitable event kind already exists and
  `details_json` can store the structured result.
- Preserves history.
- Does not overload submission status.
- Good MVP shape.

Cons:

- Harder to filter by latest precheck label.
- Requires reading the latest relevant audit event for a submission.
- May need event-kind extension if no existing kind is suitable.

### Option C: Dedicated `submission_llm_precheck` Table

A future table could represent precheck attempts directly:

```text
submission_llm_precheck
  id
  submission_id
  label
  summary
  model
  provider
  used_rag
  result_json
  context_hash
  created_at
```

Pros:

- Clean domain model.
- Queryable.
- Versionable.
- Supports multiple attempts and providers.

Cons:

- Requires a migration.
- Adds more schema surface.
- Probably premature before upload wiring, curator UI, and read behavior
  are settled.

### Recommended MVP

Use Option B as the MVP persistence strategy:

- Write an append-only audit event containing the full structured result.
- Do not mutate `submission.status`.
- Do not update `submission.llm_precheck_label` until enum semantics are
  resolved.
- Optionally update only `submission.llm_precheck_summary`,
  `submission.llm_precheck_model`, and `submission.llm_precheck_at` if the
  implementation can do so through an advisory-only path that never touches
  moderation status.
- In off mode, write nothing by default. Absence of an audit event means
  `not_run` unless an explicit product requirement later asks to record
  skipped checks.
- Do not add a dedicated table for the MVP.

Because the current audit event enum has only `llm_precheck_passed` and
`llm_precheck_flagged`, the recommended implementation slice should either
add neutral event kinds such as `llm_precheck_completed` and
`llm_precheck_failed` via a new Alembic revision, or defer persistence.
Reusing `llm_precheck_passed` for advisory `pass` is acceptable only if the
event documentation makes clear that it is not approval; however it still
does not cover `not_run`, `needs_attention`, or `failed_to_review`, so a
neutral event kind is preferred.

A future `submission_llm_precheck` table may be considered only if there
is a clear query need, such as retaining many precheck attempts per
submission with filterable structured findings. That would require a new
Alembic revision. Do not edit the initial schema migration.

---

## 13. Failure Behavior

| Scenario | Required behavior |
|---|---|
| AI Review Assistant off | Return `not_run` where a runtime result object is needed. Do not require provider config. Do not write persistence rows by default. |
| Provider timeout | Mark `failed_to_review`, write advisory metadata/audit event, do not fail upload. |
| Provider error | Mark `failed_to_review`, write advisory metadata/audit event, do not fail upload. |
| Malformed model output | Schema validation fails into `failed_to_review`, do not fail upload. |
| Context too large | Compact if possible; otherwise `failed_to_review`, do not fail upload. |
| Submission has no linked records | Prefer `not_run` or `pass` with a summary explaining that there were no linked scientific records to inspect; do not fail upload. |
| Database rollback | Precheck persistence rolls back with the surrounding transaction; no separate durability guarantee in MVP. |

Requirements:

- LLM failure must not fail an otherwise valid upload.
- LLM failure must be visible as advisory metadata or an audit event.
- LLM failure must be testable with fake providers.
- No failure mode may mutate scientific records.

---

## 14. Security and Privacy

Security rules:

- Do not send raw artifacts by default.
- Do not send full unpublished logs by default.
- Do not send secrets, API keys, or environment variables.
- Redact obvious credentials before provider calls.
- Limit context size.
- Record provider/model metadata.
- Allow deployment-level disable.
- Store only summaries by default.
- Treat provider responses as untrusted input and schema-validate them.
- Never execute tool calls or provider-suggested actions from the model.

Privacy posture by mode:

| Mode | Privacy behavior |
|---|---|
| `Off` | No model receives data. |
| `Cloud` | Compact structured context may leave the deployment boundary. Admin docs must make this explicit. |
| `Local` | Context is sent to the configured local model endpoint. Operators are responsible for local server access controls and retention settings. |

---

## 15. Relationship to RAG

RAG is future optional work.

RAG may later retrieve:

- TCKDB docs
- unit policy
- curation guidelines
- parser vocabulary notes
- workflow-tool-neutral chemistry conventions

If added later:

- RAG must use curated/versioned documents.
- RAG output must be cited internally in the structured result if used.
- RAG must not be required for the MVP.
- `used_rag` must remain `false` for MVP results.

---

## 16. Public Trust Fragment Relationship

The public `trust.llm_precheck` fragment should remain advisory and
opt-in through `include=trust` on endpoints that already expose trust.

The LLM precheck result may be summarized under `trust.llm_precheck`, but
it must not affect:

- `evidence_completeness`
- `passed_checks`
- `missing_checks`
- `warning_checks`
- `not_applicable_checks`
- `hard_fail_reason`
- `trust_status`
- record visibility
- upload success
- read success

`record_id` remains hidden unless `include=internal_ids` is requested and
allowed.

For the persistence MVP, public/read `trust.llm_precheck` may remain
`disabled`/`not_run` until persistence is wired cleanly. When exposed
later, the source should be chosen in this order:

1. Latest submission precheck audit event for the append-only MVP.
2. Submission summary fields only if the advisory-only summary update path
   is implemented.
3. A dedicated future table only if Option C is later justified.

Retrieval must be deterministic, using event timestamp plus primary key as
a tie-breaker when reading the latest audit event.

---

## 17. Tests Required Later

Future implementation should include tests proving:

- disabled/off mode writes nothing, or writes `not_run` only if explicitly
  requested by the selected implementation
- disabled/off mode requires no API key
- fake provider writes an advisory result without changing
  `submission.status`
- provider failure writes `failed_to_review` as an advisory result without
  changing `submission.status`
- malformed provider output becomes `failed_to_review`
- provider timeout does not fail upload
- full structured result appears in `submission_audit_event.details_json`
- summary/model/time fields update only if that optional summary path is
  selected by the design
- scientific records are not mutated
- submission approval/rejection status is not changed
- latest result can be retrieved deterministically
- context builder excludes raw artifacts by default
- context builder includes deterministic evidence output
- Cloud mode validates required API-key configuration
- Local mode validates required base URL/model configuration

Additional useful coverage:

- `include=all` still excludes trust and LLM precheck
- read APIs still work when precheck provider config is absent
- deterministic trust values are identical before and after precheck
- full coordinates are excluded unless explicitly enabled
- obvious credentials are redacted from context and audit details

---

## 18. Documentation Requirements

Main install docs should say only:

```text
TCKDB works without AI Review Assistant.
AI Review Assistant is optional.
It can summarize uploads and highlight missing evidence.
It never approves, rejects, or changes scientific data.
```

Detailed provider, Docker, API-key, model, gateway, timeout, and privacy
configuration belongs in admin docs.

User-facing docs should use:

```text
AI Review Assistant: Off / Cloud / Local
```

Admin/developer docs may mention:

```text
disabled
fake_test
online_api
local_http
```

---

## 19. Open Design Questions

1. Should `not_run` be persisted on every submission when the assistant is
   off, or should absence of precheck metadata imply not run? MVP answer:
   absence should imply `not_run`; off mode writes nothing by default.
2. Should precheck run inside the upload transaction, after commit, or as
   a background job? The failure contract is the same either way: LLM
   failure must not fail a valid upload.
3. Should `submission_audit_event.details_json` store full findings in
   MVP, or only a compact result summary? MVP recommendation: store the
   validated structured result, with context represented only by compact
   summary/hash metadata unless explicitly configured otherwise.
4. What maximum number of findings should be accepted from a provider?
5. Should `needs_attention` map to any existing moderation queue filter,
   or remain purely informational until a curator UI exists?
6. Which deterministic trust outputs should be included for multi-record
   submissions when a submission creates calculations plus downstream
   kinetics/thermo records?
7. Should local provider health checks run at startup or lazily at first
   precheck?
8. Should new neutral audit event kinds be added before persistence, or
   should persistence remain deferred until a broader submission audit
   migration is scheduled?

---

## 20. Recommended Implementation Order

1. Keep the existing disabled/fake provider plumbing advisory-only and
   unwired from uploads.
2. Add neutral audit event kinds such as `llm_precheck_completed` and
   `llm_precheck_failed` in a new Alembic revision only when persistence is
   explicitly implemented.
3. Add an advisory-only persistence helper that appends audit events and
   never mutates `submission.status`.
4. Optionally update `submission.llm_precheck_summary`,
   `submission.llm_precheck_model`, and `submission.llm_precheck_at` from
   that helper if a latest-result shortcut is needed.
5. Leave `submission.llm_precheck_label` unchanged until the enum
   migration/mapping decision is made.
6. Add deterministic latest-result retrieval from audit events.
7. Wire upload workflow invocation only after proving LLM failure cannot
   fail an otherwise valid upload.
8. Keep public read fragments as `disabled`/`not_run` until the persistence
   source is selected and covered by tests.
9. Add Cloud and Local provider implementations later, behind explicit
   configuration.
