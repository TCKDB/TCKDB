# Machine-Review Workstream — Handoff / Checkpoint

**Status:** handoff document for the machine-review workstream as of
2026-05-31. The private/admin stack is now **implemented and tested** through
the persisted curator task queue, its admin workflow API, the v2 provider
contract, and the (unwired) provider-plumbing foundation — including
**persisted v2 ingestion/readback** end-to-end. What remains **spec-only /
deferred** is public `trust.machine_review` exposure, a curator-facing UI, and
**real** (non-fake) Cloud/Local provider calls. This document states exactly
which is which.
**Date:** 2026-05-31 (updated after persisted v2 provider ingestion/readback)
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
| v2 provider contract | `app/services/machine_review/schemas.py` | `MachineReviewProviderResultV2`, `MachineReviewProviderFindingV2`, `MACHINE_REVIEW_V2_SCHEMA_VERSION` — native machine-review provider payload (`extra="forbid"`, `used_rag=Literal[False]`) |
| Status derivation | `app/services/machine_review/derivation.py` | `MachineReviewOutcome` + `derive_machine_review_status()` — pure, deterministic |
| Safe mapping | `app/services/machine_review/mapping.py` | `map_findings_to_submission_records()` — exact identity, no fan-out |
| Latest-record read model | `app/services/machine_review/read_model.py` | `RecordMachineReview`, `MachineReviewRecordSummary`, `select_latest_machine_review_for_record()` |
| Audit-event adapter | `app/services/machine_review/audit_adapter.py` | `submission_audit_event.details_json` -> `RecordMachineReview` tuples; **dispatches on `schema_version`**: v2 payload validated directly, no marker => legacy v1 `LLMPrecheckResult` translate, unknown version => parse warning |
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
| Provider plumbing spec | `docs/specs/machine_review_real_provider_plumbing.md` | Producer-side design (off/cloud/local, provider package, config reuse of `LLM_PRECHECK_*`, failure contract, fake/test boundary, 1→6 order) |
| Provider plumbing foundation | `app/services/machine_review/providers/` | `MachineReviewProvider` protocol + `MachineReviewContext` (`interface.py`); `DisabledMachineReviewProvider` (`disabled.py`); test-only `FakeMachineReviewProvider` + `make_pass/warning/critical/failed_result` (`fake.py`); `build_machine_review_provider()` factory (`factory.py`); `parse_machine_review_v2_payload()` / `machine_review_v2_result_to_details_json()` boundary helpers. **Unwired into production flow**; cloud/local validate config then raise `NotImplementedError` (no real call) |
| v2 audit-event recorder | `app/services/submission.py` | `record_machine_review_v2_audit_event()` — minimal glue paralleling `record_llm_precheck_audit_event()`: serializes a `MachineReviewProviderResultV2` into `details_json` (carrying `schema_version`), writes an `llm`/`llm_precheck_recorded` event, flush-only, no status mutation, **not** wired into upload/precheck |
| Persisted v2 readback tests | `tests/api/test_admin_machine_review_persisted_readback.py` | Prove the full persisted path: provider result → `record_machine_review_v2_audit_event` → real `submission_audit_event` row → DB readback → admin inspection → curator-task build → admin task API |

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
66503d5  Update machine-review handoff after curator task queue API
34abc6f  Add golden fake-provider machine-review examples
e08efbf  Spec machine-review provider output contract v2
531d3ad  Implement machine-review provider contract v2 schemas + adapter support
48dfabb  Spec real machine-review provider plumbing (off/cloud/local)
269db30  Implement machine-review v2 provider plumbing foundation
76e8305  Add persisted machine-review v2 provider ingestion/readback tests
```

(`f83178a` is the foundational spec. The five commits from `78cceaa` onward are
the curator task queue slice — model/migration, creation service, lifecycle
service, admin API, and its docs. The golden examples (`34abc6f`) then the v2
provider contract — spec (`e08efbf`) + implementation (`531d3ad`) — follow. The
last three commits are the provider-plumbing slice: its design spec
(`48dfabb`), the unwired producer foundation (`269db30` — disabled/fake v2
providers, factory, parse/serialize helpers), and the persisted v2
ingestion/readback tests + `record_machine_review_v2_audit_event` glue
(`76e8305`).)

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
record_machine_review_v2_audit_event is explicit/caller-driven only; it is NOT
  wired into the upload/precheck flow and nothing invokes it in production.
No real Cloud/Local model calls exist — the provider package is unwired and
  cloud/local modes validate config then raise NotImplementedError.
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

Provider plumbing + persisted v2 ingestion/readback invariants
(`tests/services/test_machine_review_provider_plumbing.py`,
`tests/api/test_admin_machine_review_persisted_readback.py`):

```text
off mode builds the disabled provider and needs no API key/model/base URL
disabled provider returns a valid v2 not_run result
fake v2 provider returns a valid machine_review_v2 payload (pass/warning/critical/failed)
fake v2 provider can emit v2-only categories (transition_state_validation, schema_gap)
factory never exposes the fake provider for any AI_REVIEW_ASSISTANT_MODE value
cloud/local modes validate required config, then raise NotImplementedError (no call)
parse_machine_review_v2_payload rejects used_rag=true and extra/mutation fields
v2 details_json persists schema_version and findings (survives DB readback)
v2 recommended_action survives persistence/readback
v2-only categories (e.g. transition_state_validation) survive persistence/readback
malformed v2 payload (used_rag=true) becomes a parse warning, never an exception
legacy v1 (marker-less) event still maps to a record summary and creates a task
submission-scoped v2 finding stays diagnostic only (no summary, no task, no warning)
unlinked v2 finding stays diagnostic only (mapping warning, no summary, no task)
persisted v2 path creates one needs_curator_review task with source_audit_event_id resolved
public TrustFragment still has no machine_review after the full persisted path
submission lifecycle fields and RecordReviewStatus remain unchanged
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
FakeMachineReviewProvider / future Cloud|Local provider   (off=Disabled; unwired)
  -> MachineReviewProviderResultV2     (validated v2 contract; used_rag=Literal[False])
  -> record_machine_review_v2_audit_event (explicit/caller-driven; flush-only; no status mutation)
  -> submission_audit_event.details_json  (schema_version="machine_review_v2"; event_kind=
                                        llm_precheck_recorded, actor_kind=llm)
  -> DB readback                       (real row; proven by persisted-readback tests)
  -> adapter dispatch on schema_version:
       "machine_review_v2"  -> validate MachineReviewProviderResultV2 directly
       absent (legacy v1)   -> validate LLMPrecheckResult + translate
       unknown version      -> parse warning (no record reviews)
  -> MachineReviewResult               (both versions converge here)
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

