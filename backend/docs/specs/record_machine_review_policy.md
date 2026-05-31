# Record-Level Machine-Review Retention & Re-Review Policy

**Status:** draft spec — design only. No code, no migration, no public API, no
public `trust.machine_review`, no upload-workflow trigger, no real LLM
provider, no RAG, no frontend.
**Date:** 2026-05-31
**Scope:** TCKDB backend design only. Defines the retention, staleness, and
re-review policy that must exist *before* a persistent record-level machine
review (`record_machine_review`, `provisional_machine_review.md` §9 Option B)
can be added. Nothing here is implemented; it is the policy a future table and
read layer will encode.
**Audience:** TCKDB backend maintainers, trust-layer authors, future
machine-review implementers.

**Related specs:**

- `provisional_machine_review.md` — the provisional machine-review layer. This
  file expands its §9 (persistence options), §12 (public visibility), and the
  §15 open questions Q2 (latest-vs-history) and Q7 (re-review on evidence
  change) into a concrete policy. Section references below (§N) are to that
  file unless stated otherwise.
- `automated_trust_layer.md` — the deterministic evidence engine. Its output is
  the **evidence context** whose change makes a machine review stale.
- `trust_read_api_current.md` — current public trust-fragment behavior;
  `trust.machine_review` is still absent here and stays that way.
- `admin_machine_review_inspection.md` — the admin-only inspection endpoint that
  today recomputes the projection from audit events with no persistence.
- `machine_review_provider_contract_v2.md` — the provider payload that would
  carry the `model`/`provider`/status a persisted row records.

---

## 1. Why this exists & core principle

Today there is **no** persisted record-level machine review: the admin
inspection endpoint recomputes the projection from `llm_precheck_recorded`
audit events on every call (`provisional_machine_review.md` §0 layer 4). That
is correct while machine review is recomputed-on-demand, but the moment a
machine-review result is **stored** and considered for a public
`trust.machine_review`, a new hazard appears: a stored result can outlive the
evidence it was based on.

> **Core principle: a machine review is valid only for the evidence context it
> reviewed.** A stored machine-review result must not be treated as *current*
> if the underlying deterministic evidence context has changed in a material
> way.

A machine review that flagged "missing IRC evidence" is meaningless once IRC
evidence is added; a "pass" produced before a contradicting calculation was
linked is no longer a pass. Persistence without a staleness rule would let the
system display confident, stale verdicts. This spec defines how a stored review
is judged current, stale, or historical; what retriggers review; and how all of
that coexists with authoritative human review.

This policy changes none of the existing invariants. Machine review still does
not mutate `review_status`, does not mutate deterministic evidence, and does
not certify records (§4, §10, §11 of the provisional spec).

---

## 2. Current vs. stale vs. historical

Three states for a record's machine-review history. They are **derived**, not
stored as mutable flags — staleness is computed at read time by comparing a
stored `context_hash` against a freshly computed one (§3), so an append-only
table never has to be back-updated when evidence changes.

```text
current_machine_review:
  the latest machine review for the record whose context_hash (and currency
  key, §3.5) matches the record's current deterministic evidence context.

stale_machine_review:
  the latest machine review exists, but its context_hash differs from the
  current deterministic evidence context. A review existed; the world moved.

historical_machine_review:
  any earlier machine review retained for audit / model-version comparison
  only. Never the active candidate, regardless of context_hash.
```

| State | "Latest" for the record? | context_hash matches now? | Eligible as active public candidate (future)? |
|---|---|---|---|
| `current` | yes | yes | yes (subject to §6/§7) |
| `stale` | yes | no | no — admin/debug/stale-labelled only |
| `historical` | no (superseded by a newer review) | irrelevant | no |

Notes:

- "Latest" is the single newest review by the deterministic ordering in §4.
  Exactly one review is "latest" per record; it is then classified `current` or
  `stale`.
- `not_run` (no review ever mapped to the record) is **distinct** from `stale`:
  there is nothing to be stale. It is the absence case
  (`MachineReviewStatus.not_run`), already modelled in the read layer.
