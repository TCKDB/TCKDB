# The API vocabulary

**Generated. Do not edit by hand.** Regenerate with
`conda run -n tckdb_env python backend/scripts/generate_api_vocabulary.py`.
Every token below is read from the code that defines it — the enum members,
the public-reference registry, the code catalogue, the trust rubrics — so this
document cannot describe a word TCKDB does not use. A term added upstream and
not regenerated here turns the CI gate red.

## What this is

TCKDB answers in tokens: `not_reviewed`, `hard_failed`, `matched_direction`,
`spc_01h9k…`, `smiles_too_long`. Each of them is a real distinction the code
takes seriously, and most of them are not guessable from the string. This is
the list, with a plain-language definition for each and the concrete case where
the meaning turns on something non-obvious.

It assumes you know chemistry and does not assume you know this database. So
`substructure` gets a sentence about which algorithm ran and `chebyshev` does
not appear at all: a Chebyshev fit means the same thing here as everywhere
else, and explaining it would bury the tokens that mean something only inside
TCKDB.

## What is in, and what is not

A token is here when **both** of these hold:

1. **You can meet it.** The literal string can appear in a public API response
   body. This half is checked mechanically: `app/glossary/reachability.py`
   computes the vocabulary the read schemas and the trust fragment can
   serialise, and the test suite refuses an entry outside that set. A word
   only an administrator or an internal service sees is not vocabulary you
   need.
2. **Chemistry does not decode it.** The token says something about *TCKDB's
   own process* — how a record was reviewed, how much evidence stands behind
   it, how your query matched, how a record is named, or why a request was
   refused.

Three things are deliberately absent:

- **The scientific checks.** What TCKDB guarantees about chemistry is its own
  generated document: [the scientific check
  register](scientific_check_register.md). Copying it here would create a
  second copy to keep in step.
- **Reaction family names.** A reaction's `family` is an RMG identifier —
  `H_Abstraction`, `Disproportionation`, `intra_H_migration` — carried
  verbatim. There are 125 of them seeded and the `reaction_family`
  table holds only an id, that name and a timestamp: **no display name and no
  description column exist**, so "Hydrogen Abstraction" is not a fact this
  database holds and inventing 125 of them here would be fiction
  with a generator's credibility. Recorded as a gap; giving families human-readable names is a
  schema decision, not a documentation one.
- **A definition per refusal code.** The code catalogue deliberately carries no
  prose — the refusal already has a sentence, the one you receive in `detail`,
  and a second copy would drift from it. The code table below therefore renders
  the facts the catalogue does hold, and invents nothing.


## What this covers

| Kind of token | Count | Read from |
| --- | --- | --- |
| Status, badge and query words | 107 | 24 enums, declared in `backend/app/glossary/declarations.py` |
| Identifier prefixes | 34 | `backend/app/services/public_refs.py` |
| Trust check names | 143 | `backend/app/services/trust/rubrics.py` |
| Refusal codes a caller can receive | 158 | `backend/app/api/code_catalogue.py` |
| **total** | **442** | |

## How a record is named

Every record TCKDB serves is named by a **public reference** — a short prefix, an underscore, and 26 characters: `spc_`, `rxn_`, `cg_`, `lot_`. The prefix tells you what kind of record it is, and it is the only part you are meant to read.

There are two kinds of reference, and the difference matters more than it looks:

- **Content-derived** — computed from the record's own canonical identity. The same species has the **same reference on every TCKDB instance**, so two deployments can be compared, and a reference of this kind is a claim about *what the thing is*.
- **Opaque** — 130 random bits. It identifies one row in one database and nothing more. Two instances that hold the same calculation give it different opaque references, and that is correct: an *event* is not the same event because it looks alike.

Nothing in the string says which kind you are holding, which is why this table exists.

### Content-derived prefixes (12)

| Prefix | Names a | Same on every instance? |
| --- | --- | --- |
| `cas_` | conformer assignment scheme | yes |
| `ecs_` | energy correction scheme | yes |
| `fsf_` | frequency scale factor | yes |
| `geom_` | geometry | yes |
| `lit_` | literature | yes |
| `lot_` | level of theory | yes |
| `rxn_` | chem reaction | yes |
| `soft_` | software | yes |
| `spc_` | species | yes |
| `srel_` | software release | yes |
| `wfr_` | workflow tool release | yes |
| `wft_` | workflow tool | yes |

### Opaque prefixes (22)

| Prefix | Names a | Same on every instance? |
| --- | --- | --- |
| `aie_` | artifact integrity event | no — one row, one database |
| `calc_` | calculation | no — one row, one database |
| `cg_` | conformer group | no — one row, one database |
| `co_` | conformer observation | no — one row, one database |
| `cpol_` | curation policy | no — one row, one database |
| `gasch_` | group additivity scheme | no — one row, one database |
| `kin_` | kinetics | no — one row, one database |
| `net_` | network | no — one row, one database |
| `nkin_` | network kinetics | no — one row, one database |
| `nsolve_` | network solve | no — one row, one database |
| `rel_` | dataset release | no — one row, one database |
| `rman_` | release manifest | no — one row, one database |
| `rpa_` | record reproducibility assessment | no — one row, one database |
| `rsel_` | release selection | no — one row, one database |
| `rxe_` | reaction entry | no — one row, one database |
| `sm_` | statmech | no — one row, one database |
| `spe_` | species entry | no — one row, one database |
| `sub_` | submission | no — one row, one database |
| `thm_` | thermo | no — one row, one database |
| `trn_` | transport | no — one row, one database |
| `ts_` | transition state | no — one row, one database |
| `tse_` | transition state entry | no — one row, one database |

## What a person has said about a record

### Review status

*On the wire:* `review.status` on a record, `trust.review_status` inside the trust fragment, the `review_summary` counts on a search response, and the `min_review_status` filter on a request.

What a **human curator** has said about one scientific record. It is the only vocabulary here that reports a person's judgement; everything under *What TCKDB can check by itself* is computed and says nothing about whether anybody has looked.

| Token | What it means |
| --- | --- |
| `not_reviewed` | Nobody has looked at this record. It is the state every deposit lands in, and it is a statement about TCKDB's attention, not about the science: an unreviewed record can be perfectly good. |
| `under_review` | A curator has this record open. It is entered deliberately by a curator, never by depositing, which is the whole difference from `not_reviewed`. **For example:** Until 2026-08-24 every deposit was stamped `under_review` on arrival, so 1,153 records on the hosted database claimed somebody was looking at them when nobody was. Revision `c1d8f4a25b30` moved them to `not_reviewed`. If you read the hosted API before that date, `under_review` there meant nothing at all. |
| `approved` | A curator reviewed the record and accepts it. This is the floor the `curated` read profile draws at: a curated read returns approved records and nothing below them. |
| `rejected` | A curator reviewed the record and does not accept it. Absent from results unless the request asks for it with `include_rejected=true`. |
| `deprecated` | The record should no longer be used — superseded by a better one, or withdrawn. It is kept rather than deleted so an existing citation still resolves. Absent from results unless the request asks with `include_deprecated=true`. |

> When a response ranks candidates it orders them `approved`, `under_review`, `not_reviewed`, `deprecated`, `rejected` — `REVIEW_RANK` in `backend/app/schemas/reads/scientific_common.py`.

