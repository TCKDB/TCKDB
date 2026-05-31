# Machine-Review Curator Task Queue

**Status:** draft spec — design only. No code, no API change, no migration, no
public `trust.machine_review`. Designs a *future* persisted table that would
hold human triage state over machine-review findings.
**Date:** 2026-05-31
**Scope:** TCKDB backend design only. No implementation. No migration. No
endpoint change. No real LLM provider. No RAG. No public machine review. No
automatic task creation. No frontend work. No ARC or `tckdb-client` change.
**Audience:** TCKDB backend maintainers, trust-layer authors, and whoever
eventually builds the curator review queue/UI.

**Related specs:**

- `machine_review_curator_workflow.md` — the decision framework this table would
  make persistent; defines the triage state axis (§4 there) and the persistence
  options (§9 there). This spec is the detailed design of its "curator task
  table" option.
- `admin_machine_review_inspection.md` — the admin-only, read-only inspection
  endpoint that produces the projected record-level findings a task would be
  built from.
- `provisional_machine_review.md` — the future public-facing machine-review
  layer and its status/severity/category vocabulary; the `record_machine_review`
  comparison in §11 below comes from there.
- `automated_trust_layer.md` — the deterministic evidence engine and
  `RecordReviewStatus` (authoritative human review) this table must never touch.
- `optional_llm_precheck.md` — the precheck plumbing whose `submission_audit_event`
  rows are the upstream source of findings.
- `admin_machine_review_curator_task_api.md` — the implemented admin-only
  workflow API (list/get/build/assign/start-review/resolve/reopen) over the
  table this spec designs.

---

## 1. Why this spec exists

`machine_review_curator_workflow.md` §9 lists three persistence options for the
curator workflow and recommends a "submission/record-level curator task table"
if a real queue is ever built — but stops at one table-row sketch. This spec
fills that in: the exact identity of a task, how a machine finding becomes one,
how it is resolved, how it relates to human review and to a possible future
`record_machine_review` table, the queries it must serve, and the
non-interference guarantees it must hold.

Nothing here is built. There is no migration in this spec. It is the design a
future Alembic revision would implement.

---

## 2. Required mental model

Four facts, kept on four separate axes. This table owns exactly one of them.

```text
machine-review finding = advisory signal     (what the screener said)
curator task           = human workflow item created from an advisory signal
RecordReviewStatus     = authoritative human review outcome
SubmissionStatus       = submission lifecycle / moderation
```

> A curator task tracks **whether a human has handled a machine finding**.
> It must not itself approve, reject, certify, hide, or otherwise mutate a
> record. It is a to-do item about a finding, never the finding's verdict.

A single record can hold all four facts at once and they do not contradict:

```text
MachineReviewStatus            = machine_screened_warning   (advisory)
RecordReviewStatus             = not_reviewed                (authoritative; unchanged)
SubmissionStatus               = approved                    (lifecycle; unchanged)
MachineReviewCuratorTaskState  = needs_curator_review        (this table; "a human should look")
```

Resolving the task changes only the fourth value. Any change to the second
(human review) happens through the real review system and is merely *recorded*
by the task as the reason it was resolved.

---

## 3. Proposed table

Naming follows project conventions: `snake_case` table, `BigInteger` PKs,
`record_type` (enum) + `record_id` (`BigInteger`) record addressing as in
`submission_record_link`, `TimestampMixin` for `created_at`/`updated_at`.

```text
machine_review_curator_task

  id                     BigInteger PK
  submission_id          BigInteger FK -> submission.id, NOT NULL
  record_type            SubmissionRecordType enum, NOT NULL
  record_id              BigInteger, NOT NULL            -- internal record id (private table)
  finding_fingerprint    String/char hash, NOT NULL      -- stable identity of the finding (see §4)

  workflow_state         MachineReviewCuratorTaskState enum, NOT NULL, default 'untriaged'

  -- denormalised advisory snapshot (display/ranking only; never authoritative)
  machine_review_status  MachineReviewStatus enum, NOT NULL
  highest_severity       MachineReviewSeverity enum, NOT NULL
  findings_count         Integer, NOT NULL, default 1

  -- provenance back to the advisory signal
  source_audit_event_id  BigInteger FK -> submission_audit_event.id, nullable

  -- assignment / lifecycle
  assigned_to            BigInteger FK -> app_user.id, nullable
  created_at             timestamp, NOT NULL   (TimestampMixin)
  updated_at             timestamp, NOT NULL   (TimestampMixin)

  -- resolution (all three set together when leaving an open state; see §7)
  resolved_at            timestamp, nullable
  resolved_by            BigInteger FK -> app_user.id, nullable
  resolution_note        Text, nullable
```

