# Machine-Review Workstream — Handoff / Checkpoint

**Status:** handoff document for the machine-review workstream as of
2026-05-31. The private/admin stack is now **implemented and tested** through
the persisted curator task queue and its admin workflow API; what remains
**spec-only / deferred** is public `trust.machine_review` exposure, a curator-
facing UI, and real (non-fake) provider plumbing. This document states exactly
which is which.
**Date:** 2026-05-31 (updated after the curator task queue API + docs)
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

curator task        IMPLEMENTED (table + enum + services + admin API). A
workflow            persisted human-triage queue over machine findings, on its
                    own state axis (MachineReviewCuratorTaskState). Admin-only;
                    creation is explicit, never automatic on upload. Mutates
                    only curator-task rows — never human review or trust.

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
| Curator task model + migration | `app/db/models/machine_review_curator_task.py`, `alembic/versions/b8c9d0e1f2a3_*.py` | `machine_review_curator_task` table; identity unique constraint, queue indexes, FKs, bidirectional resolution-consistency CHECK |
| Curator task DB enums | `app/db/models/common.py` | `MachineReviewCuratorTaskState` (the triage axis) + DB-layer `MachineReviewStatus` / `MachineReviewSeverity` mirrors (separate classes from the service-layer ones; drift-guarded) |
| Curator task creation service | `app/services/machine_review/curator_tasks.py` | `build_curator_tasks_for_submission()`, `compute_finding_fingerprint()`, `CuratorTaskBuildResult` — explicit upsert from the inspection projection |
| Curator task lifecycle service | `app/services/machine_review/curator_task_lifecycle.py` | `assign_curator_task()`, `start_curator_task_review()`, `resolve_curator_task()`, `reopen_curator_task()` — flush-only, no commit, no authz |
| Admin endpoints | `app/api/routes/admin.py` | inspection endpoint + 7 curator-task routes (`require_admin`, see §4) |
| Admin inspection docs | `docs/specs/admin_machine_review_inspection.md` | Inspection endpoint behavior/contract |
| Admin curator task API docs | `docs/specs/admin_machine_review_curator_task_api.md` | Curator task queue API behavior/contract |
| Curator workflow spec | `docs/specs/machine_review_curator_workflow.md` | Triage roles, queue, human-action policy, exposure gate (design; the workflow/UI layer it describes is **not** built yet) |
| Curator task queue spec | `docs/specs/machine_review_curator_task_queue.md` | The persisted task table design — **now implemented** (see model/migration row above) |

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
78cceaa  Add machine-review curator task model and migration
b02438e  Add machine-review curator task creation service
d93afc8  Add machine-review curator task lifecycle service
b4110e4  Add admin machine-review curator task queue API
1a17962  Document admin machine-review curator task API
<this>    Update machine-review handoff after curator task API
```

(`f83178a` is the foundational spec. The five commits from `78cceaa` onward are
the curator task queue slice — model/migration, creation service, lifecycle
service, admin API, and its docs — followed by this handoff update.)

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
No automatic curator-task creation runs on upload or precheck — task building
  is explicit/admin-triggered only.
Curator access is still deferred; the curator task queue is admin-only.
```

Nothing in the machine-review workstream added a field to a public response
schema. The only new routes are **admin-only** (§4): the inspection endpoint
and the seven curator-task queue endpoints. A public-trust regression test
asserts the `TrustFragment` shape is unchanged after exercising the curator
task API.

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

### Admin endpoints (all `require_admin`)

```http
GET  /api/v1/admin/submissions/{submission_id}/machine-review-inspection

GET  /api/v1/admin/machine-review/curator-tasks
GET  /api/v1/admin/machine-review/curator-tasks/{task_id}
POST /api/v1/admin/machine-review/curator-tasks/build-for-submission/{submission_id}
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/assign
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/start-review
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/resolve
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/reopen
```

Access on **every** route (identical to the inspection endpoint):

```text
anonymous     -> 401
normal user   -> 403
curator       -> 403
admin         -> allowed
```

Curator-task endpoint behavior (full contract in
`admin_machine_review_curator_task_api.md`):

