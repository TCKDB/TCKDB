# The scientific check register

**Generated. Do not edit by hand.** Regenerate with
`conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py`.

## What this is

The checks that encode chemistry rather than plumbing: elemental balance, charge
conservation, the imaginary-mode count of a saddle point, the bijectivity of an
atom map. String lengths, enum membership and `multiplicity >= 1` are not listed.
Every `file::name` here is resolved from the live function object at generation
time, and anchors name the function rather than a line.

## How to read a row

- **Asserts** — what the check requires, in one sentence.
- **Tier** — what happens when the check fires.
- **Code**, and **where it reaches a client** — the machine-readable code and
  the channel that carries it. An `error_envelope` code is raised through
  `CodedValidationError` and asserted by an end-to-end test.
- **Enforced at** — `file::qualified_name` for Python, or the constraint or
  trigger name for PostgreSQL, verified against live schema metadata. Where a
  constraint names the 409 code it returns, that code is printed with it.
- **Thresholds** — the numeric lines the check fires on. A constant is printed.
  A provenance-derived threshold is resolved per record from the execution
  provenance it carries, so it is printed as the resolver, the parameter keys
  it reads, the per-protocol table, and what it falls back to when those keys
  are absent.
- **Escape hatch** — how legitimate chemistry the check would otherwise refuse
  is deposited instead.
- **Recorded divergence** — where a check's guarantee is narrower than its name
  suggests.


## Tiers, and how many entries sit in each

| Tier | Entries | Meaning |
| --- | --- | --- |
| `block` | 43 | Refuses the payload. Reserved for a definition or a contract no correct calculation could violate. |
| `warn` | 25 | Accepts the payload and records a machine-readable warning. |
| `label` | 1 | Labels a stored record at read time without refusing anything, as a `HardFailReason` in the trust evaluator. |
| `review` | 8 | Referred to `machine_review`. Every cross-check against external reference data is here. |
| `structural` | 6 | Enforced by the shape of the schema, so a record violating it cannot be represented. |
| **total** | **83** | |

## Where a check's code reaches a client

| Channel | Entries | What a consumer can do with it |
| --- | --- | --- |
| `error_envelope` | 44 | the `code` field of the 422 error body — a client can branch on it |
| `upload_warning` | 26 | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |
| `trust_label` | 1 | a read-time trust label (`HardFailReason`), not any refusal |
| `database_constraint` | 2 | PostgreSQL only, so the refusal is a 409 rather than a 422 — named, where the constraint declares a rejection code |
| `none` | 10 | *nothing carries a code* |
| **total** | **83** | |

## Recorded divergences

Checks whose guarantee is narrower than their name suggests.

- **[9]** The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.
- **[10]** Every geometry linked to a calculation is made of the atoms and isotopes of the subject the calculation is filed under.
- **[13]** An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.
- **[17]** An imaginary mode other than a transition state's reaction coordinate, or any imaginary mode on a declared minimum, is judged by magnitude against the tolerance of the protocol that produced it.
- **[37]** A phenomenological k(T,P) declares whether it was computed or reported: a computed solve carries master-equation evidence and a reported one cites its publication.

## Entries

## Conservation across a reaction

### 1. The reactant and product sides of a reaction contain the same number of atoms of every element.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_mass_balance_failed` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_reaction_elemental_balance` — `backend/app/services/reaction_resolution.py::validate_reaction_elemental_balance`

**Escape hatch.** Declare a participant with `molecule_kind: pseudo`, which suspends the law for the whole reaction.

### 2. The summed formal charge of a reaction's reactants equals that of its products.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_charge_not_conserved` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_reaction_charge_conservation` — `backend/app/services/reaction_resolution.py::validate_reaction_charge_conservation`

**Escape hatch.** Declare the free electron as a participant (`molecule_kind: electron`, charge -1), or declare a `pseudo` participant to suspend the law.

### 3. A saddle point is made of exactly the atoms of the reaction it is declared to sit in.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_composition_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_transition_state_composition` — `backend/app/services/reaction_resolution.py::validate_transition_state_composition`

**Escape hatch.** Declare the extra species as participants, or declare a `pseudo` reactant to exempt the reaction.

### 4. A saddle point carries the same total charge as the reactants it sits between.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_charge_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_transition_state_composition` — `backend/app/services/reaction_resolution.py::validate_transition_state_composition`

**Escape hatch.** Omit the transition state's charge to skip the comparison, or declare a `pseudo` reactant, which also exempts it.

### 5. The saddle-point atoms an IRC mapping assigns to a declared participant are that participant's own atoms, element for element.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_irc_mapping_element_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_ts_evidence_participant_composition` — `backend/app/services/reaction_resolution.py::validate_ts_evidence_participant_composition`

**Escape hatch.** Omit the participant mappings, which are optional, or declare a `pseudo` participant to skip that participant.

### 6. A reaction entry whose reactants and products are the same species entries carries evidence that something happens: a transition state or a kinetics record.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `reaction_entry_null_reaction` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `refuse_null_reaction_entry` — `backend/app/services/reaction_resolution.py::refuse_null_reaction_entry`

**Escape hatch.** Deposit a transition state or a kinetics record with the reaction, or deposit stereoisomers as different species entries.

## A structure against its own label

### 7. A deposit's `molecule_kind` agrees with the kind already stored for the species identity it resolves to.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_kind_conflict` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_declared_kind_matches_stored` — `backend/app/services/species_resolution.py::assert_declared_kind_matches_stored`

**Escape hatch.** None.

### 8. A conformer geometry deposited under a species entry is made of the atoms that entry's own SMILES declares.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_geometry_composition_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_geometry_composition_matches_identity` — `backend/app/services/species_resolution.py::assert_geometry_composition_matches_identity`

**Escape hatch.** Declare `molecule_kind: pseudo`, or deposit no geometry.

### 9. The multiset of isotopic substitutions declared in a species entry's SMILES equals the multiset carried by the geometry deposited under it.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_geometry_isotope_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_geometry_isotopes_match_identity` — `backend/app/services/species_resolution.py::assert_geometry_isotopes_match_identity`

**Escape hatch.** Deposit no geometry.

**Recorded divergence.** Only the multiset of isotopes is compared, so isotopomers such as CH3-OD and CH2D-OH are not distinguished.

### 10. Every geometry linked to a calculation is made of the atoms and isotopes of the subject the calculation is filed under.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `calculation_geometry_composition_mismatch`, `calculation_geometry_isotope_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_calculation_geometry_composition` — `backend/app/services/calculation_geometry_composition.py::assert_calculation_geometry_composition`
- `assert_isotopes` — `backend/app/services/calc_isotopes.py::assert_isotopes`

**Escape hatch.** Give the calculation the geometry it was run on, or file it under the subject that geometry belongs to.

**Recorded divergence.** Only atom counts are compared, so constitutional isomers pass, and a geometry with ghost or dummy centres is refused because no SMILES contains one.

