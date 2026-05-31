# Admin Machine-Review Curator Task API

**Status:** implemented — admin-only workflow API over the persisted
`machine_review_curator_task` table; no public read-API change, no public
`trust.machine_review`, no migration in this slice.
**Date:** 2026-05-31
**Scope:** TCKDB backend only. Admin-only endpoints for listing, inspecting,
explicitly building, assigning, starting review on, resolving, and reopening
machine-review curator tasks. No real LLM provider. No RAG. No automatic task
creation on upload/precheck. No public `trust.machine_review`. No public
scientific read change. No `TrustFragment` change. No ARC or `tckdb-client`
change.
**Audience:** TCKDB backend maintainers and deployment admins driving the
machine-review curator triage queue, before any product decision to expose
machine review publicly or to grant curators access.

**Related specs:**

- `machine_review_curator_task_queue.md` — the persisted `machine_review_curator_task`
  table design (identity/dedup §4, workflow-state enum §5, creation policy §6,
  resolution policy §7, non-interference §9, queue queries §10) that this API
  drives.
- `machine_review_curator_workflow.md` — the human-triage decision framework
  (state axis §4, safe-presentation §6, human-action policy §7, public-exposure
  gate §10) this API operationalises.
- `admin_machine_review_inspection.md` — the admin-only, read-only inspection
  endpoint that produces the record-level findings the *build* endpoint turns
  into tasks.
- `provisional_machine_review.md` — the future public-facing machine-review
  layer and its status/severity/category vocabulary; the source of the
  denormalised snapshot fields.
- `automated_trust_layer.md` — the deterministic evidence engine and
  `RecordReviewStatus` (authoritative human review) this API must never touch.
- `machine_review_handoff.md` — the workstream checkpoint; this API is its §11
  "Option A".

---

## 1. Required mental model

Four facts on four axes. This API owns exactly the second row and must never be
mistaken for the others:

```text
machine-review finding        = advisory signal (what the screener said)
curator task                  = admin/private workflow item over a finding   <-- THIS API
RecordReviewStatus            = authoritative human review outcome
public trust.machine_review   = NOT exposed
```

A curator task tracks **whether a human has handled a machine finding**. It
never approves, rejects, certifies, hides, or otherwise mutates a record; it is
a to-do item about a finding, never the finding's verdict. Resolving a task —
even as `resolved_human_reviewed` — does **not** write `RecordReviewStatus`;
the authoritative human review is performed elsewhere and merely *recorded* here
as the reason the task closed (queue spec §7/§8).

All routes are admin-only and live under:

```text
/api/v1/admin/machine-review/curator-tasks
```

---

## 2. Access policy

Every endpoint is gated by `require_admin`. Curator access is **deliberately
deferred** in this slice: the API exposes raw workflow/debugging state —
internal record ids, finding fingerprints, source audit-event ids, provider-/
model-derived snapshot status, unresolved diagnostics — i.e. the same raw
machine-review internals the inspection endpoint exposes (`admin_machine_review_inspection.md`
§6). Curator access can be reconsidered once a curated review UI presents this
safely rather than as raw diagnostics (curator workflow spec §6).

Access matrix (identical to the inspection endpoint):

| Caller | Result |
|---|---|
| Anonymous | `401` |
| Normal user | `403` |
| Curator | `403` |
| Admin | `200` (or `404`/`400` on the specific error cases below) |

Service-layer `DomainError` → `400` and `NotFoundError` → `404` are mapped by
the global handlers registered in `app/api/errors.py`; the route handlers also
raise `HTTPException(404)` directly for a missing task or submission.

---

## 3. Endpoints

### 3.1 List curator tasks

```http
GET /api/v1/admin/machine-review/curator-tasks
```

**Purpose:** Browse the queue, filtered, with a deterministic ranking.

**Query filters** (all optional, ANDed):

