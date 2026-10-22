# Machine-Review Real Provider Plumbing

**Status:** design / spec only. No provider implementation, no real API calls to
OpenAI / Anthropic / local models, no public API change, no migration, no public
`trust.machine_review`, no automatic task creation, no ARC / `tckdb-client`
change. Designs how a *real* (Off / Cloud / Local) machine-review provider would
be wired in front of the already-implemented contract + adapter dispatch.
**Date:** 2026-05-31
**Scope:** TCKDB backend design only. The producer side (provider + context +
config validation) that emits payloads; everything from
`submission_audit_event.details_json` downstream is unchanged.
**Audience:** the maintainer who will implement off/cloud/local provider
plumbing (handoff §11 "Option 2"), and whoever later turns on a real model.

**Related specs:**

- `machine_review_provider_contract.md` — the provider output contract
  (`MachineReviewProviderResult` / `MachineReviewProviderFinding` /
  `MACHINE_REVIEW_SCHEMA_VERSION`) that real providers must emit. This spec
  is the *producer* in front of that contract; that spec is the *wire shape*.
- `optional_llm_precheck.md` — the advisory precheck plumbing
  (`AI_REVIEW_ASSISTANT_MODE`, `LLM_PRECHECK_*` settings, the context builder in
  `llm_precheck/`, the failure contract) this spec reuses and extends.
- `machine_review_handoff.md` — the workstream checkpoint; this spec fills in
  its §11 "Option 2: implement real provider plumbing behind off/cloud/local".
- `provisional_machine_review.md` — the machine-review vocabulary and the
  public-exposure gate this spec stays behind.
- `machine_review_golden_examples.md` — the fake-provider golden fixtures; the
  fake provider stays the test-only producer this spec keeps separate.

---

## 1. Problem statement

The machine-review **consumer** side is complete and tested: a payload on a
`submission_audit_event` flows through `audit_adapter.py` (which requires the
`format` marker) →
`MachineReviewResult` → `map_findings_to_submission_records` →
`RecordMachineReview` → `MachineReviewRecordSummary` → admin inspection →
`build_curator_tasks_for_submission` → the admin curator-task API.

The producer is the provider layer in front of that contract. The only providers
are:

```text
DisabledMachineReviewProvider   off; returns not_run, writes nothing
FakeMachineReviewProvider       test-only; deterministic output, never selectable
                                through AI_REVIEW_ASSISTANT_MODE
cloud / local                   built by build_machine_review_provider(settings);
                                missing configuration raises
                                MachineReviewProviderConfigurationError
```

This spec describes the producer so that:

```text
Off    -> no model call, no API key, no extra service (writes nothing)
Cloud  -> external model API emits a payload, validated, persisted
Local  -> local HTTP model server emits a payload, validated, persisted
```

It does **not** implement any of it. It answers the eight questions the handoff
left open for Option 2:

```text
How should Off / Cloud / Local modes work?
Where should provider implementations live?
What prompt/input context should providers receive?
What exact output schema must they return?
How are failures handled?
What happens to a payload in any other format?
What remains fake/test-only?
What must be tested before turning any of it on?
```

---

## 2. The one invariant that does not move

Real providers change the *producer*, never the *contract* or the *consumer*:

```text
The wire shape is frozen: providers MUST emit MachineReviewProviderResult
  (root `format` marker `{"name": "machine_review", "version": 1}`). No new payload shape is introduced here.
Provider output is UNTRUSTED. It is schema-validated at the service boundary
  before any persistence. Malformed output degrades to a failed review.
A provider CANNOT approve / reject / certify / hide / mutate. It writes nothing
  authoritative: no RecordReviewStatus, no submission.status, no is_certified,
  no benchmark_reference, no evidence, no public trust, no curator task.
Provider failure NEVER fails an upload and NEVER mutates a submission or record.
Everything downstream of submission_audit_event.details_json is unchanged.
```

If implementing this spec ever requires touching the adapter, the mapping, the
inspection projection, the curator-task services, or any public schema, stop —
that is out of scope and a sign the producer is reaching past its boundary.

---

## 3. Mode model (Off / Cloud / Local)

Reuse the existing user-facing mode model from `optional_llm_precheck.md` §3 — it
is already in `config.py` as `AI_REVIEW_ASSISTANT_MODE` and drives
`build_machine_review_provider()`:

