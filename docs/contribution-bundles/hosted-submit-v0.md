# Hosted bundle submit/import v0

The hosted submit endpoint lets an authenticated user POST a
`ContributionBundleV0` and have its records imported into the hosted
database through the existing thermo/kinetics upload workflows. A
`submission` row plus matching audit and record-link rows are created so
the contribution is fully traceable.

This is milestone 7 of the
[implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md).
The previous milestone shipped read-only dry-run; this milestone is the
first **write** path for contribution bundles.

## Core policy

Submitted records become **publicly visible immediately** but remain
**unreviewed** until a curator explicitly reviews them. Validation means:

- structurally valid,
- scientifically importable under the existing backend rules,
- persisted successfully through the normal upload workflows.

Validation does **not** mean curated, approved, recommended, endorsed,
or "best available". The submit response and the moderation row both
report `unreviewed` / `pending_review` wording so this distinction is
explicit at every layer.

Curator review (accept/reject/queue/UI) is **not** in this milestone.

## Endpoint

```text
POST /api/v1/bundles/submit
```

- **Auth:** required. Either a session cookie (`tckdb_session`) or an
  API key header (`X-API-Key`) is accepted, via `get_current_user`.
  Anonymous requests are rejected with `401`. Invalid or revoked API
  keys are rejected with `401`.
- **Request body:** a `ContributionBundleV0` JSON document, validated
  by the existing bundle schema. Structurally invalid bundles fail with
  the normal `422` validation response.
- **Response:** `ContributionBundleSubmitResult` (HTTP `201`).
- **Side effects:** creates one `submission`, at least two
  `submission_audit_event` rows (`submission_created` and
  `ingestion_succeeded`), one `submission_record_link` per imported
  product row and per immediate identity parent, and the underlying
  scientific rows themselves.

## Supported bundle kinds

Submit/import v0 supports the same families as bundle format v0:

- `thermo`
- `kinetics`

Mixed bundles and other families (`network`, `statmech`, `transport`,
`computed_reaction`) are rejected by `ContributionBundleV0` validation
before they reach the submit handler. An unsupported kind that somehow
slips past validation is rejected by an explicit guard in the workflow.

## Authentication and attribution

The authenticated hosted user is the actor for every row this endpoint
creates:

- `submission.created_by` is set to the hosted user.
- Every scientific row created during the import is attributed to the
  same hosted user via the existing `created_by` plumbing on the
  thermo/kinetics workflows.

Local exporter metadata (`bundle.exporter`) is preserved as
provenance-only context. Local user IDs and labels are **never** trusted
as hosted identities. Bundle-local refs (`bundle.local_refs`) carry no
hosted weight either — they are intra-bundle pointers, not hosted
primary keys.

## Dry-run gate

Submit/import calls the same dry-run service used by `/dry-run` and
applies a **strict** gate before any write:

A bundle is rejected (HTTP `400`, `DomainError`) if the dry-run reports
any of the following:

- `bundle_valid` is `false`,
- `summary.errors > 0` (item-level or message-level errors),
- `summary.unsupported > 0` (any unsupported preview action).

Warnings are **not** blocking. Dry-run warnings are carried forward into
the submit response so the client can surface them, but the import still
commits.

## Transaction behavior

The route uses the existing `get_write_db` dependency, which wraps the
request in a single SQLAlchemy transaction:

- success → commit,
- any exception → rollback of the entire bundle, including the
  `submission`, audit events, record links, and every scientific row
  that the workflows created during the call.

There are no partial imports in v0. If the third upload of a five-upload
kinetics bundle raises mid-workflow, the first two uploads, the
submission row, and any audit/link rows are all rolled back together.

## Record linkage

Submit/import creates `submission_record_link` rows for two layers:

1. **Imported product rows** (`action = imported`):
   - `thermo` rows for thermo bundles,
   - `kinetics` rows for kinetics bundles.
2. **Immediate identity parents** (`action = linked`):
   - `species_entry` for each thermo upload,
   - `reaction_entry` for each kinetics upload.

