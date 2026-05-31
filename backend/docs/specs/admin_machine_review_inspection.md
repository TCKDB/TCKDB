# Admin Machine-Review Inspection Endpoint

**Status:** implemented — admin-only debugging endpoint; no public read-API
change, no persistence, no migration.
**Date:** 2026-05-31
**Scope:** TCKDB backend only. Admin-only read endpoint over existing
submission audit events. No real LLM provider. No RAG. No upload-workflow
wiring. No public `trust.machine_review`. No `record_machine_review`
persistence. No `MachineReviewStatus` DB enum. No migration. No ARC or
`tckdb-client` change.
**Audience:** TCKDB backend maintainers and deployment admins debugging how the
optional AI Review Assistant (LLM precheck) projects onto records, before any
product decision to expose machine review publicly.

**Related specs:**

- `provisional_machine_review.md` — the future public-facing machine-review
  layer this endpoint is a *private debugging view* over.
- `optional_llm_precheck.md` — the advisory LLM-precheck plumbing that produces
  the `submission_audit_event` rows this endpoint reads.
- `automated_trust_layer.md` — the deterministic evidence engine whose output
  this endpoint must never perturb.
- `ai_review_assistant_admin_consumption.md` — the existing admin read surfaces
  for precheck events.

---

## 1. What this endpoint is

```http
GET /api/v1/admin/submissions/{submission_id}/machine-review-inspection
```

It answers one maintainer question:

> For this submission, how would its existing machine-review/precheck audit
> events project onto the records it is linked to?

It is:

- **admin-only** — gated by `require_admin`; curators and normal users get
  `403`, anonymous callers get `401`.
- **read-only** — served from a read session (`get_db`); it writes nothing.
- **debugging/inspection-oriented** — a diagnostic surface, not a workflow.
- **submission-scoped** — keyed by `submission_id`, not by a record identity.
- **derived** — computed on the fly from `submission_audit_event` rows and
  `submission_record_link` rows for that submission; nothing is persisted.

It is explicitly **not**:

- public scientific trust (it does not touch `trust.machine_review` or the
  public `TrustFragment`),
- human review (curation/endorsement),
- certification,
- moderation (approve / reject / hide).

### Why submission-scoped

The current source of truth is submission-scoped: a precheck runs against a
submission and is persisted as a
`submission_audit_event(event_kind=llm_precheck_recorded, actor_kind=llm)`,
while the records it touches are reachable through `submission_record_link`.
Keying the endpoint by `submission_id` mirrors that persistence model exactly
and avoids any ambiguous record-identity resolution. A record-centered admin
endpoint can come later, once public/private record refs are settled.

---

## 2. Mental model

Keep these four concepts distinct — the endpoint sits squarely in the third
row and must never be mistaken for the first, second, or fourth:

```text
llm_precheck             = internal/admin submission-scoped advisory mechanism
machine_review           = future public-facing provisional record-level concept
admin inspection endpoint = private debugging view over how llm_precheck could
                            map to records
human review             = authoritative curation / certification
```

The inspection endpoint reuses the private machine-review stack end-to-end and
introduces **no new policy**:

```text
submission_audit_event.details_json
  -> audit_adapter        (parse untrusted payload + safe submission->record mapping)
  -> RecordMachineReview  (record-scoped projection, never persisted)
  -> read model           (latest-per-record selection + tie-break)
  -> MachineReviewRecordSummary
  -> admin inspection response
```

---

## 3. Response semantics

The response is its own admin-only schema
(`AdminSubmissionMachineReviewInspectionResponse`), intentionally distinct from
the public `TrustFragment`. Both it and its nested record schema use
`extra="forbid"`, so no provider-supplied or mutation field can be smuggled
through.

Top level:

| Field | Meaning |
|---|---|
| `submission_id` | The inspected submission. |
| `record_summaries` | One entry per linked record that received **at least one exact-mapped** machine-review finding. Records with no mapped finding do not appear (their machine-review state is `not_run` by absence). |
| `unmapped_findings_count` | Count of findings that did **not** map to a record (submission-scoped, missing-ref, unknown-type, or unlinked). Diagnostics only. |
| `mapping_warnings` | Human-readable warnings for the *defect* unmapped cases (unknown record type, missing ref, unlinked record). Submission-scoped findings are expected and produce **no** warning. |
| `parse_warnings` | One per audit event whose `details_json` was malformed or could not be projected onto the machine-review contract. |
| `source_audit_event_ids` | The sorted, distinct ids of the machine-review audit events that were inspected — the provenance of this projection. |

Each entry in `record_summaries`
(`AdminMachineReviewRecordInspection`):

| Field | Meaning |
|---|---|
| `record_type` | The record's controlled `SubmissionRecordType` value, e.g. `kinetics`. |
| `record_ref` | The mapping key used by the stack — the stringified internal record id. |
| `record_id` | The internal record id (passthrough; admin-only). |
| `latest_summary` | The latest machine-review summary for *this record* (see below). |
| `all_record_reviews_count` | How many machine-review passes mapped to this record across the submission's events. |

The `latest_summary` (`MachineReviewRecordSummary`) carries the read model's
single selected latest review for the record:

