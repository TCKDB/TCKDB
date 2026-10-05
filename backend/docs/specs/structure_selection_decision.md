# Structure selection: decision, manifest and replay

Status: service layer (chunk 3 of the calculation, conformer and transition-state ordering work). No HTTP route serves
it yet, nothing is persisted, and no literature rule is active.

This note says what a decision is and what it is not. It sits on top of the assessment
(`app.services.structure_selection.assessment`), which says whether one unit can supply a requested energy and
structural claim. The decision never re-judges a unit; it compares assessed units.

## What a decision answers

| Intent | Question |
| --- | --- |
| `recorded_minimum` (calculation grain) | Which known, eligible recorded value is lowest within one justified cohort? |
| `validated_minimum` / `validated_saddle` | Which validated, completely assessed basin or saddle is lowest within one cohort? |
| `qualify_evidence` | Which determinations support the stated structural claim? (No number.) |
| `protocol_preferred` | Which whole protocol do audited rules prefer, for a stated objective? |

Outcomes: `no_candidates`, `energy_unavailable`, `unresolved_comparability`, `no_applicable_candidate`,
`recorded_minimum`, `representative_minimum`, `qualified_evidence`, `validated_corpus_minimum`, `policy_preferred`,
`sole_eligible_candidate`, `incomparable_alternatives`, `policy_conflict`, `evidence_conflict` (13). A negative scientific outcome is an answer, not an
error. Coverage is always the caller's authorized population: even a complete claim is not a global-search or
exhaustive-conformational-search certificate.

## Cohorts and numerical order

A cohort is the unit of numerical comparison: the same established actual recipe (the normaliser's cohort key), the same
quantity and energy convention, and the same scope family (an unconverged optimisation's intermediate-geometry value is
its own cohort). Units without an established cohort are reported and never ordered.

- Values compare **exactly**. Ties keep every tied reference. There is no tolerance and no chain of "near" values.
- Several cohorts each return a conditional minimum, and the outcome is `incomparable_alternatives`: total energies of
  different protocols are not on one scale, so a lower absolute energy never names a method winner. This holds whenever
  more than one cohort is established, **including when one of them cannot be ordered** (a disagreeing repeat, say):
  the cohort minima that exist are listed, the unordered cohort is listed with its reason, and a single cohort is never
  named the winner because another could not be ordered. Added uncertainty never makes a stronger claim. Units whose
  recipe is not established belong to no cohort; a single established cohort may still return its minimum, and the
  result says it is conditional on that cohort alone and names the units it did not compare.
- `known_values` orders what is known, conditional on it. `all_requested_members` needs every requested member eligible
  and in one cohort, otherwise `unresolved_comparability` (or `evidence_conflict` when a contradiction is the reason).

## Repeats and contradictions

Determinations of one target (one basin observation, one geometry, one saddle geometry) are alternates, not independent
confirmation. Nothing infers that differently keyed determinations are repeats.

- Alternates that agree exactly are all retained.
- Alternates that differ exactly leave their cohort unordered (`repeat_determinations_disagree`), unless the request names
  `administrative_representative`: the first repeat in the administrative order then stands for its target. When that
  substitution actually replaces a lower stored value the outcome is `representative_minimum` and the cohort figure is
  `representative_minimum_hartree` (the field `minimum_hartree` is absent): the observation-level representative minimum
  and the all-values minimum are different claims with different labels, and the all-values minimum is never claimed.
  Where no repeat disagrees the policy changes nothing and the ordinary outcome stands.
- A target with an eligible determination and a refuted one (curvature contradicts the claim, or an applicable finding
  invalidates it) is **contested**: nothing of it certifies. Choosing the older pass is not an answer. Adjudication of
  curvature evidence is not modelled in version 1.

## Administrative key

Review rank ascending, permitted-quality rank ascending, creation time descending, ordinal descending; `latest` is time
and ordinal descending, `earliest` time and ordinal ascending. The key (version `1`) is echoed with every decision. It
orders only within a scientific front or among exact ties; it never moves a unit across a front. `result_mode=first`
adds a labelled administrative presentation where several alternatives remain and never bypasses a conflict, incomplete
coverage or unresolved comparability.

## Protocol preference

Requires an objective (`physical_accuracy`, `expected_accuracy`, or `model_fidelity` with a pinned reference model).
Candidates are whole protocols (cohorts) that cover every requested target; no mixed-method profile is invented from each
basin's available method. Edges come only from audited rules through the shared graph kernel
(`app.services.selection_kernel`), a rule applies only under its own objective and reference model, an unknown
prerequisite makes no edge, and sole eligibility is `sole_eligible_candidate`, not superiority. A rule is active only if
it names its objective and pins the digest of its audited manifest. This release registers none.

## Manifest and replay

The manifest records the normalised request, read profile and effective review statuses, snapshot isolation, subject,
every normalised unit and source calculation (public refs and stored facts, no database id), each assessment, the
registry entries consulted and the decision. A manifest above 10 MiB is refused whole
(`structure_selection_manifest_too_large`; the limit itself is allowed): a truncated manifest would replay to something else.
`SelectionBounds.manifest_bytes` is the endpoint's, fixed server-side: a public interface must never let a caller set it above
10 MiB.

`replay_structure_assessment` recomputes every assessment from the normalised inputs and refuses on any difference from the
recorded one; it does not trust a recorded `eligible`, `applicable` or assessment flag. `replay_structure_decision` then
decides over the recomputed assessments and compares the result with the recorded decision and outcome. Replay refuses an
unknown manifest format, assessment, normaliser, decision, key, rule or finding-semantics version, a failed checksum, a
review status or profile the recorded request could not have seen (every recorded calculation and determination is checked, not
only the units, so a manifest relabelled curated while its sources are not approved is refused; duplicate or missing assessments
are refused; the recorded visible-unit count must be the number of recorded units; the other population counts are recorded and
not proven), and a rule that is unavailable, changed status or
rests on a different audited manifest.

**The digests are checksums, not signatures.** There is one per normalised input and one over the whole manifest. They
detect an accidental or careless edit; anyone who edits a manifest can recompute them. Replay therefore reproduces
reasoning from captured inputs. It does not authenticate scientific truth, and it cannot prove that a forged population was
the complete one. Verifying a manifest against current server-held content and authorization is a separate step that no
replay performs. A historic replay keeps the statuses captured in the manifest: a later withdrawal, supersession or rule
deactivation changes a new live decision, never the historical artifact.