| User mode | Meaning | Requirements | Internal provider |
|---|---|---|---|
| `Off` | Default. Free, no model. | None. | `disabled` |
| `Cloud` | External model API. | API key / gateway config. | `online_api` |
| `Local` | Local HTTP model server. | Base URL + model config. | `local_http` |

```text
Off    -> no model call, no API key env, no extra service, writes nothing
          (absence of an audit event already means not_run; handoff §3).
Cloud  -> outbound HTTPS to an external model API. API key/gateway config
          required. No extra Docker service. Compact context leaves the
          deployment boundary (admin docs must say so explicitly).
Local  -> HTTP to a local/in-cluster model server. Base URL + model required.
          Any container is behind an OPTIONAL compose profile; never added to
          the default stack (optional_llm_precheck.md §9).
```

`fake_test` stays a **developer/test-only** internal provider, reachable only via
`AI_REVIEW_ASSISTANT_MODE=test` (already a fourth `Literal` value in
`config.py:15`) or by passing a provider object directly into the service in a
test. It is **not** a deployer-facing mode and must never appear in user docs.

---

## 4. Configuration

### 4.1 Decision: reuse `LLM_PRECHECK_*`, do not mint a parallel namespace

The high-level switch stays `AI_REVIEW_ASSISTANT_MODE=off|cloud|local`. For the
provider-specific knobs, **reuse the existing `LLM_PRECHECK_*` settings** already
declared and wired in `config.py:156-169` rather than introducing a parallel
`MACHINE_REVIEW_*` namespace.

Rationale:

```text
The LLM_PRECHECK_* settings already exist, are typed, are documented inline,
  and are the settings the producer consults.
A second MACHINE_REVIEW_* namespace would be two config surfaces to keep
  aligned, two sets of env vars every deployment's .env / compose / Pi config
  would have to learn, and a silent-drift risk (set one, forget the other).
The producer changes; the deployer-facing config concept ("AI Review
  Assistant: Off/Cloud/Local, with a model and a key") does not.
Renaming env vars is a coordinated, cross-deployment migration (hosted, lab,
  Pi/self-hosted). Not worth it to relabel "llm_precheck" as "machine_review"
  while the public concept is still gated.
```

The `MACHINE_REVIEW_*` names listed in the handoff prompt are recorded below as
a **deferred future rename**, to be done (if ever) only alongside the larger
`llm_precheck_recorded` → `machine_review_recorded` event-kind rename that the
provider contract spec (section 7) also defers. Until then, `LLM_PRECHECK_*` is the source of
truth.

### 4.2 Settings used (all already in `config.py`)

```text
AI_REVIEW_ASSISTANT_MODE       off|cloud|local|test   high-level switch
LLM_PRECHECK_MODEL             model name passed to the provider
LLM_PRECHECK_API_KEY_ENV       NAME of the env var holding the secret (cloud)
LLM_PRECHECK_BASE_URL          model server / gateway base URL (local; cloud opt)
LLM_PRECHECK_TIMEOUT_SECONDS   per-call wall-clock budget (default 30)
LLM_PRECHECK_MAX_INPUT_TOKENS  context cap (default 6000)
LLM_PRECHECK_MAX_OUTPUT_TOKENS output cap (default 1200)
LLM_PRECHECK_INCLUDE_ARTIFACT_TEXT   default false
LLM_PRECHECK_INCLUDE_COORDINATES     default false
LLM_PRECHECK_STORE_FULL_CONTEXT      default false
```

Forward-map of the prompt's suggested names to the reused settings (for the
deferred rename only — do **not** add these now):

```text
MACHINE_REVIEW_CLOUD_PROVIDER   ~ (new) cloud vendor selector; today the cloud mode
                                  plus LLM_PRECHECK_BASE_URL
MACHINE_REVIEW_MODEL            -> LLM_PRECHECK_MODEL
MACHINE_REVIEW_API_KEY_ENV      -> LLM_PRECHECK_API_KEY_ENV
MACHINE_REVIEW_BASE_URL         -> LLM_PRECHECK_BASE_URL
MACHINE_REVIEW_TIMEOUT_SECONDS  -> LLM_PRECHECK_TIMEOUT_SECONDS
MACHINE_REVIEW_MAX_INPUT_TOKENS -> LLM_PRECHECK_MAX_INPUT_TOKENS
MACHINE_REVIEW_MAX_OUTPUT_TOKENS-> LLM_PRECHECK_MAX_OUTPUT_TOKENS
MACHINE_REVIEW_INCLUDE_ARTIFACT_TEXT -> LLM_PRECHECK_INCLUDE_ARTIFACT_TEXT
MACHINE_REVIEW_INCLUDE_COORDINATES   -> LLM_PRECHECK_INCLUDE_COORDINATES
```

