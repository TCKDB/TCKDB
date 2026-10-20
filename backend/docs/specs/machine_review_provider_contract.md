# Machine-Review Provider Output Contract

**Status:** the contract the audit adapter reads. The payload model is
`MachineReviewProviderResult` in `app/services/machine_review/schemas.py`; the
adapter is `app/services/machine_review/audit_adapter.py`. There is one
contract: no earlier payload shape is read.
**Scope:** the shape of what a machine-review provider writes into
`submission_audit_event.details_json`, and how the adapter accepts it. No
public scientific read change. No automatic task creation. No RAG. No ARC /
`tckdb-client` change.
**Audience:** TCKDB backend maintainers, and whoever wires a real (cloud/local)
provider.

**Related specs:**

- `optional_llm_precheck.md` - the advisory submission-level plumbing around
  the same audit event kind.
- `provisional_machine_review.md` - the machine-review vocabulary
  (`MachineReviewStatus` / `MachineReviewSeverity` / `MachineReviewCategory` /
  `CuratorPriority`) the payload speaks natively.
- `machine_review_golden_examples.md` - the golden fake-provider examples.
- `admin_machine_review_curator_task_api.md` - the admin workflow over the tasks
  that findings feed.
- `machine_review_handoff.md` - the workstream checkpoint (decision log, next
  options).
- `machine_review_real_provider_plumbing.md` - the *producer* design (Off /
  Cloud / Local provider plumbing) that emits these payloads.

---

## 1. Shape of the contract

A provider writes the machine-review vocabulary **natively**: the record-level
`status` is a `MachineReviewStatus` directly, with no label-to-status
translation anywhere in the stack. The payload carries `curator_priority`,
per-finding `recommended_action`, the full 11-value category set (including
`transition_state_validation` and `schema_gap`), and a first-class `provider`
field.

---

## 2. Format marker

The payload root carries a required `format` object naming the contract and its
integer version, in the same `{name, version}` shape every TCKDB-owned
versioned thing uses:

```json
{ "format": { "name": "machine_review", "version": 1 } }
```

- The name and the version are separate fields; the version is an integer, never
  glued onto the name.
- The marker is **required**. A payload without it, or whose `format` is not one
  this release holds, is refused: no result and a parse warning. Nothing is
  guessed about the shape of an unknown format.
- `MACHINE_REVIEW_FORMAT` (a `VersionRef`) is the current value; the adapter
  reads payloads through `PAYLOAD_FORMATS`, a registry keyed by `(name, version)`.
  There are no replay-only legacy formats.
- `extra="forbid"` on the model means the marker is a declared field, not stray
  data.

---

## 3. Result shape (`MachineReviewProviderResult`)

The shape mirrors the internal `MachineReviewResult`
(`app/services/machine_review/schemas.py`) plus the format marker and a
first-class `provider` field:

```json
{
  "format": {"name": "machine_review", "version": 1},
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
| `format` | `{name, version}` | required marker (section 2) |
| `status` | `MachineReviewStatus` | `not_run` / `machine_screened_pass` / `machine_screened_warning` / `machine_screened_needs_attention` / `machine_review_failed` (`machine_screened_blocking_concern` stays reserved) |
| `curator_priority` | `CuratorPriority` \| null | `low` / `medium` / `high`; advisory ordering hint only |
| `summary` | str \| null | ≤ 2000 chars |
| `model` | str \| null | ≤ 128 chars |
| `provider` | str \| null | ≤ 128 chars; part of the payload |
| `used_rag` | `Literal[false]` | RAG is a non-goal; any truthy value fails validation (section 5) |
| `findings` | list | ≤ 50; see section 3.1 |

`model_config = extra="forbid", frozen=True` - same as `MachineReviewResult`. No
mutation-payload field exists by design (section 5).

### 3.1 Finding shape

Maps 1:1 onto the existing `MachineReviewFinding`, with explicit record
addressing:

| Field | Type | Notes |
|---|---|---|
| `severity` | `MachineReviewSeverity` | `info` / `warning` / `critical` |
| `category` | `MachineReviewCategory` | full set (section 4) |
| `record_type` | str \| null | a `SubmissionRecordType` token; null ⇒ submission-scoped |
| `record_ref` | str \| null | the mapping key (public ref or stringified internal id, per the private-context rules in `audit_adapter.py`) |
| `record_id` | int \| null | optional internal-id alias; when present and `record_ref` absent, the adapter derives `record_ref = str(record_id)`  |
| `message` | str | 1–1000 chars |
| `evidence_keys` | list[str] | ≤ 20; order-insensitive for fingerprinting |
| `recommended_action` | str \| null | ≤ 1000 chars; advisory free text for a curator, **never executed** |

Addressing rule: a finding maps only to the *exact* `(record_type, record_ref)`
linked to the submission — the anti-fan-out policy in
`provisional_machine_review.md` §6/§13 and `mapping.py` is unchanged. The contract does
**not** relax mapping; it only enriches what a finding can say.

---

## 4. Category vocabulary

The contract uses the full `MachineReviewCategory` set (already in
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
`schema_gap` are first-class categories.

---


## 5. Provider constraints

```text
used_rag MUST be false for now (Literal[false]; any other value -> validation
  failure -> machine_review_failed / parse warning, never an upload failure).