```text
list        filters: workflow_state, assigned_to, record_type, record_id,
            submission_id, highest_severity, limit (default 50, 1..200), offset.
            Ordering: open states first, highest severity first, newest
            updated_at, id descending. Returns PaginatedResponse.
get         one AdminCuratorTaskResponse; 404 if missing.
build       explicit/admin-triggered upsert from the inspection projection;
            creates tasks ONLY for exact mapped warning/critical findings;
            skips info / submission-scoped / unmapped / parse-warning; reuses
            open tasks, skips terminal; never mutates submission.status; 404 if
            the submission is missing. Returns AdminCuratorTaskBuildResponse.
assign      sets/clears assigned_to (null unassigns); no state change; rejects
            a terminal task (400).
start-review untriaged/needs -> in_curator_review; idempotent if already in
            review; rejects terminal (400); actor defaults to the authenticated
            admin and auto-assigns when unassigned.
resolve     terminal state + required non-empty note; resolved_by = admin;
            resolved_at set by service; same-state idempotent, different-state
            rejected (400); resolved_human_reviewed does NOT write
            RecordReviewStatus.
reopen      terminal -> untriaged/needs_curator_review/in_curator_review; clears
            the resolution triple; preserves assigned_to unless
            clear_assignment=true.
```

All curator-task responses use admin-only `extra="forbid"` schemas and carry no
public `trust.machine_review`.

---

## 5. Core invariants (proven by tests)

Tests live in `backend/tests/services/test_machine_review_*.py`,
`backend/tests/services/test_machine_review_curator_*.py`, and
`backend/tests/api/test_admin_machine_review_*.py`.

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

Curator task queue invariants (model / creation / lifecycle / API):

```text
DB/service enum token sets stay aligned                (drift-guard test)
resolution-consistency CHECK: terminal <=> all of resolved_at/by/note set
dedup identity = (submission_id, record_type, record_id, finding_fingerprint)
fingerprint excludes source_audit_event_id, model, provider, timestamps
  (re-run with a new audit event reuses the task, never duplicates)
warning/critical exact mapped findings create tasks; info findings do not
submission-scoped / unmapped / parse-warning diagnostics create no task
open task reused (snapshot may refresh) without losing assignment
terminal task not reopened by default; same-resolution re-resolve idempotent
resolved_human_reviewed does NOT write RecordReviewStatus
build / assign / start / resolve / reopen do not mutate submission.status,
  RecordReviewStatus, scientific records, evidence, or public trust
curator-task API: admin -> 200; curator/user -> 403; anonymous -> 401
curator task API does not change public TrustFragment shape
```

---

## 6. Vocabulary boundaries (the seven axes)

```text
MachineReviewStatus            IMPLEMENTED  app/services/machine_review/schemas.py
  not_run / machine_screened_pass / machine_screened_warning /
  machine_screened_needs_attention / machine_review_failed
  (machine_screened_blocking_concern reserved, NOT produced)
  -> advisory machine conclusion. Authoritative for nothing.
  MIRRORED at the DB layer in app/db/models/common.py (same token values,
  separate class) for the persisted curator-task snapshot column.

MachineReviewSeverity          IMPLEMENTED  schemas.py
  info / warning / critical                  -> per-finding severity
  MIRRORED at the DB layer in app/db/models/common.py (same token values,
  separate class), drift-guarded against the service-layer enum.

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

MachineReviewCuratorTaskState  IMPLEMENTED  app/db/models/common.py
  untriaged / needs_curator_review / in_curator_review   (open)
  resolved_no_action / resolved_human_reviewed / dismissed_machine_finding (terminal)
  -> human-triage axis, persisted on machine_review_curator_task. A fourth axis,
     distinct from MachineReviewStatus (advisory), RecordReviewStatus
     (authoritative human review), and SubmissionStatus (lifecycle).
```