### 11. A species entry's declared charge equals the summed formal charge of its own SMILES.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_smiles_charge_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `canonical_species_identity` — `backend/app/chemistry/species.py::canonical_species_identity`

**Escape hatch.** A free electron is exempt from the comparison.

### 12. The charge, spin multiplicity, electronic state and term symbol a depositor declares agree with the calculation's log and with each other.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `charge_mismatch`, `multiplicity_mismatch`, `term_symbol_mismatch`, `electronic_state_contradicts_method`, `term_symbol_contradicts_multiplicity` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `reconcile_charge_multiplicity` — `backend/app/services/charge_multiplicity_reconciliation.py::reconcile_charge_multiplicity`
- `term_symbol_warnings` — `backend/app/services/upload_reconciliation.py::term_symbol_warnings`
- `_reconcile_deduction` — `backend/app/services/upload_reconciliation.py::_reconcile_deduction`

**Escape hatch.** None; a value the log does not state is treated as unknown and never contradicts a declaration.

### 13. An optimisation's output geometry still describes the species it was declared for — the optimiser handed back the molecule it was given.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `validate_calculation_geometry` — `backend/app/services/geometry_validation.py::validate_calculation_geometry`

**Escape hatch.** None; the check only warns.

**Recorded divergence.** The stored `is_isomorphic` is computed from the molecular formula alone, so a rearranged or dissociated structure with the same formula passes; the read surfaces also publish it as `formula_matches`.

*(No machine-readable code reaches a client for this entry.)*

## Stationary points

### 14. A transition state has at least one imaginary vibrational mode, exactly one of them is designated the reaction coordinate, and no undeclared mode is stiff enough to make that designation meaningless.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `transition_state_no_imaginary_mode`, `transition_state_reaction_coordinate_not_designated`, `transition_state_reaction_coordinate_ambiguous` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_transition_state_frequency`
- `_check_ts_reaction_coordinate_designated` (`backend/app/services/trust/rubrics.py`) reports at read time whether a record with more than one imaginary mode carries the designation, without re-judging the frequencies.

**Escape hatch.** Deposit no frequency evidence, or designate the reaction coordinate and give each other imaginary mode a disposition.

### 15. A species entry declared a minimum has no imaginary vibrational mode at or above the tolerance of the protocol that produced it.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `n_imag_contradicts_minimum` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`

**Thresholds.**

- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::resolve_tau`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
  The Hessian noise floor is flat in omega-squared, so whether a small imaginary frequency is real depends on how the Hessian was built.

  | recorded protocol | `tau` / cm-1 |
  | --- | --- |
  | analytic Hessian, tight grid, tight optimisation | 15 |
  | analytic Hessian, default grid and tolerances | 30 |
  | finite-difference Hessian from analytic gradients | 50 |
  | finite-difference Hessian from energies | 80 |
  | Hessian method not recorded and program or method family not in the assumed-default table | 50 |

  *When the provenance is missing.* Tau is taken from the method an attached output log reports, else the method the input echoed in that log requests, else the recorded `freq.hessian_method` or the method the calculation's input deck states, else the producing program's assumed default for the level of theory's method family, else 50 cm-1 with `protocol_not_recorded` as the basis, which is the conservative row. A present but unrecognised `freq.hessian_method` keeps `protocol_not_recorded` and skips the assumption.

**Escape hatch.** Declare `species_entry_kind='vdw_complex'`, or deposit the structure as a transition state.

### 16. An imaginary mode on a van der Waals complex is recorded with a warning rather than refused, and a mode too stiff to be intermolecular is also reported as suggesting a transition state.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `n_imag_contradicts_minimum`, `n_imag_higher_order_saddle`, `n_imag_suggests_transition_state` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`

**Escape hatch.** None; the check only warns.

### 17. An imaginary mode other than a transition state's reaction coordinate, or any imaginary mode on a declared minimum, is judged by magnitude against the tolerance of the protocol that produced it.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `minimum_imaginary_modes_below_tau`, `transition_state_extra_imaginary_modes_below_tau`, `transition_state_extra_imaginary_mode_above_tau`, `transition_state_extra_imaginary_modes_not_assessable` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_transition_state_frequency`
- `evaluate_species_entry_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_species_entry_frequency`

**Thresholds.**

- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::resolve_tau`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
  The Hessian noise floor is flat in omega-squared, so whether a small imaginary frequency is real depends on how the Hessian was built.

  | recorded protocol | `tau` / cm-1 |
  | --- | --- |
  | analytic Hessian, tight grid, tight optimisation | 15 |
  | analytic Hessian, default grid and tolerances | 30 |
  | finite-difference Hessian from analytic gradients | 50 |
  | finite-difference Hessian from energies | 80 |
  | Hessian method not recorded and program or method family not in the assumed-default table | 50 |

  *When the provenance is missing.* Tau is taken from the method an attached output log reports, else the method the input echoed in that log requests, else the recorded `freq.hessian_method` or the method the calculation's input deck states, else the producing program's assumed default for the level of theory's method family, else 50 cm-1 with `protocol_not_recorded` as the basis, which is the conservative row. A present but unrecognised `freq.hessian_method` keeps `protocol_not_recorded` and skips the assumption.

**Escape hatch.** None; the check never refuses.

**Recorded divergence.** The disposition of each extra imaginary mode is declared by the depositor, and magnitude alone cannot tell a spurious mode from a real one that is not the reaction coordinate.

### 18. A transition state's reaction coordinate should exceed roughly 100 cm-1 in magnitude.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_imaginary_frequency_too_small` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `evaluate_transition_state_frequency` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::evaluate_transition_state_frequency`

**Thresholds.**

- `TS_IMAGINARY_FREQUENCY_MIN_CM1` = **100 cm-1** — a constant fixed in code.
  Reaction coordinates for hydrogen transfers run to thousands of cm-1, while genuinely flat barriers fall well under 100.
- `tau` — **not a constant.** Resolved per record, in cm-1, by `resolve_tau` (`schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::resolve_tau`) from the recorded execution provenance `freq.hessian_method`, `grid.quality`, `opt.convergence`.
  The Hessian noise floor is flat in omega-squared, so whether a small imaginary frequency is real depends on how the Hessian was built.

  | recorded protocol | `tau` / cm-1 |
  | --- | --- |
  | analytic Hessian, tight grid, tight optimisation | 15 |
  | analytic Hessian, default grid and tolerances | 30 |
  | finite-difference Hessian from analytic gradients | 50 |
  | finite-difference Hessian from energies | 80 |
  | Hessian method not recorded and program or method family not in the assumed-default table | 50 |

  *When the provenance is missing.* Tau is taken from the method an attached output log reports, else the method the input echoed in that log requests, else the recorded `freq.hessian_method` or the method the calculation's input deck states, else the producing program's assumed default for the level of theory's method family, else 50 cm-1 with `protocol_not_recorded` as the basis, which is the conservative row. A present but unrecognised `freq.hessian_method` keeps `protocol_not_recorded` and skips the assumption.

**Escape hatch.** None; the check never refuses.

### 19. A deposited saddle point should carry passing intrinsic-reaction-coordinate evidence that it connects the declared reactants and products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_missing_irc_evidence` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`

**Escape hatch.** None; the check only warns.

### 20. A frequency result that deposits both a scalar imaginary-mode count and a frequency list agrees with itself about how many imaginary modes it has.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `freq_n_imag_disagrees_with_modes` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `FreqResultPayload.validate_modes_consistency` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py::FreqResultPayload.validate_modes_consistency`

