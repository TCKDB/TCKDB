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
`record_review.status = not_reviewed`; curator approval is a separate, later
step (`POST /submissions/{id}/approve`).

**And ingestion is not review, either.** Until 2026-08-24 both pathways
stamped `under_review` at deposit, which asserted that a human had the record
open when none had — all 1153 such rows on the hosted database carried a null
`reviewed_by`, a null `reviewed_at` and a null `first_approved_at`.
`under_review` now means what it says: a curator has picked the record up.
Revision `c1d8f4a25b30` moved the existing rows.

## What every accepted upload does

For each successful `POST /uploads/*` (and for `POST /bundles/submit`):

1. create a `submission` (`status = pending`, `source_kind = api`,
   `submission_kind` reflecting the upload kind),
2. append a `submission_created` audit event, and a `ingestion_succeeded`
   audit event once the records persist,
3. persist the scientific records through the existing per-family workflow,
4. create `submission_record_link` rows for the created/affected records,
5. initialise each created record's `record_review` row at `not_reviewed`
   with `submission_id` set.

| | Direct `/uploads/*` | Hosted `/bundles/submit` |
|---|---|---|
| `submission` | yes (`pending`) | yes (`pending`) |
| `submission_audit_event` | yes (`submission_created`, `ingestion_succeeded`) | yes |
| `submission_record_link` | yes — the workflow's full record set | yes — curated product + parent set |
| `record_review` | `not_reviewed`, `submission_id` set | `not_reviewed`, `submission_id` set |
| differs by | payload shape (single computed bundle, product, conformer, …) | payload shape (multi-record contribution bundle) |

The two paths use the same `ReviewPolicy(status=not_reviewed, submission_id=…)`
seam. Direct uploads additionally set `link_records=True` so the workflow's full
review-target set is linked; the bundle path keeps its own curated, role-bearing
`submission_record_link` rows.

## Citing a calculation deposited by an earlier request

A submission is normally self-contained: everything a record cites is declared
in the same payload and addressed by **local string key**, so a depositor never
needs to know a database id. Two product uploads make one bounded exception.

`POST /uploads/thermo` and `POST /uploads/statmech` accept
`source_calculations[*].existing_calculation_id` — a calculation row deposited
by a *previous* request — as an alternative to `calculation_key`. Exactly one of
the two must be given per entry.

The exception exists because **calculations are append-only and are never
deduplicated**. Without it, a client that deposits opt/freq/sp during its
conformer step and then deposits statmech has to re-send those calculations,
minting a second row for the same job. That does not merely waste space: it
destroys the meaning of counting candidates. A count of distinct calculations
supporting a species is evidence of reproducibility only while a calculation row
means *a job someone ran*; once re-deposits mint duplicates, the count silently
becomes "how many times someone re-uploaded", and the store cannot tell the two
apart. The counter-argument — a self-contained deposit is stronger for
provenance, because a record can then never cite something not reviewed
alongside it — was weighed and accepted as the smaller loss.

This is **programmatic chaining**, not the contributor UX: it is for clients
threading ids back out of a prior TCKDB upload response (ARC's adapter, replay
and repair tooling). It is not a violation of the "no FK IDs in upload schemas"
rule, which governs contributor-facing scientific content — the depositor is
quoting back an id TCKDB issued to them, not describing chemistry by row.

A chained citation is not a cheaper citation. It passes the same checks a
locally-keyed one does, in the same code, and the checks live in the resolution
service rather than the calling workflow so the two cannot drift apart:

| check | local key | `existing_calculation_id` |
|---|---|---|
| reference resolves | schema: key was declared in this payload | service: row exists, else **404** |
| owner-consistency with the target species entry | by construction, plus a defensive guard | enforced against the row, **422** |
| role/type compatibility | `assert_statmech_role_compatible` / `assert_thermo_role_matches_calculation_type` | the same function, same coded refusal |
| link uniqueness | schema, per `(key, role)` | schema, per `(key, existing_id, role)` |
| submission scoping, `record_review`, audit | applies to the statmech/thermo record created by this request | unchanged — the cited calculation is **not** re-reviewed or re-linked; it keeps the review state its own submission gave it |

