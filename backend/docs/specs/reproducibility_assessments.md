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

- `insufficient`: the assessment completed, but at least one minimum
  `described` requirement was not met. This is not the same as unassessed.
- `described`: the target, scientific claim, conditions, and origin-appropriate
  attribution are identifiable.
- `auditable`: `described`, plus enough role-labelled provenance, calculation
  metadata, typed outputs, and integrity-verified evidence to inspect how the
  claim was produced.
- `rerunnable`: `auditable`, plus the deposited inputs, execution-affecting
  settings, upstream dependency graph, and execution identity needed to run the
  workflow again.

The grades are ordered outcomes, but the row's `passed_json`, `missing_json`, and
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

## System rubric v1

`tckdb_reproducibility:v1` is deterministic and fail-closed across the record
types supported by the assessment table. It derives every grade and check from
persisted structured evidence; an API caller supplies only the record address.
Version 1 caps every non-calculation record at `described`; product derivation
and source-role policies remain explicit missing checks until their complete
recipes are modeled. A calculation reaches `auditable` only when it has an
exact software release, level-of-theory identity, type-appropriate structured
output, and an output artifact whose bytes are reachable and pass digest and
size verification through the normal artifact read path.

Artifact reads are restricted to output logs attached directly to a
calculation being assessed. Product records and transitive parent calculations
are metadata-only in v1 and never trigger downloads. A direct output larger
than 50 MiB fails the verification check with a typed warning rather than being
read. Each assessment reads at most eight output logs and at most 50 MiB in
aggregate; further logs receive explicit count/aggregate budget statuses and
warnings. One successfully verified qualifying output remains sufficient for
the auditable artifact check. Software evidence includes both package and release identity, requires a
nonblank version/revision/build token, and fails when declared-versus-parsed
software reconciliation is `mismatch`; this is a nonconflicting declared
identity, not a verified runtime environment.

Canonical chemical identity/alignment rows use `not_applicable` source
attribution. Selected collections such as conformer groups and networks do
not: `created_by` is administrative provenance, so they remain below
`described` until explicit scientific collection provenance is modeled.

Version 1 never awards `rerunnable`. Deposited inputs, parameters, and the full
upstream calculation snapshot are recorded, but the mandatory typed execution
environment manifest is not yet supported. This explicit missing check avoids
treating software labels or filenames as environment closure and makes no
byte-exact claim. Reassessment always appends a new system-owned snapshot.

## Attribution

`assessor_kind=system` has no user. `assessor_kind=curator` requires an
`assessor_user_id`. `source_submission_id` is optional provenance for the
contribution whose evidence was assessed. Authorization remains the calling
workflow's responsibility.

## Append-only guarantee

The database rejects every `UPDATE` and `DELETE` on the assessment table via a
PostgreSQL trigger. Reassessment and correction always append. The base-table
migration downgrade removes the trigger, table, and its two enums. The later
`insufficient`-grade migration downgrade only removes that enum value and
refuses to run while any assessment uses it.
