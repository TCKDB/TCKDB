# Admin UI / Mock — Machine-Review Inspection

**Status:** draft spec — design only. No frontend code, no backend code, no API
change, no migration, no public `trust.machine_review`. Designs a *lightweight
admin-only* inspection page/panel (or static mock) over the existing inspection
endpoint, to judge whether machine-review output is useful before building a
persisted curator queue or exposing anything publicly.
**Date:** 2026-05-31
**Scope:** TCKDB design only. No implementation. No route change. No migration.
No public machine review. No curator-task persistence. No automatic review
actions. No ARC or `tckdb-client` change.
**Audience:** TCKDB backend maintainers and whoever builds the first admin-only
machine-review view (real panel or static mock).

**Related specs:**

- `machine_review_handoff.md` — the workstream checkpoint; this spec is its
  recommended "Option B" (admin UI/mock before public exposure).
- `admin_machine_review_inspection.md` — the admin-only endpoint this UI renders.
  The single source of every field shown here.
- `machine_review_curator_workflow.md` — the future curator triage layer; this
  mock is the evidence-gathering step that decides whether that layer is worth
  building, and what should be curator-facing vs admin-only.
- `provisional_machine_review.md` — vocabulary (status/severity/category) and the
  public-exposure gate this mock feeds.

---

## 1. Why this spec exists

The inspection endpoint produces a diagnostic projection, but there is no human
way to *look* at it — only raw JSON. Before anyone commits to a persisted
curator queue (`machine_review_curator_task_queue.md`) or to public
`trust.machine_review`, maintainers need to eyeball real projections and decide:
is this output specific, understandable, and low-noise enough to be worth a
workflow?

This spec designs the cheapest thing that answers that question: an **admin-only
read view** over `GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection`.
It can be implemented as a minimal frontend panel or as a static mock filled
from saved fake-provider responses. It adds no schema, no route, no persistence.

---

## 2. Page identity

```text
Page:           Submission Machine-Review Inspection
Audience:       admin only (require_admin; curators/users never see this)
Primary input:  submission_id
Backend source: GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection
Nature:         read-only diagnostic view; renders the response, mutates nothing
```

The page makes one API call and renders the response. It has no write actions,
no review controls, no persistence (see §9 for the one manual-notes exception,
which is mock-only).

---

## 3. What the endpoint actually returns

Everything the page renders without a future join comes from
`AdminSubmissionMachineReviewInspectionResponse`:

```text
submission_id
record_summaries[]            (one per record with >=1 exactly-mapped finding)
  .record_type
  .record_ref
  .record_id
  .latest_summary             (MachineReviewRecordSummary)
    .status
    .curator_priority
    .findings_count
    .highest_severity
    .model
    .provider
    .reviewed_at
    .submission_id
  .all_record_reviews_count
unmapped_findings_count
mapping_warnings[]
parse_warnings[]
source_audit_event_ids[]
```

Anything **not** in this list (submission status, uploader, human-review status,
evidence label, …) is a **future join** and is marked as such wherever it
appears below. A first mock should render only the fields above.

---

## 4. UI goals

The view exists to let an admin quickly answer:

```text
Did machine review run for this submission?       (run summary; empty vs populated)
Which records received exact mapped findings?     (record summaries table)
Which findings failed to map?                      (unmapped_findings_count)
Were there parse warnings?                         (parse_warnings)
Which audit events contributed?                    (source_audit_event_ids)
Would this be useful enough for a curator queue?   (the evaluation rubric, §10)
```

---

## 5. Sections

```text
1. Submission header
2. Machine-review run summary
3. Record summaries (table)
4. Diagnostics
5. Raw JSON / debug drawer
6. Maintainer observations (mock-only manual notes)
```

### 5.1 Submission header

```text
submission_id            (from response)
submission status        FUTURE JOIN (submission.status — not in endpoint today)
created / submitted at    FUTURE JOIN (submission.submitted_at)
created_by / uploader     FUTURE JOIN (submission.created_by -> app_user)
record link count         FUTURE JOIN (len(submission.record_links))
audit event count         FUTURE JOIN (len(submission.audit_events))
```

Only `submission_id` is available from the current endpoint. The rest require a
join the endpoint does not perform today; render them as "—" or a "future" tag,
or fetch them from a separate admin submission read if one exists. Do not block
the first mock on them.