**Escape hatch.** Omit `modes`.

### 21. A frequency list carries no more modes than the geometry it is attached to has degrees of freedom: at most `3N` for `N` atoms, the six rigid-body modes included.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `freq_list_exceeds_geometry_degrees_of_freedom` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `evaluate_frequency_list_completeness` — `schemas/python/tckdb-schemas/tckdb_schemas/frequency_completeness.py::evaluate_frequency_list_completeness`
- `raise_for_blocking_findings` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::raise_for_blocking_findings`

**Escape hatch.** None.

### 22. A frequency list's mode count matches the shape of its geometry: 3N - 5 vibrations for a collinear molecule and 3N - 6 for a non-linear one.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `freq_list_linear_mode_count_for_bent_geometry`, `freq_list_bent_mode_count_for_linear_geometry`, `freq_list_incomplete_for_geometry` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `evaluate_frequency_list_completeness` — `schemas/python/tckdb-schemas/tckdb_schemas/frequency_completeness.py::evaluate_frequency_list_completeness`
- `evaluate_frequency_list_linearity` — `backend/app/services/frequency_geometry_linearity.py::evaluate_frequency_list_linearity`

**Thresholds.**

- `COLLINEAR_MAX_TRANSVERSE_RATIO` = **0.001 dimensionless (sigma_2 / sigma_1 of the centred coordinates)** — a constant fixed in code.
  At or below this the geometry is treated as linear, which is about 0.2 degrees of bend for a symmetric triatomic.
- `BENT_MIN_TRANSVERSE_RATIO` = **0.03 dimensionless (sigma_2 / sigma_1 of the centred coordinates)** — a constant fixed in code.
  At or above this the geometry is treated as bent, about 6 degrees for a symmetric triatomic, and between the two the check says nothing.

**Escape hatch.** None; the check only warns.

### 23. The energies an energy-ordering record compares should be taken at one level of theory per energy kind.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_energy_ordering_mixed_levels` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`

**Escape hatch.** Take every energy of a kind at one level of theory.

### 24. A stated energy agrees, within its printed precision, with the energy TCKDB stores for the calculation(s) it cites, and an E0 built from a frequency calculation's ZPE that states no `zpe_scale_factor` is not refused.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `ts_energy_ordering_stated_energy_mismatch`, `network_state_energy_sum_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`
- `compare_state_energy_sums` — `backend/app/services/network_energy_sources.py::compare_state_energy_sums`

**Escape hatch.** State the stored energy, or cite the calculation the number came from; an E0 built with a scaled ZPE states `zpe_scale_factor`.

### 25. Every energy an energy-ordering record or a network state energy states should be comparable with the energy TCKDB stores for the calculation(s) it cites.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `transition_state_energy_ordering_not_compared`, `network_state_energy_sum_not_compared`, `network_energy_zpe_source_absent`, `network_state_energy_sources_partial` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `persist_transition_state_validation_evidence` — `backend/app/services/transition_state_validation.py::persist_transition_state_validation_evidence`
- `collect_state_energy_source_warnings` — `backend/app/services/network_energy_sources.py::collect_state_energy_source_warnings`
- `collect_zpe_source_absent_warnings` — `backend/app/services/network_energy_sources.py::collect_zpe_source_absent_warnings`

**Escape hatch.** Deposit the cited energy, and the ZPE where one is needed, so the comparison can run.

### 26. A calculation cited as the source of a stated energy is of a type that carries that kind of energy.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `ts_energy_ordering_source_type_mismatch`, `network_energy_source_type_mismatch`, `thermo_source_role_type_mismatch`, `statmech_source_role_type_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `_assert_energy_sources_are_comparable` — `backend/app/services/transition_state_validation.py::_assert_energy_sources_are_comparable`
- `assert_network_energy_source_type` — `backend/app/services/network_energy_sources.py::assert_network_energy_source_type`
- `assert_network_source_role_type` — `backend/app/services/network_energy_sources.py::assert_network_source_role_type`
- `assert_thermo_role_matches_calculation_type` — `backend/app/workflows/thermo.py::assert_thermo_role_matches_calculation_type`
- `assert_statmech_role_compatible` — `backend/app/services/statmech_resolution.py::assert_statmech_role_compatible`

**Escape hatch.** Cite a calculation of a type that carries the energy.

## Atom mapping across a reaction

### 27. An atom does not change element on the way across a reaction: carbon does not map onto nitrogen.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_element_not_conserved` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`
- `ck_reaction_atom_map_pair_element_matches` (check on `reaction_atom_map_pair`)
  `element = ts_element`
  Violating this returns **409 `atom_map_element_not_conserved`** — An atom map pairs two atoms of different elements. An atom does not change element on the way across a reaction.

**Escape hatch.** None.

### 28. One saddle-point atom is claimed by exactly one atom of each leg, and one participant atom maps to exactly one saddle-point atom.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_not_a_bijection` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`
- `uq_reaction_atom_map_pair_ts_atom_index` (unique on `reaction_atom_map_pair`)
  `(atom_map_id, side, ts_atom_index)`
  Violating this returns **409 `atom_map_not_a_bijection`** — Two atoms of one leg claim the same saddle-point atom. A map is a bijection or it is not a map.
- `uq_reaction_atom_map_pair_atom_map_id` (unique on `reaction_atom_map_pair`)
  `(atom_map_id, structure_participant_id, atom_index)`
  Violating this returns **409 `atom_map_not_a_bijection`** — One participant atom is mapped more than once. A map is a bijection or it is not a map.

**Escape hatch.** None.

### 29. Every atom index in a map names an atom of a geometry the participant owns, and a map names only participants of a reaction that has a saddle point.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_indices_not_geometry_relative`, `atom_map_participant_not_declared`, `atom_map_without_transition_state` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`
- `fk_reaction_atom_map_pair_geometry_id_geometry_atom` (foreign_key on `reaction_atom_map_pair`)
  `(geometry_id, atom_index, element) -> geometry_atom(geometry_id, atom_index, element)`
- `fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom` (foreign_key on `reaction_atom_map_pair`)
  `(transition_state_geometry_id, ts_atom_index, ts_element) -> geometry_atom(geometry_id, atom_index, element)`
- `fk_reaction_atom_map_pair_structure_participant` (foreign_key on `reaction_atom_map_pair`)
  `(structure_participant_id, side) -> reaction_entry_structure_participant(id, role)`

**Escape hatch.** None.