Everything in that chain is private/admin-only. The producer (provider →
`record_machine_review_v2_audit_event`) is **not** wired into the upload/precheck
flow — it runs only when an admin/test explicitly invokes it; the fake provider
is the only producer today and no real Cloud/Local call exists. From the audit
event onward the read/projection steps are pure (the inspection endpoint); the
curator-task steps persist and mutate **only** `machine_review_curator_task`
rows.

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
providers/interface.py    MachineReviewProvider protocol, MachineReviewContext,
                          parse_machine_review_v2_payload(), machine_review_v2_result_to_details_json(),
                          MachineReviewProviderConfigurationError
providers/disabled.py     DisabledMachineReviewProvider (off; v2 not_run)
providers/fake.py         FakeMachineReviewProvider + make_pass/warning/critical/failed_result (TEST-ONLY)
providers/factory.py      build_machine_review_provider() (off->disabled; cloud/local->validate+NotImplementedError)
```

(Precheck source: `backend/app/services/llm_precheck/` — `schemas.py`
`LLMPrecheckResult`, `providers.py` fake provider. v2 audit-event recorder glue:
`backend/app/services/submission.py` `record_machine_review_v2_audit_event()`,
paralleling `record_llm_precheck_audit_event()`.)

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
backend/tests/services/test_machine_review_provider_plumbing.py      (provider foundation)
backend/tests/api/test_admin_machine_review_inspection.py
backend/tests/api/test_admin_machine_review_curator_tasks.py         (admin queue API)
backend/tests/api/test_admin_machine_review_persisted_readback.py    (persisted v2 ingestion/readback)
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
machine_review_real_provider_plumbing.md  (design: off/cloud/local producer)
machine_review_handoff.md   (this file)
```