### 5.2 Machine-review run summary

Diagnostic-only summary line(s):

```text
record_summaries_count          = len(record_summaries)
unmapped_findings_count         = unmapped_findings_count
mapping_warnings_count          = len(mapping_warnings)
parse_warnings_count            = len(parse_warnings)
source_audit_event_ids          = source_audit_event_ids
overall_highest_severity        = max highest_severity across record_summaries
                                  (DERIVED in the view; not a response field)
```

Label this block clearly as **diagnostic only**: it summarizes how the
projection landed, not a verdict about the submission or its science. "Did
machine review run?" is answered here — an empty `record_summaries` with empty
diagnostics and no `source_audit_event_ids` means *no machine-review events for
this submission*, which is different from *ran and found nothing*.

### 5.3 Record summaries table

One row per record that received at least one exactly-mapped finding.

Current columns (all from the response):

```text
record_type
record_ref
record_id
status                  (latest_summary.status — badge, see §6)
highest_severity        (latest_summary.highest_severity)
findings_count          (latest_summary.findings_count)
model                   (latest_summary.model)
provider                (latest_summary.provider)
reviewed_at             (latest_summary.reviewed_at)
all_record_reviews_count
```

Future-join columns (NOT in the endpoint today — render only once joined):

```text
human_review_status     FUTURE JOIN (RecordReviewStatus for this record)
evidence_label          FUTURE JOIN (deterministic evidence badge)
evidence_completeness   FUTURE JOIN (deterministic evidence ratio)
workflow_state          FUTURE (MachineReviewCuratorTaskState — spec only, no table)
```

`record_id` is the raw internal id; it is shown because this is an admin-only
surface. It must never leak to a curator or public view (see §7, §8).

---

## 6. Status badges

Badge text and meaning for each `MachineReviewStatus`:

| Badge | Color intent | Meaning |
|---|---|---|
| `not_run` | neutral / grey | Reviewer did not run (disabled or not_performed). No machine opinion. |
| `machine_screened_pass` | green | Reviewer ran; no warning/critical findings. **Not** an approval. |
| `machine_screened_warning` | amber | Reviewer raised >=1 warning finding. **Not** a rejection. |
| `machine_screened_needs_attention` | red | Reviewer raised >=1 critical finding. Record stays fully visible. |
| `machine_review_failed` | grey-hatched / error | The *reviewer* failed (timeout, bad/oversized output). **Not** a record failure. |

The view must make these disclaimers unmissable (tooltip or footnote on every
badge):

```text
machine_screened_pass            does NOT mean approved.
machine_screened_warning         does NOT mean rejected.
machine_screened_needs_attention does NOT hide the record.
machine_review_failed            is a reviewer failure, NOT a record failure.
```

Authority lives in human review (`RecordReviewStatus`), never in these badges.
`machine_screened_blocking_concern` is reserved and never produced — do not add a
badge for it.

---

## 7. Diagnostics section

```text
mapping_warnings           list (projection problems: a finding referenced a
                           record identity that could not be matched within
                           this submission)
parse_warnings             list (provider/payload problems: the reviewer's
                           output could not be validated as an LLMPrecheckResult)
unmapped_findings_count    int  (findings that did not apply to any record:
                           submission-scoped or unresolved)
source_audit_event_ids     list (which submission_audit_event rows contributed)
```

Reading guidance to surface inline:

```text
parse_warnings        => provider / payload problems (the reviewer's output was unusable)
mapping_warnings      => projection problems (a finding could not be tied to a record)
unmapped findings     => do not apply to any record (diagnostic only; never fanned out)
diagnostics           => maintainer signal only; never shown to normal users
```

