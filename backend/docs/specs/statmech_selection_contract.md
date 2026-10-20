# Statmech selection: the contract

Status: stage 1 of the statmech selection programme. This page fixes the request, the answer vocabulary, the
bounds, what an existing record can establish, and what changes on which surface. Nothing here is wired to a route
yet; the sections say what each later stage adds.

## What the operation answers

"Which supplied statistical model supports this declared use, and is any applicable whole recipe preferred by
scoped evidence?" It returns qualification, honest alternatives and, only where an audited rule says so, a
preference with its basis. It selects existing interpretations. It never assembles a favourable combination of
energies, frequencies or rotors, and it never computes a partition function, a thermochemical value, a
population or a rate.

A statmech row is an interpretation container. A basin, an electronic-level set, an ensemble member and a TS
determination are different units. A candidate is a whole supplied model revision (stage 2 adds the declaration)
or, until one is declared, a legacy statmech record read as it is. A legacy record is never given ensemble
membership or post-processing settings it does not state.

## Request

`POST /api/v1/scientific/statmech/select` (stage 5), one typed target union. Schema:
`app/schemas/reads/scientific_statmech_selection.py`; the rules live in
`app/services/statmech_selection/models.py`.

| Field | Meaning |
| --- | --- |
| `contract_version` | integer `1` |
| `intent` | `qualify` (no comparison, no selection ref) or `protocol_preferred` (also compares whole recipes) |
| `task` | one of six supported tasks, or a recognised deferred one (below) |
| `observable` | what is qualified; implied for the three component tasks |
| `target` | `species_entry` or `transition_state_entry`, a scope, and the isotope, electronic-state and stereochemical conventions |
| `conditions` | temperature domain (K), environment, normalization (`volume_m3`, `pressure_pa`, `concentration_mol_m3`), energy reference |
| `population` | all authorized matching models, explicit model revision refs, or a pinned release. Never a page prefix |
| `coverage_requirement` | `entire_requested_domain` (a complete claim; refuses when coverage is missing) or `eligible_population` |
| `comparison_objective` | `protocol_preferred` only: `physical_accuracy`, `expected_accuracy` or `model_fidelity` (which needs a pinned `reference_model_ref`) |
| `administrative_policy`, `result_mode`, `min_review_status` | presentation within a front; a stricter record-review floor that never relaxes the read profile's |

Strict by construction: unknown fields, database ids, pagination, sort expressions, rule expressions and any
bound are refused. A misspelled token is a validation error (422). Units are fixed by field name: `cm1`,
`relative_energy_kj_mol`, kelvin, `pa`, `m3`, `mol_m3`; there are no free-text units and no non-finite numbers.

### Tasks

| Task | Observable | Scope | Needs |
| --- | --- | --- | --- |
| `rotational_component` | `rotational_characterization` | atom, basin, ts_determination | nothing; no normalization (a stated temperature domain is accepted) |
| `vibrational_component` | `vibrational_treatment` | basin, ts_determination | nothing; no normalization (a stated temperature domain is accepted) |
| `zpe_component` | `zero_point_energy_interpretation` | atom, basin, ts_determination | `energy_reference` |
| `within_basin_thermal` | `entropy`, `heat_capacity` | atom, basin | temperature domain; never an energy reference or electronic offset (a standard-state S or Cp states its pressure or concentration) |
| `canonical_model` | `internal_partition`, `full_partition` | atom, basin, explicit_equilibrium_ensemble | temperature domain; a full model also a translational normalization and `energy_reference`; an internal model never needs a pressure |
| `conventional_ts_transverse_model` | `transverse_partition` | ts_determination | TS owner, `dividing_surface`, `reaction_coordinate_excluded: true`, temperature domain |

An ensemble scope needs `ensemble_declaration_ref`, the immutable membership declaration. A filtered conformer
context is not membership and is refused. Deferred tasks (`density_of_states`, `sum_of_states`,
`higher_order_ts_model`, `prepared_population`, `trapped_population`, `thermochemical_formation`,
`numerical_evaluation`, `numerical_construction`) validate and are answered `unsupported_task`; a canonical,
RRHO or equilibrium model is never substituted. An energy domain (`energy_domain_kj_mol`) belongs to those and
must state its `energy_reference`.

## Answer vocabulary

HTTP 200 for every valid scientific outcome, including negative ones; 422 for a malformed or contradictory request
and for the bounded refusal `statmech_selection_bound_exceeded`; the existing non-disclosing 404 for a target that
is missing or hidden.

Candidate states: `applicable`, `incompatible`, `unresolved`, `unsupported`, plus independent owner/source
exclusion reasons. Applicability is not comparison coverage: a known applicable model can have unknown comparative
accuracy.

Outcomes: `qualified_models`, `no_candidates`, `no_applicable_candidate`, `unresolved_applicability`,
`missing_required_data`, `unsupported_task`, `unsupported_model`, `evidence_conflict`, `unresolved_comparability`,
`policy_conflict`, `sole_eligible_candidate`, `policy_preferred`, `incomparable_alternatives`.