**OpenAPI golden** — `backend/tests/api/golden/openapi.json`. Updated by the
admin route commits (`2b0f8d4` inspection endpoint, `b4110e4` curator task API).
The curator task API commit fully-qualified the same-named DB/service enums (see
§9); regenerate with `UPDATE_OPENAPI_GOLDEN=1 pytest tests/api/test_openapi_snapshot.py`
after any intentional route/schema change. Doc-only commits (`3ee31e5`,
`489801a`, `9d04aba`, `1a17962`, `e08efbf`, `48dfabb`, this one) did not and
must not change it. The provider-plumbing code commits (`269db30`, `76e8305`)
added no FastAPI route or response schema — only service/provider modules and
tests — so they leave the OpenAPI golden untouched as well.

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
v2 provider payloads are dispatched by a single root schema_version marker
  ("machine_review_v2"); absence => legacy v1 path, unknown version => parse
  warning (never silently treated as v1). v2 is additive — no new event kind,
  no migration; the version lives in details_json. Both versions converge on
  the internal MachineReviewResult, so downstream is unchanged.
record_machine_review_v2_audit_event PARALLELS the v1
  record_llm_precheck_audit_event instead of replacing it; v1 persistence stays
  until all v1 tests/fixtures are migrated.
v2 persistence KEEPS event_kind=llm_precheck_recorded and actor_kind=llm; the
  version lives inside details_json via schema_version (no new event kind, no
  migration). A future machine_review_recorded rename is deferred.
The provider package remains UNWIRED into the production upload/precheck flow;
  record_machine_review_v2_audit_event is explicit/caller-driven only.
The fake v2 provider is test/evaluation plumbing, NOT a deployer-facing mode;
  the factory refuses to build it for any AI_REVIEW_ASSISTANT_MODE value
  (including "test") — it is reachable only via build_fake_machine_review_provider().
Config namespace is REUSED (AI_REVIEW_ASSISTANT_MODE + LLM_PRECHECK_*); no
  parallel MACHINE_REVIEW_* env vars (deferred rename only).
Task creation remains EXPLICIT, never automatic — unchanged by the provider work.
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
Do NOT call real LLM providers from tests (or anywhere yet); cloud/local stay
  config-validated NotImplementedError until a deliberate real-provider slice.
Do NOT expose machine_review publicly / grant curator access to the raw admin
  APIs without a product decision passing the exposure gate.
Do NOT replace the v1 persistence path (record_llm_precheck_audit_event) before
  all v1 tests/fixtures are migrated to v2; keep the two side by side.
Do NOT make record_machine_review_v2_audit_event run automatically on upload/
  precheck — it is explicit/caller-driven by design.
```

---

## 11. Recommended next options

The v2 provider contract, the unwired provider-plumbing foundation
(disabled/fake providers + factory + parse/serialize helpers), and **persisted
v2 ingestion/readback** (provider result → real `submission_audit_event` →
inspection → curator task) are all now **done** and tested. The producer is
deliberately not wired into the upload/precheck flow, and no real Cloud/Local
call exists. From here:

```text
Option 1: Wire the fake v2 provider into the optional precheck runner so there
          is a SINGLE service path for fake v2 runs (still no real calls). Small;
          would invoke build_machine_review_provider + record_machine_review_v2_
          audit_event from one runner, keeping it explicit/admin-triggered.

Option 2: Implement real Cloud/Local provider STUBS behind config (the
          NotImplementedError branches), with mocked transports and tests. No
          real network calls yet. See machine_review_real_provider_plumbing.md
          §15 steps 3-4.

Option 3: Implement real provider CALLS (an actual external/local model behind
          explicit config). Largest; introduces external dependencies, cost, and
          secret handling. Still advisory, still private. Spec §15 steps 5-6.

Option 4: Build a frontend / admin UI for the queue (much later). Depends on the
          queue shape being validated first (see machine_review_admin_ui_mock.md).

Option 5: Design public trust.machine_review exposure — still behind the spec
          gate in provisional_machine_review.md §10. Highest risk; needs the
          exposure-gate questions answered from real triage experience first.

Option 6: Pause machine-review work and move to another backend area; the
          private/admin stack is complete and self-contained as-is.
```

**Recommendation: pause here, or do Option 1 only if you want a single service
path for fake v2 runs. Do NOT implement real providers (Options 2-3) or public
exposure (Option 5) yet** — the private/admin stack is complete, self-contained,
and proven end-to-end through persisted v2 readback, so further work should wait
on a real product driver (a deployer who wants a model, or the exposure gate's
questions answered from real triage experience).