- A record can oscillate current → stale → current as evidence changes and a
  re-review (§5) runs; each pass is appended, never overwritten (§8).

---

## 3. Context-hash policy

### 3.1 What it is

`context_hash` is a stable digest of the **compact deterministic inputs** a
machine review saw — the same compact context the reviewer consumes
(provisional spec §7), never raw logs. Two reviews with the same `context_hash`
reviewed the same evidence world; a different `context_hash` means the evidence
context materially changed.

It is computed by the same discipline as the existing finding fingerprint
(`compute_finding_fingerprint`): canonicalise inputs (sorted keys, normalised
collections), then hash. It is deterministic — the same inputs always yield the
same hash — and order-insensitive for set-like inputs (check lists, source
refs).

### 3.2 Inputs (included)

Derived from compact deterministic evidence, not raw artifacts:

```text
record_type
record_id (or a stable public record ref)
deterministic evidence rubric name + version      (e.g. computed_kinetics_v1, 1)
passed_checks / missing_checks / warning_checks /
  not_applicable_checks                            (as sorted sets of check keys)
hard_fail_reason
source calculation refs                            (sorted)
source calculation roles                           (sorted, paired with refs)
geometry validation statuses                       (per source geometry)
artifact-kind summaries                            (kinds present, not contents)
review_status snapshot   — read-only human-review input, ONLY if review_status
                            is part of the machine context (see §3.4 and §6)
is_certified snapshot    — read-only human-review input, ONLY if part of the
                            machine context (never a machine-owned output)
selected notes / free-text fields — ONLY if those fields were part of the
                            reviewed context (the prose the rubric can't read)
```

The implemented context contract (`MachineReviewEvidenceContext`,
`app/services/machine_review/context_hash.py`) carries optional
`review_status` and `is_certified` fields for exactly these read-only inputs.
The pure adapter `build_machine_review_evidence_context_from_trust`
(`context_adapter.py`) populates them from the public deterministic
`TrustFragment`; a deployment preferring axis-independence (policy §6) can
leave them out by building the context without them.

The rubric name+version is in the hash on purpose: a rubric version bump
changes the *meaning* of the check set, so the same passed/missing checks under
a new rubric version is a different evidence context, and prior reviews go
stale (§5).

### 3.3 Explicitly excluded

```text
raw artifacts
full Gaussian/ORCA/etc. logs
full coordinate blocks
API keys / secrets / environment variables
private admin notes
volatile timestamps (created_at, reviewed_at, request ids) unless a timestamp
  is itself the semantic input (it generally is not)
provider/model identity            (currency of the EVIDENCE, not the reviewer;
                                    handled by the currency key, §3.5 below)
```

Excluding volatile fields is what makes the hash stable: re-running the same
review over an unchanged record must reproduce the same `context_hash` so a
no-op re-review can be skipped (the dedupe use named in §9 Option B).

**Implemented (commit follows this spec):** the pure builder
`build_machine_review_context_hash` in
`app/services/machine_review/context_hash.py` **rejects** the excluded inputs
rather than silently ignoring them. Its typed input
(`MachineReviewEvidenceContext`) is `extra="forbid"` and carries no field for
raw artifacts/logs/coordinates, secrets, `provider`/`model`, or any timestamp,
so passing one raises at construction — the builder can never hash an excluded
field. Set-like inputs (check sets, artifact kinds, notes, source-calculation
`(ref, role)` pairs, geometry-validation `(ref, status)` pairs) are sorted
before hashing, so input order never changes the digest. The hash is a SHA-256
over canonical, key-sorted, compact JSON (same discipline as
`compute_finding_fingerprint`). No persistence and no public exposure: this is
the pure currency primitive only.

### 3.4 Hash recipe is itself versioned: `context_schema_version`

The set of inputs and the canonicalisation recipe will evolve. Store a
`context_schema_version` alongside every `context_hash`. A review is only
comparable to "now" when both the hash **and** the schema version match the
active recipe. Bumping the recipe is a controlled migration of staleness
semantics: every prior review becomes `stale` under the new recipe until
re-reviewed, rather than silently mis-comparing across recipes.

