# Machine-Review Workstream — Handoff / Checkpoint

**Status:** handoff document for the machine-review workstream as of
2026-05-31. Some layers are **implemented and tested** (private/admin only);
some are **spec-only** (no code). This document states exactly which is which.
**Date:** 2026-05-31
**Scope:** TCKDB backend. No public `trust.machine_review`. The machine-review
stack is private/admin-only and advisory. No ARC / `tckdb-client` change.
**Audience:** the next chat or coding agent picking up machine review. Read this
first.

---

## 0. The one thing not to confuse

These six terms are **distinct layers** and are routinely conflated. Keep them
apart:

```text
llm_precheck        Implemented advisory precheck plumbing. Produces
                    submission_audit_event rows (details_json = LLMPrecheckResult).
                    Public trust.llm_precheck is FROZEN disabled/not_run.

machine_review      Implemented PRIVATE stack that translates precheck audit
                    events into advisory, record-level review summaries.
                    Authoritative for NOTHING. Not public.

admin inspection    Implemented admin-only, read-only endpoint that projects a
                    submission's machine-review audit events onto its records.
                    Debugging surface. Not workflow, not trust, not moderation.

curator task        SPEC ONLY (no code). A future persisted human-triage queue
workflow            over machine findings. Its own state axis.

human review        Authoritative curator decision: RecordReviewStatus,
                    is_certified, benchmark_reference. Implemented elsewhere.
                    Machine review NEVER writes this.

public trust        The public TrustFragment. Machine review is NOT in it.
                    Exposing trust.machine_review is a deferred product decision.
```

Authority order, absolute: **human review wins over machine review wins over
nothing.** Machine review is advisory context only.

---

## 1. What is implemented (private stack)

All of the following exist in code with tests, and are private/admin-only.

| Layer | Module | Role |
|---|---|---|
| Foundational spec | `docs/specs/provisional_machine_review.md` | Vocabulary, mental model, public-exposure gate |
| Contracts | `app/services/machine_review/schemas.py` | `MachineReviewStatus`, `MachineReviewSeverity`, `MachineReviewCategory`, `CuratorPriority`, `MachineReviewFinding`, `MachineReviewResult` (all `extra="forbid"`, no mutation field) |
| Status derivation | `app/services/machine_review/derivation.py` | `MachineReviewOutcome` + `derive_machine_review_status()` — pure, deterministic |
| Safe mapping | `app/services/machine_review/mapping.py` | `map_findings_to_submission_records()` — exact identity, no fan-out |
| Latest-record read model | `app/services/machine_review/read_model.py` | `RecordMachineReview`, `MachineReviewRecordSummary`, `select_latest_machine_review_for_record()` |
| Audit-event adapter | `app/services/machine_review/audit_adapter.py` | `submission_audit_event.details_json` -> `LLMPrecheckResult` -> `RecordMachineReview` tuples |
| Private trust-envelope adapter | `app/services/machine_review/trust_adapter.py` | `InternalTrustEnvelopeWithMachineReview` — assembles a private envelope beside (never inside) the public `TrustFragment` |
| Inspection service | `app/services/machine_review/inspection.py` | `build_submission_machine_review_inspection()` and the per-submission/per-record inspection dataclasses |
| Admin endpoint | `app/api/routes/admin.py` | `GET /api/v1/admin/submissions/{id}/machine-review-inspection` (`require_admin`, read-only) |
| Admin inspection docs | `docs/specs/admin_machine_review_inspection.md` | Endpoint behavior/contract |
| Curator workflow spec | `docs/specs/machine_review_curator_workflow.md` | **Spec only** — triage roles, queue, human-action policy, exposure gate |
| Curator task queue spec | `docs/specs/machine_review_curator_task_queue.md` | **Spec only** — the persisted task table design |

---

## 2. Commit sequence

The workstream, in order (subjects as committed):

```text
f83178a  Spec provisional machine-review layer between evidence and human review
e660468  Add machine-review contracts and deterministic status derivation
32b581e  Add machine-review non-interference tests using the fake provider
0362599  Add pure submission->record machine-review mapping policy
329c5d2  Add internal latest-record machine-review read model
18812a8  Add private machine-review audit-event adapter
aad4064  Add private machine-review trust-envelope adapter
53ccf5f  Add private/admin machine-review inspection service
2b0f8d4  Add admin-only submission machine-review inspection endpoint
3ee31e5  Document admin machine-review inspection endpoint
489801a  Spec curator/admin machine-review workflow
9d04aba  Spec persisted curator task queue for machine-review findings
```

(`f83178a` is the foundational spec; the prompt's list starts at `e660468`. The
hashes match; commit subjects above are the actual git-log subjects.)

---

## 3. Current PUBLIC behavior (unchanged)

```text
Public scientific reads do NOT expose trust.machine_review.
Public TrustFragment has NOT changed (review_status, trust_status, evidence,
  llm_precheck, is_certified — same shape as before this workstream).
trust.llm_precheck remains FROZEN: enabled=False, label="not_run"
  (TrustLLMPrecheck default; no LLM is wired in).
include=all still does NOT include trust.
search / list endpoints still do NOT expose trust.
```