The **contribution-bundle routes do not offer this field, deliberately**. A
bundle is self-contained by construction — it carries one global calc-key
namespace covering every calculation it deposits — so every citation it needs to
make is expressible as a key within the request it arrives in, and there is
nothing for chaining to reach for. The bundle paths keep the key-only
`StatmechSourceCalcIn`; because the wire base sets `extra="forbid"`, a bundle
that sends `existing_calculation_id` is refused with `extra_forbidden` rather
than silently ignored.

## Async upload jobs (`/jobs/*`)

### Support and retry contract

`/jobs/*` is an **experimental authenticated ingestion surface**, not a
public-release or Python-client feature.  It supports exactly the nine current
job kinds (`computed_reaction`, `conformer`, `reaction`, `kinetics`, `network`,
`network_pdep`, `thermo`, `transition_state`, and `transport`); statmech and
computed-species have no async endpoint by deliberate v1 scope.  This avoids a
false feature-parity claim: contributors should use the documented synchronous
upload or contribution-bundle APIs unless an operator has explicitly enabled a
worker.

Every enqueue accepts the standard optional `Idempotency-Key`; an exact retry
returns the original `202` response and creates neither a second job nor a
second submission.  Job status is visible only to its owner or a curator/admin.
Workers claim rows with a five-minute lease and heartbeat. A process killed
after claim leaves a `processing` row that a later worker atomically reclaims
after lease expiry. Each claim consumes one attempt; after `max_attempts` the
job and its submission are terminally failed. Workflow persistence and marking
the job complete share one transaction, so recovery replays only rolled-back
scientific work.

Async uploads are wrapped in the **same** submission model, on the Option-C
"submission at enqueue" design:

1. **Enqueue** (`POST /jobs/*`, `202`): create the `upload_job`, then create a
   `submission` (`status = pending`, `source_kind = api`) with
   `submission.upload_job_id` pointing at the job, plus a `submission_created`
   audit event. The enqueue response carries `submission_id`. The contribution
   event is therefore auditable from the moment it is accepted — even if the
   worker never runs.
2. **Worker success**: the worker runs the ingestion under the job's submission
   (`ReviewPolicy(not_reviewed, submission_id, link_records=True)`), so records
   are persisted awaiting review, linked to the submission, and an
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

## Licensing is not yet part of the upload contract — and must become one

A submission records **who** deposited **what**, **when**, and **how it was
produced**. It does not record **under what terms it may be republished**, and
nothing in an upload payload says.

That gap is invisible today and only today. The scientific corpus is published
under CC BY 4.0 (`LICENSE-DATA`; `dataset_release.data_license` defaults to
`CC-BY-4.0`), and every depositor on the hosted instance is the operator, who
may license his own deposits. The moment a second contributor uploads, a
release cut with that default would republish their records under a license
they never agreed to. A configuration default is not consent, and a deposit
already accepted cannot be retroactively consented to.

**The constraint, stated so a future implementer meets it before the second
contributor does:** the data license must become part of the upload contract —
declared or agreed at deposit time, recorded against the submission, and
honoured when a release selects the records — **before** a deployment accepts
deposits from anyone but its operator. Until then, the `CC-BY-4.0` default
means only "the terms the operator applies to data the operator is entitled to
license".

Deliberately not designed here. It touches the upload payloads, the submission
tables, the bundle format, and the release manifest at once, and it deserves
its own decision record rather than a field bolted onto a schema. What this
paragraph fixes is that the requirement is written down where ingestion is
specified, instead of living in the head of the person who noticed it. The
same constraint is recorded from the release side in
[`dataset_release_and_profiles.md`](dataset_release_and_profiles.md) §7b and
in `LICENSE-DATA`.

## What is unchanged

- Scientific products (`thermo`, `statmech`, `transport`, `kinetics`) remain
  append-only candidates with read-time selection; submissions add audit/review
  state around them but never mark a product canonical.
- Identity deduplication (species, geometry, level-of-theory, reaction, …) is
  unchanged.
- Artifact persistence and compensation are unchanged (linking is additive).