**Implemented:** `context_schema_version` (constant
`MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION = "v1"`) is **folded into the hashed
payload** and also returned on the `MachineReviewContextDigest`. Folding it in
means two contexts identical except for their schema version hash differently,
so even a hash-only comparison is recipe-safe — a v1 digest can never equal a
v2 digest of the same inputs.

### 3.5 Currency key (currency vs. provenance)

Distinguish two questions:

- **Did the evidence change?** → `context_hash` (+ `context_schema_version`).
- **Did the reviewer recipe change?** → `prompt_version`, `rubric_versions_json`.

A review is **current** iff *all* of:

```text
context_schema_version == active recipe version
context_hash           == hash of the record's current evidence context
prompt_version         == active machine-review prompt version
rubric_versions_json   == active rubric versions for this record_type
```

`provider`/`model` are **not** part of the hard currency key by default
(swapping models does not invalidate the evidence a prior review saw); a
model-refresh policy is an *optional/background* trigger (§5.2), not a
staleness signal. A deployment that wants model-pinned currency can extend the
currency key, but that is opt-in, not the default.

---

## 4. Latest-vs-history retention policy

```text
latest current review:
  the active record-level machine_review candidate. The only review eligible to
  back a future public trust.machine_review (subject to §6/§7).

latest stale review:
  surfaced only as stale/admin/debug context — never as the active public
  machine_review. Useful to a curator ("the last review predates new evidence").

history:
  every earlier review, retained append-only for audit, model/version
  comparison, and re-review provenance. Never an active candidate.
```

### Deterministic ordering

"Latest" is selected by a total order so the choice is stable across calls and
machines:

```text
ORDER BY reviewed_at DESC,
         source_audit_event_id DESC NULLS LAST,
         id DESC                                  -- record_machine_review.id
```

This mirrors the **already implemented** read-model tie-break (`reviewed_at` →
`audit_event_id` → …, commit `ce23e86`): newest by `reviewed_at`, then the
higher source audit event id (monotonic, so the strictly later event), then the
higher persisted row `id` as the final monotonic backstop. For the audit-event
MVP (no table yet) the key is `reviewed_at` then `audit_event_id`; the table
adds `id` as the last tiebreak. The order is total, so latest selection is never
ambiguous.

Selection is two steps, in this order:

1. pick the single **latest** review by the ordering above;
2. classify it `current` or `stale` by the §3.5 currency key.

Staleness never changes *which* review is latest — a stale latest review is
still the latest; it is just not an active public candidate.

### Implemented classifier (naming)

The pure classifier `classify_machine_review_currency`
(`app/services/machine_review/currency.py`) realises §2/§3.5/§4 with no
persistence and no public exposure. Its naming:

- `MachineReviewCurrencyState` — enum `not_run` / `current` / `stale` /
  `historical`. The classification's overall `state` is the latest-derived one
  (`not_run` / `current` / `stale`); `historical` is the per-review label of
  every non-latest review.
- `StoredMachineReviewProjection` — the minimal per-review currency metadata
  (record identity, `reviewed_at`, `id`, `source_audit_event_id`, and the four
  currency dimensions). `source_audit_event_id` is the persisted/projection
  name for the in-memory read model's `audit_event_id`; ordering behavior is
  identical (`reviewed_at` DESC, `source_audit_event_id` DESC NULLS LAST, `id`
  DESC NULLS LAST). Both `id` and `source_audit_event_id` may be `None` and
  sort last.
- `MachineReviewCurrencyKey` — the four currency dimensions
  (`context_schema_version`, `context_hash`, `prompt_version`,
  `rubric_versions`) compared for the active recipe vs. each stored review.
  `rubric_versions` is compared by canonical mapping equality (key order
  irrelevant; a missing/extra/changed key is a mismatch).
- `MachineReviewCurrencyClassification` — the result: `state`, `active_review`
  (the latest, or `None`), `historical_reviews` (non-latest, newest-first), and
  `stale_reasons`.