Nothing in the machine-review workstream added a field to a public response
schema. The only new route is the admin-only inspection endpoint (§4).

---

## 4. Current PRIVATE / admin behavior

```http
GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection
```

Response schema: `AdminSubmissionMachineReviewInspectionResponse`
(`submission_id`, `record_summaries[]`, `unmapped_findings_count`,
`mapping_warnings[]`, `parse_warnings[]`, `source_audit_event_ids[]`).

It is:

```text
admin-only        require_admin; curators/users -> 403, anonymous -> 401
read-only         served via get_db (no write session); mutates nothing
submission-scoped keyed by submission_id, not by a record identity
derived           computed on the fly from submission_audit_event +
                  submission_record_link; nothing persisted
```

It is NOT:

```text
human review        (no RecordReviewStatus write)
public trust        (own admin schema; never touches TrustFragment)
certification       (never sets is_certified / benchmark_reference)
moderation          (never approves / rejects / hides; never touches submission.status)
```

Only records that received at least one **exactly mapped** finding appear in
`record_summaries`. Submission-scoped, unlinked, and sibling findings show up
only in the counters/warnings, never as a record summary.

---

## 5. Core invariants (proven by tests)

Tests live in `backend/tests/services/test_machine_review_*.py` and
`backend/tests/api/test_admin_machine_review_inspection.py`.

```text
machine review does not mutate submission.status
machine review does not mutate scientific records
machine review does not change deterministic evidence (rubric output byte-stable)
failed provider behavior is advisory only            (outcome=failed -> machine_review_failed, never an upload failure)
off / not_performed exposes no public trust state     (outcome=not_performed -> not_run)
repeated runs keep evidence byte-identical
submission-scoped findings do not fan out to records
unlinked findings remain diagnostics (counted, never a record summary)
sibling findings do not affect each other            (latest selection filters on exact (record_type, record_ref))
private trust-envelope assembly preserves public TrustFragment fields
admin inspection endpoint does not change public trust behavior
```

---

## 6. Vocabulary boundaries (the seven axes)

```text
MachineReviewStatus            IMPLEMENTED  app/services/machine_review/schemas.py
  not_run / machine_screened_pass / machine_screened_warning /
  machine_screened_needs_attention / machine_review_failed
  (machine_screened_blocking_concern reserved, NOT produced)
  -> advisory machine conclusion. Authoritative for nothing.

MachineReviewSeverity          IMPLEMENTED  schemas.py
  info / warning / critical                  -> per-finding severity

MachineReviewCategory          IMPLEMENTED  schemas.py
  provenance / units / geometry / kinetics / thermo / statmech / transport /
  transition_state_validation / calculation_parameters / consistency / schema_gap

MachineReviewOutcome           IMPLEMENTED  app/services/machine_review/derivation.py
  not_performed / failed / completed
  -> whether the reviewer RAN (distinct from what it concluded). Drives the
     first two derivation rules; completed defers to finding severities.

RecordReviewStatus             IMPLEMENTED  app/db/models/common.py
  not_reviewed / under_review / approved / rejected / deprecated
  -> AUTHORITATIVE human review. Machine review never writes it.

SubmissionStatus               IMPLEMENTED  app/db/models/common.py
  pending / precheck_passed / auto_flagged / approved / rejected / superseded
  -> submission lifecycle / moderation. Machine review never writes it.

MachineReviewCuratorTaskState  SPEC ONLY (NOT implemented; no enum, no table)
  untriaged / needs_curator_review / in_curator_review /
  resolved_no_action / resolved_human_reviewed / dismissed_machine_finding
  -> proposed human-triage axis (curator task queue spec). Docs only.
```

`MachineReviewCuratorTaskState` is **proposed in docs only**. There is no enum
in `common.py` and no table for it yet. Do not assume it exists.

---

## 7. Architecture pipeline (implemented)

```text
submission_audit_event.details_json
  -> LLMPrecheckResult                 (validate; malformed -> failed, advisory)
  -> MachineReviewResult               (translate to machine-review contract)
  -> map_findings_to_submission_records (exact (record_type, record_id) only; no fan-out)
  -> RecordMachineReview               (per-record, stamped with reviewed_at/submission_id)
  -> MachineReviewRecordSummary        (select latest per record; status derived)
  -> private/admin inspection response (AdminSubmissionMachineReviewInspectionResponse)
```

Future optional extension — **NOT public, NOT implemented**:

```text
MachineReviewRecordSummary
  -> future trust.machine_review block        [DEFERRED — behind the spec gate]
```

`MachineReviewRecordSummary` is *shaped* like a future public fragment could
render, but nothing imports it into a public response schema. Exposing it is a
deferred product decision (`provisional_machine_review.md` §10).

---

## 8. Important files by category