Empty states should read affirmatively ("No mapping warnings.", "No parse
warnings.") so an admin can tell "clean" from "not loaded".

---

## 8. Raw JSON / debug drawer

A collapsible panel showing the verbatim endpoint response.

```text
Raw JSON is for maintainers only.
Do NOT expose raw diagnostics (parse/mapping warnings, source_audit_event_ids,
  provider/model strings, raw record_id) to curators by default.
Do NOT expose raw diagnostics publicly.
```

Collapsed by default. It is a debugging aid, not a presentation surface — the
distinction between an admin debug drawer and a future curated curator view is
exactly the line drawn in `machine_review_curator_workflow.md` §6.

---

## 9. Maintainer observations (mock-only)

A simple notes area letting an admin jot an assessment **per record summary or
per submission**. Prompts:

```text
[ ] false positive?
[ ] useful finding?
[ ] confusing status?
[ ] bad mapping?
[ ] bad prompt / provider output?
[ ] should this be curator-facing?
+ free-text note
```

**This is mock-only / manual.** There is no persistence in this spec: notes live
in the admin's head, a scratch doc, or browser-local state at most. This is a
deliberate stand-in for the future `machine_review_curator_task` resolution
fields (`resolution_note`, dismissal) — it lets maintainers *practice* the
triage judgment and decide whether persisting it (Option A in §11) is warranted,
without building the table first.

---

## 10. Evaluation rubric

What an admin is actually deciding while using this view: **is machine review
ready to graduate from a debug surface to a curator workflow (or public
exposure)?** Judge against:

```text
Are mapped findings actually record-specific?      (or vague/submission-level noise?)
Are warnings understandable?                        (could a non-author act on the text?)
Are false positives tolerable?                      (low enough to dismiss cheaply?)
Are parse / mapping warnings rare?                  (frequent => the projection is broken)
Does the status match the finding severity?         (warning badge <-> warning findings, etc.)
Would a curator know what action to take?           (or just "something's off"?)
Is any public wording misleading?                   (does a badge imply approval/rejection?)
```

A useful heuristic: if the answers are mostly "yes", the curator workflow / task
table is worth building; if "parse/mapping warnings are common" or "findings are
not record-specific", fix the projection/provider first — building a queue on
noisy input just industrializes the noise. These answers are the concrete input
the public-exposure gate (`provisional_machine_review.md` §10) requires.

---

## 11. Recommended next step

After this spec, do one of:

```text
A. Implement a minimal admin-only frontend panel rendering this endpoint
   (no schema/migration; turns the diagnostic into something humans can drive).
B. Collect example endpoint responses from fake-provider runs and assemble a
   static mock from them (cheapest; no frontend stack needed).
C. Design the curator task table migration (machine_review_curator_task_queue.md)
   if the rubric (§10) shows workflow persistence is now clearly needed.
```

**Recommendation: start with B (collect fake-provider example responses) or A
(a minimal panel).** Both generate the real triage experience the evaluation
rubric and the public-exposure gate require, at near-zero cost and with no new
schema or public surface. Defer C until §10 answers show the projection is
specific and low-noise — persisting workflow over noisy findings is premature.

---

## 12. Example mock layout

```text
Submission #123  —  Machine-Review Inspection            [admin only · diagnostic]

Header
  submission_id   123
  status          —  (future join)
  uploaded by     —  (future join)
  submitted at    —  (future join)
  record links    —  (future join)        audit events  —  (future join)

Run summary  (diagnostic only)
  Records with mapped findings : 1
  Unmapped findings            : 1
  Mapping warnings             : 0
  Parse warnings               : 0
  Source audit events          : [456]
  Overall highest severity     : warning

Records
  TYPE      REF   ID   STATUS                    SEV      FIND  MODEL                  REVIEWED
  kinetics  k101  101  machine_screened_warning  warning   1    fake_test/simple-v1    2026-05-31T...
  (status badge tooltip: "warning ≠ rejected; advisory only")

Diagnostics
  Mapping warnings : none
  Parse warnings   : none
  Unmapped findings: 1   (did not apply to any record — diagnostic only)
  Source events    : 456

Raw JSON                                                              [▸ collapsed]

Maintainer observations  (mock-only, not persisted)
  [ ] false positive   [ ] useful   [ ] confusing status   [ ] bad mapping
  [ ] bad provider output   [ ] should be curator-facing
  note: ________________________________________________
```

---

## 13. Non-goals

```text
No public trust.machine_review.
No curator task persistence.
No frontend implementation in this spec.
No route changes.
No migration.
No automatic review actions.
No approval / rejection / certification / hiding.
```

When a real admin panel or curator workflow is built, fold the lessons from the
maintainer observations and evaluation rubric back into
`machine_review_curator_workflow.md` and the public-exposure gate in
`provisional_machine_review.md`.