Summary precedence: bounded refusal, unsupported request, no candidates, then (when none qualifies)
`evidence_conflict`, `unsupported_model`, `missing_required_data`, `unresolved_applicability`,
`no_applicable_candidate`, then the preference decision. Every per-candidate reason is kept. With qualifying
candidates and unresolved alternatives the outcome is scoped to the eligible population and a required-full-coverage
claim is not certified.

`scientific_ref`, `sole_eligible_ref` and `administrative_ref` are mutually exclusive. A representative or
administrative pick is never labelled a minimum or a preferred model. `result_mode=first` may set an attributed
administrative ref among incomparable first-front alternatives only; it cannot rescue a conflict, incomplete
coverage or a refusal, and it is suppressed when any group is unordered.

## What an existing record establishes

Implemented in `app/services/statmech_selection/legacy.py` and reported by the read-only inventory
(`backend/scripts/ops/statmech_selection_coverage_inventory.py`, which names its database explicitly and opens a
read-only repeatable-read transaction). Each record, per claim, is `represented_and_usable`,
`represented_but_ambiguous`, `absent` or, when the owner kind or scope cannot carry the claim at all,
`not_applicable` (not a data gap);
`unsupported_declaration_version` counts declared models this release cannot read and is 0 until stage 2.

- A linked scale factor (`frequency_scale_factor_id`) says which factor belongs to the record, not that the claim's
  frequencies were scaled by it: application is unknown until a declaration states it. The one existing evidence of
  application is an applied energy correction carrying a frequency scale factor on one of the record's own source
  calculations. Every such correction is an energy role (zero-point energy, entropy contribution, thermal correction),
  none of which means "the frequencies were scaled", so the purpose is read from the correction's `application_role`
  (`zpe` is a zero-point scaling, `entropy_contribution` an entropy scaling, `thermal_correction_enthalpy` an enthalpy
  scaling) and counts only when the factor's own kind agrees. A factor of another kind in that role is evidence that a
  factor was used and nothing about its purpose. A fundamental-frequency or heat-capacity scaling can never be
  evidenced by a correction and stays unknown. Purposes are not interchangeable, so the profiles are
  `within_basin_thermal.entropy` and `.heat_capacity`, each needing its own purpose.
- Curvature is discovered on any source calculation with a frequency or Hessian result, whatever its job type.
- Several distinct source geometries are undeclared alternatives, not a pick. Where an optimisation declares which
  output is its `final` geometry, an initial or intermediate output is not an alternative to it.
- An atom needs no frequency, scan or constants, and has no vibrational component (that scope is refused, so the
  record is `not_applicable` there). An imported record needs no local geometry job.
- A declared model whose geometry or curvature rests on imported literature parameters is supported by them: the role is
  met, no local job is asked for, and the advisory `imported_parameters_not_revalidated:<role>` discloses that the source's
  own stationary-point statement is not re-judged here. An imported source never overrides a local witness that contradicts
  the claim, and a source declared for one role does not stand in for another.
  An imported source meets its role only if its literature carries a DOI or an ISBN (the identifiers the literature policy
  resolves); a title-only citation is kept but, for a role the claim requires, reported as `required_source_unusable:<role>`; the advisory
  `source_literature_unidentified:<role>`. A rotor's `potential_source_key` must name a `torsional_surface` source.
  `imported_parameters_not_revalidated` is part of the decision response (chunk 4).
- A torsion-bearing record that does not say whether its frequencies were projected is ambiguous, not assumed
  projected. An invalidated rotor is ambiguous here and blocks the claim in an assessment.
- An unknown optical factor or absent electronic levels keep the thermal claims open; they are not one. A chiral
  saddle point exists, so the optical factor is also required of the transition-state claim.
- No legacy record states an energy-reference zero, a zero-point inclusion convention or a translational
  normalization. The ZPE interpretation and the full partition function are reachable only through a declared
  model.

## Bounds

500 candidates, 25,000 necessary nested rows, dependency depth 8, 10 MiB canonical manifest. Resource limits, not
scientific tolerances; counted over the complete authorized population, so exceeding one refuses the answer and
never selects from a prefix. A caller cannot set one. Basis: a candidate with a hundred-atom geometry carries about
three hundred mode rows and a few hundred scan points, so roughly seven hundred nested rows; 25,000 rows hold about
thirty-five such candidates or the full 500 small ones, and their normalised payload stays far under 10 MiB. These
figures are provisional and are revised from measured payloads when the loader lands.

## Rules

Zero active literature preference rules ship. Synthetic rules exist only in tests. A rule is inactive until an
audited manifest pinned by content says otherwise, and no council example is activated by being written down.

## Decision