## What TCKDB can check by itself

### Trust status (the evidence badge)

*On the wire:* `trust.trust_status`, and `trust.evidence.label` beside it.

How much of the evidence TCKDB expects for this kind of record is actually attached. It is **completeness, not quality**: a `well_supported` rate coefficient is one whose paperwork is complete, not one TCKDB says is right. The first five are the completeness ladder, computed as a weighted ratio of the checks in the record's rubric; `hard_failed` is not on that ladder at all.

| Token | What it means |
| --- | --- |
| `well_supported` | Evidence completeness of 0.90 or better **and** every check the rubric marks `required` passed. The required-checks gate is separate from the ratio: a record cannot reach this badge on volume of evidence alone. |
| `mostly_supported` | Evidence completeness of 0.75 or better. |
| `partial` | Evidence completeness of 0.50 or better. |
| `sparse` | Evidence completeness of 0.25 or better. |
| `unsupported` | Evidence completeness below 0.25. Incomplete, which is not the same as wrong — this is the expected badge for an old record deposited before TCKDB asked for much. |
| `hard_failed` | A discrete structural failure was found, and the badge is set by that finding rather than by the ratio. Always read `trust.evidence.hard_fail_reason` beside it: the reason says whether the record contradicts itself, or whether TCKDB can no longer produce the evidence it rests on. |

### Check outcomes

*On the wire:* the **values** of the `trust.evidence.checks` map, whose keys are the check names listed further down.

What one deterministic check found. Four states and never a boolean: `missing` and `not_applicable` are different answers and collapsing them would penalise a record for a question nobody could ask.

| Token | What it means |
| --- | --- |
| `passed` | The check's condition held. Positive evidence, and it counts. |
| `missing` | The check applied and did not pass. It counts against the record: it is inside the denominator and outside the numerator of the completeness ratio. |
| `warning` | The check applied, the underlying signal is tri-state, and the result is informational. Warning-kind checks carry zero weight, so this never moves the completeness ratio. |
| `not_applicable` | The question could not be asked of this record, so the check is excluded from **both** the numerator and the denominator — it neither helps nor hurts. **For example:** When `geometry_validation_present_for_source_calculations` is `missing`, there is no validation verdict to inspect, so `geometry_validation_not_failed_for_source_calculations` is `not_applicable`: "did it fail?" has no answer. Rendering that as `false` would count the same absence twice. |

> Check names are written as assertions, so read the name and the value together: `"ts_graph_or_smiles_present": "missing"` means *there is no SMILES or mol blob on this transition-state entry*. Until 2026-08-24 the same fact was reported as the string `ts_graph_or_smiles_present` sitting inside an array named `missing_checks`, which read as a double negative; the four arrays `passed_checks` / `missing_checks` / `warning_checks` / `not_applicable_checks` were replaced by this one map. If you are reading a client written before then, that is what those arrays were.

### Hard-fail reasons

*On the wire:* `trust.evidence.hard_fail_reason`, and only when `trust_status` is `hard_failed`.

Why a record was hard-failed. Each names one discrete, evidenced structural failure — never a low score. Six of them (`invalid_lj_pair`, `no_transport_property_present`, `no_thermo_representation_present`, `invalid_external_symmetry`, `invalid_torsion_dimension`, `multiplicity_invalid`) re-derive a rule the upload path already refuses, so seeing one means the record reached the database by some route that skipped upload validation — an archive restore, a data migration, a bulk importer or direct SQL. Treat those as an incident to trace, not as a record that merely scored badly.

| Token | What it means |
| --- | --- |
| `calculation_missing` | The calculation this verdict is about could not be loaded at all. |
| `calculation_rejected` | The calculation's `quality` is `rejected` — a curator marked it unusable. |
| `kinetics_missing` | The kinetics record could not be loaded. |
| `statmech_missing` | The statistical-mechanics record could not be loaded. |
| `thermo_missing` | The thermochemistry record could not be loaded. |
| `transport_missing` | The transport record could not be loaded. |
| `species_entry_missing` | The record does not point at the species entry it is supposed to describe, so there is nothing to attribute it to. |
| `no_thermo_representation_present` | A thermochemistry record carrying no thermochemistry: no NASA polynomial, no Wilhoit, no scalar values, no points. A backstop — the upload path refuses this. |
| `no_transport_property_present` | A transport record carrying no transport property at all. A backstop — the upload path refuses this. |
| `invalid_lj_pair` | The Lennard-Jones parameters are not a usable pair. A backstop — the upload path refuses this. |
| `invalid_temperature_range` | The record's validity range is definitionally impossible: a non-positive temperature, or a minimum above the maximum. Note that a single-temperature range (`tmin == tmax`) is legal and does **not** fire this, and there is no upper bound — shock-tube and plasma chemistry are not structurally broken. |
| `invalid_external_symmetry` | The external symmetry number is below 1. A backstop — the upload path refuses this. |
| `invalid_torsion_dimension` | A hindered-rotor torsion declares a dimension below 1. A backstop — the upload path refuses this. |
| `geometry_validation_failed` | TCKDB compared the calculation's geometry against the structure the record claims it is, and the comparison failed. |
| `artifact_integrity_failed` | The stored bytes behind one of this calculation's artifacts no longer match their digest, or are gone. This one is a statement about **TCKDB's custody of the evidence**, not about the depositor's science: the record may be perfectly good and we can no longer show you what it rests on. It reflects the latest observation per artifact, so a restored object clears it. |
| `missing_required_identity` | The kinetics record does not identify a complete reaction — no reaction entry, or a side with no participants on it. |
| `source_calculation_hard_failed_for_required_role` | A calculation this record depends on for a role it cannot do without — a reactant or product energy, the TS energy, the frequencies — is itself hard-failed. The failure is inherited, so read that calculation's own reason. |
| `transition_state_entry_missing` | The transition-state entry could not be loaded. |
| `transition_state_parent_missing` | The entry does not point at the transition state it is an entry for. |
| `reaction_entry_missing` | The parent transition state names no reaction entry, so the TS belongs to no reaction. |
| `ts_entry_status_rejected` | The transition-state entry's own `status` is `rejected`. |
| `multiplicity_invalid` | The spin multiplicity is below 1. A backstop — the upload path refuses this. |
| `all_source_calculations_hard_failed` | Every calculation supporting this transition-state entry is itself hard-failed, so nothing is left to support it. |
| `geometry_validation_failed_for_source_calculation` | A calculation supporting this transition-state entry failed geometry validation. |
| `frequency_source_has_zero_imaginary_modes_for_validated_ts` | A transition-state entry whose status is `optimized` or `validated`, whose frequency evidence reports no imaginary mode. The record says saddle point and the numbers say minimum. |
| `frequency_source_reaction_coordinate_not_designated_for_validated_ts` | The record reports more than one imaginary mode and does not say which one is the reaction coordinate. More than one imaginary mode is acceptable — this fires only on the missing designation, which is why it is a question about what was recorded and not about physics. |

### Reproducibility grade

*On the wire:* `assessments.reproducibility.grade`.

