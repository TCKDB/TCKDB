# Machine-Review Lifecycle — Architecture & Data Flow

**Status:** architecture/lifecycle reference for the **private** record-level
machine-review stack as implemented through the admin fake trigger. It explains
module boundaries and end-to-end data flow; it is descriptive, not a new design
proposal.
**Date:** 2026-06-01
**Scope:** TCKDB backend only. The machine-review stack is **private/admin-only
and advisory**. No public `trust.machine_review`. No real provider, no RAG, no
background worker, no upload-workflow wiring, no ARC / `tckdb-client` change.
**Audience:** maintainers who need the shape of the stack before touching any
`app/services/machine_review/*` module or the admin endpoint.

**Related specs**

- `record_machine_review_policy.md` — the authoritative policy: currency
  (current/stale/historical), context-hash inputs/exclusions,
  `context_schema_version`, append-only persistence, human-review interaction,
  and the future public-display rules. This document is the *implementation
  walkthrough* of that policy.
- `machine_review_handoff.md` — workstream checkpoint and the "do not confuse
  these layers" glossary (`llm_precheck` vs `machine_review` vs admin inspection
  vs curator task workflow).
- `automated_trust_layer.md` — the deterministic trust evaluator that produces
  the `TrustFragment` this stack reads.

---

## 0. One-paragraph summary

A scientific record is evaluated by the existing deterministic trust evaluator
into a `TrustFragment`. The machine-review stack projects that fragment into a
compact, hashable evidence context, computes a **currency key** from it, and
asks whether the latest persisted machine review for that record is still
current. If not, a **producer** (only a *fake* producer ships) creates a
record-scoped review and an executor **appends** one `record_machine_review`
row — never mutating anything else. An admin-only endpoint drives this loop on
demand. Nothing here is public: scientific reads are untouched and expose no
`trust.machine_review`.

---

## 1. Lifecycle (end to end)

1. **Deterministic trust/evidence evaluation.** The trust evaluator
   (`app/services/trust/evaluator.py`, e.g. `evaluate_computed_calculation`)
   runs the versioned rubric for the record type and returns an
   `EvidenceEvaluation`. `build_trust_fragment(...)` folds in the record's human
   `review_status` and produces the public-shaped `TrustFragment`. Machine
   review only **reads** this fragment.
2. **`MachineReviewEvidenceContext` construction.**
   `context_adapter.build_machine_review_evidence_context_from_trust(...)`
   projects the fragment value-for-value onto the typed evidence context: the
   rubric name + version, the four check sets (passed / missing / warning /
   not-applicable), `hard_fail_reason`, and `review_status` / `is_certified` as
   **read-only context inputs** (observed, never owned).
3. **Context hash / currency key.**
   `context_hash.build_machine_review_context_hash(...)` canonicalises (sorts
   set-like inputs, folds in `context_schema_version`) and hashes the context
   into a `MachineReviewContextDigest` (`context_hash` +
   `context_schema_version`). The **currency key** is the digest plus the active
   `prompt_version` and `rubric_versions`. `provider` / `model` are deliberately
   **not** currency dimensions.
4. **Re-review planning.** `rereview.plan_record_machine_rereview(...)` loads the
   record's persisted rows (via the query service), classifies their currency,
   and returns a read-only `MachineReviewReReviewPlan` whose decision is
   `skip_current`, `run_not_reviewed`, or `run_stale`. Planning **appends
   nothing**.
5. **Producer interface.** `producer.MachineReviewProducer` is the protocol
   seam: `review_record(context, *, reviewed_at) -> RecordMachineReview`, or
   raise `MachineReviewProductionError`. Orchestration depends on this protocol,
   not on any concrete provider.
6. **Fake producer.** `producer.FakeMachineReviewProducer` is the only shipped
   implementation: a deterministic, benign `machine_screened_pass` (no findings)
   stamped with obvious `fake` / `fake-test` provenance so a row is never
   mistaken for a real verdict. It performs no I/O and no persistence.
7. **Re-review execution.**
   `rereview_execution.execute_record_machine_rereview_plan(...)` is the **sole
   write path**. It refuses to append for `skip_current`, appends exactly one
   row for `run_*`, and re-checks live currency (a conservative idempotency
   guard) so a re-run of an unchanged plan never double-appends.
8. **Append-only persistence.**
   `persistence.create_record_machine_review_row(...)` inserts one
   `RecordMachineReviewRow` (currency key + status + findings JSON + provenance +
   `reviewed_at`) and flushes; it never updates or deletes. The caller owns the
   transaction.
9. **Persisted query / currency classification.**
   `query.get_record_machine_review_currency_for_record(...)` is the single read
   path over persisted rows: filter by exact `(record_type, record_id)`, order
   newest-first deterministically, and classify the latest as
   current / stale / historical via the pure `currency` classifier.
10. **Admin fake trigger endpoint.**
    `POST /api/v1/admin/machine-review/records/{record_type}/{record_id}/run-fake`
    (`app/api/routes/admin.py`, backed by
    `admin_trigger.run_admin_fake_machine_review`) wires steps 1–9 together for
    one record, on demand, admin-only, fake producer only.
11. **Private inspection / read helpers.** `inspection.py` projects a
    submission's machine-review audit events onto its linked records (the
    admin-only `…/machine-review-inspection` debugging view);
    `read_model.py` selects the latest review summary per record; `trust_adapter.py`
    assembles the **private** internal trust envelope that a future public
    fragment could render. None of these are wired into a public read.
12. **Future public `trust.machine_review` projection.** Not implemented. When
    built, it will project the latest-current row (step 9) behind the display
    rules in `record_machine_review_policy.md` §7, always labelled as machine
    output and never altering deterministic fields.