Qualification is the floor. `qualify` returns the qualification summary and no selection reference. `protocol_preferred`
compares only a decided population: if the summary is anything but `qualified_models` (an unsupported request, no
candidates, nothing qualifying, or qualified models alongside undecided alternatives under a request for the entire
domain) that outcome is the answer, every selection reference is null and the qualified subset stays visible.

Preference runs on whole models that are each `applicable`, through the shared graph kernel: rule supersession is settled
before any opposition is judged; a surviving opposing pair or a cycle is a `policy_conflict` with no selection; fronts and
the administrative order come out. A rule applies only when its task, observable and objective (and, for `model_fidelity`,
its pinned reference model) are the request's, its scope contains the request and its audited coverage contains the
*entire* requested scope. An edge needs the preferred side, the yielding side and the pair's compatibility all true.

A pair for which a rule might give an edge, with a prerequisite unknown on one side (or an unknown scope or coverage), is
listed under `undecided` and the outcome is `unresolved_comparability`: nothing is selected, and an administrative
reference cannot rescue it. A refuted prerequisite is a clean "no", not undecided, and a rule whose scope is unknown but
which touches neither model leaves nothing undecided.

Precedence among preference outcomes: `policy_conflict` (an established opposition or cycle stays a conflict however much
else is undecided, unless an undecided rule declares it supersedes one of the conflicting rules), `unresolved_comparability`,
`sole_eligible_candidate`, `policy_preferred`, `incomparable_alternatives`. The selection block carries three mutually exclusive references, each with
its basis and population limit: `scientific_ref` (supported dominance over every eligible alternative), `sole_eligible_ref`
(noncomparative), `administrative_ref` (only `result_mode=first`, among incomparable first-front alternatives, never over a
conflict, an unresolved comparability or an incomplete population). Administrative key, versioned: own review rank
ascending, creation time descending, public ordinal descending; `latest` is time and ordinal descending only.

Per-candidate advisories travel with the decision: `advisories` maps each candidate to its advisory codes, and `notices`
says in words what `imported_parameters_not_revalidated:<role>` and `source_literature_unidentified:<role>` mean. Both are
part of the response contract of the decision, not only of the assessment.

Replacing a statmech record requires the same subject (species entry or transition-state entry) and, where both records
declare a model, the same declared scope: both an atom, the same basin, both an ensemble, or both the same
transition-state determination (a basin is one conformer group, a transition-state model one determination). A record that declares nothing can be replaced, and the absence is not hidden.

## Manifest and replay

The manifest carries the normalised request, profile and effective statuses, subject, population counts, every candidate's
facts and every source calculation (no database id), each assessment, the registry entries consulted and the decision.
Three offline operations: `reproduce_statmech_ordering` runs the decision over the *recorded* assessments (`reassessed:
false`); `reassess_statmech_captured_inputs`
recomputes every assessment from the captured facts and refuses any difference, never trusting a recorded flag even when
coordinated edits agree; `replay_statmech_decision` reassesses, re-decides over the recomputed assessments and refuses
(`decision_mismatch`) a decision that is not the one recorded. A
manifest format, semantics, rule or decision version this release does not carry is refused (`UnknownVersion`, surfaced
as the engine's `ReplayError`), never substituted. There are no replay-only legacy versions: every TCKDB-owned version is
1, and a manifest recorded under any other one is not replayed. Digests are checksums, not signatures; replay verifies the consequences of captured
inputs, not scientific truth, provenance or completeness of the captured population.

## Surface behavior, old and new

| Surface | Today | Changes in |
| --- | --- | --- |
| statmech search, species-entry statmech, detail, nested lists | administrative browse; unchanged orders | stage 6: honest effective-order and exclusion echo and supersession notices on the species read (PR 6.1, done); TS-entry filter (6.2) |
| conformer filters on statmech search | owning-entry context | unchanged; exact membership is the declared-member filter of the new operation |
| frequency pointer | `freq` role links only | stage 6: qualified role witnesses added; the pointer is preserved |
| trust badge | species-owner rubric | stage 6 (separate PR): TS-owned records no longer penalised |
| release candidate map | species owner only | stage 6: discriminated owner |
| MCP statmech tools | narrower filter set | stage 6: backend-valid filters, `electronic_levels`, explicit `trust` |
| detail-with-task, parent-with-task and trust-with-task surfaces | absent | not built in stage 5: they would repeat this decision per page of another endpoint, and the plan lists them only as consumers of the same normalised result; a follow-up when a named consumer needs one |
| selected-model export and release inclusion | absent | not built: no other selector's decision is exported or released (only network kinetics serialises numbers, and statmech returns none); declared models already ship with their statmech record |
| new `select` operation | absent | stage 5: `POST /api/v1/scientific/statmech/select` (+ `/manifest`), `select_statmech_models` in the client, `tckdb_select_statmech_models` in the MCP, `docs/guides/selecting_statmech_models.md` |

Transport export borrows the lowest-id non-null statmech linearity. That is a separate witness question and this
programme does not change it.