| Param | Type | Notes |
|---|---|---|
| `workflow_state` | enum | `untriaged` / `needs_curator_review` / `in_curator_review` / `resolved_no_action` / `resolved_human_reviewed` / `dismissed_machine_finding` |
| `assigned_to` | int | app_user id |
| `record_type` | enum | `SubmissionRecordType` (e.g. `kinetics`) |
| `record_id` | int | internal record id |
| `submission_id` | int | |
| `highest_severity` | enum | `info` / `warning` / `critical` |
| `limit` | int | default `50`, range `1–200` |
| `offset` | int | default `0`, `≥ 0` |

**Ordering** (deterministic, fixed):

```text
1. open states first        (untriaged / needs_curator_review / in_curator_review
                             before the three terminal states)
2. highest severity first   (critical > warning > info)
3. newest updated_at first
4. id descending            (final tie-break)
```

Terminal tasks are **included**; narrow with `workflow_state` to hide them.

**Response:** `PaginatedResponse[AdminCuratorTaskResponse]` —
`{ items: [...], total, skip, limit }`. `total` is the unpaginated count under
the same filters.

**Side effects:** none (served from `get_db`, read-only).
**Non-side-effects:** mutates nothing.
**Common errors:** `401` anonymous, `403` non-admin.

### 3.2 Get one task

```http
GET /api/v1/admin/machine-review/curator-tasks/{task_id}
```

**Purpose:** Fetch one task by id.
**Response:** `AdminCuratorTaskResponse` (§4).
**Side effects:** none.
**Common errors:** `404` if the task does not exist; `401`/`403` as above.

### 3.3 Build tasks for a submission

```http
POST /api/v1/admin/machine-review/curator-tasks/build-for-submission/{submission_id}
```

**Purpose:** Explicit, admin-triggered creation/upsert of curator tasks for one
submission, from the existing inspection projection.

**Mechanism:** loads the submission (404 if missing), projects its machine-review
audit events onto its linked records via `build_submission_machine_review_inspection`
(the same private stack behind the inspection endpoint), then calls
`build_curator_tasks_for_submission` to create/upsert tasks.

It **does**:

```text
run only when explicitly called (admin-triggered)
read existing submission_audit_event + submission_record_link rows
create/upsert tasks ONLY for exact mapped warning/critical record findings
upsert on (submission_id, record_type, record_id, finding_fingerprint)
reuse an existing open task (optionally refreshing its denormalised snapshot)
skip an existing terminal task (never reopened here)
```

It **skips** (never creates a task for):

```text
info findings
submission-scoped findings (no record_type)
unmapped findings (unknown type / missing ref / unlinked record)
parse warnings (the reviewer's own output was unusable)
```

It **does not**:

```text
run automatically on upload or precheck
mutate submission.status
mutate scientific records, RecordReviewStatus, evidence, or public trust
```

**Request body:** none.

**Response:** `AdminCuratorTaskBuildResponse` (mirrors the service
`CuratorTaskBuildResult`):

| Field | Meaning |
|---|---|
| `created_count` | New tasks inserted. |
| `reused_count` | Existing open tasks matched (and, by default, snapshot-refreshed). |
| `refreshed_count` | Sub-count of `reused_count` whose snapshot was refreshed in place. |
| `skipped_info_count` | Info-severity findings that opened no task. |
| `skipped_unmapped_count` | Submission-scoped + unmapped diagnostics not turned into tasks. |
| `skipped_terminal_count` | Matching tasks already in a terminal state (left untouched). |
| `task_ids` | Ids of the tasks created or reused this run. |
| `warnings` | Non-fatal builder notes (e.g. a record with no resolvable internal id). |

**Side effects:** inserts/updates rows **only** in `machine_review_curator_task`
(served via `get_write_db`).
**Common errors:** `404` if the submission does not exist; `401`/`403`.

### 3.4 Assign

```http
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/assign
```

**Purpose:** Set or clear a task's assignee.