Deeper identity/provenance rows (species, chem_reaction, literature,
software_release, workflow_tool_release) are intentionally **not**
linked in v0 — that is a follow-up once a curator UI exists to consume
the richer trail. Links are deduped per `(record_type, record_id)` so a
bundle that touches the same species_entry from multiple uploads still
produces a single link row.

## Audit events

A successful import appends two audit events:

1. `submission_created` — auto-fired by the existing
   `create_submission` helper.
2. `ingestion_succeeded` — appended after the per-family workflows
   complete, recording how many records the import wrote.

Submission status stays at `pending` throughout. Ingestion success is
explicitly **not** moderation approval; status only changes when a
curator acts.

## Submission status mapping

The existing `submission_status` enum has:

- `pending` (initial),
- `precheck_passed`,
- `auto_flagged`,
- `approved`,
- `rejected`,
- `superseded`.

Submit/import v0 always lands rows at `pending`. That is the existing
status that best means "publicly visible via read APIs that don't gate
on review, but not curator-approved" — `Submission.is_public` returns
`True` only for `approved`, so application code that already gates on
that property continues to behave correctly.

The submit response surfaces `pending` in the raw `status` field and
`unreviewed` in the human-facing `review_status` field.

## Request shape

```jsonc
POST /api/v1/bundles/submit
Content-Type: application/json
X-API-Key: tck_…           // or session cookie

{
  "bundle_format": "tckdb-contribution-bundle",
  "bundle_version": "0.1",
  "bundle_kind": "thermo",
  "created_at": "2026-04-25T00:00:00Z",
  "source_instance": { /* … */ },
  "exporter":        { /* provenance-only; not actor identity */ },
  "submission":      { "title": "…", "summary": "…", "source_kind": "local_bundle" },
  "records":         { "thermo_uploads": [ /* … */ ], "kinetics_uploads": [] },
  "local_refs":      { /* informational only */ },
  "manifest":        { /* … */ }
}
```

## Response shape

```jsonc
HTTP/1.1 201 Created
Content-Type: application/json

{
  "submission_id": 123,
  "status": "pending",
  "review_status": "unreviewed",
  "bundle_kind": "thermo",
  "summary": {
    "records_imported": 1,
    "records_linked": 1,
    "warnings": 0
  },
  "records": [
    { "record_type": "thermo",        "record_id": 456,
      "action": "imported", "review_status": "unreviewed",
      "local_ref": "thermo_uploads[0]" },
    { "record_type": "species_entry", "record_id": 789,
      "action": "linked",   "review_status": "unreviewed",
      "local_ref": "thermo_uploads[0].species_entry" }
  ],
  "messages": [
    { "level": "info", "code": "ingestion_succeeded",
      "message": "Bundle imported successfully. Records are publicly visible but unreviewed; curator review is a separate, future step." }
  ]
}
```

## Relationship to dry-run

Dry-run (`POST /api/v1/bundles/dry-run`) and submit
(`POST /api/v1/bundles/submit`) share the same input schema and the same
underlying validation service. Submit additionally:

- requires the dry-run to report no blocking errors,
- writes through the existing thermo/kinetics workflows,
- creates the submission/audit/link rows.

A client that wants to preview before committing can call `/dry-run`
first; submit also runs it internally as a gate, so the preview is not
strictly required.

## What this milestone is not

Out of scope for v0:

- curator accept/reject workflow,
- moderation UI,
- frontend bundle upload page,
- local push-to-hosted UX,
- artifact import,
- network bundle import,
- raw database synchronization,
- service accounts,
- review-status exposure on read schemas (still derived from
  `Submission.is_public`),
- duplicate scientific-product detection beyond what existing workflows
  already perform.

## Validation vs curation

This is the central policy of submit/import v0:

| State            | What it means                                                           |
|------------------|-------------------------------------------------------------------------|
| **submitted**    | Bundle was posted by an authenticated hosted user.                      |
| **imported**     | Records were persisted through the normal upload workflows.             |
| **unreviewed**   | No curator has yet evaluated the submission.                            |
| **pending_review** | Submission is queued for future curator review (this milestone exits here). |
| accepted / approved / curated | **Not** produced by this milestone.                          |
