# TCKDB Schema Analysis

This document classifies each table by four questions:

- Identity: what the entity is
- Result: scientific/computational values produced about it
- Provenance: where it came from, who made it, what generated it
- Curation: review, selection, status, and quality fields

The goal is not to repeat SQL types or constraints. The goal is to make the semantic role of each field clear.

Migration details that materially affect semantics:

- `transition_state_entry.status` defaults to `optimized`
- `calculation.quality` defaults to `raw`
- `species.created_at`, `species_entry.created_at`, `chem_reaction.created_at`, `reaction_entry.created_at`, `transition_state.created_at`, `transition_state_entry.created_at`, `calculation.created_at`, `literature.created_at`, and `author.created_at` are `NOT NULL DEFAULT now()`
- `calculation.quality` uses the `calculation_quality` enum type
- `calculation_output_geometry.role` uses the `calc_geom_role` enum type
- `calculation_dependency.dependency_role` uses the `calc_dependency_role` enum type
- the calculation ownership check is named `ck_calculation_exactly_one_owner`
- `kinetics.model_kind` defaults to `modified_arrhenius`
- `app_user.role` defaults to `user`
- `network_species.role` is nullable in the migration

## 1. species

### Identity

- `id`
- `kind`
- `smiles`
- `inchi_key`
- `charge`
- `multiplicity`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## 2. species_entry

### Identity

- `id`
- `species_id`
- `kind`
- `mol`
- `unmapped_smiles`
- `stereo_label`
- `electronic_state_kind`
- `electronic_state_label`
- `term_symbol`
- `isotopologue_label`

### Result

- None

### Provenance

- `term_symbol_raw`
- `created_by`
- `created_at`

### Curation

- None

## 3. transport

### Identity

- `id`
- `species_id`

### Result

- `sigma_angstrom`
- `epsilon_over_k_k`
- `dipole_debye`
- `polarizability_angstrom3`
- `rotational_relaxation`

### Provenance

- `scientific_origin`
- `literature_id`
- `software_id`
- `workflow_tool_id`
- `created_by`
- `created_at`

### Curation

- `note`

## 4. species_entry_review

### Identity

- `id`
- `species_entry_id`
- `user_id`

### Result

- None

### Provenance

- `user_id`
- `created_at`

### Curation

- `role`
- `note`

## 5. geometry

### Identity

- `id`
- `natoms`
- `geom_hash`
- `xyz_text`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## 6. geometry_atom

### Identity

- `geometry_id`
- `atom_index`
- `element`
- `x`
- `y`
- `z`

### Result

- None

### Provenance

- Inherited through `geometry_id`

### Curation

- None

## 7. chem_reaction

### Identity

- `id`
- `stoichiometry_hash`
- `reversible`
- `reaction_family_id`
- `reaction_family_raw`
- `reaction_family_source_note`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## reaction_family

### Identity

- `id`
- `name`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## 8. reaction_participant

### Identity

- `reaction_id`
- `species_id`
- `role`
- `stoichiometry`

### Result

- None

### Provenance

- Inherited through `reaction_id`

### Curation

- None

## 9. reaction_entry

### Identity

- `id`
- `reaction_id`

### Result

- None

### Provenance

- `created_by`
- `created_at`

### Curation

- `preferred_kinetics_id`

Note:

- this is the only preferred-pointer field in the initial schema that is not deferred

## 10. transition_state

### Identity

- `id`
- `reaction_entry_id`
- `label`

### Result

- None

### Provenance

- `created_by`
- `created_at`

### Curation

- `note`

## 11. transition_state_entry

### Identity

- `id`
- `transition_state_id`
- `charge`
- `multiplicity`
- `mol`
- `unmapped_smiles`

### Result

- None

### Provenance

- `created_by`
- `created_at`

### Curation

- `preferred_calculation_id`
- `status`

Note:

- `status` defaults to `optimized`

## 12. software

### Identity

- `id`
- `name`
- `website`
- `description`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## 13. software_release

### Identity

- `id`
- `software_id`
- `version`
- `revision`
- `build`

### Result

- `release_date`

### Provenance

- `created_at`

### Curation

- `notes`

## 14. level_of_theory

### Identity

- `id`
- `method`
- `basis`
- `aux_basis`
- `dispersion`
- `solvent`
- `solvent_model`
- `keywords`
- `lot_hash`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## 15. calculation

