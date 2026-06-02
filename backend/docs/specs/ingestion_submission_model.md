# Ingestion & Submission Model

Status: current. Supersedes the earlier "direct uploads bypass the submission
tables" behavior.

## Principle

**All accepted uploads are reviewable submissions.** A `submission` is the
audit wrapper for an upload event — the contribution that produced or affected
scientific records — not only a website-hosted manual contribution.

Direct computed uploads and hosted contribution bundles differ by **payload
shape**, not by whether they are reviewable. Both create the same audit and
review scaffolding around the scientific records they persist.

**Successful ingestion is not scientific approval.** A submission whose
records persisted sits at `submission.status = pending` with its records at
`record_review.status = under_review`; curator approval is a separate, later
step (`POST /submissions/{id}/approve`).

## What every accepted upload does

For each successful `POST /uploads/*` (and for `POST /bundles/submit`):

1. create a `submission` (`status = pending`, `source_kind = api`,
   `submission_kind` reflecting the upload kind),
2. append a `submission_created` audit event, and a `ingestion_succeeded`
   audit event once the records persist,
3. persist the scientific records through the existing per-family workflow,
4. create `submission_record_link` rows for the created/affected records,
5. initialise each created record's `record_review` row at `under_review`
   with `submission_id` set.

| | Direct `/uploads/*` | Hosted `/bundles/submit` |
|---|---|---|
| `submission` | yes (`pending`) | yes (`pending`) |
| `submission_audit_event` | yes (`submission_created`, `ingestion_succeeded`) | yes |
| `submission_record_link` | yes — the workflow's full record set | yes — curated product + parent set |
| `record_review` | `under_review`, `submission_id` set | `under_review`, `submission_id` set |
| differs by | payload shape (single computed bundle, product, conformer, …) | payload shape (multi-record contribution bundle) |

The two paths use the same `ReviewPolicy(status=under_review, submission_id=…)`
seam. Direct uploads additionally set `link_records=True` so the workflow's full
review-target set is linked; the bundle path keeps its own curated, role-bearing
`submission_record_link` rows.

## Wiring

- Routes: `app/api/routes/uploads.py` — each handler wraps its workflow call
  with `open_upload_submission(...)` / `mark_upload_ingested(...)` from
  `app/services/upload_submission.py`.
- Policy + linking: `app/services/record_review.py` — `ReviewPolicy` carries
  `status`, `submission_id`, `link_records`; `apply_review_policy` writes the
  `record_review` rows and (when `link_records`) the `submission_record_link`
  rows from one target list.
- Submission lifecycle: `app/services/submission.py` — `create_submission`,
  `mark_ingestion_succeeded`, `link_record`, curator approve/reject.

## Transactionality

The submission, scientific records, links, audit events, and review rows commit
or roll back together. On the synchronous route path, `get_write_db` rolls the
whole transaction back if the workflow raises, so a failed upload leaves **no
orphan submission**. `ingestion_failed` is therefore reserved for a future
async/two-phase path that can commit a failure record independently of the
records it was importing.

## Idempotency

Idempotency is unchanged and route-level (header `Idempotency-Key`). A replay
returns the stored response — including the original `submission_id` — and
creates no second submission or duplicate links.

## What is unchanged

- Scientific products (`thermo`, `statmech`, `transport`, `kinetics`) remain
  append-only candidates with read-time selection; submissions add audit/review
  state around them but never mark a product canonical.
- Identity deduplication (species, geometry, level-of-theory, reaction, …) is
  unchanged.
- Artifact persistence and compensation are unchanged.
- The async `/jobs/*` worker path is **not** yet wrapped in submissions
  (`computed_species`/`statmech` are not async-enqueueable anyway); wrapping it
  is a follow-up that should reuse `open_upload_submission`.