- `MachineReviewStaleReason` (`stale_reasons`) — enum
  `context_schema_version_mismatch` / `context_hash_mismatch` /
  `prompt_version_mismatch` / `rubric_versions_mismatch`, emitted (in that fixed
  order) only when `state` is `stale`.

A latest-selection determinism backstop: when the three policy ordering keys are
fully tied (only possible for in-memory projections with `id is None`, never for
unique-PK rows), a final tiebreak on immutable currency content keeps the
classification deterministic across input order without altering the policy
ordering for any real data.

---

## 5. Re-review triggers

A trigger is anything that *could* change the evidence context (§3) or the
reviewer recipe, and therefore *could* move the latest review from `current` to
`stale`. Triggers are graded by obligation.

### 5.1 Required re-review triggers

The latest review's currency is materially affected; the record should be
re-reviewed (or explicitly marked stale until it is). Each of these changes the
`context_hash` or the currency key:

```text
deterministic evidence evaluation changed (any passed/missing/warning/
  not_applicable check set, evidence_completeness inputs, or hard_fail_reason)
a new source calculation is linked, or a linked source calculation is removed
a linked source calculation's quality/status changes (e.g. to rejected)
geometry validation status changes for a source geometry
a new artifact kind is linked (artifact-kind summary changes)
deterministic evidence rubric name/version bump
machine-review prompt_version bump
machine-review rubric_versions bump (the reviewer's own rubric recipe)
context_schema_version bump (the hash recipe itself, §3.4)
```

Required does **not** mean "re-review synchronously inside a request." It means
"the stored latest review is no longer current; until a re-review runs, the
record's active machine_review is stale (and omitted/labelled per §7)."

### 5.2 Optional / background re-review triggers

Worth re-reviewing eventually, but not a correctness hazard if deferred:

```text
provider/model version changed (only if a model-refresh policy opts in, §3.5)
a newer/stronger machine-review model becomes available
periodic background refresh of long-unreviewed records
batch re-review after a non-material reviewer change
```

These run as background/batch jobs. They never block uploads or reads and never
fail an upload (the failure contract is unchanged: machine failure → a
`machine_review_failed` pass, never an upload failure).

### 5.3 Admin / manual re-review triggers

```text
an admin explicitly requests re-review of a record or submission
a curator disputes a stale or suspicious result
debugging via the admin inspection endpoint surfaces a mapping/parse anomaly
```

### 5.4 What a trigger does NOT do

```text
does not mutate review_status
does not mutate deterministic evidence
does not delete or overwrite prior machine reviews (append-only, §8)
does not auto-approve/auto-reject the record
does not run synchronously in the upload transaction (failure must never fail
  an otherwise valid upload)
```

When evidence changes and no re-review has run yet, the prior review is **not**
silently kept as current and **not** destructively reset — it is retained and
classified `stale` by comparison (§2). Whether a background job then
auto-re-reviews or leaves it stale-until-asked is an open question (§11 Q3).

---

## 6. Human-review interaction

Human review (`RecordReviewStatus`: `not_reviewed` / `under_review` /
`approved` / `rejected` / `deprecated`) remains the **authoritative** axis.
Machine review is advisory on its own axis and never overrides it.

```text
human approved/rejected/deprecated does NOT erase machine-review history
  — the append-only history is preserved for audit (§8).

human approved
  — makes machine review less load-bearing, not invalid. A current machine
    review may still be shown as context, but human review_status is dominant.

human rejected / deprecated
  — suppresses the active machine_review from public surfaces (§7): a rejected
    record must not be made to look acceptable by a stale or even current
    machine "pass".

benchmark_reference
  — a human action (a ConformerSelectionKind). Machine review can recommend
    against promotion but can never assign it (provisional spec §11).

machine review cannot override human review
  — on conflict, human wins for visibility and endorsement; the machine signal
    remains visible only as advisory context.
```

### Does a human `review_status` change make machine review stale?

Conditional, and deliberately so:

- **If `review_status` is part of the machine context** (the snapshot was an
  input the reviewer saw, §3.2), then a human `review_status` change alters the
  `context_hash` → the prior machine review goes `stale` (a §5.1 required
  trigger). Use this when the reviewer's prompt reasons about review state.
- **If `review_status` is NOT in the machine context** (the default-safe
  choice — machine and human review are separate axes), a human review change
  does **not** make the machine review stale. Human `rejected`/`deprecated`
  still **suppresses** public display (§7), but that is a display rule, not a
  staleness/recompute rule.

Recommended default: **exclude `review_status` from `context_hash`**, and rely
on the §7 suppression rule. This keeps the axes independent and avoids
re-reviewing a record merely because a human looked at it.

---

## 7. Public display policy (future — not implemented now)

This describes how a *future* public `trust.machine_review` would behave. It is
specified here only so the persistence and currency model is sufficient to
support it. **No public `trust.machine_review` is emitted today**, and
`trust.llm_precheck` stays disabled/`not_run` (provisional spec §0, §12).

```text
current machine review exists:
  public trust.machine_review MAY show status + summary, clearly labelled as
  machine output, never altering any deterministic evidence field.

only a stale machine review exists:
  public trust.machine_review is OMITTED, or shown explicitly marked `stale`
  (status carried, but flagged not-current). It is never presented as a current
  verdict.

record is human rejected / deprecated:
  public machine_review must NOT make the record look acceptable. Suppress the
  active machine_review (or show it only as historical/advisory context).
  Human rejection dominates.

record is human approved:
  human review_status remains dominant; a current machine review may appear
  beside it as context but does not change the human verdict.
```

Strict-consumer filters (`review_level=human_only`,
`include_machine_reviewed=false`) remain a separate, later read-API design
(provisional spec §12) and are out of scope here.

---

## 8. Persistence implications (prepare, do not implement)

This spec **prepares** the future table; it does not add it. The table lands as
a **new Alembic revision** per `migration-rules.md` (a deployed-schema change →
new revision with both `upgrade()`/`downgrade()`), never folded into a deployed
migration, and only when public record-level review is actually being built.

### Candidate `record_machine_review` table

```text
record_machine_review
  id                        -- PK; monotonic; final latest-selection tiebreak
  record_type               -- SubmissionRecordType
  record_id                 -- internal id (governed by internal-id policy)
  status                    -- MachineReviewStatus
  curator_priority          -- low | medium | high (nullable)
  summary                   -- bounded text (nullable)
  findings_json             -- the validated structured findings for this pass
  model                     -- provider/model string (nullable)
  provider                  -- provider name (nullable)
  prompt_version            -- machine-review prompt version (currency key, §3.5)
  rubric_versions_json      -- rubric name->version map seen (currency key)
  context_hash              -- digest of the compact evidence context (§3)
  context_schema_version    -- version of the hash recipe (§3.4)
  source_submission_id      -- nullable; submission that triggered this review
  source_audit_event_id     -- nullable; the llm_precheck_recorded event, if any
  reviewed_at               -- when the review ran (latest-selection primary key)
  created_at                -- row insertion time
```

This is the provisional spec §9 Option B sketch made precise: it adds the
currency fields (`prompt_version`, `rubric_versions_json`,
`context_schema_version`), the provenance link (`source_audit_event_id`), and
states the ordering columns.

**Implemented** as `RecordMachineReviewRow`
(`app/db/models/record_machine_review.py`) + revision
`c9d0e1f2a3b4_add_record_machine_review`. Notes on the realised shape: `status`
reuses the existing DB-layer `machine_review_status` enum and `record_type` the
`submission_record_type` enum (no enum churn); `curator_priority` is stored as
text with application-level validation. CHECK constraints enforce
`char_length(context_hash) = 64` and `jsonb_typeof(findings_json) = 'array'`;
indexes back the latest-selection paths
(`(record_type, record_id, reviewed_at DESC)` and the
`source_audit_event_id DESC` tiebreak) plus `context_hash`,
`source_submission_id`, and `source_audit_event_id`.

### Uniqueness / write model