Notes:

- `record_id` is the raw internal id. This is acceptable because the table is
  **private** (admin/curator-only) and never serialised onto the public read
  surface — consistent with `submission_record_link`, which also stores raw
  `record_id`. The internal-id-exposure policy still applies to any
  *response body*, never to this storage row.
- `machine_review_status`, `highest_severity`, and `findings_count` are a
  **denormalised snapshot** of the advisory signal at task-creation time, kept
  for cheap queue ranking/filtering without recomputing the inspection
  projection. They are display metadata, never authority. If a newer review
  supersedes the finding, the policy in §6 governs whether the snapshot is
  refreshed.
- `source_audit_event_id` is nullable so a task can outlive log compaction or be
  created from a recomputed projection that no longer points at a single event.

---

## 4. Identity and deduplication

A task is **one human to-do about one finding on one record in one submission**.
Its natural identity is:

```text
(submission_id, record_type, record_id, finding_fingerprint)
```

This is enforced as a `UniqueConstraint` (mirroring
`uq_submission_record_link_identity`). The task builder upserts on this tuple:
if a task already exists for the same finding on the same record in the same
submission, no second task is created — the existing one is reused (and, per
§6, optionally has its denormalised snapshot refreshed). This is what prevents
re-running machine review, or recomputing the projection, from spawning
duplicate work items.

### `finding_fingerprint`

`finding_fingerprint` is a stable hash over the **identity-bearing** fields of a
finding — the fields that make it "the same concern" rather than "a new
concern". From `MachineReviewFinding` (and its surrounding review context):

```text
severity
category
record_type
record_ref / record_id
message
evidence_keys            (order-normalised)
recommended_action
source review context     (the projection context, NOT the raw audit event id)
```

Computed as a stable digest (e.g. SHA-256 over a canonicalised, sorted
serialisation), per the project convention of computing derived hashes in the
service layer — never accepting them from a client and never storing them on a
Create/Update upload schema.

**Should the fingerprint include `source_audit_event_id`?** No. Including the
event id would make every re-run of the precheck (each producing a fresh audit
event) look like a brand-new finding and defeat deduplication. Instead the
fingerprint folds in a *review-context hash* (the stable identity of the
projection/finding content), and the raw `source_audit_event_id` is stored
separately as nullable provenance only.

**Should the fingerprint include model/provider?**

```text
Recommendation: do NOT include model/provider in the default
finding_fingerprint — UNLESS the product intentionally wants different models
to produce separate, independently-triaged tasks for the same concern.
```

By default, "the kinetics note mentions tunneling but `tunneling_model` is null"
is *the same finding* whether GPT-X or Claude-Y raised it, so the two should
collapse to one task a curator handles once. Folding the model string into the
fingerprint would instead create two tasks for the identical concern — almost
always noise. The model/provider remains discoverable through
`source_audit_event_id` for debugging without fragmenting the queue. Revisit
only if a deliberate "compare reviewers head-to-head" workflow is wanted.

---

## 5. Workflow state enum

A dedicated enum, centralised in `app/db/models/common.py` per the schema rules
(never inline). Values are machine-friendly tokens, per project convention:

```text
class MachineReviewCuratorTaskState(str, Enum):
    untriaged
    needs_curator_review
    in_curator_review
    resolved_no_action
    resolved_human_reviewed
    dismissed_machine_finding
```

These map 1:1 to the workflow-state axis in
`machine_review_curator_workflow.md` §4. The first three are **open** states
(work outstanding); the last three are **terminal/resolved** states.

State clearly, and enforce by giving it its own enum and its own column:

```text
This is NOT MachineReviewStatus   (advisory machine output).
This is NOT RecordReviewStatus    (authoritative human review).
This is NOT SubmissionStatus      (submission lifecycle / moderation).
```

Conflating any of these into another column would corrupt the authoritative
human-review or moderation vocabularies — the entire reason for a fourth axis.

Typical transitions:

```text
untriaged ----------------> needs_curator_review      (triaged into the queue)
needs_curator_review ------> in_curator_review         (a curator picks it up)
in_curator_review ---------> resolved_no_action         | resolved
                          -> resolved_human_reviewed    | terminal
                          -> dismissed_machine_finding   | states
```

Reopening (terminal -> open) is allowed but must clear `resolved_*` fields and
should be auditable; design it explicitly when implemented rather than leaving
it implicit.

