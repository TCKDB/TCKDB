# Private Machine-Review Stack — Readiness Audit

**Status:** audit / readiness report. Descriptive only — no feature work, no
migration, no API change. Records the state of the private/admin machine-review
stack and recommends the next phase.
**Date:** 2026-06-01
**Scope:** TCKDB backend. The machine-review stack remains **private/admin-only
and advisory**; this audit confirms that boundary still holds.
**Method:** code + docs inspection of `app/services/machine_review/*`,
`app/db/models/record_machine_review.py`, the `c9d0e1f2a3b4` migration, the admin
route, and the public scientific read surface; plus the targeted test run below.
**Related:** `machine_review_lifecycle.md` (architecture), `record_machine_review_policy.md`
(policy), `machine_review_handoff.md` (workstream checkpoint).

---

## Implemented stack summary

The full private loop is implemented and composes cleanly, each layer pure where
it should be and writing only where it must:

| Layer | Module | Notes |
|-------|--------|-------|
| Deterministic evidence | `app/services/trust/evaluator.py` + `fragment.py` | Produces the `TrustFragment` machine review reads |
| Evidence context | `machine_review/context_adapter.py` | `TrustFragment` → `MachineReviewEvidenceContext` |
| Context digest / schema | `machine_review/context_hash.py` | `context_hash` + `context_schema_version` |
| Currency classifier | `machine_review/currency.py` | Pure current/stale/historical |
| Re-review planner | `machine_review/rereview.py` | Planning only; appends nothing |
| Re-review executor | `machine_review/rereview_execution.py` | Sole append decision; idempotency guard |
| Producer seam | `machine_review/producer.py` | Protocol + `FakeMachineReviewProducer` only |
| Orchestration | `machine_review/orchestration.py` | plan → produce → execute |
| Persistence | `machine_review/persistence.py` | Append-only row + row→projection |
| Query / currency read | `machine_review/query.py` | Persisted-row read path |
| Admin trigger | `machine_review/admin_trigger.py` + `app/api/routes/admin.py` | Admin-only, fake-only |
| Private inspection / read | `machine_review/inspection.py`, `read_model.py`, `trust_adapter.py` | No public wiring |

Supporting axes also present: `schemas.py`, `derivation.py`, `mapping.py`,
`audit_adapter.py`, and the curator-task queue (`curator_tasks.py`,
`curator_task_lifecycle.py`) on its own state axis.

**Audit findings (tasks 1–9):**

1. **Module boundaries — consistent.** Planner is read-only; the executor is the
   only decision point that appends; persistence is the only module that calls
   `session.add` for this table (see write-boundary evidence below). Producer is
   pure (no DB, no I/O).
2. **Naming — consistent.** `context_*` / `currency` / `rereview` / `rereview_execution`
   / `producer` / `orchestration` / `persistence` / `query` / `admin_trigger`
   match the lifecycle doc and the policy. The DB row type is `RecordMachineReviewRow`;
   the in-memory pass is `RecordMachineReview` — distinct and used consistently.
3. **Docs — consistent after this cycle.** `record_machine_review_policy.md` status
   header was updated to "partially implemented"; `machine_review_lifecycle.md`
   was added and cross-linked. No remaining "design only / nothing implemented"
   contradiction was found in those two docs. (`provisional_machine_review.md`
   and the handoff doc remain historical context, clearly dated.)
4. **Public/private boundary — no leak.** `grep` for `machine_review` across
   `app/api/routes/scientific/`, `app/schemas/reads/`, and
   `app/services/scientific_read/` returns nothing. `TrustFragment` fields are
   exactly `review_status`, `trust_status`, `evidence`, `llm_precheck`,
   `is_certified` — no `machine_review`.
5. **Exports — intentional.** `machine_review/__init__.py` re-exports the
   admin-trigger entrypoints and the active-recipe constants; the private
   `InternalTrustEnvelopeWithMachineReview` / `build_private_trust_envelope_with_machine_review`
   are exported but have **no importer outside the package** — they are the seam
   for the future public projection, not a wired public path. Not a leak.