```text
Append-only. No destructive updates. No "one row per record" constraint.
The latest-current review is selected by query (§4), never by mutating a row.
```

Recommended over a "one active current row + history" shape because:

- it makes staleness derivable (compare stored vs. current hash) instead of
  requiring a back-update of an `is_current` flag every time evidence changes —
  which would be a write amplification and a drift hazard;
- it preserves full history for model/version comparison for free;
- it matches the existing append-only audit-event model the projection already
  reads, so the cutover (Option A → Option B, §11 Q8) is additive.

An optional **materialised latest view** (provisional spec §9 Option C) can be
added later purely for read/search filtering; it is a cache of the §4 query, not
a source of truth, and is not required for MVP.

A non-unique index on `(record_type, record_id, reviewed_at DESC, id DESC)`
supports the latest-selection query; it is an implementation detail for the
revision, noted here only so the ordering is index-supportable.

---

## 9. Tests to require later

When the table and currency model are implemented, require tests proving (these
are future obligations, not part of this design):

```text
same context_hash selects the latest current review (no spurious staleness)
a changed evidence context marks the prior latest review stale (hash differs)
linking a new source calculation changes context_hash (required trigger)
linking a new artifact kind changes context_hash (required trigger)
a rubric name/version bump changes context_hash / currency key -> stale
a prompt_version bump marks prior reviews stale (currency key, not hash)
a context_schema_version bump marks all prior reviews stale until re-review
human rejected/deprecated suppresses the active public machine_review
human approved does not let machine_review override review_status
machine review never mutates review_status or deterministic evidence
latest-selection tie-break is deterministic (reviewed_at, then audit_event_id/
  id), stable across input orders and processes
history is retained after re-review (append-only; no row deleted/overwritten)
a no-op re-review over unchanged evidence reproduces the same context_hash
  (dedupe: the re-review can be skipped)
provider/model change alone does NOT mark a review stale by default
volatile timestamps do NOT affect context_hash (stable digest)
```

---

## 10. Non-goals

```text
No implementation.
No migration / no table creation.
No public API change.
No public trust.machine_review.
No upload-workflow trigger or synchronous re-review.
No real LLM provider.
No RAG.
No frontend.
No automatic approval/rejection.
No mutation of review_status, deterministic evidence, or certification.
```

---

## 11. Open design questions

1. **`review_status` in the context_hash?** Default recommended: exclude it,
   rely on the §7 suppression rule (keeps machine/human axes independent).
   Revisit if a reviewer prompt is designed to reason about human review state.
2. **Provider/model in the currency key?** Default: no (currency is about the
   evidence, not the reviewer). A model-pinned deployment may opt in (§3.5).
3. **Stale-until-asked vs. auto-re-review.** When a required trigger fires and
   no re-review has run, is the latest review left `stale` until a background
   job or admin asks, or is auto-re-review enqueued immediately? (Provisional
   spec §15 Q7.) The failure contract is identical either way.
4. **Re-review trigger ownership.** Where do triggers live — in the workflow
   that mutates evidence, in a background watcher diffing `context_hash`, or in
   a dedicated re-review service? (Provisional spec §15 Q6.)
5. **History retention bound.** Is history kept forever, or pruned/rolled-up
   after N reviews or T time per record? Pruning must preserve at least the
   latest current and latest stale review for explainability.
6. **Cross-rubric records.** A record evaluated by more than one rubric over its
   life — does `context_hash` key on the *active* rubric only, or carry a
   per-rubric currency? (Interacts with the rubric-version input, §3.2.)
7. **Audit-event MVP vs. table cutover.** Do the recomputed-from-audit-events
   projection (today) and the persisted table run in parallel during
   transition, and which is authoritative meanwhile? (Provisional spec §15 Q8.)
8. **Materialised latest view.** Add the Option C view at table-introduction
   time, or defer until per-record read/search filtering is a real requirement?

---

## 12. Recommended implementation order

Contract-and-policy first; persistence and exposure last. Each step is gated on
the prior and on the provisional spec's §13 non-interference tests.