---

## 6. Task creation policy

Tasks would be created **only for exact, mapped, record-level findings** — the
output of the inspection projection that landed on a specific `(record_type,
record_id)`.

```text
CREATE a task for:    exact mapped record-level findings only
Do NOT create for:    submission-scoped findings (no specific record)
Do NOT create for:    unmapped findings (couldn't be tied to a record)
Do NOT create for:    parse warnings (the reviewer's own output was unusable)
Optional, later:      admin-only diagnostic tasks for parse/mapping warnings,
                      clearly flagged as operability issues, NOT science triage
```

Rationale matches `machine_review_curator_workflow.md` §6: a parse failure means
the *reviewer's* output was unusable — an admin/operability concern, not a
curation signal about the science. Surfacing it as a curator task would be noise
at best, misleading at worst.

### Initial state by severity

```text
critical finding  ->  needs_curator_review
warning finding   ->  needs_curator_review
info finding      ->  no task   (recommended default)
```

**Recommendation:** critical and warning findings open directly as
`needs_curator_review` (they are, by definition, things worth a human glance);
`info` findings create **no task** by default — they are advisory colour, and
auto-opening tasks for them would flood the queue with low-value items. If
product experience later shows specific `info` categories deserve tracking,
open those as `untriaged` (visible but unranked) rather than `needs_curator_review`.

### When creation runs

```text
No automatic task creation in this spec or its first implementation.
Task creation is an explicit, admin-triggered (or batch) build step over the
existing inspection projection — never a side effect of upload, precheck, or
human review.
```

This keeps the table off the hot submission path and preserves the
non-interference guarantee (§9): building a task reads diagnostics and writes
only to `machine_review_curator_task`.

---

## 7. Resolution policy

Leaving an open state means a human has handled the finding. The three terminal
states mean distinct things:

```text
resolved_human_reviewed
    A human used the REAL human-review system and changed or confirmed
    RecordReviewStatus (e.g. moved the record to approved/rejected/deprecated,
    or affirmatively confirmed not_reviewed-as-fine via the review layer).
    The task records that this happened; it did not perform it.

dismissed_machine_finding
    A human judged the machine finding a false positive / not actionable and
    dismissed it. No database record changed. The finding stays as audit
    context (it is not deleted), but the task is closed.

resolved_no_action
    A human inspected the issue, decided no database change is warranted, and
    did NOT route it through the human-review layer either. ("Looked, it's
    fine, nothing to do.") Distinct from dismissed: the finding wasn't judged
    wrong, the record just needs no change.
```

Any resolution requires, set together atomically:

```text
resolved_by      (the acting human; app_user.id)
resolved_at      (timestamp)
resolution_note  (free-text justification — required, not optional)
```

A required `resolution_note` makes every closed task auditable: a future reader
can always see *why* a finding was considered handled, which is essential before
any of this could inform a public machine-review state (workflow spec §10 gate).

---

## 8. Relationship to human review

The task table is a workflow tracker, not a review authority.

```text
Approving / rejecting / deprecating a record happens through RecordReviewStatus,
  via the existing human-review layer — NEVER through this table.
A task MAY link to the human-review event / record-review row later (an optional
  FK or audit pointer), to show "this task was resolved because review X happened".
A task can be RESOLVED because a human review occurred (resolved_human_reviewed),
  but the task is not, and never becomes, the human review itself.
```

Concretely: closing a task as `resolved_human_reviewed` does **not** write
`RecordReviewStatus`. The flow is the reverse — a human performs the review
through the authoritative layer, and *then* the task is closed to reflect it.
If the two ever disagree (task says resolved, review says `not_reviewed`), the
review layer is authoritative; the task is just stale workflow metadata.

---

## 9. Non-interference policy

The task table must **never** mutate, set, or gate any of:

```text
submission.status
RecordReviewStatus
benchmark_reference
is_certified
deterministic evidence (computed_*_v1 outputs, evidence_completeness, checks)
scientific records (species/reaction/kinetics/thermo/... domain tables)
public trust fragments (trust.*, including trust.machine_review)
```

Writes from any task operation are confined to rows in
`machine_review_curator_task`. Reads may join freely (it is a private surface),
but a task create/assign/resolve is a write to this table and nothing else. This
is the same authority boundary asserted by the non-interference tests and
`provisional_machine_review.md`: human review wins on any conflict, and machine
signals — including this workflow over them — are advisory context only.

---

## 10. Queue queries and indexes

Expected access patterns:

```text
list open tasks ordered by severity / status / reviewed_at
filter by workflow_state
filter by assigned_to
filter by record_type
filter by submission_id
find all tasks for one record            (record_type, record_id)
find tasks linked to one source_audit_event_id
count open tasks by severity / state     (dashboard tiles)
```

Suggested indexes (final names follow `NAMING_CONVENTION`; illustrative here):

```text
UniqueConstraint(submission_id, record_type, record_id, finding_fingerprint)
                                                  -- identity / dedupe (§4)
ix ... (workflow_state)                           -- queue filter; the common case
ix ... (workflow_state, highest_severity)         -- "open, ranked by severity"
ix ... (assigned_to)                              -- "my queue"
ix ... (record_type, record_id)                   -- tasks for one record
ix ... (submission_id)                            -- tasks for one submission
ix ... (source_audit_event_id)                    -- trace back to the signal
```

The hottest query — "open tasks, highest severity first" — is served by the
`(workflow_state, highest_severity)` composite (filter on the open states, sort
by severity). `reviewed_at` ordering within that can lean on `updated_at` or the
denormalised snapshot; add a dedicated index only if profiling shows the sort is
expensive. Don't pre-add indexes for query shapes that aren't yet real.

---

## 11. Relationship to `record_machine_review`

Two distinct future tables, often confused:

```text
machine_review_curator_task   = human WORKFLOW over findings
                                (triage state, assignee, resolution, notes)

record_machine_review         = persisted record-level machine-review RESULT
                                (the projected status/findings themselves,
                                 per provisional_machine_review.md §6 Option B)
```

```text
They are ORTHOGONAL — one stores human workflow, the other stores machine output.
If only workflow state is needed, implement the task table FIRST. It can be
  built entirely on today's on-demand inspection projection (workflow spec
  Option A) plus this new table; no persisted machine result is required.
Implement record_machine_review LATER, only if public machine_review exposure or
  expensive recomputation makes a stable persisted machine result worth its own
  table. The task table does not depend on it.
```

If both eventually exist, a task may reference its `record_machine_review` row
instead of (or alongside) `source_audit_event_id`, but that is an additive
enhancement, not a prerequisite.

---

## 12. Example

```text
1. A machine finding warns that a kinetics record's note mentions tunneling but
   tunneling_model is null. It is an advisory warning finding, mapped exactly to
   kinetics record 101 in submission 42, recorded as a submission_audit_event.

2. The (admin-triggered) task builder computes the finding_fingerprint and
   upserts on (submission_id=42, record_type=kinetics, record_id=101,
   finding_fingerprint=...). No task existed, so it creates ONE
   machine_review_curator_task:
       workflow_state        = needs_curator_review   (warning -> needs review)
       machine_review_status = machine_screened_warning
       highest_severity      = warning
       findings_count        = 1
       source_audit_event_id = <the audit event>

3. A curator opens the task (-> in_curator_review), inspects kinetics record 101,
   and decides the note was loose wording — the finding is a false positive.
   They resolve it:
       workflow_state  = dismissed_machine_finding
       resolved_by     = <curator user id>
       resolved_at     = <now>
       resolution_note = "Note is descriptive; tunneling not modelled here. FP."

4. Kinetics record 101 remains RecordReviewStatus.not_reviewed. The task did not
   approve, reject, certify, hide, or change the record or its evidence. If the
   curator had instead wanted to act on it, they would have changed
   RecordReviewStatus through the human-review layer and closed the task as
   resolved_human_reviewed.
```

---

## 13. Migration policy

```text
No migration in this spec — design only.
When implemented:
  - add a NEW Alembic revision (per the phase-aware migration policy); do NOT
    edit d861dfd60891 or any deployed revision.
  - implement BOTH upgrade() and downgrade().
  - create the MachineReviewCuratorTaskState enum in app/db/models/common.py and
    register the new model module in app/db/models/__init__.py so Alembic
    discovers it.
  - the table is brand-new and holds no production data on introduction, but it
    is NOT in the network/PDep exception group; standard already-deployed rules
    apply once it ships.
```

---

## 14. Non-goals

```text
No code.
No migration.
No endpoint changes.
No public machine_review.
No automatic task creation yet.
No frontend work.
No provider / RAG changes.
No ARC / client changes.
```

When a real curator task queue is built, retire the relevant "future" notes
here and in `machine_review_curator_workflow.md` (§9), and update
`admin_machine_review_inspection.md` and `provisional_machine_review.md` if the
task table changes how findings are consumed.
