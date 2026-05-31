# Provisional Machine-Review Layer

**Status:** draft spec — design only, no code, no migrations, no read-API
changes.
**Date:** 2026-05-30
**Scope:** TCKDB backend design only. No implementation. No real LLM
provider. No RAG. No upload-workflow wiring. No public read-API change. No
migration. No ARC or `tckdb-client` change. No automatic approval/rejection.
No scientific-correctness certification. No frontend work.
**Audience:** TCKDB backend maintainers, trust-layer authors, future
machine-review implementers.

**Related specs:**

- `automated_trust_layer.md` — deterministic evidence engine (the rubrics).
- `optional_llm_precheck.md` — optional advisory LLM precheck plumbing
  (the implementation foundation this layer builds on).
- `trust_read_api_current.md` — current public trust-fragment behavior.
- `ai_review_assistant_admin_consumption.md` — current admin read surfaces.
- `admin_machine_review_inspection.md` — the admin-only, read-only debugging
  endpoint that projects this layer over existing submission audit events
  (private; not public `trust.machine_review`).
- `machine_review_curator_workflow.md` — design for the future curator/admin
  triage workflow (review queue, roles, human actions, public-exposure gate)
  built on the inspection endpoint.
- `machine_review_curator_task_queue.md` — detailed design of the persisted
  curator task table, including how it compares to a future
  `record_machine_review` table (§6 Option B here).
- `machine_review_provider_contract_v2.md` — design for a versioned v2 provider
  output contract that speaks this layer's status/severity/category/priority
  vocabulary natively, while the adapter stays backward-compatible with v1
  precheck payloads.

---

## 1. Why this exists

Human curation may be rare. TCKDB has chosen to be **trust-stratified, not
curator-gated** (`automated_trust_layer.md` §1): a valid, provenance-rich
record is useful *now* with an honest evidence label, even with no human
sign-off. But there is a gap between two extremes:

- **Deterministic evidence** answers "does this record carry the metadata
  one would expect?" It is checklist-derived and cannot reason about
  *contradictions*, *narrative inconsistency*, or *cross-field implausibility*.
- **Human review** answers "is this record curator-endorsed?" It is scarce.

A record can be 100% evidence-complete (every check passes) and still be
quietly wrong — e.g. notes that contradict the structured fields, a
provenance chain that doesn't add up, an Arrhenius `A` that is physically
implausible for the stated reaction class. The deterministic rubric is
blind to those. A human would catch them, but humans are the bottleneck.

This spec defines a **provisional machine-review layer** that sits *between*
deterministic evidence and human review. It lets an LLM (or any future
automated reviewer) act as a **semi-curator** — interpreting deterministic
evidence, detecting inconsistencies, and assigning a provisional state —
**without pretending the machine is a human curator** and without touching
the deterministic evidence or the human review status.

---

## 2. Core mental model

> **Rubric = evidence engine. Machine review = provisional reviewer over
> deterministic evidence. Human review = final curator / endorsement.**

| Layer | Question it answers | Who/what produces it | Authoritative for |
|---|---|---|---|
| **Deterministic evidence** (rubric) | "Does the record carry expected metadata?" | `computed_*_v1` evaluators | `evidence_completeness`, `passed/missing/warning/not_applicable_checks`, `hard_fail_reason`, `trust_status` |
| **Machine review** (this spec) | "Does anything look inconsistent or under-supported, beyond what the checklist sees?" | LLM / automated reviewer | A *provisional, advisory* `machine_review.status` + `findings`. Authoritative for **nothing** the other two layers own. |
| **Human review** | "Is this curator-endorsed / certified?" | Human curator | `review_status` (`RecordReviewStatus`), `benchmark_reference`, `is_certified` |

Three distinct axes. Machine review is the new middle axis. It reads the
first axis, informs the third, and overwrites neither.

The machine reviewer **may**:

- interpret deterministic evidence (read `passed/missing/warning_checks`),
- detect inconsistencies (notes vs. structured fields, contradictory
  provenance, implausible values),
- assign a **provisional** machine-review state,
- prioritize records for scarce human attention,
- produce curator-facing findings with pointers into the evidence.

The machine reviewer **must not**:

- approve submissions as human-reviewed,
- reject submissions as curator-reviewed,
- mark records as `benchmark_reference`,
- mutate scientific records,
- change deterministic evidence completeness or any rubric output,
- hide or exclude records by itself,
- certify scientific correctness.

These mirror the LLM boundaries already in `optional_llm_precheck.md` §4,
extended to the record level.

---

## 3. Machine-review state vocabulary

A new, dedicated enum — **not** reusing `RecordReviewStatus`,
`SubmissionPrecheckLabel`, or the precheck `label` vocabulary, so the
boundary is enforced by the type system, not by convention.

Proposed values (suggested name: `MachineReviewStatus`):

```text
not_run
machine_screened_pass
machine_screened_warning
machine_screened_needs_attention
machine_review_failed
```

Optionally, only if a clear operational distinction from
`needs_attention` exists at implementation time:

```text
machine_screened_blocking_concern
```

| State | Meaning |
|---|---|
| `not_run` | No machine review has been performed, or the machine reviewer is disabled/intentionally skipped. This is the default for every record until a review runs. Absence of machine-review metadata implies `not_run`. |
| `machine_screened_pass` | The machine reviewer found no obvious inconsistencies and no missing critical evidence beyond what the deterministic rubric already reports. **Does not mean approved, certified, or correct** — only "nothing obvious stood out to the screener." |
| `machine_screened_warning` | The record is usable but has **advisory** concerns the reviewer wants noted — e.g. missing IRC evidence on a TS, missing uncertainty on a rate, a thin provenance chain. Roughly maps to `findings` of severity `warning`. |
| `machine_screened_needs_attention` | The record has **strong** concerns that should be prioritized for human review — e.g. notes/free-text that contradict the structured fields, contradictory provenance, a value that looks physically implausible. The record stays visible; it is flagged for a curator, not hidden. |
| `machine_review_failed` | The reviewer could not complete review: provider timeout, malformed/non-schema output, provider error, or insufficient/oversized context. **A failure of the reviewer, never of the record.** Must not change visibility, evidence, or upload success. Analogous to `failed_to_review` in `optional_llm_precheck.md`. |
| `machine_screened_blocking_concern` *(optional)* | Reserved. Use **only** if there is a concrete need to distinguish "a curator should look soon" (`needs_attention`) from "a curator should look *before this record is relied on for anything load-bearing*." Even then, this is still advisory: it does **not** hide the record, change `trust_status`, or block reads. If no such operational distinction is wired, do not introduce it — collapse it into `needs_attention`. |

A parallel `curator_priority` field (`low` / `medium` / `high`) MAY be
emitted alongside the state to drive a future review queue ordering. It is
advisory metadata, not a state, and has no effect on visibility.

### State vs. severity

The single record-level `status` summarizes the worst finding, but
`findings[]` carry their own `severity` (`info` / `warning` / `critical`).
Suggested derivation (deterministic, so the same findings always yield the
same status):

```text
any finding severity == critical            -> machine_screened_needs_attention
                                               (or blocking_concern, if adopted)
else any finding severity == warning        -> machine_screened_warning
else (no findings, review completed)        -> machine_screened_pass
review could not complete                   -> machine_review_failed
not performed / disabled                    -> not_run
```

---

## 4. Relationship to existing `review_status` (human review)

Human/moderation review remains a **separate axis** with its own enum,
`RecordReviewStatus` (`app/db/models/common.py`):

```text
not_reviewed
under_review
approved
rejected
deprecated
```

Hard rules:

- Machine review **must not** overwrite, set, or masquerade as
  `review_status`.