```text
1. Land this policy (done here): current/stale/historical, context_hash inputs
   + exclusions + context_schema_version, currency key, ordering, triggers,
   human-review interaction, public-display rule, append-only persistence shape.
2. Implement a PURE context_hash builder over the compact evidence context
   (no table, no exposure), unit-tested for stability, order-insensitivity, and
   exclusion of volatile/raw inputs. Reuse the fingerprint canonicalisation.
   DONE: `app/services/machine_review/context_hash.py`
   (`build_machine_review_context_hash` + the typed `MachineReviewEvidenceContext`
   / `MachineReviewContextDigest`), covered by
   `tests/services/test_machine_review_context_hash.py`. Excluded inputs are
   rejected at the typed boundary (§3.3); the schema version is folded into the
   hash (§3.4). No persistence, no public exposure.
3. Implement a PURE currency classifier (current/stale/historical) over the
   existing in-memory RecordMachineReview projections — still no persistence —
   so staleness is provable before any table exists.
   DONE: `app/services/machine_review/currency.py`
   (`classify_machine_review_currency`), covered by
   `tests/services/test_machine_review_currency.py`. See "Implemented
   classifier" below for naming. No persistence, no public exposure.
   The pure **context/currency adapter** that wires steps 2–3 against *real*
   deterministic trust output is also DONE:
   `app/services/machine_review/context_adapter.py`
   (`build_machine_review_evidence_context_from_trust` and
   `stored_projection_from_record_machine_review`), covered by
   `tests/services/test_machine_review_context_adapter.py`. It reads the public
   `TrustFragment` (read-only, non-interfering), builds the evidence context →
   digest → `StoredMachineReviewProjection`, and feeds the classifier — proving
   currency against real evidence with no persistence and no public exposure.
   This was the last pure step before persistence.
4. Add the record_machine_review table (§8) in a NEW Alembic revision only when
   public record-level review is actually being built; append-only, both
   upgrade()/downgrade(). Backfill is not required (history starts empty).
   DONE (persistence only — NOT public exposure): new Alembic revision
   `c9d0e1f2a3b4_add_record_machine_review` (append-only table, real
   upgrade()/downgrade(), reusing the `submission_record_type` /
   `machine_review_status` enums); ORM model
   `app/db/models/record_machine_review.py::RecordMachineReviewRow`; the
   row→`StoredMachineReviewProjection` path and append helper in
   `app/services/machine_review/persistence.py`
   (`stored_projection_from_record_machine_review_row`,
   `classify_record_machine_review_currency_from_rows`,
   `create_record_machine_review_row`); covered by
   `tests/services/test_record_machine_review_persistence.py`. No uniqueness
   over `(record_type, record_id)` — multiple historical rows are expected and
   "which is live" stays a read-time classification. Not wired into uploads; no
   public `trust.machine_review`.
   The private **query service** over persisted rows is also DONE:
   `app/services/machine_review/query.py`
   (`list_record_machine_review_rows_for_record`,
   `get_latest_record_machine_review_row`,
   `get_record_machine_review_currency_for_record`), covered by
   `tests/services/test_record_machine_review_query.py`. It is the single
   read path (for future admin inspection, re-review triggers, and eventual
   public-trust projection): it filters by exact `(record_type, record_id)`,
   returns rows newest-first in the classifier's exact ordering (`reviewed_at`
   DESC, `source_audit_event_id` DESC NULLS LAST, `id` DESC NULLS LAST), and
   classifies persisted currency via the pure classifier. Read-only and
   non-interfering. **Re-review triggers (step 5) and public exposure (step 6)
   remain NOT done.**
5. Wire re-review triggers (§5) as background/admin paths that APPEND rows;
   never synchronous in uploads, never mutating review_status or evidence.
6. Only then expose public trust.machine_review behind the latest-current
   selection (§4) and the display rules (§7), labelled as machine output,
   never altering deterministic fields, with the §9 tests green.
7. Add strict-consumer read filters and the optional materialised latest view
   as a separate, later read-API design.
```

Human `review_status` remains authoritative throughout every step.
