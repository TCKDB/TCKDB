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

## Async upload jobs (`/jobs/*`)

Async uploads are wrapped in the **same** submission model, on the Option-C
"submission at enqueue" design:

1. **Enqueue** (`POST /jobs/*`, `202`): create the `upload_job`, then create a
   `submission` (`status = pending`, `source_kind = api`) with
   `submission.upload_job_id` pointing at the job, plus a `submission_created`
   audit event. The enqueue response carries `submission_id`. The contribution
   event is therefore auditable from the moment it is accepted — even if the
   worker never runs.
2. **Worker success**: the worker runs the ingestion under the job's submission
   (`ReviewPolicy(under_review, submission_id, link_records=True)`), so records
   are persisted under review, linked to the submission, and an
   `ingestion_succeeded` audit event is appended. Status stays `pending`.
3. **Worker terminal failure** (retries exhausted): in a transaction separate
   from the rolled-back persistence attempt, the worker appends an
   `ingestion_failed` audit event and sets `submission.status = failed`. No
   partial scientific records survive (the persistence transaction rolled back).
   Retryable failures leave the submission `pending` for the next attempt.

Async jobs are **never** auto-reviewed or auto-approved.

## Artifact links

Uploaded calculation artifacts are linked to the submission as evidence:
`submission_record_link` rows with `record_type = artifact`, `role = "artifact"`.
This is derived centrally in `apply_review_policy` from the linked `calculation`
targets, so it applies uniformly to every upload path (sync and async).

Artifacts are evidence, **not** reviewable scientific results: they receive a
record link but **never** a `record_review` row.

**Geometry is intentionally not linked.** Geometries are content-addressed and
deduplicated — one row is reused across many uploads — so linking a geometry to
a submission would falsely imply the submission owns or produced it. If geometry
provenance per upload is ever needed, it should be expressed through the
calculation's input/output geometry attachments (which carry roles like
`final`), not through `submission_record_link`.

## Wiring

- Routes: `app/api/routes/uploads.py` — each handler wraps its workflow call
  with `open_upload_submission(...)` / `mark_upload_ingested(...)` and is
  decorated with `@audit_sync_upload_failure(kind)` for durable failure audit;
  helpers live in `app/services/upload_submission.py`.
- Async: `app/api/routes/jobs.py` (`open_job_submission` at enqueue) and
  `app/workers/upload_worker.py` (`review_policy_for_submission`,
  `mark_ingestion_succeeded` / `mark_ingestion_failed`).
- Policy + linking: `app/services/record_review.py` — `ReviewPolicy` carries
  `status`, `submission_id`, `link_records`; `apply_review_policy` writes the
  `record_review` rows, the `submission_record_link` rows, and the artifact
  evidence links from one target list.
- Submission lifecycle: `app/services/submission.py` — `create_submission`,
  `mark_ingestion_succeeded`, `mark_ingestion_failed`, `link_record`, curator
  approve/reject.

## Transactionality & failed ingestion

Scientific persistence is always atomic: a failed upload never leaves partial
scientific records, links, or review rows.

- **Synchronous `/uploads/*`**: the scientific transaction (`get_write_db`)
  rolls back fully on failure, so the in-band submission is discarded. To still
  answer "who attempted what, when, on which route, why did it fail", the route
  decorator records a durable failed submission in a **separate** transaction:
  `submission.status = failed` + `submission_created` + `ingestion_failed`, with
  no scientific records, links, or review rows. This best-effort audit never
  masks the original upload error. Only authenticated, request-parsed payloads
  reach this path — invalid payloads are rejected by FastAPI before the route
  body and never create a submission.
- **Async `/jobs/*`**: the submission committed at enqueue is durable; terminal
  worker failure flips it to `failed` (see above).

`SubmissionStatus.failed` is a system-set terminal state distinct from curator
`rejected` (which carries reviewer/reason invariants). `failed` is never
curator-approvable and never public.

## Idempotency

Idempotency is unchanged and route-level (header `Idempotency-Key`). A replay
returns the stored response — including the original `submission_id` — and
creates no second submission, duplicate record links, or duplicate artifact
links. Failed attempts do not store an idempotency record, so a retry re-attempts.

## What is unchanged

- Scientific products (`thermo`, `statmech`, `transport`, `kinetics`) remain
  append-only candidates with read-time selection; submissions add audit/review
  state around them but never mark a product canonical.
- Identity deduplication (species, geometry, level-of-theory, reaction, …) is
  unchanged.
- Artifact persistence and compensation are unchanged (linking is additive).