**Request body:**

```json
{ "assignee_id": 123 }
```

Unassign:

```json
{ "assignee_id": null }
```

**Explain:**

```text
assignment changes assigned_to only
assignment does NOT change workflow_state
assignment does NOT resolve the task
assignment does NOT alter human review or submission state
```

A terminal task is rejected (`400`) — a resolved/dismissed task is not
re-assignable through this endpoint.

**Response:** the updated `AdminCuratorTaskResponse`.
**Common errors:** `404` missing task; `400` assigning a terminal task;
`401`/`403`.

### 3.5 Start review

```http
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/start-review
```

**Purpose:** Move an open task into `in_curator_review`.

**Request body:** optional; defaults applied when omitted:

```json
{ "actor_user_id": 123, "assign_actor_if_unassigned": true }
```

**Explain:**

```text
untriaged / needs_curator_review -> in_curator_review
already in_curator_review        -> idempotent (no error, no state change)
terminal task                    -> rejected (400)
actor defaults to the authenticated admin (actor_user_id overrides)
auto-assigns the actor when the task is currently unassigned
                                 (disable with assign_actor_if_unassigned=false)
does NOT change RecordReviewStatus or submission state
```

**Response:** the updated `AdminCuratorTaskResponse`.
**Common errors:** `404` missing task; `400` starting review on a terminal task;
`401`/`403`.

### 3.6 Resolve

```http
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/resolve
```

**Purpose:** Close a task into one of the terminal states.

**Allowed `resolution_state`:**

```text
resolved_no_action          looked, no DB change warranted, not routed to review
resolved_human_reviewed     a human review happened via the real review layer
dismissed_machine_finding   judged a false positive / not actionable
```

**Request body:**

```json
{
  "resolution_state": "dismissed_machine_finding",
  "resolution_note": "False positive: note is descriptive; tunneling was not modeled."
}
```

**Explain:**

```text
resolution_note is REQUIRED and non-empty (blank/whitespace -> 400)
resolved_by is the authenticated admin (not client-supplied)
resolved_at is set by the service
the resolved_at/resolved_by/resolution_note triple is set together
resolved_human_reviewed does NOT itself write RecordReviewStatus
  (it only records that a human review happened elsewhere)
```

Idempotency: re-resolving an already-terminal task with the **same**
`resolution_state` is a no-op that returns the existing row unchanged
(preserving the original resolver/note/timestamp); a **different** terminal
state is rejected (`400`) — reopen first to change a resolution. A non-terminal
`resolution_state` is rejected (`400`).

**Response:** the updated `AdminCuratorTaskResponse`.
**Common errors:** `404` missing task; `400` blank note / non-terminal target /
conflicting re-resolution; `401`/`403`.

### 3.7 Reopen

```http
POST /api/v1/admin/machine-review/curator-tasks/{task_id}/reopen
```

**Purpose:** Reopen a terminal task into an open state.

**Allowed `target_state`:**

```text
untriaged
needs_curator_review     (default)
in_curator_review
```

**Request body:** optional; defaults applied when omitted:

```json
{ "target_state": "needs_curator_review", "clear_assignment": false }
```

**Explain:**

```text
only a terminal task can be reopened (an already-open task -> 400)
reopen clears resolved_at / resolved_by / resolution_note
assignment (assigned_to) is preserved unless clear_assignment=true
does NOT mutate scientific records, RecordReviewStatus, or submission state
```

**Response:** the updated `AdminCuratorTaskResponse`.
**Common errors:** `404` missing task; `400` reopening a non-terminal task or an
out-of-range `target_state`; `401`/`403`.

---

## 4. Response schema

The task response is its own admin-only schema, `AdminCuratorTaskResponse`,
distinct from any public scientific schema and from the public `TrustFragment`.
It is `extra="forbid"`, so no provider-supplied or mutation field can be
smuggled through. It carries **no** public `trust.machine_review`.