One genuinely new knob may be justified later for cloud vendor selection
(`openai` vs `anthropic` vs `institutional_gateway`) when more than one cloud
backend exists; until then `online_api` + `LLM_PRECHECK_BASE_URL` /
`LLM_PRECHECK_MODEL` is enough and no new setting is added.

### 4.3 Config validation (fail fast, fail clearly)

Validation belongs in `build_machine_review_provider()` (the factory, §6) and is
mirrored by a startup check so a misconfigured deployment fails to boot rather
than failing silently at first review:

```text
Off    -> require NOTHING. No API key, model, base URL, or service. Always
          builds the disabled provider.
Cloud  -> require LLM_PRECHECK_API_KEY_ENV to be set AND to name an env var that
          is actually present and non-empty. Require LLM_PRECHECK_MODEL.
          A gateway deployment also requires LLM_PRECHECK_BASE_URL.
Local  -> require LLM_PRECHECK_BASE_URL and LLM_PRECHECK_MODEL.
Invalid AI_REVIEW_ASSISTANT_MODE value -> Pydantic Literal already rejects it at
          settings load (config.py:15); startup fails with a clear error.
Deployment-level disable -> a deployment may force Off regardless of provider
          settings (e.g. hosted_public default). Off always wins.
```

Missing required config raises `MachineReviewProviderConfigurationError`, which
`run_machine_review_for_submission` converts into a **failed review**, never a
crash mid-upload. The startup
check surfaces the same error earlier so operators see it at deploy time.

---

## 5. Where provider code lives

### 5.1 Decision: a native provider package beside the contract

Real providers emit the **machine-review** provider contract, which lives in
`app/services/machine_review/schemas.py`. The producer lives beside it in
`app/services/machine_review/providers/` (`interface.py` for the protocol, the
strict parser and the error type; `disabled.py`, `fake.py`, `cloud.py`,
`factory.py`), with the orchestration in
`app/services/machine_review/run.py` (`run_machine_review_for_submission`). The
`app/services/llm_precheck/` package now holds only the compact submission
context and its builder.

The `llm_precheck` context builder (`build_llm_precheck_context`) is the seed for
the machine-review context; §7 says how it is wrapped.

### 5.2 Persistence

`run_machine_review_for_submission` persists the validated payload through
`record_machine_review_audit_event` (`app/services/submission.py`). The event
kind (`llm_precheck_recorded`) and actor (`llm`) are as before, and
`details_json` carries the root `format` marker
(`{"name": "machine_review", "version": 1}`), which the adapter requires.

---

## 6. Provider interface