### Identity

- `id`
- `type`
- `species_entry_id`
- `conformer_id`
- `transition_state_entry_id`

### Result

- None

### Provenance

- `software_id`
- `workflow_tool_id`
- `lot_id`
- `literature_id`
- `created_by`
- `created_at`

### Curation

- `quality`

Note:

- `quality` defaults to `raw`
- the enum type name is `calculation_quality`
- the ownership check is named `ck_calculation_exactly_one_owner`
- species-side calculations may be entry-level, conformer-level, or both

## 16. calculation_output_geometry

### Identity

- `calculation_id`
- `geometry_id`
- `output_order`

### Result

- None

### Provenance

- Inherited through `calculation_id`

### Curation

- `role`

Note:

- `role` uses the `calc_geom_role` enum with values `initial`, `final`, `intermediate`, `scan_point`, `irc_forward`, `irc_reverse`, `neb_image`

## 17. calculation_dependency

### Identity

- `parent_calculation_id`
- `child_calculation_id`

### Result

- None

### Provenance

- Inherited through the linked calculations

### Curation

- `dependency_role`

Note:

- `dependency_role` uses the `calc_dependency_role` enum with values `optimized_from`, `freq_on`, `single_point_on`, `arkane_source`, `irc_start`, `irc_followup`, `scan_parent`, `neb_parent`

## 18. calc_sp_result

### Identity

- `calculation_id`

### Result

- `electronic_energy_hartree`

### Provenance

- Inherited through `calculation_id`

### Curation

- None

## 19. calc_opt_result

### Identity

- `calculation_id`

### Result

- `converged`
- `n_steps`
- `final_energy_hartree`

### Provenance

- Inherited through `calculation_id`

### Curation

- None

## 20. calc_freq_result

### Identity

- `calculation_id`

### Result

- `n_imag`
- `img_freq_cm1`
- `zpe_hartree`

### Provenance

- Inherited through `calculation_id`

### Curation

- None

## 21. calc_freq_mode

### Identity

- `calculation_id`
- `mode_index`

### Result

- `frequency_cm1`
- `reduced_mass_amu`
- `force_constant_mdyn_a`
- `ir_intensity_km_mol`
- `is_scaled`
- `is_projected`

### Provenance

- Inherited through `calculation_id`

### Curation

- None

## 22. calc_hessian

### Identity

- `calculation_id`

### Result

- `n_atoms`
- `matrix_dim`
- `units`
- `representation`

### Provenance

- `artifact_id`

### Curation

- None

## 23. calc_scan_result

### Identity

- `calculation_id`
- `scan_kind`
- `dimension`
- `n_points`

### Result

- `converged`

### Provenance

- Inherited through `calculation_id`

### Curation

- `note`

## 24. calc_scan_coordinate

### Identity

- `calculation_id`
- `coordinate_index`
- `coordinate_kind`
- `atom1_index`
- `atom2_index`
- `atom3_index`
- `atom4_index`

### Result

- `symmetry_number`

### Provenance

- Inherited through `calculation_id`

### Curation

- `top_description`

## 25. calc_scan_point

### Identity

- `calculation_id`
- `point_index`

### Result

- `relative_energy_kj_mol`
- `is_valid`

### Provenance

- `geometry_id`

### Curation

- None

## 26. calculation_artifact

### Identity

- `id`
- `calculation_id`
- `kind`
- `uri`

### Result

- `sha256`
- `bytes`

### Provenance

- `created_at`

### Curation

- None

## 27. thermo

### Identity

- `id`
- `species_entry_id`
- `statmech_id`
- `model_kind`

### Result

- `h298_kj_mol`
- `s298_j_mol_k`

### Provenance

- `scientific_origin`
- `literature_id`
- `workflow_tool_id`
- `software_id`
- `created_by`
- `created_at`

### Curation

- `note`

## 28. thermo_point

### Identity

- `thermo_id`
- `temperature_k`

### Result

- `h_kj_mol`
- `s_j_mol_k`
- `g_kj_mol`

### Provenance

- Inherited through `thermo_id`

### Curation

- None

## 29. thermo_nasa

### Identity

- `thermo_id`

### Result

- `t_low_k`
- `t_mid_k`
- `t_high_k`
- `low_a1` to `low_a7`
- `high_a1` to `high_a7`