6. **Test coverage — broad** (see summary below).
7. **Migration/model drift — none.** `record_machine_review` columns, FKs
   (`source_submission_id`→`submission`, `source_audit_event_id`→`submission_audit_event`),
   indexes, and the two `CheckConstraint`s (`char_length(context_hash)=64`,
   `jsonb_typeof(findings_json)='array'`) match between the ORM model and the
   `c9d0e1f2a3b4` migration. The pytest fixture rebuilds the DB from migrations
   and the suite is green, so the runtime schema matches the model.
8. **Active prompt/rubric ownership — single source, see risk R1.** The active
   recipe lives **only** in `admin_trigger.py`
   (`ACTIVE_MACHINE_REVIEW_PROMPT_VERSION = "machine_review_v1"`,
   `ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS` derived from the trust rubric
   constants). No duplicate/contradictory definition exists. Only the admin
   trigger consumes it today.
9. **Internal-ID exposure — contained, see risk R2.** Internal ids
   (`record_id`, `appended_review_id`, `source_audit_event_id`) appear only on
   the **admin-only** trigger/inspection responses, which is documented and
   acceptable. No internal id reaches a public scientific surface.

---

## Public boundary status

All required boundary invariants hold (verified by inspection + tests):

```text
[OK] public TrustFragment has no machine_review        (TrustFragment fields fixed; no machine_review key)
[OK] include=machine_review is rejected                (422 unknown_include_token; not a legal token anywhere)
[OK] include=trust,machine_review is rejected          (422; tested)
[OK] record_machine_review is append-only              (only persistence.create_*_row inserts; no update/delete)
[OK] fake trigger is admin-only                        (require_admin; anon 401, user/curator 403 — tested)
[OK] fake trigger writes only record_machine_review    (sole session.add is persistence.py:169)
[OK] machine review does not mutate review_status      (non-interference tests)
[OK] machine review does not mutate is_certified       (non-interference tests)
[OK] machine review does not mutate deterministic evidence (byte-identical snapshot test)
[OK] machine review does not mutate scientific records (non-interference tests)
[OK] machine review does not mutate submission.status  (non-interference + admin-trigger tests)
```

**Write-boundary evidence.** Across `persistence.py`, `rereview_execution.py`,
`orchestration.py`, and `admin_trigger.py` the only mutating ORM call is a single
`session.add(row)` at `persistence.py:169` (the `RecordMachineReviewRow`). The
executor/orchestration/trigger never add, delete, merge, or bulk-update.

---

## Persistence / currency status

- **Table:** `record_machine_review` (`c9d0e1f2a3b4`), append-only, no uniqueness
  over `(record_type, record_id)` — multiple historical rows are expected and
  "which is live" is a read-time classification (policy §4).
- **Currency key:** `context_hash` + `context_schema_version` + `prompt_version`
  + `rubric_versions_json`. `provider`/`model` are deliberately not currency
  dimensions. Latest selection is `reviewed_at DESC, source_audit_event_id DESC
  NULLS LAST, id DESC NULLS LAST`, backed by matching indexes.
- **Classifier:** pure; the persisted wrapper only loads/projects rows. current /
  stale / historical / not_run states are all exercised.

---

## Admin trigger status

`POST /api/v1/admin/machine-review/records/{record_type}/{record_id}/run-fake`

- Admin-only (`require_admin`); fake producer only (rows carry `provider="fake"`,
  `model="fake-test"`).
- Supported `record_type`: `calculation`, `kinetics`, `thermo`, `statmech`,
  `transport`, `transition_state_entry` (those with both a computed trust
  evaluator and a persisted home).
- Outcomes verified by tests: not reviewed → appends (`run_not_reviewed`); stale
  → appends (`run_stale`); current → skips (`skip_current`); unsupported
  `record_type` → 400; missing record → 404; unchanged recipe → idempotent.
- Response is `AdminRunFakeMachineReviewResponse` (`extra="forbid"`), mirrors
  `MachineReviewOrchestrationResult`, carries no mutation instruction and no
  public `trust.machine_review`.

---

## Test coverage summary

Targeted run (this audit):

```text
pytest test_machine_review_producer / orchestration / rereview_execution /
       rereview / record_machine_review_query / record_machine_review_persistence /
       test_admin_machine_review_trigger / test_trust_evaluator
=> 198 passed
```

