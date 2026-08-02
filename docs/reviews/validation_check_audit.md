# Validation check audit

Applies the rule in [ADR 0008 — Validation tiers: definitions block, expectations warn,
comparisons review](../adr/0008-validation-tiers-definitions-block-expectations-warn.md)
to every validation check that currently exists in the TCKDB backend, so a human can
review the placements and decide what to change.

**Audited tree:** `01d5570` (`origin/main`, "Declare the backend's real dependencies; add a
live-deployment notebook (#71)"). Every `file:line` below was opened and confirmed against
that tree. Line numbers for Pydantic validators point at the `def` line, not the decorator.

**Read-only.** Nothing in this audit changes a check. Everything here is a proposal for review.

## The rule being applied

| Classification | Meaning | Tier it implies |
|---|---|---|
| **definition / contract** | No correct calculation could produce this; or the declaration contradicts the evidence; or an internal contract is violated | blocking (Pydantic) |
| **expectation** | Could plausibly fire on a *correct novel result* (flat/variational TS, unusual-but-real values) | `UploadWarning` |
| **absence** | Evidence is missing, not wrong. Trust/reproducibility layers exist to grade incompleteness | `UploadWarning` (and graded by the trust rubric) |
| **comparison** | Cross-check against external reference data (RMG-database GA, evaluated kinetics libraries) | `machine_review` |

Two further labels appear below where none of the four fit:

- **notification** — the row records something the server *did* (filled a value, ignored a
  field). It is not a check and has no "correct" tier under the ADR; it belongs wherever
  the producer will see it.
- **curation / lookup** — the row reports a stored curation verdict or a broken row
  reference rather than judging science.

## Headline counts

| Tier | Rows enumerated | proposed ≠ current | unclear |
|---|---|---|---|
| 1. Pydantic schema validation (refuses at upload) | 172 | 1 | 3 |
| 2. `UploadWarning` (annotates an accepted payload) | 22 | 4 | 1 |
| 3. `HardFailReason` (labels a stored record at read time) | 26 | 9 | 1 |
| 4. `machine_review` (asynchronous, versioned rubrics) | 0 | — | — |
| **Total** | **220** | **14** | **5** |

A tier-1 "row" is one validator, except in §1i and §1j where one row covers a family of
identical rules replicated across the CRUD, upload and bundle copies of the same schema —
the classification is the same for every copy and listing them separately would add ~60 rows
of noise. Section 1e also carries one row that is not a check but a *gap*: the transport
contract that has no upload-tier owner. Around 115 further validators and the string/length
`Field(...)` constraints were excluded as purely structural; see [§8](#8-deliberately-excluded-as-purely-structural).

Plus 142 `EvidenceCheckSpec` graded checks in the trust rubrics
(`backend/app/services/trust/rubrics.py`), summarised in
[§5](#5-trust-rubric-graded-checks-142-not-a-tier) rather than tabulated: they grade
completeness and refuse nothing, which is correct for absence — with one exception, noted
there, that duplicates a definitional check.

---

## 1. Tier 1 — Pydantic schema validation (blocking at upload)

### 1a. Calculation evidence: internal consistency

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `FrequencyModePayload.validate_sign_matches_is_imaginary` | A mode flagged imaginary whose frequency is not negative (or vice versa) — the sign convention and the flag contradict each other | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py:163` | Pydantic | definition | Pydantic | yes |
| `FreqResultPayload.validate_modes_consistency` | A declared `n_imag` that disagrees with the number of modes actually marked imaginary in the same payload, or duplicate `mode_index` values | `.../fragments/calculation.py:194` | Pydantic | definition | Pydantic | yes |
| `SCFStabilityPayload.validate_status_consistency` | An SCF-stability block whose `status` contradicts its own evidence — `stable` with a re-optimised wavefunction, `stabilized` with zero instabilities found, `unstable` with a re-optimisation recorded | `.../fragments/calculation.py:257` | Pydantic | definition | Pydantic | yes |
| `HessianPayload.validate_triangle_length` | A packed Hessian lower triangle whose length is not 3N(3N+1)/2 for the N atoms in the attached geometry | `.../fragments/calculation.py:336` | Pydantic | definition | Pydantic | yes |
| `WavefunctionDiagnosticPayload.validate_has_diagnostic_value` | A wavefunction-diagnostic block carrying none of T1, D1, T1 norm or largest T2 amplitude — an empty claim | `.../fragments/calculation.py:387` | Pydantic | contract | Pydantic | yes |
| `IRCResultPayload.validate_points` | An IRC whose `ts_point_index` names no supplied point, duplicate point indices, or forward/reverse points present while the corresponding `has_forward`/`has_reverse` flag is false | `.../fragments/calculation.py:487` | Pydantic | definition | Pydantic | yes |
| `PathSearchResultPayload.validate_points` | A path search (NEB/string) whose selected-TS or climbing-image index names no supplied image, duplicate indices, or an `n_points` that disagrees with the image list | `.../fragments/calculation.py:595` | Pydantic | definition | Pydantic | yes |
| `CalculationWithResultsPayload.validate_result_matches_type` | A result block that does not belong to the declared calculation type (e.g. `freq_result` on an `sp` job) | `.../fragments/calculation.py:760` | Pydantic | contract | Pydantic | yes |
| `CalculationWithResultsPayload.validate_tckdb_origin_metadata` | A malformed `parameters_json["tckdb_origin"]` provenance block (DR-0026) | `.../fragments/calculation.py:787` | Pydantic | contract | Pydantic | yes |
| `CalculationOriginMetadata.validate_reused_result_constraints` | A calculation declared `origin_kind='reused_result'` that either names no source type or simultaneously claims `independent_ess_job=True` — a reused result by definition ran no independent job | `.../fragments/calculation_origin.py:87` | Pydantic | definition | Pydantic | yes |
| `CalculationConstraintPayload.validate_arity_and_distinct_atoms` | A geometric constraint whose atom count does not match its kind (bond=2, angle=3, dihedral/improper=4) or that repeats an atom | `.../fragments/calculation.py:58` | Pydantic | definition | Pydantic | yes |
| `CalculationScanCoordinatePayload.validate_arity_and_distinct_atoms` | The same arity/distinctness rule for a scan coordinate | `.../fragments/scan.py:39` | Pydantic | definition | Pydantic | yes |
| `CalculationScanResultCreate.validate_scan_bundle` | A scan whose coordinate indices are not contiguous 1..dimension, duplicate constraint or point indices, or a point whose coordinate values name an undeclared coordinate | `.../fragments/scan.py:133` | Pydantic | definition | Pydantic | yes |
| `CalculationScanPointCreate.validate_unique_coordinate_values` | A scan point that reports the same coordinate twice | `.../fragments/scan.py:108` | Pydantic | definition | Pydantic | yes |
| `CalculationScanPointPayload.validate_geometry_exclusive` | A scan point that supplies both an inline geometry and a resolved `geometry_id` | `.../fragments/scan.py:93` | Pydantic | contract | Pydantic | yes |
| `GeometryPayload.validate_isotopes` | An isotope map with a non-positive (i.e. non-1-based) atom index or a mass number below 1 | `.../fragments/geometry.py:28` | Pydantic | definition | Pydantic | yes |
| `CalculationDependencyBase.validate_not_self_edge` | A calculation-DAG edge from a calculation to itself | `backend/app/schemas/entities/calculation.py:172` | Pydantic | definition | Pydantic | yes |
| `CalculationOwnerRequiredMixin.validate_exactly_one_owner` | A calculation owned by neither, or both, a species entry and a transition-state entry | `backend/app/schemas/fragments/calculation.py:54` | Pydantic | contract | Pydantic | yes |
| `ComputedReactionCalculationIn.validate_result_matches_type` | Bundle mirror of the result-block matrix, extended to `scan_result`; also forbids scalar opt/freq/sp fields on a `scan` calculation | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:118` | Pydantic | contract | Pydantic | yes |
| `CalculationInBundle.validate_result_matches_type` | Same matrix on the computed-species bundle | `.../workflows/computed_species_upload.py:192` | Pydantic | contract | Pydantic | yes |
| `CalculationInBundle.reject_database_id_fields` | Database FK ids smuggled inside a bundle's opaque `parameters_json` (DR-0029) | `.../workflows/computed_species_upload.py:255` | Pydantic | contract | Pydantic | yes |

### 1b. Kinetics

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `KineticsUploadRequest.validate_a_units_vs_molecularity` | Arrhenius A-factor units whose dimensionality does not match the reaction's molecularity (via `validate_a_units_for_molecularity`, `backend/app/chemistry/units.py:48`) | `backend/app/schemas/workflows/kinetics_upload.py:583` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_arrhenius_entries_a_units` | A summed `multi_arrhenius` term whose A-units differ from the main-line molecularity — every term is the same reaction (DR-0036) | `.../kinetics_upload.py:590` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_plog_entries_a_units` | A PLOG pressure entry whose A-units differ from the reaction's molecularity (DR-0032 Part C) | `.../kinetics_upload.py:606` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_falloff_low_a_units` | Low-pressure-limit k0 units that are not one order higher than k∞ (DR-0032 Part B) | `.../kinetics_upload.py:622` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_third_body_is_meaningful` | `is_third_body` asserted on a PLOG or Chebyshev fit, whose parameterization already carries the full pressure dependence | `.../kinetics_upload.py:555` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_model_scientific_content` | A declared functional form with no parameters for it (Arrhenius with no A, falloff kind with no falloff block, PLOG with no entries, Chebyshev with no coefficients), or child blocks the form does not admit (efficiencies on PLOG/Chebyshev) | `.../kinetics_upload.py:636` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_multi_arrhenius` | A DUPLICATE/`multi_arrhenius` channel with fewer than two terms, a scalar `a` set alongside the terms, or duplicate entry indices | `.../kinetics_upload.py:491` | Pydantic | definition | Pydantic | yes |
| `ChebyshevUpload.validate_grid` | A Chebyshev block whose coefficient matrix is not n_T × n_P, contains a non-finite coefficient, omits any of the four T/P bounds the reduced variables need, or has inverted bounds | `.../kinetics_upload.py:195` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_temperature_range` | `tmin_k > tmax_k` on a rate | `.../kinetics_upload.py:481` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_pressure_context` | `pressure_context='apparent_at_pressure'` with no pressure recorded — an apparent rate with no pressure is meaningless | `.../kinetics_upload.py:460` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_a_uncertainty_kind` | An A-factor uncertainty without its kind (or vice versa), or a multiplicative factor f < 1 | `.../kinetics_upload.py:521` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_reported_ea_pair` | A reported activation energy with no units, or units with no value | `.../kinetics_upload.py:471` | Pydantic | definition | Pydantic | yes |
| `MultiArrheniusEntryUpload.validate_reported_ea_pair` | The same Ea/units pairing on a summed term | `.../kinetics_upload.py:168` | Pydantic | definition | Pydantic | yes |
| `KineticsTunnelingApplicationUpload.validate_model_inputs` | A tunneling-evidence block that contradicts itself: `model='none'`, a non-negative imaginary frequency under the declared negative-cm⁻¹ convention, Wigner/Eckart/SCT with no imaginary frequency, Eckart with missing reactant/product energies or forward/reverse barriers or energy conventions, SCT with no path-integral artifact, `model='other'` with no identifier or no result artifact, a half-supplied artifact reference, or an `other` convention with no note | `.../kinetics_upload.py:313` | Pydantic | definition | Pydantic | yes |
| `KineticsUploadRequest.validate_tunneling_declaration_agrees` | A `tunneling_application` evidence block whose model disagrees with the declared `tunneling_model` label | `.../kinetics_upload.py:710` | Pydantic | definition | Pydantic | yes |
| `KineticsInterpretationAssignmentUpload.validate_role_shape` | A partition-function assignment whose shape contradicts its role: a TS assignment with a participant index or a conformer selection, a reactant/product assignment with a TS ref or no participant index | `.../kinetics_upload.py:238` | Pydantic | definition | Pydantic | yes |
| `KineticsInterpretationAssignmentUpload.validate_other_requires_note` | An `other` ensemble / standard-state / degeneracy convention with no note explaining it | `.../kinetics_upload.py:262` | Pydantic | contract | Pydantic | yes |
| `KineticsUploadRequest.validate_interpretation_content` | A partition-function interpretation set that is offered but incomplete: a participant index outside the declared reactant/product list, duplicate subjects, or a set that names some but not all reaction subjects (plus the TS when a tunneling correction is claimed) | `.../kinetics_upload.py:734` | Pydantic | contract | Pydantic | yes |
| `KineticsReactionUpload.validate_reaction_family` | A non-canonical reaction family supplied with no source note, or a source note with no family | `.../kinetics_upload.py:82` | Pydantic | contract | Pydantic | yes |
| `KineticsBase.validate_temperature_range` | `tmin_k > tmax_k` on the CRUD kinetics schema | `backend/app/schemas/entities/kinetics.py:131` | Pydantic | definition | Pydantic | yes |
| `KineticsUpdate.validate_temperature_range_when_complete` | The same, on partial update once both bounds are present | `backend/app/schemas/entities/kinetics.py:225` | Pydantic | definition | Pydantic | yes |
| `KineticsBase.validate_pressure_context` | Apparent-pressure context with no pressure (CRUD) | `backend/app/schemas/entities/kinetics.py:120` | Pydantic | definition | Pydantic | yes |
| `KineticsBase.validate_a_uncertainty_kind` | Uncertainty/kind pairing and multiplicative f ≥ 1 (CRUD) | `backend/app/schemas/entities/kinetics.py:141` | Pydantic | definition | Pydantic | yes |
| `BundleKineticsIn.validate_model_kind_is_representable` | A pressure-dependent or multi-term functional form declared on the bundle payload, which carries only scalar Arrhenius fields and would persist a `plog`-tagged row with zero PLOG entries | `.../workflows/computed_reaction_upload.py:802` | Pydantic | contract | Pydantic | yes |
| `BundleKineticsIn.validate_temperature_range` | `tmin_k > tmax_k` (bundle) | `.../computed_reaction_upload.py:792` | Pydantic | definition | Pydantic | yes |
| `BundleKineticsIn.validate_pressure_context` | Apparent-pressure context with no pressure (bundle) | `.../computed_reaction_upload.py:737` | Pydantic | definition | Pydantic | yes |
| `BundleKineticsIn.validate_ea_pair` | Ea without units (bundle) | `.../computed_reaction_upload.py:764` | Pydantic | definition | Pydantic | yes |
| `BundleKineticsIn.validate_a_uncertainty_kind` | Uncertainty/kind pairing and multiplicative f ≥ 1 (bundle) | `.../computed_reaction_upload.py:772` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsCreate.validate_parameterization_matches_model` | A network-kinetics row whose declared model kind has no matching coefficient block (CRUD) | `backend/app/schemas/entities/network_pdep.py:609` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsBase.validate_temperature_range` / `validate_pressure_range` | Inverted T or P bounds on network kinetics (CRUD) | `backend/app/schemas/entities/network_pdep.py:576`, `:586` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsUpdate.validate_temperature_range` / `validate_pressure_range` | The same on update | `backend/app/schemas/entities/network_pdep.py:670`, `:680` | Pydantic | definition | Pydantic | yes |

### 1c. Thermo

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `ThermoUploadRequest.validate_has_scientific_content` | A thermo upload carrying only identity and provenance — no scalar H298/S298, no NASA-7, no NASA-9 intervals, no Wilhoit, no tabulated points | `backend/app/schemas/workflows/thermo_upload.py:300` | Pydantic | contract | Pydantic | yes |
| `ThermoUploadRequest.validate_representation_consistency` | Two temperature-dependent fits on one record (NASA-7 + NASA-9 + Wilhoit are mutually exclusive), non-contiguous NASA-9 interval indices, or a `model_kind` that disagrees with the populated block | `.../thermo_upload.py:325` | Pydantic | contract | Pydantic | yes |
| `ThermoUploadRequest.validate_group_additivity_origin` | A group-additivity breakdown attached to a record whose origin is not `estimated` — GA *is* the estimation method | `.../thermo_upload.py:282` | Pydantic | definition | Pydantic | yes |
| `ThermoUploadRequest.validate_temperature_range` | `tmin_k > tmax_k` | `.../thermo_upload.py:214` | Pydantic | definition | Pydantic | yes |
| `ThermoUploadRequest.validate_unique_points` | Two tabulated thermo points at the same temperature | `.../thermo_upload.py:224` | Pydantic | definition | Pydantic | yes |
| `ThermoNASABase.validate_temperature_bounds` | A NASA-7 block with partially supplied bounds, or bounds that are not strictly t_low < t_mid < t_high | `schemas/python/tckdb-schemas/tckdb_schemas/thermo.py:82` | Pydantic | definition | Pydantic | yes |
| `ThermoNASA9IntervalBase.validate_interval_bounds` | A NASA-9 interval with `t_max_k <= t_min_k` | `.../thermo.py:136` | Pydantic | definition | Pydantic | yes |
| `ThermoBase.validate_temperature_range` / `ThermoUpdate.validate_temperature_range` | Inverted bounds on the CRUD thermo schemas | `backend/app/schemas/entities/thermo.py:199`, `:276` | Pydantic | definition | Pydantic | yes |
| `ThermoCreate.validate_unique_points` | Duplicate tabulated temperature (CRUD) | `backend/app/schemas/entities/thermo.py:225` | Pydantic | definition | Pydantic | yes |
| `ThermoInBundle.validate_has_scientific_content` | A bundle thermo block with no scalar value, no NASA block and no points | `.../workflows/computed_species_upload.py:376` | Pydantic | contract | Pydantic | yes |
| `ThermoInBundle.validate_temperature_range` / `validate_unique_points` | Inverted bounds / duplicate temperatures (bundle) | `.../computed_species_upload.py:349`, `:359` | Pydantic | definition | Pydantic | yes |
| `BundleThermoIn.validate_temperature_range` | Inverted bounds (reaction bundle) | `.../workflows/computed_reaction_upload.py:290` | Pydantic | definition | Pydantic | yes |
| `AppliedGroupAdditivityUploadPayload.validate_has_components` | A GA breakdown with no component contributions — a decomposition into nothing | `backend/app/schemas/workflows/group_additivity_upload.py:86` | Pydantic | contract | Pydantic | yes |

### 1d. Statmech

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `StatmechUploadRequest.validate_scientific_interpretation` | A rotor-aware `statmech_treatment` (`rrho_1d` / `rrho_nd` / `rrho_1d_nd`) that lists no torsions — a hindered-rotor treatment is *defined* by the rotors it treats | `backend/app/schemas/workflows/statmech_upload.py:233` | Pydantic | definition | Pydantic | yes |
| `ConformerUploadStatmechPayload.validate_scientific_interpretation` | The same rule on the conformer upload path | `backend/app/schemas/workflows/conformer_upload.py:93` | Pydantic | definition | Pydantic | yes |
| `BundleStatmechIn.validate_scientific_interpretation` | The same rule on the computed-reaction bundle | `.../workflows/computed_reaction_upload.py:415` | Pydantic | definition | Pydantic | yes |
| `StatmechInBundle.validate_scientific_interpretation` | The same rule on the computed-species bundle | `.../workflows/computed_species_upload.py:546` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionIn.validate_coordinates` | A torsion whose coordinate count differs from its declared dimension, or whose coordinate indices are not contiguous 1..dimension | `backend/app/schemas/workflows/statmech_upload.py:102` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCreate.validate_coordinates` | The same (CRUD) | `backend/app/schemas/entities/statmech.py:158` | Pydantic | definition | Pydantic | yes |
| `BundleStatmechTorsionIn.validate_coordinates` / `StatmechTorsionInBundle.validate_coordinates` | The same (bundles) | `.../computed_reaction_upload.py:339`, `.../computed_species_upload.py:442` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCoordinateBase.validate_distinct_atoms` | A torsion dihedral that repeats an atom index — four distinct atoms define a dihedral | `backend/app/schemas/entities/statmech.py:64` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCoordinateUpdate.validate_distinct_atoms_when_complete` | The same, once all four indices are present on update | `backend/app/schemas/entities/statmech.py:109` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCoordinateIn.validate_distinct_atoms` | The same on the shared upload fragment | `schemas/python/tckdb-schemas/tckdb_schemas/statmech_bits.py:36` | Pydantic | definition | Pydantic | yes |
| `StatmechUploadRequest.validate_electronic_levels` / `ConformerUploadStatmechPayload.validate_electronic_levels` | Duplicate `level_index` in an electronic-level manifold | `.../statmech_upload.py:188`, `.../conformer_upload.py:84` | Pydantic | definition | Pydantic | yes |
| `StatmechUploadRequest.validate_unique_torsion_indices` and bundle equivalents | The same torsion declared twice | `.../statmech_upload.py:224`, `.../computed_reaction_upload.py:406`, `.../computed_species_upload.py:527` | Pydantic | definition | Pydantic | yes |
| `StatmechCreate.validate_nested_uniqueness` | Duplicate torsion indices or duplicate (calculation, role) source links (CRUD) | `backend/app/schemas/entities/statmech.py:282` | Pydantic | definition | Pydantic | yes |

### 1e. Transport

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `TransportUploadPayload.validate_lj_pair` | A Lennard-Jones σ with no ε/k, or ε/k with no σ — the pair is one potential, not two independent numbers | `backend/app/schemas/workflows/transport_upload.py:61` | Pydantic | definition | Pydantic | yes |
| `TransportCreate.validate_lj_pair` | The same (CRUD) | `backend/app/schemas/entities/transport.py:92` | Pydantic | definition | Pydantic | yes |
| *(no upload-tier equivalent)* | A transport upload carrying **no** transport property at all is accepted; only the read-time evaluator objects (`HardFailReason.no_transport_property_present`) | — | none | contract | Pydantic | **no** — see [§6.1](#61-tier-3-checks-that-are-contracts-and-should-block-instead) |

### 1f. Transition state

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `validate_ts_evidence_set` | More than one IRC evidence record for a TS, or a *passing* record whose participant mappings do not name every reactant/product and account for every TS atom exactly once on both sides — a partial map cannot be passing evidence that the saddle connects the declared endpoints | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/ts_validation_evidence.py:80` | Pydantic | contract | Pydantic | yes |
| `TransitionStateValidationEvidenceIn.validate_participant_mapping` | An empty mapping, a blank participant key, a non-positive (non-1-based) atom index, or an atom repeated within one participant | `.../fragments/ts_validation_evidence.py:54` | Pydantic | definition | Pydantic | yes |
| `TransitionStateValidationEvidenceIn.validate_mapping_sides_are_paired` | A one-sided participant map — a reactant map with no product map cannot be checked for completeness | `.../fragments/ts_validation_evidence.py:69` | Pydantic | contract | Pydantic | yes |
| `TransitionStateUploadRequest.validate_validation_evidence` | IRC evidence on a standalone TS upload with no, or more than one, `irc` additional calculation to bind it to; or a `source_calculation_key` on a payload with no key namespace | `backend/app/schemas/workflows/transition_state_upload.py:166` | Pydantic | contract | Pydantic | yes |
| `BundleTransitionStateIn.validate_evidence_source_is_a_ts_irc_calculation` | Bundle TS evidence that names no calculation, names one the TS does not own, or names a non-`irc` calculation | `.../workflows/computed_reaction_upload.py:601` | Pydantic | contract | Pydantic | yes |
| `ComputedReactionUploadRequest.validate_ts_validation_evidence` | Runs `validate_ts_evidence_set` against the bundle's reaction participant counts and TS atom count | `.../computed_reaction_upload.py:873` | Pydantic | contract | Pydantic | yes |
| `TransitionStateUploadRequest.validate_primary_opt_is_opt` | A TS upload whose primary calculation is not an `opt` | `backend/app/schemas/workflows/transition_state_upload.py:157` | Pydantic | contract | Pydantic | yes |
| `BundleTransitionStateIn.validate_primary_is_opt` / `TransitionStateIn.validate_primary_calc_is_opt` | The same on the reaction bundle and the PDep bundle | `.../computed_reaction_upload.py:592`, `backend/app/schemas/workflows/network_pdep_upload.py:215` | Pydantic | contract | Pydantic | yes |
| `TransitionStateUploadRequest.validate_additional_calculation_types` | An additional calculation of a type the TS upload does not accept | `.../transition_state_upload.py:210` | Pydantic | contract | Pydantic | yes |
| `ConformerUploadRequest.validate_additional_calculation_types` | The same on the conformer upload | `backend/app/schemas/workflows/conformer_upload.py:168` | Pydantic | contract | Pydantic | yes |
| `TSReactionUpload.validate_reaction_family` | Non-canonical family with no source note (TS path) | `.../transition_state_upload.py:70` | Pydantic | contract | Pydantic | yes |
| `ConformerIn.validate_primary_calc_is_opt` (PDep and reaction bundle) | A conformer whose primary calculation is not an `opt` | `backend/app/schemas/workflows/network_pdep_upload.py:103`, `.../computed_reaction_upload.py:248` | Pydantic | **unclear** | — | see [§7](#7-unclear) |
| `ConformerInBundle.validate_primary_is_opt` | The same on the computed-species bundle | `.../workflows/computed_species_upload.py:285` | Pydantic | **unclear** | — | see [§7](#7-unclear) |

### 1g. Pressure-dependent networks

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `NetworkKineticsIn.validate_model_payload` | Exactly one model sub-block matching `model_kind`; a Chebyshev surface missing any of the four T/P bounds its reduced variables need; `stores_log10_k` on a PLOG (a Chebyshev-only concept); and `tabulated`, whose write path does not exist | `backend/app/schemas/workflows/network_pdep_upload.py:652` | Pydantic | contract | Pydantic | yes |
| `ChebyshevKineticsIn.validate_grid_dimensions` | A network Chebyshev coefficient matrix that is not n_T × n_P, or that contains a non-finite coefficient | `.../network_pdep_upload.py:519` | Pydantic | definition | Pydantic | yes |
| `PlogKineticsIn.validate_unique_pressure_index` | Two PLOG entries at the same (pressure, entry index) | `.../network_pdep_upload.py:581` | Pydantic | definition | Pydantic | yes |
| `NetworkSolveIn.validate_bath_composition_and_state_energies` | Bath-gas mole fractions that do not sum to 1.0 (abs tol 1e-9), duplicate state energies, or duplicate (state, collider) energy-transfer scopes | `.../network_pdep_upload.py:807` | Pydantic | definition | Pydantic | yes (tolerance noted in [§7](#7-unclear)) |
| `NetworkPDepUploadRequest.validate_mechanistic_channel_evidence` | Duplicate microreaction paths on a channel; a path naming a TS that belongs to a different microreaction; **and** three coverage rules — one state energy for every state, one ⟨ΔE⟩down entry for every (well, bath-gas collider) pair, one barrier for every saddle-point channel path and none for a barrierless one | `.../network_pdep_upload.py:1139` | Pydantic | mixed: definition + **absence** | split | **no** — see [§6.2](#62-tier-1-coverage-requirements-that-are-really-absence) |
| `EnergyTransferIn.validate_scope` | An energy-transfer entry with no state and no collider — a global ⟨ΔE⟩down is scientifically ambiguous about which well and which bath gas it describes | `.../network_pdep_upload.py:419` | Pydantic | definition | Pydantic | yes |
| `StateEnergyIn.validate_energy_is_finite` | A non-finite (NaN/inf) state energy | `.../network_pdep_upload.py:458` | Pydantic | definition | Pydantic | yes |
| `ChannelBarrierIn.validate_barriers_are_finite` | A non-finite forward or reverse barrier | `.../network_pdep_upload.py:485` | Pydantic | definition | Pydantic | yes |
| `NetworkChannelIn.validate_source_ne_sink` | A channel from a state to itself | `.../network_pdep_upload.py:357` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsIn.validate_source_ne_sink` | Network kinetics with equal source and sink, or a half-supplied legacy endpoint pair with no channel key | `.../network_pdep_upload.py:642` | Pydantic | definition | Pydantic | yes |
| `NetworkChannelBase.validate_source_ne_sink` | The same on the CRUD channel schema | `backend/app/schemas/entities/network_pdep.py:136` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsIn.validate_ranges` / `NetworkSolveIn.validate_ranges` | Inverted T or P bounds on network kinetics and on a solve | `.../network_pdep_upload.py:717`, `:792` | Pydantic | definition | Pydantic | yes |
| `NetworkSolveBase.validate_temperature_range` / `validate_pressure_range` (+ `NetworkSolveUpdate` pair) | The same on the CRUD solve schemas | `backend/app/schemas/entities/network_pdep.py:314`, `:324`, `:392`, `:402` | Pydantic | definition | Pydantic | yes |
| `NetworkPDepUploadRequest.validate_states_connected` | A network whose channel graph leaves some states unreachable — two disconnected subnetworks deposited as one | `.../network_pdep_upload.py:1237` | Pydantic | contract | Pydantic | yes |
| `NetworkPDepUploadRequest.validate_no_unused_species` | A species defined in the payload that no state, microreaction or bath-gas entry references | `.../network_pdep_upload.py:1212` | Pydantic | contract | Pydantic | yes |
| `NetworkPDepUploadRequest.validate_unique_channels` / `validate_unique_channel_kinetics` | Duplicate channel keys; two entries of the same model kind on one channel (one Chebyshev **and** one PLOG on the same channel is legitimate and allowed) | `.../network_pdep_upload.py:1101`, `:1109` | Pydantic | definition | Pydantic | yes |
| `NetworkStateIn.validate_unique_participants` / `NetworkStateCreate.validate_unique_participants` | The same species listed twice in one network state | `.../network_pdep_upload.py:260`, `backend/app/schemas/entities/network_pdep.py:92` | Pydantic | definition | Pydantic | yes |
| `NetworkSolveIn.validate_unique_bath_gas` / `NetworkSolveCreate.validate_unique_bath_gases` | The same bath gas declared twice | `.../network_pdep_upload.py:800`, `backend/app/schemas/entities/network_pdep.py:350` | Pydantic | definition | Pydantic | yes |
| `NetworkMicroReactionIn.validate_reaction_family` | Non-canonical family with no source note (PDep path) | `.../network_pdep_upload.py:321` | Pydantic | contract | Pydantic | yes |
| `ConventionBlock.validate_other_requires_note` | An `other` energy-zero or correction convention with no note | `.../network_pdep_upload.py:438` | Pydantic | contract | Pydantic | yes |
| `NetworkCreate.validate_unique_links` | Duplicate reaction links or duplicate (species, role) links on a network (CRUD) | `backend/app/schemas/entities/network.py:101` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsCreate.validate_unique_plog_entries` / `validate_unique_points` | Duplicate PLOG (pressure, index) or duplicate tabulated (T, P) (CRUD) | `backend/app/schemas/entities/network_pdep.py:630`, `:641` | Pydantic | definition | Pydantic | yes |

### 1h. Energy corrections, provenance and identity

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `AppliedEnergyCorrectionUploadPayload.validate_role_scheme_kind_compatibility` | A correction whose application role demands a particular scheme kind but was given another (e.g. `aec_total` fed a `bac_petersson` scheme) | `schemas/python/tckdb-schemas/tckdb_schemas/energy_correction.py:222` | Pydantic | definition | Pydantic | yes |
| `AppliedEnergyCorrectionUploadPayload.validate_role_source_compatibility` | A frequency-scale-factor role given a scheme, or a scheme role given a scale factor | `.../energy_correction.py:207` | Pydantic | definition | Pydantic | yes |
| `AppliedEnergyCorrectionUploadPayload.validate_exactly_one_provenance_source` | A correction carrying both, or neither, a scheme and a frequency scale factor | `.../energy_correction.py:197` | Pydantic | contract | Pydantic | yes |
| `AppliedEnergyCorrectionUploadPayload.validate_fsf_requires_source_calculation` | A frequency scale factor with no source calculation naming the freq job it was applied to | `.../energy_correction.py:254` | Pydantic | contract | Pydantic | yes |
| `AppliedEnergyCorrectionBase.validate_exactly_one_target` | A correction targeting both, or neither, a species entry and a reaction entry (CRUD) | `backend/app/schemas/entities/energy_correction.py:264` | Pydantic | contract | Pydantic | yes |
| `AppliedEnergyCorrectionBase.validate_exactly_one_provenance_source` / `validate_role_source_compatibility` / `validate_fsf_requires_source_calculation` | The CRUD equivalents of the three rules above | `backend/app/schemas/entities/energy_correction.py:275`, `:285`, `:302` | Pydantic | contract | Pydantic | yes |
| `EnergyCorrectionSchemeCreate.validate_unique_atom_params` / `..._bond_params` / `..._component_params` and the `EnergyCorrectionSchemeRef` trio | Two corrections for the same element, bond key, or (component kind, key) in one scheme — an ambiguous correction library | `backend/app/schemas/entities/energy_correction.py:113`, `:120`, `:127`; `.../energy_correction.py:64`, `:71`, `:78` | Pydantic | definition | Pydantic | yes |
| `AppliedEnergyCorrectionCreate.validate_unique_components` / `AppliedEnergyCorrectionUploadPayload.validate_unique_components` | The same component contribution counted twice in one applied correction | `backend/app/schemas/entities/energy_correction.py:320`, `.../energy_correction.py:266` | Pydantic | definition | Pydantic | yes |
| `ExecutionEnvironmentManifestPayload.validate_closure` | A reproducibility manifest that is not self-consistent: a pinned runtime with no executable digest or fewer than two closure entries, a closure that does not contain the exact executable / conda lockfile / container image it claims, a container closure digest that differs from the OCI image digest, or HPC closure digests that do not match the declared environment | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/execution_environment.py:276` | Pydantic | contract | Pydantic | yes |
| `ContainerRuntime.validate_image` | A container reference that is not an immutable `@sha256:<64 hex>` OCI digest — a mutable tag cannot pin an environment | `.../fragments/execution_environment.py:166` | Pydantic | contract | Pydantic | yes |
| `DescribedRuntime.unique_modules` / `HPCModuleRuntime.unique_modules` | The same module/version declared twice in an environment description | `.../execution_environment.py:147`, `:197` | Pydantic | definition | Pydantic | yes |
| `LiteratureUploadRequest.validate_identifier_or_manual_fields` | A literature submission with neither a DOI/ISBN to resolve nor the minimum manual pair (kind + title) | `schemas/python/tckdb-schemas/tckdb_schemas/literature.py:77` | Pydantic | contract | Pydantic | yes |
| `LiteratureCreate.validate_unique_authors` | The same author, or the same author position, listed twice on one reference | `backend/app/schemas/entities/literature.py:92` | Pydantic | definition | Pydantic | yes |
| `ReproducibilityAssessmentAppend.validate_assessor_identity` | A curator assessment with no user, or a system assessment attributed to one | `backend/app/schemas/entities/reproducibility_assessment.py:47` | Pydantic | contract | Pydantic | yes |
| `MolecularPropertyObservationBase._at_least_one_value_representation` | An observation with no scalar, vector or tensor value | `backend/app/schemas/entities/molecular_property_observation.py:84` | Pydantic | contract | Pydantic | yes |
| `MolecularPropertyObservationBase._scalar_value_requires_unit` | A scalar observation with no unit | `.../molecular_property_observation.py:97` | Pydantic | definition | Pydantic | yes |
| `MolecularPropertyObservationBase._property_kind_other_requires_label` | `property_kind='other'` with no label saying what was observed | `.../molecular_property_observation.py:105` | Pydantic | contract | Pydantic | yes |
| `ReactionParticipantUpload.validate_reference_choice` | A participant supplying both, or neither, an existing species-entry id and inline species content | `backend/app/schemas/workflows/reaction_upload.py:24` | Pydantic | contract | Pydantic | yes |
| `ReactionUploadRequest.validate_reaction_family` / `ComputedReactionUploadRequest.validate_reaction_family` | Non-canonical family with no source note | `backend/app/schemas/workflows/reaction_upload.py:59`, `.../computed_reaction_upload.py:897` | Pydantic | contract | Pydantic | yes |
| `ContributionBundleV0.validate_records_match_kind` | A v0 contribution bundle whose declared kind carries no matching records, or that mixes thermo and kinetics | `backend/app/schemas/workflows/contribution_bundle.py:204` | Pydantic | contract | Pydantic | yes |
| `ContributionBundleV0.validate_local_ref_keys` | A malformed local-ref key, or one whose label is a bare number (a raw DB primary key masquerading as a portable ref) | `.../contribution_bundle.py:231` | Pydantic | contract | Pydantic | yes |
| `BundleManifest.validate_unique_paths` | Two manifest entries for the same file path | `.../contribution_bundle.py:175` | Pydantic | contract | Pydantic | yes |
| `ArtifactIn._check_filename` | A filename that is unsafe or inconsistent with the declared artifact kind | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/artifact.py:113` | Pydantic | contract | Pydantic | yes |

### 1i. Numeric domain bounds declared as `Field(...)` constraints

Each of these refuses a physically impossible value at parse time. All are definitional and
all agree with their current tier; they are grouped because the rule is the same in every row.

| Constraint | What it detects | file:line | Classification |
|---|---|---|---|
| `multiplicity: int = Field(ge=1)` | A spin multiplicity below 1 — 2S+1 is a positive integer | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/identity.py:72`; `backend/app/schemas/workflows/transition_state_upload.py:125`; `backend/app/schemas/workflows/network_pdep_upload.py:190`; `backend/app/schemas/entities/transition_state.py:76`, `:96` | definition |
| `external_symmetry`, `optical_isomers`, torsion `symmetry_number`, torsion `dimension` `Field(ge=1)` | A symmetry number, optical-isomer count or rotor dimension below 1 — these are counts of indistinguishable configurations | `backend/app/schemas/workflows/statmech_upload.py:89`, `:92`, `:157`, `:169`; `backend/app/schemas/workflows/conformer_upload.py:58`, `:67`; `backend/app/schemas/entities/statmech.py:136`, `:139`, `:256`, `:309`; `.../computed_species_upload.py:432`, `:435`, `:506`, `:507` | definition |
| `rotational_constant_{a,b,c}_cm1: Field(gt=0)` | A non-positive rotational constant | `backend/app/schemas/workflows/statmech_upload.py:158`–`:160`; `.../computed_species_upload.py:514`–`:516` | definition |
| electronic level `energy_cm1: Field(ge=0)`, `degeneracy: Field(ge=1)`, `level_index: Field(ge=1)` | A negative excitation energy above the ground level, or a degeneracy below 1 | `backend/app/schemas/entities/statmech.py:220`–`:222`; `backend/app/schemas/workflows/conformer_upload.py:39`–`:41` | definition |
| `tmin_k` / `tmax_k` / `t_min_k` / `t_max_k`: `Field(gt=0)` | A non-positive absolute temperature | `backend/app/schemas/entities/kinetics.py:102`–`:103`; `backend/app/schemas/entities/thermo.py:66`–`:67`, `:193`–`:194`, `:270`–`:271`; `backend/app/schemas/workflows/kinetics_upload.py:188`–`:189`, `:427`–`:428`; `.../thermo_upload.py:152`–`:153`; `.../network_pdep_upload.py:626`–`:627`, `:759`–`:760`; `.../computed_species_upload.py:336`–`:337` | definition |
| `pressure_bar` / `pmin_bar` / `pmax_bar` / `reference_pressure_bar`: `Field(gt=0)` | A non-positive absolute pressure | `.../kinetics_upload.py:144`, `:190`–`:191`, `:438`; `.../thermo_upload.py:149`; `.../network_pdep_upload.py:558`, `:628`–`:629`, `:761`–`:762` | definition |
| `mole_fraction: Field(gt=0, le=1)` | A bath-gas mole fraction outside (0, 1] | `backend/app/schemas/workflows/network_pdep_upload.py:391` | definition |
| `alpha0_cm_inv: Field(gt=0)`, `t_ref_k: Field(gt=0)` | A non-positive ⟨ΔE⟩down or reference temperature | `.../network_pdep_upload.py:407`, `:409` | definition |
| `sigma_angstrom` / `epsilon_over_k_k`: `Field(gt=0)`; `rotational_relaxation: Field(ge=0)` | Non-positive Lennard-Jones parameters, negative rotational relaxation | `backend/app/schemas/entities/transport.py:69`–`:74`, `:123`–`:128`; `backend/app/schemas/workflows/transport_upload.py:46`–`:51` | definition |
| `cp0_j_mol_k` / `cp_inf_j_mol_k`: `Field(ge=0)`, `b_k: Field(gt=0)` | Negative Wilhoit heat-capacity limits or a non-positive Wilhoit scaling temperature | `backend/app/schemas/entities/thermo.py:89`–`:91` | definition |
| `h298_uncertainty_kj_mol` / `s298_uncertainty_j_mol_k`: `Field(ge=0)` | A negative uncertainty | `backend/app/schemas/entities/thermo.py:182`–`:183`, `:259`–`:260`; `.../thermo_upload.py:132`–`:133`; `.../computed_species_upload.py:334`–`:335` | definition |
| `degeneracy: Field(gt=0, allow_inf_nan=False)` (reaction path degeneracy) | A non-positive or non-finite reaction-path degeneracy | `backend/app/schemas/workflows/kinetics_upload.py:430` | definition |
| `efficiency: Field(ge=0)` | A negative third-body collision efficiency | `backend/app/schemas/workflows/kinetics_upload.py:137` | definition |
| `n_temperature` / `n_pressure`: `Field(ge=1)` | A Chebyshev grid with a zero-length axis | `.../kinetics_upload.py:186`–`:187`; `.../network_pdep_upload.py:514`–`:515` | definition |
| `stoichiometry: Field(ge=1)`, `participant_index: Field(ge=1)`, `count: Field(ge=1)`, `grain_count: Field(ge=1)` | Non-positive stoichiometric coefficients, 1-based participant slots, GA group counts, master-equation grain counts | `backend/app/schemas/entities/reaction.py:82`, `:90`, `:127`, `:140`; `backend/app/schemas/reads/scientific_network_composition.py:14`; `backend/app/schemas/workflows/network_pdep_upload.py:237`, `:765`; `backend/app/schemas/workflows/group_additivity_upload.py:62` | definition |
| torsion / constraint atom indices: `Field(ge=1)` | A non-1-based atom index | `backend/app/schemas/entities/statmech.py:57`–`:61`, `:102`–`:106`; `schemas/python/tckdb-schemas/tckdb_schemas/statmech_bits.py:29`–`:33` | definition |
| `opt_n_steps: Field(ge=0)` | A negative optimisation step count | `schemas/python/tckdb-schemas/tckdb_schemas/shared/calculation_in.py:85` | definition |
| frequency-scale-factor `value: Field(gt=0)` | A non-positive scale factor | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/refs.py:148` | definition |
| `year: Field(ge=1, le=3000)` | A publication year outside a plausible calendar range | `backend/app/schemas/entities/literature.py:53`, `:109` | contract |

### 1j. Payload referential-integrity contracts

Local-key namespaces are a contract between producer and server: a key must be unique, and
every reference must resolve. Violating one is not a scientific error, but it is an internal
contract violation, so the blocking tier is correct for all of them. Listed for
exhaustiveness; every row is *classification = contract, proposed = Pydantic, agrees = yes*.

| Check | file:line |
|---|---|
| `NetworkPDepUploadRequest.validate_unique_keys` (species / state / microreaction / TS keys unique; calculation and geometry keys globally unique) | `backend/app/schemas/workflows/network_pdep_upload.py:887` |
| `NetworkPDepUploadRequest.validate_key_references` (state→species, channel→state, microreaction→species, TS→microreaction, TS evidence→own `irc` calc, calc→geometry, species statmech source and torsion-scan keys scoped to that species's own calcs and to `scan` type, bath gas→species, solve source calcs, channel kinetics→channel) | `.../network_pdep_upload.py:927` |
| `NetworkSpeciesIn.validate_species_calc_geometry_key` / `validate_species_calc_geometry_belongs_to_conformer` | `.../network_pdep_upload.py:143`, `:154` |
| `BundleSpeciesIn.validate_calc_geometry_keys` / `validate_calc_geometry_belongs_to_conformer` | `.../computed_reaction_upload.py:489`, `:499` |
| `ComputedReactionUploadRequest.validate_unique_keys` / `validate_species_key_refs` / `validate_calculation_key_refs` (incl. no self-edges in `depends_on`, statmech torsion scan keys must be `scan` type) | `.../computed_reaction_upload.py:913`, `:941`, `:959` |
| `ComputedReactionCalculationIn.validate_constraint_indices_union_unique` | `.../computed_reaction_upload.py:177` |
| `CalculationInBundle.validate_constraints` | `.../computed_species_upload.py:228` |
| `CalculationWithResultsPayload.validate_constraint_indices_unique` | `.../fragments/calculation.py:751` |
| `ComputedSpeciesUploadRequest.validate_unique_conformer_keys` / `validate_unique_calculation_keys_global` / `validate_dependency_keys_resolve` / `validate_thermo_source_keys_resolve` / `validate_statmech_source_keys_resolve` / `validate_statmech_torsion_scan_keys_resolve` / `validate_top_level_applied_correction_source_keys_resolve` | `.../computed_species_upload.py:612`, `:619`, `:634`, `:647`, `:670`, `:683`, `:704` |
| `StatmechUploadRequest.validate_unique_calculation_keys` / `validate_source_calculation_keys_exist` / `validate_unique_source_calculation_pairs` / `validate_torsion_scan_calculation_keys` | `backend/app/schemas/workflows/statmech_upload.py:197`, `:204`, `:215`, `:268` |
| `ThermoUploadRequest.validate_unique_calculation_keys` / `validate_source_calculation_keys_exist` / `validate_unique_source_calculation_pairs` / `validate_applied_correction_source_calc_keys` | `.../thermo_upload.py:231`, `:238`, `:253`, `:266` |
| `ThermoSourceCalculationIn.validate_exactly_one_reference` | `.../thermo_upload.py:94` |
| `TransportUploadRequest.validate_unique_calculation_keys` / `validate_source_calculation_keys_exist` / `validate_unique_source_calculation_pairs` | `.../transport_upload.py:128`, `:135`, `:148` |
| `BundleStatmechIn.validate_unique_source_calculation_pairs`, `StatmechInBundle.validate_unique_source_calculation_pairs`, `ThermoInBundle.validate_unique_source_calculation_pairs`, `BundleKineticsIn.validate_unique_source_calculation_pairs` | `.../computed_reaction_upload.py:396`, `.../computed_species_upload.py:536`, `:366`, `.../computed_reaction_upload.py:748` |
| `KineticsCreate.validate_unique_source_calculations`, `ThermoCreate.validate_unique_source_calculations`, `TransportCreate.validate_unique_source_calculations`, `NetworkSolveCreate.validate_unique_source_calculations` | `backend/app/schemas/entities/kinetics.py:174`, `.../thermo.py:234`, `.../transport.py:102`, `.../network_pdep.py:359` |

---

## 2. Tier 2 — `UploadWarning` (payload accepted, annotated)

Twenty-one `W_*` constants plus one inline code. (ADR 0008 says "twenty upload warnings" and
"ten are `W_MISSING_*`"; the tree has 21 and 11 respectively — a small drafting slip, not a
change in substance.)

| Code | What it detects | Constant / emit site | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `W_N_IMAG_CONTRADICTS_MINIMUM` | A record declared a minimum (or vdW complex) whose frequency analysis reports one imaginary mode — the Hessian spectrum says first-order saddle | const `backend/app/services/upload_reconciliation.py:31`, emitted `:246` | warning | **definition** | Pydantic | **no** |
| `W_N_IMAG_HIGHER_ORDER_SADDLE` | Two or more imaginary modes: neither a minimum nor a transition state | const `.../upload_reconciliation.py:33`, emitted `:267` | warning | **definition** | Pydantic | **no** |
| `W_N_IMAG_SUGGESTS_TS` | Exactly one imaginary mode on a species-entry upload, whatever the declared kind — advisory routing hint toward the TS endpoint. Fires even when the declared kind already *is* a transition state, so it can annotate a wholly correct record | const `.../upload_reconciliation.py:32`, emitted `:253` | warning | expectation | warning | yes |
| `W_CHARGE_MISMATCH` | Declared formal charge vs charge deduced from the ESS result | const `.../upload_reconciliation.py:45`, emitted via `_reconcile_deduction` `:304` | warning | **definition** | Pydantic | **no** (and see [§6.3](#63-two-warning-codes-that-cannot-fire-on-any-current-path)) |
| `W_MULTIPLICITY_MISMATCH` | Declared spin multiplicity vs multiplicity deduced from the ESS result | const `.../upload_reconciliation.py:46`, emitted via `:304` | warning | **definition** | Pydantic | **no** (and see [§6.3](#63-two-warning-codes-that-cannot-fire-on-any-current-path)) |
| `W_TERM_SYMBOL_MISMATCH` | Declared term symbol vs one *derived* from multiplicity plus point group / linearity (`deduce_term_symbol`, `backend/app/services/ess_species_deduction.py:129`, confidence `derived`/`heuristic`) | const `.../upload_reconciliation.py:44`, emitted via `:304` | warning | expectation | warning | yes |
| `W_ELECTRONIC_STATE_CONTRADICTS_METHOD` | Declared electronic state vs one inferred from the method and job keywords; the "ground" branch is explicitly `heuristic` and multireference methods return no deduction at all | const `.../upload_reconciliation.py:43`, emitted via `:304` | warning | expectation | warning | yes |
| `W_FREQ_PARSED_NO_MODES` | A freq result produced by an automated ESS parser that ships no per-mode frequencies — the mode list was lost somewhere in the pipeline | const `.../upload_reconciliation.py:40`, emitted `:216` | warning | absence | warning | yes |
| `W_SP_ENERGY_MISMATCH` | A single-point energy that disagrees with the value re-derived from the attached output log beyond 1e-6 Ha | const `backend/app/services/sp_energy_reconciliation.py:46`, emitted `:150` | warning | definition-shaped, but see note | warning | yes (contested — [§7](#7-unclear)) |
| `W_SP_ENERGY_FILLED_FROM_LOG` | Records that TCKDB supplied a missing SP energy from the log rather than leaving it null | const `.../sp_energy_reconciliation.py:50`, emitted `:170` | warning | **notification** | warning | yes (not a check) |
| `W_MISSING_LITERATURE_PROVENANCE` | A non-computed (experimental/estimated) record with no literature anchor | const `backend/app/services/provenance_warnings.py:40`, emitted `:72` | warning | absence | warning | yes |
| `W_MISSING_SOFTWARE_RELEASE_PROVENANCE` | A computed record that does not name the electronic-structure or post-processing software that produced it | const `.../provenance_warnings.py:41`, emitted `:86` | warning | absence | warning | yes |
| `W_MISSING_WORKFLOW_TOOL_PROVENANCE` | A computed record that does not name the orchestration tool (e.g. ARC) | const `.../provenance_warnings.py:42`, emitted `:100` | warning | absence | warning | yes |
| `W_MISSING_LEVEL_OF_THEORY_PROVENANCE` | Computed kinetics with no electronic-energy level of theory to anchor its source SP calculations to | const `.../provenance_warnings.py:43`, emitted `:114` | warning | absence | warning | yes |
| `W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE` | Computed statmech that records no frequency scaling — null means "unknown", and 1.0 means "explicitly unscaled" | const `.../provenance_warnings.py:44`, emitted `:128` | warning | absence | warning | yes |
| `W_MISSING_STATMECH_SOURCE_CALCULATIONS` | Computed statmech with no linked source calculations, so the partition function cannot be traced to what it was derived from | const `.../provenance_warnings.py:53`, emitted `:215` | warning | absence | warning | yes |
| `W_MISSING_STATMECH_FREQUENCY_SOURCE` | Computed statmech for a species with rotational structure (so its Q uses vibrational modes) and no `freq`-role source calculation. Scoped to polyatomics so it does not fire on every monatomic | const `.../provenance_warnings.py:54`, emitted `:227` | warning | absence | warning | yes |
| `W_MISSING_KINETICS_INTERPRETATIONS` | Computed kinetics that does not say which partition functions in this database it was built from — legitimately absent for a rate read out of a CHEMKIN mechanism | const `.../provenance_warnings.py:55`, emitted `:328` | warning | absence | warning | yes |
| `W_MISSING_TS_INTERPRETATION` | An interpretation set naming reactants and products but no transition state — a TST rate with no Q‡. Correctly non-blocking: a master-equation-fitted rate has no single dividing surface, which `network_kinetics_ref` declares | const `.../provenance_warnings.py:57`, emitted `:355` | warning | absence | warning | yes |
| `W_MISSING_TUNNELING_APPLICATION` | A declared tunneling model with no typed evidence block, so the correction is a reported label that cannot be replayed | const `.../provenance_warnings.py:56`, emitted `:372` | warning | absence | warning | yes |
| `W_MISSING_TS_IRC_EVIDENCE` | A transition state deposited with no *passing* IRC evidence — the saddle is stored, but nothing in the deposit shows it connects the declared reactants and products | const `backend/app/services/transition_state_validation.py:29`, emitted `:86` | warning | absence | warning | yes |
| `reaction_family_not_applied` (inline literal) | A reaction family submitted on a TS-anchored kinetics upload, where the family lives on the shared reaction identity and is not modified by a kinetics deposit. (Note: if the stored family *disagrees* with the submitted one, the same block raises instead — `workflows/kinetics.py:332`) | `backend/app/workflows/kinetics.py:322` | warning | **notification** | warning | yes (not a check) |

---

## 3. Tier 3 — `HardFailReason` (labels a stored record at read time)

Enum: `backend/app/services/trust/models.py:87`. Raise sites in
`backend/app/services/trust/evaluator.py` unless noted.

| Member | What it detects | Declared / raised | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `calculation_missing` | The calculation row the read asked for does not exist | `models.py:96` / `evaluator.py:434` | hard fail | lookup | hard fail | yes |
| `kinetics_missing` | Same, for a kinetics row | `models.py:98` / `evaluator.py:458` | hard fail | lookup | hard fail | yes |
| `statmech_missing` | Same, for a statmech row | `models.py:99` / `evaluator.py:506` | hard fail | lookup | hard fail | yes |
| `thermo_missing` | Same, for a thermo row | `models.py:100` / `evaluator.py:482` | hard fail | lookup | hard fail | yes |
| `transport_missing` | Same, for a transport row | `models.py:101` / `evaluator.py:614` | hard fail | lookup | hard fail | yes |
| `transition_state_entry_missing` | Same, for a TS entry | `models.py:115` / `evaluator.py:514` (default arg; call sites `:1011`, `:1386`) | hard fail | lookup | hard fail | yes |
| `calculation_rejected` | A calculation a curator marked `quality=rejected` | `models.py:97` / `evaluator.py:93` | hard fail | curation | hard fail | yes |
| `ts_entry_status_rejected` | A TS candidate whose status is `rejected` | `models.py:118` / `evaluator.py:554` | hard fail | curation | hard fail | yes |
| `geometry_validation_failed` | A calculation whose recorded geometry validation has status `fail` — reads a stored verdict, does not re-derive one | `models.py:109` / `evaluator.py:96` | hard fail | contract | hard fail | yes |
| `geometry_validation_failed_for_source_calculation` | The same verdict on any calculation supporting a TS entry | `models.py:121` / `evaluator.py:566` | hard fail | contract | hard fail | yes |
| `source_calculation_hard_failed_for_required_role` | A required-role source calculation (kinetics: reactant/product/TS energy, freq; thermo & statmech: opt, freq, and scan when torsions are present; transport: full-transport plus role-conditional dipole/polarizability) that is itself hard-failed | `models.py:112` / `evaluator.py:136`, `:245`, `:280`, `:285`, `:337` | hard fail | contract (propagation) | hard fail | yes |
| `all_source_calculations_hard_failed` | Every calculation supporting a TS entry is itself hard-failed | `models.py:120` / `evaluator.py:573` | hard fail | contract (propagation) | hard fail | yes |
| `missing_required_identity` | Kinetics with no reaction entry, or a reaction entry with no reactant or no product participant | `models.py:110` / `evaluator.py:114`, `:125` | hard fail | contract | hard fail (blocked at upload too) | yes |
| `species_entry_missing` | Thermo, statmech or transport with no owning species entry | `models.py:102` / `evaluator.py:232`, `:263`, `:323` | hard fail | contract | hard fail | yes |
| `transition_state_parent_missing` | A TS entry with no parent transition state | `models.py:116` / `evaluator.py:547` | hard fail | contract | hard fail | yes |
| `reaction_entry_missing` | A TS whose parent has no reaction entry | `models.py:117` / `evaluator.py:551` | hard fail | contract | hard fail | yes |
| `no_thermo_representation_present` | A thermo row with no scalar value, no complete NASA-7, no NASA-9 intervals, no Wilhoit and no populated points | `models.py:103` / `evaluator.py:235` | hard fail | contract | Pydantic (already there) + cite | **no** (duplicate) |
| `no_transport_property_present` | A transport row with no σ, ε/k, dipole, polarizability or rotational relaxation | `models.py:104` / `evaluator.py:326` | hard fail | contract | Pydantic | **no** (no upload-tier owner) |
| `invalid_lj_pair` | Exactly one of σ / ε/k populated | `models.py:105` / `evaluator.py:329` | hard fail | definition | Pydantic (already there) + cite | **no** (duplicate) |
| `invalid_external_symmetry` | Statmech external symmetry number below 1 | `models.py:107` / `evaluator.py:266` | hard fail | definition | Pydantic (already there) + cite | **no** (duplicate) |
| `invalid_torsion_dimension` | A torsion with dimension below 1 | `models.py:108` / `evaluator.py:269` | hard fail | definition | Pydantic (already there) + cite | **no** (duplicate) |
| `multiplicity_invalid` | A TS entry with multiplicity below 1 | `models.py:119` / `evaluator.py:557` | hard fail | definition | Pydantic (already there) + cite | **no** (duplicate) |
| `invalid_temperature_range` | Kinetics outside `0 < tmin < tmax <= 10 000 K`; thermo outside `0 < tmin < tmax <= 20 000 K` including NASA-9 intervals and NASA-7 t_low<t_mid<t_high | `models.py:106` / `evaluator.py:129`, `:238` | hard fail | **mixed**: ordering is definition, the upper cap is expectation | split | **no** (partial) |
| `frequency_source_has_zero_imaginary_modes_for_validated_ts` | An optimized/validated TS whose representative freq result reports zero imaginary modes — that geometry is a minimum | `models.py:124` / `evaluator.py:585` | hard fail | **definition** | Pydantic | **no** |
| `frequency_source_has_multiple_imaginary_modes_for_validated_ts` | An optimized/validated TS whose representative freq result reports more than one imaginary mode — a higher-order saddle | `models.py:127` / `evaluator.py:589` | hard fail | **definition** | Pydantic | **no** (the ADR's named duplication) |
| `result_block_missing_when_successful` | *Nothing.* The member is declared and never referenced anywhere in the repository | `models.py:111` / no raise site | hard fail | **unclear (dead)** | — | see [§7](#7-unclear) |

---

## 4. Tier 4 — `machine_review`

**There are no scientific checks at this tier.**

`_ACTIVE_RUBRICS` (`backend/app/services/machine_review/recipe.py:46`) is not an independent
set of rubrics. It is the six *trust* rubrics (`COMPUTED_CALCULATION_V1`,
`COMPUTED_KINETICS_V1`, `COMPUTED_THERMO_V1`, `COMPUTED_STATMECH_V1`,
`COMPUTED_TRANSPORT_V1`, `COMPUTED_TRANSITION_STATE_V1`), imported solely so that their
`version` integers can be stamped onto a machine review as a currency key
(`ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS`, `recipe.py:65`). Bumping a trust rubric restales
existing reviews; that is the entire function of the constant.

The findings themselves would come from a provider. The shipped providers are
`DisabledMachineReviewProvider` (`providers/disabled.py`, returns `status=not_run`) and a
test-only `fake` provider that the deployer-facing factory refuses to build
(`providers/fake.py`). `build_machine_review_provider`
(`providers/factory.py:70`) validates configuration for `cloud` and `local` modes and then
raises — no model call is implemented.

| Item | Status | file:line |
|---|---|---|
| `_ACTIVE_RUBRICS` | Six trust-rubric version pins, no checks of their own | `backend/app/services/machine_review/recipe.py:46` |
| `MachineReviewCategory` | The finding vocabulary a provider may emit: `provenance`, `units`, `geometry`, `kinetics`, `thermo`, `statmech`, `transport`, `transition_state_validation`, `calculation_parameters`, `consistency`, `schema_gap` | `backend/app/services/machine_review/schemas.py:63` |
| `cloud` / `local` provider | Config validated, then `NotImplemented` | `backend/app/services/machine_review/providers/factory.py:84`, `:91` |

**Consequence for the audit and for the paper:** ADR 0008 assigns comparison against
external reference data (RMG-database group additivity, evaluated kinetics libraries) to
this tier. **No such comparison exists anywhere in the codebase today** — not at this tier
and not, correctly, at any earlier one. The tier is architecture without content. Any
statement of what TCKDB enforces should say so plainly rather than implying that reference
comparison is performed asynchronously.

---

## 5. Trust rubric graded checks (142) — not a tier

`backend/app/services/trust/rubrics.py` declares 142 `EvidenceCheckSpec` entries across six
rubrics. They produce `passed` / `missing` / `warning` / `not_applicable` outcomes feeding an
evidence-completeness ratio; they refuse nothing. Essentially all of them are
`*_present` / `*_recorded` presence graders — that is **absence**, and grading it is exactly
what the ADR says the trust layer is for. They are not tabulated here individually because
the classification is uniform.

One exception matters:

- **`single_imaginary_frequency_for_ts`** (`rubrics.py:2922`, runner
  `_check_ts_single_imaginary_frequency_for_ts` at `rubrics.py:2723`, kind `required`)
  encodes "a transition state has exactly one imaginary frequency" a **third** time. The
  same physical fact is therefore evaluated at three tiers with three different
  consequences: annotated at upload (`W_N_IMAG_*`), graded here, and labelled hard-failed by
  the evaluator for optimized/validated entries. ADR 0008 names the tier-2/tier-3 pair; this
  is the missing third occurrence, and any collapse onto a single owner has to account for
  it.

Also out of scope but worth naming so it is not mistaken for a fifth tier: the
reproducibility rubric (`backend/app/services/reproducibility_rubric.py`) emits its own
diagnostic codes (`artifact_verification_size_limit` `:369`,
`artifact_storage_unavailable` `:381`, `artifact_integrity_failed` `:387`,
`artifact_verification_count_budget` `:413`, `artifact_verification_aggregate_budget`
`:422`). Per ADR 0002 reproducibility is a judgement independent of trust; these grade
whether a record could be *re-run*, not whether its science is right.

---

## 6. Rows where proposed ≠ current

Fourteen rows. This is the section a human actually needs to review.

### 6.1 Contradictions currently sitting at the warning tier (4)

| Check | Reasoning |
|---|---|
| `W_N_IMAG_CONTRADICTS_MINIMUM` (`upload_reconciliation.py:31`, emitted `:246`) | A record declared `minimum` whose own frequency evidence reports one imaginary mode is internally contradictory; no correct calculation produces a minimum with a negative Hessian eigenvalue. Definitional, therefore blocking. |
| `W_N_IMAG_HIGHER_ORDER_SADDLE` (`:33`, emitted `:267`) | Two or more imaginary modes is neither a minimum nor a first-order saddle; the record cannot be what it says it is. Definitional. |
| `W_CHARGE_MISMATCH` (`:45`, emitted `:304`) | A declared formal charge that disagrees with the charge the ESS actually ran is a declaration-vs-evidence contradiction, not an expectation. (See also §6.3 — today it cannot fire.) |
| `W_MULTIPLICITY_MISMATCH` (`:46`, emitted `:304`) | Same reasoning for spin multiplicity. (See also §6.3.) |

### 6.2 Tier-1 coverage requirements that are really absence (1)

| Check | Reasoning |
|---|---|
| `NetworkPDepUploadRequest.validate_mechanistic_channel_evidence` (`network_pdep_upload.py:1139`) | The validator mixes two kinds of rule. The structural half is definitional and should keep blocking: duplicate microreaction paths, and a channel path whose TS belongs to a different microreaction. The **coverage** half — "one state energy for every state", "one ⟨ΔE⟩down for every (well, bath-gas collider) pair", "one barrier for every saddle-point path" — refuses a payload for *missing evidence*, which the ADR assigns to the warning tier. A depositor archiving a network whose per-well ⟨ΔE⟩down values were never separately recorded (a single network-wide value is common practice in RMG/Arkane inputs) currently cannot deposit it at all. The "unexpected extra entry" direction of the same rule *is* a contract violation and can stay blocking. Proposal: split the validator, keep the structural half blocking, and demote the missing-coverage half to `UploadWarning`. |

### 6.3 Two warning codes that cannot fire on any current path (0 tier changes beyond §6.1, but a correctness finding)

`build_ess_result_from_upload` (`upload_reconciliation.py:76`) constructs the `ESSResult`
from the upload payload itself: `meta.charge = payload.charge` and
`meta.multiplicity = payload.multiplicity` (`:130`–`:131`). `deduce_charge_multiplicity`
(`ess_species_deduction.py:165`) then reports those same values back, and
`_reconcile_deduction` (`:282`) compares the payload against them. The comparison is
`payload.charge == payload.charge`. **`W_CHARGE_MISMATCH` and `W_MULTIPLICITY_MISMATCH` are
therefore unreachable**, and `deduce_all` has no other caller in the repository.

Two further scoping facts a reviewer should know before promoting them:

- The whole layer-2 deduction pass runs on exactly one endpoint. `reconcile_species_entry_full`
  is called only from `POST /uploads/conformers` (`backend/app/api/routes/uploads.py:195`);
  every other upload route calls the layer-1 `reconcile_species_entry`, which emits only the
  `n_imag` warnings.
- `W_TERM_SYMBOL_MISMATCH` and `W_ELECTRONIC_STATE_CONTRADICTS_METHOD` are *not* affected —
  their deductions derive from point group / linearity and from method keywords, which are
  genuinely independent of the declared field.

Promoting the two mismatch warnings to blocking is therefore not a migration risk today
(nothing can trigger them), but it is also not a fix: the checks need a real
parsed-from-log charge/multiplicity source before they mean anything.

### 6.4 Tier-3 checks that are contracts and should block instead (8 of 9; the ninth is §6.5)

| Check | Reasoning |
|---|---|
| `frequency_source_has_zero_imaginary_modes_for_validated_ts` (`models.py:124`, raised `evaluator.py:585`) | A TS entry whose status is `optimized`/`validated` and whose freq result reports zero imaginary modes is asserting that a minimum is a saddle point. Definitional. Currently only labelled at read time; the record is stored and served. |
| `frequency_source_has_multiple_imaginary_modes_for_validated_ts` (`models.py:127`, raised `:589`) | Same, for a higher-order saddle. This is the duplication ADR 0008 names: the identical physics is a warning at upload (`W_N_IMAG_HIGHER_ORDER_SADDLE`) and a read-time label here, with a third graded copy at `rubrics.py:2922`. One owner at the blocking tier; the other two cite it. |
| `no_transport_property_present` (`models.py:104`, raised `evaluator.py:326`) | A transport record with no transport property is not a transport record. Unlike its thermo counterpart there is **no upload-tier equivalent** — `TransportUploadPayload` only pairs σ with ε/k — so this contract is enforced nowhere except as a read-time label. It should refuse at upload. |
| `invalid_lj_pair` (`models.py:105`, raised `:329`) | Already refused at upload by `validate_lj_pair` (twice: `transport_upload.py:61`, `transport.py:92`). Re-deriving it at read time means the two tiers can disagree about one record. The blocking tier owns it; this should cite rather than re-evaluate. |
| `invalid_external_symmetry` (`models.py:107`, raised `:266`) | Already refused at upload by `Field(ge=1)` on every `external_symmetry` field. Same duplication argument. |
| `invalid_torsion_dimension` (`models.py:108`, raised `:269`) | Already refused at upload by `Field(ge=1)` on torsion `dimension`. Same argument. |
| `multiplicity_invalid` (`models.py:119`, raised `:557`) | Already refused at upload by `multiplicity: Field(ge=1)` on every TS and species identity payload. Same argument. |
| `invalid_temperature_range` (`models.py:106`, raised `:129`, `:238`) | Two rules in one member. The ordering half (`tmin < tmax`, `t_low < t_mid < t_high`) is definitional and already blocks at upload. The **upper caps** — 10 000 K for kinetics (`evaluator.py:128`), 20 000 K for thermo (`_MAX_THERMO_TEMPERATURE_K`, `evaluator.py:195`) — are sanity expectations with no upload-tier counterpart; a shock-tube or plasma dataset above 10 000 K is unusual but not wrong. Proposal: let the blocking tier own the ordering (cite it here), and reclassify the caps as an expectation so they warn rather than force `hard_failed`. |

### 6.5 The ninth tier-3 row: a pure duplicate

`no_thermo_representation_present` (`models.py:103`, raised `evaluator.py:235`) duplicates
`ThermoUploadRequest.validate_has_scientific_content` (`thermo_upload.py:300`). Same
one-owner argument as §6.4, listed separately because unlike the transport case the upload
tier already owns it — the read-time copy is pure redundancy against rows created by other
paths.

---

## 7. Unclear

Honest unknowns rather than confident guesses.

| Item | Why it cannot be classified from the code |
|---|---|
| `HardFailReason.result_block_missing_when_successful` (`trust/models.py:111`) | Declared and **never referenced** — no raise site, no test, no other file mentions the string. Whether it is an abandoned check, a reserved name for planned work, or a check that was moved and left its enum behind cannot be determined from the tree. It has no behaviour to classify. |
| `ConformerIn.validate_primary_calc_is_opt` / `ConformerInBundle.validate_primary_is_opt` (`network_pdep_upload.py:103`, `computed_reaction_upload.py:248`, `computed_species_upload.py:285`) | Whether "a conformer's primary calculation is an `opt`" is a **definition** in TCKDB's data model (conformers are optimized stationary points, per the conformer-group / torsional-basin design) or a **convention** that happens to hold for the deposits seen so far. If it is a convention, the check blocks legitimate deposits: a conformer taken from a crystal structure, an MD snapshot, or an externally supplied geometry has no `opt` job, and today those payloads are refused outright. Resolving this needs a product decision, not more code reading. |
| `W_SP_ENERGY_MISMATCH` (`sp_energy_reconciliation.py:46`) | Formally a declaration-vs-evidence contradiction, which the ADR rule would put at the blocking tier. But it is a *comparison against a re-parse*, and the re-parse can be wrong: the module's own docstring notes that ORCA and Gaussian SP-energy extraction is not wired and that some Molpro methods are unsupported, and a composite or corrected energy in the payload can legitimately differ from the raw energy line in the log. That is exactly the "the reference can be inapplicable" failure mode the ADR uses to send comparisons to a non-blocking tier. Left at warning; flagged because the classification genuinely depends on how much the parser is trusted. |
| `NetworkSolveIn.validate_bath_composition_and_state_energies` mole-fraction sum (`network_pdep_upload.py:807`) | The rule (a composition sums to 1) is definitional. The **tolerance** — `abs_tol=1e-9`, no relative tolerance — is a numerical policy, not a scientific one, and would reject a composition transcribed at, say, six decimal places. Classified as definition; the tolerance is worth a separate look. |

---

## 8. Deliberately excluded as purely structural

Roughly **115 of the 307** Pydantic validators in `backend/app/schemas/` and
`schemas/python/tckdb-schemas/`, plus the string/length `Field(...)` constraints, were left
out. They encode no scientific or contractual rule:

| Category | Approx. count | Why excluded |
|---|---|---|
| `normalize_*` / `_normalize_*` / `strip_*` text normalizers (whitespace stripping, NFC, lower-casing names and versions, `normalize_optional_text` wrappers) | ~88 | They transform values and reject nothing. Present on nearly every schema in the tree. |
| `min_length=1` / `max_length=N` on names, labels, notes, SMILES, term symbols | ~60 field constraints | "A name must be non-empty" is typing, not chemistry. |
| Free-text length caps on public search inputs (`_bound_participant_lengths`, `_bound_participant_refs`, `_bound_participant_smiles`) | 4 (`scientific_kinetics_search.py:111`, `scientific_reactions.py:84`, `scientific_network_kinetics_search.py:137`, `:144`) | Request-size guards on read endpoints; they protect the server, not the science. |
| Pagination and response-shape bounds (`offset ge=0`, `limit ge=1 le=200`, counters `ge=0`) | ~15 | API mechanics. |
| Deprecated-alias reconciliation (`_resolve_pressure_alias`, `scientific_kinetics.py:94`, `scientific_kinetics_search.py:121`) | 2 | An API-compatibility rule about two spellings of one query parameter. |
| Identifier hygiene and secret-scanning in the execution-environment manifest (`_validate_locator`, `_validate_digest`, `_validate_safe_identifier`, the `_SECRET` regex on `DescribedRuntime.validate_description`) | 8 (`fragments/execution_environment.py:77`, `:82`, `:106`, `:111`, `:123`, `:141`, `:193`, `:223`/`:243`) | Format and security checks. The *structural* manifest rules that do encode a reproducibility contract (`validate_closure`, `ContainerRuntime.validate_image`, the two `unique_modules`) are included in §1h. |
| ORCID and DOI/ISBN format normalizers (`validate_orcid`, `normalize_orcid`) | 2 (`entities/author.py:26`, `:62`) | Identifier syntax. |
| SHA-256 `pattern=r"^[0-9a-f]{64}$"` constraints | 3 | Digest syntax. |
| Ingestion/dry-run result counters | ~9 (`contribution_bundle_submit.py:105`–`:107`, `contribution_bundle_dry_run.py:116`–`:122`) | Report shapes, not validation. |
| `handle_not_found` / handle-conflict codes in `backend/app/services/scientific_read/` | ~12 | Read-path diagnostics, not `UploadWarning`s and not validation. |

Judgement calls in the excluded set: primary-key `Field(gt=0)` guards on `existing_*_id`
fields were excluded (an id is a positive integer — typing), whereas `year ge=1 le=3000` was
**included** in §1i because it encodes a plausibility range rather than a type.