### 30. When a map covers every declared participant of an atom-balanced reaction, both legs claim the same saddle-point atoms and no saddle-point atom is left unclaimed.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_atoms_unaccounted_for` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_reaction_atom_map` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::validate_reaction_atom_map`

**Escape hatch.** Leave the map incomplete, or deposit an unbalanced reaction, which drops the rule to a warning.

### 31. Where a deposit carries both an atom map and an IRC participant mapping for the same saddle point, they agree about which saddle-point atoms each participant is made of.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `atom_map_contradicts_irc_mapping` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_atom_map_agrees_with_irc_evidence` — `backend/app/services/reaction_atom_map.py::validate_atom_map_agrees_with_irc_evidence`

**Escape hatch.** Omit one surface, or correct whichever is wrong.

### 32. A reaction that has a transition state should say which atom of the reactants is which atom of the saddle point and of the products.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_absent` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `_warn_absent` — `backend/app/services/reaction_atom_map.py::_warn_absent`

**Escape hatch.** None; the check only warns.

### 33. A supplied atom map should cover every declared participant molecule, every atom of each mapped participant, and every atom of the saddle point.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `reaction_atom_map_participants_incomplete`, `reaction_atom_map_atoms_incomplete` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `_warn_incomplete` — `backend/app/services/reaction_atom_map.py::_warn_incomplete`

**Escape hatch.** None.

### 34. An atom map records whether a human asserted it or an algorithm produced it, an inferred map names the algorithm, and neither attribution can be changed afterwards.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `atom_map_inferred_requires_note` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `ReactionAtomMapIn.validate_inferred_names_its_algorithm` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py::ReactionAtomMapIn.validate_inferred_names_its_algorithm`
- `ck_reaction_atom_map_inferred_requires_note` (check on `reaction_atom_map`)
  `source <> 'inferred' OR (note IS NOT NULL AND btrim(note) <> '')`
- `trg_reaction_atom_map_source_immutable` (trigger on `reaction_atom_map`)
  `BEFORE UPDATE FOR EACH ROW: refuse any change to the source column`

**Escape hatch.** Edit the mutable `note` and `equivalent_map_count` fields, which do not touch the attribution.

## Rate coefficients

### 35. An Arrhenius pre-exponential factor carries the units its reaction order requires: per second, concentration^-1 time^-1 or concentration^-2 time^-1 for order one, two or three, and a coefficient of any other order is refused as `unsupported_reaction_molecularity`.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `arrhenius_a_units_molecularity_mismatch`, `unsupported_reaction_molecularity` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `validate_a_units_for_molecularity` — `backend/app/chemistry/units.py::validate_a_units_for_molecularity`

**Escape hatch.** None; a coefficient of any order other than one, two or three has no accepted units.

## Statistical mechanics

### 36. A partition function belongs to exactly one subject — a species entry or a transition-state entry, never both and never neither.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `statmech_subject_not_exactly_one` |
| **Code reaches a client via** | PostgreSQL only, so the refusal is a 409 rather than a 422 — named, where the constraint declares a rejection code |

**Enforced at.**

- `ck_statmech_statmech_exactly_one_subject` (check on `statmech`)
  `(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)`
  Violating this returns **409 `statmech_subject_not_exactly_one`** — A partition function names both a species entry and a transition-state entry, or neither. It belongs to exactly one subject.

**Escape hatch.** None.

## Pressure-dependent networks

### 37. A phenomenological k(T,P) declares whether it was computed or reported: a computed solve carries master-equation evidence and a reported one cites its publication.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `reported_network_solve` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `ck_network_solve_reported_requires_literature` (check on `network_solve`)
  `kind <> 'reported' OR literature_id IS NOT NULL`
  Violating this returns **409 `network_solve_reported_requires_literature`** — A network solve declared as 'reported' cites no literature. Transcribed rates must name the publication they came from, because nothing else in the deposit accounts for them.
- `ct_network_solve_computed_evidence` (trigger on `network_solve`)
  `deferred constraint trigger, at COMMIT: a computed solve must hold at least one state energy; at least one energy-transfer model if its network declares a well; at least one channel barrier if its network declares a saddle-point path`

**Escape hatch.** Declare `kind='reported'` and cite the literature.

**Recorded divergence.** The database guarantees a computed solve carries master-equation evidence, but state-energy, energy-transfer and barrier coverage are checked only by the upload validator.

### 38. A collisional energy-transfer model records whether its ⟨ΔE⟩down was determined per (well, collider) pair or declared once for the whole network.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `network_wide_energy_transfer_scope` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `ck_network_solve_energy_transfer_scope_columns_agree` (check on `network_solve_energy_transfer`)
  `(scope='per_well' AND state_id IS NOT NULL AND collider_species_entry_id IS NOT NULL) OR (scope='network_wide' AND state_id IS NULL AND collider_species_entry_id IS NULL)`
  Violating this returns **409 `energy_transfer_scope_columns_disagree`** — A collisional energy-transfer model declares a scope its own columns contradict: a 'per_well' model must name the well and the collider it was determined for, and a 'network_wide' one must name neither.

**Escape hatch.** Declare `scope='network_wide'`.

## Custody of the evidence

### 39. The bytes TCKDB serves for a stored artifact are the bytes it stored, and a record whose evidence is known not to be is labelled at read time.

| Field | Value |
| --- | --- |
| **Tier** | `label` |
| **Code** | `artifact_integrity_failed` |
| **Code reaches a client via** | a read-time trust label (`HardFailReason`), not any refusal |

**Enforced at.**

- `load_artifact_bytes` — `backend/app/services/artifact_storage.py::load_artifact_bytes`
- `record_integrity_observation` — `backend/app/services/artifact_integrity.py::record_integrity_observation`
- `_detect_calculation_hard_fail` — `backend/app/services/trust/evaluator.py::_detect_calculation_hard_fail`
- `ck_artifact_integrity_event_observed_digest_present_iff_read` (check on `artifact_integrity_event`)
  `(finding = 'object_missing' AND observed_sha256 IS NULL) OR (finding <> 'object_missing' AND observed_sha256 IS NOT NULL)`
- `ck_artifact_integrity_event_verified_requires_matching_digest` (check on `artifact_integrity_event`)
  `finding <> 'verified' OR observed_sha256 = sha256`

**Escape hatch.** Restore the object, or re-upload the original bytes under the same digest so a later verification supersedes the break.

## Reproducibility

### 40. Whether a record's preserved evidence is sufficient to understand, audit or repeat it is assessed separately from its trust and from curator approval.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `reproducibility_assessment` rows carry `described` / `auditable` / `rerunnable` under a versioned rubric, append-only, independent of `record_review` and of the trust evaluator (`app/services/trust/`)

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

## Advisory consistency

### 41. Compare every supplied NASA Cp/entropy fit with exact points, s298 and explicitly named neighbours.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_thermo` — `backend/app/services/consistency/thermo.py::compare_thermo`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

