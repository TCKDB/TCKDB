# Machine-Review Golden Examples

**Status:** test fixtures + docs only. No real LLM provider, no public
`trust.machine_review`, no automatic task creation, no migration.
**Date:** 2026-05-31
**Scope:** TCKDB backend. Golden fake-provider payloads that exercise the
private/admin machine-review pipeline end-to-end so maintainers can judge
whether statuses read clearly, findings map correctly, false positives look
manageable, task creation is sane, and public exposure remains premature.
**Audience:** maintainers evaluating machine-review behavior before any real
provider or public exposure.

**Related specs:** `machine_review_handoff.md`,
`admin_machine_review_inspection.md`, `admin_machine_review_curator_task_api.md`,
`machine_review_curator_task_queue.md`, `provisional_machine_review.md`.

---

## What these are

Each fixture in `backend/tests/fixtures/machine_review/*.json` is a
self-contained golden input:

```text
case                       short id
description                what it demonstrates
linked_records             the submission's record links
audit_event_details_json   the EXACT persisted precheck payload (a fake
                           provider's output, stored on a
                           submission_audit_event, event_kind=llm_precheck_recorded)
expected                   the stable outputs the test asserts
```

`backend/tests/services/test_machine_review_golden_examples.py` seeds a
submission + links + the audit event, then runs the real pipeline:

```text
details_json
  -> machine-review audit adapter (validate + safe, exact-identity mapping)
  -> build_submission_machine_review_inspection
  -> build_curator_tasks_for_submission
  -> machine_review_curator_task rows
```

The test asserts statuses, counts, mapping/parse diagnostics, task workflow
state, and fingerprint stability — **not** exact timestamps (`reviewed_at` /
`created_at` are left unasserted, per the stable-output policy).

### Vocabulary caveat

The persisted precheck vocabulary (`LLMFindingCategory`) is a **subset** of the
service-layer `MachineReviewCategory`, and `LLMFinding` carries no
`recommended_action`. So a payload originating from the precheck path cannot
emit `transition_state_validation` / `schema_gap` categories or a
recommended action. The transition-state golden case therefore uses the
closest available token, `consistency`. Findings constructed directly against
the service-layer contract (e.g. the fingerprint unit tests) can use the full
vocabulary.

---

## The cases

| Case | Input | Result |
|---|---|---|
| `clean_pass_no_tasks` | `label=pass`, no findings | No record summary, no task. The adapter only builds record reviews from findings, so a clean pass yields nothing to triage. |
| `kinetics_warning_creates_task` | one `warning` finding on linked kinetics `9001` | One record summary `machine_screened_warning` (highest `warning`); one `needs_curator_review` task. |
| `transition_state_critical_creates_task` | one `critical` finding (`consistency`) on linked `transition_state_entry` `9002` | One record summary `machine_screened_needs_attention` (highest `critical`); one `needs_curator_review` task; siblings unaffected. |
| `submission_scoped_finding_no_task` | one `warning` finding with no `record_type` | Diagnostic only: `unmapped_findings_count=1`, **no** mapping warning (submission-scoped is expected), no record summary, no task. |
| `unlinked_record_finding_diagnostic_only` | one `warning` finding naming unlinked kinetics `7777` | Anti-fan-out: `unmapped_findings_count=1` + one mapping warning ("not linked"); no record summary, no task. |
| `malformed_payload_parse_warning_only` | `label` outside the precheck enum | Degrades safely: `parse_warnings_count=1`, no record summaries, no tasks, no exception. |

Record-level status is derived from the record's **findings**
(`derive_machine_review_status`): any `critical` →
`machine_screened_needs_attention`, any `warning` → `machine_screened_warning`,
else `machine_screened_pass`. Only `warning`/`critical` exact-mapped findings
create tasks; `info` findings create none by default.

---

## Fingerprint behavior (proven by the same test module)

`compute_finding_fingerprint` is a stable SHA-256 over the identity-bearing
fields:

```text
same finding, different source_audit_event_id  -> SAME fingerprint (dedups; re-run reuses the task)
same finding, different model / provider        -> SAME fingerprint (dedups)
evidence_keys reordered                          -> SAME fingerprint (keys are sorted)
changed message / evidence_keys / recommended_action -> DIFFERENT fingerprint
```

This is what lets re-running the precheck (each run a fresh audit event) reuse
the existing curator task instead of spawning duplicates, while a genuinely
different concern gets its own task.

---

## Public boundary

The API-level golden case asserts the public boundary is intact after building
and inspecting tasks:

```text
public TrustFragment has NO machine_review field
trust.llm_precheck stays enabled=false, label=not_run, summary=null
```

No golden example exposes `trust.machine_review`, makes task creation
automatic, or grants curator access — all remain deferred product decisions
(see `machine_review_handoff.md` §10/§11).