How far somebody else could get with what was deposited. An evidence ladder, independent of both the review status and the trust badge, and a statement about *completeness of the deposit* rather than a promise of bitwise-identical output. Only a calculation can be graded above `described`: a thermo or kinetics record keeps the limits of the sources behind it.

| Token | What it means |
| --- | --- |
| `insufficient` | The record does not even describe what was done well enough to audit. |
| `described` | What the record is and the scientific context around it are recorded. The ceiling for every non-calculation record. |
| `auditable` | The preserved evidence can be inspected: the output bytes are there and were read back through the artifact path. |
| `rerunnable` | The deposit is complete enough to **attempt** a rerun — preserved inputs, an execution-parameter snapshot, the upstream dependency snapshot, and no warnings about artifact bytes TCKDB could not read. It is not a claim that a rerun would reproduce the numbers. |

### Check names

*On the wire:* the **keys** of the `trust.evidence.checks` map.

A check name is an assertion, so read it together with its value: `"irc_evidence_present": "missing"` means there is no IRC evidence. The names below are the rubrics' own, and each explanation is the sentence the rubric itself carries — not a paraphrase written here.

`kind` decides what a failure costs: **required** — failing it stops the record reaching `well_supported`, however good the ratio is; **optional** — contributes to completeness; its absence blocks no badge; **warning** — informational; carries zero weight. `weight` is that check's share of the completeness ratio.

Which rubric applies is decided by the kind of record: `computed_calculation` (v1), `computed_kinetics` (v1), `computed_statmech` (v1), `computed_thermo` (v1), `computed_transition_state` (v2), `computed_transport` (v1).

