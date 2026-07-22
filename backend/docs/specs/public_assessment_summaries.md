# Public Assessment Summaries

Scientific kinetics, thermo, statmech, and transport detail/search records accept the explicit
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

Statmech and transport retain their existing detail/subresource-only `trust`
fragments. Their compact assessment summary is available on detail, broad
search, and species-entry subresource responses. As on thermo and kinetics,
the summary is absent by default and `include=all` never expands it.
