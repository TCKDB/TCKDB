# Validation check audit

Applies the rule in [ADR 0008 — Validation tiers: definitions block, expectations warn,
comparisons review](../adr/0008-validation-tiers-definitions-block-expectations-warn.md)
to every validation check that currently exists in the TCKDB backend, so a human can
review the placements and decide what to change.

**Audited tree:** `8802ada` (`origin/main`, "Restore the Stage 4 read/query surface (#84)").
Every `file:line` below was re-read against that tree by script; see
[§9](#9-how-the-anchors-were-verified). Line numbers for Pydantic validators point at the
`def` line, not the decorator.

**Read-only.** Nothing in this audit changes a check. Everything here is a proposal for review.

> **This audit supersedes the version drafted at `01d5570`.** Six PRs that change validation
> merged after that draft — [#77](https://github.com/TCKDB/TCKDB/pull/77),
> [#78](https://github.com/TCKDB/TCKDB/pull/78), [#79](https://github.com/TCKDB/TCKDB/pull/79),
> [#80](https://github.com/TCKDB/TCKDB/pull/80), [#81](https://github.com/TCKDB/TCKDB/pull/81),
> [#82](https://github.com/TCKDB/TCKDB/pull/82) — and between them they resolved most of what
> the earlier draft proposed, deleted one function it analysed at length, and rejected one of
> its recommendations on stated grounds. [§7](#7-what-the-earlier-draft-proposed-and-what-happened)
> records that disposition item by item, so the reasoning is not lost.

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

A third label, **backstop**, appears in [§3](#3-tier-3--hardfailreason-labels-a-stored-record-at-read-time).
It is not a tier classification but a *role*: the rule is owned by the blocking tier, and the
read-time copy exists only to catch records that never passed through the upload tier at all
(archive restore, migration, bulk importer, direct SQL). #78 introduced this concept and
documented it on the `HardFailReason` docstring; see
[§3.1](#31-the-backstop-role).

## Headline counts

| Tier | Rows enumerated | proposed ≠ current | unclear |
|---|---|---|---|
| 1. Pydantic schema validation (refuses at upload) | 176 | 1 | 3 |
| 2. `UploadWarning` (annotates an accepted payload) | 23 | 0 | 3 |
| 3. `HardFailReason` (labels a stored record at read time) | 25 | 2 | 0 |
| 4. `machine_review` (asynchronous, versioned rubrics) | 0 | — | — |
| **Total** | **224** | **3** | **6** |

The six unclear rows collapse into four questions, discussed in [§6.3](#63-unclear): the three
contested tier-2 comparison warnings share one argument, and the two `ConformerIn` rows share
another.

A tier-1 "row" is one validator, except in §1i, §1j and §1k where one row covers a family of
identical rules replicated across the CRUD, upload and bundle copies of the same schema —
the classification is the same for every copy and listing them separately would add ~70 rows
of noise. Around 115 further validators and the string/length `Field(...)` constraints were
excluded as purely structural; see [§8](#8-deliberately-excluded-as-purely-structural).

Plus 142 `EvidenceCheckSpec` graded checks in the trust rubrics
(`backend/app/services/trust/rubrics.py`), summarised in
[§5](#5-trust-rubric-graded-checks-142-not-a-tier) rather than tabulated: they grade
completeness and refuse nothing, which is correct for absence — with two exceptions noted
there.

Two structural findings that are not tier placements at all — a pair of unwired duplicate
schema modules, and a stale module docstring — are in [§6.4](#64-two-wire-package-copies-that-lag-the-live-schemas)
and [§6.5](#65-a-stale-module-docstring-that-the-earlier-draft-trusted).

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
| `CalculationOriginMetadata.validate_reused_result_constraints` | A calculation declared `origin_kind='reused_result'` that either names no source type or simultaneously claims `independent_ess_job=True` — a reused result by definition ran no independent job | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation_origin.py:87` | Pydantic | definition | Pydantic | yes |
| `CalculationConstraintPayload.validate_arity_and_distinct_atoms` | A geometric constraint whose atom count does not match its kind (bond=2, angle=3, dihedral/improper=4) or that repeats an atom | `.../fragments/calculation.py:58` | Pydantic | definition | Pydantic | yes |
| `CalculationScanCoordinatePayload.validate_arity_and_distinct_atoms` | The same arity/distinctness rule for a scan coordinate | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/scan.py:39` | Pydantic | definition | Pydantic | yes |
| `CalculationScanResultCreate.validate_scan_bundle` | A scan whose coordinate indices are not contiguous 1..dimension, duplicate constraint or point indices, or a point whose coordinate values name an undeclared coordinate | `.../fragments/scan.py:133` | Pydantic | definition | Pydantic | yes |
| `CalculationScanPointCreate.validate_unique_coordinate_values` | A scan point that reports the same coordinate twice | `.../fragments/scan.py:108` | Pydantic | definition | Pydantic | yes |
| `CalculationScanPointPayload.validate_geometry_exclusive` | A scan point that supplies both an inline geometry and a resolved `geometry_id` | `.../fragments/scan.py:93` | Pydantic | contract | Pydantic | yes |
| `GeometryPayload.validate_isotopes` | An isotope map with a non-positive (i.e. non-1-based) atom index or a mass number below 1 | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/geometry.py:28` | Pydantic | definition | Pydantic | yes |
| `CalculationDependencyBase.validate_not_self_edge` | A calculation-DAG edge from a calculation to itself | `backend/app/schemas/entities/calculation.py:172` | Pydantic | definition | Pydantic | yes |
| `CalculationOwnerRequiredMixin.validate_exactly_one_owner` | A calculation owned by neither, or both, a species entry and a transition-state entry | `backend/app/schemas/fragments/calculation.py:54` | Pydantic | contract | Pydantic | yes |
| `ComputedReactionCalculationIn.validate_result_matches_type` | Bundle mirror of the result-block matrix, extended to `scan_result`; also forbids scalar opt/freq/sp fields on a `scan` calculation | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:125` | Pydantic | contract | Pydantic | yes |
| `CalculationInBundle.validate_result_matches_type` | Same matrix on the computed-species bundle | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py:197` | Pydantic | contract | Pydantic | yes |
| `CalculationInBundle.reject_database_id_fields` | Database FK ids smuggled inside a bundle's opaque `parameters_json` (DR-0029) | `.../workflows/computed_species_upload.py:260` | Pydantic | contract | Pydantic | yes |

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
| `BundleKineticsIn.validate_model_kind_is_representable` | A pressure-dependent or multi-term functional form declared on the bundle payload, which carries only scalar Arrhenius fields and would persist a `plog`-tagged row with zero PLOG entries | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:882` | Pydantic | contract | Pydantic | yes |
| `BundleKineticsIn.validate_temperature_range` | `tmin_k > tmax_k` (bundle) | `.../workflows/computed_reaction_upload.py:872` | Pydantic | definition | Pydantic | yes |
| `BundleKineticsIn.validate_pressure_context` | Apparent-pressure context with no pressure (bundle) | `.../computed_reaction_upload.py:817` | Pydantic | definition | Pydantic | yes |
| `BundleKineticsIn.validate_ea_pair` | Ea without units (bundle) | `.../computed_reaction_upload.py:844` | Pydantic | definition | Pydantic | yes |
| `BundleKineticsIn.validate_a_uncertainty_kind` | Uncertainty/kind pairing and multiplicative f ≥ 1 (bundle) | `.../computed_reaction_upload.py:852` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsCreate.validate_parameterization_matches_model` | A network-kinetics row whose declared model kind has no matching coefficient block (CRUD) | `backend/app/schemas/entities/network_pdep.py:615` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsBase.validate_temperature_range` / `validate_pressure_range` | Inverted T or P bounds on network kinetics (CRUD) | `backend/app/schemas/entities/network_pdep.py:582`, `:592` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsUpdate.validate_temperature_range` / `validate_pressure_range` | The same on update | `backend/app/schemas/entities/network_pdep.py:676`, `:686` | Pydantic | definition | Pydantic | yes |

### 1c. Thermo

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `ThermoUploadRequest.validate_has_scientific_content` | A thermo upload carrying only identity and provenance — no scalar H298/S298, no NASA-7, no NASA-9 intervals, no Wilhoit, no tabulated points | `backend/app/schemas/workflows/thermo_upload.py:322` | Pydantic | contract | Pydantic | yes |
| `ThermoUploadRequest.validate_representation_consistency` | Two temperature-dependent fits on one record (NASA-7 + NASA-9 + Wilhoit are mutually exclusive), non-contiguous NASA-9 interval indices, or a `model_kind` that disagrees with the populated block | `.../thermo_upload.py:347` | Pydantic | contract | Pydantic | yes |
| `ThermoUploadRequest.validate_group_additivity_origin` | A group-additivity breakdown attached to a record whose origin is not `estimated` — GA *is* the estimation method | `.../thermo_upload.py:304` | Pydantic | definition | Pydantic | yes |
| `ThermoUploadRequest.validate_temperature_range` | `tmin_k > tmax_k` | `.../thermo_upload.py:219` | Pydantic | definition | Pydantic | yes |
| `ThermoUploadRequest.validate_unique_points` | Two tabulated thermo points at the same temperature | `.../thermo_upload.py:229` | Pydantic | definition | Pydantic | yes |
| `ThermoNASABase.validate_temperature_bounds` | A NASA-7 block with partially supplied bounds, or bounds that are not strictly t_low < t_mid < t_high | `schemas/python/tckdb-schemas/tckdb_schemas/thermo.py:82` | Pydantic | definition | Pydantic | yes |
| `ThermoNASA9IntervalBase.validate_interval_bounds` | A NASA-9 interval with `t_max_k <= t_min_k` | `.../tckdb_schemas/thermo.py:136` | Pydantic | definition | Pydantic | yes |
| `ThermoBase.validate_temperature_range` / `ThermoUpdate.validate_temperature_range` | Inverted bounds on the CRUD thermo schemas | `backend/app/schemas/entities/thermo.py:199`, `:276` | Pydantic | definition | Pydantic | yes |
| `ThermoCreate.validate_unique_points` | Duplicate tabulated temperature (CRUD) | `backend/app/schemas/entities/thermo.py:225` | Pydantic | definition | Pydantic | yes |
| `ThermoInBundle.validate_has_scientific_content` | A bundle thermo block with no scalar value, no NASA block and no points | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py:381` | Pydantic | contract | Pydantic | yes |
| `ThermoInBundle.validate_temperature_range` / `validate_unique_points` | Inverted bounds / duplicate temperatures (bundle) | `.../workflows/computed_species_upload.py:354`, `:364` | Pydantic | definition | Pydantic | yes |
| `BundleThermoIn.validate_temperature_range` | Inverted bounds (reaction bundle) | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:297` | Pydantic | definition | Pydantic | yes |
| `AppliedGroupAdditivityUploadPayload.validate_has_components` | A GA breakdown with no component contributions — a decomposition into nothing | `backend/app/schemas/workflows/group_additivity_upload.py:86` | Pydantic | contract | Pydantic | yes |

### 1d. Statmech

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `StatmechUploadRequest.validate_scientific_interpretation` | A rotor-aware `statmech_treatment` (`rrho_1d` / `rrho_nd` / `rrho_1d_nd`) that lists no torsions — a hindered-rotor treatment is *defined* by the rotors it treats | `backend/app/schemas/workflows/statmech_upload.py:255` | Pydantic | definition | Pydantic | yes |
| `ConformerUploadStatmechPayload.validate_scientific_interpretation` | The same rule on the conformer upload path | `backend/app/schemas/workflows/conformer_upload.py:98` | Pydantic | definition | Pydantic | yes |
| `BundleStatmechIn.validate_scientific_interpretation` | The same rule on the computed-reaction bundle | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:422` | Pydantic | definition | Pydantic | yes |
| `StatmechInBundle.validate_scientific_interpretation` | The same rule on the computed-species bundle | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py:551` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionIn.validate_coordinates` | A torsion whose coordinate count differs from its declared dimension, or whose coordinate indices are not contiguous 1..dimension | `backend/app/schemas/workflows/statmech_upload.py:107` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCreate.validate_coordinates` | The same (CRUD) | `backend/app/schemas/entities/statmech.py:158` | Pydantic | definition | Pydantic | yes |
| `BundleStatmechTorsionIn.validate_coordinates` / `StatmechTorsionInBundle.validate_coordinates` | The same (bundles) | `.../workflows/computed_reaction_upload.py:346`, `.../workflows/computed_species_upload.py:447` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCoordinateBase.validate_distinct_atoms` | A torsion dihedral that repeats an atom index — four distinct atoms define a dihedral | `backend/app/schemas/entities/statmech.py:64` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCoordinateUpdate.validate_distinct_atoms_when_complete` | The same, once all four indices are present on update | `backend/app/schemas/entities/statmech.py:109` | Pydantic | definition | Pydantic | yes |
| `StatmechTorsionCoordinateIn.validate_distinct_atoms` | The same on the shared upload fragment | `schemas/python/tckdb-schemas/tckdb_schemas/statmech_bits.py:36` | Pydantic | definition | Pydantic | yes |
| `StatmechUploadRequest.validate_electronic_levels` / `ConformerUploadStatmechPayload.validate_electronic_levels` | Duplicate `level_index` in an electronic-level manifold | `backend/app/schemas/workflows/statmech_upload.py:193`, `backend/app/schemas/workflows/conformer_upload.py:89` | Pydantic | definition | Pydantic | yes |
| `StatmechUploadRequest.validate_unique_torsion_indices` and bundle equivalents | The same torsion declared twice | `backend/app/schemas/workflows/statmech_upload.py:246`, `.../workflows/computed_reaction_upload.py:413`, `.../workflows/computed_species_upload.py:532` | Pydantic | definition | Pydantic | yes |
| `StatmechCreate.validate_nested_uniqueness` | Duplicate torsion indices or duplicate (calculation, role) source links (CRUD) | `backend/app/schemas/entities/statmech.py:282` | Pydantic | definition | Pydantic | yes |

### 1e. Transport

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `TransportUploadPayload.validate_has_scientific_content` | A transport record carrying none of σ, ε/k, dipole, polarizability or rotational relaxation — it claims to be transport data while carrying no transport property. **Added by #78**; the earlier draft flagged its absence as the one tier-1 *gap* in the tree | `backend/app/schemas/workflows/transport_upload.py:66` | Pydantic | contract | Pydantic | yes |
| `TransportUploadPayload.validate_lj_pair` | A Lennard-Jones σ with no ε/k, or ε/k with no σ — the pair is one potential, not two independent numbers | `backend/app/schemas/workflows/transport_upload.py:99` | Pydantic | definition | Pydantic | yes |
| `TransportCreate.validate_lj_pair` | The same (CRUD) | `backend/app/schemas/entities/transport.py:92` | Pydantic | definition | Pydantic | yes |

Both validators sit on `TransportUploadPayload`, the shared base, so the nested upload paths
(conformer bundle, network PDep) inherit them and not only
`POST /api/v1/uploads/transport`.

### 1f. Transition state

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `validate_ts_evidence_set` | More than one IRC evidence record for a TS, or a *passing* record whose participant mappings do not name every reactant/product and account for every TS atom exactly once on both sides — a partial map cannot be passing evidence that the saddle connects the declared endpoints | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/ts_validation_evidence.py:80` | Pydantic | contract | Pydantic | yes |
| `TransitionStateValidationEvidenceIn.validate_participant_mapping` | An empty mapping, a blank participant key, a non-positive (non-1-based) atom index, or an atom repeated within one participant | `.../fragments/ts_validation_evidence.py:54` | Pydantic | definition | Pydantic | yes |
| `TransitionStateValidationEvidenceIn.validate_mapping_sides_are_paired` | A one-sided participant map — a reactant map with no product map cannot be checked for completeness | `.../fragments/ts_validation_evidence.py:69` | Pydantic | contract | Pydantic | yes |
| `TransitionStateUploadRequest.validate_validation_evidence` | IRC evidence on a standalone TS upload with no, or more than one, `irc` additional calculation to bind it to; or a `source_calculation_key` on a payload with no key namespace | `backend/app/schemas/workflows/transition_state_upload.py:171` | Pydantic | contract | Pydantic | yes |
| `BundleTransitionStateIn.validate_evidence_source_is_a_ts_irc_calculation` | Bundle TS evidence that names no calculation, names one the TS does not own, or names a non-`irc` calculation | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:681` | Pydantic | contract | Pydantic | yes |
| `ComputedReactionUploadRequest.validate_ts_validation_evidence` | Runs `validate_ts_evidence_set` against the bundle's reaction participant counts and TS atom count | `.../workflows/computed_reaction_upload.py:970` | Pydantic | contract | Pydantic | yes |
| `TransitionStateUploadRequest.validate_primary_opt_is_opt` | A TS upload whose primary calculation is not an `opt` | `backend/app/schemas/workflows/transition_state_upload.py:162` | Pydantic | contract | Pydantic | yes |
| `BundleTransitionStateIn.validate_primary_is_opt` / `TransitionStateIn.validate_primary_calc_is_opt` | The same on the reaction bundle and the PDep bundle | `.../workflows/computed_reaction_upload.py:646`, `backend/app/schemas/workflows/network_pdep_upload.py:268` | Pydantic | contract | Pydantic | yes |
| `TransitionStateUploadRequest.validate_additional_calculation_types` | An additional calculation of a type the TS upload does not accept | `backend/app/schemas/workflows/transition_state_upload.py:215` | Pydantic | contract | Pydantic | yes |
| `ConformerUploadRequest.validate_additional_calculation_types` | The same on the conformer upload | `backend/app/schemas/workflows/conformer_upload.py:173` | Pydantic | contract | Pydantic | yes |
| `TSReactionUpload.validate_reaction_family` | Non-canonical family with no source note (TS path) | `backend/app/schemas/workflows/transition_state_upload.py:75` | Pydantic | contract | Pydantic | yes |
| `ConformerIn.validate_primary_calc_is_opt` (PDep and reaction bundle) | A conformer whose primary calculation is not an `opt` | `backend/app/schemas/workflows/network_pdep_upload.py:111`, `.../workflows/computed_reaction_upload.py:255` | Pydantic | **unclear** | — | see [§6.3](#63-unclear) |
| `ConformerInBundle.validate_primary_is_opt` | The same on the computed-species bundle | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py:290` | Pydantic | **unclear** | — | see [§6.3](#63-unclear) |

### 1g. Pressure-dependent networks

Anchors are to `backend/app/schemas/workflows/network_pdep_upload.py`, which is the module
every route, workflow and worker imports. A second, unwired copy exists under
`schemas/python/tckdb-schemas/`; see [§6.4](#64-two-wire-package-copies-that-lag-the-live-schemas).

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `NetworkKineticsIn.validate_model_payload` | Exactly one model sub-block matching `model_kind`; a Chebyshev surface missing any of the four T/P bounds its reduced variables need; `stores_log10_k` on a PLOG (a Chebyshev-only concept); and `tabulated`, whose write path does not exist | `backend/app/schemas/workflows/network_pdep_upload.py:780` | Pydantic | contract | Pydantic | yes |
| `ChebyshevKineticsIn.validate_grid_dimensions` | A network Chebyshev coefficient matrix that is not n_T × n_P, or that contains a non-finite coefficient | `.../network_pdep_upload.py:647` | Pydantic | definition | Pydantic | yes |
| `PlogKineticsIn.validate_unique_pressure_index` | Two PLOG entries at the same (pressure, entry index) | `.../network_pdep_upload.py:709` | Pydantic | definition | Pydantic | yes |
| `NetworkSolveIn.validate_bath_composition_and_state_energies` | Bath-gas mole fractions that do not sum to 1.0 (abs tol 1e-9), duplicate state energies, or duplicate (state, collider) energy-transfer scopes | `.../network_pdep_upload.py:935` | Pydantic | definition | Pydantic | yes (tolerance noted in [§6.3](#63-unclear)) |
| `NetworkPDepUploadRequest.validate_mechanistic_channel_evidence` | Duplicate microreaction paths on a channel; a path naming a TS that belongs to a different microreaction; **and** three coverage rules — one state energy for every state, one ⟨ΔE⟩down entry for every (well, bath-gas collider) pair *unless the payload declares `scope='network_wide'`*, one barrier for every saddle-point channel path and none for a barrierless one | `.../network_pdep_upload.py:1283` | Pydantic | mixed: definition + **absence** | split | **partly** — the ⟨ΔE⟩down half is resolved by [ADR 0009](../adr/0009-record-what-energy-transfer-was-specified-over.md); the state-energy and barrier halves remain, see [§6.1](#61-tier-1-coverage-requirements-that-are-really-absence-1) |
| `NetworkPDepUploadRequest.validate_well_skipping_channels` | A `mechanism='well_skipping'` declaration whose endpoints *are* directly connected by the topology, or that is otherwise unsupported by the network graph | `.../network_pdep_upload.py:1356` | Pydantic | contract | Pydantic | yes |
| `EnergyTransferIn.validate_scope` | An energy-transfer entry whose declared `scope` and supplied keys disagree — a `per_well` entry naming no well or no collider, or a `network_wide` entry naming either. Reworked by [ADR 0009](../adr/0009-record-what-energy-transfer-was-specified-over.md): a network-wide ⟨ΔE⟩down is no longer refused, it is declared, stored and warned about. | `.../network_pdep_upload.py:547` | Pydantic | definition | Pydantic | yes |
| `StateEnergyIn.validate_energy_is_finite` | A non-finite (NaN/inf) state energy | `.../network_pdep_upload.py:586` | Pydantic | definition | Pydantic | yes |
| `ChannelBarrierIn.validate_barriers_are_finite` | A non-finite forward or reverse barrier | `.../network_pdep_upload.py:613` | Pydantic | definition | Pydantic | yes |
| `NetworkChannelIn.validate_source_ne_sink` | A channel from a state to itself | `.../network_pdep_upload.py:460` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsIn.validate_source_ne_sink` | Network kinetics with equal source and sink, or a half-supplied legacy endpoint pair with no channel key | `.../network_pdep_upload.py:770` | Pydantic | definition | Pydantic | yes |
| `NetworkChannelBase.validate_source_ne_sink` | The same on the CRUD channel schema | `backend/app/schemas/entities/network_pdep.py:141` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsIn.validate_ranges` / `NetworkSolveIn.validate_ranges` | Inverted T or P bounds on network kinetics and on a solve | `.../network_pdep_upload.py:845`, `:920` | Pydantic | definition | Pydantic | yes |
| `NetworkSolveBase.validate_temperature_range` / `validate_pressure_range` (+ `NetworkSolveUpdate` pair) | The same on the CRUD solve schemas | `backend/app/schemas/entities/network_pdep.py:320`, `:330`, `:398`, `:408` | Pydantic | definition | Pydantic | yes |
| `NetworkPDepUploadRequest.validate_states_connected` | A network whose channel graph leaves some states unreachable — two disconnected subnetworks deposited as one | `.../network_pdep_upload.py:1488` | Pydantic | contract | Pydantic | yes |
| `NetworkPDepUploadRequest.validate_no_unused_species` | A species defined in the payload that no state, microreaction or bath-gas entry references | `.../network_pdep_upload.py:1463` | Pydantic | contract | Pydantic | yes |
| `NetworkPDepUploadRequest.validate_unique_channels` / `validate_unique_channel_kinetics` | Duplicate channel keys; two entries of the same model kind on one channel (one Chebyshev **and** one PLOG on the same channel is legitimate and allowed) | `.../network_pdep_upload.py:1245`, `:1253` | Pydantic | definition | Pydantic | yes |
| `NetworkStateIn.validate_unique_participants` / `NetworkStateCreate.validate_unique_participants` | The same species listed twice in one network state | `.../network_pdep_upload.py:339`, `backend/app/schemas/entities/network_pdep.py:93` | Pydantic | definition | Pydantic | yes |
| `NetworkSolveIn.validate_unique_bath_gas` / `NetworkSolveCreate.validate_unique_bath_gases` | The same bath gas declared twice | `.../network_pdep_upload.py:928`, `backend/app/schemas/entities/network_pdep.py:356` | Pydantic | definition | Pydantic | yes |
| `NetworkMicroReactionIn.validate_reaction_family` | Non-canonical family with no source note (PDep path) | `.../network_pdep_upload.py:400` | Pydantic | contract | Pydantic | yes |
| `ConventionBlock.validate_other_requires_note` | An `other` energy-zero or correction convention with no note | `.../network_pdep_upload.py:566` | Pydantic | contract | Pydantic | yes |
| `NetworkCreate.validate_unique_links` | Duplicate reaction links or duplicate (species, role) links on a network (CRUD) | `backend/app/schemas/entities/network.py:101` | Pydantic | definition | Pydantic | yes |
| `NetworkKineticsCreate.validate_unique_plog_entries` / `validate_unique_points` | Duplicate PLOG (pressure, index) or duplicate tabulated (T, P) (CRUD) | `backend/app/schemas/entities/network_pdep.py:636`, `:647` | Pydantic | definition | Pydantic | yes |

### 1h. Energy corrections, provenance and identity

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `AppliedEnergyCorrectionUploadPayload.validate_role_scheme_kind_compatibility` | A correction whose application role demands a particular scheme kind but was given another (e.g. `aec_total` fed a `bac_petersson` scheme) | `schemas/python/tckdb-schemas/tckdb_schemas/energy_correction.py:222` | Pydantic | definition | Pydantic | yes |
| `AppliedEnergyCorrectionUploadPayload.validate_role_source_compatibility` | A frequency-scale-factor role given a scheme, or a scheme role given a scale factor | `.../tckdb_schemas/energy_correction.py:207` | Pydantic | definition | Pydantic | yes |
| `AppliedEnergyCorrectionUploadPayload.validate_exactly_one_provenance_source` | A correction carrying both, or neither, a scheme and a frequency scale factor | `.../tckdb_schemas/energy_correction.py:197` | Pydantic | contract | Pydantic | yes |
| `AppliedEnergyCorrectionUploadPayload.validate_fsf_requires_source_calculation` | A frequency scale factor with no source calculation naming the freq job it was applied to | `.../tckdb_schemas/energy_correction.py:254` | Pydantic | contract | Pydantic | yes |
| `AppliedEnergyCorrectionBase.validate_exactly_one_target` | A correction targeting both, or neither, a species entry and a reaction entry (CRUD) | `backend/app/schemas/entities/energy_correction.py:264` | Pydantic | contract | Pydantic | yes |
| `AppliedEnergyCorrectionBase.validate_exactly_one_provenance_source` / `validate_role_source_compatibility` / `validate_fsf_requires_source_calculation` | The CRUD equivalents of the three rules above | `backend/app/schemas/entities/energy_correction.py:275`, `:285`, `:302` | Pydantic | contract | Pydantic | yes |
| `EnergyCorrectionSchemeCreate.validate_unique_atom_params` / `..._bond_params` / `..._component_params` and the `EnergyCorrectionSchemeRef` trio | Two corrections for the same element, bond key, or (component kind, key) in one scheme — an ambiguous correction library | `backend/app/schemas/entities/energy_correction.py:113`, `:120`, `:127`; `.../tckdb_schemas/energy_correction.py:64`, `:71`, `:78` | Pydantic | definition | Pydantic | yes |
| `AppliedEnergyCorrectionCreate.validate_unique_components` / `AppliedEnergyCorrectionUploadPayload.validate_unique_components` | The same component contribution counted twice in one applied correction | `backend/app/schemas/entities/energy_correction.py:320`, `.../tckdb_schemas/energy_correction.py:266` | Pydantic | definition | Pydantic | yes |
| `ExecutionEnvironmentManifestPayload.validate_closure` | A reproducibility manifest that is not self-consistent: a pinned runtime with no executable digest or fewer than two closure entries, a closure that does not contain the exact executable / conda lockfile / container image it claims, a container closure digest that differs from the OCI image digest, or HPC closure digests that do not match the declared environment | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/execution_environment.py:276` | Pydantic | contract | Pydantic | yes |
| `ContainerRuntime.validate_image` | A container reference that is not an immutable `@sha256:<64 hex>` OCI digest — a mutable tag cannot pin an environment | `.../fragments/execution_environment.py:166` | Pydantic | contract | Pydantic | yes |
| `DescribedRuntime.unique_modules` / `HPCModuleRuntime.unique_modules` | The same module/version declared twice in an environment description | `.../fragments/execution_environment.py:147`, `:197` | Pydantic | definition | Pydantic | yes |
| `LiteratureUploadRequest.validate_identifier_or_manual_fields` | A literature submission with neither a DOI/ISBN to resolve nor the minimum manual pair (kind + title) | `schemas/python/tckdb-schemas/tckdb_schemas/literature.py:77` | Pydantic | contract | Pydantic | yes |
| `LiteratureCreate.validate_unique_authors` | The same author, or the same author position, listed twice on one reference | `backend/app/schemas/entities/literature.py:92` | Pydantic | definition | Pydantic | yes |
| `ReproducibilityAssessmentAppend.validate_assessor_identity` | A curator assessment with no user, or a system assessment attributed to one | `backend/app/schemas/entities/reproducibility_assessment.py:47` | Pydantic | contract | Pydantic | yes |
| `MolecularPropertyObservationBase._at_least_one_value_representation` | An observation with no scalar, vector or tensor value | `backend/app/schemas/entities/molecular_property_observation.py:84` | Pydantic | contract | Pydantic | yes |
| `MolecularPropertyObservationBase._scalar_value_requires_unit` | A scalar observation with no unit | `.../entities/molecular_property_observation.py:97` | Pydantic | definition | Pydantic | yes |
| `MolecularPropertyObservationBase._property_kind_other_requires_label` | `property_kind='other'` with no label saying what was observed | `.../entities/molecular_property_observation.py:105` | Pydantic | contract | Pydantic | yes |
| `ReactionParticipantUpload.validate_reference_choice` | A participant supplying both, or neither, an existing species-entry id and inline species content | `backend/app/schemas/workflows/reaction_upload.py:24` | Pydantic | contract | Pydantic | yes |
| `ReactionUploadRequest.validate_reaction_family` / `ComputedReactionUploadRequest.validate_reaction_family` | Non-canonical family with no source note | `backend/app/schemas/workflows/reaction_upload.py:59`, `.../workflows/computed_reaction_upload.py:994` | Pydantic | contract | Pydantic | yes |
| `ContributionBundleV0.validate_records_match_kind` | A v0 contribution bundle whose declared kind carries no matching records, or that mixes thermo and kinetics | `backend/app/schemas/workflows/contribution_bundle.py:204` | Pydantic | contract | Pydantic | yes |
| `ContributionBundleV0.validate_local_ref_keys` | A malformed local-ref key, or one whose label is a bare number (a raw DB primary key masquerading as a portable ref) | `.../workflows/contribution_bundle.py:231` | Pydantic | contract | Pydantic | yes |
| `BundleManifest.validate_unique_paths` | Two manifest entries for the same file path | `.../workflows/contribution_bundle.py:175` | Pydantic | contract | Pydantic | yes |
| `ArtifactIn._check_filename` | A filename that is unsafe or inconsistent with the declared artifact kind | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/artifact.py:113` | Pydantic | contract | Pydantic | yes |

### 1i. Numeric domain bounds declared as `Field(...)` constraints

Each of these refuses a physically impossible value at parse time. All are definitional and
all agree with their current tier; they are grouped because the rule is the same in every row.

| Constraint | What it detects | file:line | Classification |
|---|---|---|---|
| `multiplicity: int = Field(ge=1)` | A spin multiplicity below 1 — 2S+1 is a positive integer | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/identity.py:72`; `backend/app/schemas/workflows/transition_state_upload.py:130`; `backend/app/schemas/workflows/network_pdep_upload.py:243`; `backend/app/schemas/entities/transition_state.py:76`, `:96`; `backend/app/schemas/entities/species.py:12`, `:25` | definition |
| `external_symmetry`, `optical_isomers`, torsion `symmetry_number`, torsion `dimension` `Field(ge=1)` | A symmetry number, optical-isomer count or rotor dimension below 1 — these are counts of indistinguishable configurations | `backend/app/schemas/workflows/statmech_upload.py:94`, `:97`, `:162`, `:174`; `backend/app/schemas/workflows/conformer_upload.py:63`, `:72`; `backend/app/schemas/entities/statmech.py:136`, `:139`, `:256`, `:309`; `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py:437`, `:440`, `:511`, `:512` | definition |
| `rotational_constant_{a,b,c}_cm1: Field(gt=0)` | A non-positive rotational constant | `backend/app/schemas/workflows/statmech_upload.py:163`–`:165`; `.../workflows/computed_species_upload.py:519`–`:521` | definition |
| electronic level `energy_cm1: Field(ge=0)`, `degeneracy: Field(ge=1)`, `level_index: Field(ge=1)` | A negative excitation energy above the ground level, or a degeneracy below 1 | `backend/app/schemas/entities/statmech.py:220`–`:222`; `backend/app/schemas/workflows/conformer_upload.py:44`–`:46` | definition |
| `tmin_k` / `tmax_k` / `t_min_k` / `t_max_k`: `Field(gt=0)` | A non-positive absolute temperature | `backend/app/schemas/entities/kinetics.py:102`–`:103`, `:209`–`:210`; `backend/app/schemas/entities/thermo.py:66`–`:67`, `:193`–`:194`, `:270`–`:271`; `backend/app/schemas/workflows/kinetics_upload.py:188`–`:189`, `:427`–`:428`; `backend/app/schemas/workflows/thermo_upload.py:157`–`:158`; `backend/app/schemas/workflows/network_pdep_upload.py:754`–`:755`, `:887`–`:888`; `schemas/python/tckdb-schemas/tckdb_schemas/thermo.py:122`–`:123`; `.../workflows/computed_species_upload.py:341`–`:342` | definition |
| `pressure_bar` / `pmin_bar` / `pmax_bar` / `reference_pressure_bar`: `Field(gt=0)` | A non-positive absolute pressure | `backend/app/schemas/workflows/kinetics_upload.py:144`, `:190`–`:191`, `:438`; `backend/app/schemas/workflows/thermo_upload.py:154`; `backend/app/schemas/workflows/network_pdep_upload.py:686`, `:756`–`:757`, `:889`–`:890` | definition |
| `mole_fraction: Field(gt=0, le=1)` | A bath-gas mole fraction outside (0, 1] | `backend/app/schemas/workflows/network_pdep_upload.py:519`; `backend/app/schemas/entities/network_pdep.py:177` | definition |
| `alpha0_cm_inv: Field(gt=0)`, `t_ref_k: Field(gt=0)` | A non-positive ⟨ΔE⟩down or reference temperature | `backend/app/schemas/workflows/network_pdep_upload.py:535`, `:537` | definition |
| `sigma_angstrom` / `epsilon_over_k_k`: `Field(gt=0)`; `rotational_relaxation: Field(ge=0)` | Non-positive Lennard-Jones parameters, negative rotational relaxation | `backend/app/schemas/entities/transport.py:69`–`:74`, `:123`–`:128`; `backend/app/schemas/workflows/transport_upload.py:51`–`:56` | definition |
| `cp0_j_mol_k` / `cp_inf_j_mol_k`: `Field(ge=0)`, `b_k: Field(gt=0)` | Negative Wilhoit heat-capacity limits or a non-positive Wilhoit scaling temperature | `backend/app/schemas/entities/thermo.py:89`–`:91`; `schemas/python/tckdb-schemas/tckdb_schemas/thermo.py:165`–`:167` | definition |
| `h298_uncertainty_kj_mol` / `s298_uncertainty_j_mol_k`: `Field(ge=0)` | A negative uncertainty | `backend/app/schemas/entities/thermo.py:182`–`:183`, `:259`–`:260`; `backend/app/schemas/workflows/thermo_upload.py:137`–`:138`; `.../workflows/computed_species_upload.py:339`–`:340` | definition |
| `degeneracy: Field(gt=0, allow_inf_nan=False)` (reaction path degeneracy) | A non-positive or non-finite reaction-path degeneracy | `backend/app/schemas/workflows/kinetics_upload.py:430`; `backend/app/schemas/entities/kinetics.py:105`, `:212` | definition |
| `efficiency: Field(ge=0)` | A negative third-body collision efficiency | `backend/app/schemas/workflows/kinetics_upload.py:137` | definition |
| `n_temperature` / `n_pressure`: `Field(ge=1)` | A Chebyshev grid with a zero-length axis | `backend/app/schemas/workflows/kinetics_upload.py:186`–`:187`; `backend/app/schemas/workflows/network_pdep_upload.py:642`–`:643`; `backend/app/schemas/entities/network_pdep.py:443`–`:444` | definition |
| `stoichiometry: Field(ge=1)`, `participant_index: Field(ge=1)`, `count: Field(ge=1)`, `grain_count: Field(ge=1)` | Non-positive stoichiometric coefficients, 1-based participant slots, GA group counts, master-equation grain counts | `backend/app/schemas/entities/reaction.py:82`, `:90`, `:127`, `:140`; `backend/app/schemas/reads/scientific_network_composition.py:14`; `backend/app/schemas/workflows/network_pdep_upload.py:316`, `:893`; `backend/app/schemas/workflows/group_additivity_upload.py:62` | definition |
| torsion / constraint atom indices: `Field(ge=1)` | A non-1-based atom index | `backend/app/schemas/entities/statmech.py:57`–`:61`, `:102`–`:106`; `schemas/python/tckdb-schemas/tckdb_schemas/statmech_bits.py:29`–`:33` | definition |
| `opt_n_steps: Field(ge=0)` | A negative optimisation step count | `schemas/python/tckdb-schemas/tckdb_schemas/shared/calculation_in.py:85` | definition |
| frequency-scale-factor `value: Field(gt=0)` | A non-positive scale factor | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/refs.py:148`; `backend/app/schemas/entities/energy_correction.py:164` | definition |
| `year: Field(ge=1, le=3000)` | A publication year outside a plausible calendar range | `backend/app/schemas/entities/literature.py:53`, `:109`; `schemas/python/tckdb-schemas/tckdb_schemas/literature.py:45` | contract |

### 1j. Payload referential-integrity contracts

Local-key namespaces are a contract between producer and server: a key must be unique, and
every reference must resolve. Violating one is not a scientific error, but it is an internal
contract violation, so the blocking tier is correct for all of them. Listed for
exhaustiveness; every row is *classification = contract, proposed = Pydantic, agrees = yes*.

| Check | file:line |
|---|---|
| `NetworkPDepUploadRequest.validate_unique_keys` (species / state / microreaction / TS keys unique; calculation and geometry keys globally unique) | `backend/app/schemas/workflows/network_pdep_upload.py:1031` |
| `NetworkPDepUploadRequest.validate_key_references` (state→species, channel→state, microreaction→species, TS→microreaction, TS evidence→own `irc` calc, calc→geometry, species statmech source and torsion-scan keys scoped to that species's own calcs and to `scan` type, bath gas→species, solve source calcs, channel kinetics→channel) | `.../network_pdep_upload.py:1071` |
| `NetworkSpeciesIn.validate_species_calc_geometry_key` / `validate_species_calc_geometry_belongs_to_conformer` | `.../network_pdep_upload.py:151`, `:162` |
| `BundleSpeciesIn.validate_calc_geometry_keys` / `validate_calc_geometry_belongs_to_conformer` | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:496`, `:506` |
| `ComputedReactionUploadRequest.validate_unique_keys` / `validate_species_key_refs` / `validate_calculation_key_refs` (incl. no self-edges in `depends_on`, statmech torsion scan keys must be `scan` type) | `.../workflows/computed_reaction_upload.py:1010`, `:1038`, `:1056` |
| `ComputedReactionCalculationIn.validate_constraint_indices_union_unique` | `.../workflows/computed_reaction_upload.py:184` |
| `CalculationInBundle.validate_constraints` | `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py:233` |
| `CalculationWithResultsPayload.validate_constraint_indices_unique` | `schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py:751` |
| `ComputedSpeciesUploadRequest.validate_unique_conformer_keys` / `validate_unique_calculation_keys_global` / `validate_dependency_keys_resolve` / `validate_thermo_source_keys_resolve` / `validate_statmech_source_keys_resolve` / `validate_statmech_torsion_scan_keys_resolve` / `validate_top_level_applied_correction_source_keys_resolve` | `.../workflows/computed_species_upload.py:617`, `:624`, `:639`, `:688`, `:711`, `:724`, `:745` |
| `StatmechUploadRequest.validate_unique_calculation_keys` / `validate_source_calculation_keys_exist` / `validate_unique_source_calculation_pairs` / `validate_torsion_scan_calculation_keys` | `backend/app/schemas/workflows/statmech_upload.py:202`, `:226`, `:237`, `:290` |
| `ThermoUploadRequest.validate_unique_calculation_keys` / `validate_source_calculation_keys_exist` / `validate_unique_source_calculation_pairs` / `validate_applied_correction_source_calc_keys` | `backend/app/schemas/workflows/thermo_upload.py:236`, `:260`, `:275`, `:288` |
| `ThermoSourceCalculationIn.validate_exactly_one_reference` | `backend/app/schemas/workflows/thermo_upload.py:99` |
| `TransportUploadRequest.validate_unique_calculation_keys` / `validate_source_calculation_keys_exist` / `validate_unique_source_calculation_pairs` | `backend/app/schemas/workflows/transport_upload.py:166`, `:190`, `:203` |
| `BundleStatmechIn.validate_unique_source_calculation_pairs`, `StatmechInBundle.validate_unique_source_calculation_pairs`, `ThermoInBundle.validate_unique_source_calculation_pairs`, `BundleKineticsIn.validate_unique_source_calculation_pairs` | `.../workflows/computed_reaction_upload.py:403`, `.../workflows/computed_species_upload.py:541`, `:371`, `.../workflows/computed_reaction_upload.py:828` |
| `KineticsCreate.validate_unique_source_calculations`, `ThermoCreate.validate_unique_source_calculations`, `TransportCreate.validate_unique_source_calculations`, `NetworkSolveCreate.validate_unique_source_calculations` | `backend/app/schemas/entities/kinetics.py:174`, `backend/app/schemas/entities/thermo.py:234`, `backend/app/schemas/entities/transport.py:102`, `backend/app/schemas/entities/network_pdep.py:365` |

### 1k. Stationary-point consistency (added by #82)

`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py` is the single owner of the
physics, the codes and the threshold. It returns tiered `StationaryPointFinding` records
rather than raising, so one traversal serves both tiers: upload schemas call
`raise_for_blocking_findings` from a `model_validator`, and the route layer converts the
warning-tier findings into `UploadWarning`s (see [§2](#2-tier-2--uploadwarning-payload-accepted-annotated)).

The module splits one rule four ways, and only two of the four block:

| Declared | Rule | Tier | Code |
|---|---|---|---|
| `minimum` | `n_imag == 0` | **block** | `W_N_IMAG_CONTRADICTS_MINIMUM` (`.../stationary_point.py:88`) |
| `vdw_complex` | `n_imag == 0` *expected* | warn | same code, or `W_N_IMAG_HIGHER_ORDER_SADDLE` (`:93`) for ≥ 2 |
| transition state | `n_imag == 1` | **block** | `W_TS_N_IMAG_NOT_ONE` (`:113`) |
| transition state | `\|imag_freq_cm1\| >= 100 cm⁻¹` | warn | `W_TS_IMAG_FREQ_TOO_SMALL` (`:118`) |

> **The transition-state row is superseded.**
> [ADR 0012](../adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md)
> retired `n_imag == 1` and `W_TS_N_IMAG_NOT_ONE` with it. What blocks now is a
> contract — at least one imaginary mode, exactly one designated the reaction
> coordinate, and no undeclared mode stiffer than it — while extra imaginary
> modes are judged by magnitude against a tolerance read from the recorded
> execution provenance. The minimum and van der Waals rows are unchanged. The
> generated register at `docs/guides/scientific_check_register.md` is the
> current statement; this audit is pinned to its commit and is not maintained.

The threshold is the named constant `TS_IMAGINARY_FREQUENCY_MIN_CM1`
(`.../stationary_point.py:77`), not a literal, because 100 cm⁻¹ is a starting point rather
than a physical constant.

| Check | What it detects | file:line | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `validate_n_imag_matches_species_entry_kind` (7 sites) | A species entry declared `minimum` whose own frequency evidence reports any imaginary mode. A minimum has zero imaginary modes by definition, so no correct calculation produces the record as submitted | `backend/app/schemas/workflows/conformer_upload.py:212`; `backend/app/schemas/workflows/statmech_upload.py:215`; `backend/app/schemas/workflows/thermo_upload.py:249`; `backend/app/schemas/workflows/transport_upload.py:179`; `backend/app/schemas/workflows/network_pdep_upload.py:212`; `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_species_upload.py:679`; `.../workflows/computed_reaction_upload.py:555` | Pydantic | definition | Pydantic | yes |
| `validate_n_imag_is_one` (3 sites) | A transition state whose frequency evidence does not report exactly one imaginary mode — zero is a minimum, two or more is a higher-order saddle | `backend/app/schemas/workflows/transition_state_upload.py:253`; `backend/app/schemas/workflows/network_pdep_upload.py:294`; `.../workflows/computed_reaction_upload.py:669` | Pydantic | definition | Pydantic | yes |

The owning classes are, in order: `ConformerUploadRequest`, `StatmechUploadRequest`,
`ThermoUploadRequest`, `TransportUploadRequest`, `NetworkSpeciesIn`,
`ComputedSpeciesUploadRequest`, `BundleSpeciesIn`; and `TransitionStateUploadRequest`,
`TransitionStateIn`, `BundleTransitionStateIn`. The bundle checks sit on the nested species
and transition-state models rather than on the request, because those payloads carry both
kinds of entity and a request-level scan that ignored which entity owns a frequency would
reject every real transition state.

Two shared helpers carry the traversal:
`backend/app/schemas/workflows/stationary_point_seam.py:34` (`inline_calculation_findings`,
used by the three standalone product uploads) and
`backend/app/services/upload_reconciliation.py:296` (`stationary_point_warnings`, the
warning-tier conversion).

---

## 2. Tier 2 — `UploadWarning` (payload accepted, annotated)

Twenty-two `W_*` constants that can reach this tier, plus one inline code. (`W_TS_N_IMAG_NOT_ONE`
is a twenty-third constant but is blocking-only, so it appears in
[§1k](#1k-stationary-point-consistency-added-by-82) and not here.)

Eleven of the twenty-three are `W_MISSING_*`-style absences, which is what ADR 0008's prose
describes; the ADR says "twenty upload warnings" and "ten are `W_MISSING_*`", written against
an earlier tree.

| Code | What it detects | Constant / emit site | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `W_N_IMAG_CONTRADICTS_MINIMUM` | **Now reachable only for `vdw_complex`** — one imaginary mode on an entry declared a van der Waals complex. A vdW complex's intermolecular modes sit low enough that Hessian grid noise is comparable to the true curvature, so a small imaginary mode there is usually an artifact rather than evidence. For a covalently bound `minimum` the same fact blocks | const `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py:88`, emitted `backend/app/services/upload_reconciliation.py:309` | warning | expectation | warning | yes |
| `W_N_IMAG_HIGHER_ORDER_SADDLE` | Two or more imaginary modes on a `vdw_complex`. Blocking for `minimum` and for transition states | const `.../stationary_point.py:93`, emitted `.../upload_reconciliation.py:309` | warning | expectation | warning | yes |
| `W_N_IMAG_SUGGESTS_TS` | **Narrowed by #82.** A `vdw_complex` whose imaginary mode is at or above `TS_IMAGINARY_FREQUENCY_MIN_CM1` — too stiff to be an intermolecular mode, so it looks like a genuine reaction coordinate rather than the grid artifact the carve-out exists to tolerate | const `.../stationary_point.py:109`, emitted `.../upload_reconciliation.py:309` | warning | expectation | warning | yes |
| `W_TS_IMAG_FREQ_TOO_SMALL` | A transition state's single imaginary mode below 100 cm⁻¹ in magnitude. The saddle-point count is correct, so the record is accepted; a mode this soft is often an under-converged geometry, but flat and variational barriers genuinely produce them — which is why ADR 0008 names magnitude explicitly as a check that must not block | const `.../stationary_point.py:118`, emitted `.../upload_reconciliation.py:309` | warning | expectation | warning | yes |
| `W_CHARGE_MISMATCH` | Declared formal charge vs the charge **stated by the uploaded output log**, parsed by program (Gaussian, ORCA, Molpro, Psi4). Multiple conflicting declarations in one log yield no value rather than a guess | const `backend/app/services/charge_multiplicity_reconciliation.py:52`, emitted `:267` | warning | **definition-shaped** | warning | yes (contested — [§6.2](#62-two-read-time-labels-whose-fact-is-now-owned-by-the-blocking-tier-2), [§6.3](#63-unclear)) |
| `W_MULTIPLICITY_MISMATCH` | Declared spin multiplicity vs the multiplicity stated by the uploaded output log | const `.../charge_multiplicity_reconciliation.py:54`, emitted `:283` | warning | **definition-shaped** | warning | yes (contested) |
| `W_TERM_SYMBOL_MISMATCH` | Declared term symbol vs one *derived* from multiplicity plus point group / linearity (`deduce_term_symbol`, `backend/app/services/ess_species_deduction.py:129`) | const `backend/app/services/upload_reconciliation.py:82`, emitted via `:364` | warning | expectation | warning | yes |
| `W_ELECTRONIC_STATE_CONTRADICTS_METHOD` | Declared electronic state vs one inferred from the method and job keywords; the "ground" branch is a heuristic and multireference methods return no deduction at all | const `.../upload_reconciliation.py:81`, emitted via `:364` | warning | expectation | warning | yes |
| `W_FREQ_PARSED_NO_MODES` | A freq result produced by an automated ESS parser that ships no per-mode frequencies — the mode list was lost somewhere in the pipeline | const `.../upload_reconciliation.py:78`, emitted `:280` | warning | absence | warning | yes |
| `W_SP_ENERGY_MISMATCH` | A single-point energy that disagrees with the value re-derived from the attached output log beyond 1e-6 Ha | const `backend/app/services/sp_energy_reconciliation.py:46`, emitted `:162` | warning | definition-shaped, but see note | warning | yes (contested — [§6.3](#63-unclear)) |
| `W_SP_ENERGY_FILLED_FROM_LOG` | Records that TCKDB supplied a missing SP energy from the log rather than leaving it null | const `.../sp_energy_reconciliation.py:50`, emitted `:182` | warning | **notification** | warning | yes (not a check) |
| `W_MISSING_LITERATURE_PROVENANCE` | A non-computed (experimental/estimated) record with no literature anchor | const `backend/app/services/provenance_warnings.py:40`, emitted `:72` | warning | absence | warning | yes |
| `W_MISSING_SOFTWARE_RELEASE_PROVENANCE` | A computed record that does not name the electronic-structure or post-processing software that produced it | const `.../provenance_warnings.py:41`, emitted `:86` | warning | absence | warning | yes |
| `W_MISSING_WORKFLOW_TOOL_PROVENANCE` | A computed record that does not name the orchestration tool (e.g. ARC) | const `.../provenance_warnings.py:42`, emitted `:100` | warning | absence | warning | yes |
| `W_MISSING_LEVEL_OF_THEORY_PROVENANCE` | Computed kinetics with no electronic-energy level of theory to anchor its source SP calculations to | const `.../provenance_warnings.py:43`, emitted `:114` | warning | absence | warning | yes |
| `W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE` | Computed statmech that records no frequency scaling — null means "unknown", and 1.0 means "explicitly unscaled" | const `.../provenance_warnings.py:44`, emitted `:128` | warning | absence | warning | yes |
| `W_MISSING_STATMECH_SOURCE_CALCULATIONS` | Computed statmech with no linked source calculations, so the partition function cannot be traced to what it was derived from | const `.../provenance_warnings.py:53`, emitted `:215` | warning | absence | warning | yes |
| `W_MISSING_STATMECH_FREQUENCY_SOURCE` | Computed statmech for a species with rotational structure (so its Q uses vibrational modes) and no `freq`-role source calculation. Scoped to polyatomics so it does not fire on every monatomic | const `.../provenance_warnings.py:54`, emitted `:227` | warning | absence | warning | yes |
| `W_MISSING_KINETICS_INTERPRETATIONS` | Computed kinetics that does not say which partition functions in this database it was built from — legitimately absent for a rate read out of a CHEMKIN mechanism | const `.../provenance_warnings.py:55`, emitted `:328` | warning | absence | warning | yes |
| `W_MISSING_TUNNELING_APPLICATION` | A declared tunneling model with no typed evidence block, so the correction is a reported label that cannot be replayed | const `.../provenance_warnings.py:56`, emitted `:372` | warning | absence | warning | yes |
| `W_MISSING_TS_INTERPRETATION` | An interpretation set naming reactants and products but no transition state — a TST rate with no Q‡. Correctly non-blocking: a master-equation-fitted rate has no single dividing surface, which `network_kinetics_ref` declares | const `.../provenance_warnings.py:57`, emitted `:355` | warning | absence | warning | yes |
| `W_MISSING_TS_IRC_EVIDENCE` | A transition state deposited with no *passing* IRC evidence — the saddle is stored, but nothing in the deposit shows it connects the declared reactants and products | const `backend/app/services/transition_state_validation.py:29`, emitted `:86` | warning | absence | warning | yes |
| `reaction_family_not_applied` (inline literal) | A reaction family submitted on a TS-anchored kinetics upload, where the family lives on the shared reaction identity and is not modified by a kinetics deposit. (Note: if the stored family *disagrees* with the submitted one, the same block raises instead — `backend/app/workflows/kinetics.py:332`) | `backend/app/workflows/kinetics.py:322` | warning | **notification** | warning | yes (not a check) |

### 2.1 Scope of the charge/multiplicity pair

The earlier draft reported that `W_CHARGE_MISMATCH` and `W_MULTIPLICITY_MISMATCH` could not
fire at all, because the value they compared against was copied out of the same payload.
**That is fixed.** #77 built
`backend/app/services/charge_multiplicity_reconciliation.py`, which re-reads the pair from the
uploaded output log; #80 added Psi4; #81 then deleted the tautological
`deduce_charge_multiplicity` and removed the two codes from the deduction path, leaving the
log-based module as their sole owner.

Three scoping facts a reviewer should still know:

- The comparison runs only where an output-log artifact is present. With no log, or an
  unrecognised program, the outcome is `unverifiable` and nothing is reported —
  `parse_charge_multiplicity_from_log` returns `None` rather than guessing
  (`.../charge_multiplicity_reconciliation.py:165`).
- A log that declares the pair more than once with conflicting values (Gaussian counterpoise
  fragments, ORCA compound jobs, multi-state MRCI decks, Psi4 SAPT fragments) yields no value:
  `_unanimous` (`:93`) rejects rather than picking one and risking a fabricated mismatch.
- Molpro never declares the charge outright, so only an explicitly written `charge=` is
  trusted; a defaulted or derived charge is an inference and is not compared (`:143`).

Call sites are `backend/app/api/routes/calculations.py:684` (artifact upload hook),
`backend/app/workflows/computed_species.py:585` and
`backend/app/workflows/computed_reaction.py:158`, all via
`try_reconcile_charge_multiplicity_from_output_upload`
(`backend/app/services/charge_multiplicity_extraction.py:47`).

The classification is left at **warning** deliberately. The check is definition-shaped — a
declaration contradicting the evidence — but it is a *comparison against a re-parse*, and the
re-parse can be wrong in ways the module itself enumerates. That is the "the reference can be
inapplicable" failure mode ADR 0008 uses to keep comparisons out of the blocking tier. See
[§6.3](#63-unclear).

---

## 3. Tier 3 — `HardFailReason` (labels a stored record at read time)

Enum: `backend/app/services/trust/models.py:87`. Raise sites in
`backend/app/services/trust/evaluator.py` unless noted.

### 3.1 The backstop role

#78 added a table to the `HardFailReason` docstring (`.../trust/models.py:95`–`:129`) pairing
six reasons with the upload-tier validator that already refuses the same rule, and marking
each member `# backstop`. The reasoning, which this audit adopts:

> Under ADR 0008 the upload tier *owns* each of these rules; these reasons exist to catch
> records that never went through the upload tier at all — archive restore, data migrations,
> bulk importers, and direct SQL. That path is real: an archive-restore defect was found in
> this repository recently, and a record that entered that way has been validated by nothing.

The practical consequence is that a backstop firing is **not a routine grading outcome**. Every
path that can create such a record through the API already rejects it, so the record is
evidence of a data-integrity problem to be traced back to its ingestion path — unlike `sparse`
/ `unsupported`, which mean "incomplete but true" and are expected in normal operation.

This supersedes the earlier draft's recommendation that these reasons should "cite rather than
re-evaluate". They are marked **backstop** below rather than **duplicate**.

| Member | What it detects | Declared / raised | Current | Classification | Proposed | Agrees? |
|---|---|---|---|---|---|---|
| `calculation_missing` | The calculation row the read asked for does not exist | `backend/app/services/trust/models.py:132` / `backend/app/services/trust/evaluator.py:479` | hard fail | lookup | hard fail | yes |
| `kinetics_missing` | Same, for a kinetics row | `models.py:134` / `evaluator.py:503` | hard fail | lookup | hard fail | yes |
| `statmech_missing` | Same, for a statmech row | `models.py:135` / `evaluator.py:551` | hard fail | lookup | hard fail | yes |
| `thermo_missing` | Same, for a thermo row | `models.py:136` / `evaluator.py:527` | hard fail | lookup | hard fail | yes |
| `transport_missing` | Same, for a transport row | `models.py:137` / `evaluator.py:659` | hard fail | lookup | hard fail | yes |
| `transition_state_entry_missing` | Same, for a TS entry | `models.py:150` / `evaluator.py:559` (default arg) | hard fail | lookup | hard fail | yes |
| `calculation_rejected` | A calculation a curator marked `quality=rejected` | `models.py:133` / `evaluator.py:93` | hard fail | curation | hard fail | yes |
| `ts_entry_status_rejected` | A TS candidate whose status is `rejected` | `models.py:153` / `evaluator.py:599` | hard fail | curation | hard fail | yes |
| `geometry_validation_failed` | A calculation whose recorded geometry validation has status `fail` — reads a stored verdict, does not re-derive one | `models.py:145` / `evaluator.py:96` | hard fail | contract | hard fail | yes |
| `geometry_validation_failed_for_source_calculation` | The same verdict on any calculation supporting a TS entry | `models.py:156` / `evaluator.py:611` | hard fail | contract | hard fail | yes |
| `source_calculation_hard_failed_for_required_role` | A required-role source calculation (kinetics: reactant/product/TS energy, freq; thermo & statmech: opt, freq, and scan when torsions are present; transport: full-transport plus role-conditional dipole/polarizability) that is itself hard-failed | `models.py:147` / `evaluator.py:182`, `:290`, `:325`, `:330`, `:382` | hard fail | contract (propagation) | hard fail | yes |
| `all_source_calculations_hard_failed` | Every calculation supporting a TS entry is itself hard-failed | `models.py:155` / `evaluator.py:618` | hard fail | contract (propagation) | hard fail | yes |
| `missing_required_identity` | Kinetics with no reaction entry, or a reaction entry with no reactant or no product participant | `models.py:146` / `evaluator.py:158`, `:169` | hard fail | contract | hard fail (blocked at upload too) | yes |
| `species_entry_missing` | Thermo, statmech or transport with no owning species entry | `models.py:138` / `evaluator.py:277`, `:308`, `:368` | hard fail | contract | hard fail | yes |
| `transition_state_parent_missing` | A TS entry with no parent transition state | `models.py:151` / `evaluator.py:592` | hard fail | contract | hard fail | yes |
| `reaction_entry_missing` | A TS whose parent has no reaction entry | `models.py:152` / `evaluator.py:596` | hard fail | contract | hard fail | yes |
| `no_thermo_representation_present` | A thermo row with no scalar value, no complete NASA-7, no NASA-9 intervals, no Wilhoit and no populated points | `models.py:139` / `evaluator.py:280` | hard fail | contract | hard fail (**backstop** for `ThermoUploadRequest.validate_has_scientific_content`) | yes |
| `no_transport_property_present` | A transport row with no σ, ε/k, dipole, polarizability or rotational relaxation | `models.py:140` / `evaluator.py:371` | hard fail | contract | hard fail (**backstop** — gained its upload-tier owner in #78) | yes |
| `invalid_lj_pair` | Exactly one of σ / ε/k populated | `models.py:141` / `evaluator.py:374` | hard fail | definition | hard fail (**backstop** for `validate_lj_pair`) | yes |
| `invalid_external_symmetry` | Statmech external symmetry number below 1 | `models.py:143` / `evaluator.py:311` | hard fail | definition | hard fail (**backstop** for `external_symmetry: Field(ge=1)`) | yes |
| `invalid_torsion_dimension` | A torsion with dimension below 1 | `models.py:144` / `evaluator.py:314` | hard fail | definition | hard fail (**backstop** for torsion `dimension: Field(ge=1)`) | yes |
| `multiplicity_invalid` | A TS entry with multiplicity below 1 | `models.py:154` / `evaluator.py:602` | hard fail | definition | hard fail (**backstop** for `multiplicity: Field(ge=1)`) | yes |
| `invalid_temperature_range` | **Narrowed by #78 and #79.** Now only the definitional rule: `0 < tmin <= tmax` for a validity range (`validity_range_is_definitionally_invalid`, `evaluator.py:115`) and `0 < t_low < t_high` for a fitting interval (`fitting_interval_is_definitionally_invalid`, `evaluator.py:132`). The 10 000 K / 20 000 K sanity caps that used to fuse into this member now live only in the graded rubrics | `models.py:142` / `evaluator.py:172`, `:283` | hard fail | definition | hard fail | yes |
| `frequency_source_has_zero_imaginary_modes_for_validated_ts` | An optimized/validated TS whose representative freq result reports zero imaginary modes — that geometry is a minimum | `models.py:159` / `evaluator.py:630` | hard fail | **definition** | hard fail, **marked backstop** | **no** — see [§6.2](#62-two-read-time-labels-whose-fact-is-now-owned-by-the-blocking-tier-2) |
| `frequency_source_has_multiple_imaginary_modes_for_validated_ts` | An optimized/validated TS whose representative freq result reports more than one imaginary mode — a higher-order saddle | `models.py:162` / `evaluator.py:634` | hard fail | **definition** | hard fail, **marked backstop** | **no** — see [§6.2](#62-two-read-time-labels-whose-fact-is-now-owned-by-the-blocking-tier-2) |

`HardFailReason.result_block_missing_when_successful`, which the earlier draft listed as
"declared and never referenced", was **deleted by #78**. It is gone from the tree; a repo-wide
grep returns no hits.

### 3.2 Temperature ranges: what #78 and #79 changed

Worth recording because the earlier draft proposed exactly this and it is now done, with one
refinement the draft did not anticipate.

`invalid_temperature_range` used to assert `0 < tmin_k < tmax_k <= 10_000` in one condition.
#78 split off the cap as an expectation — shock-tube, detonation, plasma and re-entry chemistry
legitimately exceed 10 000 K, and a read-time label was publicly marking correct
high-temperature science `hard_failed`. The cap was **demoted, not deleted**: it still applies
in the graded rubrics (`backend/app/services/trust/rubrics.py:378` for kinetics,
`_MAX_THERMO_TEMPERATURE_K` at `.../trust/rubrics.py:672` for thermo), both
`EvidenceCheckKind.optional`, so a hot range lowers evidence completeness and appears in
`missing_checks` but can never force `hard_failed`.

#79 then split the definitional predicate itself in two, which the earlier draft did not
propose:

- `validity_range_is_definitionally_invalid` — `0 < tmin <= tmax`. Equality is **allowed**: a
  rate constant measured at 298 K has a one-point validity range, and TCKDB accepts
  experimental records of that shape. The upload schema and the `ck_*_tmin_le_tmax` CHECK
  constraints already permitted it; the read tier was the sole dissenter.
- `fitting_interval_is_definitionally_invalid` — `0 < t_low < t_high`. Equality stays rejected:
  a zero-width NASA-7 segment or NASA-9 interval is a degenerate polynomial piece.

---

## 4. Tier 4 — `machine_review`

**There are no scientific checks at this tier.** Unchanged since the earlier draft.

`_ACTIVE_RUBRICS` (`backend/app/services/machine_review/recipe.py:46`) is not an independent
set of rubrics. It is the six *trust* rubrics, imported solely so that their `version` integers
can be stamped onto a machine review as a currency key
(`ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS`, `.../machine_review/recipe.py:65`). Bumping a trust
rubric restales existing reviews; that is the entire function of the constant.

The findings themselves would come from a provider. The shipped providers are
`DisabledMachineReviewProvider` (`backend/app/services/machine_review/providers/disabled.py:24`,
returns `status=not_run`) and a test-only `fake` provider that the deployer-facing factory
refuses to build. `build_machine_review_provider`
(`backend/app/services/machine_review/providers/factory.py:70`) validates configuration for
`cloud` and `local` modes and then raises — no model call is implemented.

| Item | Status | file:line |
|---|---|---|
| `_ACTIVE_RUBRICS` | Six trust-rubric version pins, no checks of their own | `backend/app/services/machine_review/recipe.py:46` |
| `MachineReviewCategory` | The finding vocabulary a provider may emit: `provenance`, `units`, `geometry`, `kinetics`, `thermo`, `statmech`, `transport`, `transition_state_validation`, `calculation_parameters`, `consistency`, `schema_gap` | `backend/app/services/machine_review/schemas.py:63` |
| `cloud` / `local` provider | Config validated, then `NotImplementedError` | `backend/app/services/machine_review/providers/factory.py:86`, `:93` |

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

Two exceptions matter:

- **`single_imaginary_frequency_for_ts`** (`backend/app/services/trust/rubrics.py:2943`, runner
  `_check_ts_single_imaginary_frequency_for_ts` at `.../trust/rubrics.py:2744`, kind `required`)
  encodes "a transition state has exactly one imaginary frequency" a **third** time. Since #82
  the blocking tier owns that fact ([§1k](#1k-stationary-point-consistency-added-by-82)), so
  this graded copy and the two read-time labels in [§3](#3-tier-3--hardfailreason-labels-a-stored-record-at-read-time)
  are all downstream of an owner that now exists. Grading it is defensible — the rubric's job
  is to score completeness of evidence for records however they arrived — but the three copies
  should say so, and currently only the six #78 members are labelled.
- **`temperature_range_valid`** (`.../trust/rubrics.py:1742` for kinetics, `:1909` for thermo)
  is where the 10 000 K / 20 000 K caps landed after #78 demoted them. This is the intended
  home for an expectation and is correct; it is called out only because the same numbers used
  to force `hard_failed`.

Also out of scope but worth naming so it is not mistaken for a fifth tier: the
reproducibility rubric (`backend/app/services/reproducibility_rubric.py`) emits its own
diagnostic codes. Per ADR 0002 reproducibility is a judgement independent of trust; these
grade whether a record could be *re-run*, not whether its science is right.

---

## 6. Findings

### 6.1 Tier-1 coverage requirements that are really absence (1)

| Check | Reasoning |
|---|---|
| `NetworkPDepUploadRequest.validate_mechanistic_channel_evidence` (`backend/app/schemas/workflows/network_pdep_upload.py:1283`) | The validator mixes two kinds of rule. The structural half is definitional and should keep blocking: duplicate microreaction paths (`:1287`), and a channel path whose TS belongs to a different microreaction (`:1298`). The **coverage** half — "one state energy for every state" (`:1302`), "one ⟨ΔE⟩down for every (well, bath-gas collider) pair", "one barrier for every saddle-point path" — refuses a payload for *missing evidence*, which the ADR assigns to the warning tier. The "unexpected extra entry" direction of the same rule *is* a contract violation and can stay blocking. Proposal: split the validator, keep the structural half blocking, and demote the missing-coverage half to `UploadWarning`. |

**The ⟨ΔE⟩down third of this is now resolved**, by
[ADR 0009](../adr/0009-record-what-energy-transfer-was-specified-over.md). The diagnosis above
was right about the symptom and slightly wrong about the cure: the problem was not that the
coverage rule blocked, but that the payload had no way to *say* a single network-wide value was
what the run determined, so the only compliant response was to paste one number once per well.
`EnergyTransferIn.scope` lets the producer declare it; the coverage rule still blocks a partial
per-well set, and a network-wide declaration is accepted with the
`network_wide_energy_transfer_scope` warning.

The state-energy and channel-barrier coverage rules are untouched and remain the surviving
tier-1 disagreement.

### 6.2 Two read-time labels whose fact is now owned by the blocking tier (2)

| Check | Reasoning |
|---|---|
| `frequency_source_has_zero_imaginary_modes_for_validated_ts` (`backend/app/services/trust/models.py:159`, raised `backend/app/services/trust/evaluator.py:630`) | #82 gave "a transition state has exactly one imaginary mode" a blocking owner at the upload tier (`validate_n_imag_is_one`, three sites). #82 deliberately left this read-time label untouched, so it now grades a strictly narrower population: only rows deposited before #82, or rows that entered by a non-upload path. That is precisely the **backstop** role #78 defined for six other members — but these two carry no `# backstop` marker and no docstring entry, so a reader cannot tell them apart from a routine grading outcome. |
| `frequency_source_has_multiple_imaginary_modes_for_validated_ts` (`.../trust/models.py:162`, raised `.../trust/evaluator.py:634`) | Same. This is the duplication ADR 0008 names by name; it is now resolved in the direction the ADR requires (the blocking tier owns the fact), and what remains is undocumented rather than undecided. |

**Proposed (documentation only, no behaviour change):** add both members to the `HardFailReason`
docstring table with their upload-tier owner, and mark them `# backstop`, so all eight
backstops read the same way. Deleting them would be wrong for the same reason #78 gave: a
record restored from an archive has been validated by nothing.

The third copy at `backend/app/services/trust/rubrics.py:2943` ([§5](#5-trust-rubric-graded-checks-142-not-a-tier))
should be cross-referenced in the same edit.

### 6.3 Unclear

Honest unknowns rather than confident guesses.

| Item | Why it cannot be classified from the code |
|---|---|
| `ConformerIn.validate_primary_calc_is_opt` / `ConformerInBundle.validate_primary_is_opt` (`backend/app/schemas/workflows/network_pdep_upload.py:111`, `schemas/python/tckdb-schemas/tckdb_schemas/workflows/computed_reaction_upload.py:255`, `.../workflows/computed_species_upload.py:290`) | Whether "a conformer's primary calculation is an `opt`" is a **definition** in TCKDB's data model (conformers are optimized stationary points, per the conformer-group / torsional-basin design) or a **convention** that happens to hold for the deposits seen so far. If it is a convention, the check blocks legitimate deposits: a conformer taken from a crystal structure, an MD snapshot, or an externally supplied geometry has no `opt` job, and today those payloads are refused outright. Resolving this needs a product decision, not more code reading. Unchanged from the earlier draft. |
| `W_SP_ENERGY_MISMATCH` (`backend/app/services/sp_energy_reconciliation.py:46`) and the `W_CHARGE_MISMATCH` / `W_MULTIPLICITY_MISMATCH` pair (`backend/app/services/charge_multiplicity_reconciliation.py:52`, `:54`) | All three are formally declaration-vs-evidence contradictions, which the ADR rule would put at the blocking tier. All three are *comparisons against a re-parse*, and a re-parse can be wrong: a composite or corrected energy in the payload can legitimately differ from the raw energy line in a log, Molpro MRCI-F12 is unsupported for SP energy and Psi4 has no SP extractor at all (`.../sp_energy_reconciliation.py:114`), and Molpro never declares a charge outright. That is the "the reference can be inapplicable" failure mode the ADR uses to send comparisons to a non-blocking tier. Left at warning; flagged because the classification genuinely depends on how much the parser is trusted, and #77/#80 raised that trust without settling the question. |
| `NetworkSolveIn.validate_bath_composition_and_state_energies` mole-fraction sum (`backend/app/schemas/workflows/network_pdep_upload.py:935`, the sum itself at `:936`) | The rule (a composition sums to 1) is definitional. The **tolerance** — `rel_tol=0.0, abs_tol=1e-9` — is a numerical policy, not a scientific one, and would reject a composition transcribed at, say, six decimal places. Classified as definition; the tolerance is worth a separate look. Unchanged from the earlier draft. |
| Whether the unwired wire-package copies in [§6.4](#64-two-wire-package-copies-that-lag-the-live-schemas) are an in-progress migration or an accident | Nothing in the tree states an intent. There is no shim-identity test binding them to the live modules, no deprecation note, and no importer outside the wire package's own test. Contrast `CalculationIn` / `GeometryIn`, which `backend/tests/schemas/test_tckdb_schemas_shim_identity.py:92` *does* pin by object identity. Whether the backend copies are meant to become shims — as `computed_species_upload.py` and `computed_reaction_upload.py` already are — cannot be determined from the code. |

### 6.4 Two wire-package copies that lag the live schemas

Not a tier placement; a divergence risk found while re-deriving anchors.

`#84` added `schemas/python/tckdb-schemas/tckdb_schemas/workflows/network_pdep_upload.py` and
`schemas/python/tckdb-schemas/tckdb_schemas/transport.py`. Both are **near-copies of live
backend modules, not shims**, and both are behind:

| Wire-package copy | Live module | Validators missing from the copy |
|---|---|---|
| `schemas/python/tckdb-schemas/tckdb_schemas/workflows/network_pdep_upload.py` | `backend/app/schemas/workflows/network_pdep_upload.py` | `validate_n_imag_matches_species_entry_kind`, `validate_n_imag_is_one` (both #82), `validate_paths_match_mechanism`, `validate_well_skipping_channels` |
| `schemas/python/tckdb-schemas/tckdb_schemas/transport.py` | `backend/app/schemas/workflows/transport_upload.py` | `validate_has_scientific_content` (#78) |

**No behaviour is affected today.** Every route, workflow, worker and ingestion script imports
`app.schemas.workflows.network_pdep_upload`; the wire-package PDep copy is imported only by
`schemas/python/tckdb-schemas/tests/test_network_pdep_roundtrip.py:19`, and the wire-package
transport copy only by that same PDep copy. Verified by repo-wide grep for
`tckdb_schemas.workflows.network_pdep_upload` and `tckdb_schemas.transport`.

The risk is that the copies look authoritative — they sit in the package a client would install
— and a future switch of the import would silently drop four blocking checks, two of them the
imaginary-frequency definitions #82 was written to establish. Contrast the modules that *were*
migrated properly: `backend/app/schemas/workflows/computed_species_upload.py` is a 26-line
re-export shim, so there is exactly one copy of each validator.

Recorded as a finding, not a proposal: whether to converge them, delete them, or finish the
migration is the question in [§6.3](#63-unclear).

### 6.5 A stale module docstring that the earlier draft trusted

`backend/app/services/sp_energy_reconciliation.py:18`–`:20` says single-point-energy
extraction is "not yet wired" for "ORCA and Gaussian today". **That is false and has been since
#51**: `parse_sp_energy` exists for both
(`backend/app/services/orca_parameter_parser.py:1153`,
`backend/app/services/gaussian_parameter_parser.py:711`) and the dispatch calls them
(`.../sp_energy_reconciliation.py:110`, `:112`). The program with no SP extractor is **Psi4**
(`.../sp_energy_reconciliation.py:114`).

This matters to the audit because the earlier draft cited that docstring as evidence for
leaving `W_SP_ENERGY_MISMATCH` at the warning tier. The conclusion survives — the remaining
justifications (composite/corrected energies, unsupported Molpro methods, Psi4) are real — but
the stated reason was wrong. Documentation fix, outside this audit's scope; noted so the next
reader does not inherit it.

---

## 7. What the earlier draft proposed, and what happened

The draft written at `01d5570` listed fourteen rows where proposed ≠ current. Their disposition:

| Earlier proposal | Disposition |
|---|---|
| §6.1 `W_N_IMAG_CONTRADICTS_MINIMUM` → blocking | **Done, and refined, by #82.** Blocking for `minimum`; deliberately kept at the warning tier for `vdw_complex`, whose soft intermolecular modes make a small imaginary frequency a likely grid artifact. The draft did not distinguish the two kinds. |
| §6.1 `W_N_IMAG_HIGHER_ORDER_SADDLE` → blocking | **Done by #82**, folded into the blocking message for `minimum` and into `W_TS_N_IMAG_NOT_ONE` for transition states; still warns for `vdw_complex`. |
| §6.1 `W_CHARGE_MISMATCH` → blocking | **Superseded.** #77 gave the check a real log-derived comparand and #81 deleted the tautological deduction, so it can now fire; it is left at the warning tier because it is a comparison against a re-parse ([§6.3](#63-unclear)). |
| §6.1 `W_MULTIPLICITY_MISMATCH` → blocking | Same as above. |
| §6.2 split `validate_mechanistic_channel_evidence` | **Partly done.** The ⟨ΔE⟩down coverage rule was resolved by [ADR 0009](../adr/0009-record-what-energy-transfer-was-specified-over.md) — not by demoting it, but by giving the payload a way to declare a network-wide scope, which is what the run actually determined. The state-energy and barrier coverage rules are carried forward in [§6.1](#61-tier-1-coverage-requirements-that-are-really-absence-1). |
| §6.3 "`W_CHARGE_MISMATCH` and `W_MULTIPLICITY_MISMATCH` are unreachable; `deduce_charge_multiplicity` compares the payload against itself" | **Obsolete.** The analysis was correct for `01d5570`, and #81 acted on it by deleting `deduce_charge_multiplicity` outright. That function no longer exists; #77 had already built the log-based replacement. |
| §1e "no upload-tier owner for an empty transport record" | **Fixed by #78** — `TransportUploadPayload.validate_has_scientific_content`, on the shared base so nested paths are covered. |
| §6.4 `no_transport_property_present` → block at upload | **Fixed by #78**, as above; the read-time label stays as a backstop. |
| §6.4 `invalid_lj_pair` → "cite, don't re-derive" | **Rejected by #78, on stated grounds.** Reframed as a deliberate backstop for records that entered by a non-upload path. This audit adopts that reasoning; see [§3.1](#31-the-backstop-role). |
| §6.4 `invalid_external_symmetry` → cite | Rejected by #78; backstop. |
| §6.4 `invalid_torsion_dimension` → cite | Rejected by #78; backstop. |
| §6.4 `multiplicity_invalid` → cite | Rejected by #78; backstop. |
| §6.5 `no_thermo_representation_present` → cite | Rejected by #78; backstop. |
| §6.4 `invalid_temperature_range` → split ordering from caps | **Done by #78**, and split further by #79 into validity-range vs fitting-interval predicates, which the draft did not anticipate. See [§3.2](#32-temperature-ranges-what-78-and-79-changed). |
| §6.4 the two `frequency_source_*_imaginary_modes_for_validated_ts` → blocking | **Partly done by #82**, which put the blocking owner at the upload tier but deliberately left these labels alone. What remains is a documentation gap, carried forward as [§6.2](#62-two-read-time-labels-whose-fact-is-now-owned-by-the-blocking-tier-2). |
| §7 `result_block_missing_when_successful` is dead | **Deleted by #78**, with the reasoning that the check as named could not distinguish a calculation that produced no result (a contradiction) from one whose result type TCKDB does not yet model (an absence). |

Two draft claims were **wrong at the time**, not merely overtaken, and are corrected here:

- Its §7 cited the `sp_energy_reconciliation` module docstring as evidence that ORCA and
  Gaussian SP-energy extraction was unwired. The docstring says that; the code has contradicted
  it since #51. See [§6.5](#65-a-stale-module-docstring-that-the-earlier-draft-trusted).
- Its line anchors for `backend/app/services/upload_reconciliation.py` (`:31`, `:33`, `:45`,
  `:46`) pointed at warning-code constants. Those lines now hold import statements; the codes
  moved to `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py` (#82) and
  `backend/app/services/charge_multiplicity_reconciliation.py` (#77/#81).

---

## 8. Deliberately excluded as purely structural

Roughly **115 of the 359** validator decorators in `backend/app/schemas/` and
`schemas/python/tckdb-schemas/`, plus the string/length `Field(...)` constraints, were left
out. They encode no scientific or contractual rule:

| Category | Approx. count | Why excluded |
|---|---|---|
| `normalize_*` / `_normalize_*` / `strip_*` text normalizers (whitespace stripping, NFC, lower-casing names and versions, `normalize_optional_text` wrappers) | ~104 definitions | They transform values and reject nothing. Present on nearly every schema in the tree. |
| `min_length=1` / `max_length=N` on names, labels, notes, SMILES, term symbols | ~60 field constraints | "A name must be non-empty" is typing, not chemistry. |
| Free-text length caps on public search inputs (`_bound_participant_lengths`, `_bound_participant_refs`, `_bound_participant_smiles`) | 4 (`backend/app/schemas/reads/scientific_kinetics_search.py:111`, `backend/app/schemas/reads/scientific_reactions.py:84`, `backend/app/schemas/reads/scientific_network_kinetics_search.py:137`, `:144`) | Request-size guards on read endpoints; they protect the server, not the science. |
| Pagination and response-shape bounds (`offset ge=0`, `limit ge=1 le=200`, counters `ge=0`) | ~15 | API mechanics. |
| Deprecated-alias reconciliation (`_resolve_pressure_alias`) | 2 (`backend/app/schemas/reads/scientific_kinetics.py:94`, `backend/app/schemas/reads/scientific_kinetics_search.py:121`) | An API-compatibility rule about two spellings of one query parameter. |
| Identifier hygiene and secret-scanning in the execution-environment manifest (`_validate_locator`, `_validate_digest`, `_validate_safe_identifier`, the `_SECRET` regex on `DescribedRuntime.validate_description`) | 5 (`schemas/python/tckdb-schemas/tckdb_schemas/fragments/execution_environment.py:43`, `:46`, `:52`, `:63`, `:141`) | Format and security checks. The *structural* manifest rules that do encode a reproducibility contract (`validate_closure`, `ContainerRuntime.validate_image`, the two `unique_modules`) are included in §1h. |
| ORCID and DOI/ISBN format normalizers (`validate_orcid`, `normalize_orcid`) | 3 (`backend/app/schemas/entities/author.py:26`, `:62`; `schemas/python/tckdb-schemas/tckdb_schemas/utils.py:60`) | Identifier syntax. |
| SHA-256 `pattern=r"^[0-9a-f]{64}$"` constraints | 3 | Digest syntax. |
| Ingestion/dry-run result counters | ~9 | Report shapes, not validation. |
| `handle_not_found` / handle-conflict codes in `backend/app/services/scientific_read/` | 26 occurrences | Read-path diagnostics, not `UploadWarning`s and not validation. |

Judgement calls in the excluded set: primary-key `Field(gt=0)` guards on `existing_*_id`
fields were excluded (an id is a positive integer — typing), whereas `year ge=1 le=3000` was
**included** in §1i because it encodes a plausibility range rather than a type.

---

## 9. How the anchors were verified

Every `file:line` in this document was re-read against `8802ada` by two scripts, run from the
repository root. Neither is committed; they are reproduced here so the check can be repeated.

1. **Symbol ground truth.** An `ast`-based index of every class, method and module-level
   assignment under `backend/app`, `schemas/python` and `clients/python`, mapping
   `Class.method` → `path:def_line`. Every validator anchor in §§1a–1h, 1j, 1k and 2 was taken
   from that index rather than copied forward, so a validator that moved carries its new line
   automatically. The index found 7 486 symbol names.
2. **Anchor re-read.** A resolver that walks the rendered Markdown, expands the doc's
   shorthand (`.../fragments/calculation.py:194` and bare `:267` resolve against the last full
   path named with that basename), opens each file, and prints the line actually found. Every
   anchor resolves and every resolved line contains what the surrounding text says it does.

The `Field(...)` constraint anchors in §1i cannot come from a symbol index — they are field
declarations, not defs — so each was re-derived by grepping for the field name together with
its constraint (for example `grep -rn "multiplicity: int" --include=*.py | grep "ge=1"`) and
the full match list was used to rebuild the row.

Two facts to keep the scripts honest if this is repeated:

- `backend/app/schemas/workflows/computed_species_upload.py` and
  `computed_reaction_upload.py` are 26-line re-export shims; the real modules are under
  `schemas/python/tckdb-schemas/tckdb_schemas/workflows/`. A resolver that matches on basename
  alone will silently point at the shim and report a line past its end.
- `network_pdep_upload.py` and `transport.py` each exist twice with *different* content
  ([§6.4](#64-two-wire-package-copies-that-lag-the-live-schemas)). Anchors in this document
  always name the live backend module for those two.