| Field | Meaning |
|---|---|
| `id` | Task id. |
| `submission_id` | The submission the task belongs to. |
| `record_type` | `SubmissionRecordType` of the addressed record. |
| `record_id` | Internal record id (admin-only surface; never publicly serialised). |
| `finding_fingerprint` | Stable SHA-256 identity of the finding (derived; never client-supplied). |
| `workflow_state` | `MachineReviewCuratorTaskState` — the human triage axis. |
| `machine_review_status` | Denormalised advisory snapshot (display/ranking only). |
| `highest_severity` | Denormalised highest finding severity (display/ranking only). |
| `findings_count` | Denormalised record-level finding count at task creation/refresh. |
| `source_audit_event_id` | Provenance back to a machine-review audit event (nullable). |
| `assigned_to` | Current assignee app_user id, or `null`. |
| `created_at` | Naive-UTC creation timestamp. |
| `updated_at` | Naive-UTC last-touched timestamp. |
| `resolved_at` | Set on terminal states; `null` while open. |
| `resolved_by` | Resolver app_user id; `null` while open. |
| `resolution_note` | Required free-text justification on terminal states; `null` while open. |

`machine_review_status`, `highest_severity`, and `findings_count` are a
**denormalised snapshot** for cheap queue ranking/filtering — never
authoritative (queue spec §3). The request bodies (`AdminCuratorTaskAssignRequest`,
`AdminCuratorTaskStartReviewRequest`, `AdminCuratorTaskResolveRequest`,
`AdminCuratorTaskReopenRequest`) are likewise `extra="forbid"`.

---

## 5. OpenAPI enum qualification note

The DB-layer `MachineReviewStatus` / `MachineReviewSeverity`
(`app/db/models/common.py`, referenced by `AdminCuratorTaskResponse`) and the
service-layer `MachineReviewStatus` / `MachineReviewSeverity`
(`app/services/machine_review/schemas.py`, referenced by the inspection
endpoint's `MachineReviewRecordSummary`) **intentionally share token values but
are separate classes** — the DB layer cannot import service-layer Pydantic
schemas, so the vocabulary is mirrored.

Because two distinct classes share a name, FastAPI qualifies the OpenAPI
component names by module path to avoid a collision, e.g.:

```text
app__db__models__common__MachineReviewStatus
app__services__machine_review__schemas__MachineReviewStatus
```

This is **cosmetic at the wire level** — the emitted enum *values* are identical
on both sides, and drift-guard tests pin the DB-layer and service-layer token
sets to each other. Clients consume the same string tokens regardless of the
qualified component name.

---

## 6. Non-interference

These endpoints are a workflow tracker, not a review authority. Writes are
confined to rows in `machine_review_curator_task` (the read endpoints write
nothing). No endpoint here mutates, sets, or gates any of:

```text
submission.status
RecordReviewStatus
benchmark_reference
is_certified
deterministic evidence (evidence_completeness, passed/missing/warning/
  not_applicable checks, hard_fail_reason, trust_status)
scientific records
public trust fragments (trust.*, including trust.machine_review)
```

This is the same authority boundary asserted across the machine-review
workstream: human review wins on any conflict, and machine signals — including
this workflow over them — are advisory context only. A public-trust regression
test asserts the public `TrustFragment` still has no `machine_review` and that
`trust.llm_precheck` stays `enabled=false`, `label=not_run`, `summary=null`
after exercising this API.

---

## 7. Non-goals

```text
No public trust.machine_review.
No curator access yet.
No automatic task creation on upload/precheck.
No frontend.
No real provider / RAG changes.
No ARC / tckdb-client changes.
No new migration in this slice.
```

When curator access, a curated UI, or public machine-review exposure lands,
retire the relevant deferral notes here and in `machine_review_curator_workflow.md`
(§6/§10) and `machine_review_handoff.md` (§11).
