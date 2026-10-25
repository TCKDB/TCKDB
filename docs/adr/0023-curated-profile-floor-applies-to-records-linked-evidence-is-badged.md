# Curated profile: the approved floor applies to records; linked evidence is badged

**Status: accepted 2026-10-25** (owner decision). Related:
[0006](0006-exploratory-reads-by-default-with-mandatory-disclosure.md) (exploratory by default, with disclosure; **this record
amends its detail-page seam**, see Q1),
[0007](0007-curated-selections-are-a-release-overlay-not-a-column.md) (curated selections are a release overlay),
[0016](0016-review-approves-a-claim-and-does-not-cascade.md) (review approves a claim and does not cascade).
Supersedes the read-side rule known as D6 ("hidden == unknown == absent") for linked evidence. The rule is being rolled out in
steps; a site that has not been moved yet still applies D6. The helper that holds the rule (`evidence_withheld_under_profile` and
its siblings in `app/services/scientific_read/common.py`) is called, from step 2, by the response-level ref guard
(`ref_guard.py`), the selection documents, the export streams, the legacy entity reads and the ML export's conformer refs. Still on
D6: service counts and derived levels, the trust and selection loaders' own populations, made-under comparisons, and
detail-by-ref. Matching in search, browse and select moved in step 4 (filters match through linked evidence unless it is rejected
or deprecated; a filter ref that names such evidence matches nothing).

Under the `curated` profile, the `approved` floor decides which **records** a list, search or select returns. The **linked
evidence** of a returned record is always shown with its review badge. Only `rejected` and `deprecated` evidence is withheld.

## Context

D6 made the floor apply twice. A record below `approved` was absent from a list, and it was also absent wherever an approved
record linked to it: a source calculation, a level of theory derived from calculations, a conformer observation inside a group, an
evidence count, a nested ref. The two were treated as one thing, "hidden equals unknown equals absent".

That made approved records look incomplete. An approved thermo whose single-point calculation had not been reviewed showed no
single-point energy, as if none existed. Review approves a claim and does not cascade (ADR 0016), so almost every approved
record has unreviewed evidence under it. D6 turned that normal state into apparent missing data, and made a curated read
disagree with the exploratory read of the same record about what the record rests on.

## Decision

There are two rules, and they are different.

- **Record floor.** Under `curated`, a list, search or select returns only records at or above `approved`. Unchanged.
- **Evidence rule.** Under `curated`, a returned record's own linked evidence is shown with its badge (`not_reviewed`,
  `under_review`, `approved`). Evidence at `rejected` or `deprecated` is withheld, with the existing "absent" semantics. A
  missing review row reads as `not_reviewed`, so it is visible.

Put the other way round: `curated` withholds evidence for the same statuses the default profile hides by default (`rejected`,
`deprecated`). Unlike the default, whose exclusions a request lifts with `include_rejected` / `include_deprecated`, the evidence
rule has no lift switch. Under `exploratory` nothing is withheld as evidence.

Three things are assigned to three rules, so none is mixed up:

- **Record floor** (`approved`): lists, searches and selects.
- **Detail-by-ref** under `curated`: the withheld set (`rejected`, `deprecated`) from step 7 onward. This amends ADR 0006's
  second seam (`resolve_path_handle`, which reaches `_hidden_by_floor` and `status_hidden_under_profile` in `handles.py`).
  Until step 7 the seam still applies the floor.
- **Evidence rule**: a returned record's linked evidence.

The boundary between a record and its evidence, as approved by the owner:

- **Q1** Under `curated`, a detail page serves any record that is not `rejected` or `deprecated`, badged. Lists stay approved-only.
- **Q2** Conformer observations inside a group are evidence. Observation search stays approved-only.
- **Q3** Transition-state entries are records and still gate their own evidence in select. An entry's parent transition state is
  context, shown badged.
- **Q4** Structure-select basins are records. The calculations inside them are evidence.
- **Q5** Network solves are records. A supersession successor is shown badged.
- **Q6** The transition-state trust badge follows the evidence rule.
- **Q7** Dataset releases are unchanged. ML-export energy rows keep their calculations approved-only.
- **Q8** `curated` will check made-under inputs (step 6): curated responses compare a stamp's inputs as exploratory does.
- **Q9** Entry lists follow the 2026-10-24 decision (a list filter). An entry embedded in another record, such as a `/full`
  participant, is shown badged.

Unchanged: species and reaction entries are scoping parents and never gate. A read never borrows provenance from a record it
does not link to. TCKDB ships no built-in method ranking.

## Consequences

- Matching under `curated` may go through linked evidence that is not reviewed. It never goes through `rejected` or
  `deprecated` evidence.
- Under `exploratory`, matching still goes through rejected evidence, because exploratory withholds nothing as evidence. The
  asymmetry is by design: `exploratory` withholds records only (its default exclusions, which a request can lift), while
  `curated` withholds at two levels, records by the floor and evidence by the withheld set (which a request cannot lift).
- A curated reader sees what an approved record rests on, and the badge says how much of it anyone has checked.
- Counts of evidence under `curated` include unreviewed evidence and exclude rejected and deprecated evidence.

## Version effects

Step 2 (nested refs in responses, selection documents and exports) moved the selection manifests to format 4:

- The thermo, kinetics, statmech, structure, network and relative-energy (with its comparison) manifests are format 4 and record
  `linked_evidence_withheld` (`["deprecated", "rejected"]` under `curated`, `[]` otherwise) in `read_profile` (`visibility` for the
  network manifest). A manifest in an earlier format is refused, not read as the new rule.
- The `computed_transition_state` rubric is re-pinned without a version bump. The grading code is unchanged; only the inputs a
  curated read supplies to it change. The `computed_kinetics` pin follows when its inputs move (step 5).