Designed around (the prompt's shape, made concrete):

```python
from typing import Protocol
from app.services.machine_review.context import MachineReviewContext
from app.services.machine_review.schemas import MachineReviewProviderResult


class MachineReviewProvider(Protocol):
    """Provider interface for native machine-review results."""

    def review_submission(
        self,
        context: MachineReviewContext,
    ) -> MachineReviewProviderResult:
        """Return a schema-validated machine-review result.

        Implementations call a real or fake model, then pass the raw model
        output through ``parse_machine_review_payload`` (the single trust
        boundary) so the returned value is ALWAYS a validated
        ``MachineReviewProviderResult``. They never persist, never mutate,
        and never raise for model misbehavior — malformed output is converted
        to a failed review by the service, not by the provider.
        """
```

### 6.1 Raw output vs validated model

```text
A provider RECEIVES raw model output (text / JSON) and is responsible for
  turning it into validated structure via parse_machine_review_payload().
The SERVICE BOUNDARY re-validates defensively: run_machine_review_for_submission
  treats any provider return value as untrusted and calls model_validate again
  (cheap, idempotent) before persistence. Two checks, one source of truth
  (parsing.py), so a future provider that forgets to validate cannot leak an
  unvalidated payload into the audit event.
parse_machine_review_payload(raw: str | dict) -> MachineReviewProviderResult
  - json.loads if raw is str (ValueError -> caller maps to failed review)
  - MachineReviewProviderResult.model_validate (ValidationError -> failed)
  - returns the frozen, extra="forbid" model; nothing else is trusted.
```

The disabled and fake providers skip the network but still return a valid
`MachineReviewProviderResult` so the type contract holds for every mode.

---

## 7. Context builder

### 7.1 Decision: a `MachineReviewContext`, seeded from the precheck context builder

The prompt names `MachineReviewContext`, and providers want richer context
than `LLMPrecheckContext` carries (it has submission metadata +
record_refs + an empty `trust_summaries` placeholder, `schemas.py:65-80`). Define
a `MachineReviewContext` that **extends** that shape with the deterministic
evidence the reviewer is supposed to reason over, and build it by enriching the
existing `build_llm_precheck_context` output rather than duplicating the
submission/link queries.

### 7.2 What the context includes (compact, structured only)

```text
submission metadata          id, status, kind, source_kind, title, summary
linked records               (record_type, record_ref/record_id, role) per link
record refs/ids              the SAME refs the mapper keys on (so a finding's
                             record_ref round-trips to an exact link)
deterministic evidence       per-record trust/evidence summaries:
  missing checks               missing_checks[]
  warning checks               warning_checks[]
  passed / not-applicable      passed_checks[] / not_applicable_checks[]
  hard_fail_reason             the deterministic hard-fail string, if any
  trust_status                 the deterministic status token
source calculation summaries roles, level-of-theory labels, software labels
geometry validation summaries pass/fail + atom counts (NOT raw coordinates)
artifact kind summaries      kind + presence flags (NOT artifact bytes)
selected notes/free-text     submission title/summary, record-level notes that
                             are already public-ish; NEVER private admin notes
```

Each context field maps to an `evidence_key` token a finding can cite
(`missing_checks.irc_evidence_present`, `geometry.atom_count`, …) so a real
provider's findings stay grounded in the same deterministic outputs the trust
layer produced — the reviewer reads evidence, it does not compute it.

### 7.3 Excluded by default (privacy / size)

```text
full raw logs (Gaussian/ORCA/…)      large coordinate blocks
full artifacts / artifact bytes      secrets / API keys / env vars
private admin notes                  anything not needed to reason over evidence
```

Optional, controlled expansion only via the existing flags
(`LLM_PRECHECK_INCLUDE_ARTIFACT_TEXT`, `LLM_PRECHECK_INCLUDE_COORDINATES`,
`LLM_PRECHECK_STORE_FULL_CONTEXT`), all defaulting `false`. Even when enabled,
size caps (`LLM_PRECHECK_MAX_INPUT_TOKENS`) and credential redaction still apply
(`optional_llm_precheck.md` §10/§14).

### 7.4 Context-too-large behavior

```text
Prefer compacting summaries (drop passed_checks, truncate notes, cap finding
  evidence) before failing.
If the compacted context still exceeds LLM_PRECHECK_MAX_INPUT_TOKENS, return a
  failed review (status=machine_review_failed) with a summary saying the context
  was too large. Do NOT fail the upload. Do NOT silently truncate evidence the
  reviewer needs without saying so in the summary.
```

---

## 8. Prompt / output contract

### 8.1 Output: the contract, verbatim

Real providers must return JSON matching `MachineReviewProviderResult`
(`machine_review/schemas.py:177-200`), validated through `parse_machine_review_payload`:

```json
{
  "format": {"name": "machine_review", "version": 1},
  "status": "machine_screened_needs_attention",
  "curator_priority": "high",
  "summary": "One critical transition-state contradiction.",
  "model": "vendor/model-name",
  "provider": "CloudMachineReviewProvider",
  "used_rag": false,
  "findings": [
    {
      "severity": "critical",
      "category": "transition_state_validation",
      "record_type": "transition_state_entry",
      "record_ref": "9002",
      "message": "Marked validated, but the frequency set shows no single imaginary mode expected for a first-order saddle point.",
      "evidence_keys": ["ts.imaginary_frequency_count", "ts.validated"],
      "recommended_action": "Re-run the TS frequency analysis and confirm exactly one imaginary mode before validating."
    }
  ]
}
```

Required constraints (all already enforced by the Pydantic model — the prompt
template restates them so a real model is told the rules it will be validated
against):

```text
format == {"name": "machine_review", "version": 1}   (required marker)
used_rag == false                                (Literal[False]; any truthy fails)
extra fields forbidden                           (extra="forbid")
no mutation payloads                             (no set_*/override/mutation field)
no approval/rejection/certification/hiding language as ACTIONS
findings cite evidence_keys when possible        (grounding)
record-level findings include record_type AND (record_ref OR record_id)
findings reference ONLY records present in the context (no invented ids)
status / severity / category use ONLY the allowed enum tokens
```

### 8.2 Prompt template + safety wording

The prompt template lives in `providers/` (e.g. `prompts.py`), versioned, with
the deterministic context (§7) rendered into it. It must tell the model, in
substance:

```text
You are not a curator. You cannot approve, reject, certify, hide, or mutate
  records. You are a provisional machine reviewer over deterministic evidence.
Your output must be JSON only, matching the given schema. No prose outside JSON.
Use ONLY the allowed enum tokens for status, severity, and category.
If unsure, produce warning / needs_attention findings rather than authoritative
  claims. Prefer info/warning over critical when evidence is ambiguous.
Do not invent record identifiers. Reference only record_type/record_ref values
  that appear in the provided context.
Do not create findings for records not present in the context.
used_rag must be false.
recommended_action is advice for a human curator; it is never executed.
```

The safety wording is **belt-and-braces**, not the enforcement: even a model that
ignores every line above is caught by `extra="forbid"`, the `Literal[False]`
`used_rag`, the enum validation, and the adapter's exact-identity mapping (an
invented `record_ref` simply fails to map and becomes a diagnostic, never a
record review). The prompt reduces noise; the schema + mapping guarantee safety.