| Check | Rubric | Kind | Weight | What it asks |
| --- | --- | --- | --- | --- |
| `arrhenius_parameters_complete` | `computed_kinetics` | required | 1 | Arrhenius-family kinetics should include A, A units, n, and Ea. |
| `arrhenius_units_present` | `computed_kinetics` | optional | 1 | Arrhenius A units should be populated for Arrhenius-family kinetics. |
| `artifacts_present` | `computed_calculation` | optional | 1 | At least one calculation_artifact (log, input, ...) should be retained. |
| `at_least_one_thermo_representation_present` | `computed_thermo` | required | 1 | Thermo must have scalar, NASA-7, NASA-9, Wilhoit, or tabulated-point evidence. |
| `calculation_dependencies_present` | `computed_transition_state` | optional | 1 | At least one calculation_dependency edge should document the source-set DAG. |
| `calculation_dependencies_present_when_expected` | `computed_calculation` | optional | 1 | Calculations derived from another step (freq/sp/irc/scan) should record their upstream parent. |
| `calculation_has_owner` | `computed_calculation` | required | 1 | Calculation must be owned by exactly one species_entry or transition_state_entry. |
| `calculation_type_present` | `computed_calculation` | required | 1 | Calculation.type must be set. |
| `charge_present` | `computed_transition_state` | required | 1 | transition_state_entry.charge must be set. |
| `chem_reaction_present` | `computed_transition_state` | optional | 1 | Parent reaction_entry should resolve to a chem_reaction. |
| `dipole_present` | `computed_transport` | optional | 1 | Dipole evidence should be populated when this representation is present. |
| `dipole_source_present_if_dipole_present` | `computed_transport` | optional | 1 | Computed dipole transport evidence should link a dipole source calculation. |
| `epsilon_present` | `computed_transport` | optional | 1 | Lennard-Jones transport should include epsilon/k. |
| `external_symmetry_present` | `computed_statmech` | optional | 1 | External symmetry number should be recorded. |
| `extra_imaginary_modes_not_flagged` | `computed_transition_state` | warning | 1 | Recorded ADR 0012 structural flag: an extra imaginary mode at or above the protocol's tau (advisory). |
| `freq_source_present` | `computed_statmech` | optional | 1 | Computed statmech should link a frequency source calculation when available. |
| `freq_source_present` | `computed_thermo` | optional | 1 | Computed thermo should link a frequency source calculation when available. |
| `frequency_scale_factor_present_if_applicable` | `computed_statmech` | optional | 1 | Frequency-derived statmech should record a frequency scale factor. |
| `frequency_scale_factor_present_if_applicable` | `computed_thermo` | optional | 1 | Frequency-derived thermo should record its frequency scale factor when schema support exists. |
| `frequency_source_present` | `computed_kinetics` | optional | 1 | Frequency source calculations should be linked when available. |
| `full_transport_source_present` | `computed_transport` | optional | 1 | Computed transport should link a full-transport source calculation when available. |
| `geometry_validation_not_failed_for_source_calculations` | `computed_kinetics` | warning | 1 | Source calculation geometry validation is warning (advisory). |
| `geometry_validation_not_failed_for_source_calculations` | `computed_statmech` | warning | 1 | Source calculation geometry validation is warning (advisory). |
| `geometry_validation_not_failed_for_source_calculations` | `computed_thermo` | warning | 1 | Source calculation geometry validation is warning (advisory). |
| `geometry_validation_not_failed_for_source_calculations` | `computed_transition_state` | warning | 1 | Source calculation geometry validation is warning (advisory). |
| `geometry_validation_not_failed_for_source_calculations` | `computed_transport` | warning | 1 | Source calculation geometry validation is warning (advisory). |
| `geometry_validation_passed_or_warning` | `computed_calculation` | warning | 1 | Geometry validation status is warning (advisory). |
| `geometry_validation_present` | `computed_calculation` | optional | 1 | Opt calculations should carry geometry-validation evidence. |
| `geometry_validation_present_for_source_calculations` | `computed_kinetics` | optional | 1 | Strong source calculations should carry geometry-validation evidence. |
| `geometry_validation_present_for_source_calculations` | `computed_statmech` | optional | 1 | Strong source calculations should carry geometry-validation evidence. |
| `geometry_validation_present_for_source_calculations` | `computed_thermo` | optional | 1 | Strong source calculations should carry geometry-validation evidence. |
| `geometry_validation_present_for_source_calculations` | `computed_transition_state` | optional | 1 | At least one opt/irc/path_search source calc should carry geometry validation. |
| `geometry_validation_present_for_source_calculations` | `computed_transport` | optional | 1 | Strong source calculations should carry geometry-validation evidence. |
| `imaginary_frequency_count_recorded` | `computed_transition_state` | optional | 1 | Representative freq result should record n_imag. |
| `imaginary_frequency_value_present` | `computed_transition_state` | optional | 1 | Representative freq result should record the imaginary-mode value (cm-1). |
| `input_geometry_present` | `computed_calculation` | required | 1 | At least one input geometry must be linked. |
| `irc_evidence_present` | `computed_kinetics` | optional | 1 | IRC evidence should be linked when available. |
| `irc_evidence_present` | `computed_transition_state` | optional | 1 | IRC evidence should be linked when available (additive only). |
| `is_linear_present` | `computed_statmech` | optional | 1 | Linearity should be explicitly recorded. |
| `kinetics_model_present` | `computed_kinetics` | required | 1 | Kinetics.model_kind must be set. |
| `level_of_theory_present` | `computed_calculation` | required | 1 | Calculation must resolve to a level_of_theory row. |
| `lj_pair_present_if_applicable` | `computed_transport` | optional | 1 | Lennard-Jones transport should include both sigma and epsilon/k. |
| `master_equation_or_fit_source_present_if_applicable` | `computed_kinetics` | optional | 1 | Explicit master-equation or fit-source roles count when present. |
| `multiplicity_present` | `computed_transition_state` | required | 1 | transition_state_entry.multiplicity must be set. |
| `multiplicity_valid` | `computed_transition_state` | required | 1 | transition_state_entry.multiplicity must be >= 1. |
| `nasa_coefficients_present` | `computed_thermo` | optional | 1 | NASA thermo should include a complete coefficient block. |
| `opt_source_present` | `computed_statmech` | optional | 1 | Computed statmech should link an optimization source calculation when available. |
| `opt_source_present` | `computed_thermo` | optional | 1 | Computed thermo should link an optimization source calculation when available. |
| `output_geometry_present` | `computed_calculation` | optional | 1 | Geometry-producing calculation types should record an output geometry. |
| `parameters_parsed` | `computed_calculation` | optional | 1 | ESS execution parameters should be parsed (EAV rows or JSONB snapshot). |
| `path_search_evidence_present` | `computed_kinetics` | optional | 1 | Path-search evidence should be linked when available. |
| `path_search_evidence_present` | `computed_transition_state` | optional | 1 | Path-search (NEB/GSM/scan parent) evidence should be linked when available. |
| `point_group_present` | `computed_statmech` | optional | 1 | Point group should be recorded when known. |
| `polarizability_present` | `computed_transport` | optional | 1 | Polarizability evidence should be populated when this representation is present. |
| `polarizability_source_present_if_polarizability_present` | `computed_transport` | optional | 1 | Computed polarizability transport evidence should link a polarizability source calculation. |
| `product_energy_sources_present` | `computed_kinetics` | optional | 1 | Product energy source calculations should cover all loaded products. |
| `quality_recorded` | `computed_calculation` | optional | 1 | An independent reviewer should have approved this calculation via record_review; self-declared CalculationQuality is not sufficient (decoupled from trust, see _check_quality_recorded). |
| `reactant_energy_sources_present` | `computed_kinetics` | optional | 1 | Reactant energy source calculations should cover all loaded reactants. |
| `reaction_coordinate_designated_for_ts` | `computed_transition_state` | required | 1 | Representative freq result must identify its reaction coordinate: one imaginary mode needs no designation, more than one does (ADR 0012). |
| `reaction_entry_present` | `computed_kinetics` | required | 1 | Kinetics must be attached to a reaction_entry. |
| `reaction_entry_present` | `computed_transition_state` | required | 1 | Parent transition_state must resolve to a reaction_entry. |
| `result_block_present` | `computed_calculation` | required | 1 | Calculation must have the result block matching its type (sp/opt/freq/irc/scan/path_search). |
| `review_not_rejected_or_deprecated_if_applicable` | `computed_transition_state` | required | 1 | TS-entry review/deprecation checks apply once record_review lookup is wired. |
| `rigid_rotor_kind_present` | `computed_statmech` | required | 1 | Rigid rotor treatment should be recorded. |
| `rotational_relaxation_present` | `computed_transport` | optional | 1 | Rotational-relaxation evidence should be populated when present. |
| `scalar_thermo_present` | `computed_thermo` | optional | 1 | Scalar H298 or S298 values should be populated when using scalar thermo. |
| `scan_source_present_if_torsions_present` | `computed_statmech` | optional | 1 | Torsion-bearing statmech should link scan source evidence. |
| `scf_stability_present_if_claimed` | `computed_calculation` | optional | 1 | SCF stability evidence should be attached when claimed. |
| `sigma_epsilon_pair_consistent` | `computed_transport` | required | 1 | sigma_angstrom and epsilon_over_k_k must be both present or both absent. |
| `sigma_present` | `computed_transport` | optional | 1 | Lennard-Jones transport should include sigma. |
| `software_release_present` | `computed_calculation` | optional | 1 | Calculation should declare which software_release produced it. |
| `source_calculation_artifacts_present` | `computed_kinetics` | optional | 1 | At least one linked source calculation should retain an artifact. |
| `source_calculation_artifacts_present` | `computed_statmech` | optional | 1 | At least one linked source calculation should retain an artifact. |
| `source_calculation_artifacts_present` | `computed_thermo` | optional | 1 | At least one linked source calculation should retain an artifact. |
| `source_calculation_artifacts_present` | `computed_transition_state` | optional | 1 | At least one source calculation should retain an artifact. |
| `source_calculation_artifacts_present` | `computed_transport` | optional | 1 | At least one linked source calculation should retain an artifact. |
| `source_calculation_has_non_hard_failed_evidence` | `computed_statmech` | optional | 1 | Linked source calculations should avoid deterministic hard-fail signals. |
| `source_calculation_has_non_hard_failed_evidence` | `computed_thermo` | optional | 1 | Linked source calculations should avoid deterministic hard-fail signals. |
| `source_calculation_has_non_hard_failed_evidence` | `computed_transition_state` | required | 2 | At least one source calculation must avoid deterministic hard-fail signals. |
| `source_calculation_has_non_hard_failed_evidence` | `computed_transport` | optional | 1 | Linked source calculations should avoid deterministic hard-fail signals. |
| `source_calculation_lot_present` | `computed_kinetics` | required | 1 | All linked source calculations should resolve to level_of_theory. |
| `source_calculation_lot_present` | `computed_statmech` | required | 1 | All linked source calculations should resolve to level_of_theory. |
| `source_calculation_lot_present` | `computed_thermo` | required | 1 | All linked source calculations should resolve to level_of_theory. |
| `source_calculation_lot_present` | `computed_transition_state` | required | 1 | Every source calculation must resolve to a level_of_theory. |
| `source_calculation_lot_present` | `computed_transport` | optional | 1 | All linked source calculations should resolve to level_of_theory. |
| `source_calculation_result_blocks_present` | `computed_kinetics` | optional | 1 | Linked source calculations should have their expected result blocks. |
| `source_calculation_result_blocks_present` | `computed_statmech` | optional | 1 | Linked source calculations should have their expected result blocks. |
| `source_calculation_result_blocks_present` | `computed_thermo` | optional | 1 | Linked source calculations should have their expected result blocks. |
| `source_calculation_result_blocks_present` | `computed_transport` | optional | 1 | Linked source calculations should have their expected result blocks. |
| `source_calculation_software_present` | `computed_kinetics` | optional | 1 | All linked source calculations should declare software_release. |
| `source_calculation_software_present` | `computed_statmech` | optional | 1 | All linked source calculations should declare software_release. |
| `source_calculation_software_present` | `computed_thermo` | optional | 1 | All linked source calculations should declare software_release. |
| `source_calculation_software_present` | `computed_transition_state` | optional | 1 | Every source calculation should declare a software_release. |
| `source_calculation_software_present` | `computed_transport` | optional | 1 | All linked source calculations should declare software_release. |
| `source_calculation_workflow_tool_present` | `computed_statmech` | optional | 1 | Statmech or at least one source calc should declare workflow-tool release metadata. |
| `source_calculation_workflow_tool_present` | `computed_thermo` | optional | 1 | Thermo or at least one source calc should declare workflow-tool release metadata. |
| `source_calculation_workflow_tool_present` | `computed_transition_state` | optional | 1 | At least one source calculation should declare a workflow_tool_release. |
| `source_calculation_workflow_tool_present` | `computed_transport` | optional | 1 | Transport or at least one source calc should declare workflow-tool release metadata. |
| `source_calculations_present` | `computed_kinetics` | required | 1 | At least one kinetics_source_calculation row should support computed kinetics. |
| `source_calculations_present` | `computed_statmech` | required | 1 | At least one statmech_source_calculation row should support computed statmech. |
| `source_calculations_present` | `computed_thermo` | required | 1 | At least one thermo_source_calculation row should support computed thermo. |
| `source_calculations_present` | `computed_transport` | optional | 1 | At least one transport_source_calculation row should support computed transport. |
| `sp_or_composite_source_present` | `computed_statmech` | optional | 1 | Computed statmech may link single-point or composite supporting evidence. |
| `sp_or_composite_source_present_if_applicable` | `computed_thermo` | optional | 1 | Computed thermo should link single-point or composite energy evidence when applicable. |
| `species_entry_present` | `computed_statmech` | required | 1 | Statmech must be attached to a species_entry. |
| `species_entry_present` | `computed_thermo` | required | 1 | Thermo must be attached to a species_entry. |
| `species_entry_present` | `computed_transport` | required | 1 | Transport must be attached to a species_entry. |
| `statmech_not_rejected_or_deprecated_if_applicable` | `computed_statmech` | optional | 1 | Statmech rejection/deprecation checks apply once modeled. |
| `statmech_origin_is_computed` | `computed_statmech` | required | 1 | Statmech.scientific_origin should be computed for this rubric. |
| `statmech_present` | `computed_thermo` | optional | 1 | Linked statmech evidence is expected when schema support exists. |
| `statmech_treatment_present` | `computed_statmech` | required | 1 | Statmech treatment kind should be recorded. |
| `supporting_calculations_present` | `computed_transition_state` | required | 2 | At least one calculation should support this TS entry. |
| `supporting_geometry_source_present` | `computed_transport` | optional | 1 | Computed transport should link supporting-geometry evidence when available. |
| `temperature_range_present` | `computed_kinetics` | optional | 1 | Both tmin_k and tmax_k should be populated. |
| `temperature_range_present_if_applicable` | `computed_thermo` | optional | 1 | Range-bearing thermo should declare temperature bounds. |
| `temperature_range_valid` | `computed_kinetics` | optional | 1 | Temperature range should satisfy 0 < tmin_k < tmax_k <= 10000. |
| `temperature_range_valid` | `computed_thermo` | optional | 1 | Temperature ranges should satisfy 0 < low < high <= 20000. |
| `thermo_model_present` | `computed_thermo` | required | 1 | Thermo should expose scalar, NASA-7, NASA-9, Wilhoit, or tabulated-point model evidence. |
| `thermo_not_rejected_or_deprecated_if_applicable` | `computed_thermo` | optional | 1 | Thermo rejection/deprecation checks apply once modeled. |
| `thermo_origin_is_computed` | `computed_thermo` | required | 1 | Thermo.scientific_origin should be computed for this rubric. |
| `thermo_points_present` | `computed_thermo` | optional | 1 | Tabulated thermo should include at least one point with a thermo value. |
| `torsion_definitions_present` | `computed_statmech` | optional | 1 | Recorded torsions should include torsion coordinate definitions. |
| `torsion_symmetry_recorded` | `computed_statmech` | optional | 1 | Recorded torsions should include symmetry numbers. |
| `torsions_recorded_if_hindered_rotor_treatment` | `computed_statmech` | optional | 1 | Hindered-rotor/statmech torsion treatments should include torsion rows. |
| `transition_state_entry_present` | `computed_transition_state` | required | 1 | The transition_state_entry record under evaluation is loaded. |
| `transition_state_parent_present` | `computed_transition_state` | required | 1 | transition_state_entry must resolve to its parent transition_state. |
| `transport_model_present` | `computed_transport` | required | 1 | Transport should expose at least one structured property representation. |
| `transport_not_rejected_or_deprecated_if_applicable` | `computed_transport` | optional | 1 | Transport rejection/deprecation checks apply once modeled. |
| `transport_origin_is_computed` | `computed_transport` | required | 1 | Transport.scientific_origin should be computed for this rubric. |
| `transport_property_present` | `computed_transport` | required | 1 | At least one transport property must be populated. |
| `ts_energy_source_present` | `computed_kinetics` | optional | 1 | Computed TST kinetics should link a TS energy source calculation. |
| `ts_frequency_present` | `computed_transition_state` | optional | 1 | A frequency calculation should be in the source set. |
| `ts_graph_or_smiles_present` | `computed_transition_state` | optional | 1 | A SMILES or mol blob should be attached to the TS entry. |
| `ts_optimization_present` | `computed_transition_state` | optional | 1 | A TS-optimization calculation should be in the source set. |
| `ts_single_point_present` | `computed_transition_state` | optional | 1 | A single-point calculation should be in the source set. |
| `ts_status_not_rejected` | `computed_transition_state` | required | 1 | transition_state_entry.status must not be rejected. |
| `ts_status_recorded` | `computed_transition_state` | required | 1 | transition_state_entry.status must be set. |
| `tunneling_metadata_present_if_claimed` | `computed_kinetics` | optional | 1 | A claimed tunneling model should have a non-empty identifier. |
| `uncertainty_present` | `computed_kinetics` | optional | 1 | At least one uncertainty field should be populated. |
| `uncertainty_present` | `computed_thermo` | optional | 1 | At least one thermo uncertainty field should be populated. |
| `uses_projected_frequencies_recorded` | `computed_statmech` | optional | 1 | Projected-frequency handling should be explicitly recorded. |
| `workflow_tool_release_present` | `computed_calculation` | optional | 1 | Calculation should declare which workflow_tool_release orchestrated it. |
| `workflow_tool_release_present` | `computed_kinetics` | optional | 1 | Kinetics or at least one source calc should declare workflow-tool release metadata. |