Coverage is broad across the package: context hash, context adapter, currency,
planner, executor, producer, orchestration, persistence, query, admin trigger,
inspection, read model, trust adapter, mapping, derivation, audit adapter,
non-interference, contracts, golden examples, and the curator-task queue + admin
APIs. The OpenAPI golden snapshot includes the run-fake route.

**Gaps (minor, not blocking):**

- No explicit test asserts the active-recipe constants stay derived from the
  trust rubric constants (a rubric `version` bump should change
  `ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS`). Worth a tiny contract test before a
  real provider relies on the recipe.
- No test pins the `record_ref` basis used by the admin trigger
  (`str(record_id)`); see risk R2 — relevant only when public projection lands.

---

## Known risks

- **R1 — Active-recipe ownership will need a real home (low now).** The recipe
  lives in `admin_trigger.py`. The moment a second consumer appears (a real
  provider, a background re-review job), it should move to a small shared
  `recipe`/config module so all callers agree on prompt/rubric versions. Easy to
  refactor; flag before adding the second consumer.
- **R2 — `record_ref` is internal-id based (medium for public projection).**
  Persisted rows' `context_hash` is computed from `record_ref = str(record_id)`.
  A public projection keyed by `public_ref` must either keep the same id-based
  ref basis for hashing or accept a one-time re-hash/migration. Decide the ref
  basis *before* exposing publicly. No impact on the current private stack.
- **R3 — Fake-only data (expected).** Every persisted row today is fake
  (`provider="fake"`). Projecting these publicly would be meaningless, so public
  projection should not precede real rows.
- **R4 — Wall-clock at the trigger boundary (negligible).** The route stamps
  `reviewed_at` with naive-UTC now (consistent with `record_review`/`submission`
  convention); the orchestration core stays clock-free. No correctness issue.

---

## Recommended next phase

**Real provider implementation** is the highest-leverage, lowest-architectural-risk
next step, and it unblocks everything downstream:

- The `MachineReviewProducer` protocol seam is complete and isolated. A real
  provider slots in behind `run_record_machine_review_with_producer(...)` with
  **no** change to planning, currency, execution, persistence, or the admin
  route — only a new `producer` implementation and a way to select it.
- It is a prerequisite for any *meaningful* public projection (projecting fake
  rows is pointless — R3) and for background re-review (which needs a real
  producer to call).

**Sequencing recommendation:**

1. **Real provider** (next) — behind the existing seam; keep it admin/opt-in and
   private; do not change the public contract.
2. **Background/scheduled re-review** — natural follow-on once a real producer
   exists; append-only, never synchronous in uploads.
3. **Public `trust.machine_review` projection** — only after real rows exist and
   R2 (ref basis) + policy §7 display rules + read filters are settled. This is
   the only phase that touches the public contract, so it should be last and
   gated on the §9 policy tests.

**Admin/curator UI** can proceed **in parallel and independently** — it is a
frontend effort over the already-stable private admin/inspection/curator-task
APIs and does not block or depend on the backend phases above.

---

## Concrete next tasks

```text
[real provider]
- Add a real provider implementing MachineReviewProducer (e.g. cloud/local),
  selected behind a private/admin config flag; keep FakeMachineReviewProducer
  the default. No orchestration/persistence/route changes.
- Move ACTIVE_MACHINE_REVIEW_PROMPT_VERSION / ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS
  into a shared machine_review recipe/config module (R1) once the provider
  consumes them too; add a contract test that the rubric versions stay derived
  from the trust rubric constants.
- Keep the real provider out of the upload transaction; failures must not fail
  uploads.

[before public projection — design, not code yet]
- Decide the record_ref basis for public projection (R2): id-based hash vs
  public_ref; document in record_machine_review_policy.md §3/§7.
- Specify the public display rules and read filters (review_level=human_only,
  include_machine_reviewed=false) as a separate read-API design.

[hygiene now]
- Optional: tiny test asserting a rubric version bump changes
  ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS (guards R1).
```

**Not started / explicitly deferred:** real/cloud/local producer, RAG,
background/scheduled triggers, upload-workflow wiring, public
`trust.machine_review` exposure, frontend/curator UI, and public filters such as
`review_level=human_only` / `include_machine_reviewed=false`.