---

## 9. Failure behavior

`run_machine_review_for_submission` synthesizes a `machine_review_failed`
payload on every failure path, so the audit trail stays in one format:

| Scenario | Behavior |
|---|---|
| Off mode | Disabled provider; **write nothing** by default (absence ⇒ `not_run`). |
| Provider timeout (`LLM_PRECHECK_TIMEOUT_SECONDS`) | Synthesize `status=machine_review_failed`, persist advisory audit event, **do not fail upload**. |
| Provider error (network, auth, 5xx) | Same: `machine_review_failed`, persisted, upload unaffected. |
| Malformed / non-JSON / extra-field / `used_rag=true` output | `parse_machine_review_payload` raises → caught → `machine_review_failed`. Upload unaffected. |
| Context too large after compaction | `machine_review_failed` with an explanatory summary (§7.4). |
| Config error (missing key/base URL) | `MachineReviewProviderConfigurationError` → `machine_review_failed`; also caught at startup (§4.3). |
| Submission has no linked records | `not_run` or a `machine_screened_pass` with a summary saying there was nothing to inspect. |
| DB rollback | Audit event rolls back with the surrounding transaction; no separate durability guarantee in MVP. |

Hard guarantees (each a required test, §12):

```text
provider failure does NOT fail the upload
provider failure does NOT mutate submission.status
provider failure does NOT mutate scientific records, evidence, or public trust
provider failure does NOT create a curator task
a failed review is visible only as an advisory audit event
```