### 42. Compare explicitly supplied opposite elementary rates with equilibrium from explicitly mapped NASA thermo.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_kinetics` — `backend/app/services/consistency/kinetics.py::compare_kinetics`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

### 43. Compare every stored Gibbs value of one thermo record with H(T) - T*S(T) taken from each of that record's own representations, H and S always from the same one.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_gibbs` — `backend/app/services/consistency/gibbs.py::compare_gibbs`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

### 44. Compare a thermo record's supplied enthalpies at shared temperatures, each enthalpy change with the Cp integral of a fit, and each fit's own boundary jumps.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_kirchhoff` — `backend/app/services/consistency/kirchhoff.py::compare_kirchhoff`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

### 45. Compare a kinetics record's stated reaction energy with the Hess sum of explicitly mapped formation enthalpies.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_hess` — `backend/app/services/consistency/hess.py::compare_hess`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

### 46. Find, on a protium species entry, calculation geometries that declare a non-standard nuclide.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_isotope_identity` — `backend/app/services/consistency/isotope_identity.py::compare_isotope_identity`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

### 47. Report a conformer group whose converged structures are linked end to end but are not all within the matching tolerance of each other.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_wide_basin` — `backend/app/services/consistency/wide_basin.py::compare_wide_basin`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

## External reference comparison

### 48. A computed thermo record's heat capacity is compared at each temperature an independent external observation reports, and the residual is recorded.

| Field | Value |
| --- | --- |
| **Tier** | `review` |
| **Code** | *(none — prose only)* |
| **Code reaches a client via** | *nothing carries a code* |

**Enforced at.**

- `compare_thermo_with_cp_observations` — `backend/app/services/external_comparison/cp.py::compare_thermo_with_cp_observations`

**Escape hatch.** None.

*(No machine-readable code reaches a client for this entry.)*

## Scan coordinates

### 49. A scan point's stored coordinate_value is the internal coordinate at that point's own sampled geometry, in that coordinate's own unit.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `scan_point_coordinate_disagrees_with_geometry` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `collect_scan_coordinate_warnings` — `backend/app/services/scan_coordinate_conformance.py::collect_scan_coordinate_warnings`
- `evaluate_point_coordinate_conformance` — `backend/app/services/scan_coordinate_conformance.py::evaluate_point_coordinate_conformance`

**Thresholds.**

- `dihedral_not_checkable_min_sin_theta` = **0.05 dimensionless (sin of a flanking bond angle)** — a constant fixed in code.
  Below this the dihedral of a near-collinear quartet is too ill-conditioned to compare.
- `tolerance_floor_degrees` = **0.001 degree** — a constant fixed in code.
  The tightest tolerance a 6-decimal deposit can distinguish.
- `tolerance_ceiling_degrees` = **1 degree** — a constant fixed in code.
  Above this a derived tolerance is too wide to mean anything, so the point is reported as not checkable.
- `tolerance_ceiling_angstrom` = **0.1 Angstrom** — a constant fixed in code.
  About 5 to 10 percent of a typical bond length, beyond which a tolerance cannot tell a bond-length error from deposit noise.

**Escape hatch.** None; the check only warns, so send each point's coordinate value as measured on its own geometry.

## Vibrational frequency provenance

### 50. A calculation that records how its frequency job built the Hessian (`freq.hessian_method`) and also attaches the input deck that ran it, or an output log that echoes that input, states the same method in both.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `hessian_method_contradicts_input_deck` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `check_recorded_method_agrees_with_deck` — `schemas/python/tckdb-schemas/tckdb_schemas/hessian_method_detection.py::check_recorded_method_agrees_with_deck`

**Escape hatch.** Correct whichever of the two is wrong, or omit the recorded `freq.hessian_method`.

## Stereochemistry of a species entry

### 51. A species entry states one stereo configuration: its geometries, SMILES marks and any supplied stereo label agree wherever they state the same element, up to the molecule's symmetry.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `species_stereo_contradiction` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `resolve_stereo` — `backend/app/chemistry/stereo.py::resolve_stereo`
- `merge_sources` — `backend/app/chemistry/stereo.py::merge_sources`
- `_refuse_disagreement_past_the_cap` — `backend/app/chemistry/stereo.py::_refuse_disagreement_past_the_cap`

**Thresholds.**

- `allene_torsion_window` = **10 degree (lower bound; the upper bound is 170)** — a constant fixed in code.
  An allene's end-to-end torsion is read only inside this window, because outside it the axis is flattened and states nothing.
- `odd_cumulene_planar_tolerance` = **30 degree** — a constant fixed in code.
  An odd cumulene is Z within this torsion of planar, E within it of anti-planar, and otherwise unread.
- `automorphism_cap` = **100000 automorphisms examined** — a constant fixed in code.
  Past this many automorphisms the sources are compared as multisets instead.
- `enumeration_cap` = **8 potential stereo elements** — a constant fixed in code.
  Above this many potential elements the stereoisomer enumeration is skipped.

**Escape hatch.** State `?` for the element, omit the label, or deposit the other isomer as its own species entry.

## Wavefunction reliability diagnostics

### 52. A wavefunction reliability diagnostic is reported when it is past its conventional threshold: closed-shell coupled-cluster T1 above 0.02, a stored <S^2> (not the annihilated one) more than 0.1 from S(S+1), or a printed ideal <S^2> more than 1e-3 from S(S+1), with S in both taken from the entry's declared multiplicity and never from `s_squared_expected`.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `t1_diagnostic_above_closed_shell_threshold`, `spin_contamination_above_conventional_threshold`, `spin_expected_contradicts_multiplicity` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `collect_wavefunction_reliability_warnings` — `backend/app/services/wavefunction_reliability_warnings.py::collect_wavefunction_reliability_warnings`
- `is_coupled_cluster_method` — `backend/app/services/wavefunction_reliability_warnings.py::is_coupled_cluster_method`

**Thresholds.**

- `t1_closed_shell_threshold` = **0.02 dimensionless (singles norm over the square root of the number of correlated electrons)** — a constant fixed in code.
  A conventional rule of thumb for closed-shell coupled-cluster calculations, applied strictly greater than.
- `s_squared_contamination_threshold` = **0.1 dimensionless (absolute deviation of <S^2> from S(S+1))** — a constant fixed in code.
  A conventional absolute deviation of <S^2> from S(S+1), applied strictly greater than.
- `s_squared_expected_tolerance` = **0.001 dimensionless (absolute difference between two <S^2> values)** — a constant fixed in code.
  Programs print the ideal <S^2> to four to six decimals, so 1e-3 is above print noise and far below the gap between spin states.

**Escape hatch.** None; the check only warns.

## Bath gas of a rate