## Which contract a response answers under

### Read profile

*On the wire:* `request.profile`, echoed on every scientific response.

Which contract the response answers under. Always echoed, never inferred: a consumer should not have to guess whether they are looking at the archive or at a curated set.

| Token | What it means |
| --- | --- |
| `exploratory` | Every visible candidate, each with its own review and trust state, and **no recommendation from TCKDB** about which is right. This is the default on every read surface, deliberately: on a corpus that is mostly uncurated a curated default returns an empty page and reads as a broken database. |
| `curated` | Only records at or above the `approved` review floor. Records are not annotated with any release selection that names them — for an attributed endorsement use the `/api/v1/scientific/releases/*` endpoints. |

### Profile recommendation

*On the wire:* `request.profile_recommendation`, echoed beside `request.profile`.

Whether the records in this response carry a TCKDB recommendation. It is a separate field from the profile because none of its three values can be derived from the profile token alone.

| Token | What it means |
| --- | --- |
| `none` | These are candidates. TCKDB is not telling you which one to use. |
| `approved_floor_only` | Every record shown is at or above the `approved` review floor and **nothing more is claimed**. A human accepted each of them, which is not the same as TCKDB preferring them over their siblings. |
| `tckdb_curated_release` | These records are the ones an attributed, append-only release selection names in a published release. Only the release endpoints emit this. |