- A record can be `review_status=not_reviewed` and
  `machine_review.status=machine_screened_pass` at the same time — that is
  the *normal, expected* state for an un-curated but machine-screened
  record. It is **not** "approved."
- `approved` / `rejected` / `deprecated` are human verdicts. Machine review
  never produces them.
- Machine review never assigns `benchmark_reference` and never sets
  `is_certified` (see §11).

Public shape showing the two axes side by side (illustrative; **not yet
emitted** — see §6/§7 on the gating policy):

```json
{
  "trust": {
    "review_status": "not_reviewed",
    "machine_review": {
      "status": "machine_screened_warning",
      "curator_priority": "medium",
      "findings_count": 2,
      "model": "cloud/gpt-x",
      "reviewed_at": "2026-05-30T12:00:00Z"
    },
    "evidence": {
      "rubric": "computed_kinetics_v1",
      "label": "well_supported",
      "evidence_completeness": 0.82
    },
    "is_certified": false
  }
}
```

`review_status` stays `not_reviewed` regardless of machine-review outcome.
`is_certified` stays `false` until a human certifies. The
`machine_review` block is purely additive and clearly labeled as machine
output.

---

## 5. Relationship to the current `llm_precheck`

The current `llm_precheck` (see `optional_llm_precheck.md`) is
**submission-scoped, admin-scoped, advisory**, persisted as
`submission_audit_event(event_kind=llm_precheck_recorded, actor_kind=llm)`,
and read via `/submissions/{id}/ai-review-summary`. The public scientific
`trust.llm_precheck` fragment is permanently disabled/`not_run`.

**Recommended direction:**

- **Keep** the existing `llm_precheck` / audit-event / context-builder /
  provider plumbing as the **implementation foundation**. It already does
  the hard parts: provider abstraction, context building, schema-validated
  structured output, failure→`failed_to_review` conversion, append-only
  audit persistence, and admin read surfaces.
- **Do not** rename `llm_precheck` now. It is an internal/admin
  implementation detail and a stable existing surface.
- The **future public-facing state is `machine_review`, not
  `llm_precheck`.** `machine_review` is the consumer-facing vocabulary; an
  LLM precheck run is one *mechanism* that can produce a machine-review
  result. Keeping the names distinct prevents the public surface from being
  coupled to "it was an LLM."
- The public scientific `trust.llm_precheck` fragment **stays
  disabled/`not_run`** and is effectively **superseded** by
  `trust.machine_review` once record-level mapping exists (§6). It is not
  deleted (that would be a breaking read-API change); it is left frozen.
- Mapping: a submission-scoped `llm_precheck` result with record-level
  findings is one valid **source** that the future record-level
  `machine_review` mapping (§6 Option B) can consume. The mapping is what
  turns an admin precheck into a public machine-review state — and it must
  be explicit and tested before anything is exposed.

Summary: `llm_precheck` = internal/admin mechanism, kept. `machine_review`
= the public-facing state, new. No deprecation of the precheck
infrastructure; the public exposure is renamed/relocated to
`machine_review`.

---

## 6. Submission-level vs. record-level machine review

### Option A — Submission-level only

Machine review applies to the whole submission.

- **Pros:** matches the current `submission_audit_event` infrastructure;
  simplest; good for upload-time triage and an admin queue.
- **Cons:** hard to show on an individual scientific record; one submission
  can create many records; one record can have many submissions over time;
  a submission-level verdict over-/under-states any single record.

### Option B — Record-level machine review

Machine review applies to individual scientific records
(`record_type`, `record_id`).

- **Pros:** can appear in public per-record `trust.machine_review`
  fragments; filterable per record; maps cleanly onto the per-record read
  API and the per-record `record_review` model.
- **Cons:** requires mapping submission/precheck results to specific
  records (via the `submission_record_link` graph already referenced in
  `optional_llm_precheck.md` §16); needs a policy for multiple reviews per
  record over time (latest-wins vs. history); likely needs new persistence
  (§9).

