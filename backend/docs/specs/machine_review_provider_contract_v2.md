# Machine-Review Provider Output Contract v2

**Status:** design / spec only. No provider code, no schema change, no adapter
change, no migration, no public `trust.machine_review`. Designs a *future* v2
provider output contract and the adapter dispatch that would accept it
alongside the existing v1 precheck payloads.
**Date:** 2026-05-31
**Scope:** TCKDB backend design only. No implementation. No public scientific
read change. No automatic task creation. No real provider / prompt text. No
RAG. No ARC / `tckdb-client` change.
**Audience:** TCKDB backend maintainers who will implement the v2 contract +
adapter dispatch, and whoever later wires a real (cloud/local) provider.

**Related specs:**

- `optional_llm_precheck.md` — the v1 advisory precheck plumbing and its
  `LLMPrecheckResult` / `LLMFinding` contract this spec extends.
- `provisional_machine_review.md` — the machine-review vocabulary
  (`MachineReviewStatus` / `MachineReviewSeverity` / `MachineReviewCategory` /
  `CuratorPriority`) the v2 payload speaks natively.
- `machine_review_golden_examples.md` — the golden fake-provider examples that
  surfaced the v1↔target vocabulary gap this spec closes; future v2 examples
  extend that set.
- `admin_machine_review_curator_task_api.md` — the admin workflow over the tasks
  that v2 findings would feed.
- `machine_review_handoff.md` — the workstream checkpoint (§9 decision log,
  §11 next options).
- `machine_review_real_provider_plumbing.md` — the *producer* design (Off /
  Cloud / Local provider plumbing) that would emit the v2 payloads this contract
  defines.

---

## 1. Problem statement

The machine-review stack was bootstrapped on the **v1** advisory precheck
contract:

```text
optional_llm_precheck was originally submission/admin advisory plumbing.
machine_review is now a richer provisional record-level concept.
The audit adapter (audit_adapter.py) translates LLMPrecheckResult ->
  MachineReviewResult on the fly.
The golden examples (machine_review_golden_examples.md) showed the SOURCE
  contract cannot express every TARGET concept.
```

Concretely, a payload persisted as `LLMPrecheckResult` cannot express several
machine-review concepts the adapter's *target* (`MachineReviewResult` /
`MachineReviewFinding`) already supports:

| Concept | v1 `LLMPrecheckResult` / `LLMFinding` | machine-review target |
|---|---|---|
| record-level status | only `label` (`LLMPrecheckLabel`), translated | `status` (`MachineReviewStatus`) directly |
| `curator_priority` | absent | `CuratorPriority` (low/medium/high) |
| finding `recommended_action` | absent | present on `MachineReviewFinding` |
| categories | 8-value `LLMFindingCategory` subset | 11-value `MachineReviewCategory` |
| `transition_state_validation` / `schema_gap` | **not expressible** | first-class categories |