---

## 2. Module map

Core lifecycle modules (`app/services/machine_review/`):

| Module                  | Responsibility                                             |
|-------------------------|------------------------------------------------------------|
| `context_hash.py`       | Typed evidence context, context digest, schema-version contract |
| `context_adapter.py`    | `TrustFragment` → `MachineReviewEvidenceContext`           |
| `currency.py`           | Pure current / stale / historical classifier               |
| `rereview.py`           | Re-review **planning only** (no append)                    |
| `rereview_execution.py` | Re-review **append execution only** (sole write path)      |
| `producer.py`           | Producer protocol + `FakeMachineReviewProducer`            |
| `orchestration.py`      | plan → produce → execute loop                              |
| `persistence.py`        | Append `record_machine_review` row + row → projection      |
| `query.py`              | Persisted-row query / currency read path                   |
| `admin_trigger.py`      | Admin-only fake trigger: record resolver + active recipe   |
| `inspection.py`         | Private diagnostic projection (admin inspection view)      |
| `trust_adapter.py`      | Private (future) machine-review trust-envelope assembly    |

Supporting modules in the same package (adjacent axes, referenced by the above):

| Module                     | Responsibility                                          |
|----------------------------|---------------------------------------------------------|
| `schemas.py`               | Shared enums/value types (status, severity, findings)   |
| `read_model.py`            | `RecordMachineReview` + latest-per-record selection      |
| `derivation.py`            | Deterministic machine-review status derivation          |
| `mapping.py`               | Pure finding → record mapping policy                    |
| `audit_adapter.py`         | Audit-event → record-review projection                  |
| `curator_tasks.py` / `curator_task_lifecycle.py` | Human-triage queue (separate state axis) |

---

## 3. Data flow

```text
Scientific record
  ↓
deterministic trust evaluator        (app/services/trust/evaluator.py)
  ↓
TrustFragment                        (read-only; review_status folded in)
  ↓
MachineReviewEvidenceContext         (context_adapter.py)
  ↓
context_hash + context_schema_version (context_hash.py)  ──┐
  ↓                                                        │ currency key =
planner checks persisted row currency (rereview.py +       │ digest + prompt_version
                                       query.py + currency.py)  + rubric_versions
  ↓                                                        ┘
  ├─ skip_current ──────────────► (nothing appended)
  └─ run_not_reviewed / run_stale
       ↓
     producer creates RecordMachineReview   (producer.py — fake only)
       ↓
     executor appends record_machine_review  (rereview_execution.py → persistence.py)
       ↓
     query service classifies latest row as current / stale / historical (query.py)
```

The admin fake trigger (`admin_trigger.py` → `orchestration.py`) is the on-demand
driver of this exact flow for a single record.

---

## 4. Privacy / boundary guarantees

These invariants hold today and any change to a `machine_review` module or the
admin endpoint must preserve them:

```text
Machine review is still private/admin-only.
Public scientific reads do not expose trust.machine_review.
TrustFragment is unchanged.
include=machine_review is not supported.
include=trust,machine_review is not supported.
Machine review never mutates review_status, benchmark_reference, is_certified,
  deterministic evidence, scientific records, or submission.status.
record_machine_review is append-only.
```

`include=machine_review` (alone or combined with `trust`) is rejected with `422
unknown_include_token` by `validate_includes`, because `machine_review` is not a
legal include token on any public endpoint. The public `TrustFragment` keeps its
frozen key set (`review_status`, `trust_status`, `evidence`, `llm_precheck`,
`is_certified`) with no `machine_review` key.

---

## 5. What is **not** done

```text
real/cloud/local producer
RAG
background/scheduled triggers
upload-workflow wiring
public trust.machine_review exposure
frontend/curator UI
public filters such as review_level=human_only or include_machine_reviewed=false
```

Only the **fake** producer ships, only the **admin** trigger drives it, and the
output stays in `record_machine_review` and the private read/inspection helpers.

---

## 6. Operational examples — admin fake trigger

```http
POST /api/v1/admin/machine-review/records/{record_type}/{record_id}/run-fake
```

- **Auth:** admin-only. Anonymous → `401`; normal user / curator → `403`.
- **`record_type`:** an internal id–based admin surface; supported types are
  exactly those with both a computed trust evaluator and a persisted home —
  `calculation`, `kinetics`, `thermo`, `statmech`, `transport`,
  `transition_state_entry`.
- **Active recipe (private constants).** `prompt_version = "machine_review_v1"`;
  `rubric_versions` is derived from the trust rubric constants
  (e.g. `{"computed_calculation_v1": "1"}`), so a rubric bump restales reviews
  without a second source of truth.
- **Producer:** `FakeMachineReviewProducer` only — the appended row carries
  `provider="fake"`, `model="fake-test"`.

Expected outcomes:

```text
not reviewed          → appends   (decision run_not_reviewed, status appended)
stale                 → appends   (decision run_stale,        status appended)
current               → skips     (decision skip_current,     status skipped_current)
unsupported record_type → 400
missing record          → 404
```

Re-running with an unchanged recipe and unchanged evidence is **idempotent**:
the first run appends, subsequent runs report `skipped_current` and append no
new row. The response echoes the live `context_hash`, `context_schema_version`,
`prompt_version`, and `rubric_versions` the run acted on, plus the
`appended_review_id` when a row was written.

The response is the admin-only `AdminRunFakeMachineReviewResponse` (mirrors
`MachineReviewOrchestrationResult`, `extra="forbid"`): it reports an outcome and
carries no mutation instruction and no public `trust.machine_review`.