Provider output is UNTRUSTED and MUST be schema-validated before any use.
Malformed / non-projectable output degrades to a parse warning and
  status machine_review_failed - it NEVER raises and NEVER fails the upload.
Provider-supplied mutation payloads are FORBIDDEN: extra="forbid" guarantees
  no field beyond the contract can be smuggled through.
A provider CANNOT approve / reject / certify / hide a record. recommended_action
  is advisory text for a human; it is never executed. Machine review writes
  nothing authoritative (no RecordReviewStatus, submission.status, is_certified,
  benchmark_reference, evidence, or public trust).
```

---

## 6. Adapter behavior

The audit adapter (`machine_review_result_from_audit_event` in
`audit_adapter.py`) reads the payload through a registry keyed by
`(format name, format version)`:

```text
read details_json (untrusted)
if not a dict                              -> parse warning, result=None
elif no format marker, or a (name, version) the registry does not hold
                                           -> parse warning, result=None
else:
    validate as MachineReviewProviderResult
    on ValidationError                     -> parse warning, result=None
```

Notes:

- Dispatch is purely on the marker, and it is strict: a missing marker or an
  unknown format is refused with a parse warning, never read as the current one.
- The validated payload converges on the internal `MachineReviewResult` ->
  `map_findings_to_submission_records` -> `RecordMachineReview` ->
  `MachineReviewRecordSummary` pipeline.
- The payload's `provider` field is the provider the adapter reports.
- Everything downstream (`inspection`, `build_curator_tasks_for_submission`,
  the curator task queue API) consumes `MachineReviewResult` /
  `RecordMachineReview`.

---

## 7. Persistence

The event identity is fixed; the format lives inside the payload:

```text
submission_audit_event.details_json   <- holds the payload
event_kind = llm_precheck_recorded     (the machine-review event kind)
actor_kind = llm
```

- `event_is_machine_review()` keys on `(llm_precheck_recorded, llm)`.
- The payload-level `format` marker identifies the contract, so the event kind
  carries no additional version information.
- A future rename of the event kind (e.g. `machine_review_recorded`) would be its
  own enum migration plus an adapter update; it is out of scope here.

---

## 8. Example

A `transition_state_validation` critical finding with a `recommended_action`:

```json
{
  "format": {"name": "machine_review", "version": 1},
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

Through the adapter this yields a `transition_state_entry` record summary
with `status = machine_screened_needs_attention`, `highest_severity = critical`,
a populated `recommended_action`, and (via
`build_curator_tasks_for_submission`) one `needs_curator_review` task.

The fingerprint contract: `compute_finding_fingerprint` folds in
`recommended_action` and sorts `evidence_keys`, so findings fingerprint stably and
exclude `source_audit_event_id` / `model` / `provider` / timestamps.

---

## 9. Non-goals

```text
No real provider implementation here.
No public trust.machine_review.
No automatic task creation on upload/precheck.
No frontend.
No ARC / tckdb-client changes.
```