### Dataset release status

*On the wire:* `status` on a dataset release.

Lifecycle of a citable dataset release.

| Token | What it means |
| --- | --- |
| `draft` | Being assembled. Selections may still be appended, no manifest is frozen, and it is neither served under the curated profile nor citable. |
| `published` | The manifest is frozen and checksummed. The release is citable and its bytes are reproducible. |
| `withdrawn` | It was published and later retracted. The row and its manifest are **kept** so an existing citation never dangles; the status is how a consumer is told not to rely on it. |

### Release selection action

*On the wire:* `action` on a release selection row.

What one selection row asserts. Selections are append-only and never edited: a curator changing their mind appends a row pointing at the one it replaces.

| Token | What it means |
| --- | --- |
| `select` | The first attributed selection of a candidate record. |
| `supersede` | Replaces an earlier selection with a different candidate. |
| `withdraw` | Retires an earlier selection with no replacement — the release then makes no recommendation for that subject. |

### Release artifact kind

*On the wire:* `kind` on a file listed in a release manifest.

The role of one checksummed file inside a dataset release. A release deliberately ships the selection *and* the material needed to disagree with it.

| Token | What it means |
| --- | --- |
| `selected_records` | The records the release selects. |
| `candidate_records` | The candidates that were **not** selected, so a reader can check the choice. |
| `review_history` | The review decisions behind those records. |
| `selection_ledger` | The append-only log of selection actions themselves. |

## How your query matched

### Reaction direction

*On the wire:* the `direction` request parameter, and — the one that surprises people — `matched_direction` on every reaction and kinetics search result.

Which way round the stored equation had to be read for your query to match it. TCKDB stores a reaction in one orientation; a query naming the other side still matches, and `matched_direction` is how the response tells you that happened. It is present on every row, including the forward ones, so its absence never has to be interpreted.

| Token | What it means |
| --- | --- |
| `forward` | Your query matched the reaction as stored: what you asked for as reactants are that reaction's reactants. |
| `reverse` | Your query matched the reaction read backwards: **what you asked for as reactants are that reaction's products.** |
| `either` | Match the reaction in whichever orientation works. This is the default on a request; it is not an answer a result row gives — a matched row always reports `forward` or `reverse`. |

> Search `reactants=NN` and one of the results is `[NH2] + [NH2] <=> NN` with `matched_direction: "reverse"`. Nothing is wrong: NN is stored as a **product** of that reaction, and reading the equation backwards puts it on the reactant side, which is what your query asked about. What `matched_direction` does **not** tell you is which way round the rate coefficients on that record run: a kinetics record carries its own `direction` (below), stated relative to the stored orientation. So read both — `matched_direction` says how TCKDB found the reaction for you, `direction` says what the numbers on it describe.

### Kinetics direction

*On the wire:* `direction` on a kinetics record, and the filter of the same name.

Which direction of the stored equation a rate coefficient describes. It is stated relative to the reaction entry's stored reactant-to-product orientation, which is what makes it a different question from `matched_direction`: that one is about your query, this one is about the numbers. A record may leave it unset — the historical default — in which case the value comes back empty and all you know is that the producer did not say.

| Token | What it means |
| --- | --- |
| `forward` | A fit of the rate in the stored orientation: reactants to products as written. |
| `reverse` | A fit of the rate for the same equation run backwards. It can sit on the same reaction entry as a forward fit — Chemkin and Cantera give the two as separate expressions, so TCKDB keeps them as separate records rather than merging them. |
| `net` | A rate that already folds both directions together — an apparent or observed net rate. |

### Participant match mode

*On the wire:* the `match` request parameter on reaction and kinetics search.

How the species you listed are compared against a stored reaction's sides.

| Token | What it means |
| --- | --- |
| `contains` | The default. Set containment per role: every species you name must appear in that role of the stored reaction, and a side you did not mention constrains nothing. Containment is by **set**, not multiset — naming a species once matches a reaction consuming two of it. **For example:** `reactants=NN` alone means "NN among the reactants, products unconstrained" — which is what a chemist means by "reactions of hydrazine". |
| `exact` | Multiset equality on both sides: precisely this equation, both sides, counts included. Ask for this when you want one specific reaction rather than a family. |

### Collapse mode

*On the wire:* the `collapse` request parameter, echoed in `request.collapse`.

How many records per subject the response returns.

| Token | What it means |
| --- | --- |
| `all` | Every eligible record, after filtering, sorting and pagination. The default. |
| `first` | At most one record — zero or one — after filtering and sorting. Which one is decided by the selection policy below, and that choice is made at read time; it stores nothing. |

### Selection policy

*On the wire:* the `selection_policy` request parameter, meaningful when `collapse=first`.

Which candidate wins when the response is collapsed to one. Naming the policy makes "show me one" an explicit choice rather than a silent one. No policy persists a curator decision — for an attributed endorsement, use a dataset release.

| Token | What it means |
| --- | --- |
| `default` | The endpoint's standard ranking. |
| `latest` | Most recently created first. |
| `most_reviewed` | Best review status first, then most recent. |

### Structure search mode

*On the wire:* the `mode` request parameter on structure search, echoed in each match summary.

Which matching algorithm produced a structure-search hit.

| Token | What it means |
| --- | --- |
| `substructure` | RDKit substructure containment: the stored molecule contains the query pattern. |
| `similarity` | Tanimoto similarity over Morgan fingerprints. Only this mode populates `similarity_score` on a result. |
| `exact` | Equality of canonical InChIKey. |

### Structure query kind

*On the wire:* `matched_query_kind` on a structure-search match summary.

Which of the query fields a hit came from, echoed so a caller with several query inputs can attribute a result without re-parsing the request.

| Token | What it means |
| --- | --- |
| `smiles` | The match came from the supplied SMILES. |
| `smarts` | The match came from the supplied SMARTS pattern. |
| `inchi` | The match came from the supplied InChI. |
| `inchi_key` | The match came from the supplied InChIKey. |

## How a record was produced

### Scientific origin

*On the wire:* `scientific_origin` on a record, and the filter of the same name.

Where a number came from, before anything else is said about it.

| Token | What it means |
| --- | --- |
| `computed` | Produced by a quantum-chemistry or kinetics calculation. |
| `experimental` | Measured in a laboratory rather than computed or estimated. |
| `estimated` | Estimated — group additivity, an analogy, a correlation. |

### Calculation type

*On the wire:* `calculation_type` on a calculation record, and the filter of the same name.

What kind of job a stored calculation was. TCKDB records the job, not the intent: a `freq` on an unoptimised geometry is still a `freq`.

| Token | What it means |
| --- | --- |
| `opt` | Geometry optimisation. |
| `freq` | Frequency (Hessian) calculation. |
| `sp` | Single-point energy. |
| `irc` | Intrinsic reaction coordinate following. |
| `scan` | A scan over one or more internal coordinates. |
| `path_search` | A reaction-path search producing a TS guess. Which algorithm ran (NEB, GSM, …) is recorded on the result row, not as a separate type. |
| `conf` | A conformer search — a job exploring the accessible conformations of one species. |