### Option C — Hybrid (**recommended**)

- **MVP:** submission-level only, reusing the existing audit-event
  persistence. No public exposure.
- **Future:** record-level `machine_review` as the public-facing layer,
  *once a mapping policy is defined and tested.*

**Recommended MVP policy:**

```text
Keep current submission-level audit persistence (llm_precheck_recorded).
Spec record-level machine_review as the future public-facing layer.
Do NOT map submission-level review into public per-record trust fragments
until record-level mapping is explicit, deterministic, and tested.
```

This is the same gate `optional_llm_precheck.md` §16 already states for
`trust.llm_precheck`. The reasons carry over verbatim: a submission bundle
is not one record; mapping a bundle verdict onto every linked record
overstates coverage; records in one submission can differ in quality; a
record can have multiple submission histories; latest submission precheck
≠ latest record assessment; advisory submission review ≠ record
certification.

---

## 7. Machine-review input context

The reviewer consumes **compact structured context**, reusing the
`optional_llm_precheck.md` §10 context-builder discipline.

Include:

- submission metadata,
- linked scientific records (`record_type` + ref/id),
- deterministic evidence evaluations (per record),
- `missing_checks`,
- `warning_checks`,
- `hard_fail_reason` (if any),
- source calculation summaries (role summaries, levels of theory),
- geometry validation summaries,
- review badges / current `review_status`,
- selected notes / free-text fields (the human-authored narrative the
  rubric can't read),
- artifact-kind summaries (kinds present, not contents).

Exclude **by default** (same redaction posture as the precheck context
builder):

- full raw logs (Gaussian/ORCA/etc.),
- full artifacts,
- full coordinate blocks,
- secrets / environment variables / API keys,
- private admin notes.

The free-text/structured-field pairing is the load-bearing input for
`machine_screened_needs_attention`: the reviewer's primary added value over
the deterministic rubric is **noticing when the prose and the numbers
disagree.** Context size limits, compaction-before-failure, and
`machine_review_failed`-on-overflow all follow `optional_llm_precheck.md`
§10.

---

## 8. Machine-review output schema

Structured, schema-validated before persistence (malformed →
`machine_review_failed`, never an upload failure). Extends the
`optional_llm_precheck.md` §11 result shape with record-level addressing.

```json
{
  "status": "machine_screened_warning",
  "curator_priority": "medium",
  "summary": "Core evidence is present, but IRC/path-search support is missing.",
  "findings": [
    {
      "severity": "warning",
      "category": "transition_state_validation",
      "record_type": "transition_state_entry",
      "record_ref": "tse_...",
      "message": "The TS has opt/frequency evidence and one imaginary mode, but lacks IRC or path-search evidence.",
      "evidence_keys": [
        "evidence.passed_checks.single_imaginary_frequency_for_ts",
        "evidence.missing_checks.irc_evidence_present",
        "evidence.missing_checks.path_search_evidence_present"
      ],
      "recommended_action": "Keep visible as machine-screened, but do not promote to benchmark_reference without path verification."
    }
  ],
  "model": "provider/model",
  "used_rag": false
}
```

Allowed `severity`:

```text
info
warning
critical
```

Allowed `category` (at least):

```text
provenance
units
geometry
kinetics
thermo
statmech
transport
transition_state_validation
calculation_parameters
consistency
schema_gap
```

Schema constraints (mirroring `optional_llm_precheck.md` §11):

- `status` ∈ `MachineReviewStatus`; `severity`/`category` ∈ their enums only.
- `summary` length-bounded; bounded number of `findings`; bounded
  `message` length per finding.
- `record_ref` uses public refs; raw `record_id` is governed by the
  existing internal-id policy (hidden unless `include=internal_ids` and
  allowed).
- `evidence_keys` are **pointers** into the deterministic evidence — they
  cite, they do not mutate.
- No provider-supplied mutation payloads. The reviewer cannot return
  "set field X."
- `used_rag` must be `false` for MVP.
- `recommended_action` is advisory free text for a curator; it is never
  executed.

---

## 9. Persistence options (future record-level)

### Option A — reuse `submission_audit_event` only

Keep writing `llm_precheck_recorded` audit events with the structured
result in `details_json`.

- **Good for:** the MVP, submission-level triage, append-only history.
- **Not enough for:** public per-record `trust.machine_review` — it is
  submission-scoped, not addressable by `(record_type, record_id)`, and is
  awkward to filter per record.

### Option B — future `record_machine_review` table

```text
record_machine_review
  id
  record_type
  record_id
  status               -- MachineReviewStatus
  curator_priority     -- low | medium | high (nullable)
  summary
  findings_json
  model
  provider
  context_hash         -- dedupe / "did the inputs change since last review?"
  created_at
  submission_id        -- nullable; which submission triggered this review
```

- **Pros:** clean per-record domain model; queryable/filterable; supports
  multiple reviews per record over time; `context_hash` lets a re-review
  be skipped when inputs are unchanged.
- **Cons:** requires a new Alembic revision (a deployed-table change under
  `migration-rules.md` → new revision, both `upgrade()`/`downgrade()`);
  needs a latest-vs-history read policy; premature before mapping and read
  behavior are settled.

### Option C — materialized latest-machine-review view

A view/materialization exposing the latest `record_machine_review` per
`(record_type, record_id)`.

- **Useful later** for read/search filtering (`review_level=...`,
  `include_machine_reviewed=false`); not needed for MVP.

### Recommendation

```text
No migration now.
Keep the audit-event MVP (Option A) — submission-scoped, advisory.
Spec record_machine_review (Option B) only when public record-level
  machine review is actually implemented; it lands as a NEW Alembic
  revision per migration-rules.md, never folded into a deployed migration.
Defer Option C until per-record filtering is a real product requirement.
```

---

## 10. Relationship to deterministic trust fragments

Once record-level mapping exists (§6 Option B), machine review may appear
**beside** deterministic evidence in the public trust envelope:

```json
{
  "trust": {
    "review_status": "not_reviewed",
    "machine_review": { "status": "...", "curator_priority": "...", "...": "..." },
    "evidence": { "rubric": "computed_kinetics_v1", "label": "...", "...": "..." },
    "is_certified": false
  }
}
```

Machine review **must not** change any deterministic field:

```text
evidence_completeness
passed_checks
missing_checks
warning_checks
not_applicable_checks
hard_fail_reason
trust_status
is_certified
```

The deterministic evaluator stays the **sole** source of those. The
machine reviewer reads them and may *cite* them (`evidence_keys`), but the
trust evaluator's output is byte-identical whether or not a machine review
exists. This is the same non-interference contract `optional_llm_precheck.md`
§16 places on `trust.llm_precheck`, now applied to `trust.machine_review`.

---

## 11. Human override

Human review stays authoritative on its own axis:

- A human `approved` / `rejected` / `deprecated` `review_status` is
  authoritative and overrides any machine-review signal for visibility and
  endorsement purposes.
- Human review **does not erase** machine-review history. A record can be
  human-`approved` and still carry a prior `machine_screened_warning` as
  audit context — the curator simply reviewed it and decided.
- A human reviewer may **cite or ignore** machine findings freely; findings
  are advisory inputs to curation, not verdicts.
- `benchmark_reference` (a `ConformerSelectionKind`) and `is_certified`
  **require human action.** Machine review can *recommend against*
  promotion (as in the §8 example) but can never *grant* it.
- Conflict resolution: where human and machine disagree, **human wins** for
  `review_status` / certification; the machine signal remains visible as
  context, never silently overwritten.

---

## 12. Public visibility policy

Recommended default:

```text
Machine-reviewed records may remain visible by default if they are not
rejected/deprecated, but must be LABELED as machine-reviewed rather than
human-approved.
```

This preserves the trust-stratified, non-curator-gated stance: a
`machine_screened_warning` or `machine_screened_needs_attention` record is
still **visible** (it is flagged, not hidden), exactly because machine
review cannot hide records by itself (§2). Only human `rejected` /
`deprecated` excludes a record by default.

Strict consumers MAY later request filters such as:

```text
review_level=human_only
include_machine_reviewed=false
```

**These filters are out of scope for this spec — do not implement them
here.** They are noted only so the state vocabulary is designed to support
them later (the `machine_review.status` + `review_status` pair is
sufficient to compute both).

Until record-level mapping (§6) is implemented and tested, **no
`machine_review` block is emitted on any public scientific read.** The
public surface is unchanged by this spec.

---

## 13. Tests to require later

(Mirrors and extends `optional_llm_precheck.md` §17. These are future
obligations, not part of this design.)

- Machine review does **not** mutate `review_status`.
- Machine review does **not** mutate `evidence_completeness` (or any
  deterministic field in §10).
- `machine_review_failed` does **not** fail uploads.
- Submission-level machine review remains admin-scoped.
- Record-level machine review requires an **explicit** submission→record
  mapping; no implicit fan-out of a submission verdict onto every record.
- Public trust fragments do **not** expose `machine_review` until mapping
  exists and is enabled.
- Human review remains authoritative (human verdict overrides machine
  signal for visibility/certification).
- `benchmark_reference` / `is_certified` **cannot** be assigned by machine
  review.
- Record-level mapping only attaches findings linked to *that* record and
  preserves internal-id visibility rules.
- Deterministic trust values are identical before and after a machine
  review runs.
- Malformed/oversized/timed-out machine review → `machine_review_failed`,
  upload still succeeds.
- `off`/disabled mode performs no review and exposes nothing; absence
  implies `not_run`.

---

## 14. Non-goals

```text
No implementation.
No real LLM provider.
No RAG (used_rag stays false).
No upload-workflow wiring.
No public read-API changes (no machine_review block emitted yet).
No migrations (no record_machine_review table yet).
No automatic approval/rejection.
No scientific-correctness certification.
No benchmark_reference / is_certified assignment by machine.
No record-hiding by machine review.
No frontend work (badges, filters, review queue UI).
No new deterministic rubrics or changes to existing rubric outputs.
```

---

## 15. Open design questions

1. **Adopt `machine_screened_blocking_concern`?** Only if a concrete
   operational distinction from `needs_attention` exists (e.g. it drives a
   distinct queue or a stronger UI label). Otherwise collapse into
   `needs_attention`. MVP leans toward *not* adopting it.
2. **Latest-wins vs. history for record-level review.** When a record is
   re-reviewed (new submission, new model, changed evidence), is the public
   surface the latest review only, or is history queryable? `context_hash`
   in `record_machine_review` is designed to support either.
3. **Trigger model.** Does machine review run at upload time, post-commit,
   or as a background re-review job when evidence changes? The failure
   contract is identical either way: machine failure never fails an upload.
   (Same open question as `optional_llm_precheck.md` §19.2.)
4. **`curator_priority` ↔ review queue.** Should `curator_priority` map to a
   concrete moderation-queue ordering, or stay purely informational until a
   curator UI exists?
5. **Multi-record submissions.** Which records' deterministic evidence is
   bundled into one review context when a submission creates calculations
   plus downstream kinetics/thermo/statmech/transport? (Same as
   `optional_llm_precheck.md` §19.6.)
6. **Mapping ownership.** Does the submission→record mapping live in the
   precheck service, in the read layer at projection time, or in a
   dedicated mapping service writing `record_machine_review`?
7. **Re-review on evidence change.** When deterministic evidence for a
   record changes (new calculation linked, rubric version bump), should the
   prior machine-review state be invalidated to `not_run`, kept with a
   stale flag, or auto-re-reviewed?
8. **Audit-event-kind vs. new table cutover.** At what point does the
   audit-event MVP (Option A) get superseded by `record_machine_review`
   (Option B), and do both run in parallel during transition?

---

## 16. Recommended implementation order

1. Keep the existing `llm_precheck` plumbing (providers, context builder,
   schema-validated output, `failed_to_review` conversion, append-only
   `llm_precheck_recorded` audit events) advisory-only and submission-scoped.
2. Introduce the `MachineReviewStatus` vocabulary and the record-level
   output schema (§3, §8) as **types/contracts only** — no public exposure.
   Reuse them inside the existing precheck result so submission-level runs
   already emit machine-review-shaped findings.
3. Keep public scientific reads unchanged: no `machine_review` block, and
   `trust.llm_precheck` stays disabled/`not_run`.
4. Define and **test** the submission→record mapping over the
   `submission_record_link` graph in isolation, without exposing it.
   *Done:* the pure mapping policy lives in
   `app/services/machine_review/mapping.py`
   (`map_findings_to_submission_records`) with the anti-fan-out rules
   (§6/§13) and is covered by `tests/services/test_machine_review_mapping.py`.
   It maps a finding only to the exact `(record_type, record_ref)` it names
   when that record is linked to the submission; submission-scoped, unknown,
   ref-less, and unlinked findings route to `unmapped_findings` with a
   warning instead. No persistence or public exposure yet.
5. Add the `record_machine_review` table (Option B) in a **new Alembic
   revision** only when public record-level review is actually being
   implemented; implement `upgrade()`/`downgrade()`.
6. Expose `trust.machine_review` on per-record reads behind the mapping,
   labeled as machine output, never altering deterministic fields, with the
   §13 tests green.
7. Add strict-consumer read filters (`review_level=human_only`,
   `include_machine_reviewed=false`) as a **separate** read-API design,
   after the per-record block ships.
8. Add real Cloud/Local providers behind explicit configuration, last —
   the contract, persistence, and non-interference guarantees must be
   proven with the fake provider first.

---

## 17. Final report (design summary)

- **Spec file created:** `backend/docs/specs/provisional_machine_review.md`
  (separate file, cross-linked to `optional_llm_precheck.md` and
  `automated_trust_layer.md` — matches the per-concern docs organization).
- **Machine-review states defined:** `not_run`,
  `machine_screened_pass`, `machine_screened_warning`,
  `machine_screened_needs_attention`, `machine_review_failed`, plus optional
  `machine_screened_blocking_concern` (§3), as a new `MachineReviewStatus`
  enum distinct from `RecordReviewStatus`.
- **Relationship to human review defined:** separate axis; machine never
  writes `review_status` / `benchmark_reference` / `is_certified`; human is
  authoritative; machine history is preserved (§4, §11).
- **Submission-vs-record policy defined:** Hybrid (Option C). MVP =
  submission-scoped audit events; record-level `machine_review` is future
  public-facing work, gated on an explicit, tested mapping (§6).
- **Output schema defined:** record-addressed findings with
  `severity`/`category` enums, `evidence_keys` citations, no mutation
  payloads, schema-validated → `machine_review_failed` on malformed (§8).
- **Public visibility policy defined:** machine-reviewed records stay
  visible-but-labeled; machine review never hides records; only human
  `rejected`/`deprecated` excludes; strict filters deferred (§12).
- **Persistence recommendation:** no migration now; keep the audit-event
  MVP; spec a future `record_machine_review` table for when public
  record-level review ships, as a new Alembic revision (§9).
- **Open design questions:** 8 listed (§15) — blocking-concern adoption,
  latest-vs-history, trigger model, queue mapping, multi-record context,
  mapping ownership, re-review-on-change, table cutover.
- **Recommended implementation order:** 8 steps (§16), contract-and-tests
  first, public exposure and real providers last.
