# Reproducibility assessments

`record_reproducibility_assessment` stores an explicit, versioned assessor
claim about how reproducible one scientific record is from its deposited
evidence. The stored grade is not an independently verified guarantee: the
named system or curator applies the identified rubric and owns the claim.

This is a separate curation axis from:

- `record_review`: human acceptance of the scientific record;
- deterministic trust/evidence badges: read-time evidence quality; and
- `record_machine_review`: advisory machine-review findings.

None implies another. An approved record can still lack rerun inputs, while an
unapproved record can have a complete execution package.

## Grades

- `described`: the target, scientific claim, conditions, and origin-appropriate
  attribution are identifiable.
- `auditable`: `described`, plus enough role-labelled provenance, calculation
  metadata, typed outputs, and integrity-verified evidence to inspect how the
  claim was produced.
- `rerunnable`: `auditable`, plus the deposited inputs, execution-affecting
  settings, upstream dependency graph, and execution identity needed to run the
  workflow again.

The grades are ordered claims, but the row's `passed_json`, `missing_json`, and
`warnings_json` are the auditable explanation. Callers must not infer that a
grade alone proves that the rubric was applied correctly or guarantees bitwise
reproducibility.

## Versioning and currency

Each row preserves the exact evidence snapshot in mandatory `context_json`.
The append service serializes that object with sorted keys and compact JSON,
then computes its lowercase SHA-256 `context_hash`; callers cannot choose the
digest. An optional expected hash turns upstream context drift into a hard
failure. A changed evidence context or rubric appends another row. The latest row is selected by
`assessed_at DESC, id DESC`; no mutable `is_current` flag exists.

`is_reproducibility_assessment_context_current(...)` canonicalizes a
caller-supplied current evidence context and compares its digest with the
stored snapshot. Callers should use that result rather than treating “latest”
as synonymous with “current.” Assessment timestamps more than five minutes in
the future are rejected so a future-dated claim cannot pin latest-row ordering.

`append_reproducibility_assessment(...)` validates and flushes one row without
committing. `get_latest_reproducibility_assessment(...)` is the sole initial
read helper. The service does not modify science, submissions, reviews, or
trust projections.

## Attribution

`assessor_kind=system` has no user. `assessor_kind=curator` requires an
`assessor_user_id`. `source_submission_id` is optional provenance for the
contribution whose evidence was assessed. Authorization remains the calling
workflow's responsibility.

## Append-only guarantee

The database rejects every `UPDATE` and `DELETE` on the assessment table via a
PostgreSQL trigger. Reassessment and correction always append. The Alembic
downgrade removes the trigger, table, and the two enums owned by the revision;
it leaves the shared `submission_record_type` enum untouched.