### Calculation quality

*On the wire:* `quality` on a calculation record, and the filter of the same name.

A curation flag on one calculation, separate from the review status of the record it supports.

| Token | What it means |
| --- | --- |
| `raw` | The default every calculation is stored with. It means nobody has curated it, not that anything is wrong. |
| `curated` | Someone has curated this calculation and stands behind it. |
| `rejected` | Marked unusable. Such calculations are excluded from results unless a request opts in with `include_rejected_quality=true`, and a record resting on one is hard-failed with `calculation_rejected`. |

### Geometry validation status

*On the wire:* `validation_status` on a calculation's geometry-validation summary, and `geometry_validation_status` on a calculation evidence summary.

What TCKDB found when it compared a calculation's geometry against the structure the record claims it is — connectivity and molecular identity, not energetics.

| Token | What it means |
| --- | --- |
| `passed` | The geometry is the structure the record claims. |
| `warning` | Something differs and TCKDB will not call it a failure. An optimisation that drifted is science to record, not a payload to refuse. |
| `fail` | The geometry is not the claimed structure. This hard-fails the calculation's trust badge with `geometry_validation_failed`. |
| `not_present` | Nobody checked. There is no validation row for this calculation, and the read layer says so rather than leaving the field empty — an absent check and a passed check are different answers and TCKDB will not let them look alike. |

The record itself stores only `passed`, `warning`, `fail`; `not_present` is added by the read layer, which is why you will not find it in the database schema.

### Atom-map source

*On the wire:* `source` on a reaction's atom-map badge.

How the atom-to-atom correspondence across a reaction was obtained. The column has no default: a map that cannot say how it was obtained is not a map TCKDB accepts.

| Token | What it means |
| --- | --- |
| `declared` | A depositor stated the correspondence — they ran the calculation and followed the reaction coordinate, so they know which atom went where. |
| `inferred` | An algorithm produced it. Read back as inferred, never as though a human asserted it. |

### Transition-state entry status

*On the wire:* `status` on a transition-state entry.

How far a transition-state entry has been taken by whoever deposited it.

| Token | What it means |
| --- | --- |
| `guess` | A starting structure. Not optimised. |
| `optimized` | Optimised to a stationary point. |
| `validated` | Optimised and checked as a transition state for the reaction it claims — the strongest thing an entry says about itself. |
| `rejected` | Kept, but not to be used. It hard-fails the entry's trust badge with `ts_entry_status_rejected`. |

### Artifact integrity finding

*On the wire:* `finding` on an artifact-integrity observation.

What was observed when TCKDB read stored bytes back and checked them against their digest. Not severities: four different observations, and the log is append-only, so a repaired object is a **new** observation rather than an edit.

| Token | What it means |
| --- | --- |
| `digest_mismatch` | The object was read and does not hash to the key it is stored under. The bytes are not the bytes TCKDB claims to hold. |
| `size_mismatch` | The digest could not be faulted but the length differs from the byte count on the artifact row — almost always a truncated read rather than a changed object. |
| `object_missing` | The object is absent from the store entirely. |
| `verified` | The object was read and does hash to its key. Recorded only for a digest that already carries a break — this is the observation that clears one, which is why a restored record does not stay condemned forever. |

## Why a request was refused

When TCKDB refuses a request the body carries a `code`. Branch on that, never on the English `detail` — the code is the contract and the sentence is not. These are every code a caller can receive; the catalogue also holds codes for internal guards and server faults, which no request can provoke and which are therefore not listed here.

Two facts accompany each one, and neither is obvious from the name:

- **A *thing* or a *relationship*.** A code naming a thing (`unknown_release`, `smiles_too_long`) is complete as it stands. A code naming a relationship (`state_conflict`, a mismatch, an ambiguity) asserts something about two or more things and names none of them, so the envelope's structured `context` owes you which ones. Where a `context` is still empty for such a code, that is a known gap rather than a claim that there is nothing to say.
- **Whether replaying helps.** Every code here refuses something about the request, so replaying an unchanged request generally meets the same answer. The exception worth knowing is the short list at the end of this section: server-side conditions a retry layer must **not** replay, because they are deterministic and no wait will clear them.

There is deliberately no definition column: the refusal already sent you a sentence, and a second copy kept here would drift from it. `backend/app/api/code_catalogue.py` names the module that raises each one.

### HTTP 400 (1 code)

| Code | Names |
| --- | --- |
| `invalid_idempotency_key` | a thing |

### HTTP 404 (19 codes)

| Code | Names |
| --- | --- |
| `curator_task_not_found` | a thing |
| `handle_not_found` | a thing |
| `irc_result_not_found` | a thing |
| `manifest_not_frozen` | a thing |
| `owner_missing` | a thing |
| `path_search_result_not_found` | a thing |
| `scan_result_not_found` | a thing |
| `unknown_calculation_artifact_ref` | a thing |
| `unknown_calculation_ref` | a thing |
| `unknown_conformer_group_ref` | a thing |
| `unknown_conformer_selection` | a thing |
| `unknown_curation_policy` | a thing |
| `unknown_network_kinetics_ref` | a thing |
| `unknown_record` | a thing |
| `unknown_release` | a thing |
| `unknown_release_artifact` | a thing |
| `unknown_selection` | a thing |
| `unknown_statmech_ref` | a thing |
| `unknown_transition_state_entry_ref` | a thing |

### HTTP 409 (19 codes)

| Code | Names |
| --- | --- |
| `atom_map_element_not_conserved` | a relationship — read `context` |
| `atom_map_not_a_bijection` | a relationship — read `context` |
| `curation_policy_version_conflict` | a relationship — read `context` |
| `doi_already_recorded` | a relationship — read `context` |
| `email_taken` | a thing |
| `energy_transfer_scope_columns_disagree` | a relationship — read `context` |
| `idempotency_conflict` | a relationship — read `context` |
| `manifest_already_frozen` | a thing |
| `network_solve_reported_requires_literature` | a thing |
| `reference_conflict` | a relationship — read `context` |
| `release_not_draft` | a thing |
| `release_not_published` | a thing |
| `release_tag_taken` | a thing |
| `selection_already_stands` | a relationship — read `context` |
| `selection_already_superseded` | a relationship — read `context` |
| `state_conflict` | a relationship — read `context` |
| `statmech_subject_not_exactly_one` | a thing |
| `unique_conflict` | a relationship — read `context` |
| `username_taken` | a thing |

### HTTP 422 (115 codes)

