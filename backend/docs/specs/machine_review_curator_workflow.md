# Machine-Review Curator/Admin Workflow

**Status:** draft spec — design only. No code, no API change, no migration, no
public `trust.machine_review`. Decision framework for a *future* human-facing
triage layer over machine-review inspection.
**Date:** 2026-05-31
**Scope:** TCKDB backend design only. No implementation. No real LLM provider.
No RAG. No persistence table. No migration. No frontend implementation. No
automatic human-review actions. No ARC or `tckdb-client` change.
**Audience:** TCKDB backend maintainers, trust-layer authors, and whoever
eventually builds a curator review queue/UI.

**Related specs:**

- `admin_machine_review_inspection.md` — the admin-only, read-only inspection
  endpoint this workflow would sit on top of.
- `provisional_machine_review.md` — the future public-facing machine-review
  layer and its status/severity vocabulary.
- `optional_llm_precheck.md` — the precheck plumbing that produces the audit
  events.
- `automated_trust_layer.md` — the deterministic evidence engine and
  `RecordReviewStatus` (human review).
- `machine_review_curator_task_queue.md` — detailed design of the persisted
  curator task table that would make this workflow's triage axis (§4) and
  task-table persistence option (§9) concrete.

---

## 1. Why this spec exists

The machine-review inspection endpoint
(`admin_machine_review_inspection.md`) gives maintainers a *diagnostic
projection*: it shows how a submission's precheck audit events map onto its
linked records. It deliberately stops there — it triggers no human action and
exposes nothing to curators or the public.

This spec designs the **next layer up**: how humans should consume those
diagnostics, what a future review queue should look like, what stays raw/hidden,
when curators (not just admins) should get access, and — crucially — what the
workflow must **never** automate. It is a decision framework, not an
implementation plan. Nothing here is built yet.

---

## 2. Mental model

Four distinct layers; this spec defines the second:

```text
machine-review inspection   = diagnostic projection (exists, admin-only, read-only)
curator workflow            = human-facing triage layer (this spec; future)
human review                = authoritative decision (RecordReviewStatus)
public trust.machine_review = deferred product decision (not built)
```

The workflow is a *triage* layer. It helps a human decide where to spend
attention and what to do; it does not decide anything itself, and it never
becomes the authoritative record state. Authority always lives in human review.

---

## 3. Roles

| Role | What they see / do with machine review |
|---|---|
| **admin** | May inspect **raw** machine-review diagnostics today via the inspection endpoint: unmapped findings, mapping/parse warnings, source audit event ids, provider/model strings, internal ids. This is debugging, not workflow. |
| **curator** | No machine-review access today. *Eventually* may see a **curated/safer queue** — record-specific findings, plain-language actions, evidence pointers — **not** raw diagnostics or provider internals. |
| **normal user** | Never sees private inspection output, the queue, or any machine-review state. Machine review is not part of the public read surface. |

Role progression is deliberate: admins debug the mechanism first; curators get
a workflow only once the diagnostics are trustworthy enough to present safely
(see §10). Curator access is a product decision, not a default.

---

## 4. Workflow states (human triage), kept separate from machine status

A curator workflow needs its **own** state vocabulary describing *human triage
progress*, distinct from the machine's advisory status and from the
authoritative human-review status. Proposed workflow states:

| Workflow state | Meaning |
|---|---|
| `untriaged` | A machine finding exists; no human has looked at it. |
| `needs_curator_review` | Flagged into the queue for a human to examine. |
| `in_curator_review` | A curator is actively working it. |
| `resolved_no_action` | A human looked; nothing to change (finding acknowledged, record fine as-is). |
| `resolved_human_reviewed` | A human acted via the real human-review layer (record moved in `RecordReviewStatus`). |
| `dismissed_machine_finding` | A human judged the finding a false positive / not actionable and dismissed it with a note. |

These workflow states are a **separate axis**. They must **not** replace,
overload, or be conflated with:

```text
MachineReviewStatus   (advisory machine output: not_run / machine_screened_* / machine_review_failed)
RecordReviewStatus    (authoritative human review: not_reviewed / under_review / approved / rejected / deprecated)
SubmissionStatus      (submission lifecycle/moderation)
```

A record can simultaneously be `MachineReviewStatus.machine_screened_warning`,
`RecordReviewStatus.not_reviewed`, and curator workflow `needs_curator_review`
— three orthogonal facts. The workflow state tracks *human attention*, the
machine status tracks *what the screener said*, and the review status tracks
*the authoritative decision*.

---

## 5. Queue design

A future curator/admin queue is a ranked list of records (or submission×record
pairs) carrying machine concerns. Recommended columns:

| Field | Source |
|---|---|
| `submission_id` | inspection response |
| `record_type` | inspection record summary |
| `record_ref` / `record_id` | inspection record summary |
| `machine_review_status` | `latest_summary.status` |
| `highest_severity` | `latest_summary.highest_severity` |
| `findings_count` | `latest_summary.findings_count` |
| `mapping_warnings_count` | inspection (submission-level) |
| `parse_warnings_count` | inspection (submission-level) |
| `model` | `latest_summary.model` |
| `reviewed_at` | `latest_summary.reviewed_at` |
| `curator_priority` | `latest_summary.curator_priority` (advisory; currently always null) |
| `human_review_status` | `RecordReviewStatus` (joined from the record) |
| `evidence_label` | deterministic evidence badge (joined) |
| `evidence_completeness` | deterministic evidence ratio (joined) |
| `workflow_state` | this spec's triage axis (§4) |

### Ranking

Ranking should prioritize human attention where it pays off most. A reasonable
ordering (highest first):

```text
1. critical findings (highest_severity == critical)
2. machine_screened_needs_attention status
3. mapping/parse warnings present (the diagnostics themselves look broken)
4. unreviewed records (RecordReviewStatus == not_reviewed)
5. recent submissions (newer reviewed_at)
6. records with high evidence_completeness but machine concerns
```

The last criterion is intentional: a record that *looks* well-supported by the
deterministic rubric but still drew a machine concern is exactly the case a
human should sanity-check — the checklist and the screener disagree.

Ranking is advisory ordering only; it changes nothing about the record.

---

## 6. Safe presentation policy

What is appropriate for an **admin debugging surface** is not appropriate for a
**curator triage UI**. Split accordingly.

**Admins may see (raw):**

```text
raw mapping warnings
parse warnings
source audit event ids
provider/model strings
internal ids
unmapped findings diagnostics
```

**Curator-facing UI should prefer (curated):**

```text
record-specific findings (already mapped to an exact record)
plain-language recommended actions
evidence pointers (which deterministic checks the finding relates to)
human review controls (open/approve/reject/note)
links to the source record(s) and submission
```

Avoid showing curators raw parse failures, unmapped-finding noise, or provider
internals unless a specific debugging need arises. A parse failure means the
*reviewer's* output was unusable — it is an admin/operability concern, not a
curation signal about the science. Surfacing it in a curator queue would be
noise at best and misleading at worst.

---

## 7. Human action policy

After seeing a machine finding, a human (curator/admin) may take any of these
actions **through the existing human-review and submission layers** — never
through machine review:

```text
open record
open submission
mark record under_review            (RecordReviewStatus.under_review)
approve record                      (RecordReviewStatus.approved)
reject / deprecate record           (RecordReviewStatus.rejected / deprecated)
add curator note
dismiss machine finding             (workflow_state.dismissed_machine_finding + note)
request uploader clarification
promote benchmark_reference         (only after independent human validation)
```

Machine review itself must perform **none** of these. The machine produces
advisory findings and a status; a human decides what, if anything, happens.
`benchmark_reference` in particular is promoted only after a human validates the
record on its own merits — a machine finding can prompt that validation but can
never be its justification.

---

## 8. Non-interference and authority

The authority rules are absolute and carry over from
`provisional_machine_review.md` and the non-interference tests:

```text
machine review never approves or rejects a record
machine review never certifies a record (never sets is_certified)
machine review never sets benchmark_reference
machine review never hides a record or changes its visibility
machine review never changes deterministic evidence
human review wins on any conflict
machine findings remain audit context unless explicitly dismissed or superseded
```

"Human review wins on conflict" means: if a record is human-`approved` while a
machine review says `machine_screened_needs_attention`, the record is approved.
The machine status remains visible (to admins, and later possibly curators) as
context, but it does not contest the human decision. A machine finding is never
silently dropped either — it persists as audit context until a human explicitly
dismisses it or a newer review supersedes it.

---

## 9. Persistence decision

Does the workflow need its own persistence? Three options:

| Option | What it is | Trade-off |
|---|---|---|
| **Audit-event-only diagnostics** | Recompute projections on demand from existing `submission_audit_event` rows (today's model). | Zero new schema; stateless; but no place to store triage state (`workflow_state`, dismissals, curator notes scoped to a finding). |
| **Submission/record-level curator task table** | A new table keyed by `(submission_id, record_type, record_id)` holding `workflow_state`, assignee, notes, dismissal. | Real workflow state; a focused queue table; needs a migration when built. |
| **`record_machine_review` table** | Persist the projected record-level machine reviews themselves (spec §6 Option B). | Stores the machine output, not the human workflow; useful if recomputation becomes expensive or public exposure needs a stable row. Orthogonal to the task table. |

**Recommendation:**

```text
No migration in this spec — it is design only.
If a real queue/workflow is implemented, introduce a new Alembic revision
  (a curator-task table, and optionally record_machine_review), per the
  phase-aware migration policy.
Do NOT overload submission.status or RecordReviewStatus to carry machine-review
  workflow state — the triage axis is separate (see §4) and conflating it would
  corrupt the authoritative human-review and moderation vocabularies.
```

---

## 10. Public-exposure decision gate

Public `trust.machine_review` stays **deferred** until the curator/admin
workflow has produced evidence answering all of these:

```text
Are the statuses understandable to a non-author consumer?
Are false positives manageable in practice (low enough, dismissible)?
Should machine_screened_warning records be public by default?
Should needs_attention records remain public but clearly labeled?
Should strict consumers be able to request review_level=human_only?
Does curator dismissal of a finding affect the public machine_review state?
Do we need record_machine_review persistence before exposing anything public?
```

Until those are answered from real triage experience, the private trust-envelope
adapter (`provisional_machine_review.md`) remains the only place machine review
sits beside evidence, and it stays unexposed.

---

## 11. Example user story

```text
1. A fake provider flags a kinetics record: the note mentions tunneling but
   tunneling_model is null. It records an advisory llm_precheck_recorded audit
   event with a warning finding addressed to kinetics record 101.

2. An admin hits GET /api/v1/admin/submissions/{id}/machine-review-inspection.
   The finding mapped exactly to kinetics record 101; latest_summary.status is
   machine_screened_warning, highest_severity warning. No fan-out to sibling
   records.

3. A future curator queue shows record 101 as workflow_state=needs_curator_review,
   with the plain-language finding and a pointer to the relevant evidence check —
   not the raw audit payload.

4. A curator opens kinetics record 101, checks the provenance/representation,
   and either: updates+human-reviews the record (RecordReviewStatus.under_review
   -> approved/rejected), or dismisses the finding as a false positive with a
   note (workflow_state=dismissed_machine_finding).

5. Throughout, the machine finding never approved, rejected, hid, or certified
   the record, and never touched deterministic evidence. The human made the call;
   the machine finding was only context.
```

---

## 12. Non-goals

```text
No code.
No endpoint changes.
No public machine_review.
No persistence / migration in this spec.
No frontend implementation.
No automatic human-review actions.
No provider / RAG changes.
No ARC / client changes.
```

When a real queue/workflow is built, retire the relevant "future" notes here,
in `admin_machine_review_inspection.md`, and in `provisional_machine_review.md`.