### Provenance

- Inherited through `thermo_id`

### Curation

- None

## 30. thermo_source_calculation

### Identity

- `thermo_id`
- `calculation_id`

### Result

- None

### Provenance

- Inherited through `thermo_id` and `calculation_id`

### Curation

- `role`

## 31. statmech

### Identity

- `id`
- `species_entry_id`
- `rigid_rotor_kind`
- `statmech_treatment`

### Result

- `external_symmetry`
- `point_group`
- `is_linear`
- `freq_scale_factor`
- `uses_projected_frequencies`

### Provenance

- `scientific_origin`
- `literature_id`
- `workflow_tool_id`
- `software_id`
- `created_by`
- `created_at`

### Curation

- `note`

Note:

- `external_symmetry` must be at least 1 when present

## 32. statmech_source_calculation

### Identity

- `statmech_id`
- `calculation_id`

### Result

- None

### Provenance

- Inherited through `statmech_id` and `calculation_id`

### Curation

- `role`

## 33. statmech_torsion

### Identity

- `id`
- `statmech_id`
- `torsion_index`
- `treatment_kind`
- `source_scan_calculation_id`

### Result

- `symmetry_number`
- `dimension`

### Provenance

- Inherited through `statmech_id`

### Curation

- `top_description`
- `invalidated_reason`
- `note`

Note:

- `dimension` must be at least 1

## 34. statmech_torsion_definition

### Identity

- `torsion_id`
- `coordinate_index`
- `atom1_index`
- `atom2_index`
- `atom3_index`
- `atom4_index`

### Result

- None

### Provenance

- Inherited through `torsion_id`

### Curation

- None

## 35. kinetics

### Identity

- `id`
- `reaction_entry_id`
- `model_kind`

### Result

- `a`
- `a_units`
- `n`
- `ea_kj_mol`
- `tmin_k`
- `tmax_k`
- `degeneracy`
- `tunneling_model`

### Provenance

- `scientific_origin`
- `literature_id`
- `workflow_tool_release_id`
- `software_release_id`
- `created_by`
- `created_at`

### Curation

- `note`

Note:

- `model_kind` is identity/classification for the stored law form and defaults to `modified_arrhenius`
- allowed values are `arrhenius`, `modified_arrhenius`, `chebyshev`, `plog`, `falloff`, and `tabulated`

## 36. kinetics_source_calculation

### Identity

- `kinetics_id`
- `calculation_id`

### Result

- None

### Provenance

- Inherited through `kinetics_id` and `calculation_id`

### Curation

- `role`

## 37. network

### Identity

- `id`
- `name`
- `description`

### Result

- `is_pressure_dependent`
- `method`
- `tmin_k`
- `tmax_k`
- `pmin_bar`
- `pmax_bar`
- `maximum_grain_size_kj_mol`
- `minimum_grain_count`

### Provenance

- `literature_id`
- `software_release_id`
- `workflow_tool_release_id`
- `created_by`
- `created_at`

### Curation

- None

## 38. network_reaction

### Identity

- `network_id`
- `reaction_entry_id`

### Result

- None

### Provenance

- Inherited through `network_id`

### Curation

- None

## 39. network_species

### Identity

- `network_id`
- `species_id`

### Result

- None

### Provenance

- Inherited through `network_id`

### Curation

- `role`

Note:

- `role` is optional in the migration; unlabeled membership rows are allowed

## 40. literature

### Identity

- `id`
- `kind`
- `title`
- `journal`
- `year`
- `volume`
- `issue`
- `pages`
- `article_number`
- `doi`
- `isbn`
- `url`
- `publisher`
- `institution`

### Result

- None

### Provenance

- `created_by`
- `created_at`

### Curation

- None

## 41. author

### Identity

- `id`
- `given_name`
- `family_name`
- `full_name`
- `orcid`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## 42. literature_author

### Identity

- `literature_id`
- `author_id`

### Result

- None

### Provenance

- Inherited through `literature_id` and `author_id`

### Curation

- `author_order`

Note:

- `author_order` must be greater than zero

## 43. workflow_tool

### Identity

- `id`
- `name`
- `website`
- `description`

### Result

- None

### Provenance

- `created_at`

### Curation

- None

## 44. workflow_tool_release

### Identity

- `id`
- `workflow_tool_id`
- `version`
- `git_commit`
- `git_branch`
- `git_tag`
- `is_dirty`