The golden `transition_state_critical_creates_task` case had to fall back to
`consistency` and drop `recommended_action` precisely because the v1 source
contract is narrower (see `machine_review_golden_examples.md`, "Vocabulary
caveat"). v2 closes this gap.

---

## 2. Backward compatibility (hard requirement)

```text
Existing llm_precheck_recorded audit events remain valid forever.
The adapter MUST continue to parse v1 LLMPrecheckResult payloads unchanged.
V2 is ADDITIVE and VERSIONED — never a breaking replacement of v1.
A submission may carry a mix of v1 and v2 events over time; each event is
  parsed by its own version. No backfill, no rewrite of stored v1 payloads.
```

v1 golden tests must keep passing after v2 lands. v2 is a new accepted shape,
not a migration of the old one.

---

## 3. Versioning marker

**Recommendation: a single namespaced string field on the payload root:**

```json
{ "schema_version": "machine_review_v2" }
```

Chosen over `{ "kind": "machine_review_result", "version": 2 }` because:

- **One field, one decision.** Dispatch is a single equality/prefix check; the
  two-field form couples `kind` and `version` and invites mismatches
  (`kind` right, `version` wrong).
- **Future-proof + greppable.** `machine_review_v3` is an obvious next token;
  `schema_version` is easy to search across payloads and logs.
- **Clean v1 detection by absence.** v1 `LLMPrecheckResult` payloads have **no**
  `schema_version` key, so "no marker ⇒ v1" is unambiguous and needs no change
  to already-stored events.
- **Already additive.** `details_json` already carries an extra `provider` key
  the precheck contract ignores; adding `schema_version` follows the same
  additive pattern.

The marker lives at the payload root in `details_json`. `extra="forbid"` on the
v2 model means the marker is a declared field, not stray data.

---

## 4. v2 result shape (`MachineReviewResultV2`)

A provider emitting v2 writes the machine-review vocabulary **natively** — no
label→status translation. The shape mirrors the existing internal
`MachineReviewResult` (`app/services/machine_review/schemas.py`) plus the
version marker and a first-class `provider` field (today provider is a sibling
key on the event, read separately):

```json
{
  "schema_version": "machine_review_v2",
  "status": "machine_screened_needs_attention",
  "curator_priority": "high",
  "summary": "One critical transition-state contradiction.",
  "model": "vendor/model-name",
  "provider": "VendorProvider",
  "used_rag": false,
  "findings": [ ... ]
}
```

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `"machine_review_v2"` | required v2 marker (§3) |
| `status` | `MachineReviewStatus` | `not_run` / `machine_screened_pass` / `machine_screened_warning` / `machine_screened_needs_attention` / `machine_review_failed` (`machine_screened_blocking_concern` stays reserved) |
| `curator_priority` | `CuratorPriority` \| null | `low` / `medium` / `high`; advisory ordering hint only |
| `summary` | str \| null | ≤ 2000 chars |
| `model` | str \| null | ≤ 128 chars |
| `provider` | str \| null | ≤ 128 chars; folded into the payload in v2 |
| `used_rag` | `Literal[false]` | RAG is a non-goal; any truthy value fails validation (§6) |
| `findings` | list | ≤ 50; see §4.1 |

`model_config = extra="forbid", frozen=True` — same as `MachineReviewResult`. No
mutation-payload field exists by design (§6).

### 4.1 v2 finding shape

Maps 1:1 onto the existing `MachineReviewFinding`, with explicit record
addressing:

| Field | Type | Notes |
|---|---|---|
| `severity` | `MachineReviewSeverity` | `info` / `warning` / `critical` |
| `category` | `MachineReviewCategory` | full set (§5) |
| `record_type` | str \| null | a `SubmissionRecordType` token; null ⇒ submission-scoped |
| `record_ref` | str \| null | the mapping key (public ref or stringified internal id, per the private-context rules in `audit_adapter.py`) |
| `record_id` | int \| null | optional internal-id alias; when present and `record_ref` absent, the adapter derives `record_ref = str(record_id)` (matches today's v1 behavior) |
| `message` | str | 1–1000 chars |
| `evidence_keys` | list[str] | ≤ 20; order-insensitive for fingerprinting |
| `recommended_action` | str \| null | ≤ 1000 chars; advisory free text for a curator, **never executed** |

Addressing rule: a finding maps only to the *exact* `(record_type, record_ref)`
linked to the submission — the anti-fan-out policy in
`provisional_machine_review.md` §6/§13 and `mapping.py` is unchanged. v2 does
**not** relax mapping; it only enriches what a finding can say.

---

## 5. Category vocabulary

v2 uses the full `MachineReviewCategory` set (already in
`app/services/machine_review/schemas.py`):

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

This is the real implemented set; `transition_state_validation` and
`schema_gap` — absent from the v1 `LLMFindingCategory` subset — become
expressible.

---

## 6. Provider constraints (unchanged from v1, restated for v2)

```text
used_rag MUST be false for now (Literal[false]; any other value -> validation
  failure -> machine_review_failed / parse warning, never an upload failure).
Provider output is UNTRUSTED and MUST be schema-validated before any use.
Malformed / non-projectable output degrades to a parse warning and
  status machine_review_failed — it NEVER raises and NEVER fails the upload.
Provider-supplied mutation payloads are FORBIDDEN: extra="forbid" guarantees
  no field beyond the contract can be smuggled through.
A provider CANNOT approve / reject / certify / hide a record. recommended_action
  is advisory text for a human; it is never executed. Machine review writes
  nothing authoritative (no RecordReviewStatus, submission.status, is_certified,
  benchmark_reference, evidence, or public trust).
```

---

## 7. Adapter behavior (version dispatch)

The audit adapter (`machine_review_result_from_audit_event` in
`audit_adapter.py`) gains a single dispatch at the top, then keeps both paths:

```text
read details_json (untrusted)
if not a dict                          -> parse warning, result=None
elif details_json.get("schema_version") == "machine_review_v2":
    validate directly as MachineReviewResultV2   (no label->status translation)
    on ValidationError                 -> parse warning, result=None
else:                                  # no marker => legacy v1
    validate as LLMPrecheckResult and translate to MachineReviewResult
                                       (current behavior, unchanged)
```

Notes:

- Dispatch is purely on the marker; a missing/unknown `schema_version`
  routes to the v1 path (preserving every stored event). An *unknown but
  present* version (e.g. `machine_review_v3` before it exists) should produce a
  parse warning, not silently fall back to v1.
- Both branches converge on the same internal `MachineReviewResult` →
  `map_findings_to_submission_records` → `RecordMachineReview` →
  `MachineReviewRecordSummary` pipeline. v2 simply skips the translation step
  because it already speaks the target vocabulary.
- The v2 `provider` field, when present, supersedes the sibling
  `details_json["provider"]` lookup; for v1 the sibling lookup stays.
- Everything downstream (`inspection`, `build_curator_tasks_for_submission`,
  the curator task queue API) is **unchanged** — it already consumes
  `MachineReviewResult` / `RecordMachineReview`.

---

## 8. Persistence

**Recommendation: keep the current event identity; version inside the payload.**

```text
submission_audit_event.details_json   <- holds the v1 OR v2 payload
event_kind = llm_precheck_recorded     (unchanged)
actor_kind = llm                       (unchanged)
```

Rationale for not minting a new event kind now:

- `event_is_machine_review()` already keys on `(llm_precheck_recorded, llm)`;
  reusing it means no change to event detection, the inspection projection, or
  the queue.
- A new `SubmissionAuditEventKind` value is an enum addition → an Alembic
  migration on a deployed enum, which this design-only slice explicitly avoids.
- The payload-level `schema_version` already disambiguates v1 vs v2, so the
  event kind carries no additional information a new kind would provide.

A future rename (e.g. `machine_review_recorded`) is possible once machine review
is a first-class concept distinct from "precheck", but it is **out of scope**
here and would be its own migration + adapter update.

---

## 9. Examples

### 9.1 v1 (legacy, still valid — no marker)

```json
{
  "label": "warning",
  "summary": "One advisory warning on a kinetics record.",
  "findings": [
    {
      "severity": "warning",
      "category": "kinetics",
      "record_type": "kinetics",
      "record_id": 9001,
      "message": "Note mentions tunneling but tunneling_model is null.",
      "evidence_keys": ["missing_checks.tunneling_model"]
    }
  ],
  "model": "fake_test/simple-v1",
  "used_rag": false,
  "provider": "FakeLLMPrecheckProvider"
}
```

### 9.2 v2 — transition_state_validation critical with recommended_action

The case the v1 contract could not express:

```json
{
  "schema_version": "machine_review_v2",
  "status": "machine_screened_needs_attention",
  "curator_priority": "high",
  "summary": "A critical transition-state validation contradiction.",
  "model": "vendor/model-name",
  "provider": "VendorProvider",
  "used_rag": false,
  "findings": [
    {
      "severity": "critical",
      "category": "transition_state_validation",
      "record_type": "transition_state_entry",
      "record_ref": "9002",
      "message": "Marked validated, but the frequency set shows no single imaginary mode expected for a first-order saddle point.",
      "evidence_keys": ["ts.imaginary_frequency_count", "ts.validated"],
      "recommended_action": "Re-run the TS frequency analysis and confirm exactly one imaginary mode before validating; flag to the uploader if it cannot be reproduced."
    }
  ]
}
```

Through the v2 adapter this yields a `transition_state_entry` record summary
with `status = machine_screened_needs_attention`, `highest_severity = critical`,
a populated `recommended_action`, and (via
`build_curator_tasks_for_submission`) one `needs_curator_review` task — none of
which the v1 path could fully represent.

---

## 10. Golden examples update (when v2 is implemented)

```text
Keep all existing legacy_v1 golden fixtures + tests passing unchanged.
Add machine_review_v2 fixtures alongside them under
  backend/tests/fixtures/machine_review/ (e.g. a v2/ subfolder or *_v2.json).
At minimum add a v2 example for:
  transition_state_validation critical finding WITH recommended_action
  (the §9.2 payload), asserting the category and recommended_action survive
  end-to-end — the gap the v1 set documented.
Add an adapter-dispatch test: same logical finding expressed as v1 and as v2
  produces equivalent record summaries / tasks (modulo the v2-only fields).
```

The fingerprint contract is unaffected: `compute_finding_fingerprint` already
folds in `recommended_action` and sorts `evidence_keys`, so v2's richer findings
fingerprint stably and still exclude `source_audit_event_id` / `model` /
`provider` / timestamps.

---

## 11. Non-goals

```text
No real provider implementation.
No prompt text for cloud/local models yet.
No public trust.machine_review.
No automatic task creation on upload/precheck.
No frontend.
No migration (no new event kind; version lives in details_json).
No ARC / tckdb-client changes.
```

---

## 12. Recommended next implementation (after this spec)

```text
1. Add the v2 schemas/contracts (MachineReviewResultV2 + v2 finding) beside the
   existing machine-review schemas; reuse MachineReviewStatus / Severity /
   Category / CuratorPriority verbatim.
2. Add the adapter version dispatch (§7): marker present -> validate v2
   directly; absent -> existing v1 translate path. Unknown version -> parse
   warning.
3. Add v2 golden examples (§10); keep all v1 golden tests passing.
```

No downstream change to inspection, curator-task creation/lifecycle, or the
admin API is required — they already consume the internal `MachineReviewResult`
/ `RecordMachineReview` shapes both versions converge on.