### 53. A bath gas a rate or a network solve states is a composition: its mole fractions sum to one, and for a rate each species appears once and no bath gas is given at the high-pressure limit, where there is no collider.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `kinetics_bath_gas_mole_fraction_sum`, `kinetics_bath_gas_duplicate_species`, `kinetics_bath_gas_at_high_pressure_limit` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `check_kinetics_bath_gas` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/kinetics_bath_gas.py::check_kinetics_bath_gas`
- `check_bath_gas_mole_fraction_sum` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/kinetics_bath_gas.py::check_bath_gas_mole_fraction_sum`

**Thresholds.**

- `bath_gas_mole_fraction_sum_tolerance` = **1e-09 absolute, on the sum** — a constant fixed in code.
  Fractions are stated, not measured, so a sum off by more than rounding means a component is missing or doubled.

**Escape hatch.** Omit the bath gas (it then reads as not stated), merge repeated components into one with their total mole fraction, or state the pressure context the rate was obtained in.

## Network composition and sources

### 54. The kind of a network channel (isomerization, association, dissociation, exchange) is the one its source and sink states imply, so a stated kind that disagrees is refused.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `network_channel_kind_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `derive_channel_kind` — `schemas/python/tckdb-schemas/tckdb_schemas/network_channel_kind.py::derive_channel_kind`
- `NetworkPDepUploadRequest.validate_channel_kind_from_states` — `backend/app/schemas/workflows/network_pdep_upload.py::NetworkPDepUploadRequest.validate_channel_kind_from_states`

**Escape hatch.** Omit the kind and it is derived from the states.

### 55. A network state is the multiset of its participants' species entries, a state lists a species entry once (with a stoichiometry), and a network holds one state per composition.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `network_state_duplicate_composition`, `network_state_duplicate_participant` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `refuse_duplicate_state_compositions` — `backend/app/workflows/network_pdep.py::refuse_duplicate_state_compositions`

**Escape hatch.** Declare each state once, or make two states differ by stereo marks, a stereo label or a geometry on the species so they resolve to different entries.

## Levels and geometries of linked calculations

### 56. One record has one energy level, and the declared level matches the level of the energies linked to it (the optimisations' level when no energy is linked).

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `thermo_energy_level_contradiction`, `statmech_energy_level_contradiction`, `kinetics_energy_level_contradiction`, `thermo_energy_level_ambiguous`, `statmech_energy_level_ambiguous`, `thermo_energy_level_requires_sp`, `statmech_energy_level_requires_sp` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_role_consistency` — `backend/app/services/calculation_levels.py::assert_role_consistency`
- `assert_kinetics_energy_level_consistency` — `backend/app/services/calculation_levels.py::assert_kinetics_energy_level_consistency`

**Escape hatch.** Omit the declaration (the level is then derived from the links at read time), link every energy at one level, or declare the level the linked calculations ran at.

### 57. Energy calculations and optimisations pair one-to-one by geometry: each energy sits on a linked optimisation's geometry, each optimisation has one energy, and an SCF stability source is on the verdict's conformer.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `thermo_sp_geometry_mismatch`, `statmech_sp_geometry_mismatch`, `thermo_role_duplicate`, `statmech_role_duplicate`, `thermo_energy_sp_and_composite_linked`, `statmech_energy_sp_and_composite_linked`, `thermo_energy_level_requires_sp`, `statmech_energy_level_requires_sp`, `scf_stability_source_geometry_mismatch`, `scf_stability_source_conformer_unanchored` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_role_consistency` — `backend/app/services/calculation_levels.py::assert_role_consistency`
- `scf_source_geometry_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::scf_source_geometry_error`
- `scf_source_unanchored_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::scf_source_unanchored_error`

**Escape hatch.** Link the optimisation whose geometry the energy was computed at, one energy per optimisation.

### 58. A calculation names one conformer and one input geometry: its conformer key and geometry key agree, and a transition-state calculation does not run on a species minimum's geometry.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `calculation_geometry_conformer_mismatch`, `calculation_input_geometry_mismatch`, `transition_state_calculation_species_geometry` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_calculation_geometry_matches_conformer` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::assert_calculation_geometry_matches_conformer`
- `attach_calculation_input_geometries` — `backend/app/services/calculation_resolution.py::attach_calculation_input_geometries`
- `assert_transition_state_calculations_unanchored` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::assert_transition_state_calculations_unanchored`

**Escape hatch.** State one conformer, or omit the redundant key; list the geometry the calculation ran on.

## Composite and single-point arithmetic

### 59. The numbers a composite energy states agree with each other and with its recipe.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `composite_e0_inconsistent`, `composite_terms_do_not_sum`, `composite_total_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_composite_result_arithmetic` — `schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py::assert_composite_result_arithmetic`
- `assert_composite_total_recomputes` — `schemas/python/tckdb-schemas/tckdb_schemas/composite_total.py::assert_composite_total_recomputes`

**Thresholds.**

- `composite_arithmetic_tolerance` = **1e-06 hartree (floor; 5e-7 per rounded quantity above two)** — a constant fixed in code.
  An equation among n numbers printed to six decimals can differ by n half-units of the last place without anything being wrong.
- `composite_total_recomputation_tolerance` = **1e-06 hartree (floor; 5e-7 times 1 + the summed sensitivity weights)** — a constant fixed in code.
  The tolerance scales with how strongly each stored input is weighted in the recomputed total, so inputs printed to six decimals stay inside it.

**Escape hatch.** Send the values the program printed, or omit the number you did not read.

### 60. Each input of an assembled composite ran at the level of theory its slot in the scheme names, and the inputs and the composite itself declare one geometry between them.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `composite_input_level_mismatch`, `composite_input_geometry_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `_finalize_one` — `backend/app/services/composite_input_resolution.py::_finalize_one`

**Escape hatch.** Cite the calculation at the slot's level, all computed at the one geometry.

### 61. The parts of a single point's electronic energy agree with the energy they are parts of.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `sp_energy_component_total_mismatch`, `sp_energy_components_do_not_sum`, `sp_energy_component_duplicate` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `check_sp_energy_components` — `schemas/python/tckdb-schemas/tckdb_schemas/sp_energy_components.py::check_sp_energy_components`

**Thresholds.**

- `sp_energy_component_sum_tolerance` = **1e-06 hartree** — a constant fixed in code.
  Tight enough to catch a part from a different run, loose enough for the digits a program prints.

**Escape hatch.** Send the parts the program printed with its energy, or omit the components.

### 62. A frequency calculation linked beside a composite runs at the level of theory the composite's scheme prescribes for frequencies, and the composite role is given only to a calculation of type composite.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `composite_frequency_level_differs_from_recipe`, `composite_role_on_non_composite_calculation` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `collect_composite_link_warnings` — `backend/app/services/calculation_levels.py::collect_composite_link_warnings`

**Escape hatch.** The link is stored as stated; link the frequency calculation at the recipe's level to clear the warning.

### 63. A named composite method (CBS-QB3, G4, W1U) is one program run and is deposited as a calculation of type composite, not as an optimisation or a single point at its level.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `named_composite_deposited_as_opt`, `named_composite_deposited_as_sp` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `collect_named_composite_deposit_warnings` — `backend/app/services/composite_result_resolution.py::collect_named_composite_deposit_warnings`