`MachineReviewCuratorTaskState` now exists as a DB enum in `common.py` and backs
the `machine_review_curator_task.workflow_state` column. It is the **fourth**
axis — never overloaded onto machine status, human review, or submission
lifecycle.

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
  -> build_curator_tasks_for_submission (explicit/admin-triggered upsert; warning/
                                        critical exact mapped findings only)
  -> machine_review_curator_task rows  (persisted; dedup on submission_id+record_type+
                                        record_id+finding_fingerprint)
  -> assign / start-review / resolve / reopen lifecycle (curator_task_lifecycle.py)
  -> admin curator task queue API      (/api/v1/admin/machine-review/curator-tasks*)
```

Everything in that chain is private/admin-only. The first six steps are pure
read/projection (the inspection endpoint); the last four persist and mutate
**only** `machine_review_curator_task` rows.

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
curator_tasks.py          build_curator_tasks_for_submission(), compute_finding_fingerprint(), CuratorTaskBuildResult
curator_task_lifecycle.py assign / start_curator_task_review / resolve / reopen (flush-only)
```

(Precheck source: `backend/app/services/llm_precheck/` — `schemas.py`
`LLMPrecheckResult`, `providers.py` fake provider.)

**Model + migration**

```text
backend/app/db/models/machine_review_curator_task.py   MachineReviewCuratorTask ORM
backend/app/db/models/common.py                        MachineReviewCuratorTaskState +
                                                       DB-layer MachineReviewStatus/Severity mirrors
backend/alembic/versions/b8c9d0e1f2a3_*.py             create table + 3 enum types
```

**Tests**

```text
backend/tests/services/test_machine_review_contracts.py
backend/tests/services/test_machine_review_mapping.py
backend/tests/services/test_machine_review_read_model.py
backend/tests/services/test_machine_review_audit_adapter.py
backend/tests/services/test_machine_review_trust_adapter.py
backend/tests/services/test_machine_review_inspection.py
backend/tests/services/test_machine_review_non_interference.py
backend/tests/services/test_machine_review_curator_task_queue.py     (model/migration)
backend/tests/services/test_machine_review_curator_tasks.py          (creation service)
backend/tests/services/test_machine_review_curator_task_lifecycle.py (lifecycle service)
backend/tests/api/test_admin_machine_review_inspection.py
backend/tests/api/test_admin_machine_review_curator_tasks.py         (admin queue API)
```

**API / admin route**

```text
backend/app/api/routes/admin.py
  - AdminMachineReviewRecordInspection
  - AdminSubmissionMachineReviewInspectionResponse
  - inspect_submission_machine_review  (GET .../machine-review-inspection)
  - AdminCuratorTaskResponse / AdminCuratorTaskBuildResponse + request schemas
  - list / get / build / assign / start-review / resolve / reopen handlers
```

**Schemas** — machine-review contracts live in the service module
(`app/services/machine_review/schemas.py`), not under `app/schemas/`. Public
trust shape: `app/services/trust/models.py` (`TrustFragment`,
`TrustLLMPrecheck`).

**docs/specs**

```text
provisional_machine_review.md
admin_machine_review_inspection.md
admin_machine_review_curator_task_api.md
machine_review_curator_workflow.md
machine_review_curator_task_queue.md
machine_review_admin_ui_mock.md
machine_review_golden_examples.md         (golden fake-provider examples)
machine_review_provider_contract_v2.md    (design: v2 provider output contract)
machine_review_handoff.md   (this file)
```

**OpenAPI golden** — `backend/tests/api/golden/openapi.json`. Updated by the
admin route commits (`2b0f8d4` inspection endpoint, `b4110e4` curator task API).
The curator task API commit fully-qualified the same-named DB/service enums (see
§9); regenerate with `UPDATE_OPENAPI_GOLDEN=1 pytest tests/api/test_openapi_snapshot.py`
after any intentional route/schema change. Doc-only commits (`3ee31e5`,
`489801a`, `9d04aba`, `1a17962`, this one) did not and must not change it.

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
The admin endpoints are require_admin, NOT curator-accessible.
Curator task state is persisted on a SEPARATE axis (MachineReviewCuratorTaskState)
  from MachineReviewStatus, RecordReviewStatus, and SubmissionStatus.