| Code | Names |
| --- | --- |
| `ambiguous_conformer_selection_locator` | a relationship — read `context` |
| `applied_energy_correction_source_calculation_owner_mismatch` | a relationship — read `context` |
| `applied_energy_correction_source_key_undeclared` | a thing |
| `arrhenius_a_units_molecularity_mismatch` | a relationship — read `context` |
| `atom_map_atoms_unaccounted_for` | a relationship — read `context` |
| `atom_map_contradicts_irc_mapping` | a relationship — read `context` |
| `atom_map_element_not_conserved` | a relationship — read `context` |
| `atom_map_geometry_unparseable` | a thing |
| `atom_map_indices_not_geometry_relative` | a relationship — read `context` |
| `atom_map_inferred_requires_note` | a thing |
| `atom_map_not_a_bijection` | a relationship — read `context` |
| `atom_map_participant_not_declared` | a thing |
| `atom_map_without_transition_state` | a thing |
| `calculation_geometry_composition_mismatch` | a relationship — read `context` |
| `calculation_handle_conflict` | a relationship — read `context` |
| `calculation_key_undeclared` | a thing |
| `canonical_parameter_value_requires_key` | a thing |
| `client_sort_not_supported` | a thing |
| `composed_search_candidate_limit_exceeded` | a relationship — read `context` |
| `composed_search_invalid_page` | a relationship — read `context` |
| `composed_search_pagination_changed` | a relationship — read `context` |
| `composed_search_pagination_stalled` | a relationship — read `context` |
| `conformer_key_undeclared` | a thing |
| `cursor_offset_conflict` | a relationship — read `context` |
| `cursor_query_mismatch` | a relationship — read `context` |
| `export_all_cap_exceeded` | a relationship — read `context` |
| `export_seed_empty` | a thing |
| `export_seed_unresolved` | a thing |
| `freq_list_exceeds_geometry_degrees_of_freedom` | a relationship — read `context` |
| `freq_mode_index_not_unique` | a relationship — read `context` |
| `freq_n_imag_disagrees_with_modes` | a relationship — read `context` |
| `geometry_key_unresolved` | a thing |
| `geometry_too_large` | a relationship — read `context` |
| `handle_type_mismatch` | a relationship — read `context` |
| `include_not_implemented_yet` | a thing |
| `invalid_cursor` | a thing |
| `invalid_handle` | a thing |
| `invalid_pagination` | a thing |
| `invalid_range` | a relationship — read `context` |
| `invalid_structure_query` | a thing |
| `invalid_temperature_range` | a relationship — read `context` |
| `kinetics_interpretation_conformer_selection_owner_mismatch` | a relationship — read `context` |
| `kinetics_interpretation_statmech_owner_mismatch` | a relationship — read `context` |
| `level_of_theory_handle_conflict` | a relationship — read `context` |
| `limit_too_large` | a relationship — read `context` |
| `lowest_energy_unavailable` | a thing |
| `micro_reaction_key_undeclared` | a thing |
| `missing_filter` | a thing |
| `missing_identifier` | a thing |
| `missing_reaction_search_filter` | a thing |
| `missing_structure_query` | a thing |
| `missing_version_parent` | a thing |
| `ml_export_all_cap_exceeded` | a relationship — read `context` |
| `ml_export_lot_unresolved` | a thing |
| `ml_export_seed_empty` | a thing |
| `ml_export_seed_unresolved` | a thing |
| `multiple_structure_queries` | a relationship — read `context` |
| `n_imag_contradicts_minimum` | a relationship — read `context` |
| `network_channel_key_undeclared` | a thing |
| `network_state_key_undeclared` | a thing |
| `non_finite_value` | a thing |
| `offset_too_large` | a relationship — read `context` |
| `parameter_value_requires_key` | a thing |
| `post_search_fields_must_be_in_body` | a thing |
| `pressure_alias_conflict` | a relationship — read `context` |
| `query_too_expensive` | a relationship — read `context` |
| `rationale_required` | a thing |
| `reaction_charge_not_conserved` | a relationship — read `context` |
| `reaction_entry_handle_conflict` | a relationship — read `context` |
| `reaction_handle_conflict` | a relationship — read `context` |
| `reaction_mass_balance_failed` | a relationship — read `context` |
| `record_has_no_subject` | a thing |
| `record_not_approved` | a thing |
| `record_ref_not_selectable` | a thing |
| `record_subject_mismatch` | a relationship — read `context` |
| `record_type_not_selectable` | a thing |
| `release_scoping_not_implemented` | a thing |
| `release_selects_nothing` | a thing |
| `selection_no_longer_approved` | a thing |
| `smiles_too_long` | a relationship — read `context` |
| `species_entry_handle_conflict` | a relationship — read `context` |
| `species_geometry_composition_mismatch` | a relationship — read `context` |
| `species_geometry_isotope_mismatch` | a relationship — read `context` |
| `species_handle_conflict` | a relationship — read `context` |
| `species_key_undeclared` | a thing |
| `species_kind_conflict` | a relationship — read `context` |
| `species_smiles_charge_mismatch` | a relationship — read `context` |
| `statmech_calculation_key_undeclared` | a thing |
| `statmech_source_calculation_owner_mismatch` | a relationship — read `context` |
| `statmech_source_role_type_mismatch` | a relationship — read `context` |
| `statmech_torsion_scan_calculation_owner_mismatch` | a relationship — read `context` |
| `stored_species_smiles_unparseable` | a thing |
| `subject_type_mismatch` | a relationship — read `context` |
| `supersedes_same_record` | a relationship — read `context` |
| `thermo_source_calculation_owner_mismatch` | a relationship — read `context` |
| `thermo_source_role_type_mismatch` | a relationship — read `context` |
| `thermo_statmech_owner_mismatch` | a relationship — read `context` |
| `too_many_element_symbols` | a relationship — read `context` |
| `transition_state_charge_mismatch` | a relationship — read `context` |
| `transition_state_composition_mismatch` | a relationship — read `context` |
| `transition_state_irc_mapping_element_mismatch` | a relationship — read `context` |
| `transition_state_key_undeclared` | a thing |
| `transition_state_no_imaginary_mode` | a thing |
| `transition_state_reaction_coordinate_ambiguous` | a relationship — read `context` |
| `transition_state_reaction_coordinate_not_designated` | a thing |
| `unknown_element_symbol` | a thing |
| `unknown_include_token` | a thing |
| `unknown_record_type` | a thing |
| `unsafe_lowest_energy_comparison` | a thing |
| `unsupported_direction` | a thing |
| `unsupported_filter` | a thing |
| `unsupported_ranking_for_calculation_type` | a thing |
| `unsupported_reaction_molecularity` | a thing |
| `unsupported_release_record_type` | a thing |
| `withdraw_reason_required` | a thing |

### HTTP 426 (3 codes)

| Code | Names |
| --- | --- |
| `tckdb_client_version_invalid` | a thing |
| `tckdb_client_version_missing` | a thing |
| `tckdb_client_version_unsupported` | a relationship — read `context` |

### HTTP 429 (1 code)

| Code | Names |
| --- | --- |
| `rate_limit_exceeded` | a thing |

### Codes it is pointless to replay (2)

These arrive at a status that normally invites a retry, and for these codes a retry is a loop with no exit: the condition is deterministic, so waiting cannot clear it and only an operator can. They refuse nothing you did, which is why they are absent from the tables above. The same list is generated into the Python client as `NON_RETRYABLE_CODES`; `backend/app/api/code_catalogue.py` records why each one is permanent.

- **`artifact_integrity_failed`** — HTTP 502
- **`artifact_object_missing`** — HTTP 502

---

Tokens are read from the code at generation time; the definitions live in `backend/app/glossary/declarations.py`, beside the enum each one describes. `backend/tests/scripts/test_api_vocabulary.py` holds this document to its sources: it fails if the committed copy drifts from a fresh render, if a declared token is not a real member of its enum, or if an enum every response carries has no entry here.