**Escape hatch.** The deposit is accepted; send it as type composite with a composite_result block to clear the warning.

### 64. An assembled composite's inputs each declare their geometry, and the deposited total can be recomputed from them.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `composite_input_geometry_undeclared`, `composite_total_unverifiable` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `_unverifiable_warning` — `backend/app/services/composite_input_resolution.py::_unverifiable_warning`

**Escape hatch.** The deposit is accepted; declare each input's geometry and state the energies used to clear the warning.

## Subject of a record

### 65. What a record cites as evidence belongs to the record's own subject.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `network_energy_source_subject_mismatch`, `thermo_source_calculation_owner_mismatch`, `statmech_source_calculation_owner_mismatch`, `transport_source_calculation_owner_mismatch`, `scf_stability_source_calculation_owner_mismatch`, `composite_input_owner_mismatch`, `statmech_torsion_scan_calculation_owner_mismatch`, `applied_energy_correction_source_calculation_owner_mismatch`, `thermo_statmech_owner_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_owned_by` — `backend/app/services/calculation_ownership.py::assert_owned_by`
- `owner_mismatch_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::owner_mismatch_error`
- `assert_state_energy_source_owner` — `backend/app/services/network_energy_sources.py::assert_state_energy_source_owner`
- `assert_barrier_source_owner` — `backend/app/services/network_energy_sources.py::assert_barrier_source_owner`
- `assert_network_role_source_owner` — `backend/app/services/network_energy_sources.py::assert_network_role_source_owner`
- `assert_state_participant_source_owner` — `backend/app/services/network_energy_sources.py::assert_state_participant_source_owner`

**Escape hatch.** Cite the calculation or statmech record of the subject the record is about.

## Declarations against the record

### 66. A kinetics applicability or protocol declaration does not contradict the columns the record already states (direction, pressure context, bath gas, scientific origin): the stored columns stay authoritative.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `kinetics_declaration_contradicts_record` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `kinetics_applicability_error` — `schemas/python/tckdb-schemas/tckdb_schemas/kinetics_declarations.py::kinetics_applicability_error`
- `kinetics_protocol_origin_error` — `schemas/python/tckdb-schemas/tckdb_schemas/kinetics_declarations.py::kinetics_protocol_origin_error`
- `assert_kinetics_declaration_columns` — `backend/app/services/kinetics_declaration_resolution.py::assert_kinetics_declaration_columns`

**Escape hatch.** Correct whichever statement is wrong, or omit the declaration.

### 67. A determination, statistical-model, structure, protocol or interpretation declaration describes the record that carries it.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `kinetics_determination_mismatch`, `statmech_model_declaration_mismatch`, `structure_determination_mismatch`, `kinetics_protocol_calculation_owner_mismatch`, `thermo_protocol_calculation_owner_mismatch`, `kinetics_interpretation_statmech_owner_mismatch`, `kinetics_interpretation_conformer_selection_owner_mismatch` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `_mismatch` — `backend/app/services/kinetics_declaration_resolution.py::_mismatch`
- `_resolve_protocol` — `backend/app/services/thermo_declaration_resolution.py::_resolve_protocol`
- `_mismatch` — `backend/app/services/statmech_declaration_resolution.py::_mismatch`
- `_mismatch` — `backend/app/services/structure_determination_resolution.py::_mismatch`
- `assert_statmech_owned_by` — `backend/app/services/calculation_ownership.py::assert_statmech_owned_by`

**Escape hatch.** Declare it on the record it describes, with the calculations and sources of that record.

## Observation identity

### 68. A curator attaches an observation of unresolved identity only to a ground-state, minimum-energy entry of a species whose connectivity agrees with the observation's own identity hint.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `observation_identity_target_not_ground_state_minimum`, `observation_identity_hint_conflict` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `_assert_ground_state_minimum_entry` — `backend/app/services/observation_identity_attach.py::_assert_ground_state_minimum_entry`
- `_assert_no_identity_hint_conflict` — `backend/app/services/observation_identity_attach.py::_assert_no_identity_hint_conflict`

**Escape hatch.** Attach it to the ground-state minimum entry of the species it was measured on.

## Corrections and scale factors

### 69. A Petersson bond-additivity correction applied as a total carries at least one bond component, unless its target species has no bonds; a transition state has no bond assignment and so cannot take a componentless total.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `bac_total_requires_components` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `assert_bac_total_has_required_components` — `backend/app/services/energy_correction_resolution.py::assert_bac_total_has_required_components`

**Escape hatch.** Send the bond components, or apply the correction under another role.

### 70. The frequency scale factor a statmech record applies was derived for the program that ran the frequency calculation it is applied to.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `statmech_frequency_scale_factor_software_mismatch` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `collect_frequency_scale_factor_software_mismatch_warnings` — `backend/app/services/statmech_resolution.py::collect_frequency_scale_factor_software_mismatch_warnings`

**Escape hatch.** The deposit is accepted; confirm the choice, or attach a factor derived for that program.

## Enthalpy reference

### 71. A thermo record that carries an enthalpy declares that it is the standard enthalpy of formation at 298.15 K, the only accepted reference, and a record without enthalpy content declares none.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `enthalpy_declaration_absent`, `enthalpy_reference_kind_unrecognized`, `enthalpy_declaration_without_content`, `enthalpy_quantity_not_storable_here` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `enthalpy_reference_error` — `schemas/python/tckdb-schemas/tckdb_schemas/enthalpy_reference.py::enthalpy_reference_error`
- `assert_enthalpy_reference` — `backend/app/workflows/thermo.py::assert_enthalpy_reference`

**Escape hatch.** Declare `formation_298k` when the source states that convention, or deposit increments and absolute enthalpies through the molecular property observation route.

## Calculation graph

### 72. The graph of which calculation depends on which is acyclic: no calculation depends on itself through a chain of others.

| Field | Value |
| --- | --- |
| **Tier** | `structural` |
| **Code** | `calculation_dependency_cycle` |
| **Code reaches a client via** | PostgreSQL only, so the refusal is a 409 rather than a 422 — named, where the constraint declares a rejection code |

**Enforced at.**

- `trg_calculation_dependency_acyclic` (trigger on `calculation_dependency`)
  `BEFORE INSERT OR UPDATE FOR EACH ROW: refuse an edge whose parent is already reachable from its child`
- `ck_calculation_dependency_not_self` (check on `calculation_dependency`)
  `parent_calculation_id <> child_calculation_id`

**Escape hatch.** Drop or reverse the dependency that points back.

### 73. SCF stability verdicts do not cite one another as the job that measured them in a ring: some job measured each.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `scf_stability_source_cycle` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `scf_source_cycle_error` — `schemas/python/tckdb-schemas/tckdb_schemas/bundle_source_rules.py::scf_source_cycle_error`

**Escape hatch.** Name, as the measuring job, a calculation that is not itself measured by the first.

## Energy comparison