DB-layer MachineReviewStatus/MachineReviewSeverity mirror the service-layer
  token values but are SEPARATE classes (DB models must not import service
  Pydantic schemas); drift-guard tests keep the two token sets aligned.
finding_fingerprint EXCLUDES source_audit_event_id, model, provider, and
  timestamps so precheck re-runs reuse the task instead of duplicating it.
Warning/critical exact mapped findings create tasks; info findings do NOT by
  default; submission-scoped / unmapped / parse-warning never create tasks.
Curator task creation is EXPLICIT/admin-triggered, never automatic on upload
  or precheck.
Task lifecycle services do NOT enforce authorization (they take user ids and
  validate transitions only); the route layer enforces admin access.
resolved_human_reviewed does NOT write RecordReviewStatus — it records that a
  human review happened elsewhere.
The admin queue API uses require_admin, NOT curator access (raw workflow state).
FastAPI qualifies the same-named DB/service enums by module path in OpenAPI to
  avoid a component-name collision; this is COSMETIC (identical token values,
  drift-guarded), not a wire-level change.
No public trust.machine_review until an explicit product decision passes the
  exposure gate.
```

---

## 10. What NOT to do next

```text
Do NOT add machine_review to the public TrustFragment yet.
Do NOT make curator-task creation automatic on upload/precheck — keep it
  explicit/admin-triggered.
Do NOT grant curator access to the raw queue/API without a product decision
  (admin-only for now; see curator workflow spec §6).
Do NOT let resolved_human_reviewed (or any task lifecycle op) mutate
  RecordReviewStatus / submission.status / scientific records / public trust.
Do NOT include source_audit_event_id, model, provider, or timestamps in
  finding_fingerprint (it would break re-run dedup).
Do NOT map submission-level summaries to every linked record (no fan-out).
Do NOT use machine review to approve / reject / certify / hide records.
Do NOT mutate deterministic trust / evidence from machine review.
Do NOT overload submission.status or RecordReviewStatus for workflow state.
Do NOT edit deployed initial migrations (d861dfd60891 etc.); the curator task
  table shipped in its own revision (b8c9d0e1f2a3) and is now deployed-rules too.
Do NOT create a record_machine_review table before a product decision.
```

---

## 11. Recommended next options

The persisted curator task queue + admin API (the old "Option A") **and** the
golden fake-provider examples (the old "Option 1") are now **done** (see
`machine_review_golden_examples.md`). The golden examples surfaced a v1↔target
vocabulary gap, now **designed** (not yet built) in
`machine_review_provider_contract_v2.md`. From here:

```text
Option 1 (next, low risk): Implement the v2 provider output contract + adapter
          version dispatch designed in machine_review_provider_contract_v2.md,
          plus v2 golden examples, keeping all v1 golden tests passing. No real
          provider, no public API, no migration (version lives in details_json).

Option 1b (legacy, partly done): Add MORE golden fake-provider examples / fixtures
          (varied severities, categories, mapped/unmapped mixes). Base set already
          exists; extend as needed to evaluate false positives and tune the queue.

Option 2: Implement real provider plumbing behind off/cloud/local config
          (replace the fake provider). Larger; introduces external dependencies
          and cost/secret handling. Still advisory, still private.

Option 3: Build a frontend / admin UI for the queue (much later). Depends on the
          queue shape being validated first (see machine_review_admin_ui_mock.md).

Option 4: Design public trust.machine_review exposure — still behind the spec
          gate in provisional_machine_review.md §10. Highest risk; needs the
          exposure-gate questions answered from real triage experience first.

Option 5: Stop machine-review work and move to another backend area; the
          private/admin stack is complete and self-contained as-is.
```

**Recommendation: implement the v2 contract (Option 1) before real providers
(Option 2) or public exposure (Option 4).** It closes the documented vocabulary
gap (richer categories, `recommended_action`, `curator_priority`, native
`status`) with no provider, public-API, or migration change, and keeps every v1
payload valid — exactly the foundation a real provider (Option 2) and the
exposure gate (Option 4) need, at the lowest risk and cost.