A `machine_review_failed` payload, by the adapter's existing rules, does **not**
become a record review or a record summary (handoff §5, "failed review payloads
do NOT become record reviews"), so it never seeds a curator task — exactly the
behavior of every failure path.

---

## 10. Persistence

Unchanged from the provider contract spec (section 7) — no migration, no new event kind, no new
table:

```text
submission_audit_event.details_json   <- the validated payload
event_kind = llm_precheck_recorded     (unchanged; event_is_machine_review keys on it)
actor_kind = llm                       (unchanged)
format = {"name": "machine_review", "version": 1}   inside details_json (the marker)
```

```text
No record_machine_review table.
No automatic task creation on upload/precheck (build remains admin-triggered).
No new SubmissionAuditEventKind value (that would be a deployed-enum migration).
The persistence layer is the one recorder in §5.2.
```

The future `llm_precheck_recorded` → `machine_review_recorded` event-kind rename
stays deferred (provider contract spec, section 7); it is a migration + adapter update and is
out of scope here.

---

## 11. Provider choices (examples only)

Not hardcoded; the repo has no committed preference, and `online_api` /
`local_http` are deliberately generic in `config.py`:

```text
Cloud (online_api):  OpenAI / Anthropic / an institutional gateway. Selected by
                     LLM_PRECHECK_MODEL (+ LLM_PRECHECK_BASE_URL for a gateway);
                     secret named by LLM_PRECHECK_API_KEY_ENV.
Local (local_http):  Ollama / vLLM / a llama.cpp-compatible HTTP server. Selected
                     by LLM_PRECHECK_BASE_URL + LLM_PRECHECK_MODEL.
```

A concrete cloud vendor selector (`MACHINE_REVIEW_CLOUD_PROVIDER`-style) is only
worth adding once more than one cloud backend is implemented (§4.2). For a single
backend, `online_api` + model + base URL is sufficient.

Whatever the backend: it must produce a `MachineReviewProviderResult` through
`parse_machine_review_payload`, respect the timeout, and never be required for
upload success.

---

## 12. Tests required (for the later implementation)

```text
off mode writes nothing                          (no audit event; absence = not_run)
off mode requires no API key / model / base URL
cloud mode validates required API key env config (missing -> failed review +
                                                   startup check error)
local mode validates required base URL/model config
fake provider remains test-only                  (reachable only via test mode /
                                                   direct injection; not in user docs)
provider output is schema-validated              (parse_machine_review_payload
                                                   is the single trust boundary)
used_rag=true is rejected                         -> failed review
extra / mutation field in output rejected         -> failed review
malformed (non-JSON / wrong enum) output           -> failed review
provider timeout                                   -> failed review, upload OK
context too large after compaction                 -> failed review with summary
payload persisted to submission_audit_event.details_json
payload flows through inspection and (when admin-triggered) creates a
  curator task for warning/critical exact-mapped findings
provider NEVER mutates submission.status, RecordReviewStatus, scientific records,
  evidence, or public trust
golden fixtures (all in the current format) still pass unchanged
deterministic trust values are byte-identical before and after a real review
context excludes raw artifacts / coordinates / secrets by default; includes
  deterministic evidence output
invented record_ref in a finding does not map (diagnostic only), never a record
  review or task
```

These extend the test list in `optional_llm_precheck.md` §17 and the invariants proven in `machine_review_handoff.md` §5.

---

## 13. Other formats are refused

The adapter reads exactly one format: a payload whose root `format` marker
is `{"name": "machine_review", "version": 1}`.

```text
A payload with no format marker, or with another one, is an unknown format:
  no result, one parse warning. Nothing is translated or guessed.
The retired precheck contract (a `label`, no marker) and its providers, runner
  and result models were deleted; only LLMPrecheckContext and its builder remain.
Golden fixtures under backend/tests/fixtures/machine_review/ are all in the
  current format.
```

---

## 14. What stays fake / test-only

```text
FakeMachineReviewProvider               test/dev only; deterministic output
                                        for golden fixtures + adapter tests.
AI_REVIEW_ASSISTANT_MODE=test           the only way to select a fake provider by
                                        config; never a user-facing mode.
The golden fixtures under backend/tests/fixtures/machine_review/ remain the
  evaluation harness; real providers do not replace them — they are validated
  AGAINST the same contract those fixtures exercise.
```

No real network call, no real API key, and no real model is introduced by this
spec or by the stub providers it describes.

---

## 15. Recommended implementation order

```text
1. Settings/config validation only.
   Add §4.3 validation to a factory (build_machine_review_provider) + a startup
   check. No provider logic yet. Cloud/Local still raise until step 6.
2. Provider interface + disabled + fake provider.
   base.py Protocol, disabled.py (off), fake.py (deterministic). Wire
   run_machine_review_for_submission to persist a payload via the §5.2
   helper tweak. Tests: off writes nothing; fake writes a validated event.
3. Local-http provider stub with mocked tests.
   local.py that builds only when base URL + model are configured; the HTTP call
   is mocked in tests. Assert timeout -> failed review, malformed -> failed.
4. Cloud provider stub with mocked tests.
   cloud.py gated on API-key-env + model; mocked transport. Same failure asserts.
5. Prompt template + strict JSON parsing.
   prompts.py (versioned) + parse_machine_review_payload as the single trust
   boundary. Tests: used_rag=true, extra field, bad enum, non-JSON -> failed.
6. Optional real provider implementation behind config.
   Replace a stub's mocked transport with a real client, behind explicit
   LLM_PRECHECK_* config and still advisory/private. Only after 1-5 are green.
```

Steps 1-5 introduce **no** real model call. Step 6 is the only one that does, and
it stays behind config, advisory, private, and gated by the public-exposure rule
in `provisional_machine_review.md` §10.

---

## 16. Non-goals

```text
No provider implementation in this spec.
No real API calls to OpenAI / Anthropic / local models.
No public trust.machine_review.
No automatic task creation on upload/precheck.
No frontend / curator UI.
No migration (no new event kind, no new table; version lives in details_json).
No ARC / tckdb-client changes.
No new MACHINE_REVIEW_* config namespace (reuse LLM_PRECHECK_*; §4).
No change to the wire contract, the adapter, the mapping, the inspection
  projection, the curator-task services, or any public schema.
No RAG (used_rag stays false).
```