| Field | Meaning |
|---|---|
| `status` | Record-level machine-review status (`not_run`, `machine_screened_pass`, `machine_screened_warning`, `machine_screened_needs_attention`, `machine_review_failed`). |
| `curator_priority` | Advisory queue-ordering hint (`low`/`medium`/`high`) or `null`. Currently always `null` — the precheck payload carries no priority, so nothing populates it yet. |
| `findings_count` | Number of findings on the selected review. |
| `highest_severity` | Highest finding severity present (`info`/`warning`/`critical`) or `null`. |
| `model` | Model string from the precheck result, if recorded. |
| `provider` | Provider name from the audit event, if recorded. |
| `reviewed_at` | The selected event's `created_at`. |
| `submission_id` | The submission whose event produced the selected review. |

Key behaviors to internalize:

- `record_summaries` only include **exact mapped** record findings.
- Submission-scoped findings **do not fan out** to any record.
- Unlinked findings remain **diagnostics only** (`unmapped_findings_count` /
  `mapping_warnings`), never a record summary.
- `parse_warnings` indicate malformed/unusable audit payloads — a reviewer/data
  problem, never a record problem.
- `source_audit_event_ids` identify which audit events contributed to the
  inspection.

---

## 4. Anti-fan-out policy

This endpoint preserves the mapping policy defined in
`provisional_machine_review.md` §6/§13 verbatim — it does not relax it:

```text
A submission-level finding does not apply to every record in the submission.
A finding maps to a record only when it explicitly identifies that record and
  the record is linked to the same submission.
Sibling record findings do not affect each other.
```

Concretely: a finding becomes a record-level summary only when it names an
exact `(record_type, record_id)` **and** that record is linked to the
submission. A submission-scoped finding (no record identity) is never promoted.
A finding naming a record that is not linked to the submission is refused and
kept as a diagnostic. Each record's `latest_summary` is derived from *only that
record's* mapped findings — a sibling record's findings, even if newer or more
severe, can never alter it. Record links are scoped per submission so a finding
from one submission cannot map to a record linked only via a different
submission.

---

## 5. Non-interference policy

The endpoint is a pure read. It must not — and structurally does not — mutate:

```text
submission.status
review_status
benchmark_reference
is_certified
deterministic evidence (evidence_completeness, passed/missing/warning/
  not_applicable checks, hard_fail_reason, trust_status)
scientific records
public trust fragments
```

It serves from a read-only session and constructs read-model projections that
have no write path. The deterministic evidence/trust evaluator output is
byte-identical with or without this endpoint being called — the same invariant
proved in `test_machine_review_non_interference.py` and the trust-adapter
slice.

---

## 6. Access policy

The endpoint is **admin-only** (`require_admin`); curator access is
deliberately *not* granted. Rationale:

- It exposes raw machine-review diagnostics and LLM/precheck internals
  (unmapped findings, parse failures, provider/model strings, internal ids).
- It is intended for maintainers/debugging, not general curator workflow yet.
- Curator access can be reconsidered later, when a curated review UI/queue
  exists to present this safely rather than as raw diagnostics.

Access matrix:

| Caller | Result |
|---|---|
| Anonymous | `401` |
| Normal user | `403` |
| Curator | `403` |
| Admin | `200` |
| Admin, unknown `submission_id` | `404` |

---

## 7. Example response

A submission with one linked `kinetics` record that received one advisory
`warning` finding, plus one finding that did not map (e.g. a submission-scoped
or unlinked finding kept as a diagnostic):

```json
{
  "submission_id": 123,
  "record_summaries": [
    {
      "record_type": "kinetics",
      "record_ref": "101",
      "record_id": 101,
      "latest_summary": {
        "status": "machine_screened_warning",
        "curator_priority": null,
        "findings_count": 1,
        "highest_severity": "warning",
        "model": "fake_test/simple-v1",
        "provider": "FakeLLMPrecheckProvider",
        "reviewed_at": "2026-05-31T12:00:00",
        "submission_id": 123
      },
      "all_record_reviews_count": 1
    }
  ],
  "unmapped_findings_count": 1,
  "mapping_warnings": [],
  "parse_warnings": [],
  "source_audit_event_ids": [456]
}
```

Notes on faithfulness to the current implementation:

- `curator_priority` is shown as `null`: the precheck payload carries no
  priority today, so the projection never sets it.
- `reviewed_at` is the audit event's `created_at`, stored as a naive timestamp
  (no timezone suffix).
- A submission with no machine-review events returns
  `record_summaries: []` and empty diagnostics, with `submission_id` echoed —
  the `not_run`-by-absence case.

---

## 8. Non-goals

This endpoint does **not**:

```text
approve or reject submissions
mark records human-reviewed
certify records
set benchmark_reference
hide records
change deterministic trust
expose machine_review publicly
create record_machine_review persistence
```

It also adds no public filters and no public read-API surface. It is a private
diagnostic over data that already exists in `submission_audit_event`.

---

## 9. Future work

Possible follow-ups, each gated on a product decision (none are commitments):

- A curator-facing review **queue** that consumes these projections through a
  safe, curated UI rather than raw diagnostics.
- An admin **UI inspection panel** rendering this endpoint.
- **Record-level persistence** (`record_machine_review` table) via a new
  Alembic revision, if/when projections need to be stored rather than recomputed.
- **Public `trust.machine_review` exposure** beside deterministic evidence,
  after a product decision (the private trust-envelope adapter already proves
  the assembly shape).
- **Strict public filters** such as `review_level=human_only` or
  `include_machine_reviewed=false`, only once machine review is a public
  concept.

When any of these land, retire the relevant "future" note here and in
`provisional_machine_review.md`.
