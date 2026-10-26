# Curated profile: the approved floor applies to records; linked evidence is badged

**Status: accepted 2026-10-25** (owner decision). Related:
[0006](0006-exploratory-reads-by-default-with-mandatory-disclosure.md) (exploratory by default, with disclosure; **this record
amends its detail-page seam**, see Q1),
[0007](0007-curated-selections-are-a-release-overlay-not-a-column.md) (curated selections are a release overlay),
[0016](0016-review-approves-a-claim-and-does-not-cascade.md) (review approves a claim and does not cascade).
Supersedes the read-side rule known as D6 ("hidden == unknown == absent") for linked evidence. The rule is being rolled out in
steps; a site that has not been moved yet still applies D6. The helper that holds the rule (`evidence_withheld_under_profile` and
its siblings in `app/services/scientific_read/common.py`) is called, from step 2, by the response-level ref guard
(`ref_guard.py`), the selection documents, the export streams, the legacy entity reads and the ML export's conformer refs, and, from
step 3, by the service counts, flags and derived levels of a returned record, and, from step 5, by the trust evaluators and the
selection loaders' own evidence populations. Still on D6: made-under comparisons, and detail-by-ref. Matching in search, browse and select moved in step 4 (filters match through
linked evidence unless it is rejected or deprecated; a filter ref that names such evidence matches nothing).

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
  curated read supplies to it change. The `computed_kinetics` pin followed when its inputs moved (step 5).
- Step 3 (counts, flags and derived levels of a returned record) changed no manifest content and no rubric. The selection loaders
  and the trust evaluators do not read those summaries, so the format-4 `linked_evidence_withheld` field already describes the
  rule a recorded manifest was made under, and `generate_rubric_pins.py --check` stays clean without a re-pin.
- Step 5 (the trust evaluators and the selection loaders' own evidence populations) moved two manifests to format 5: the
  structure manifest and the relative-energy manifest (single and comparison). A basin determination used to record one combined
  `review_status`, the weaker of its conformer observation's and that observation's group's. The group is the record that gates the
  basin (Q4) and keeps the record floor; the observation is evidence (Q2) and follows the evidence rule; one combined status cannot
  tell a `not_reviewed` that came from the group from one that came from the observation. Format 5 records
  `observation_review_status` and `group_review_status` apart beside the combined status, and a replay judges the group by the
  record floor, the observation by the evidence rule, and the combined status as the weaker of the two. Formats 4 and earlier are
  refused. The thermo, kinetics, statmech and network manifests keep their formats: they record each input's own `review_status`.
  What every replay accepts moved: it judges a source calculation, a lineage parent, a declared member and a basin's conformer
  observation by the evidence rule (every status but `rejected` and `deprecated`, over the caller's own floor), computed from the
  recorded request alone, and still judges the records themselves (the candidates, the compared groups, the owning
  transition-state entry) by the record floor; a recorded `rejected` or `deprecated` input is still refused. Manifests made in
  steps 2 to 4 captured only approved inputs, which the new check still accepts, so a replay of one gives no silent wrong verdict;
  the `linked_evidence_withheld` field they carry names the rule at the time and does not by itself tell step 4 from step 5. The `computed_kinetics` and `computed_transition_state` rubrics are re-pinned without a
  version bump: the checks and grading are unchanged, only the calculations a curated read hands them change (an unreviewed one is
  read), so stored and exploratory verdicts are unchanged and curated read-time verdicts may change. The ML export's energy rows
  keep their calculations approved-only (Q7); that is a named function, `ml_dataset.ml_energy_row_floor`, with its own test.
