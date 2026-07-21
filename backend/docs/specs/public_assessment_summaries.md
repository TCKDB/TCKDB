# Public Assessment Summaries

Scientific kinetics and thermo detail/search records accept the explicit
`include=assessments` token. The compact block is designed for automated
consumers and contains two independent signals:

- `deterministic_trust`: the current code-defined evidence rubric, its
  version, evidence grade, and an explicit hard-fail reason when present.
- `reproducibility`: the latest immutable assessment's rubric, version,
  grade, timestamp, and one of `current`, `stale`, or `unassessed`.

`current` means the latest assessment uses the active rubric and its stored
context hash exactly matches a fresh evaluation. A changed rubric or evidence
snapshot is `stale`; absence of an assessment is `unassessed`.

The block is opt-in and is excluded from `include=all` because freshness
requires evaluation against current evidence. No assessment-grade search
filter is exposed: filtering stored grades without first enforcing freshness
would mix current and stale claims and give machine clients misleading
results.

Statmech and transport already expose deterministic `trust` fragments, but
assessment summaries are deferred there to avoid changing their high-impact
record builders in this slice.