### Result

- `release_date`

### Provenance

- `created_at`
- `source_url`

### Curation

- `notes`

## 45. app_user

### Identity

- `id`
- `username`
- `email`
- `full_name`
- `affiliation`
- `orcid`

### Result

- None

### Provenance

- `created_at`

### Curation

- `role`

Note:

- `role` defaults to `user`

## 46. record_reproducibility_assessment

### Identity

- `id`
- `record_type`
- `record_id`
- `rubric_name`
- `rubric_version`
- `context_hash`

### Result

- `grade`
- `passed_json`
- `missing_json`
- `warnings_json`

### Provenance

- `context_json`
- `assessor_kind`
- `assessor_user_id`
- `source_submission_id`
- `assessed_at`
- `created_at`

### Curation

- The complete row is an immutable curation snapshot. Reassessment appends a
  new row; a database trigger rejects updates and deletes.

Note:

- Reproducibility grading is independent of human review and deterministic
  trust badges.

## High-level observations

- Identity is concentrated in `species`, `chem_reaction`, `transition_state`, `geometry`, and the bibliographic/reference tables.
- Result values are intentionally pushed into typed result tables such as `calc_sp_result`, `calc_opt_result`, `calc_freq_result`, `calc_freq_mode`, `calc_hessian`, `calc_scan_result`, `thermo`, `thermo_point`, `thermo_nasa`, `statmech`, and `kinetics`.
- Provenance is carried mainly through `created_by`, `created_at`, `scientific_origin`, `literature_id`, software and workflow foreign keys, and link tables back to `calculation`.
- Curation currently appears as review roles, conformer-selection tags, `status`, `quality`, and role-like labeling fields in link tables.
- Explicit reproducibility grades and their preserved evidence contexts live in append-only `record_reproducibility_assessment` rows.
- Conformer-selection tags can be scoped by assignment scheme, so curation and selection logic can evolve together without rewriting conformer identity.
- `calculation.conformer_id` allows species-side computational provenance to attach directly to a conformer when entry-level ownership is too coarse.

## Enthalpy reference declaration (2026-09-23)

`thermo.enthalpy_reference_kind = formation_from_elements_298k` declares
standard enthalpy of formation at 298.15 K: one mole of the species formed
from elements in their reference forms, whose formation enthalpies are zero.
At another temperature, H(T) is that formation energy plus the species' own
enthalpy increment from 298.15 K. The elemental term remains pinned at
298.15 K; it is not recomputed against the elements at T.

The declaration covers `h298_kj_mol`, `thermo_point.h_kj_mol`, Wilhoit
`h0_kj_mol`, and the NASA-7/NASA-9 enthalpy integration constants. A point's
`g_kj_mol` means H(T) - T*S(T), on the same reference zero, with entropy
converted to kJ/(mol*K). It is not a formation Gibbs energy recomputed
against elemental entropies. `enthalpy_formation_0k_kj_mol` retains its
separate, existing meaning.

The two-layer rule deliberately is not a scalar iff constraint:

- The database CHECK is `h298_kj_mol IS NULL OR enthalpy_reference_kind IS NOT NULL`.
- Every deposit workflow requires the declaration for any h298 scalar, point
  enthalpy, Wilhoit h0, or NASA-7/NASA-9 block, and refuses a declaration when
  none of that content exists. Cp/entropy-only deposits leave it null.
- Declared fit-only and point-only records are valid without h298. No scalar
  is evaluated from a fit and stored as though the depositor supplied it.

Absence is absence: null means the source did not declare the reference.
`EnthalpyReferenceKind` has exactly one member and no `unspecified` member.
No default depends on origin, software, magnitude, or another row. Legacy
rows are not backfilled, including approved immutable rows. The migration
adds the CHECK as `NOT VALID`, preserving legacy nulls while enforcing new
inserts and updates. It must not later be validated by inferring references
or by using the accepted-science repair mechanism.

Sensible increments such as H(T)-H(0) and absolute quantum-chemistry
enthalpies belong in `molecular_property_observation`, with their stated
property label, state, temperature, pressure and uncertainty meaning.
CCCBDB's explicitly labelled H(298.15)-H(0) is routed to an observation
payload with its source datum and identity hint intact. ARC requires an
explicit adapter configuration; its output does not establish a basis.
