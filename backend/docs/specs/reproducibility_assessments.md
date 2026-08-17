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
  settings, and upstream dependency graph needed to attempt the workflow again.
  This grades the *completeness of the deposited evidence*, not a promise of
  bitwise-identical output.

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

## System rubric

`tckdb_reproducibility:v1` is deterministic and fail-closed across the record
types supported by the assessment table. It derives every grade and check from
persisted structured evidence; an API caller supplies only the record address.

TCKDB is pre-release, so there is deliberately **one** rubric implementation
rather than a chain of versioned ones: the rubric is changed in place and the
`v1` label is retained. The `rubric_version` column exists so that stored rows
can be attributed after a release, but it is not the staleness mechanism —
freshness is decided by comparing an assessment's snapshotted `context_json`
hash against a fresh evaluation, so an evidence change is detected whether or
not the label moved. Introduce a second version only when a released consumer
depends on the old grades.

The rubric caps every non-calculation record at `described`; product derivation
and source-role policies remain explicit missing checks until their complete
recipes are modeled. A calculation reaches `auditable` only when it has an
exact software release, level-of-theory identity, type-appropriate structured
output, and an output artifact whose bytes are reachable and pass digest and
size verification through the normal artifact read path.

Artifact reads are restricted to output logs attached directly to a
calculation being assessed. Product records and transitive parent calculations
are metadata-only and never trigger downloads. A direct output larger
than 50 MiB fails the verification check with a typed warning rather than being
read. Each assessment reads at most eight output logs and at most 50 MiB in
aggregate; further logs receive explicit count/aggregate budget statuses and
warnings. One successfully verified qualifying output remains sufficient for
the auditable artifact check. Software evidence includes both package and release identity, requires a
nonblank version/revision/build token, and fails when declared-versus-parsed
software reconciliation is `mismatch`; this is a nonconflicting declared
identity, not a verified runtime environment.

### Artifact verdicts, and the two ways a read can fail to happen

Each artifact in `context_json` carries a `verification` verdict. Three of them
are about the artifact's bytes rather than about the rubric's budget, and two of
those are easy to confuse:

| Verdict | What happened | Durable consequence |
|---|---|---|
| `verified` | The bytes were read and match the persisted digest and size. | A later `verified` custody observation, if a break was on record. |
| `unavailable` | The object store did not answer. **We could not check right now.** | None. An unreachable store says nothing about the object, so it can neither create a custody break nor clear one. |
| `evidence_missing` | The store answered, and said the key a live artifact row references is not there. **The thing we would have checked is gone.** | An `object_missing` row in `artifact_integrity_event`, detected during `reproducibility_verification`. |
| `integrity_failed` | The bytes came back and were wrong. | A `digest_mismatch` / `size_mismatch` row. |

`unavailable` and `evidence_missing` both arrive as one exception,
`ArtifactStorageUnavailable`, and are told apart by its `missing` attribute —
the same discriminator the download route uses to choose between `503
artifact_storage_unavailable` and `502 artifact_object_missing`. A transient
failure and a permanent one deserve different words, and reporting both as
`unavailable` lost the one that matters.

A disappearance is a **break in custody**, and custody of stored evidence is
recorded rather than logged (ADR 0014): the sweep is one of the few things that
systematically re-reads stored artifacts, so it is the most likely place in the
system to discover such a break, and a discovery it discarded would simply be
rediscovered and discarded by the next sweep. The row is written in its own
transaction, so it survives whether or not the assessment it feeds is committed.
`evidence_missing` is deliberately a *weaker* verdict than `integrity_failed`
and not a hard failure of its own: the grade consequence is the same either way
(the artifact is not `verified`, so the auditable artifact check has no evidence
to pass on), and what differs is what an operator does next — look for where the
object went, rather than for what changed it.

For an artifact this evaluation does not read — every input, checkpoint and
Hessian file, and every artifact of an upstream dependency — the verdict is
copied from the custody record rather than invented, and cites the observation it
copied by `integrity_event_ref` (resolvable at
`GET /scientific/artifacts/{sha256}/integrity`). The copy uses the same
vocabulary, so a recorded `object_missing` is reported as `evidence_missing`
whether the sweep read the object itself or only cited the row.

### Reaching `rerunnable`

`rerunnable` is awarded only to a calculation, and only when every `auditable`
requirement passes, the deposited inputs / parameters / upstream dependency
snapshot are present, and no evidence warnings were raised — an artifact whose
bytes could not be verified can never be silently upgraded into a rerun claim.
The grade does not claim bitwise-identical output, available
licences/schedulers, or recomputability of product records without their own
recipe and source-role closure.

**The execution-environment manifest is not graded.** It is recorded under
`context_json['execution_environment']` as provenance and is deliberately
excluded from every check, for two reasons.

First, accessibility. Someone who runs `module load gaussian/16` against a site
install cannot produce a byte digest for it, and they are the common case.
Gating the top grade on data that most honest uploaders cannot obtain makes the
grade a measure of local tooling rather than of evidence quality — and a
required field that a person cannot honestly fill gets filled with a guess,
which we would then store as though it were verified.

Second, the environment is weaker evidence than what is already recorded. The
values that actually determine a number — applied energy corrections per atom
and per bond, frequency scale factors, level of theory, execution parameters —
are stored as typed rows. Those beat a pointer to an environment that could
regenerate them. For workflow-tool-derived products,
`workflow_tool_release.git_commit` pins the code state (and therefore its
declared dependency set) without asking for a lockfile.

A pinned environment moves a converged SCF energy by roughly 1e-8 Hartree via
BLAS summation order and FMA rounding — some five orders of magnitude below the
~1.6 mHartree that is chemical accuracy. It is not what makes a result
trustworthy.

Canonical chemical identity/alignment rows use `not_applicable` source
attribution. Selected collections such as conformer groups and networks do
not: `created_by` is administrative provenance, so they remain below
`described` until explicit scientific collection provenance is modeled.

A calculation with deposited inputs, parameters, and a full upstream snapshot
reaches `rerunnable` whether or not a manifest was supplied. Reassessment always
appends a new system-owned snapshot.

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