### 74. A lowest-energy selection compares energies of one level and one species entry, and a relative-energy comparison names two different protocols.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `lowest_energy_across_levels`, `unsafe_lowest_energy_comparison`, `relative_conformer_energy_compare_identical_protocols` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `_refuse_mixed_levels` — `backend/app/api/routes/lookup.py::_refuse_mixed_levels`
- `_validate_ranking` — `backend/app/services/scientific_read/species_calculations_search.py::_validate_ranking`
- `identical_protocols` — `backend/app/services/relative_conformer_energy/compare.py::identical_protocols`

**Escape hatch.** Filter to one level (method and basis) and one species entry, or use a select endpoint.

## Evidence a result rests on

### 75. An optimisation recorded as converged has an energy behind it: a single point of the same owner at any level, or its own final energy stated with an artifact.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `converged_opt_no_usable_energy` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `collect_converged_opt_energy_warnings` — `backend/app/services/calculation_resolution.py::collect_converged_opt_energy_warnings`

**Escape hatch.** The deposit is accepted; deposit a single point, or the optimisation's final energy with its artifact.

## Declared against the uploaded evidence

### 76. What a depositor typed about a calculation's settings and level of theory agrees with the uploaded input deck and output log, the deck agrees with the log, a later log agrees with an earlier one, and a setting a log reports agrees with the value requested.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `parameter_disagrees_with_artifact`, `input_deck_disagrees_with_output_log`, `output_log_disagrees_with_earlier_output_log`, `reported_setting_differs_from_requested`, `level_of_theory_disagrees_with_artifact` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `declared_parameter_warnings` — `backend/app/services/parameter_reconciliation.py::declared_parameter_warnings`
- `merge_artifact_observations` — `backend/app/services/parameter_reconciliation.py::merge_artifact_observations`
- `level_of_theory_warnings` — `backend/app/services/parameter_reconciliation.py::level_of_theory_warnings`
- `level_of_theory_qualifier_warnings` — `backend/app/services/parameter_reconciliation.py::level_of_theory_qualifier_warnings`
- `reported_setting_warnings` — `backend/app/services/parameter_reconciliation.py::reported_setting_warnings`

**Escape hatch.** The deposit is accepted; correct the typed value, or attach the right deck.

### 77. The electronic, E0 and zero-point energies a tool states agree with the output log attached to the same calculation, and the log is the composite method the level of theory names.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `sp_energy_payload_log_mismatch`, `composite_energy_log_mismatch`, `composite_log_method_mismatch` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `reconcile_sp_energy` — `backend/app/services/sp_energy_reconciliation.py::reconcile_sp_energy`
- `reconcile_composite_energy` — `backend/app/services/composite_energy_reconciliation.py::reconcile_composite_energy`

**Thresholds.**

- `composite_log_tolerance` = **1e-06 hartree (max(1e-6, 5e-7 * n), n the rounded quantities compared)** — a constant fixed in code.
  Each log number is printed to six decimals, so a copy can differ by half-units of the last place of each term.
- `sp_energy_log_tolerance` = **1e-06 hartree** — a constant fixed in code.
  Tight enough that a wrong line, wrong method or unit slip lies far outside it, loose enough for reduced stored precision.

**Escape hatch.** The deposit is accepted; send the value the log prints, or omit it and let the log speak.

### 78. The Hessian method a deck or log attached after the deposit states is the method the calculation's stored imaginary-mode tolerance rests on.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `hessian_method_disagrees_with_stored_tau_basis` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `try_hessian_method_disagreement_from_input_upload` — `backend/app/services/hessian_method_deck_check.py::try_hessian_method_disagreement_from_input_upload`

**Escape hatch.** None; the record is annotated, not refused.

### 79. A neutral ground-state atom's term symbol and lowest-level degeneracy agree with the NIST ground term of its element.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `atomic_electronic_degeneracy_contradicts_term`, `term_symbol_contradicts_ground_term` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `_degeneracy_warnings` — `backend/app/services/atomic_electronic_warnings.py::_degeneracy_warnings`
- `term_symbol_warnings` — `backend/app/services/upload_reconciliation.py::term_symbol_warnings`

**Escape hatch.** The deposit is accepted; deposit the levels that match the declared term.

### 80. The job named as having measured an SCF stability verdict ran at the level of theory of the calculation carrying the verdict, and the two can be shown to be on one conformer.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `scf_stability_source_level_mismatch`, `scf_stability_source_conformer_unverified` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `link_scf_stability_sources` — `backend/app/services/scf_stability_sources.py::link_scf_stability_sources`

**Escape hatch.** None; the link is stored as stated.

## Thermo declarations

### 81. A thermo declaration the file format defines describes what the format defines: a target declared by the CHEMKIN format is an equilibrium ensemble, on a gas-phase record that carries a NASA-7 block.

| Field | Value |
| --- | --- |
| **Tier** | `block` |
| **Code** | `thermo_declared_by_target_incoherent`, `thermo_declared_by_record_incoherent` |
| **Code reaches a client via** | the `code` field of the 422 error body — a client can branch on it |

**Enforced at.**

- `thermo_target_error` — `schemas/python/tckdb-schemas/tckdb_schemas/thermo_declarations.py::thermo_target_error`
- `_declared_by_record_error` — `schemas/python/tckdb-schemas/tckdb_schemas/thermo_declarations.py::_declared_by_record_error`

**Escape hatch.** Declare the target as depositor-defined, or send a gas-phase record with its NASA-7 block.

## Hessian method of a stored tolerance

### 82. A recorded finite difference of gradients on an ORCA NumFreq deck, for a method with no analytic gradient, is stored as finite differences of energies, and the record says so.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `hessian_method_refined` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `_refinement_findings` — `schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py::_refinement_findings`

**Escape hatch.** The deposit is accepted; record finite_difference_energy to state it directly.

## One-electron participants

### 83. A kinetics record's declared correlated energy level is accepted for a one-electron participant's Hartree-Fock energy in the same basis, because with one electron every correlation term is zero.

| Field | Value |
| --- | --- |
| **Tier** | `warn` |
| **Code** | `one_electron_hf_substitution` |
| **Code reaches a client via** | the `code` field of an `UploadWarning` returned alongside the accepted upload, and stored with the record it concerns (or, when no one record could take it, with its submission), which shows it, in TCKDB's own wording, under `record_warnings` or `deposit_warnings` on that record's detail reads and on the per-parent kinetics and thermo reads |

**Enforced at.**

- `kinetics_one_electron_hf_substitutions` — `backend/app/services/calculation_levels.py::kinetics_one_electron_hf_substitutions`
- `is_one_electron_hf_substitute` — `backend/app/services/calculation_levels.py::is_one_electron_hf_substitute`

**Escape hatch.** The deposit is accepted; link a correlated energy to state a level without the substitution.

---

Declarations live beside the checks they describe; see `backend/app/scientific_checks/__init__.py` for how to add one, and `backend/app/scientific_checks/declarations.py` for the PostgreSQL objects and wire-schema checks that cannot sit beside a Python check.