**Service modules** (`backend/app/services/machine_review/`)

```text
schemas.py        contracts + enums (MachineReviewStatus/Severity/Category, finding, result)
derivation.py     MachineReviewOutcome + derive_machine_review_status()
mapping.py        map_findings_to_submission_records() — safe, exact-identity mapping
read_model.py     RecordMachineReview, MachineReviewRecordSummary, select_latest_*
audit_adapter.py  audit-event details_json -> RecordMachineReview tuples
trust_adapter.py  InternalTrustEnvelopeWithMachineReview (private; beside TrustFragment)
inspection.py     build_submission_machine_review_inspection() + inspection dataclasses
```

(Precheck source: `backend/app/services/llm_precheck/` — `schemas.py`
`LLMPrecheckResult`, `providers.py` fake provider.)

**Tests**

```text
backend/tests/services/test_machine_review_contracts.py
backend/tests/services/test_machine_review_mapping.py
backend/tests/services/test_machine_review_read_model.py
backend/tests/services/test_machine_review_audit_adapter.py
backend/tests/services/test_machine_review_trust_adapter.py
backend/tests/services/test_machine_review_inspection.py
backend/tests/services/test_machine_review_non_interference.py
backend/tests/api/test_admin_machine_review_inspection.py
```

**API / admin route**

```text
backend/app/api/routes/admin.py
  - AdminMachineReviewRecordInspection
  - AdminSubmissionMachineReviewInspectionResponse
  - inspect_submission_machine_review  (GET .../machine-review-inspection)
```

**Schemas** — machine-review contracts live in the service module
(`app/services/machine_review/schemas.py`), not under `app/schemas/`. Public
trust shape: `app/services/trust/models.py` (`TrustFragment`,
`TrustLLMPrecheck`).

**docs/specs**

```text
provisional_machine_review.md
admin_machine_review_inspection.md
machine_review_curator_workflow.md
machine_review_curator_task_queue.md
machine_review_admin_ui_mock.md
machine_review_handoff.md   (this file)
```

**OpenAPI golden** — `backend/tests/api/golden/openapi.json`. The admin
endpoint commit (`2b0f8d4`) updated it; the doc-only commits (`3ee31e5`,
`489801a`, `9d04aba`, this one) did not and must not change it.

---

## 9. Decision log

```text
MachineReviewStatus is a separate axis from RecordReviewStatus and the
  submission precheck label — its own enum, never overloaded onto either.
Reviewer OUTCOME dominates findings in status derivation: failed -> failed,
  not_performed -> not_run, regardless of any findings collected.
Mapping requires EXACT record identity; submission-scoped findings never fan
  out to a submission's records.
Inspection groups links per submission, so a finding can only map within its
  own submission — no cross-submission mapping.
Latest-review selection tie-break: reviewed_at first (missing sorts oldest),
  then higher submission_id, then later input order. Total order, deterministic.
Failed review payloads do NOT become record reviews / record summaries.
The admin endpoint is submission-centered, not record-centered.
The admin endpoint is require_admin, NOT curator-accessible.
Curator workflow state is a separate axis from machine status and human review.
The future task table excludes source_audit_event_id from finding_fingerprint
  (uses a review-context hash) so re-runs don't spawn duplicate tasks; model/
  provider also excluded by default.
Info findings do not create curator tasks by default.
No public trust.machine_review until an explicit product decision passes the
  exposure gate.
```

---

## 10. What NOT to do next

```text
Do NOT add machine_review to the public TrustFragment yet.
Do NOT map submission-level summaries to every linked record (no fan-out).
Do NOT use machine review to approve / reject / certify / hide records.
Do NOT mutate deterministic trust / evidence from machine review.
Do NOT overload submission.status or RecordReviewStatus for workflow state.
Do NOT edit deployed initial migrations (d861dfd60891 etc.).
Do NOT create a record_machine_review table before a product decision.
Do NOT grant curators raw inspection output without a safer curated workflow
  (see curator workflow spec §6).
```

---

## 11. Recommended next options

```text
Option A: Implement the machine_review_curator_task table + an admin queue API
          (new Alembic revision, MachineReviewCuratorTaskState enum). Most code;
          gives persistent human triage state.

Option B: Build a lightweight admin UI / mock around the existing inspection
          endpoint. No schema/migration; turns the diagnostic into something a
          human can actually drive and learn from. Designed in
          machine_review_admin_ui_mock.md.

Option C: Design public trust.machine_review exposure — still behind the spec
          gate in provisional_machine_review.md §10. Highest risk; needs the
          exposure-gate questions answered from real triage experience first.
```

**Recommendation: Option B (or a small admin UI/mock) before any public
exposure.** The inspection endpoint already produces the projection; the missing
ingredient is real human triage experience. A mock/admin UI generates that
experience cheaply, with no new schema and no public surface, and is exactly
what the exposure gate (Option C) requires as input. Option A is the natural
follow-on once Option B shows the queue shape is right; Option C stays deferred
until the gate questions can be answered.
