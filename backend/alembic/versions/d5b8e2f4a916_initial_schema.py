"""Create the TCKDB schema: tables, enums, trigger functions, triggers, indexes and seed rows."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5b8e2f4a916"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REACTION_FAMILIES = [
    '1+2_Cycloaddition',
    '1,2-Birad_to_alkene',
    '1,2_Elimination_LiR',
    '1,2_Insertion_CO',
    '1,2_Insertion_carbene',
    '1,2_Intra_Elimination_LiR',
    '1,2_NH3_elimination',
    '1,2_XY_interchange',
    '1,2_shiftC',
    '1,2_shiftS',
    '1,3_Insertion_CO2',
    '1,3_Insertion_ROR',
    '1,3_Insertion_RSR',
    '1,3_NH3_elimination',
    '1,3_sigmatropic_rearrangement',
    '1,4_Cyclic_birad_scission',
    '1,4_Linear_birad_scission',
    '2+2_cycloaddition',
    '6_membered_central_C-C_shift',
    'Baeyer-Villiger_step1_cat',
    'Baeyer-Villiger_step2',
    'Baeyer-Villiger_step2_cat',
    'Bimolec_Hydroperoxide_Decomposition',
    'Birad_R_Recombination',
    'Birad_recombination',
    'Br_Abstraction',
    'CO_Disproportionation',
    'Cation_Addition_MultipleBond',
    'Cation_Addition_MultipleBond_Disprop',
    'Cation_Li_Abstraction',
    'Cation_NO_Ring_Opening',
    'Cation_NO_Substitution',
    'Cation_R_Recombination',
    'Cl_Abstraction',
    'Concerted_Intra_Diels_alder_monocyclic_1,2_shiftH',
    'Cyclic_Ether_Formation',
    'Cyclic_Thioether_Formation',
    'Cyclopentadiene_scission',
    'Diels_alder_addition',
    'Diels_alder_addition_Aromatic',
    'Disproportionation',
    'Disproportionation-Y',
    'F_Abstraction',
    'H2_Loss',
    'HO2_Elimination_from_PeroxyRadical',
    'H_Abstraction',
    'Intra_2+2_cycloaddition_Cd',
    'Intra_5_membered_conjugated_C=C_C=C_addition',
    'Intra_Diels_alder_monocyclic',
    'Intra_Disproportionation',
    'Intra_RH_Add_Endocyclic',
    'Intra_RH_Add_Exocyclic',
    'Intra_R_Add_Endocyclic',
    'Intra_R_Add_ExoTetCyclic',
    'Intra_R_Add_Exo_scission',
    'Intra_R_Add_Exocyclic',
    'Intra_Retro_Diels_alder_bicyclic',
    'Intra_ene_reaction',
    'Ketoenol',
    'Korcek_step1',
    'Korcek_step1_cat',
    'Korcek_step2',
    'Li_Abstraction',
    'Li_Addition_MultipleBond',
    'Li_NO_Ring_Opening',
    'Li_NO_Substitution',
    'Peroxyl_Disproportionation',
    'Peroxyl_Termination',
    'R_Addition_COm',
    'R_Addition_CSm',
    'R_Addition_MultipleBond',
    'R_Addition_MultipleBond_Disprop',
    'R_Recombination',
    'Retroene',
    'Singlet_Carbene_Intra_Disproportionation',
    'Singlet_Val6_to_triplet',
    'SubstitutionS',
    'Substitution_O',
    'Surface_Abstraction',
    'Surface_Abstraction_Beta',
    'Surface_Abstraction_Beta_double_vdW',
    'Surface_Abstraction_Beta_vdW',
    'Surface_Abstraction_Single_vdW',
    'Surface_Abstraction_vdW',
    'Surface_Adsorption_Bidentate',
    'Surface_Adsorption_Dissociative',
    'Surface_Adsorption_Dissociative_Double',
    'Surface_Adsorption_Double',
    'Surface_Adsorption_Single',
    'Surface_Adsorption_vdW',
    'Surface_Bidentate_Dissociation',
    'Surface_Carbonate_2F_Decomposition',
    'Surface_Carbonate_CO_2F_Decomposition',
    'Surface_Carbonate_CO_Decomposition',
    'Surface_Carbonate_Deposition',
    'Surface_Carbonate_F_CO_Decomposition',
    'Surface_Dissociation',
    'Surface_Dissociation_Beta',
    'Surface_Dissociation_Beta_vdW',
    'Surface_Dissociation_Double',
    'Surface_Dissociation_Double_vdW',
    'Surface_Dissociation_to_Bidentate',
    'Surface_Dissociation_vdW',
    'Surface_EleyRideal_Addition_Multiple_Bond',
    'Surface_Migration',
    'Surface_Monodentate_to_Bidentate',
    'Surface_Proton_Electron_Reduction_Alpha',
    'Surface_Proton_Electron_Reduction_Alpha_vdW',
    'Surface_Proton_Electron_Reduction_Beta',
    'Surface_Proton_Electron_Reduction_Beta_Dissociation',
    'Surface_Proton_Electron_Reduction_Beta_vdW',
    'Surface_vdW_to_Bidentate',
    'XY_Addition_MultipleBond',
    'XY_elimination_hydroxyl',
    'halocarbene_recombination',
    'halocarbene_recombination_double',
    'intra_H_migration',
    'intra_NO2_ONO_conversion',
    'intra_OH_migration',
    'intra_halogen_migration',
    'intra_substitutionCS_cyclization',
    'intra_substitutionCS_isomerization',
    'intra_substitutionS_cyclization',
    'intra_substitutionS_isomerization',
    'lone_electron_pair_bond',
]

_PARAMETER_VOCAB_COLUMNS = (
    "canonical_key",
    "description",
    "expected_value_type",
    "affects_scientific_result",
    "affects_numerics",
    "affects_resources",
    "note",
)

_PARAMETER_VOCAB = [
    ['scf.convergence', 'SCF energy/density convergence target.', 'enum', True, True, False, None],
    ['scf.max_cycles', 'Maximum SCF iterations before fallback.', 'int', False, True, False, None],
    ['scf.direct', 'SCF integral handling: direct/incore.', 'enum', False, True, False, None],
    ['scf.fallback', 'SCF fallback algorithm (e.g. xqc, qc).', 'enum', False, True, False, None],
    ['scf.convergence_failure_ignored', 'Producer configured the SCF to continue if convergence fails. Reported energy/geometry comes from a non-converged wavefunction.', 'bool', True, True, False, 'Triggered by Gaussian IOp(5/13=1). This is a calculation trust flag, not SCF wavefunction stability evidence -- it should not populate calc_scf_stability.'],
    ['scf.convergence_failure_action', 'Action the producer instructed for SCF convergence failure (e.g. continue, fallback).', 'enum', True, True, False, 'Co-emitted with scf.convergence_failure_ignored from Gaussian IOp(5/13=1).'],
    ['opt.convergence', 'Geometry optimisation convergence target.', 'enum', True, True, False, None],
    ['opt.max_cycles', 'Maximum optimisation steps.', 'int', False, True, False, None],
    ['opt.max_step', 'Maximum trust-radius / step size.', 'float', False, True, False, None],
    ['opt.initial_hessian', 'Initial Hessian source: calcfc / calcall / readfc.', 'enum', True, True, False, 'calcall changes converged path, not just speed.'],
    ['opt.saddle_order', 'Saddle-point order for transition-state search.', 'int', True, False, False, None],
    ['opt.eigen_test', 'Eigenvalue test toggle for opt steps.', 'enum', False, True, False, None],
    ['grid.quality', 'DFT integration grid quality.', 'string', True, True, True, None],
    ['integral.accuracy', 'Two-electron integral accuracy threshold.', 'int', True, True, True, None],
    ['guess.strategy', 'Initial-guess strategy: harris/mix/read/INDO/etc.', 'string', True, True, False, 'Affects converged electronic state and convergence success.'],
    ['symmetry.disabled', 'Disables use of molecular symmetry in the electronic-structure calculation.', 'bool', True, True, False, None],
    ['pno.truncation', 'PNO truncation level for DLPNO methods.', 'enum', True, True, True, None],
    ['output.verbosity', 'Output verbosity tier.', 'enum', False, False, False, None],
    ['internal_option.iop', 'Gaussian IOp(overlay/option=value) internal option. Raw key carries the overlay/option coordinate; raw value carries the assigned value.', 'string', True, True, False, 'Generic catch-all for IOp(...) directives. Behavior depends on the specific overlay/option.'],
    ['parallel.nproc', 'Total processor count requested.', 'int', False, False, True, None],
    ['parallel.nproc_shared', 'Shared-memory processor count.', 'int', False, False, True, None],
    ['memory.raw', 'Memory request as software-emitted string (units in row).', 'string', False, False, True, None],
    ['memory.maxcore_mb', 'Per-process memory cap in megabytes (ORCA %maxcore).', 'int', False, False, True, None],
    ['f12.ansatz', 'F12 explicit-correlation ansatz (e.g. 3C(FIX)) used by explicitly-correlated methods such as CCSD(T)-F12.', 'string', True, True, False, 'Observed in Molpro CCSD(T)-F12 / cc-pVTZ-F12 outputs. Distinct F12 ansatze (e.g. 3C(FIX) vs 3*A) change the explicitly-correlated energy.'],
    ['freq.hessian_method', "How the frequency job's second derivatives were obtained: analytic, finite_difference_gradient, or finite_difference_energy.", 'enum', True, True, True, "Distinct from opt.initial_hessian, which names where the optimiser got its starting Hessian rather than how the frequency job built its own. ADR 0012's tau keys on this value; absence means 'not recorded' and selects the conservative tolerance. Emitted from Gaussian Freq=Numer and ORCA NumFreq / AnFreq."],
    ['dispersion.correction', 'Empirical dispersion correction requested as a separate keyword.', 'enum', True, False, False, 'Closed set: none, d2, d3_zero, d3_bj, d4. A functional with dispersion built into its name (wB97X-D) belongs to the method, not here.'],
    ['solvation.model', 'Continuum solvation model.', 'enum', True, False, False, 'Closed set: smd, cpcm, iefpcm, cosmo, alpb. Absent when the deck names a solvent but no model; the program default is not assumed.'],
    ['solvation.solvent', 'Solvent named for the continuum solvation model.', 'string', True, False, False, 'Lower-cased solvent name as the program spells it. Names are not unified across programs.'],
    ['ri.approximation', 'Resolution-of-the-identity approximation for the Coulomb and exchange terms.', 'enum', True, True, False, 'Closed set: none, rij, rijcosx, rijk, rijonx. Absent when the deck is silent; the program default is not assumed.'],
    ['core.frozen', 'Frozen-core choice for a correlated method.', 'enum', True, False, False, 'Closed set: frozen_core, all_electron, window. Absent when the deck is silent; the program default is not assumed.'],
    ['relativistic.hamiltonian', 'Scalar relativistic Hamiltonian.', 'enum', True, False, False, 'Closed set: none, dkh0, dkh2, dkhso, resc, x2c, zora, iora.'],
    ['basis.d_function_type', 'Spherical (5D) or Cartesian (6D) d functions.', 'enum', True, True, False, 'Closed set: spherical, cartesian.'],
    ['basis.f_function_type', 'Spherical (7F) or Cartesian (10F) f functions.', 'enum', True, True, False, 'Closed set: spherical, cartesian.'],
    ['scf.stability_analysis', 'SCF wavefunction stability request.', 'enum', True, True, False, 'Closed set: check_only, optimize. This is the request in the deck, not the stability result.'],
    ['grid.integration_accuracy', 'DFT radial integration accuracy the program reports (ORCA IntAcc).', 'float', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual.'],
    ['grid.angular_max', 'Largest angular (Lebedev) grid, in points, the program reports for the DFT grid.', 'int', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual. The COSX grids are not this key.'],
    ['scf.tol_energy_eh', 'SCF energy-change tolerance the program reports, in hartree.', 'float', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual.'],
    ['scf.tol_orbital_gradient', 'SCF orbital-gradient tolerance the program reports (ORCA TolG).', 'float', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual.'],
    ['scf.tol_orbital_rotation', 'SCF orbital-rotation tolerance the program reports (ORCA TolX).', 'float', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual.'],
    ['scf.tol_diis_error', 'SCF DIIS-error tolerance the program reports (ORCA TolErr).', 'float', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual.'],
    ['scf.tol_rms_density', 'SCF RMS density-matrix tolerance the program reports (Gaussian).', 'float', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual.'],
    ['scf.tol_max_density', 'SCF maximum density-matrix tolerance the program reports (Gaussian).', 'float', True, True, False, 'Read from the value an output log prints as used; never derived from a keyword or a manual.'],
]

_CONFORMER_ASSIGNMENT_SCHEMES = [
    ['torsion_basin', 1, 'canonical', 'Torsional basin matching: group conformers whose comparable rotatable-bond torsions all differ by less than the threshold under circular comparison with optional symmetry folding.', '{"tie_break": "closest_torsional_distance", "methyl_symmetry_fold": 3, "exclude_methyl_rotors": false, "rigid_fallback_use_rmsd": true, "rmsd_threshold_angstrom": 0.5, "use_circular_difference": true, "quantization_bin_degrees": 15, "exclude_terminal_noisy_rotors": true, "torsion_match_threshold_degrees": 15, "require_all_comparable_torsions_within_threshold": true}', None, False, 'cas_tdjs4djyadm557gfqtkimuylna'],
    ['torsion_basin', 2, 'canonical', "Symmetry-aware torsional basin matching: two geometries share a basin when some graph symmetry of the molecule puts every rotor within its own tolerance (20 degrees for heavy-atom and methyl rotors, 30 degrees for one- and two-hydrogen tops such as O-H and -NH2). One- and two-hydrogen tops are rotors. The symmetry search is capped; the stored fingerprint's bins are display only. Above the cap the answer can depend on the order of atoms in a deposited file (recorded as symmetry_cap_hit).", '{"tie_break": "closest_torsional_distance", "matching_rule": "symmetry_existence", "methyl_symmetry_fold": 3, "exclude_methyl_rotors": false, "max_symmetry_mappings": 4096, "rigid_fallback_use_rmsd": true, "rmsd_threshold_angstrom": 0.5, "use_circular_difference": true, "quantization_bin_degrees": 15, "exclude_terminal_noisy_rotors": false, "torsion_tolerance_heavy_degrees": 20, "torsion_tolerance_terminal_h_degrees": 30, "require_all_comparable_torsions_within_threshold": true}', None, False, 'cas_aaoqrxx2cvvk274zebwd24pd7q'],
    ['torsion_basin', 3, 'canonical', 'Basins are connected components: the converged optimised structures of a species entry are linked when the symmetry-aware matching rule says they share a basin (some graph symmetry puts every rotor within its own tolerance: 20 degrees for heavy-atom and methyl rotors, 30 degrees for one- and two-hydrogen tops), and a basin is what the links reach, so the order of deposition cannot change it. The two hydrogens of an -NH2 or radical -CH2 top are compared as a pair. Structures that are not converged optimisations are filed into a basin but never link two. The stored representative is the medoid of the defining members, derived and not identity. A basin whose members are not all within tolerance of each other is flagged as wide, never split. The symmetry search is capped; linkage is evaluated both ways round so the cap cannot make it order-dependent.', '{"linkage": "connected_components", "reference": "medoid", "tie_break": "closest_torsional_distance", "matching_rule": "symmetry_existence", "defining_members": "converged_opt", "methyl_symmetry_fold": 3, "exclude_methyl_rotors": false, "max_symmetry_mappings": 4096, "rigid_fallback_use_rmsd": true, "rmsd_threshold_angstrom": 0.5, "use_circular_difference": true, "quantization_bin_degrees": 15, "exclude_terminal_noisy_rotors": false, "torsion_tolerance_heavy_degrees": 20, "torsion_tolerance_terminal_h_degrees": 30, "require_all_comparable_torsions_within_threshold": true}', None, True, 'cas_xcbembrw3e4zkjsstc6i7wbdqu'],
]

# Tables, enum types, trigger functions, constraints, indexes and triggers, in dependency order.
_SCHEMA_SQL = r'''
SET LOCAL check_function_bodies = false;
CREATE TYPE public.app_user_role AS ENUM (
    'user',
    'curator',
    'admin'
);
CREATE TYPE public.applied_correction_component_kind AS ENUM (
    'atom',
    'bond',
    'molecular',
    'zpe_scale',
    'soc',
    'other'
);
CREATE TYPE public.arrhenius_a_units AS ENUM (
    'per_s',
    'cm3_mol_s',
    'cm3_molecule_s',
    'm3_mol_s',
    'cm6_mol2_s',
    'cm6_molecule2_s',
    'm6_mol2_s'
);
CREATE TYPE public.artifact_integrity_detection_context AS ENUM (
    'download',
    'verification_sweep',
    'store_dedup_verification',
    'parameter_extraction',
    'archive',
    'reproducibility_verification',
    'reclaim_restore',
    'input_geometry_extraction'
);
CREATE TYPE public.artifact_integrity_finding AS ENUM (
    'digest_mismatch',
    'size_mismatch',
    'object_missing',
    'verified'
);
CREATE TYPE public.artifact_kind AS ENUM (
    'input',
    'output_log',
    'checkpoint',
    'formatted_checkpoint',
    'ancillary',
    'hessian'
);
CREATE TYPE public.artifact_storage_capacity_observation AS ENUM (
    'refused',
    'accepted',
    'capacity_report',
    'operator_clear'
);
CREATE TYPE public.atom_map_source AS ENUM (
    'declared',
    'inferred'
);
CREATE TYPE public.atom_param_application AS ENUM (
    'subtracted',
    'added'
);
CREATE TYPE public.calc_quality AS ENUM (
    'raw',
    'curated',
    'rejected'
);
CREATE TYPE public.calc_type AS ENUM (
    'opt',
    'freq',
    'sp',
    'irc',
    'scan',
    'path_search',
    'conf',
    'composite'
);
CREATE TYPE public.calculation_dependency_role AS ENUM (
    'optimized_from',
    'freq_on',
    'single_point_on',
    'arkane_source',
    'irc_start',
    'irc_followup',
    'scan_parent',
    'composite_input'
);
CREATE TYPE public.calculation_geometry_role AS ENUM (
    'final',
    'initial',
    'scan_point',
    'irc_forward',
    'irc_reverse',
    'path_search_point'
);
CREATE TYPE public.calculation_input_geometry_source AS ENUM (
    'deposited',
    'extracted_from_artifact'
);
CREATE TYPE public.calculation_parameter_source AS ENUM (
    'parser',
    'upload',
    'curated'
);
CREATE TYPE public.composite_assembly AS ENUM (
    'program_run',
    'assembled'
);
CREATE TYPE public.composite_binding_source AS ENUM (
    'named_method_catalogue',
    'declared'
);
CREATE TYPE public.composite_extrapolation_formula AS ENUM (
    'inverse_power',
    'inverse_power_shifted_half',
    'karton_martin_scf',
    'exponential_three_point',
    'exponential_sqrt'
);
CREATE TYPE public.composite_input_slot AS ENUM (
    'value',
    'high',
    'low',
    'cardinal'
);
CREATE TYPE public.composite_log_outcome AS ENUM (
    'confirmed',
    'mismatch',
    'method_mismatch',
    'available',
    'unverifiable',
    'absent'
);
CREATE TYPE public.composite_scheme_kind AS ENUM (
    'named_method',
    'extrapolation',
    'additive'
);
CREATE TYPE public.composite_term_operation AS ENUM (
    'base',
    'extrapolation',
    'difference',
    'value',
    'empirical'
);
CREATE TYPE public.conformer_assignment_scope_kind AS ENUM (
    'canonical',
    'imported',
    'experimental',
    'custom'
);
CREATE TYPE public.conformer_selection_kind AS ENUM (
    'display_default',
    'curator_pick',
    'lowest_energy',
    'benchmark_reference',
    'preferred_for_thermo',
    'preferred_for_kinetics',
    'representative_geometry'
);
CREATE TYPE public.constraint_kind AS ENUM (
    'cartesian_atom',
    'bond',
    'angle',
    'dihedral',
    'improper'
);
CREATE TYPE public.coordinate_unit AS ENUM (
    'angstrom',
    'degree'
);
CREATE TYPE public.core_treatment AS ENUM (
    'frozen_core',
    'all_electron',
    'window'
);
CREATE TYPE public.dataset_release_status AS ENUM (
    'draft',
    'published',
    'withdrawn'
);
CREATE TYPE public.energy_component_kind AS ENUM (
    'total',
    'reference',
    'correlation',
    'triples',
    'dboc',
    'scalar_relativistic',
    'correlation_excluding_triples'
);
CREATE TYPE public.energy_correction_application_role AS ENUM (
    'zpe',
    'thermal_correction_energy',
    'thermal_correction_enthalpy',
    'thermal_correction_gibbs',
    'entropy_contribution',
    'bac_total',
    'aec_total',
    'soc_total',
    'atomization_reference_adjustment',
    'composite_delta',
    'custom'
);
CREATE TYPE public.energy_correction_convention AS ENUM (
    'electronic_only',
    'electronic_plus_zpe',
    'atom_and_bond_corrected',
    'thermal_enthalpy_298k',
    'other'
);
CREATE TYPE public.energy_correction_scheme_kind AS ENUM (
    'atom_energy',
    'atom_hf',
    'atom_thermal',
    'soc',
    'bac_petersson',
    'bac_melius',
    'isodesmic',
    'other'
);
CREATE TYPE public.energy_unit AS ENUM (
    'hartree',
    'kj_mol',
    'kcal_mol'
);
CREATE TYPE public.energy_zero_convention AS ENUM (
    'lowest_state',
    'entrance_channel',
    'separated_reactants',
    'absolute',
    'other'
);
CREATE TYPE public.enthalpy_reference_kind AS ENUM (
    'formation_298k'
);
CREATE TYPE public.external_source_record_kind AS ENUM (
    'thermoml_article',
    'cccbdb_page'
);
CREATE TYPE public.frequency_scale_kind AS ENUM (
    'fundamental',
    'zpe',
    'enthalpy',
    'entropy',
    'heat_capacity'
);
CREATE TYPE public.group_additivity_component_kind AS ENUM (
    'group',
    'ring_correction',
    'gauche_correction',
    'cis_correction',
    'symmetry_correction',
    'other'
);
CREATE TYPE public.hessian_source AS ENUM (
    'parsed_fchk',
    'parsed_hess',
    'parsed_log',
    'uploaded',
    'derived',
    'parsed_log_archive'
);
CREATE TYPE public.imaginary_mode_disposition AS ENUM (
    'rigid_body_residue',
    'torsion',
    'ring_pucker',
    'intermolecular',
    'symmetry_breaking',
    'unassigned'
);
CREATE TYPE public.irc_direction AS ENUM (
    'forward',
    'reverse',
    'both'
);
CREATE TYPE public.kinetics_calc_role AS ENUM (
    'reactant_energy',
    'product_energy',
    'ts_energy',
    'freq',
    'irc',
    'master_equation',
    'fit_source'
);
CREATE TYPE public.kinetics_degeneracy_convention AS ENUM (
    'already_applied',
    'not_applied',
    'unknown'
);
CREATE TYPE public.kinetics_degeneracy_interpretation AS ENUM (
    'external_symmetry_number',
    'reaction_path_degeneracy',
    'symmetry_number_and_path_degeneracy',
    'no_symmetry_treatment',
    'other'
);
CREATE TYPE public.kinetics_determination_target_kind AS ENUM (
    'whole_reaction',
    'resolved_channel'
);
CREATE TYPE public.kinetics_direction AS ENUM (
    'forward',
    'reverse',
    'net'
);
CREATE TYPE public.kinetics_ensemble_policy AS ENUM (
    'single_structure',
    'lowest_energy_conformer',
    'boltzmann_weighted_conformers',
    'multi_structural_torsional',
    'other'
);
CREATE TYPE public.kinetics_model_kind AS ENUM (
    'arrhenius',
    'modified_arrhenius',
    'lindemann',
    'troe',
    'sri',
    'plog',
    'chebyshev',
    'multi_arrhenius'
);
CREATE TYPE public.kinetics_representation_role AS ENUM (
    'complete',
    'additive_component'
);
CREATE TYPE public.kinetics_standard_state_convention AS ENUM (
    'ideal_gas_1_bar',
    'ideal_gas_1_atm',
    'concentration_1_mol_cm3',
    'concentration_1_mol_l',
    'other'
);
CREATE TYPE public.kinetics_uncertainty_kind AS ENUM (
    'additive',
    'multiplicative'
);
CREATE TYPE public.literature_kind AS ENUM (
    'article',
    'book',
    'thesis',
    'report',
    'dataset',
    'webpage'
);
CREATE TYPE public.literature_title_source AS ENUM (
    'resolved_metadata',
    'depositor_supplied'
);
CREATE TYPE public.machine_review_curator_task_state AS ENUM (
    'untriaged',
    'needs_curator_review',
    'in_curator_review',
    'resolved_no_action',
    'resolved_human_reviewed',
    'dismissed_machine_finding'
);
CREATE TYPE public.machine_review_severity AS ENUM (
    'info',
    'warning',
    'critical'
);
CREATE TYPE public.machine_review_status AS ENUM (
    'not_run',
    'machine_screened_pass',
    'machine_screened_warning',
    'machine_screened_needs_attention',
    'machine_review_failed'
);
CREATE TYPE public.melius_bac_component_kind AS ENUM (
    'atom_corr',
    'bond_corr_length',
    'bond_corr_neighbor',
    'mol_corr'
);
CREATE TYPE public.molecular_property_kind AS ENUM (
    'dipole_moment',
    'quadrupole_moment',
    'polarizability',
    'polarizability_iso',
    'ionization_energy',
    'electron_affinity',
    'proton_affinity',
    'enthalpy_of_formation',
    'atomization_energy',
    'homo_energy',
    'lumo_energy',
    'homo_lumo_gap',
    'rotational_constant',
    'spectroscopic_constant',
    'other',
    'heat_capacity_cp'
);
CREATE TYPE public.molecule_kind AS ENUM (
    'molecule',
    'pseudo',
    'electron'
);
CREATE TYPE public.network_channel_kind AS ENUM (
    'isomerization',
    'association',
    'dissociation',
    'exchange'
);
CREATE TYPE public.network_channel_mechanism AS ENUM (
    'elementary',
    'well_skipping'
);
CREATE TYPE public.network_energy_transfer_scope AS ENUM (
    'per_well',
    'network_wide'
);
CREATE TYPE public.network_kinetics_model_kind AS ENUM (
    'chebyshev',
    'plog',
    'tabulated'
);
CREATE TYPE public.network_representation_role AS ENUM (
    'complete',
    'additive_component',
    'overlapping_contribution'
);
CREATE TYPE public.network_solve_calc_role AS ENUM (
    'well_energy',
    'barrier_energy',
    'well_freq',
    'barrier_freq',
    'master_equation_run',
    'fit_source'
);
CREATE TYPE public.network_solve_kind AS ENUM (
    'computed',
    'reported'
);
CREATE TYPE public.network_species_role AS ENUM (
    'well',
    'reactant',
    'product',
    'bath_gas'
);
CREATE TYPE public.network_state_kind AS ENUM (
    'well',
    'bimolecular',
    'termolecular'
);
CREATE TYPE public.observed_state_basis AS ENUM (
    'ideal_gas',
    'real_gas'
);
CREATE TYPE public.observed_uncertainty_assessor AS ENUM (
    'source_author',
    'source_evaluator'
);
CREATE TYPE public.observed_uncertainty_kind AS ENUM (
    'standard',
    'expanded',
    'combined_standard',
    'combined_expanded'
);
CREATE TYPE public.parameter_statement_kind AS ENUM (
    'requested',
    'reported'
);
CREATE TYPE public.path_search_method AS ENUM (
    'neb',
    'gsm',
    'growing_string',
    'freezing_string',
    'other'
);
CREATE TYPE public.phase_kind AS ENUM (
    'gas',
    'liquid',
    'solid',
    'aqueous'
);
CREATE TYPE public.pno_truncation AS ENUM (
    'loose',
    'normal',
    'tight'
);
CREATE TYPE public.pressure_context AS ENUM (
    'high_p_limit',
    'apparent_at_pressure',
    'pressure_dependent'
);
CREATE TYPE public.pressure_unit AS ENUM (
    'bar',
    'atm'
);
CREATE TYPE public.reaction_role AS ENUM (
    'reactant',
    'product'
);
CREATE TYPE public.read_profile AS ENUM (
    'exploratory',
    'curated'
);
CREATE TYPE public.record_review_event_kind AS ENUM (
    'created',
    'status_change'
);
CREATE TYPE public.record_review_status AS ENUM (
    'not_reviewed',
    'under_review',
    'approved',
    'rejected',
    'deprecated'
);
CREATE TYPE public.relativistic_hamiltonian AS ENUM (
    'none',
    'dkh0',
    'dkh2',
    'dkhso',
    'resc',
    'x2c',
    'zora',
    'iora'
);
CREATE TYPE public.release_artifact_kind AS ENUM (
    'selected_records',
    'candidate_records',
    'review_history',
    'selection_ledger'
);
CREATE TYPE public.release_selection_action AS ENUM (
    'select',
    'supersede',
    'withdraw'
);
CREATE TYPE public.reproducibility_assessor_kind AS ENUM (
    'system',
    'curator'
);
CREATE TYPE public.reproducibility_grade AS ENUM (
    'insufficient',
    'described',
    'auditable',
    'rerunnable'
);
CREATE TYPE public.ri_approximation AS ENUM (
    'none',
    'rij',
    'rijcosx',
    'rijk',
    'rijonx'
);
CREATE TYPE public.rights_basis_kind AS ENUM (
    'depositor_agreement',
    'operator_own_data',
    'historical_review',
    'source_terms'
);
CREATE TYPE public.rigid_rotor_kind AS ENUM (
    'atom',
    'linear',
    'spherical_top',
    'symmetric_top',
    'asymmetric_top'
);
CREATE TYPE public.scan_coordinate_kind AS ENUM (
    'bond',
    'angle',
    'dihedral',
    'improper'
);
CREATE TYPE public.scf_stability_status AS ENUM (
    'stable',
    'unstable',
    'stabilized',
    'inconclusive'
);
CREATE TYPE public.scientific_origin_kind AS ENUM (
    'computed',
    'experimental',
    'estimated'
);
CREATE TYPE public.software_reconciliation_status AS ENUM (
    'matched',
    'enriched',
    'mismatch',
    'declared_only',
    'parsed_only'
);
CREATE TYPE public.software_version_evidence_kind AS ENUM (
    'owner_attestation'
);
CREATE TYPE public.species_entry_review_role AS ENUM (
    'curator',
    'reviewer',
    'validator',
    'linker'
);
CREATE TYPE public.species_entry_state_kind AS ENUM (
    'ground',
    'excited'
);
CREATE TYPE public.species_name_kind AS ENUM (
    'common',
    'systematic',
    'abbreviation'
);
CREATE TYPE public.species_name_source_kind AS ENUM (
    'curated',
    'offline_import'
);
CREATE TYPE public.spin_treatment AS ENUM (
    'restricted',
    'unrestricted',
    'restricted_open',
    'unknown'
);
CREATE TYPE public.stationary_point_kind AS ENUM (
    'minimum',
    'vdw_complex'
);
CREATE TYPE public.statmech_calc_role AS ENUM (
    'opt',
    'freq',
    'sp',
    'scan',
    'composite',
    'imported'
);
CREATE TYPE public.statmech_model_scope_kind AS ENUM (
    'atom',
    'basin',
    'explicit_equilibrium_ensemble',
    'ts_determination'
);
CREATE TYPE public.statmech_model_source_role AS ENUM (
    'geometry',
    'curvature',
    'absolute_energy',
    'relative_energy',
    'electronic_levels',
    'torsional_surface',
    'scale_factor',
    'parameters'
);
CREATE TYPE public.statmech_treatment_kind AS ENUM (
    'rrho',
    'rrho_1d',
    'rrho_nd',
    'rrho_1d_nd',
    'rrho_ad',
    'rrao'
);
CREATE TYPE public.stereo_kind AS ENUM (
    'unspecified',
    'achiral',
    'enantiomer',
    'diastereomer',
    'ez_isomer'
);
CREATE TYPE public.stereo_resolution AS ENUM (
    'resolved',
    'partial',
    'unresolved'
);
CREATE TYPE public.structure_determination_quantity AS ENUM (
    'electronic_energy',
    'zero_kelvin_energy'
);
CREATE TYPE public.structure_determination_target_kind AS ENUM (
    'geometry',
    'conformer_basin',
    'saddle_point'
);
CREATE TYPE public.structure_finding_authority AS ENUM (
    'producer_assertion',
    'authorized_adjudication'
);
CREATE TYPE public.structure_finding_kind AS ENUM (
    'identity_incompatibility',
    'state_incompatibility',
    'path_incompatibility',
    'role_invalidation',
    'contradictory_characterization',
    'adjudication'
);
CREATE TYPE public.structure_finding_scope AS ENUM (
    'geometry',
    'calculation',
    'determination'
);
CREATE TYPE public.structure_finding_verdict AS ENUM (
    'invalidates',
    'does_not_invalidate',
    'unresolved'
);
CREATE TYPE public.structure_source_role AS ENUM (
    'energy',
    'geometry_optimization',
    'curvature',
    'correction',
    'connectivity',
    'alternative_characterization'
);
CREATE TYPE public.submission_actor_kind AS ENUM (
    'user',
    'curator',
    'admin',
    'llm',
    'system'
);
CREATE TYPE public.submission_audit_event_kind AS ENUM (
    'submission_created',
    'ingestion_succeeded',
    'ingestion_failed',
    'llm_precheck_passed',
    'llm_precheck_flagged',
    'curator_approved',
    'curator_rejected',
    'correction_window_opened',
    'correction_uploaded',
    'submission_superseded',
    'status_changed',
    'public_visibility_changed',
    'llm_precheck_recorded',
    'observation_identity_attached'
);
CREATE TYPE public.submission_kind AS ENUM (
    'computed_reaction',
    'conformer',
    'reaction',
    'kinetics',
    'network',
    'network_pdep',
    'thermo',
    'transition_state',
    'transport',
    'other',
    'computed_species',
    'statmech'
);
CREATE TYPE public.submission_precheck_label AS ENUM (
    'passed',
    'flagged'
);
CREATE TYPE public.submission_record_type AS ENUM (
    'species',
    'species_entry',
    'conformer_group',
    'conformer_observation',
    'reaction',
    'reaction_entry',
    'transition_state',
    'transition_state_entry',
    'calculation',
    'statmech',
    'thermo',
    'kinetics',
    'transport',
    'network',
    'network_solve',
    'applied_energy_correction',
    'artifact',
    'molecular_property_observation'
);
CREATE TYPE public.submission_source_kind AS ENUM (
    'api',
    'web',
    'bulk_import',
    'system',
    'migration'
);
CREATE TYPE public.submission_status AS ENUM (
    'pending',
    'precheck_passed',
    'auto_flagged',
    'approved',
    'rejected',
    'superseded',
    'failed'
);
CREATE TYPE public.temperature_unit AS ENUM (
    'kelvin'
);
CREATE TYPE public.thermo_calc_role AS ENUM (
    'opt',
    'freq',
    'sp',
    'composite',
    'imported'
);
CREATE TYPE public.thermo_declaration_source AS ENUM (
    'depositor',
    'chemkin_format'
);
CREATE TYPE public.thermo_model_kind AS ENUM (
    'nasa7',
    'nasa9',
    'wilhoit',
    'tabulated',
    'scalar'
);
CREATE TYPE public.thermo_target_kind AS ENUM (
    'equilibrium_ensemble',
    'single_conformer'
);
CREATE TYPE public.torsion_treatment_kind AS ENUM (
    'hindered_rotor',
    'free_rotor',
    'rigid_top',
    'hindered_rotor_dos'
);
CREATE TYPE public.transition_state_entry_status AS ENUM (
    'guess',
    'optimized',
    'validated',
    'rejected'
);
CREATE TYPE public.transition_state_selection_kind AS ENUM (
    'display_default',
    'curator_pick',
    'lowest_barrier',
    'benchmark_reference',
    'preferred_for_kinetics',
    'representative_geometry'
);
CREATE TYPE public.transport_calc_role AS ENUM (
    'full_transport',
    'dipole',
    'polarizability',
    'supporting_geometry'
);
CREATE TYPE public.tunneling_model AS ENUM (
    'none',
    'wigner',
    'eckart',
    'sct',
    'other'
);
CREATE TYPE public.upload_job_kind AS ENUM (
    'computed_reaction',
    'conformer',
    'reaction',
    'kinetics',
    'network',
    'network_pdep',
    'thermo',
    'transition_state',
    'transport'
);
CREATE TYPE public.upload_job_status AS ENUM (
    'queued',
    'processing',
    'complete',
    'failed'
);
CREATE TYPE public.validation_status AS ENUM (
    'passed',
    'warning',
    'fail'
);
CREATE FUNCTION public.aie_opaque_public_ref() RETURNS text
    LANGUAGE plpgsql
    AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'aie_'; bit_pos integer; value integer;
        BEGIN
          first_hex := replace(gen_random_uuid()::text, '-', '');
          second_hex := replace(gen_random_uuid()::text, '-', '');
          bytes := decode(substring(
            substring(first_hex, 1, 12) || substring(first_hex, 14, 3) || substring(first_hex, 18, 15) ||
            substring(second_hex, 1, 12) || substring(second_hex, 14, 3) || substring(second_hex, 18, 15),
            1, 32), 'hex');
          FOR char_pos IN 0..25 LOOP
            value := 0;
          FOR bit_offset IN 0..4 LOOP
            bit_pos := char_pos * 5 + bit_offset;
              IF bit_pos < 128 THEN
                value := value * 2 + ((get_byte(bytes, bit_pos / 8) >> (7 - (bit_pos % 8))) & 1);
              ELSE value := value * 2; END IF;
            END LOOP;
            out := out || substr('abcdefghijklmnopqrstuvwxyz234567', value + 1, 1);
          END LOOP;
          RETURN out;
        END $$;
CREATE FUNCTION public.art_opaque_public_ref() RETURNS text
    LANGUAGE plpgsql
    AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'art_'; bit_pos integer; value integer;
        BEGIN
          first_hex := replace(gen_random_uuid()::text, '-', '');
          second_hex := replace(gen_random_uuid()::text, '-', '');
          bytes := decode(substring(
            substring(first_hex, 1, 12) || substring(first_hex, 14, 3) || substring(first_hex, 18, 15) ||
            substring(second_hex, 1, 12) || substring(second_hex, 14, 3) || substring(second_hex, 18, 15),
            1, 32), 'hex');
          FOR char_pos IN 0..25 LOOP
            value := 0;
          FOR bit_offset IN 0..4 LOOP
            bit_pos := char_pos * 5 + bit_offset;
              IF bit_pos < 128 THEN
                value := value * 2 + ((get_byte(bytes, bit_pos / 8) >> (7 - (bit_pos % 8))) & 1);
              ELSE value := value * 2; END IF;
            END LOOP;
            out := out || substr('abcdefghijklmnopqrstuvwxyz234567', value + 1, 1);
          END LOOP;
          RETURN out;
        END $$;
CREATE FUNCTION public.mpo_opaque_public_ref() RETURNS text
    LANGUAGE plpgsql
    AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'mpo_'; bit_pos integer; value integer;
        BEGIN
          first_hex := replace(gen_random_uuid()::text, '-', '');
          second_hex := replace(gen_random_uuid()::text, '-', '');
          bytes := decode(substring(
            substring(first_hex, 1, 12) || substring(first_hex, 14, 3) || substring(first_hex, 18, 15) ||
            substring(second_hex, 1, 12) || substring(second_hex, 14, 3) || substring(second_hex, 18, 15),
            1, 32), 'hex');
          FOR char_pos IN 0..25 LOOP
            value := 0;
          FOR bit_offset IN 0..4 LOOP
            bit_pos := char_pos * 5 + bit_offset;
              IF bit_pos < 128 THEN
                value := value * 2 + ((get_byte(bytes, bit_pos / 8) >> (7 - (bit_pos % 8))) & 1);
              ELSE value := value * 2; END IF;
            END LOOP;
            out := out || substr('abcdefghijklmnopqrstuvwxyz234567', value + 1, 1);
          END LOOP;
          RETURN out;
        END $$;
CREATE FUNCTION public.network_solve_computed_evidence() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    solve record;
    applicable boolean;
BEGIN
    IF TG_TABLE_NAME = 'network_solve' THEN
        target_id := NEW.id;
    ELSE
        -- Evidence tables: only losing a row can break an existence rule, so
        -- the solve at risk is always the one OLD pointed at.
        target_id := OLD.solve_id;
    END IF;

    SELECT s.id, s.kind, s.network_id, s.public_ref
      INTO solve
      FROM network_solve s
     WHERE s.id = target_id;

    IF NOT FOUND THEN
        -- Solve deleted in this same transaction (network teardown).
        RETURN NULL;
    END IF;

    -- A reported solve holds none of these inputs by construction (ADR 0010).
    IF solve.kind <> 'computed' THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM network_solve_state_energy e WHERE e.solve_id = solve.id
    ) THEN
        RAISE EXCEPTION
            'computed_requires_state_energy: network_solve % declares '
            'kind=''computed'', which asserts that this database holds the '
            'master-equation derivation behind its k(T,P), but no '
            'network_solve_state_energy row was committed for it.',
            coalesce(solve.public_ref, '(no public_ref)')
            USING ERRCODE = '23514',
                  HINT = 'Deposit the state energies with the solve, or '
                         'declare kind=''reported'' and cite the publication '
                         'the rates were transcribed from (ADR 0010).';
    END IF;

    -- Gated on the topology so the trigger is never stricter than
    -- validate_mechanistic_channel_evidence, which requires exactly zero
    -- energy-transfer rows for a network that declares no wells.
    SELECT 
    EXISTS (
        SELECT 1 FROM network_state st
         WHERE st.network_id = solve.network_id
           AND st.kind = 'well'
    )
 INTO applicable;

    IF applicable AND NOT EXISTS (
        SELECT 1 FROM network_solve_energy_transfer t WHERE t.solve_id = solve.id
    ) THEN
        RAISE EXCEPTION
            'computed_requires_energy_transfer: network_solve % declares '
            'kind=''computed'' on a network with an energized well, but no '
            'network_solve_energy_transfer row was committed for it. '
            'Collisional energy transfer is what makes the network '
            'pressure-dependent; without it there is no solve to speak of.',
            coalesce(solve.public_ref, '(no public_ref)')
            USING ERRCODE = '23514',
                  HINT = 'Supply one per_well entry per (well, bath-gas '
                         'collider) pair, or a single network_wide entry '
                         '(ADR 0009); or declare kind=''reported'' (ADR 0010).';
    END IF;

    -- Likewise gated: an all-barrierless network is correct science with
    -- legitimately zero barriers, and refusing a correct result is what
    -- ADR 0008 forbids the blocking tier to do.
    SELECT 
    EXISTS (
        SELECT 1
          FROM network_channel_microreaction m
          JOIN network_channel c ON c.id = m.channel_id
         WHERE c.network_id = solve.network_id
           AND m.transition_state_entry_id IS NOT NULL
    )

      INTO applicable;

    IF applicable AND NOT EXISTS (
        SELECT 1 FROM network_solve_channel_barrier b WHERE b.solve_id = solve.id
    ) THEN
        RAISE EXCEPTION
            'computed_requires_channel_barrier: network_solve % declares '
            'kind=''computed'' on a network with at least one saddle-point '
            'path, but no network_solve_channel_barrier row was committed '
            'for it. The barrier heights are what the microcanonical rates '
            'were computed from.',
            coalesce(solve.public_ref, '(no public_ref)')
            USING ERRCODE = '23514',
                  HINT = 'Supply one barrier per saddle-point channel path '
                         'and none for a barrierless one; or declare '
                         'kind=''reported'' (ADR 0010).';
    END IF;

    RETURN NULL;
END;
$$;
CREATE FUNCTION public.network_solve_computed_evidence_truncate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    computed_count bigint;
BEGIN
    SELECT count(*) INTO computed_count
      FROM network_solve
     WHERE kind = 'computed';

    IF computed_count > 0 THEN
        RAISE EXCEPTION
            'truncate_would_strand_computed_evidence: TRUNCATE on % would '
            'remove master-equation evidence while % network_solve row(s) '
            'still declare kind=''computed'', which asserts that this '
            'database holds the derivation behind their k(T,P).',
            TG_TABLE_NAME, computed_count
            USING ERRCODE = '23514',
                  HINT = 'Use DELETE instead -- it is row-level, so the '
                         'deferred constraint triggers check each affected '
                         'solve at COMMIT. To discard the solves too, delete '
                         'them first, or re-declare them kind=''reported'' '
                         'with the publication their rates came from '
                         '(ADR 0010).';
    END IF;

    RETURN NULL;
END;
$$;
CREATE FUNCTION public.reject_deposit_warning_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning is append-only';
        END;
        $$;
CREATE FUNCTION public.reject_deposit_warning_retirement_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning_retirement is append-only';
        END;
        $$;
CREATE FUNCTION public.reject_execution_environment_manifest_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ BEGIN
          RAISE EXCEPTION 'execution_environment_manifest is immutable';
        END $$;
CREATE FUNCTION public.reject_release_artifact_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'release_artifact is append-only';
            END;
            $$;
CREATE FUNCTION public.reject_release_manifest_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'release_manifest is append-only';
            END;
            $$;
CREATE FUNCTION public.reject_release_selection_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                RAISE EXCEPTION USING
                    ERRCODE = '55000',
                    MESSAGE = 'release_selection is append-only';
            END;
            $$;
CREATE FUNCTION public.reject_reproducibility_assessment_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'record_reproducibility_assessment is append-only';
        END;
        $$;
CREATE FUNCTION public.reject_submission_rights_attestation_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'submission_rights_attestation is append-only';
        END;
        $$;
CREATE FUNCTION public.rpa_opaque_public_ref() RETURNS text
    LANGUAGE plpgsql
    AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'rpa_'; bit_pos integer; value integer;
        BEGIN
          first_hex := replace(gen_random_uuid()::text, '-', '');
          second_hex := replace(gen_random_uuid()::text, '-', '');
          bytes := decode(substring(
            substring(first_hex, 1, 12) || substring(first_hex, 14, 3) || substring(first_hex, 18, 15) ||
            substring(second_hex, 1, 12) || substring(second_hex, 14, 3) || substring(second_hex, 18, 15),
            1, 32), 'hex');
          FOR char_pos IN 0..25 LOOP
            value := 0;
          FOR bit_offset IN 0..4 LOOP
            bit_pos := char_pos * 5 + bit_offset;
              IF bit_pos < 128 THEN
                value := value * 2 + ((get_byte(bytes, bit_pos / 8) >> (7 - (bit_pos % 8))) & 1);
              ELSE value := value * 2; END IF;
            END LOOP;
            out := out || substr('abcdefghijklmnopqrstuvwxyz234567', value + 1, 1);
          END LOOP;
          RETURN out;
        END $$;
CREATE FUNCTION public.tckdb_calculation_dependency_acyclic() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    parent_ref text;
    child_ref text;
BEGIN
    -- A role-only update keeps the endpoints, so the graph shape is unchanged.
    IF TG_OP = 'UPDATE'
       AND OLD.parent_calculation_id = NEW.parent_calculation_id
       AND OLD.child_calculation_id = NEW.child_calculation_id THEN
        RETURN NEW;
    END IF;

    -- A self-edge is the not_self CHECK's to refuse (BEFORE triggers fire ahead
    -- of CHECK constraints), so it keeps its own, more specific error.
    IF NEW.parent_calculation_id = NEW.child_calculation_id THEN
        RETURN NEW;
    END IF;

    -- The advisory lock is already held: the statement-level trigger took it
    -- before any row lock. UNION (not UNION ALL) drops revisited nodes, so the
    -- walk terminates even if a cycle somehow exists.
    IF EXISTS (
        WITH RECURSIVE reach(id) AS (
            SELECT NEW.child_calculation_id
            UNION
            SELECT d.child_calculation_id
            FROM reach r
            JOIN public.calculation_dependency d ON d.parent_calculation_id = r.id
            WHERE NOT (
                TG_OP = 'UPDATE'
                AND d.parent_calculation_id = OLD.parent_calculation_id
                AND d.child_calculation_id = OLD.child_calculation_id
            )
        )
        SELECT 1 FROM reach WHERE id = NEW.parent_calculation_id
    ) THEN
        SELECT public_ref INTO parent_ref FROM public.calculation
            WHERE id = NEW.parent_calculation_id;
        SELECT public_ref INTO child_ref FROM public.calculation
            WHERE id = NEW.child_calculation_id;
        RAISE EXCEPTION 'calculation_dependency_cycle: the dependency edge would close a cycle'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'calculation_dependency_acyclic',
                  DETAIL = format('parent=%s;child=%s',
                                  coalesce(parent_ref, ''), coalesce(child_ref, '')),
                  HINT = 'The parent is already reachable from the child through existing dependency edges.';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.tckdb_calculation_dependency_acyclic_lock() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('calculation_dependency:acyclic', 0));
    RETURN NULL;
END;
$$;
CREATE FUNCTION public.tckdb_calculation_lot_not_merged() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            IF NEW.lot_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.lot_id IS NOT DISTINCT FROM OLD.lot_id THEN
                RETURN NEW;
            END IF;

            -- Take the lock the foreign key would take, first, so a merge
            -- committing on this row is either visible below or waits for us.
            PERFORM 1 FROM public.level_of_theory WHERE id = NEW.lot_id FOR KEY SHARE;

            IF EXISTS (
                SELECT 1 FROM public.level_of_theory_merge m
                 WHERE m.merged_lot_id = NEW.lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a calculation cannot use a level of theory that was merged into another',
                    HINT = 'use the level of theory it was merged into';
            END IF;

            RETURN NEW;
        END
        $$;
CREATE FUNCTION public.tckdb_freeze_merged_row() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE
            marker text := TG_ARGV[0];
            changed text[];
        BEGIN
            -- A merged-away row is a record of what it was and is frozen, absolutely: these tables carry no
            -- accepted-science guard, so no repair can be declared for them either. An update that changes nothing is
            -- let through. A later change to a merged row is a migration that replaces this trigger.
            IF (to_jsonb(OLD) ->> marker) IS NULL OR to_jsonb(NEW) = to_jsonb(OLD) THEN
                RETURN NEW;
            END IF;
            changed := ARRAY(
                SELECT key FROM jsonb_each(to_jsonb(NEW)) WHERE (to_jsonb(OLD) -> key) IS DISTINCT FROM value
            );
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = format('a merged-away %I row cannot be edited (columns %s)', TG_TABLE_NAME, array_to_string(changed, ', ')),
                HINT = format('merged rows are frozen; edit the row it was merged into, or change this with a migration replacing trg_%s_freeze_merged', TG_TABLE_NAME);
        END;
        $$;
CREATE FUNCTION public.tckdb_guard_accepted_child() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE record_ids bigint[] := ARRAY[]::bigint[];
                argument_index integer; record_id bigint;
        BEGIN
            FOR argument_index IN 1..TG_NARGS - 1 LOOP
                IF TG_OP <> 'INSERT' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(OLD)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(NEW)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
            END LOOP;
            FOR record_id IN
                SELECT DISTINCT value
                FROM unnest(record_ids) AS value
                WHERE value IS NOT NULL
                ORDER BY value
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    TG_ARGV[0]::public.submission_record_type, record_id
                );
                IF public.tckdb_record_is_accepted(
                    TG_ARGV[0]::public.submission_record_type, record_id
                ) THEN
                    PERFORM public.tckdb_raise_if_accepted(
                        TG_ARGV[0]::public.submission_record_type, record_id,
                        TG_TABLE_SCHEMA, TG_TABLE_NAME,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                    );
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$;
CREATE FUNCTION public.tckdb_guard_accepted_root() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE record_id bigint;
        BEGIN
            IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
            record_id := OLD.id;
            PERFORM public.tckdb_lock_scientific_record(
                TG_ARGV[0]::public.submission_record_type, record_id
            );
            IF public.tckdb_record_is_accepted(
                TG_ARGV[0]::public.submission_record_type, record_id
            ) THEN
                PERFORM public.tckdb_raise_if_accepted(
                    TG_ARGV[0]::public.submission_record_type, record_id,
                    TG_TABLE_SCHEMA, TG_TABLE_NAME,
                    CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                );
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$;
CREATE FUNCTION public.tckdb_guard_accepted_via_child() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
        DECLARE parent_id bigint; record_id bigint;
        BEGIN
            FOR parent_id IN
                SELECT DISTINCT value::bigint
                FROM unnest(ARRAY[
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD)->>TG_ARGV[1] END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW)->>TG_ARGV[1] END
                ]) AS value
                WHERE value IS NOT NULL
                ORDER BY value::bigint
            LOOP
                EXECUTE format(
                    'SELECT %I FROM public.%I WHERE %I = $1',
                    TG_ARGV[4], TG_ARGV[2], TG_ARGV[3]
                ) INTO record_id USING parent_id;
                IF record_id IS NOT NULL THEN
                    PERFORM public.tckdb_lock_scientific_record(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    );
                    IF public.tckdb_record_is_accepted(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    ) THEN
                        PERFORM public.tckdb_raise_if_accepted(
                            TG_ARGV[0]::public.submission_record_type, record_id,
                            TG_TABLE_SCHEMA, TG_TABLE_NAME,
                            CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                            CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                        );
                    END IF;
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $_$;
CREATE FUNCTION public.tckdb_guard_calculation_geometry() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE geometry_ids bigint[] := ARRAY[]::bigint[]; calculation_id bigint;
        BEGIN
            IF TG_TABLE_NAME = 'geometry' THEN
                IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
                IF TG_OP = 'DELETE' THEN
                    geometry_ids := ARRAY[OLD.id];
                ELSE
                    geometry_ids := ARRAY[OLD.id, NEW.id];
                END IF;
            ELSE
                IF TG_OP <> 'INSERT' THEN
                    geometry_ids := array_append(geometry_ids, OLD.geometry_id);
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    geometry_ids := array_append(geometry_ids, NEW.geometry_id);
                END IF;
            END IF;
            FOR calculation_id IN
                SELECT DISTINCT linked.calculation_id
                FROM (
                    SELECT cig.calculation_id
                    FROM public.calculation_input_geometry cig
                    WHERE cig.geometry_id = ANY(geometry_ids)
                    UNION
                    SELECT cog.calculation_id
                    FROM public.calculation_output_geometry cog
                    WHERE cog.geometry_id = ANY(geometry_ids)
                ) AS linked
                ORDER BY linked.calculation_id
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    'calculation', calculation_id
                );
                IF public.tckdb_record_is_accepted('calculation', calculation_id) THEN
                    PERFORM public.tckdb_raise_if_accepted(
                        'calculation', calculation_id,
                        TG_TABLE_SCHEMA, TG_TABLE_NAME,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                    );
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$;
CREATE FUNCTION public.tckdb_guard_merge_marker() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
        DECLARE
            marker text := TG_ARGV[0];
            target_id bigint := (to_jsonb(NEW) ->> marker)::bigint;
            chained boolean;
        BEGIN
            IF target_id IS NULL THEN
                RETURN NEW;
            END IF;
            -- A merge is one hop: the target is live, and nothing still forwards to the row being merged.
            EXECUTE format('SELECT %I IS NOT NULL FROM public.%I WHERE id = $1', marker, TG_TABLE_NAME)
               INTO chained USING target_id;
            IF coalesce(chained, false) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = format('a %I row cannot be merged into one that is itself merged', TG_TABLE_NAME),
                    HINT = 'merge into the row the target was merged into; a merge is one hop';
            END IF;
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I WHERE %I = $1)', TG_TABLE_NAME, marker)
               INTO chained USING NEW.id;
            IF chained THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = format('a %I row that other rows were merged into cannot itself be merged', TG_TABLE_NAME),
                    HINT = 're-point the rows that forward to it first; a merge is one hop';
            END IF;
            RETURN NEW;
        END;
        $_$;
CREATE FUNCTION public.tckdb_guard_record_review() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'record_review rows cannot be deleted';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.record_type IS DISTINCT FROM OLD.record_type
                   OR NEW.record_id IS DISTINCT FROM OLD.record_id THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'record_review natural keys are immutable';
                END IF;
                IF OLD.first_approved_at IS NOT NULL
                   AND NEW.first_approved_at IS DISTINCT FROM OLD.first_approved_at THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'first_approved_at is immutable once set';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.first_approved_at IS NULL
               AND NEW.first_approved_at IS NOT NULL
               AND NEW.status <> 'approved' THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'first_approved_at may only be established by approval';
            END IF;
            IF public.tckdb_is_accepted_science_type(NEW.record_type)
               AND (NEW.status = 'approved' OR NEW.first_approved_at IS NOT NULL) THEN
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.record_id
                );
            END IF;
            IF NEW.status = 'approved' THEN
                IF NEW.first_approved_at IS NULL THEN
                    NEW.first_approved_at := coalesce(NEW.reviewed_at, clock_timestamp());
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
CREATE FUNCTION public.tckdb_guard_thermo_enthalpy_reference() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND NEW.h298_kj_mol IS NOT DISTINCT FROM OLD.h298_kj_mol
               AND NEW.enthalpy_reference_kind IS NOT DISTINCT FROM OLD.enthalpy_reference_kind
            THEN
                RETURN NEW;
            END IF;
            IF NEW.h298_kj_mol IS NOT NULL AND NEW.enthalpy_reference_kind IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'thermo.h298_kj_mol requires enthalpy_reference_kind to be declared';
            END IF;
            RETURN NEW;
        END;
        $$;
CREATE FUNCTION public.tckdb_is_accepted_science_type(p_type public.submission_record_type) RETURNS boolean
    LANGUAGE sql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $$
            SELECT p_type::text IN ('calculation', 'thermo', 'statmech', 'kinetics', 'transport', 'network', 'network_solve', 'applied_energy_correction', 'transition_state_entry', 'conformer_observation')
        $$;
CREATE FUNCTION public.tckdb_kinetics_determination_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    RAISE EXCEPTION
        'kinetics_determination_is_immutable: determination % cannot be changed. A '
        'determination is identity: it is shared by every record that states it, so a '
        'change would silently re-describe all of them.', OLD.id
        USING ERRCODE = '23514',
              HINT = 'Join or create a different determination instead of editing this one.';
END;
$$;
CREATE FUNCTION public.tckdb_lock_scientific_record(p_type public.submission_record_type, p_record_id bigint) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            CASE p_type::text
                
            WHEN 'calculation' THEN
                PERFORM 1 FROM public.calculation WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'thermo' THEN
                PERFORM 1 FROM public.thermo WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'statmech' THEN
                PERFORM 1 FROM public.statmech WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'kinetics' THEN
                PERFORM 1 FROM public.kinetics WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'transport' THEN
                PERFORM 1 FROM public.transport WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'network' THEN
                PERFORM 1 FROM public.network WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'network_solve' THEN
                PERFORM 1 FROM public.network_solve WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'applied_energy_correction' THEN
                PERFORM 1 FROM public.applied_energy_correction WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'transition_state_entry' THEN
                PERFORM 1 FROM public.transition_state_entry WHERE id = p_record_id FOR UPDATE;
        

            WHEN 'conformer_observation' THEN
                PERFORM 1 FROM public.conformer_observation WHERE id = p_record_id FOR UPDATE;
        
                ELSE
                    RAISE EXCEPTION USING ERRCODE = '22023',
                        MESSAGE = 'unsupported accepted-science record type: ' || p_type::text;
            END CASE;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503',
                    MESSAGE = format('%s record %s does not exist', p_type, p_record_id);
            END IF;
        END;
        $$;
CREATE FUNCTION public.tckdb_lot_merge_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            -- Lock both levels of theory, lowest id first, so two merges
            -- that meet in the middle (A -> B and B -> C) serialise, and
            -- so a calculation being inserted on either one has committed
            -- (and is visible below) or waits until this merge has.
            PERFORM 1 FROM public.level_of_theory
             WHERE id IN (NEW.merged_lot_id, NEW.into_lot_id)
             ORDER BY id
               FOR UPDATE;

            IF EXISTS (
                SELECT 1 FROM public.level_of_theory_merge m
                 WHERE m.merged_lot_id = NEW.into_lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a level of theory cannot be merged into one that is itself merged',
                    HINT = 'merge into the row the target was merged into; a merge is one hop';
            END IF;

            IF EXISTS (
                SELECT 1 FROM public.level_of_theory_merge m
                 WHERE m.into_lot_id = NEW.merged_lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a level of theory that other merges point at cannot itself be merged',
                    HINT = 're-aim those merges at the new target first; a merge is one hop';
            END IF;

            IF EXISTS (
                SELECT 1 FROM public.calculation c
                 WHERE c.lot_id = NEW.merged_lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a level of theory that calculations still use cannot be merged',
                    HINT = 'move its calculations to the kept row first';
            END IF;

            RETURN NEW;
        END
        $$;
CREATE FUNCTION public.tckdb_network_kinetics_determination_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    RAISE EXCEPTION
        'network_kinetics_determination_is_immutable: determination % cannot be changed. A '
        'determination is identity: it is shared by every fit that states it, so a '
        'change would silently re-describe all of them.', OLD.id
        USING ERRCODE = '23514',
              HINT = 'Join or create a different determination instead of editing this one.';
END;
$$;
CREATE FUNCTION public.tckdb_raise_if_accepted(p_type public.submission_record_type, p_record_id bigint, p_schema text DEFAULT NULL::text, p_table text DEFAULT NULL::text, p_old jsonb DEFAULT NULL::jsonb, p_new jsonb DEFAULT NULL::jsonb) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
DECLARE
    record_ref text;
BEGIN
    IF NOT public.tckdb_record_is_accepted(p_type, p_record_id) THEN
        RETURN;
    END IF;
    IF public.tckdb_repair_permits(
        p_type, p_record_id, p_schema, p_table, p_old, p_new
    ) THEN
        RETURN;
    END IF;
    BEGIN
        EXECUTE format('SELECT public_ref FROM public.%I WHERE id = $1', p_type::text)
            INTO record_ref USING p_record_id;
    EXCEPTION WHEN undefined_column OR undefined_table THEN
        record_ref := NULL;
    END;
    RAISE EXCEPTION USING ERRCODE = '55000',
        MESSAGE = format(
            'accepted %s record %s is immutable', p_type,
            coalesce(record_ref, 'without a public reference')
        ),
        HINT = 'Create and approve a replacement, then record a supersession. '
               'A repair that changes no science must first declare its table '
               'and columns in accepted_science_repair.';
END;
$_$;
CREATE FUNCTION public.tckdb_reaction_atom_map_source_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    IF NEW.source IS DISTINCT FROM OLD.source THEN
        RAISE EXCEPTION
            'atom_map_source_is_immutable: reaction_atom_map % was written '
            'with source=''%'' and cannot be changed to ''%''. An atom map '
            'records who stands behind a mechanism claim: relabelling an '
            'inferred map as declared manufactures human attribution, and '
            'relabelling a declared map as inferred withdraws one the '
            'depositor made (ADR 0011).',
            OLD.id, OLD.source, NEW.source
            USING ERRCODE = '23514',
                  HINT = 'To correct a mis-declared map whose transition-state '
                         'entry has never been approved, delete it and deposit '
                         'the real one. Once that entry has been approved the '
                         'map is immutable too: deposit a new transition-state '
                         'entry carrying the corrected map, approve it, and '
                         'record a transition_state_entry supersession. Its '
                         'note and equivalent_map_count remain editable in '
                         'place while the entry is unapproved.';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.tckdb_record_is_accepted(p_type public.submission_record_type, p_record_id bigint) RETURNS boolean
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog', 'public'
    AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = p_type AND record_id = p_record_id
                  AND first_approved_at IS NOT NULL
            )
        $$;
CREATE FUNCTION public.tckdb_refuse_merged_target() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
        DECLARE
            target_table text := TG_ARGV[0];
            fk_column text := TG_ARGV[1];
            marker text := TG_ARGV[2];
            target_id bigint := (to_jsonb(NEW) ->> fk_column)::bigint;
            merged bigint;
        BEGIN
            IF target_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) ->> fk_column) IS NOT DISTINCT FROM (to_jsonb(NEW) ->> fk_column) THEN
                RETURN NEW;
            END IF;
            EXECUTE format('SELECT %I FROM public.%I WHERE id = $1', marker, target_table) INTO merged USING target_id;
            IF merged IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = format('%I.%I names a merged-away %I row', TG_TABLE_NAME, fk_column, target_table),
                    HINT = 'use the row it was merged into; a merged row forwards to it';
            END IF;
            RETURN NEW;
        END;
        $_$;
CREATE FUNCTION public.tckdb_reject_mutation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = TG_TABLE_NAME || ' is append-only';
        END;
        $$;
CREATE FUNCTION public.tckdb_reject_truncate() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = TG_TABLE_NAME || ' cannot be truncated';
        END;
        $$;
CREATE FUNCTION public.tckdb_repair_permits(p_type public.submission_record_type, p_record_id bigint, p_schema text, p_table text, p_old jsonb, p_new jsonb) RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE
            declaration public.accepted_science_repair%ROWTYPE;
            changed_columns text[];
            undeclared_columns text[];
            row_identity jsonb;
        BEGIN
            -- INSERT and DELETE are never repairable: p_old or p_new is NULL
            -- for them, and a repair may not change how many rows an accepted
            -- record is made of.
            IF p_old IS NULL OR p_new IS NULL OR p_table IS NULL THEN
                RETURN false;
            END IF;

            SELECT * INTO declaration
              FROM public.accepted_science_repair
             WHERE target_schema = p_schema
               AND target_table = p_table
               AND xact_id = (pg_current_xact_id())::text::bigint;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            IF clock_timestamp() >= declaration.expires_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'repair declaration %s for %I.%I expired at %s',
                        declaration.id, p_schema, p_table, declaration.expires_at
                    ),
                    HINT = 'A declaration is not a standing permission; declare the repair again.';
            END IF;

            SELECT coalesce(array_agg(entry.key ORDER BY entry.key), ARRAY[]::text[])
              INTO changed_columns
              FROM jsonb_each(p_old) AS entry
             WHERE entry.value IS DISTINCT FROM (p_new -> entry.key);

            SELECT array_agg(column_name ORDER BY column_name)
              INTO undeclared_columns
              FROM unnest(changed_columns) AS column_name
             WHERE NOT (column_name = ANY (declaration.declared_columns));
            IF undeclared_columns IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'repair declaration %s for %I.%I does not declare column(s) %s',
                        declaration.id, p_schema, p_table,
                        array_to_string(undeclared_columns, ', ')
                    ),
                    HINT = 'A repair may only change the columns it declared.';
            END IF;
            IF cardinality(changed_columns) = 0 THEN
                RETURN true;
            END IF;

            SELECT jsonb_object_agg(attribute.attname, p_old -> attribute.attname)
              INTO row_identity
              FROM pg_index AS index_
              JOIN pg_attribute AS attribute
                ON attribute.attrelid = index_.indrelid
               AND attribute.attnum = ANY (index_.indkey)
             WHERE index_.indrelid = format('%I.%I', p_schema, p_table)::regclass
               AND index_.indisprimary;
            IF row_identity IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'cannot record a repair to %I.%I: it has no primary key',
                        p_schema, p_table
                    );
            END IF;

            INSERT INTO public.accepted_science_repair_change (
                repair_id, record_type, record_id, target_schema, target_table,
                row_identity, changed_columns, before_json, after_json
            ) VALUES (
                declaration.id, p_type, p_record_id, p_schema, p_table,
                row_identity, changed_columns,
                (
                    SELECT coalesce(jsonb_object_agg(name, p_old -> name), '{}'::jsonb)
                      FROM unnest(changed_columns) AS name
                ),
                (
                    SELECT coalesce(jsonb_object_agg(name, p_new -> name), '{}'::jsonb)
                      FROM unnest(changed_columns) AS name
                )
            );
            RETURN true;
        END;
        $$;
CREATE FUNCTION public.tckdb_statmech_model_declaration_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE host_species bigint; host_ts bigint;
BEGIN
    SELECT species_entry_id, transition_state_entry_id INTO host_species, host_ts
    FROM public.statmech WHERE id = NEW.statmech_id;
    IF NEW.scope_kind = 'ts_determination' THEN
        IF host_ts IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.structure_determination d
            WHERE d.id = NEW.structure_determination_id
              AND d.transition_state_entry_id = host_ts AND d.target_kind = 'saddle_point'
        ) THEN
            RAISE EXCEPTION
                'statmech_model_declaration_owner: a ts_determination scope names a saddle determination of the record''s own transition state entry'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF host_species IS NULL THEN
            RAISE EXCEPTION
                'statmech_model_declaration_owner: an atom, basin or ensemble scope belongs to a species-owned statmech record'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.scope_kind = 'basin' AND NOT EXISTS (
            SELECT 1 FROM public.conformer_group g
            WHERE g.id = NEW.conformer_group_id AND g.species_entry_id = host_species
        ) THEN
            RAISE EXCEPTION
                'statmech_model_declaration_owner: a basin scope names a conformer group of the record''s own species entry'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.tckdb_statmech_model_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE
    changed_columns text[];
    root_statmech_id bigint;
    host_species bigint;
    allowed_columns text[];
BEGIN
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME IN ('statmech_model_declaration', 'statmech_model_member') THEN
        SELECT coalesce(array_agg(entry.key ORDER BY entry.key), ARRAY[]::text[])
          INTO changed_columns
          FROM jsonb_each(to_jsonb(OLD)) AS entry
         WHERE entry.value IS DISTINCT FROM (to_jsonb(NEW) -> entry.key);

        IF TG_TABLE_NAME = 'statmech_model_declaration' THEN
            allowed_columns := ARRAY['conformer_group_id', 'content_hash'];
            root_statmech_id := OLD.statmech_id;
        ELSE
            allowed_columns := ARRAY['conformer_group_id'];
            SELECT statmech_id INTO root_statmech_id
              FROM public.statmech_model_declaration WHERE id = OLD.declaration_id;
        END IF;

        IF cardinality(changed_columns) > 0 AND changed_columns <@ allowed_columns THEN
            SELECT species_entry_id INTO host_species FROM public.statmech WHERE id = root_statmech_id;

            -- A repair re-points an existing basin at a group of the record's own species entry.
            IF 'conformer_group_id' = ANY (changed_columns) AND (
                OLD.conformer_group_id IS NULL
                OR NEW.conformer_group_id IS NULL
                OR host_species IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM public.conformer_group g
                     WHERE g.id = NEW.conformer_group_id AND g.species_entry_id = host_species
                )
            ) THEN
                RAISE EXCEPTION '% is an immutable declared model: a basin repair must name a conformer group of the declaring record''s own species entry', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            -- The checksum may follow a basin that moved in this transaction, and nothing else.
            IF 'content_hash' = ANY (changed_columns) AND NOT ('conformer_group_id' = ANY (changed_columns)) AND NOT EXISTS (
                SELECT 1
                  FROM public.accepted_science_repair_change c
                  JOIN public.accepted_science_repair r ON r.id = c.repair_id
                 WHERE r.xact_id = (pg_current_xact_id())::text::bigint
                   AND 'conformer_group_id' = ANY (c.changed_columns)
                   AND (
                        (c.target_table = 'statmech_model_declaration' AND (c.row_identity ->> 'id')::bigint = OLD.id)
                     OR (c.target_table = 'statmech_model_member' AND (c.row_identity ->> 'id')::bigint IN (
                            SELECT m.id FROM public.statmech_model_member m WHERE m.declaration_id = OLD.id))
                   )
            ) THEN
                RAISE EXCEPTION 'statmech_model_declaration is an immutable declared model: content_hash may only follow a basin moved in the same repair'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            -- Under an accepted statmech record the accepted-science guard (trg_as_*, which fires first) has already
            -- required a declaration covering these columns and recorded the change.
            IF public.tckdb_record_is_accepted(CAST('statmech' AS public.submission_record_type), root_statmech_id) THEN
                RETURN NEW;
            END IF;
            -- Otherwise ask the repair mechanism directly: it checks the declaration and records old -> new.
            IF public.tckdb_repair_permits(
                CAST('statmech' AS public.submission_record_type), root_statmech_id,
                'public', TG_TABLE_NAME, to_jsonb(OLD), to_jsonb(NEW)
            ) THEN
                RETURN NEW;
            END IF;
        END IF;
    END IF;
    RAISE EXCEPTION '% is an immutable declared model: supersede the statmech record instead of editing %', TG_TABLE_NAME, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
CREATE FUNCTION public.tckdb_statmech_model_member_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE host_id bigint; host_scope public.statmech_model_scope_kind; host_species bigint; member_species bigint;
BEGIN
    SELECT d.statmech_id, d.scope_kind INTO host_id, host_scope
    FROM public.statmech_model_declaration d WHERE d.id = NEW.declaration_id;
    IF host_scope IS DISTINCT FROM 'explicit_equilibrium_ensemble' THEN
        RAISE EXCEPTION
            'statmech_model_member_scope: only an explicit_equilibrium_ensemble declaration has members'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.member_statmech_id = host_id THEN
        RAISE EXCEPTION
            'statmech_model_member_self: a declaration is not a member of itself'
            USING ERRCODE = '23514';
    END IF;
    SELECT species_entry_id INTO host_species FROM public.statmech WHERE id = host_id;
    SELECT species_entry_id INTO member_species FROM public.statmech WHERE id = NEW.member_statmech_id;
    IF member_species IS NULL OR member_species IS DISTINCT FROM host_species THEN
        RAISE EXCEPTION
            'statmech_model_member_owner: a member is a statmech record of the same species entry'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.conformer_group_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.conformer_group g
        WHERE g.id = NEW.conformer_group_id AND g.species_entry_id = host_species
    ) THEN
        RAISE EXCEPTION
            'statmech_model_member_owner: a member''s basin is a conformer group of the same species entry'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.tckdb_statmech_model_source_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
DECLARE host_species bigint; host_ts bigint; calc_species bigint; calc_ts bigint;
BEGIN
    IF NEW.calculation_id IS NOT NULL THEN
        SELECT s.species_entry_id, s.transition_state_entry_id INTO host_species, host_ts
        FROM public.statmech_model_declaration d JOIN public.statmech s ON s.id = d.statmech_id
        WHERE d.id = NEW.declaration_id;
        SELECT species_entry_id, transition_state_entry_id INTO calc_species, calc_ts
        FROM public.calculation WHERE id = NEW.calculation_id;
        IF NOT ((host_species IS NOT NULL AND calc_species = host_species)
                OR (host_ts IS NOT NULL AND calc_ts = host_ts)) THEN
            RAISE EXCEPTION
                'statmech_model_source_owner: a source calculation belongs to the declaring record''s own subject'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.geometry_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.calculation_input_geometry i
            WHERE i.calculation_id = NEW.calculation_id AND i.geometry_id = NEW.geometry_id
        ) AND NOT EXISTS (
            SELECT 1 FROM public.calculation_output_geometry o
            WHERE o.calculation_id = NEW.calculation_id AND o.geometry_id = NEW.geometry_id
        ) THEN
            RAISE EXCEPTION
                'statmech_model_source_geometry: a stated geometry is one the source calculation ran on or produced'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.tckdb_structure_determination_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    -- A basin whose owner is not a species entry is refused by the target check, with its own message.
    IF NEW.conformer_observation_id IS NOT NULL AND NEW.species_entry_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM public.conformer_observation o
        JOIN public.conformer_group g ON g.id = o.conformer_group_id
        WHERE o.id = NEW.conformer_observation_id AND g.species_entry_id = NEW.species_entry_id
    ) THEN
        RAISE EXCEPTION
            'structure_determination_observation_owner: a basin claim names an observation of its own species entry'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.tckdb_structure_finding_append_only() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    RAISE EXCEPTION
        'structure_evidence_finding_is_append_only: a finding cannot be %d. Only an authorized adjudication '
        'settles an earlier finding.', lower(TG_OP)
        USING ERRCODE = '23514',
              HINT = 'Append a finding (or, with authority, an adjudication) instead.';
END;
$$;
CREATE FUNCTION public.tckdb_structure_row_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    RAISE EXCEPTION
        'structure_row_is_immutable: a row of % cannot be changed. A structure determination is identity: '
        'it is shared by every upload that states it, so a change would silently re-describe all of them.',
        TG_TABLE_NAME
        USING ERRCODE = '23514',
              HINT = 'Join or create a different determination instead of editing this one.';
END;
$$;
CREATE FUNCTION public.tckdb_structure_source_delete_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    RAISE EXCEPTION
        'structure_determination_sources_are_pinned_at_creation: a source of determination % cannot be removed',
        OLD.determination_id
        USING ERRCODE = '23514';
END;
$$;
CREATE FUNCTION public.tckdb_structure_source_insert_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
BEGIN
    -- A determination is its pinned calculations: its content digest and its key cover them, so a source added or
    -- removed afterwards would change what it says under an unchanged identity. The write path pins every source in
    -- the transaction that creates the determination and says so here (an archive restore does the same for the
    -- determinations it re-creates, as a comma-separated list); nothing else may add one. This is a tripwire against
    -- accidental or scripted edits, not access control: whoever sets the variable is acting on purpose.
    IF NOT (
        NEW.determination_id::text = ANY (
            string_to_array(coalesce(current_setting('tckdb.structure_determination_writing', true), ''), ',')
        )
    ) THEN
        RAISE EXCEPTION
            'structure_determination_sources_are_pinned_at_creation: determination % takes no further source',
            NEW.determination_id
            USING ERRCODE = '23514',
                  HINT = 'State a different determination key for a different set of calculations.';
    END IF;
    IF NEW.conformer_observation_id IS DISTINCT FROM (
        SELECT d.conformer_observation_id FROM public.structure_determination d WHERE d.id = NEW.determination_id
    ) THEN
        RAISE EXCEPTION
            'structure_determination_source_observation: a source names the observation of its determination'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION public.tckdb_validate_accepted_science_repair() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE
            target_oid oid;
            target_owner oid;
            unknown_columns text[];
        BEGIN
            SELECT relation.oid, relation.relowner
              INTO target_oid, target_owner
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = NEW.target_schema
               AND relation.relname = NEW.target_table
               AND relation.relkind IN ('r', 'p');
            IF target_oid IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair target %I.%I is not a table',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            -- A declaration against a table nothing guards permits nothing,
            -- and would read afterwards as though a control had been stood
            -- down when none was ever in the way.
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_trigger AS guard
                  JOIN pg_proc AS guard_function ON guard_function.oid = guard.tgfoid
                 WHERE guard.tgrelid = target_oid
                   AND NOT guard.tgisinternal
                   AND guard_function.proname IN ('tckdb_guard_accepted_root', 'tckdb_guard_accepted_child', 'tckdb_guard_accepted_via_child', 'tckdb_guard_calculation_geometry')
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair target %I.%I carries no accepted-science guard',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            IF EXISTS (
                SELECT 1
                  FROM unnest(NEW.declared_columns) AS column_name
                 GROUP BY column_name
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'repair declares the same column twice';
            END IF;

            SELECT array_agg(column_name ORDER BY column_name)
              INTO unknown_columns
              FROM unnest(NEW.declared_columns) AS column_name
             WHERE NOT EXISTS (
                SELECT 1
                  FROM pg_attribute AS attribute
                 WHERE attribute.attrelid = target_oid
                   AND attribute.attname = column_name
                   AND attribute.attnum > 0
                   AND NOT attribute.attisdropped
             );
            IF unknown_columns IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '42703',
                    MESSAGE = format(
                        'repair declares column(s) %s that %I.%I does not have',
                        array_to_string(unknown_columns, ', '),
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            -- Every changed row is recorded by primary key. A table without
            -- one cannot be audited, so it cannot be repaired either.
            IF NOT EXISTS (
                SELECT 1 FROM pg_index AS index_
                 WHERE index_.indrelid = target_oid AND index_.indisprimary
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair target %I.%I has no primary key',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            -- ...and for the same reason, the key itself is off limits. The
            -- change record names the row by its primary key, so a repair
            -- that rewrote one could not say which row it changed -- and a
            -- row under a new identity is a different row, which is what
            -- supersession is for.
            SELECT array_agg(attribute.attname ORDER BY attribute.attname)
              INTO unknown_columns
              FROM pg_index AS index_
              JOIN pg_attribute AS attribute
                ON attribute.attrelid = index_.indrelid
               AND attribute.attnum = ANY (index_.indkey)
             WHERE index_.indrelid = target_oid
               AND index_.indisprimary
               AND attribute.attname = ANY (NEW.declared_columns);
            IF unknown_columns IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair declares primary-key column(s) %s of %I.%I',
                        array_to_string(unknown_columns, ', '),
                        NEW.target_schema, NEW.target_table
                    ),
                    HINT = 'A row under a new identity is a different row; record a supersession.';
            END IF;

            -- Exactly the roles that could already have run
            -- ALTER TABLE ... DISABLE TRIGGER. This grants nothing new; it
            -- makes an existing capability recordable.
            IF NOT pg_catalog.pg_has_role(current_user, target_owner, 'USAGE') THEN
                RAISE EXCEPTION USING ERRCODE = '42501',
                    MESSAGE = format(
                        'only an owner of %I.%I may declare a repair to it',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            IF NEW.xact_id <> (pg_current_xact_id())::text::bigint THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'a repair declaration cannot name another transaction';
            END IF;

            IF EXISTS (
                SELECT 1 FROM public.accepted_science_repair
                 WHERE target_schema = NEW.target_schema
                   AND target_table = NEW.target_table
                   AND xact_id = NEW.xact_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'a repair to %I.%I is already declared in this transaction',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;
            RETURN NEW;
        END;
        $$;
CREATE FUNCTION public.tckdb_validate_accepted_science_repair_change() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE
            declaration public.accepted_science_repair%ROWTYPE;
            target_owner oid;
        BEGIN
            SELECT * INTO declaration
              FROM public.accepted_science_repair
             WHERE id = NEW.repair_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503',
                    MESSAGE = 'repair change names no declaration';
            END IF;
            IF declaration.xact_id <> (pg_current_xact_id())::text::bigint THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'a repair change may only be recorded in the transaction that declared it';
            END IF;
            IF NEW.target_schema IS DISTINCT FROM declaration.target_schema
               OR NEW.target_table IS DISTINCT FROM declaration.target_table THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'repair change names a different table than its declaration';
            END IF;
            IF NOT (NEW.changed_columns <@ declaration.declared_columns) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'repair change names columns its declaration does not cover';
            END IF;

            SELECT relation.relowner
              INTO target_owner
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = NEW.target_schema
               AND relation.relname = NEW.target_table;
            IF target_owner IS NULL
               OR NOT pg_catalog.pg_has_role(current_user, target_owner, 'USAGE') THEN
                RAISE EXCEPTION USING ERRCODE = '42501',
                    MESSAGE = format(
                        'only an owner of %I.%I may record a repair change to it',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;
            RETURN NEW;
        END;
        $$;
CREATE FUNCTION public.tckdb_validate_scientific_supersession() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE same_subject boolean := false;
        BEGIN
            IF NOT public.tckdb_is_accepted_science_type(NEW.record_type) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'unsupported scientific supersession type';
            END IF;
            IF NEW.superseded_record_id = NEW.superseding_record_id THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a scientific record cannot supersede itself';
            END IF;

            IF NEW.superseded_record_id < NEW.superseding_record_id THEN
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseded_record_id
                );
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseding_record_id
                );
            ELSE
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseding_record_id
                );
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.superseded_record_id
                );
            END IF;

            PERFORM 1
            FROM public.record_review
            WHERE record_type = NEW.record_type
              AND record_id IN (
                  NEW.superseded_record_id, NEW.superseding_record_id
              )
            ORDER BY record_id
            FOR UPDATE;

            IF NOT EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = NEW.record_type
                  AND record_id = NEW.superseded_record_id
                  AND first_approved_at IS NOT NULL
                  AND status = 'deprecated'
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'superseded record must be deprecated with approval history';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = NEW.record_type
                  AND record_id = NEW.superseding_record_id
                  AND status = 'approved'
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'superseding record must currently be approved';
            END IF;

            CASE NEW.record_type::text
                WHEN 'thermo' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                    INTO same_subject
                    FROM public.thermo o CROSS JOIN public.thermo n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'statmech' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                    INTO same_subject
                    FROM public.statmech o CROSS JOIN public.statmech n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'transport' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                    INTO same_subject
                    FROM public.transport o CROSS JOIN public.transport n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'kinetics' THEN
                    SELECT o.reaction_entry_id IS NOT DISTINCT FROM n.reaction_entry_id
                       AND o.direction IS NOT DISTINCT FROM n.direction
                    INTO same_subject
                    FROM public.kinetics o CROSS JOIN public.kinetics n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'calculation' THEN
                    SELECT o.species_entry_id IS NOT DISTINCT FROM n.species_entry_id
                       AND o.transition_state_entry_id IS NOT DISTINCT FROM
                           n.transition_state_entry_id
                       AND o.type IS NOT DISTINCT FROM n.type
                    INTO same_subject
                    FROM public.calculation o CROSS JOIN public.calculation n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'network' THEN
                    same_subject := true;
                WHEN 'network_solve' THEN
                    SELECT o.network_id IS NOT DISTINCT FROM n.network_id
                    INTO same_subject
                    FROM public.network_solve o CROSS JOIN public.network_solve n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'applied_energy_correction' THEN
                    SELECT o.target_species_entry_id IS NOT DISTINCT FROM
                               n.target_species_entry_id
                       AND o.target_reaction_entry_id IS NOT DISTINCT FROM
                               n.target_reaction_entry_id
                       AND o.target_transition_state_entry_id IS NOT DISTINCT FROM
                               n.target_transition_state_entry_id
                       AND o.application_role IS NOT DISTINCT FROM n.application_role
                    INTO same_subject
                    FROM public.applied_energy_correction o
                    CROSS JOIN public.applied_energy_correction n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'transition_state_entry' THEN
                    SELECT o.transition_state_id IS NOT DISTINCT FROM
                               n.transition_state_id
                    INTO same_subject
                    FROM public.transition_state_entry o
                    CROSS JOIN public.transition_state_entry n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
                WHEN 'conformer_observation' THEN
                    SELECT o.conformer_group_id IS NOT DISTINCT FROM
                               n.conformer_group_id
                    INTO same_subject
                    FROM public.conformer_observation o
                    CROSS JOIN public.conformer_observation n
                    WHERE o.id = NEW.superseded_record_id
                      AND n.id = NEW.superseding_record_id;
            END CASE;
            IF NOT coalesce(same_subject, false) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'supersession records must describe the same subject';
            END IF;

            IF EXISTS (
                WITH RECURSIVE chain(record_id) AS (
                    SELECT NEW.superseding_record_id
                    UNION
                    SELECT edge.superseding_record_id
                    FROM public.scientific_record_supersession edge
                    JOIN chain ON edge.superseded_record_id = chain.record_id
                    WHERE edge.record_type = NEW.record_type
                )
                SELECT 1 FROM chain WHERE record_id = NEW.superseded_record_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'scientific supersession cannot create a cycle';
            END IF;
            RETURN NEW;
        END;
        $$;
CREATE FUNCTION public.tckdb_validate_sva() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            IF (SELECT version FROM public.software_release WHERE id = NEW.software_release_id) IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation must concern a release whose version is NULL';
            END IF;
            RETURN NEW;
        END;
        $$;
CREATE FUNCTION public.tckdb_validate_sva_calculation() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE
            v_attested_release bigint;
            v_attested_version text;
            v_depositor bigint;
            v_before public.software_release%ROWTYPE;
            v_after public.software_release%ROWTYPE;
            v_calc_depositor bigint;
            v_calc_release bigint;
        BEGIN
            SELECT software_release_id, attested_version, covers_depositor
              INTO v_attested_release, v_attested_version, v_depositor
              FROM public.software_version_attestation WHERE id = NEW.attestation_id;
            IF NEW.before_software_release_id IS DISTINCT FROM v_attested_release THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: before must be the attested release';
            END IF;
            SELECT * INTO v_before FROM public.software_release WHERE id = NEW.before_software_release_id;
            SELECT * INTO v_after FROM public.software_release WHERE id = NEW.after_software_release_id;
            IF v_after.software_id IS DISTINCT FROM v_before.software_id
               OR v_after.revision IS DISTINCT FROM v_before.revision
               OR v_after.build IS DISTINCT FROM v_before.build
               OR v_after.version IS DISTINCT FROM v_attested_version THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: after must be before with only the attested version added';
            END IF;
            SELECT created_by, software_release_id INTO v_calc_depositor, v_calc_release
              FROM public.calculation WHERE id = NEW.calculation_id;
            IF v_calc_depositor IS DISTINCT FROM v_depositor THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: the calculation is not the covered depositor''s';
            END IF;
            IF v_calc_release IS NOT DISTINCT FROM NEW.before_software_release_id THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'software_version_attestation_calculation: the calculation was never re-pointed';
            END IF;
            RETURN NEW;
        END;
        $$;
CREATE FUNCTION public.validate_calculation_execution_environment_binding() RETURNS trigger
    LANGUAGE plpgsql
    AS $$ DECLARE manifest_software_release_id bigint;
        manifest_workflow_tool_release_id bigint; BEGIN
          IF NEW.execution_environment_manifest_id IS NULL THEN RETURN NEW; END IF;
          SELECT software_release_id, workflow_tool_release_id
          INTO manifest_software_release_id, manifest_workflow_tool_release_id
          FROM execution_environment_manifest WHERE id = NEW.execution_environment_manifest_id;
          IF NOT FOUND
             OR NEW.software_release_id IS DISTINCT FROM manifest_software_release_id
             OR NEW.workflow_tool_release_id IS DISTINCT FROM manifest_workflow_tool_release_id THEN
            RAISE EXCEPTION 'calculation execution environment release bindings must match';
          END IF;
          RETURN NEW;
        END $$;
CREATE TABLE public.accepted_science_repair (
    id bigint NOT NULL,
    target_schema text DEFAULT 'public'::text NOT NULL,
    target_table text NOT NULL,
    declared_columns text[] NOT NULL,
    alembic_revision text NOT NULL,
    reason text NOT NULL,
    declared_by_role text DEFAULT CURRENT_USER NOT NULL,
    declared_by_login text DEFAULT SESSION_USER NOT NULL,
    xact_id bigint DEFAULT ((pg_current_xact_id())::text)::bigint NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    expires_at timestamp with time zone DEFAULT (clock_timestamp() + '01:00:00'::interval) NOT NULL,
    CONSTRAINT ck_accepted_science_repair_columns_declared CHECK (((cardinality(declared_columns) > 0) AND (array_position(declared_columns, NULL::text) IS NULL))),
    CONSTRAINT ck_accepted_science_repair_expiry_window CHECK (((expires_at > created_at) AND (expires_at <= (created_at + '24:00:00'::interval)))),
    CONSTRAINT ck_accepted_science_repair_reason_nonblank CHECK ((length(btrim(reason)) > 0)),
    CONSTRAINT ck_accepted_science_repair_revision_nonblank CHECK ((length(btrim(alembic_revision)) > 0)),
    CONSTRAINT ck_accepted_science_repair_target_nonblank CHECK (((length(btrim(target_schema)) > 0) AND (length(btrim(target_table)) > 0)))
);
CREATE TABLE public.accepted_science_repair_change (
    id bigint NOT NULL,
    repair_id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    record_id bigint NOT NULL,
    target_schema text NOT NULL,
    target_table text NOT NULL,
    row_identity jsonb NOT NULL,
    changed_columns text[] NOT NULL,
    before_json jsonb NOT NULL,
    after_json jsonb NOT NULL,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT ck_accepted_science_repair_change_columns_changed CHECK ((cardinality(changed_columns) > 0))
);
CREATE SEQUENCE public.accepted_science_repair_change_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.accepted_science_repair_change_id_seq OWNED BY public.accepted_science_repair_change.id;
CREATE SEQUENCE public.accepted_science_repair_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.accepted_science_repair_id_seq OWNED BY public.accepted_science_repair.id;
CREATE TABLE public.api_key (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    key_hash character(64) NOT NULL,
    label text,
    last_used_at timestamp without time zone,
    revoked_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.api_key_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.api_key_id_seq OWNED BY public.api_key.id;
CREATE TABLE public.app_user (
    id bigint NOT NULL,
    username text NOT NULL,
    email text,
    full_name text,
    affiliation text,
    orcid character(19),
    password_hash text,
    is_active boolean DEFAULT true NOT NULL,
    role public.app_user_role DEFAULT 'user'::public.app_user_role NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.app_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.app_user_id_seq OWNED BY public.app_user.id;
CREATE TABLE public.applied_energy_correction (
    id bigint NOT NULL,
    target_species_entry_id bigint,
    target_reaction_entry_id bigint,
    target_transition_state_entry_id bigint,
    source_conformer_observation_id bigint,
    source_calculation_id bigint,
    scheme_id bigint,
    frequency_scale_factor_id bigint,
    application_role public.energy_correction_application_role NOT NULL,
    value double precision NOT NULL,
    value_unit public.energy_unit NOT NULL,
    temperature_k double precision,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    CONSTRAINT ck_applied_energy_correction_exactly_one_provenance_source CHECK ((num_nonnulls(scheme_id, frequency_scale_factor_id) = 1)),
    CONSTRAINT ck_applied_energy_correction_exactly_one_target CHECK ((num_nonnulls(target_species_entry_id, target_reaction_entry_id, target_transition_state_entry_id) = 1)),
    CONSTRAINT ck_applied_energy_correction_temperature_k_gt_0 CHECK (((temperature_k IS NULL) OR (temperature_k > (0)::double precision)))
);
CREATE TABLE public.applied_energy_correction_component (
    id bigint NOT NULL,
    applied_correction_id bigint NOT NULL,
    component_kind public.applied_correction_component_kind NOT NULL,
    key text NOT NULL,
    multiplicity integer DEFAULT 1 NOT NULL,
    parameter_value double precision NOT NULL,
    contribution_value double precision NOT NULL,
    CONSTRAINT ck_applied_energy_correction_component_multiplicity_ge_1 CHECK ((multiplicity >= 1))
);
CREATE SEQUENCE public.applied_energy_correction_component_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.applied_energy_correction_component_id_seq OWNED BY public.applied_energy_correction_component.id;
CREATE SEQUENCE public.applied_energy_correction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.applied_energy_correction_id_seq OWNED BY public.applied_energy_correction.id;
CREATE TABLE public.applied_group_additivity (
    id bigint NOT NULL,
    thermo_id bigint NOT NULL,
    scheme_id bigint NOT NULL,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint
);
CREATE TABLE public.applied_group_additivity_component (
    id bigint NOT NULL,
    applied_group_additivity_id bigint NOT NULL,
    component_kind public.group_additivity_component_kind NOT NULL,
    group_label text NOT NULL,
    count integer DEFAULT 1 NOT NULL,
    h298_contribution_kj_mol double precision,
    s298_contribution_j_mol_k double precision,
    cp298_contribution_j_mol_k double precision,
    CONSTRAINT ck_applied_group_additivity_component_count_ge_1 CHECK ((count >= 1))
);
CREATE SEQUENCE public.applied_group_additivity_component_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.applied_group_additivity_component_id_seq OWNED BY public.applied_group_additivity_component.id;
CREATE SEQUENCE public.applied_group_additivity_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.applied_group_additivity_id_seq OWNED BY public.applied_group_additivity.id;
CREATE TABLE public.artifact_integrity_event (
    id bigint NOT NULL,
    sha256 character(64) NOT NULL,
    artifact_id bigint,
    finding public.artifact_integrity_finding NOT NULL,
    detected_during public.artifact_integrity_detection_context NOT NULL,
    expected_bytes bigint,
    observed_sha256 character(64),
    observed_bytes bigint,
    object_last_modified_at timestamp without time zone,
    object_etag text,
    object_content_length bigint,
    artifact_recorded_at timestamp without time zone,
    detail text,
    created_by bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT public.aie_opaque_public_ref() NOT NULL,
    CONSTRAINT ck_artifact_integrity_event_observed_digest_present_iff_read CHECK ((((finding = 'object_missing'::public.artifact_integrity_finding) AND (observed_sha256 IS NULL)) OR ((finding <> 'object_missing'::public.artifact_integrity_finding) AND (observed_sha256 IS NOT NULL)))),
    CONSTRAINT ck_artifact_integrity_event_observed_sha256_lower_hex CHECK (((observed_sha256 IS NULL) OR (observed_sha256 ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT ck_artifact_integrity_event_sha256_lower_hex CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_artifact_integrity_event_verified_requires_matching_digest CHECK (((finding <> 'verified'::public.artifact_integrity_finding) OR (observed_sha256 = sha256)))
);
CREATE SEQUENCE public.artifact_integrity_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.artifact_integrity_event_id_seq OWNED BY public.artifact_integrity_event.id;
CREATE TABLE public.artifact_storage_capacity_event (
    id bigint NOT NULL,
    observation public.artifact_storage_capacity_observation NOT NULL,
    observed_bytes bigint,
    s3_code text,
    detail text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    CONSTRAINT ck_artifact_storage_capacity_event_bytes_match_observation CHECK ((((observation <> 'operator_clear'::public.artifact_storage_capacity_observation) OR (observed_bytes IS NULL)) AND ((observation <> 'capacity_report'::public.artifact_storage_capacity_observation) OR (observed_bytes IS NOT NULL)) AND ((observation <> 'accepted'::public.artifact_storage_capacity_observation) OR (observed_bytes IS NOT NULL)))),
    CONSTRAINT ck_artifact_storage_capacity_event_s3_code_only_on_refused CHECK (((observation = 'refused'::public.artifact_storage_capacity_observation) OR (s3_code IS NULL)))
);
CREATE SEQUENCE public.artifact_storage_capacity_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.artifact_storage_capacity_event_id_seq OWNED BY public.artifact_storage_capacity_event.id;
CREATE TABLE public.author (
    id bigint NOT NULL,
    given_name text,
    family_name text NOT NULL,
    full_name text NOT NULL,
    orcid character(19),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.author_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.author_id_seq OWNED BY public.author.id;
CREATE TABLE public.calc_composite_input (
    calculation_id bigint NOT NULL,
    term_position smallint NOT NULL,
    slot public.composite_input_slot NOT NULL,
    input_calculation_id bigint NOT NULL,
    cardinal_number integer,
    CONSTRAINT ck_calc_composite_input_cardinal_number_positive CHECK (((cardinal_number IS NULL) OR (cardinal_number >= 1))),
    CONSTRAINT ck_calc_composite_input_cardinal_only_on_cardinal_slot CHECK (((slot = 'cardinal'::public.composite_input_slot) OR (cardinal_number IS NULL))),
    CONSTRAINT ck_calc_composite_input_cardinal_slot_needs_number CHECK (((slot <> 'cardinal'::public.composite_input_slot) OR (cardinal_number IS NOT NULL))),
    CONSTRAINT ck_calc_composite_input_not_its_own_input CHECK ((input_calculation_id <> calculation_id)),
    CONSTRAINT ck_calc_composite_input_term_position_non_negative CHECK ((term_position >= 0))
);
CREATE TABLE public.calc_composite_log_check (
    calculation_id bigint NOT NULL,
    artifact_sha256 character(64) NOT NULL,
    parser_version smallint NOT NULL,
    outcome public.composite_log_outcome NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_calc_composite_log_check_artifact_sha256_hex CHECK ((artifact_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_calc_composite_log_check_parser_version_positive CHECK ((parser_version >= 1))
);
CREATE TABLE public.calc_composite_result (
    calculation_id bigint NOT NULL,
    assembly public.composite_assembly NOT NULL,
    electronic_energy_hartree double precision,
    e0_hartree double precision,
    recipe_zpe_hartree double precision,
    CONSTRAINT ck_calc_composite_result_e0_finite CHECK (((e0_hartree IS NULL) OR ((e0_hartree > '-Infinity'::double precision) AND (e0_hartree < 'Infinity'::double precision)))),
    CONSTRAINT ck_calc_composite_result_electronic_energy_finite CHECK (((electronic_energy_hartree IS NULL) OR ((electronic_energy_hartree > '-Infinity'::double precision) AND (electronic_energy_hartree < 'Infinity'::double precision)))),
    CONSTRAINT ck_calc_composite_result_recipe_zpe_non_negative_finite CHECK (((recipe_zpe_hartree IS NULL) OR ((recipe_zpe_hartree >= (0)::double precision) AND (recipe_zpe_hartree < 'Infinity'::double precision))))
);
CREATE TABLE public.calc_composite_term (
    calculation_id bigint NOT NULL,
    term_position smallint NOT NULL,
    value_hartree double precision NOT NULL,
    CONSTRAINT ck_calc_composite_term_term_position_non_negative CHECK ((term_position >= 0)),
    CONSTRAINT ck_calc_composite_term_value_finite CHECK (((value_hartree > '-Infinity'::double precision) AND (value_hartree < 'Infinity'::double precision)))
);
CREATE TABLE public.calc_freq_mode (
    calculation_id bigint NOT NULL,
    mode_index integer NOT NULL,
    frequency_cm1 double precision NOT NULL,
    is_imaginary boolean NOT NULL,
    reduced_mass_amu double precision,
    force_constant_mdyne_angstrom double precision,
    ir_intensity_km_mol double precision,
    raman_activity double precision,
    symmetry_label text,
    note text,
    imaginary_disposition public.imaginary_mode_disposition,
    CONSTRAINT ck_calc_freq_mode_frequency_sign_matches_is_imaginary CHECK (((is_imaginary AND (frequency_cm1 < (0)::double precision)) OR ((NOT is_imaginary) AND (frequency_cm1 >= (0)::double precision)))),
    CONSTRAINT ck_calc_freq_mode_imaginary_disposition_requires_imaginary_mode CHECK (((imaginary_disposition IS NULL) OR is_imaginary)),
    CONSTRAINT ck_calc_freq_mode_ir_intensity_km_mol_ge_0 CHECK (((ir_intensity_km_mol IS NULL) OR (ir_intensity_km_mol >= (0)::double precision))),
    CONSTRAINT ck_calc_freq_mode_mode_index_ge_1 CHECK ((mode_index >= 1)),
    CONSTRAINT ck_calc_freq_mode_reduced_mass_amu_gt_0 CHECK (((reduced_mass_amu IS NULL) OR (reduced_mass_amu > (0)::double precision)))
);
CREATE TABLE public.calc_freq_result (
    calculation_id bigint NOT NULL,
    n_imag integer,
    imag_freq_cm1 double precision,
    zpe_hartree double precision,
    zpe_uncertainty_hartree double precision,
    reaction_coordinate_mode_index integer,
    imaginary_mode_tau_cm1 double precision,
    imaginary_mode_tau_basis text,
    imaginary_mode_structural_flag boolean,
    CONSTRAINT ck_calc_freq_result_imaginary_mode_tau_basis_known CHECK (((imaginary_mode_tau_basis IS NULL) OR (imaginary_mode_tau_basis = ANY (ARRAY['analytic_tight'::text, 'analytic_default'::text, 'finite_difference_gradient'::text, 'finite_difference_energy'::text, 'protocol_not_recorded'::text, 'assumed_analytic_default'::text, 'assumed_finite_difference_gradient'::text, 'assumed_finite_difference_energy'::text]))))
);
CREATE TABLE public.calc_geometry_validation (
    calculation_id bigint NOT NULL,
    input_geometry_id bigint,
    output_geometry_id bigint,
    species_smiles text NOT NULL,
    is_isomorphic boolean NOT NULL,
    rmsd double precision,
    atom_mapping jsonb,
    n_mappings integer,
    validation_status public.validation_status NOT NULL,
    validation_reason text,
    rmsd_warning_threshold double precision,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.calc_hessian (
    calculation_id bigint NOT NULL,
    geometry_id bigint NOT NULL,
    natoms integer NOT NULL,
    lower_triangle_hartree_bohr2 double precision[] NOT NULL,
    source public.hessian_source NOT NULL,
    parser_version text,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    parser_name text,
    CONSTRAINT ck_calc_hessian_hessian_lower_triangle_cardinality CHECK ((cardinality(lower_triangle_hartree_bohr2) = (((3 * natoms) * ((3 * natoms) + 1)) / 2))),
    CONSTRAINT ck_calc_hessian_hessian_natoms_ge_1 CHECK ((natoms >= 1))
);
CREATE TABLE public.calc_irc_point (
    calculation_id bigint NOT NULL,
    point_index integer NOT NULL,
    direction public.irc_direction,
    is_ts boolean DEFAULT false NOT NULL,
    reaction_coordinate double precision,
    electronic_energy_hartree double precision,
    relative_energy_kj_mol double precision,
    max_gradient double precision,
    rms_gradient double precision,
    geometry_id bigint,
    note text,
    CONSTRAINT ck_calc_irc_point_point_index_ge_0 CHECK ((point_index >= 0))
);
CREATE TABLE public.calc_irc_result (
    calculation_id bigint NOT NULL,
    direction public.irc_direction,
    has_forward boolean,
    has_reverse boolean,
    ts_point_index integer,
    point_count integer,
    zero_energy_reference_hartree double precision,
    note text,
    CONSTRAINT ck_calc_irc_result_point_count_ge_0 CHECK (((point_count IS NULL) OR (point_count >= 0)))
);
CREATE TABLE public.calc_opt_result (
    calculation_id bigint NOT NULL,
    converged boolean,
    n_steps integer,
    final_energy_hartree double precision,
    CONSTRAINT ck_calc_opt_result_n_steps_ge_0 CHECK (((n_steps IS NULL) OR (n_steps >= 0)))
);
CREATE TABLE public.calc_path_search_point (
    calculation_id bigint NOT NULL,
    point_index integer NOT NULL,
    electronic_energy_hartree double precision,
    relative_energy_kj_mol double precision,
    path_coordinate double precision,
    max_force double precision,
    rms_force double precision,
    max_gradient double precision,
    rms_gradient double precision,
    is_ts_guess boolean DEFAULT false NOT NULL,
    is_climbing_image boolean DEFAULT false NOT NULL,
    geometry_id bigint,
    note text,
    CONSTRAINT ck_calc_path_search_point_point_index_ge_0 CHECK ((point_index >= 0))
);
CREATE TABLE public.calc_path_search_result (
    calculation_id bigint NOT NULL,
    method public.path_search_method NOT NULL,
    is_double_ended boolean,
    converged boolean,
    n_points integer,
    selected_ts_point_index integer,
    climbing_image_index integer,
    source_endpoint_count integer,
    zero_energy_reference_hartree double precision,
    note text,
    CONSTRAINT ck_calc_path_search_result_climbing_image_index_ge_0 CHECK (((climbing_image_index IS NULL) OR (climbing_image_index >= 0))),
    CONSTRAINT ck_calc_path_search_result_n_points_ge_1 CHECK (((n_points IS NULL) OR (n_points >= 1))),
    CONSTRAINT ck_calc_path_search_result_selected_ts_point_index_ge_0 CHECK (((selected_ts_point_index IS NULL) OR (selected_ts_point_index >= 0))),
    CONSTRAINT ck_calc_path_search_result_source_endpoint_count_ge_1 CHECK (((source_endpoint_count IS NULL) OR (source_endpoint_count >= 1)))
);
CREATE TABLE public.calc_scan_coordinate (
    calculation_id bigint NOT NULL,
    coordinate_index integer NOT NULL,
    coordinate_kind public.scan_coordinate_kind NOT NULL,
    atom1_index integer NOT NULL,
    atom2_index integer NOT NULL,
    atom3_index integer,
    atom4_index integer,
    step_count integer,
    step_size double precision,
    start_value double precision,
    end_value double precision,
    value_unit public.coordinate_unit,
    resolution_degrees integer,
    symmetry_number smallint,
    CONSTRAINT ck_calc_scan_coordinate_atom1_index_ge_1 CHECK ((atom1_index >= 1)),
    CONSTRAINT ck_calc_scan_coordinate_atom2_index_ge_1 CHECK ((atom2_index >= 1)),
    CONSTRAINT ck_calc_scan_coordinate_atom3_index_ge_1 CHECK (((atom3_index IS NULL) OR (atom3_index >= 1))),
    CONSTRAINT ck_calc_scan_coordinate_atom4_index_ge_1 CHECK (((atom4_index IS NULL) OR (atom4_index >= 1))),
    CONSTRAINT ck_calc_scan_coordinate_coordinate_arity_matches_kind CHECK (
CASE coordinate_kind
    WHEN 'bond'::public.scan_coordinate_kind THEN ((atom3_index IS NULL) AND (atom4_index IS NULL))
    WHEN 'angle'::public.scan_coordinate_kind THEN ((atom3_index IS NOT NULL) AND (atom4_index IS NULL))
    ELSE ((atom3_index IS NOT NULL) AND (atom4_index IS NOT NULL))
END),
    CONSTRAINT ck_calc_scan_coordinate_coordinate_index_ge_1 CHECK ((coordinate_index >= 1)),
    CONSTRAINT ck_calc_scan_coordinate_resolution_degrees_ge_1 CHECK (((resolution_degrees IS NULL) OR (resolution_degrees >= 1))),
    CONSTRAINT ck_calc_scan_coordinate_step_count_ge_1 CHECK (((step_count IS NULL) OR (step_count >= 1))),
    CONSTRAINT ck_calc_scan_coordinate_symmetry_number_ge_1 CHECK (((symmetry_number IS NULL) OR (symmetry_number >= 1)))
);
CREATE TABLE public.calc_scan_point (
    calculation_id bigint NOT NULL,
    point_index integer NOT NULL,
    electronic_energy_hartree double precision,
    relative_energy_kj_mol double precision,
    geometry_id bigint,
    note text,
    CONSTRAINT ck_calc_scan_point_point_index_ge_1 CHECK ((point_index >= 1))
);
CREATE TABLE public.calc_scan_point_coordinate_value (
    calculation_id bigint NOT NULL,
    point_index integer NOT NULL,
    coordinate_index integer NOT NULL,
    coordinate_value double precision NOT NULL,
    value_unit public.coordinate_unit,
    CONSTRAINT ck_calc_scan_point_coordinate_value_coordinate_index_ge_1 CHECK ((coordinate_index >= 1)),
    CONSTRAINT ck_calc_scan_point_coordinate_value_point_index_ge_1 CHECK ((point_index >= 1))
);
CREATE TABLE public.calc_scan_result (
    calculation_id bigint NOT NULL,
    dimension integer NOT NULL,
    is_relaxed boolean,
    zero_energy_reference_hartree double precision,
    note text,
    CONSTRAINT ck_calc_scan_result_dimension_ge_1 CHECK ((dimension >= 1))
);
CREATE TABLE public.calc_scf_stability (
    calculation_id bigint NOT NULL,
    status public.scf_stability_status NOT NULL,
    lowest_eigenvalue double precision,
    instability_count integer,
    instability_type text,
    reoptimized_wavefunction boolean,
    source_calculation_id bigint,
    source_artifact_id bigint,
    note text,
    created_by bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_calc_scf_stability_instability_count_ge_0 CHECK (((instability_count IS NULL) OR (instability_count >= 0))),
    CONSTRAINT ck_calc_scf_stability_stabilized_has_instability CHECK ((NOT ((status = 'stabilized'::public.scf_stability_status) AND (instability_count = 0)))),
    CONSTRAINT ck_calc_scf_stability_stable_no_reopt CHECK ((NOT ((status = 'stable'::public.scf_stability_status) AND (reoptimized_wavefunction IS TRUE))))
);
CREATE TABLE public.calc_sp_energy_component (
    calculation_id bigint NOT NULL,
    component public.energy_component_kind NOT NULL,
    value_hartree double precision NOT NULL,
    CONSTRAINT ck_calc_sp_energy_component_component_not_derived CHECK (((component)::text <> 'correlation_excluding_triples'::text)),
    CONSTRAINT ck_calc_sp_energy_component_value_hartree_finite CHECK (((value_hartree > '-Infinity'::double precision) AND (value_hartree < 'Infinity'::double precision)))
);
CREATE TABLE public.calc_sp_result (
    calculation_id bigint NOT NULL,
    electronic_energy_hartree double precision,
    electronic_energy_uncertainty_hartree double precision
);
CREATE TABLE public.calc_spin_diagnostic (
    calculation_id bigint NOT NULL,
    s_squared double precision NOT NULL,
    s_squared_expected double precision,
    s_squared_annihilated double precision,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    CONSTRAINT ck_calc_spin_diagnostic_s_squared_annihilated_ge_0 CHECK (((s_squared_annihilated IS NULL) OR (s_squared_annihilated >= (0)::double precision))),
    CONSTRAINT ck_calc_spin_diagnostic_s_squared_expected_ge_0 CHECK (((s_squared_expected IS NULL) OR (s_squared_expected >= (0)::double precision))),
    CONSTRAINT ck_calc_spin_diagnostic_s_squared_ge_0 CHECK ((s_squared >= (0)::double precision))
);
CREATE TABLE public.calc_wavefunction_diagnostic (
    calculation_id bigint NOT NULL,
    t1_diagnostic double precision,
    d1_diagnostic double precision,
    t1_norm double precision,
    largest_t2_amplitude double precision,
    note text,
    created_by bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_calc_wavefunction_diagnostic_d1_diagnostic_ge_0 CHECK (((d1_diagnostic IS NULL) OR (d1_diagnostic >= (0)::double precision))),
    CONSTRAINT ck_calc_wavefunction_diagnostic_largest_t2_amplitude_ge_0 CHECK (((largest_t2_amplitude IS NULL) OR (largest_t2_amplitude >= (0)::double precision))),
    CONSTRAINT ck_calc_wavefunction_diagnostic_t1_diagnostic_ge_0 CHECK (((t1_diagnostic IS NULL) OR (t1_diagnostic >= (0)::double precision))),
    CONSTRAINT ck_calc_wavefunction_diagnostic_t1_norm_ge_0 CHECK (((t1_norm IS NULL) OR (t1_norm >= (0)::double precision)))
);
CREATE TABLE public.calculation (
    id bigint NOT NULL,
    type public.calc_type NOT NULL,
    quality public.calc_quality DEFAULT 'raw'::public.calc_quality NOT NULL,
    species_entry_id bigint,
    transition_state_entry_id bigint,
    software_release_id bigint,
    workflow_tool_release_id bigint,
    lot_id bigint,
    literature_id bigint,
    conformer_observation_id bigint,
    parameters_json jsonb,
    parameters_parser_version text,
    parameters_extracted_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('calc_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    software_reconciliation_status public.software_reconciliation_status,
    observed_software_banner text,
    execution_environment_manifest_id bigint,
    declared_software_banner text,
    actual_protocol_declaration jsonb,
    parameters_parser_name text,
    CONSTRAINT ck_calculation_actual_protocol_declaration_versioned_object CHECK (((actual_protocol_declaration IS NULL) OR ((jsonb_typeof(actual_protocol_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((actual_protocol_declaration -> 'version'::text)), ''::text) = 'number'::text)))),
    CONSTRAINT ck_calculation_one_owner CHECK ((((transition_state_entry_id IS NOT NULL) AND (species_entry_id IS NULL)) OR ((transition_state_entry_id IS NULL) AND (species_entry_id IS NOT NULL))))
);
CREATE TABLE public.calculation_artifact (
    id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    kind public.artifact_kind NOT NULL,
    uri text NOT NULL,
    sha256 character(64) NOT NULL,
    bytes bigint NOT NULL,
    filename text NOT NULL,
    note text,
    created_by bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT public.art_opaque_public_ref() NOT NULL,
    CONSTRAINT ck_calculation_artifact_bytes_gt_0 CHECK ((bytes > 0)),
    CONSTRAINT ck_calculation_artifact_sha256_lower_hex CHECK ((sha256 ~ '^[0-9a-f]{64}$'::text))
);
CREATE SEQUENCE public.calculation_artifact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.calculation_artifact_id_seq OWNED BY public.calculation_artifact.id;
CREATE TABLE public.calculation_constraint (
    calculation_id bigint NOT NULL,
    constraint_index integer NOT NULL,
    constraint_kind public.constraint_kind NOT NULL,
    atom1_index integer NOT NULL,
    atom2_index integer,
    atom3_index integer,
    atom4_index integer,
    target_value double precision,
    CONSTRAINT ck_calculation_constraint_atom1_index_ge_1 CHECK ((atom1_index >= 1)),
    CONSTRAINT ck_calculation_constraint_atom2_index_ge_1 CHECK (((atom2_index IS NULL) OR (atom2_index >= 1))),
    CONSTRAINT ck_calculation_constraint_atom3_index_ge_1 CHECK (((atom3_index IS NULL) OR (atom3_index >= 1))),
    CONSTRAINT ck_calculation_constraint_atom4_index_ge_1 CHECK (((atom4_index IS NULL) OR (atom4_index >= 1))),
    CONSTRAINT ck_calculation_constraint_constraint_arity_matches_kind CHECK (
CASE constraint_kind
    WHEN 'cartesian_atom'::public.constraint_kind THEN ((atom2_index IS NULL) AND (atom3_index IS NULL) AND (atom4_index IS NULL))
    WHEN 'bond'::public.constraint_kind THEN ((atom2_index IS NOT NULL) AND (atom3_index IS NULL) AND (atom4_index IS NULL))
    WHEN 'angle'::public.constraint_kind THEN ((atom2_index IS NOT NULL) AND (atom3_index IS NOT NULL) AND (atom4_index IS NULL))
    ELSE ((atom2_index IS NOT NULL) AND (atom3_index IS NOT NULL) AND (atom4_index IS NOT NULL))
END),
    CONSTRAINT ck_calculation_constraint_constraint_index_ge_1 CHECK ((constraint_index >= 1))
);
CREATE TABLE public.calculation_dependency (
    parent_calculation_id bigint NOT NULL,
    child_calculation_id bigint NOT NULL,
    dependency_role public.calculation_dependency_role NOT NULL,
    CONSTRAINT ck_calculation_dependency_not_self CHECK ((parent_calculation_id <> child_calculation_id))
);
CREATE SEQUENCE public.calculation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.calculation_id_seq OWNED BY public.calculation.id;
CREATE TABLE public.calculation_input_geometry (
    calculation_id bigint NOT NULL,
    geometry_id bigint NOT NULL,
    input_order integer DEFAULT 1 NOT NULL,
    source public.calculation_input_geometry_source DEFAULT 'deposited'::public.calculation_input_geometry_source NOT NULL,
    CONSTRAINT ck_calculation_input_geometry_input_order_ge_1 CHECK ((input_order >= 1))
);
CREATE TABLE public.calculation_output_geometry (
    calculation_id bigint NOT NULL,
    geometry_id bigint NOT NULL,
    output_order integer NOT NULL,
    role public.calculation_geometry_role,
    CONSTRAINT ck_calculation_output_geometry_output_order_ge_1 CHECK ((output_order >= 1))
);
CREATE TABLE public.calculation_parameter (
    id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    raw_key text NOT NULL,
    canonical_key text,
    raw_value text NOT NULL,
    canonical_value text,
    section text,
    value_type text,
    unit text,
    parameter_index integer,
    source public.calculation_parameter_source DEFAULT 'upload'::public.calculation_parameter_source NOT NULL,
    parser_version text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    parser_name text,
    artifact_kind public.artifact_kind,
    statement_kind public.parameter_statement_kind,
    CONSTRAINT ck_calculation_parameter_artifact_kind_parser_input_or_log CHECK (((artifact_kind IS NULL) OR ((source = 'parser'::public.calculation_parameter_source) AND (artifact_kind = ANY (ARRAY['input'::public.artifact_kind, 'output_log'::public.artifact_kind]))))),
    CONSTRAINT ck_calculation_parameter_parameter_index_ge_0 CHECK (((parameter_index IS NULL) OR (parameter_index >= 0))),
    CONSTRAINT ck_calculation_parameter_statement_kind_reported_from_log CHECK (((statement_kind IS NULL) OR ((source = 'parser'::public.calculation_parameter_source) AND ((statement_kind = 'requested'::public.parameter_statement_kind) OR (artifact_kind = 'output_log'::public.artifact_kind)))))
);
CREATE SEQUENCE public.calculation_parameter_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.calculation_parameter_id_seq OWNED BY public.calculation_parameter.id;
CREATE TABLE public.calculation_parameter_vocab (
    canonical_key text NOT NULL,
    description text,
    expected_value_type text,
    affects_scientific_result boolean,
    affects_numerics boolean,
    affects_resources boolean,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.chem_reaction (
    id bigint NOT NULL,
    stoichiometry_hash character(64),
    reversible boolean NOT NULL,
    reaction_family_id bigint,
    reaction_family_raw text,
    reaction_family_source_note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('rxn_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    merged_into_chem_reaction_id bigint,
    CONSTRAINT ck_chem_reaction_not_merged_into_itself CHECK (((merged_into_chem_reaction_id IS NULL) OR (merged_into_chem_reaction_id <> id))),
    CONSTRAINT ck_chem_reaction_reaction_family_raw_requires_source_note CHECK (((reaction_family_raw IS NULL) OR (reaction_family_source_note IS NOT NULL)))
);
CREATE SEQUENCE public.chem_reaction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.chem_reaction_id_seq OWNED BY public.chem_reaction.id;
CREATE TABLE public.composite_scheme (
    id bigint NOT NULL,
    kind public.composite_scheme_kind NOT NULL,
    name text NOT NULL,
    definition_hash character(64) NOT NULL,
    geometry_level_of_theory_id bigint,
    frequency_level_of_theory_id bigint,
    recipe_zpe_scale_factor double precision,
    source_literature_id bigint,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('csch_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    CONSTRAINT ck_composite_scheme_recipe_zpe_scale_factor_positive CHECK (((recipe_zpe_scale_factor IS NULL) OR (recipe_zpe_scale_factor > (0)::double precision)))
);
CREATE SEQUENCE public.composite_scheme_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.composite_scheme_id_seq OWNED BY public.composite_scheme.id;
CREATE TABLE public.composite_scheme_term (
    id bigint NOT NULL,
    scheme_id bigint NOT NULL,
    "position" smallint NOT NULL,
    operation public.composite_term_operation NOT NULL,
    energy_component public.energy_component_kind NOT NULL,
    formula public.composite_extrapolation_formula,
    exponent double precision,
    CONSTRAINT ck_composite_scheme_term_exponent_needs_formula CHECK (((exponent IS NULL) OR (formula IS NOT NULL))),
    CONSTRAINT ck_composite_scheme_term_formula_only_on_extrapolation CHECK (((formula IS NULL) OR (operation = 'extrapolation'::public.composite_term_operation))),
    CONSTRAINT ck_composite_scheme_term_position_non_negative CHECK (("position" >= 0))
);
CREATE SEQUENCE public.composite_scheme_term_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.composite_scheme_term_id_seq OWNED BY public.composite_scheme_term.id;
CREATE TABLE public.composite_scheme_term_input (
    id bigint NOT NULL,
    term_id bigint NOT NULL,
    slot public.composite_input_slot NOT NULL,
    level_of_theory_id bigint NOT NULL,
    cardinal_number integer,
    CONSTRAINT ck_composite_scheme_term_input_cardinal_number_positive CHECK (((cardinal_number IS NULL) OR (cardinal_number >= 1))),
    CONSTRAINT ck_composite_scheme_term_input_cardinal_slot_needs_number CHECK (((slot <> 'cardinal'::public.composite_input_slot) OR (cardinal_number IS NOT NULL)))
);
CREATE SEQUENCE public.composite_scheme_term_input_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.composite_scheme_term_input_id_seq OWNED BY public.composite_scheme_term_input.id;
CREATE TABLE public.conformer_assignment_scheme (
    id bigint NOT NULL,
    name character varying(128) NOT NULL,
    version integer NOT NULL,
    scope public.conformer_assignment_scope_kind DEFAULT 'canonical'::public.conformer_assignment_scope_kind NOT NULL,
    description text,
    parameters_json jsonb,
    code_commit character varying(64),
    is_default boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('cas_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    CONSTRAINT ck_conformer_assignment_scheme_version_positive CHECK ((version >= 1))
);
CREATE SEQUENCE public.conformer_assignment_scheme_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.conformer_assignment_scheme_id_seq OWNED BY public.conformer_assignment_scheme.id;
CREATE TABLE public.conformer_group (
    id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    label character varying(64),
    note text,
    representative_fingerprint_json jsonb,
    representative_coords_json jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('cg_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    representative_observation_id bigint,
    merged_into_conformer_group_id bigint,
    basin_is_clique boolean,
    basin_span_deg double precision,
    basin_distance_sums_json jsonb
);
CREATE SEQUENCE public.conformer_group_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.conformer_group_id_seq OWNED BY public.conformer_group.id;
CREATE TABLE public.conformer_group_merge_pending (
    id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    kept_conformer_group_id bigint,
    absorbed_conformer_group_id bigint NOT NULL,
    conformer_observation_id bigint NOT NULL,
    reason text NOT NULL,
    completed_at timestamp without time zone,
    completed_by_revision character varying(64),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_conformer_group_merge_pending_kept_differs_from_absorbed CHECK (((kept_conformer_group_id IS NULL) OR (kept_conformer_group_id <> absorbed_conformer_group_id)))
);
CREATE SEQUENCE public.conformer_group_merge_pending_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.conformer_group_merge_pending_id_seq OWNED BY public.conformer_group_merge_pending.id;
CREATE TABLE public.conformer_observation (
    id bigint NOT NULL,
    conformer_group_id bigint NOT NULL,
    assignment_scheme_id bigint,
    scientific_origin public.scientific_origin_kind DEFAULT 'computed'::public.scientific_origin_kind NOT NULL,
    note text,
    torsion_fingerprint_json jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('co_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    mapped_coords_json jsonb
);
CREATE SEQUENCE public.conformer_observation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.conformer_observation_id_seq OWNED BY public.conformer_observation.id;
CREATE TABLE public.conformer_selection (
    id bigint NOT NULL,
    conformer_group_id bigint NOT NULL,
    assignment_scheme_id bigint,
    selection_kind public.conformer_selection_kind NOT NULL,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint
);
CREATE SEQUENCE public.conformer_selection_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.conformer_selection_id_seq OWNED BY public.conformer_selection.id;
CREATE TABLE public.curation_policy (
    id bigint NOT NULL,
    name character varying(128) NOT NULL,
    version character varying(64) NOT NULL,
    description text NOT NULL,
    criteria_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_curation_policy_description_nonblank CHECK ((length(btrim(description)) > 0)),
    CONSTRAINT ck_curation_policy_name_nonblank CHECK ((length(btrim((name)::text)) > 0)),
    CONSTRAINT ck_curation_policy_version_nonblank CHECK ((length(btrim((version)::text)) > 0))
);
CREATE SEQUENCE public.curation_policy_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.curation_policy_id_seq OWNED BY public.curation_policy.id;
CREATE TABLE public.dataset_release (
    id bigint NOT NULL,
    tag character varying(64) NOT NULL,
    title text NOT NULL,
    description text,
    status public.dataset_release_status DEFAULT 'draft'::public.dataset_release_status NOT NULL,
    curation_policy_id bigint NOT NULL,
    data_license character varying(64) NOT NULL,
    code_license character varying(64) NOT NULL,
    citation_text text NOT NULL,
    doi character varying(128),
    contact text NOT NULL,
    changelog_entry text,
    published_at timestamp without time zone,
    withdrawn_at timestamp without time zone,
    withdrawn_reason text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_dataset_release_citation_nonblank CHECK ((length(btrim(citation_text)) > 0)),
    CONSTRAINT ck_dataset_release_code_license_nonblank CHECK ((length(btrim((code_license)::text)) > 0)),
    CONSTRAINT ck_dataset_release_contact_nonblank CHECK ((length(btrim(contact)) > 0)),
    CONSTRAINT ck_dataset_release_data_license_nonblank CHECK ((length(btrim((data_license)::text)) > 0)),
    CONSTRAINT ck_dataset_release_published_at_matches_status CHECK ((((status = 'draft'::public.dataset_release_status) AND (published_at IS NULL)) OR ((status = ANY (ARRAY['published'::public.dataset_release_status, 'withdrawn'::public.dataset_release_status])) AND (published_at IS NOT NULL)))),
    CONSTRAINT ck_dataset_release_tag_nonblank CHECK ((length(btrim((tag)::text)) > 0)),
    CONSTRAINT ck_dataset_release_title_nonblank CHECK ((length(btrim(title)) > 0)),
    CONSTRAINT ck_dataset_release_withdrawn_has_reason CHECK (((status <> 'withdrawn'::public.dataset_release_status) OR ((withdrawn_at IS NOT NULL) AND (length(btrim(COALESCE(withdrawn_reason, ''::text))) > 0))))
);
CREATE SEQUENCE public.dataset_release_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.dataset_release_id_seq OWNED BY public.dataset_release.id;
CREATE TABLE public.deposit_warning (
    id bigint NOT NULL,
    submission_id bigint,
    record_type public.submission_record_type,
    record_id bigint,
    code text,
    register_entry smallint,
    field text,
    message text,
    context jsonb,
    content_hash text,
    written_by_user_id bigint,
    check_version smallint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_deposit_warning_context_is_object CHECK (((context IS NULL) OR (jsonb_typeof(context) = 'object'::text))),
    CONSTRAINT ck_deposit_warning_marker_or_complete CHECK ((((code IS NULL) AND (register_entry IS NULL) AND (field IS NULL) AND (message IS NULL) AND (context IS NULL) AND (content_hash IS NULL)) OR ((code IS NOT NULL) AND (field IS NOT NULL) AND (message IS NOT NULL) AND (content_hash IS NOT NULL) AND (content_hash ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT ck_deposit_warning_record_both_or_neither CHECK (((record_type IS NULL) = (record_id IS NULL))),
    CONSTRAINT ck_deposit_warning_submission_or_record_warning CHECK (((submission_id IS NOT NULL) OR ((record_type IS NOT NULL) AND (code IS NOT NULL))))
);
CREATE SEQUENCE public.deposit_warning_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.deposit_warning_id_seq OWNED BY public.deposit_warning.id;
CREATE TABLE public.deposit_warning_retirement (
    id bigint NOT NULL,
    deposit_warning_id bigint NOT NULL,
    retired_reason_code text NOT NULL,
    retired_by_user_id bigint,
    retired_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_deposit_warning_retirement_reason_known CHECK ((retired_reason_code = ANY (ARRAY['artifact_attached'::text, 'single_point_deposited'::text, 'hessian_stored'::text, 'superseded_by_recheck'::text])))
);
CREATE SEQUENCE public.deposit_warning_retirement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.deposit_warning_retirement_id_seq OWNED BY public.deposit_warning_retirement.id;
CREATE TABLE public.energy_correction_scheme (
    id bigint NOT NULL,
    kind public.energy_correction_scheme_kind NOT NULL,
    name text NOT NULL,
    level_of_theory_id bigint,
    source_literature_id bigint,
    units public.energy_unit,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('ecs_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    workflow_tool_release_id bigint,
    software_release_id bigint,
    data_revision text,
    atom_params_applied_as public.atom_param_application,
    frequency_level_of_theory_id bigint
);
CREATE TABLE public.energy_correction_scheme_atom_param (
    scheme_id bigint NOT NULL,
    element text NOT NULL,
    value double precision NOT NULL
);
CREATE TABLE public.energy_correction_scheme_bond_param (
    scheme_id bigint NOT NULL,
    bond_key text NOT NULL,
    value double precision NOT NULL
);
CREATE TABLE public.energy_correction_scheme_component_param (
    scheme_id bigint NOT NULL,
    component_kind public.melius_bac_component_kind NOT NULL,
    key text NOT NULL,
    value double precision NOT NULL
);
CREATE SEQUENCE public.energy_correction_scheme_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.energy_correction_scheme_id_seq OWNED BY public.energy_correction_scheme.id;
CREATE TABLE public.execution_environment_manifest (
    id bigint NOT NULL,
    content_digest character varying(71) NOT NULL,
    runtime_kind character varying(32) NOT NULL,
    runtime_locator text NOT NULL,
    executable_locator text NOT NULL,
    software_release_id bigint NOT NULL,
    workflow_tool_release_id bigint,
    closure_json jsonb NOT NULL,
    canonical_json jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    schema_name character varying(64) NOT NULL,
    schema_version integer NOT NULL,
    CONSTRAINT ck_execution_environment_manifest_canonical_json_object CHECK ((jsonb_typeof(canonical_json) = 'object'::text)),
    CONSTRAINT ck_execution_environment_manifest_closure_json_array CHECK ((jsonb_typeof(closure_json) = 'array'::text)),
    CONSTRAINT ck_execution_environment_manifest_content_digest_sha256 CHECK (((content_digest)::text ~ '^sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_execution_environment_manifest_schema_version_positive CHECK ((schema_version >= 1))
);
CREATE SEQUENCE public.execution_environment_manifest_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.execution_environment_manifest_id_seq OWNED BY public.execution_environment_manifest.id;
CREATE TABLE public.external_source (
    id bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    source_name text NOT NULL,
    source_release text NOT NULL,
    source_database_doi text,
    citation_text text,
    terms_url text,
    terms_text text
);
CREATE SEQUENCE public.external_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.external_source_id_seq OWNED BY public.external_source.id;
CREATE TABLE public.external_source_record (
    id bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    external_source_id bigint NOT NULL,
    record_kind public.external_source_record_kind NOT NULL,
    source_uri text NOT NULL,
    source_record_key text NOT NULL,
    retrieved_at timestamp without time zone NOT NULL,
    http_status integer,
    content_sha256 text NOT NULL,
    content_length bigint NOT NULL,
    raw_uri text NOT NULL,
    container_digest text,
    schema_id text,
    schema_valid boolean,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    mapping_version text NOT NULL,
    mapping_report_json jsonb,
    mapping_name text NOT NULL,
    CONSTRAINT ck_external_source_record_content_length_ge_0 CHECK ((content_length >= 0)),
    CONSTRAINT ck_external_source_record_content_sha256_hex CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_external_source_record_http_status_range CHECK (((http_status IS NULL) OR ((http_status >= 100) AND (http_status <= 599))))
);
CREATE SEQUENCE public.external_source_record_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.external_source_record_id_seq OWNED BY public.external_source_record.id;
CREATE TABLE public.frequency_scale_factor (
    id bigint NOT NULL,
    level_of_theory_id bigint NOT NULL,
    scale_kind public.frequency_scale_kind NOT NULL,
    value double precision NOT NULL,
    source_literature_id bigint,
    workflow_tool_release_id bigint,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('fsf_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    software_release_id bigint,
    CONSTRAINT ck_frequency_scale_factor_value_gt_0 CHECK ((value > (0)::double precision))
);
CREATE SEQUENCE public.frequency_scale_factor_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.frequency_scale_factor_id_seq OWNED BY public.frequency_scale_factor.id;
CREATE TABLE public.geometry (
    id bigint NOT NULL,
    natoms integer NOT NULL,
    geom_hash character(64) NOT NULL,
    xyz_text text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('geom_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    CONSTRAINT ck_geometry_natoms_ge_1 CHECK ((natoms >= 1))
);
CREATE TABLE public.geometry_atom (
    geometry_id bigint NOT NULL,
    atom_index integer NOT NULL,
    element character(2) NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    z double precision NOT NULL,
    isotope_mass_number smallint,
    CONSTRAINT ck_geometry_atom_atom_index_ge_1 CHECK ((atom_index >= 1)),
    CONSTRAINT ck_geometry_atom_element_canonical CHECK ((btrim((element)::text) ~ '^[A-Z][a-z]?$'::text)),
    CONSTRAINT ck_geometry_atom_isotope_mass_number_ge_1 CHECK (((isotope_mass_number IS NULL) OR (isotope_mass_number >= 1)))
);
CREATE SEQUENCE public.geometry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.geometry_id_seq OWNED BY public.geometry.id;
CREATE TABLE public.group_additivity_scheme (
    id bigint NOT NULL,
    name text NOT NULL,
    version text,
    description text,
    source_literature_id bigint,
    code_commit text,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('gasch_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE SEQUENCE public.group_additivity_scheme_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.group_additivity_scheme_id_seq OWNED BY public.group_additivity_scheme.id;
CREATE TABLE public.idempotency_record (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    request_method character varying(8) NOT NULL,
    endpoint text NOT NULL,
    idempotency_key character varying(200) NOT NULL,
    payload_hash character(64) NOT NULL,
    status_code integer NOT NULL,
    response_body_json jsonb NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.idempotency_record_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.idempotency_record_id_seq OWNED BY public.idempotency_record.id;
CREATE TABLE public.kinetics (
    id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL,
    scientific_origin public.scientific_origin_kind NOT NULL,
    model_kind public.kinetics_model_kind DEFAULT 'modified_arrhenius'::public.kinetics_model_kind NOT NULL,
    literature_id bigint,
    workflow_tool_release_id bigint,
    software_release_id bigint,
    a double precision,
    a_units public.arrhenius_a_units,
    n double precision,
    ea_kj_mol double precision,
    a_uncertainty double precision,
    a_uncertainty_kind public.kinetics_uncertainty_kind,
    n_uncertainty double precision,
    ea_uncertainty_kj_mol double precision,
    tmin_k double precision,
    tmax_k double precision,
    degeneracy double precision,
    tunneling_model public.tunneling_model,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('kin_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    pressure_context public.pressure_context,
    pressure_bar double precision,
    is_third_body boolean DEFAULT false NOT NULL,
    direction public.kinetics_direction,
    network_kinetics_id bigint,
    degeneracy_convention public.kinetics_degeneracy_convention DEFAULT 'unknown'::public.kinetics_degeneracy_convention NOT NULL,
    t0_k double precision DEFAULT '1'::double precision NOT NULL,
    determination_id bigint,
    representation_role public.kinetics_representation_role,
    applicability_declaration jsonb,
    protocol_declaration jsonb,
    energy_level_of_theory_id bigint,
    CONSTRAINT ck_kinetics_a_uncertainty_kind_required_with_value CHECK (((a_uncertainty IS NULL) = (a_uncertainty_kind IS NULL))),
    CONSTRAINT ck_kinetics_a_uncertainty_multiplicative_ge_1 CHECK (((a_uncertainty_kind <> 'multiplicative'::public.kinetics_uncertainty_kind) OR (a_uncertainty >= (1.0)::double precision))),
    CONSTRAINT ck_kinetics_apparent_pressure_requires_pressure_bar CHECK (((pressure_context <> 'apparent_at_pressure'::public.pressure_context) OR (pressure_bar IS NOT NULL))),
    CONSTRAINT ck_kinetics_applicability_declaration_versioned_object CHECK (((applicability_declaration IS NULL) OR ((jsonb_typeof(applicability_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((applicability_declaration -> 'version'::text)), ''::text) = 'number'::text)))),
    CONSTRAINT ck_kinetics_degeneracy_finite_positive CHECK (((degeneracy IS NULL) OR ((degeneracy > (0)::double precision) AND (degeneracy < 'Infinity'::double precision)))),
    CONSTRAINT ck_kinetics_determination_iff_role CHECK (((determination_id IS NULL) = (representation_role IS NULL))),
    CONSTRAINT ck_kinetics_pressure_bar_gt_0 CHECK (((pressure_bar IS NULL) OR (pressure_bar > (0)::double precision))),
    CONSTRAINT ck_kinetics_protocol_declaration_versioned_object CHECK (((protocol_declaration IS NULL) OR ((jsonb_typeof(protocol_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((protocol_declaration -> 'version'::text)), ''::text) = 'number'::text)))),
    CONSTRAINT ck_kinetics_t0_k_finite_positive CHECK (((t0_k > (0)::double precision) AND (t0_k <= (10000)::double precision))),
    CONSTRAINT ck_kinetics_tmax_k_gt_0 CHECK (((tmax_k IS NULL) OR (tmax_k > (0)::double precision))),
    CONSTRAINT ck_kinetics_tmin_k_gt_0 CHECK (((tmin_k IS NULL) OR (tmin_k > (0)::double precision))),
    CONSTRAINT ck_kinetics_tmin_le_tmax CHECK (((tmin_k IS NULL) OR (tmax_k IS NULL) OR (tmin_k <= tmax_k)))
);
CREATE TABLE public.kinetics_arrhenius_entry (
    id bigint NOT NULL,
    kinetics_id bigint NOT NULL,
    entry_index integer NOT NULL,
    a double precision NOT NULL,
    a_units public.arrhenius_a_units,
    n double precision,
    ea_kj_mol double precision,
    CONSTRAINT ck_kinetics_arrhenius_entry_arrhenius_entry_index_ge_1 CHECK ((entry_index >= 1))
);
CREATE SEQUENCE public.kinetics_arrhenius_entry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.kinetics_arrhenius_entry_id_seq OWNED BY public.kinetics_arrhenius_entry.id;
CREATE TABLE public.kinetics_bath_gas (
    kinetics_id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    mole_fraction double precision NOT NULL,
    CONSTRAINT ck_kinetics_bath_gas_mole_fraction_range CHECK (((mole_fraction > (0)::double precision) AND (mole_fraction <= (1)::double precision)))
);
CREATE TABLE public.kinetics_chebyshev (
    kinetics_id bigint NOT NULL,
    n_temperature smallint NOT NULL,
    n_pressure smallint NOT NULL,
    tmin_k double precision,
    tmax_k double precision,
    pmin_bar double precision,
    pmax_bar double precision,
    coefficients jsonb NOT NULL,
    CONSTRAINT ck_kinetics_chebyshev_cheb_n_pressure_ge_1 CHECK ((n_pressure >= 1)),
    CONSTRAINT ck_kinetics_chebyshev_cheb_n_temperature_ge_1 CHECK ((n_temperature >= 1))
);
CREATE TABLE public.kinetics_determination (
    id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL,
    direction public.kinetics_direction NOT NULL,
    target_kind public.kinetics_determination_target_kind NOT NULL,
    target_transition_state_entry_id bigint,
    target_network_channel_id bigint,
    literature_id bigint,
    workflow_tool_release_id bigint,
    determination_key text NOT NULL,
    identity_hash character varying(64) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_kinetics_determination_identity_hash_sha256_hex CHECK (((identity_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_kinetics_determination_key_bounded CHECK (((length(btrim(determination_key)) > 0) AND (length(determination_key) <= 128))),
    CONSTRAINT ck_kinetics_determination_source_required CHECK (((literature_id IS NOT NULL) OR (workflow_tool_release_id IS NOT NULL))),
    CONSTRAINT ck_kinetics_determination_target_matches_kind CHECK ((((target_kind = 'whole_reaction'::public.kinetics_determination_target_kind) AND (target_transition_state_entry_id IS NULL) AND (target_network_channel_id IS NULL)) OR ((target_kind = 'resolved_channel'::public.kinetics_determination_target_kind) AND (num_nonnulls(target_transition_state_entry_id, target_network_channel_id) = 1))))
);
CREATE SEQUENCE public.kinetics_determination_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.kinetics_determination_id_seq OWNED BY public.kinetics_determination.id;
CREATE TABLE public.kinetics_falloff (
    kinetics_id bigint NOT NULL,
    low_a double precision NOT NULL,
    low_a_units public.arrhenius_a_units,
    low_n double precision,
    low_ea_kj_mol double precision,
    troe_alpha double precision,
    troe_t3 double precision,
    troe_t1 double precision,
    troe_t2 double precision,
    sri_a double precision,
    sri_b double precision,
    sri_c double precision,
    sri_d double precision,
    sri_e double precision,
    note text
);
CREATE SEQUENCE public.kinetics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.kinetics_id_seq OWNED BY public.kinetics.id;
CREATE TABLE public.kinetics_interpretation_assignment (
    kinetics_id bigint NOT NULL,
    subject_key text NOT NULL,
    role text NOT NULL,
    statmech_id bigint NOT NULL,
    conformer_selection_id bigint,
    transition_state_entry_id bigint,
    ensemble_policy public.kinetics_ensemble_policy NOT NULL,
    standard_state_convention public.kinetics_standard_state_convention NOT NULL,
    degeneracy_interpretation public.kinetics_degeneracy_interpretation NOT NULL,
    convention_note text,
    CONSTRAINT ck_kinetics_interpretation_assignment_other_note CHECK ((((ensemble_policy <> 'other'::public.kinetics_ensemble_policy) AND (standard_state_convention <> 'other'::public.kinetics_standard_state_convention) AND (degeneracy_interpretation <> 'other'::public.kinetics_degeneracy_interpretation)) OR (convention_note IS NOT NULL))),
    CONSTRAINT ck_kinetics_interpretation_assignment_subject_shape CHECK ((((role = 'transition_state'::text) AND (subject_key = 'transition_state'::text) AND (transition_state_entry_id IS NOT NULL) AND (conformer_selection_id IS NULL)) OR ((role = ANY (ARRAY['reactant'::text, 'product'::text])) AND (subject_key ~ (('^'::text || role) || ':[0-9]+$'::text)) AND (transition_state_entry_id IS NULL))))
);
CREATE TABLE public.kinetics_plog (
    id bigint NOT NULL,
    kinetics_id bigint NOT NULL,
    entry_index integer NOT NULL,
    pressure_bar double precision NOT NULL,
    a double precision NOT NULL,
    a_units public.arrhenius_a_units,
    n double precision,
    ea_kj_mol double precision,
    CONSTRAINT ck_kinetics_plog_plog_entry_index_ge_1 CHECK ((entry_index >= 1)),
    CONSTRAINT ck_kinetics_plog_plog_pressure_bar_gt_0 CHECK ((pressure_bar > (0)::double precision))
);
CREATE SEQUENCE public.kinetics_plog_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.kinetics_plog_id_seq OWNED BY public.kinetics_plog.id;
CREATE TABLE public.kinetics_source_calculation (
    kinetics_id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    role public.kinetics_calc_role NOT NULL
);
CREATE TABLE public.kinetics_third_body_efficiency (
    id bigint NOT NULL,
    kinetics_id bigint NOT NULL,
    collider_species_id bigint NOT NULL,
    efficiency double precision NOT NULL,
    CONSTRAINT ck_kinetics_third_body_efficiency_efficiency_ge_0 CHECK ((efficiency >= (0)::double precision))
);
CREATE SEQUENCE public.kinetics_third_body_efficiency_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.kinetics_third_body_efficiency_id_seq OWNED BY public.kinetics_third_body_efficiency.id;
CREATE TABLE public.kinetics_tunneling_application (
    kinetics_id bigint NOT NULL,
    model text NOT NULL,
    model_identifier text,
    transition_state_entry_id bigint NOT NULL,
    source_calculation_id bigint,
    imaginary_frequency_cm1 double precision,
    frequency_sign_convention text DEFAULT 'negative_imaginary_cm1'::text NOT NULL,
    reactant_energy_kj_mol double precision,
    product_energy_kj_mol double precision,
    forward_barrier_kj_mol double precision,
    reverse_barrier_kj_mol double precision,
    energy_zero_convention public.energy_zero_convention,
    energy_correction_convention public.energy_correction_convention,
    convention_note text,
    result_artifact_id bigint,
    sct_path_integral_artifact_id bigint,
    CONSTRAINT ck_kinetics_tunneling_application_imaginary_negative CHECK (((imaginary_frequency_cm1 IS NULL) OR (imaginary_frequency_cm1 < (0)::double precision))),
    CONSTRAINT ck_kinetics_tunneling_application_model_enum CHECK ((model = ANY (ARRAY['none'::text, 'wigner'::text, 'eckart'::text, 'sct'::text, 'other'::text]))),
    CONSTRAINT ck_kinetics_tunneling_application_other_note CHECK ((((energy_zero_convention IS DISTINCT FROM 'other'::public.energy_zero_convention) AND (energy_correction_convention IS DISTINCT FROM 'other'::public.energy_correction_convention)) OR (convention_note IS NOT NULL))),
    CONSTRAINT ck_kinetics_tunneling_application_other_replayable CHECK (((model <> 'other'::text) OR ((model_identifier IS NOT NULL) AND (result_artifact_id IS NOT NULL))))
);
CREATE TABLE public.level_of_theory (
    id bigint NOT NULL,
    method text NOT NULL,
    basis text,
    aux_basis text,
    cabs_basis text,
    dispersion text,
    solvent text,
    solvent_model text,
    lot_hash character(64) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('lot_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    spin_treatment public.spin_treatment,
    core_treatment public.core_treatment,
    relativistic_hamiltonian public.relativistic_hamiltonian,
    pno_truncation public.pno_truncation,
    ri_approximation public.ri_approximation
);
CREATE TABLE public.level_of_theory_composite (
    level_of_theory_id bigint NOT NULL,
    scheme_id bigint NOT NULL,
    binding_source public.composite_binding_source NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.level_of_theory_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.level_of_theory_id_seq OWNED BY public.level_of_theory.id;
CREATE TABLE public.level_of_theory_merge (
    merged_lot_id bigint NOT NULL,
    into_lot_id bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_level_of_theory_merge_not_merged_into_itself CHECK ((merged_lot_id <> into_lot_id))
);
CREATE TABLE public.literature (
    id bigint NOT NULL,
    kind public.literature_kind NOT NULL,
    title text NOT NULL,
    journal text,
    year integer,
    volume text,
    issue text,
    pages text,
    doi text,
    isbn text,
    url text,
    publisher text,
    institution text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('lit_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    title_source public.literature_title_source
);
CREATE TABLE public.literature_author (
    literature_id bigint NOT NULL,
    author_id bigint NOT NULL,
    author_order integer NOT NULL
);
CREATE SEQUENCE public.literature_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.literature_id_seq OWNED BY public.literature.id;
CREATE TABLE public.machine_review_curator_task (
    id bigint NOT NULL,
    submission_id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    record_id bigint NOT NULL,
    finding_fingerprint character varying(64) NOT NULL,
    workflow_state public.machine_review_curator_task_state DEFAULT 'untriaged'::public.machine_review_curator_task_state NOT NULL,
    machine_review_status public.machine_review_status NOT NULL,
    highest_severity public.machine_review_severity NOT NULL,
    findings_count integer DEFAULT 1 NOT NULL,
    source_audit_event_id bigint,
    assigned_to bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    resolved_at timestamp without time zone,
    resolved_by bigint,
    resolution_note text,
    CONSTRAINT ck_machine_review_curator_task_findings_count_positive CHECK ((findings_count >= 1)),
    CONSTRAINT ck_machine_review_curator_task_resolution_consistency CHECK (((workflow_state = ANY (ARRAY['resolved_no_action'::public.machine_review_curator_task_state, 'resolved_human_reviewed'::public.machine_review_curator_task_state, 'dismissed_machine_finding'::public.machine_review_curator_task_state])) = ((resolved_at IS NOT NULL) AND (resolved_by IS NOT NULL) AND (resolution_note IS NOT NULL))))
);
CREATE SEQUENCE public.machine_review_curator_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.machine_review_curator_task_id_seq OWNED BY public.machine_review_curator_task.id;
CREATE TABLE public.molecular_property_observation (
    id bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    species_entry_id bigint,
    scientific_origin public.scientific_origin_kind NOT NULL,
    property_kind public.molecular_property_kind NOT NULL,
    property_label text,
    scalar_value double precision,
    scalar_unit text,
    scalar_uncertainty double precision,
    vector_json jsonb,
    tensor_json jsonb,
    temperature_k double precision,
    wavelength_nm double precision,
    method_note text,
    state_label_raw text,
    literature_id bigint,
    software_release_id bigint,
    workflow_tool_release_id bigint,
    source_calculation_id bigint,
    external_source_name text,
    external_source_release text,
    external_source_doi text,
    external_source_url text,
    external_source_record_key text,
    external_source_page_kind text,
    external_source_content_sha256 text,
    external_source_parser_version text,
    reference_label text,
    reference_comment text,
    raw_reference_text text,
    raw_payload_json jsonb,
    note text,
    pressure_bar double precision,
    state_basis public.observed_state_basis,
    uncertainty_kind public.observed_uncertainty_kind,
    uncertainty_coverage_factor double precision,
    uncertainty_level_of_confidence_pct double precision,
    uncertainty_assessor public.observed_uncertainty_assessor,
    external_source_record_id bigint,
    public_ref character varying(40) DEFAULT public.mpo_opaque_public_ref() NOT NULL,
    external_source_parser_name text,
    CONSTRAINT ck_molecular_property_observation_mpo_scalar_uncertainty_ge_0 CHECK (((scalar_uncertainty IS NULL) OR (scalar_uncertainty >= (0)::double precision))),
    CONSTRAINT ck_molecular_property_observation_mpo_scalar_value_has_unit CHECK (((scalar_value IS NULL) OR (scalar_unit IS NOT NULL))),
    CONSTRAINT ck_molecular_property_observation_mpo_temperature_k_gt_0 CHECK (((temperature_k IS NULL) OR (temperature_k > (0)::double precision))),
    CONSTRAINT ck_molecular_property_observation_mpo_value_at_least_one CHECK (((scalar_value IS NOT NULL) OR (vector_json IS NOT NULL) OR (tensor_json IS NOT NULL))),
    CONSTRAINT ck_molecular_property_observation_mpo_wavelength_nm_gt_0 CHECK (((wavelength_nm IS NULL) OR (wavelength_nm > (0)::double precision))),
    CONSTRAINT ck_mpo_coverage_factor_only_expanded CHECK (((uncertainty_coverage_factor IS NULL) OR (uncertainty_kind = ANY (ARRAY['expanded'::public.observed_uncertainty_kind, 'combined_expanded'::public.observed_uncertainty_kind])))),
    CONSTRAINT ck_mpo_heat_capacity_cp_requires_temperature CHECK (((property_kind <> 'heat_capacity_cp'::public.molecular_property_kind) OR (temperature_k IS NOT NULL))),
    CONSTRAINT ck_mpo_heat_capacity_cp_unit_j_mol_k CHECK (((property_kind <> 'heat_capacity_cp'::public.molecular_property_kind) OR (scalar_unit = 'J/mol/K'::text))),
    CONSTRAINT ck_mpo_pressure_bar_gt_0 CHECK (((pressure_bar IS NULL) OR (pressure_bar > (0)::double precision))),
    CONSTRAINT ck_mpo_uncertainty_confidence_pct_range CHECK (((uncertainty_level_of_confidence_pct IS NULL) OR ((uncertainty_level_of_confidence_pct > (0)::double precision) AND (uncertainty_level_of_confidence_pct <= (100)::double precision)))),
    CONSTRAINT ck_mpo_uncertainty_coverage_factor_ge_1 CHECK (((uncertainty_coverage_factor IS NULL) OR (uncertainty_coverage_factor >= (1)::double precision))),
    CONSTRAINT ck_mpo_uncertainty_kind_iff_value CHECK (((property_kind <> 'heat_capacity_cp'::public.molecular_property_kind) OR ((scalar_uncertainty IS NULL) = (uncertainty_kind IS NULL))))
);
CREATE SEQUENCE public.molecular_property_observation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.molecular_property_observation_id_seq OWNED BY public.molecular_property_observation.id;
CREATE TABLE public.network (
    id bigint NOT NULL,
    name text,
    description text,
    literature_id bigint,
    software_release_id bigint,
    workflow_tool_release_id bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('net_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE TABLE public.network_channel (
    id bigint NOT NULL,
    network_id bigint NOT NULL,
    source_state_id bigint NOT NULL,
    sink_state_id bigint NOT NULL,
    kind public.network_channel_kind NOT NULL,
    channel_key text NOT NULL,
    mechanism public.network_channel_mechanism DEFAULT 'elementary'::public.network_channel_mechanism NOT NULL,
    CONSTRAINT ck_network_channel_source_ne_sink CHECK ((source_state_id <> sink_state_id))
);
CREATE SEQUENCE public.network_channel_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_channel_id_seq OWNED BY public.network_channel.id;
CREATE TABLE public.network_channel_microreaction (
    id bigint NOT NULL,
    channel_id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL,
    transition_state_entry_id bigint
);
CREATE SEQUENCE public.network_channel_microreaction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_channel_microreaction_id_seq OWNED BY public.network_channel_microreaction.id;
CREATE SEQUENCE public.network_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_id_seq OWNED BY public.network.id;
CREATE TABLE public.network_kinetics (
    id bigint NOT NULL,
    channel_id bigint NOT NULL,
    solve_id bigint NOT NULL,
    model_kind public.network_kinetics_model_kind NOT NULL,
    tmin_k double precision,
    tmax_k double precision,
    pmin_bar double precision,
    pmax_bar double precision,
    rate_units public.arrhenius_a_units,
    pressure_units public.pressure_unit,
    temperature_units public.temperature_unit,
    stores_log10_k boolean,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('nkin_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    determination_id bigint,
    representation_role public.network_representation_role,
    representation_declaration jsonb,
    CONSTRAINT ck_network_kinetics_determination_iff_representation CHECK (((determination_id IS NULL) = (representation_declaration IS NULL))),
    CONSTRAINT ck_network_kinetics_determination_iff_role CHECK (((determination_id IS NULL) = (representation_role IS NULL))),
    CONSTRAINT ck_network_kinetics_pmax_bar_gt_0 CHECK (((pmax_bar IS NULL) OR (pmax_bar > (0)::double precision))),
    CONSTRAINT ck_network_kinetics_pmin_bar_gt_0 CHECK (((pmin_bar IS NULL) OR (pmin_bar > (0)::double precision))),
    CONSTRAINT ck_network_kinetics_pmin_le_pmax CHECK (((pmin_bar IS NULL) OR (pmax_bar IS NULL) OR (pmin_bar <= pmax_bar))),
    CONSTRAINT ck_network_kinetics_representation_declaration_versioned_object CHECK (((representation_declaration IS NULL) OR ((jsonb_typeof(representation_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((representation_declaration -> 'version'::text)), ''::text) = 'number'::text) AND (COALESCE(jsonb_typeof((representation_declaration -> 'key'::text)), ''::text) = 'string'::text)))),
    CONSTRAINT ck_network_kinetics_tmax_k_gt_0 CHECK (((tmax_k IS NULL) OR (tmax_k > (0)::double precision))),
    CONSTRAINT ck_network_kinetics_tmin_k_gt_0 CHECK (((tmin_k IS NULL) OR (tmin_k > (0)::double precision))),
    CONSTRAINT ck_network_kinetics_tmin_le_tmax CHECK (((tmin_k IS NULL) OR (tmax_k IS NULL) OR (tmin_k <= tmax_k)))
);
CREATE TABLE public.network_kinetics_chebyshev (
    network_kinetics_id bigint NOT NULL,
    n_temperature smallint NOT NULL,
    n_pressure smallint NOT NULL,
    coefficients jsonb NOT NULL,
    CONSTRAINT ck_network_kinetics_chebyshev_n_pressure_ge_1 CHECK ((n_pressure >= 1)),
    CONSTRAINT ck_network_kinetics_chebyshev_n_temperature_ge_1 CHECK ((n_temperature >= 1))
);
CREATE TABLE public.network_kinetics_determination (
    id bigint NOT NULL,
    solve_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    determination_key text NOT NULL,
    observable_declaration jsonb NOT NULL,
    identity_hash character varying(64) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_network_kinetics_determination_identity_hash_sha256_hex CHECK (((identity_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_network_kinetics_determination_key_bounded CHECK (((length(btrim(determination_key)) > 0) AND (length(determination_key) <= 128))),
    CONSTRAINT ck_network_kinetics_determination_observable_versioned_object CHECK (((jsonb_typeof(observable_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((observable_declaration -> 'version'::text)), ''::text) = 'number'::text)))
);
CREATE SEQUENCE public.network_kinetics_determination_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_kinetics_determination_id_seq OWNED BY public.network_kinetics_determination.id;
CREATE SEQUENCE public.network_kinetics_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_kinetics_id_seq OWNED BY public.network_kinetics.id;
CREATE TABLE public.network_kinetics_plog (
    network_kinetics_id bigint NOT NULL,
    pressure_bar double precision NOT NULL,
    entry_index smallint DEFAULT '1'::smallint NOT NULL,
    a double precision NOT NULL,
    a_units public.arrhenius_a_units,
    n double precision NOT NULL,
    ea_kj_mol double precision NOT NULL,
    CONSTRAINT ck_network_kinetics_plog_entry_index_ge_1 CHECK ((entry_index >= 1)),
    CONSTRAINT ck_network_kinetics_plog_pressure_bar_gt_0 CHECK ((pressure_bar > (0)::double precision))
);
CREATE TABLE public.network_kinetics_point (
    network_kinetics_id bigint NOT NULL,
    temperature_k double precision NOT NULL,
    pressure_bar double precision NOT NULL,
    rate_value double precision NOT NULL,
    CONSTRAINT ck_network_kinetics_point_pressure_bar_gt_0 CHECK ((pressure_bar > (0)::double precision)),
    CONSTRAINT ck_network_kinetics_point_temperature_k_gt_0 CHECK ((temperature_k > (0)::double precision))
);
CREATE TABLE public.network_reaction (
    network_id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL
);
CREATE TABLE public.network_solve (
    id bigint NOT NULL,
    network_id bigint NOT NULL,
    literature_id bigint,
    software_release_id bigint,
    workflow_tool_release_id bigint,
    me_method text,
    interpolation_model text,
    grain_size_cm_inv double precision,
    grain_count integer,
    emax_kj_mol double precision,
    tmin_k double precision,
    tmax_k double precision,
    pmin_bar double precision,
    pmax_bar double precision,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('nsolve_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    kind public.network_solve_kind DEFAULT 'computed'::public.network_solve_kind NOT NULL,
    target_declaration jsonb,
    protocol_declaration jsonb,
    validation_declaration jsonb,
    CONSTRAINT ck_network_solve_grain_count_ge_1 CHECK (((grain_count IS NULL) OR (grain_count >= 1))),
    CONSTRAINT ck_network_solve_pmax_bar_gt_0 CHECK (((pmax_bar IS NULL) OR (pmax_bar > (0)::double precision))),
    CONSTRAINT ck_network_solve_pmin_bar_gt_0 CHECK (((pmin_bar IS NULL) OR (pmin_bar > (0)::double precision))),
    CONSTRAINT ck_network_solve_pmin_le_pmax CHECK (((pmin_bar IS NULL) OR (pmax_bar IS NULL) OR (pmin_bar <= pmax_bar))),
    CONSTRAINT ck_network_solve_protocol_declaration_versioned_object CHECK (((protocol_declaration IS NULL) OR ((jsonb_typeof(protocol_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((protocol_declaration -> 'version'::text)), ''::text) = 'number'::text)))),
    CONSTRAINT ck_network_solve_reported_requires_literature CHECK (((kind <> 'reported'::public.network_solve_kind) OR (literature_id IS NOT NULL))),
    CONSTRAINT ck_network_solve_target_declaration_versioned_object CHECK (((target_declaration IS NULL) OR ((jsonb_typeof(target_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((target_declaration -> 'version'::text)), ''::text) = 'number'::text)))),
    CONSTRAINT ck_network_solve_tmax_k_gt_0 CHECK (((tmax_k IS NULL) OR (tmax_k > (0)::double precision))),
    CONSTRAINT ck_network_solve_tmin_k_gt_0 CHECK (((tmin_k IS NULL) OR (tmin_k > (0)::double precision))),
    CONSTRAINT ck_network_solve_tmin_le_tmax CHECK (((tmin_k IS NULL) OR (tmax_k IS NULL) OR (tmin_k <= tmax_k))),
    CONSTRAINT ck_network_solve_validation_declaration_versioned_object CHECK (((validation_declaration IS NULL) OR ((jsonb_typeof(validation_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((validation_declaration -> 'version'::text)), ''::text) = 'number'::text))))
);
CREATE TABLE public.network_solve_bath_gas (
    solve_id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    mole_fraction double precision NOT NULL,
    CONSTRAINT ck_network_solve_bath_gas_mole_fraction_range CHECK (((mole_fraction > (0)::double precision) AND (mole_fraction <= (1)::double precision)))
);
CREATE TABLE public.network_solve_channel_barrier (
    solve_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL,
    transition_state_entry_id bigint NOT NULL,
    forward_barrier_kj_mol double precision NOT NULL,
    reverse_barrier_kj_mol double precision NOT NULL,
    energy_zero_convention public.energy_zero_convention NOT NULL,
    correction_convention public.energy_correction_convention NOT NULL,
    convention_note text,
    source_calculation_id bigint,
    CONSTRAINT ck_network_solve_channel_barrier_barriers_finite CHECK (((forward_barrier_kj_mol <> 'NaN'::double precision) AND (reverse_barrier_kj_mol <> 'NaN'::double precision))),
    CONSTRAINT ck_network_solve_channel_barrier_other_note CHECK ((((energy_zero_convention <> 'other'::public.energy_zero_convention) AND (correction_convention <> 'other'::public.energy_correction_convention)) OR (convention_note IS NOT NULL)))
);
CREATE TABLE public.network_solve_energy_transfer (
    id bigint NOT NULL,
    solve_id bigint NOT NULL,
    model text,
    alpha0_cm_inv double precision,
    t_exponent double precision,
    t_ref_k double precision,
    note text,
    state_id bigint,
    collider_species_entry_id bigint,
    scope public.network_energy_transfer_scope DEFAULT 'per_well'::public.network_energy_transfer_scope NOT NULL,
    CONSTRAINT ck_network_solve_energy_transfer_scope_columns_agree CHECK ((((scope = 'per_well'::public.network_energy_transfer_scope) AND (state_id IS NOT NULL) AND (collider_species_entry_id IS NOT NULL)) OR ((scope = 'network_wide'::public.network_energy_transfer_scope) AND (state_id IS NULL) AND (collider_species_entry_id IS NULL))))
);
CREATE SEQUENCE public.network_solve_energy_transfer_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_solve_energy_transfer_id_seq OWNED BY public.network_solve_energy_transfer.id;
CREATE SEQUENCE public.network_solve_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_solve_id_seq OWNED BY public.network_solve.id;
CREATE TABLE public.network_solve_source_calculation (
    solve_id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    role public.network_solve_calc_role NOT NULL
);
CREATE TABLE public.network_solve_state_energy (
    solve_id bigint NOT NULL,
    state_id bigint NOT NULL,
    energy_kj_mol double precision NOT NULL,
    energy_zero_convention public.energy_zero_convention NOT NULL,
    correction_convention public.energy_correction_convention NOT NULL,
    convention_note text,
    source_calculation_id bigint,
    source_sum_comparison text,
    source_sum_not_compared_reason text,
    energy_precision_kj_mol double precision,
    zpe_scale_factor double precision,
    CONSTRAINT ck_network_solve_state_energy_energy_precision_positive CHECK (((energy_precision_kj_mol IS NULL) OR ((energy_precision_kj_mol > (0)::double precision) AND (energy_precision_kj_mol < 'Infinity'::double precision)))),
    CONSTRAINT ck_network_solve_state_energy_other_note CHECK ((((energy_zero_convention <> 'other'::public.energy_zero_convention) AND (correction_convention <> 'other'::public.energy_correction_convention)) OR (convention_note IS NOT NULL))),
    CONSTRAINT ck_network_solve_state_energy_sum_comparison CHECK (((source_sum_comparison IS NULL) OR (source_sum_comparison = ANY (ARRAY['agrees'::text, 'not_compared'::text])))),
    CONSTRAINT ck_network_solve_state_energy_sum_reason_shape CHECK (((NOT (source_sum_comparison IS DISTINCT FROM 'not_compared'::text)) = (source_sum_not_compared_reason IS NOT NULL))),
    CONSTRAINT ck_network_solve_state_energy_sum_reason_token CHECK (((source_sum_not_compared_reason IS NULL) OR (source_sum_not_compared_reason = ANY (ARRAY['no_source_stated'::text, 'sources_incomplete'::text, 'convention_not_summable'::text, 'energy_zero_not_comparable'::text, 'stored_energy_not_stated'::text, 'zpe_not_in_source'::text, 'no_second_state_on_the_same_zero'::text, 'stated_precision_unknown'::text, 'zpe_not_stated'::text, 'geometry_not_paired'::text, 'zpe_scaling_unstated'::text])))),
    CONSTRAINT ck_network_solve_state_energy_zpe_scale_factor_positive CHECK (((zpe_scale_factor IS NULL) OR ((zpe_scale_factor > (0)::double precision) AND (zpe_scale_factor < 'Infinity'::double precision))))
);
CREATE TABLE public.network_solve_state_energy_source (
    solve_id bigint NOT NULL,
    state_id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    zpe_calculation_id bigint
);
CREATE TABLE public.network_species (
    network_id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    role public.network_species_role NOT NULL
);
CREATE TABLE public.network_state (
    id bigint NOT NULL,
    network_id bigint NOT NULL,
    kind public.network_state_kind NOT NULL,
    composition_hash character(64) NOT NULL,
    label text
);
CREATE SEQUENCE public.network_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.network_state_id_seq OWNED BY public.network_state.id;
CREATE TABLE public.network_state_participant (
    state_id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    stoichiometry smallint DEFAULT '1'::smallint NOT NULL,
    CONSTRAINT ck_network_state_participant_stoichiometry_ge_1 CHECK ((stoichiometry >= 1))
);
CREATE TABLE public.reaction_atom_map (
    id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL,
    transition_state_entry_id bigint NOT NULL,
    transition_state_geometry_id bigint NOT NULL,
    source public.atom_map_source NOT NULL,
    equivalent_map_count integer,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    CONSTRAINT ck_reaction_atom_map_equivalent_map_count_ge_1 CHECK (((equivalent_map_count IS NULL) OR (equivalent_map_count >= 1))),
    CONSTRAINT ck_reaction_atom_map_inferred_requires_note CHECK (((source <> 'inferred'::public.atom_map_source) OR ((note IS NOT NULL) AND (btrim(note) <> ''::text))))
);
CREATE SEQUENCE public.reaction_atom_map_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.reaction_atom_map_id_seq OWNED BY public.reaction_atom_map.id;
CREATE TABLE public.reaction_atom_map_pair (
    id bigint NOT NULL,
    atom_map_id bigint NOT NULL,
    side public.reaction_role NOT NULL,
    structure_participant_id bigint NOT NULL,
    geometry_id bigint NOT NULL,
    atom_index integer NOT NULL,
    transition_state_geometry_id bigint NOT NULL,
    ts_atom_index integer NOT NULL,
    element character(2) NOT NULL,
    ts_element character(2) NOT NULL,
    CONSTRAINT ck_reaction_atom_map_pair_atom_index_ge_1 CHECK ((atom_index >= 1)),
    CONSTRAINT ck_reaction_atom_map_pair_element_canonical CHECK ((btrim((element)::text) ~ '^[A-Z][a-z]?$'::text)),
    CONSTRAINT ck_reaction_atom_map_pair_element_matches CHECK ((element = ts_element)),
    CONSTRAINT ck_reaction_atom_map_pair_ts_atom_index_ge_1 CHECK ((ts_atom_index >= 1)),
    CONSTRAINT ck_reaction_atom_map_pair_ts_element_canonical CHECK ((btrim((ts_element)::text) ~ '^[A-Z][a-z]?$'::text))
);
CREATE SEQUENCE public.reaction_atom_map_pair_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.reaction_atom_map_pair_id_seq OWNED BY public.reaction_atom_map_pair.id;
CREATE TABLE public.reaction_entry (
    id bigint NOT NULL,
    reaction_id bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('rxe_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE SEQUENCE public.reaction_entry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.reaction_entry_id_seq OWNED BY public.reaction_entry.id;
CREATE TABLE public.reaction_entry_structure_participant (
    id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    role public.reaction_role NOT NULL,
    participant_index integer NOT NULL,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    CONSTRAINT ck_reaction_entry_structure_participant_participant_index_ge_1 CHECK ((participant_index >= 1))
);
CREATE SEQUENCE public.reaction_entry_structure_participant_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.reaction_entry_structure_participant_id_seq OWNED BY public.reaction_entry_structure_participant.id;
CREATE TABLE public.reaction_family (
    id bigint NOT NULL,
    name text NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.reaction_family_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.reaction_family_id_seq OWNED BY public.reaction_family.id;
CREATE TABLE public.reaction_participant (
    reaction_id bigint NOT NULL,
    species_id bigint NOT NULL,
    role public.reaction_role NOT NULL,
    stoichiometry smallint NOT NULL,
    CONSTRAINT ck_reaction_participant_stoichiometry_ge_1 CHECK ((stoichiometry >= 1))
);
CREATE TABLE public.record_machine_review (
    id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    record_id bigint NOT NULL,
    status public.machine_review_status NOT NULL,
    curator_priority character varying(16),
    summary text,
    findings_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    model character varying(128),
    provider character varying(128),
    context_hash character varying(64) NOT NULL,
    context_schema_version integer NOT NULL,
    source_submission_id bigint,
    source_audit_event_id bigint,
    reviewed_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    prompt_name character varying(128) NOT NULL,
    prompt_version integer NOT NULL,
    rubric_name character varying(128) NOT NULL,
    rubric_version integer NOT NULL,
    CONSTRAINT ck_record_machine_review_context_hash_len CHECK ((char_length((context_hash)::text) = 64)),
    CONSTRAINT ck_record_machine_review_findings_json_is_array CHECK ((jsonb_typeof(findings_json) = 'array'::text)),
    CONSTRAINT ck_record_machine_review_versions_in_range CHECK (((context_schema_version >= 1) AND (prompt_version >= 0) AND (rubric_version >= 0)))
);
CREATE SEQUENCE public.record_machine_review_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.record_machine_review_id_seq OWNED BY public.record_machine_review.id;
CREATE TABLE public.record_reproducibility_assessment (
    id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    record_id bigint NOT NULL,
    grade public.reproducibility_grade NOT NULL,
    rubric_name character varying(128) NOT NULL,
    rubric_version integer NOT NULL,
    context_hash character varying(64) NOT NULL,
    context_json jsonb NOT NULL,
    passed_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    missing_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    warnings_json jsonb DEFAULT '[]'::jsonb NOT NULL,
    assessor_kind public.reproducibility_assessor_kind NOT NULL,
    assessor_user_id bigint,
    source_submission_id bigint,
    assessed_at timestamp without time zone DEFAULT now() NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT public.rpa_opaque_public_ref() NOT NULL,
    CONSTRAINT ck_record_reproducibility_assessment_assessor_user_consistent CHECK ((((assessor_kind = 'system'::public.reproducibility_assessor_kind) AND (assessor_user_id IS NULL)) OR ((assessor_kind = 'curator'::public.reproducibility_assessor_kind) AND (assessor_user_id IS NOT NULL)))),
    CONSTRAINT ck_record_reproducibility_assessment_context_hash_lower_hex CHECK (((context_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_record_reproducibility_assessment_context_json_is_object CHECK ((jsonb_typeof(context_json) = 'object'::text)),
    CONSTRAINT ck_record_reproducibility_assessment_missing_json_is_array CHECK ((jsonb_typeof(missing_json) = 'array'::text)),
    CONSTRAINT ck_record_reproducibility_assessment_passed_json_is_array CHECK ((jsonb_typeof(passed_json) = 'array'::text)),
    CONSTRAINT ck_record_reproducibility_assessment_rubric_version_nonneg CHECK ((rubric_version >= 0)),
    CONSTRAINT ck_record_reproducibility_assessment_warnings_json_is_array CHECK ((jsonb_typeof(warnings_json) = 'array'::text))
);
CREATE SEQUENCE public.record_reproducibility_assessment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.record_reproducibility_assessment_id_seq OWNED BY public.record_reproducibility_assessment.id;
CREATE TABLE public.record_review (
    id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    record_id bigint NOT NULL,
    status public.record_review_status DEFAULT 'not_reviewed'::public.record_review_status NOT NULL,
    submission_id bigint,
    reviewed_by bigint,
    reviewed_at timestamp without time zone,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    first_approved_at timestamp without time zone,
    CONSTRAINT ck_record_review_record_review_approved_has_first_approval CHECK (((status <> 'approved'::public.record_review_status) OR (first_approved_at IS NOT NULL))),
    CONSTRAINT ck_record_review_record_review_terminal_requires_reviewer CHECK (((status <> ALL (ARRAY['approved'::public.record_review_status, 'rejected'::public.record_review_status, 'deprecated'::public.record_review_status])) OR ((reviewed_by IS NOT NULL) AND (reviewed_at IS NOT NULL))))
);
CREATE TABLE public.record_review_event (
    id bigint NOT NULL,
    record_review_id bigint NOT NULL,
    event_kind public.record_review_event_kind NOT NULL,
    from_status public.record_review_status,
    to_status public.record_review_status,
    actor_user_id bigint,
    reason text,
    details_json jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.record_review_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.record_review_event_id_seq OWNED BY public.record_review_event.id;
CREATE SEQUENCE public.record_review_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.record_review_id_seq OWNED BY public.record_review.id;
CREATE TABLE public.release_artifact (
    id bigint NOT NULL,
    release_manifest_id bigint NOT NULL,
    kind public.release_artifact_kind NOT NULL,
    path character varying(255) NOT NULL,
    media_type character varying(128) NOT NULL,
    content bytea NOT NULL,
    sha256 character varying(64) NOT NULL,
    byte_count bigint NOT NULL,
    record_count bigint NOT NULL,
    CONSTRAINT ck_release_artifact_byte_count_nonneg CHECK ((byte_count >= 0)),
    CONSTRAINT ck_release_artifact_path_nonblank CHECK ((length(btrim((path)::text)) > 0)),
    CONSTRAINT ck_release_artifact_record_count_nonneg CHECK ((record_count >= 0)),
    CONSTRAINT ck_release_artifact_sha256_hex CHECK (((sha256)::text ~ '^[0-9a-f]{64}$'::text))
);
CREATE SEQUENCE public.release_artifact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.release_artifact_id_seq OWNED BY public.release_artifact.id;
CREATE TABLE public.release_manifest (
    id bigint NOT NULL,
    dataset_release_id bigint NOT NULL,
    profile public.read_profile DEFAULT 'curated'::public.read_profile NOT NULL,
    alembic_revision character varying(64) NOT NULL,
    backend_version character varying(64) NOT NULL,
    schemas_package_version character varying(64) NOT NULL,
    curation_policy_id bigint NOT NULL,
    release_public_ref character varying(40) NOT NULL,
    release_published_at timestamp without time zone,
    release_tag character varying(64) NOT NULL,
    release_title text NOT NULL,
    release_description text,
    release_contact text NOT NULL,
    release_changelog_entry text,
    release_status_at_publication public.dataset_release_status NOT NULL,
    release_doi_at_publication character varying(128),
    data_license character varying(64) NOT NULL,
    code_license character varying(64) NOT NULL,
    citation_text text NOT NULL,
    curation_policy_ref character varying(40) NOT NULL,
    curation_policy_name character varying(128) NOT NULL,
    curation_policy_version character varying(64) NOT NULL,
    curation_policy_description text NOT NULL,
    curation_policy_criteria_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    contract_json jsonb NOT NULL,
    document_json jsonb NOT NULL,
    content_sha256 character varying(64) NOT NULL,
    selected_record_count bigint NOT NULL,
    candidate_record_count bigint NOT NULL,
    generated_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    rights_summary_json jsonb,
    manifest_format_name character varying(64) NOT NULL,
    manifest_format_version integer NOT NULL,
    review_policy_name character varying(64) NOT NULL,
    review_policy_version integer NOT NULL,
    recovery_archive_format_name character varying(64) NOT NULL,
    recovery_archive_format_version integer NOT NULL,
    CONSTRAINT ck_release_manifest_alembic_revision_nonblank CHECK ((length(btrim((alembic_revision)::text)) > 0)),
    CONSTRAINT ck_release_manifest_candidate_count_nonneg CHECK ((candidate_record_count >= 0)),
    CONSTRAINT ck_release_manifest_content_sha256_hex CHECK (((content_sha256)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_release_manifest_selected_count_nonneg CHECK ((selected_record_count >= 0)),
    CONSTRAINT ck_release_manifest_versions_positive CHECK (((manifest_format_version >= 1) AND (review_policy_version >= 1) AND (recovery_archive_format_version >= 1)))
);
CREATE SEQUENCE public.release_manifest_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.release_manifest_id_seq OWNED BY public.release_manifest.id;
CREATE TABLE public.release_selection (
    id bigint NOT NULL,
    dataset_release_id bigint NOT NULL,
    curation_policy_id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    record_id bigint NOT NULL,
    subject_type public.submission_record_type NOT NULL,
    subject_id bigint NOT NULL,
    action public.release_selection_action DEFAULT 'select'::public.release_selection_action NOT NULL,
    supersedes_selection_id bigint,
    rationale text NOT NULL,
    selected_by bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_release_selection_no_self_supersession CHECK (((supersedes_selection_id IS NULL) OR (supersedes_selection_id <> id))),
    CONSTRAINT ck_release_selection_rationale_nonblank CHECK ((length(btrim(rationale)) > 0)),
    CONSTRAINT ck_release_selection_selectable_record_type CHECK ((record_type = ANY (ARRAY['kinetics'::public.submission_record_type, 'network_solve'::public.submission_record_type, 'statmech'::public.submission_record_type, 'thermo'::public.submission_record_type, 'transition_state_entry'::public.submission_record_type, 'transport'::public.submission_record_type]))),
    CONSTRAINT ck_release_selection_supersession_matches_action CHECK ((((action = 'select'::public.release_selection_action) AND (supersedes_selection_id IS NULL)) OR ((action = ANY (ARRAY['supersede'::public.release_selection_action, 'withdraw'::public.release_selection_action])) AND (supersedes_selection_id IS NOT NULL))))
);
CREATE SEQUENCE public.release_selection_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.release_selection_id_seq OWNED BY public.release_selection.id;
CREATE TABLE public.scientific_record_supersession (
    id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    superseded_record_id bigint NOT NULL,
    superseding_record_id bigint NOT NULL,
    reason text NOT NULL,
    created_by bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_scientific_record_supersession_distinct_records CHECK ((superseded_record_id <> superseding_record_id)),
    CONSTRAINT ck_scientific_record_supersession_reason_nonblank CHECK ((length(btrim(reason)) > 0)),
    CONSTRAINT ck_scientific_record_supersession_supported_type CHECK ((record_type = ANY (ARRAY['calculation'::public.submission_record_type, 'thermo'::public.submission_record_type, 'statmech'::public.submission_record_type, 'kinetics'::public.submission_record_type, 'transport'::public.submission_record_type, 'network'::public.submission_record_type, 'network_solve'::public.submission_record_type, 'applied_energy_correction'::public.submission_record_type, 'transition_state_entry'::public.submission_record_type, 'conformer_observation'::public.submission_record_type])))
);
CREATE SEQUENCE public.scientific_record_supersession_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.scientific_record_supersession_id_seq OWNED BY public.scientific_record_supersession.id;
CREATE TABLE public.software (
    id bigint NOT NULL,
    name text NOT NULL,
    website text,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('soft_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE SEQUENCE public.software_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.software_id_seq OWNED BY public.software.id;
CREATE TABLE public.software_release (
    id bigint NOT NULL,
    software_id bigint NOT NULL,
    version text,
    revision text,
    build text,
    release_date date,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('srel_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE SEQUENCE public.software_release_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.software_release_id_seq OWNED BY public.software_release.id;
CREATE TABLE public.software_version_attestation (
    id bigint NOT NULL,
    software_release_id bigint NOT NULL,
    attested_version text NOT NULL,
    statement text NOT NULL,
    evidence_kind public.software_version_evidence_kind NOT NULL,
    attested_by bigint NOT NULL,
    covers_depositor bigint NOT NULL,
    attested_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_software_version_attestation_statement_nonblank CHECK ((length(btrim(statement)) > 0)),
    CONSTRAINT ck_software_version_attestation_version_nonblank CHECK ((length(btrim(attested_version)) > 0))
);
CREATE TABLE public.software_version_attestation_calculation (
    id bigint NOT NULL,
    attestation_id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    before_software_release_id bigint NOT NULL,
    after_software_release_id bigint NOT NULL,
    CONSTRAINT ck_software_version_attestation_calculation_release_changed CHECK ((before_software_release_id <> after_software_release_id))
);
CREATE SEQUENCE public.software_version_attestation_calculation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.software_version_attestation_calculation_id_seq OWNED BY public.software_version_attestation_calculation.id;
CREATE SEQUENCE public.software_version_attestation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.software_version_attestation_id_seq OWNED BY public.software_version_attestation.id;
CREATE TABLE public.species (
    id bigint NOT NULL,
    kind public.molecule_kind NOT NULL,
    smiles text NOT NULL,
    inchi_key character(27) NOT NULL,
    charge smallint NOT NULL,
    multiplicity smallint NOT NULL,
    stereo_kind public.stereo_kind NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('spc_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    merged_into_species_id bigint,
    CONSTRAINT ck_species_multiplicity_ge_1 CHECK ((multiplicity >= 1)),
    CONSTRAINT ck_species_not_merged_into_itself CHECK (((merged_into_species_id IS NULL) OR (merged_into_species_id <> id)))
);
CREATE TABLE public.species_entry (
    id bigint NOT NULL,
    species_id bigint NOT NULL,
    kind public.stationary_point_kind DEFAULT 'minimum'::public.stationary_point_kind NOT NULL,
    mol public.mol,
    unmapped_smiles text,
    stereo_label text,
    electronic_state_kind public.species_entry_state_kind DEFAULT 'ground'::public.species_entry_state_kind NOT NULL,
    electronic_state_label character varying(8),
    term_symbol_raw character varying(64),
    term_symbol character varying(64),
    isotopologue_label character varying(64),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('spe_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    isotope_key text,
    stereo_descriptor text,
    stereo_resolution public.stereo_resolution,
    stereo_smiles text,
    merged_into_species_entry_id bigint,
    CONSTRAINT ck_species_entry_not_merged_into_itself CHECK (((merged_into_species_entry_id IS NULL) OR (merged_into_species_entry_id <> id))),
    CONSTRAINT ck_species_entry_stereo_descriptor_vocabulary CHECK (((stereo_descriptor IS NULL) OR (stereo_descriptor ~ '^(Ra|Sa|[RSrsEZez?-])(,(Ra|Sa|[RSrsEZez?-]))*$'::text))),
    CONSTRAINT ck_species_entry_stereo_resolution_iff_descriptor CHECK (((stereo_descriptor IS NULL) = (stereo_resolution IS NULL)))
);
CREATE SEQUENCE public.species_entry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.species_entry_id_seq OWNED BY public.species_entry.id;
CREATE TABLE public.species_entry_review (
    id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    user_id bigint NOT NULL,
    role public.species_entry_review_role NOT NULL,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.species_entry_review_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.species_entry_review_id_seq OWNED BY public.species_entry_review.id;
CREATE SEQUENCE public.species_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.species_id_seq OWNED BY public.species.id;
CREATE TABLE public.species_name (
    id bigint NOT NULL,
    species_id bigint NOT NULL,
    name text NOT NULL,
    name_key text NOT NULL COLLATE pg_catalog."C",
    kind public.species_name_kind NOT NULL,
    source_kind public.species_name_source_kind NOT NULL,
    external_source_id bigint,
    created_by_user_id bigint,
    retired_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) NOT NULL,
    external_record_key text,
    retrieved_at timestamp without time zone,
    CONSTRAINT ck_species_name_curated_has_no_record CHECK (((source_kind <> 'curated'::public.species_name_source_kind) OR ((external_record_key IS NULL) AND (retrieved_at IS NULL)))),
    CONSTRAINT ck_species_name_name_key_length CHECK (((char_length(name_key) >= 1) AND (char_length(name_key) <= 128))),
    CONSTRAINT ck_species_name_name_length CHECK (((char_length(name) >= 1) AND (char_length(name) <= 128))),
    CONSTRAINT ck_species_name_offline_import_has_source CHECK (((source_kind <> 'offline_import'::public.species_name_source_kind) OR ((external_source_id IS NOT NULL) AND (external_record_key IS NOT NULL) AND (retrieved_at IS NOT NULL))))
);
CREATE SEQUENCE public.species_name_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.species_name_id_seq OWNED BY public.species_name.id;
CREATE TABLE public.statmech (
    id bigint NOT NULL,
    species_entry_id bigint,
    scientific_origin public.scientific_origin_kind NOT NULL,
    literature_id bigint,
    workflow_tool_release_id bigint,
    software_release_id bigint,
    external_symmetry smallint,
    point_group text,
    is_linear boolean,
    rigid_rotor_kind public.rigid_rotor_kind,
    statmech_treatment public.statmech_treatment_kind,
    frequency_scale_factor_id bigint,
    uses_projected_frequencies boolean,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('sm_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    optical_isomers smallint,
    rotational_constant_a_cm1 double precision,
    rotational_constant_b_cm1 double precision,
    rotational_constant_c_cm1 double precision,
    transition_state_entry_id bigint,
    energy_level_of_theory_id bigint,
    CONSTRAINT ck_statmech_external_symmetry_ge_1 CHECK (((external_symmetry IS NULL) OR (external_symmetry >= 1))),
    CONSTRAINT ck_statmech_optical_isomers_ge_1 CHECK (((optical_isomers IS NULL) OR (optical_isomers >= 1))),
    CONSTRAINT ck_statmech_rotational_constant_a_cm1_positive CHECK (((rotational_constant_a_cm1 IS NULL) OR (rotational_constant_a_cm1 > (0)::double precision))),
    CONSTRAINT ck_statmech_rotational_constant_b_cm1_positive CHECK (((rotational_constant_b_cm1 IS NULL) OR (rotational_constant_b_cm1 > (0)::double precision))),
    CONSTRAINT ck_statmech_rotational_constant_c_cm1_positive CHECK (((rotational_constant_c_cm1 IS NULL) OR (rotational_constant_c_cm1 > (0)::double precision))),
    CONSTRAINT ck_statmech_statmech_exactly_one_subject CHECK (((species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)))
);
CREATE TABLE public.statmech_electronic_level (
    id bigint NOT NULL,
    statmech_id bigint NOT NULL,
    level_index integer NOT NULL,
    energy_cm1 double precision NOT NULL,
    degeneracy integer NOT NULL,
    CONSTRAINT ck_statmech_electronic_level_degeneracy_ge_1 CHECK ((degeneracy >= 1)),
    CONSTRAINT ck_statmech_electronic_level_energy_cm1_ge_0 CHECK ((energy_cm1 >= (0)::double precision)),
    CONSTRAINT ck_statmech_electronic_level_level_index_ge_1 CHECK ((level_index >= 1))
);
CREATE SEQUENCE public.statmech_electronic_level_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.statmech_electronic_level_id_seq OWNED BY public.statmech_electronic_level.id;
CREATE SEQUENCE public.statmech_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.statmech_id_seq OWNED BY public.statmech.id;
CREATE TABLE public.statmech_model_declaration (
    id bigint NOT NULL,
    statmech_id bigint NOT NULL,
    schema_version smallint NOT NULL,
    scope_kind public.statmech_model_scope_kind NOT NULL,
    conformer_group_id bigint,
    structure_determination_id bigint,
    literature_id bigint,
    workflow_tool_release_id bigint,
    recipe jsonb NOT NULL,
    content_hash character varying(64) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_statmech_model_declaration_attribution_required CHECK (((literature_id IS NOT NULL) OR (workflow_tool_release_id IS NOT NULL))),
    CONSTRAINT ck_statmech_model_declaration_content_hash_sha256_hex CHECK (((content_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_statmech_model_declaration_recipe_object CHECK ((jsonb_typeof(recipe) = 'object'::text)),
    CONSTRAINT ck_statmech_model_declaration_schema_version_positive CHECK ((schema_version >= 1)),
    CONSTRAINT ck_statmech_model_declaration_scope_matches_pointers CHECK ((((scope_kind = 'basin'::public.statmech_model_scope_kind) = (conformer_group_id IS NOT NULL)) AND ((scope_kind = 'ts_determination'::public.statmech_model_scope_kind) = (structure_determination_id IS NOT NULL))))
);
CREATE SEQUENCE public.statmech_model_declaration_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.statmech_model_declaration_id_seq OWNED BY public.statmech_model_declaration.id;
CREATE TABLE public.statmech_model_member (
    id bigint NOT NULL,
    declaration_id bigint NOT NULL,
    member_key text NOT NULL,
    member_statmech_id bigint NOT NULL,
    conformer_group_id bigint,
    counting_multiplicity smallint,
    relative_energy_kj_mol double precision,
    region_key text,
    relative_energy_convention public.energy_correction_convention,
    CONSTRAINT ck_statmech_model_member_counting_multiplicity_ge_1 CHECK (((counting_multiplicity IS NULL) OR (counting_multiplicity >= 1))),
    CONSTRAINT ck_statmech_model_member_member_key_bounded CHECK (((length(btrim(member_key)) > 0) AND (length(member_key) <= 64))),
    CONSTRAINT ck_statmech_model_member_relative_energy_convention_not_other CHECK ((relative_energy_convention IS DISTINCT FROM 'other'::public.energy_correction_convention)),
    CONSTRAINT ck_statmech_model_member_relative_energy_convention_with_energy CHECK (((relative_energy_kj_mol IS NULL) = (relative_energy_convention IS NULL))),
    CONSTRAINT ck_statmech_model_member_relative_energy_finite CHECK (((relative_energy_kj_mol IS NULL) OR ((relative_energy_kj_mol <> 'NaN'::double precision) AND (abs(relative_energy_kj_mol) < 'Infinity'::double precision))))
);
CREATE SEQUENCE public.statmech_model_member_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.statmech_model_member_id_seq OWNED BY public.statmech_model_member.id;
CREATE TABLE public.statmech_model_source (
    id bigint NOT NULL,
    declaration_id bigint NOT NULL,
    source_key text NOT NULL,
    role public.statmech_model_source_role NOT NULL,
    calculation_id bigint,
    geometry_id bigint,
    literature_id bigint,
    frequency_scale_factor_id bigint,
    member_key text,
    CONSTRAINT ck_statmech_model_source_exactly_one_locator CHECK ((num_nonnulls(calculation_id, literature_id, frequency_scale_factor_id) = 1)),
    CONSTRAINT ck_statmech_model_source_geometry_needs_calculation CHECK (((geometry_id IS NULL) OR (calculation_id IS NOT NULL))),
    CONSTRAINT ck_statmech_model_source_scale_factor_role_matches_locator CHECK (((role = 'scale_factor'::public.statmech_model_source_role) = (frequency_scale_factor_id IS NOT NULL))),
    CONSTRAINT ck_statmech_model_source_source_key_bounded CHECK (((length(btrim(source_key)) > 0) AND (length(source_key) <= 64)))
);
CREATE SEQUENCE public.statmech_model_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.statmech_model_source_id_seq OWNED BY public.statmech_model_source.id;
CREATE TABLE public.statmech_source_calculation (
    statmech_id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    role public.statmech_calc_role NOT NULL
);
CREATE TABLE public.statmech_torsion (
    id bigint NOT NULL,
    statmech_id bigint NOT NULL,
    torsion_index integer NOT NULL,
    symmetry_number smallint,
    treatment_kind public.torsion_treatment_kind,
    dimension integer NOT NULL,
    top_description text,
    invalidated_reason text,
    note text,
    source_scan_calculation_id bigint,
    CONSTRAINT ck_statmech_torsion_dimension_ge_1 CHECK ((dimension >= 1)),
    CONSTRAINT ck_statmech_torsion_symmetry_number_ge_1 CHECK (((symmetry_number IS NULL) OR (symmetry_number >= 1))),
    CONSTRAINT ck_statmech_torsion_torsion_index_ge_1 CHECK ((torsion_index >= 1))
);
CREATE TABLE public.statmech_torsion_definition (
    torsion_id bigint NOT NULL,
    coordinate_index integer NOT NULL,
    atom1_index integer NOT NULL,
    atom2_index integer NOT NULL,
    atom3_index integer NOT NULL,
    atom4_index integer NOT NULL,
    CONSTRAINT ck_statmech_torsion_definition_atom1_index_ge_1 CHECK ((atom1_index >= 1)),
    CONSTRAINT ck_statmech_torsion_definition_atom2_index_ge_1 CHECK ((atom2_index >= 1)),
    CONSTRAINT ck_statmech_torsion_definition_atom3_index_ge_1 CHECK ((atom3_index >= 1)),
    CONSTRAINT ck_statmech_torsion_definition_atom4_index_ge_1 CHECK ((atom4_index >= 1)),
    CONSTRAINT ck_statmech_torsion_definition_coordinate_index_ge_1 CHECK ((coordinate_index >= 1))
);
CREATE SEQUENCE public.statmech_torsion_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.statmech_torsion_id_seq OWNED BY public.statmech_torsion.id;
CREATE TABLE public.structure_determination (
    id bigint NOT NULL,
    species_entry_id bigint,
    transition_state_entry_id bigint,
    conformer_observation_id bigint,
    target_kind public.structure_determination_target_kind NOT NULL,
    quantity public.structure_determination_quantity,
    evaluated_geometry_id bigint NOT NULL,
    literature_id bigint,
    workflow_tool_release_id bigint,
    determination_key text NOT NULL,
    energy_convention jsonb,
    actual_recipe jsonb,
    identity_hash character varying(64) NOT NULL,
    content_hash character varying(64) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_structure_determination_actual_recipe_versioned_object CHECK (((actual_recipe IS NULL) OR ((jsonb_typeof(actual_recipe) = 'object'::text) AND (COALESCE(jsonb_typeof((actual_recipe -> 'version'::text)), ''::text) = 'number'::text)))),
    CONSTRAINT ck_structure_determination_content_hash_sha256_hex CHECK (((content_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_structure_determination_convention_iff_zero_kelvin CHECK ((COALESCE((quantity = 'zero_kelvin_energy'::public.structure_determination_quantity), false) = (energy_convention IS NOT NULL))),
    CONSTRAINT ck_structure_determination_energy_convention_object CHECK (((energy_convention IS NULL) OR (jsonb_typeof(energy_convention) = 'object'::text))),
    CONSTRAINT ck_structure_determination_identity_hash_sha256_hex CHECK (((identity_hash)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_structure_determination_key_bounded CHECK (((length(btrim(determination_key)) > 0) AND (length(determination_key) <= 128))),
    CONSTRAINT ck_structure_determination_one_owner CHECK ((num_nonnulls(species_entry_id, transition_state_entry_id) = 1)),
    CONSTRAINT ck_structure_determination_source_required CHECK (((literature_id IS NOT NULL) OR (workflow_tool_release_id IS NOT NULL))),
    CONSTRAINT ck_structure_determination_target_matches_owner CHECK ((((target_kind = 'conformer_basin'::public.structure_determination_target_kind) AND (species_entry_id IS NOT NULL) AND (conformer_observation_id IS NOT NULL)) OR ((target_kind = 'saddle_point'::public.structure_determination_target_kind) AND (transition_state_entry_id IS NOT NULL) AND (conformer_observation_id IS NULL)) OR ((target_kind = 'geometry'::public.structure_determination_target_kind) AND (conformer_observation_id IS NULL))))
);
CREATE SEQUENCE public.structure_determination_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.structure_determination_id_seq OWNED BY public.structure_determination.id;
CREATE TABLE public.structure_determination_source (
    id bigint NOT NULL,
    determination_id bigint NOT NULL,
    role public.structure_source_role NOT NULL,
    calculation_id bigint NOT NULL,
    species_entry_id bigint,
    transition_state_entry_id bigint,
    conformer_observation_id bigint,
    geometry_id bigint,
    CONSTRAINT ck_structure_determination_source_one_owner CHECK ((num_nonnulls(species_entry_id, transition_state_entry_id) = 1))
);
CREATE SEQUENCE public.structure_determination_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.structure_determination_source_id_seq OWNED BY public.structure_determination_source.id;
CREATE TABLE public.structure_evidence_finding (
    id bigint NOT NULL,
    kind public.structure_finding_kind NOT NULL,
    scope public.structure_finding_scope NOT NULL,
    subject_geometry_id bigint,
    subject_calculation_id bigint,
    subject_determination_id bigint,
    role public.structure_source_role,
    verdict public.structure_finding_verdict NOT NULL,
    authority public.structure_finding_authority NOT NULL,
    rationale text NOT NULL,
    semantic_version smallint NOT NULL,
    source_calculation_id bigint,
    literature_id bigint,
    supersedes_finding_id bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_structure_evidence_finding_adjudication_needs_authority CHECK (((kind <> 'adjudication'::public.structure_finding_kind) OR ((supersedes_finding_id IS NOT NULL) AND (authority = 'authorized_adjudication'::public.structure_finding_authority)))),
    CONSTRAINT ck_structure_evidence_finding_only_adjudication_supersedes CHECK (((supersedes_finding_id IS NULL) OR (kind = 'adjudication'::public.structure_finding_kind))),
    CONSTRAINT ck_structure_evidence_finding_rationale_bounded CHECK (((length(btrim(rationale)) > 0) AND (length(rationale) <= 2000))),
    CONSTRAINT ck_structure_evidence_finding_role_iff_role_invalidation CHECK (((kind = 'role_invalidation'::public.structure_finding_kind) = (role IS NOT NULL))),
    CONSTRAINT ck_structure_evidence_finding_semantic_version_positive CHECK ((semantic_version >= 1)),
    CONSTRAINT ck_structure_evidence_finding_subject_matches_scope CHECK ((((scope = 'geometry'::public.structure_finding_scope) AND (subject_geometry_id IS NOT NULL) AND (subject_calculation_id IS NULL) AND (subject_determination_id IS NULL)) OR ((scope = 'calculation'::public.structure_finding_scope) AND (subject_calculation_id IS NOT NULL) AND (subject_geometry_id IS NULL) AND (subject_determination_id IS NULL)) OR ((scope = 'determination'::public.structure_finding_scope) AND (subject_determination_id IS NOT NULL) AND (subject_geometry_id IS NULL) AND (subject_calculation_id IS NULL))))
);
CREATE SEQUENCE public.structure_evidence_finding_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.structure_evidence_finding_id_seq OWNED BY public.structure_evidence_finding.id;
CREATE TABLE public.submission (
    id bigint NOT NULL,
    created_by bigint NOT NULL,
    submission_kind public.submission_kind NOT NULL,
    source_kind public.submission_source_kind DEFAULT 'api'::public.submission_source_kind NOT NULL,
    upload_job_id uuid,
    status public.submission_status DEFAULT 'pending'::public.submission_status NOT NULL,
    title character varying(200),
    summary text,
    submitted_at timestamp without time zone DEFAULT now() NOT NULL,
    approved_at timestamp without time zone,
    approved_by bigint,
    rejected_at timestamp without time zone,
    rejected_by bigint,
    rejection_reason text,
    correction_due_at timestamp without time zone,
    supersedes_submission_id bigint,
    llm_precheck_label public.submission_precheck_label,
    llm_precheck_summary text,
    llm_precheck_model character varying(128),
    llm_precheck_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('sub_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    CONSTRAINT ck_submission_submission_approver_not_creator CHECK (((status <> 'approved'::public.submission_status) OR ((approved_by IS NOT NULL) AND (approved_by <> created_by)))),
    CONSTRAINT ck_submission_submission_rejected_requires_reason CHECK (((status <> 'rejected'::public.submission_status) OR (rejection_reason IS NOT NULL))),
    CONSTRAINT ck_submission_submission_rejecter_not_creator CHECK (((status <> 'rejected'::public.submission_status) OR ((rejected_by IS NOT NULL) AND (rejected_by <> created_by))))
);
CREATE TABLE public.submission_audit_event (
    id bigint NOT NULL,
    submission_id bigint NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    actor_user_id bigint,
    actor_kind public.submission_actor_kind NOT NULL,
    event_kind public.submission_audit_event_kind NOT NULL,
    from_status public.submission_status,
    to_status public.submission_status,
    reason text,
    summary text,
    details_json jsonb,
    related_submission_id bigint
);
CREATE SEQUENCE public.submission_audit_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.submission_audit_event_id_seq OWNED BY public.submission_audit_event.id;
CREATE SEQUENCE public.submission_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.submission_id_seq OWNED BY public.submission.id;
CREATE TABLE public.submission_record_link (
    id bigint NOT NULL,
    submission_id bigint NOT NULL,
    record_type public.submission_record_type NOT NULL,
    record_id bigint NOT NULL,
    role character varying(64),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.submission_record_link_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.submission_record_link_id_seq OWNED BY public.submission_record_link.id;
CREATE TABLE public.submission_rights_attestation (
    id bigint NOT NULL,
    submission_id bigint NOT NULL,
    license_id character varying(64) NOT NULL,
    basis public.rights_basis_kind NOT NULL,
    attested_by bigint NOT NULL,
    actor_kind public.submission_actor_kind NOT NULL,
    attested_at timestamp without time zone DEFAULT now() NOT NULL,
    source_terms text,
    note text,
    supersedes_attestation_id bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) NOT NULL,
    CONSTRAINT ck_submission_rights_attestation_license_id_nonblank CHECK ((length(btrim((license_id)::text)) > 0)),
    CONSTRAINT ck_submission_rights_attestation_source_terms_required CHECK (((basis <> 'source_terms'::public.rights_basis_kind) OR ((source_terms IS NOT NULL) AND (length(btrim(source_terms)) > 0))))
);
CREATE SEQUENCE public.submission_rights_attestation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.submission_rights_attestation_id_seq OWNED BY public.submission_rights_attestation.id;
CREATE TABLE public.thermo (
    id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    scientific_origin public.scientific_origin_kind NOT NULL,
    literature_id bigint,
    workflow_tool_release_id bigint,
    software_release_id bigint,
    h298_kj_mol double precision,
    s298_j_mol_k double precision,
    h298_uncertainty_kj_mol double precision,
    s298_uncertainty_j_mol_k double precision,
    tmin_k double precision,
    tmax_k double precision,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('thm_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    enthalpy_formation_0k_kj_mol double precision,
    enthalpy_formation_0k_uncertainty_kj_mol double precision,
    reference_pressure_bar double precision,
    phase public.phase_kind,
    statmech_id bigint,
    model_kind public.thermo_model_kind,
    enthalpy_reference_kind public.enthalpy_reference_kind,
    energy_level_of_theory_id bigint,
    thermodynamic_target_kind public.thermo_target_kind,
    target_conformer_group_id bigint,
    protocol_declaration jsonb,
    thermodynamic_target_declared_by public.thermo_declaration_source DEFAULT 'depositor'::public.thermo_declaration_source NOT NULL,
    CONSTRAINT ck_thermo_enthalpy_formation_0k_uncertainty_ge_0 CHECK (((enthalpy_formation_0k_uncertainty_kj_mol IS NULL) OR (enthalpy_formation_0k_uncertainty_kj_mol >= (0)::double precision))),
    CONSTRAINT ck_thermo_h298_uncertainty_ge_0 CHECK (((h298_uncertainty_kj_mol IS NULL) OR (h298_uncertainty_kj_mol >= (0)::double precision))),
    CONSTRAINT ck_thermo_protocol_declaration_versioned_object CHECK (((protocol_declaration IS NULL) OR ((jsonb_typeof(protocol_declaration) = 'object'::text) AND (COALESCE(jsonb_typeof((protocol_declaration -> 'version'::text)), ''::text) = 'number'::text)))),
    CONSTRAINT ck_thermo_s298_uncertainty_ge_0 CHECK (((s298_uncertainty_j_mol_k IS NULL) OR (s298_uncertainty_j_mol_k >= (0)::double precision))),
    CONSTRAINT ck_thermo_target_group_iff_single_conformer CHECK (((NOT (thermodynamic_target_kind IS DISTINCT FROM 'single_conformer'::public.thermo_target_kind)) = (target_conformer_group_id IS NOT NULL))),
    CONSTRAINT ck_thermo_tmax_k_gt_0 CHECK (((tmax_k IS NULL) OR (tmax_k > (0)::double precision))),
    CONSTRAINT ck_thermo_tmin_k_gt_0 CHECK (((tmin_k IS NULL) OR (tmin_k > (0)::double precision))),
    CONSTRAINT ck_thermo_tmin_le_tmax CHECK (((tmin_k IS NULL) OR (tmax_k IS NULL) OR (tmin_k <= tmax_k)))
);
CREATE SEQUENCE public.thermo_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.thermo_id_seq OWNED BY public.thermo.id;
CREATE TABLE public.thermo_nasa (
    thermo_id bigint NOT NULL,
    t_low double precision,
    t_mid double precision,
    t_high double precision,
    a1 double precision,
    a2 double precision,
    a3 double precision,
    a4 double precision,
    a5 double precision,
    a6 double precision,
    a7 double precision,
    b1 double precision,
    b2 double precision,
    b3 double precision,
    b4 double precision,
    b5 double precision,
    b6 double precision,
    b7 double precision,
    CONSTRAINT ck_thermo_nasa_t_high_gt_t_mid CHECK (((t_mid IS NULL) OR (t_high IS NULL) OR (t_high > t_mid))),
    CONSTRAINT ck_thermo_nasa_t_low_gt_0 CHECK (((t_low IS NULL) OR (t_low > (0)::double precision))),
    CONSTRAINT ck_thermo_nasa_t_mid_gt_t_low CHECK (((t_low IS NULL) OR (t_mid IS NULL) OR (t_mid > t_low))),
    CONSTRAINT ck_thermo_nasa_temperature_bounds_all_or_none CHECK ((((t_low IS NULL) AND (t_mid IS NULL) AND (t_high IS NULL)) OR ((t_low IS NOT NULL) AND (t_mid IS NOT NULL) AND (t_high IS NOT NULL))))
);
CREATE TABLE public.thermo_nasa9_interval (
    id bigint NOT NULL,
    thermo_id bigint NOT NULL,
    interval_index integer NOT NULL,
    t_min_k double precision NOT NULL,
    t_max_k double precision NOT NULL,
    a1 double precision NOT NULL,
    a2 double precision NOT NULL,
    a3 double precision NOT NULL,
    a4 double precision NOT NULL,
    a5 double precision NOT NULL,
    a6 double precision NOT NULL,
    a7 double precision NOT NULL,
    a8 double precision NOT NULL,
    a9 double precision NOT NULL,
    CONSTRAINT ck_thermo_nasa9_interval_interval_index_ge_1 CHECK ((interval_index >= 1)),
    CONSTRAINT ck_thermo_nasa9_interval_t_max_k_gt_t_min_k CHECK ((t_max_k > t_min_k)),
    CONSTRAINT ck_thermo_nasa9_interval_t_min_k_gt_0 CHECK ((t_min_k > (0)::double precision))
);
CREATE SEQUENCE public.thermo_nasa9_interval_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.thermo_nasa9_interval_id_seq OWNED BY public.thermo_nasa9_interval.id;
CREATE TABLE public.thermo_point (
    thermo_id bigint NOT NULL,
    temperature_k double precision NOT NULL,
    cp_j_mol_k double precision,
    h_kj_mol double precision,
    s_j_mol_k double precision,
    g_kj_mol double precision
);
CREATE TABLE public.thermo_source_calculation (
    thermo_id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    role public.thermo_calc_role NOT NULL
);
CREATE TABLE public.thermo_wilhoit (
    thermo_id bigint NOT NULL,
    cp0_j_mol_k double precision NOT NULL,
    cp_inf_j_mol_k double precision NOT NULL,
    b_k double precision NOT NULL,
    a0 double precision NOT NULL,
    a1 double precision NOT NULL,
    a2 double precision NOT NULL,
    a3 double precision NOT NULL,
    h0_kj_mol double precision,
    s0_j_mol_k double precision,
    CONSTRAINT ck_thermo_wilhoit_b_k_gt_0 CHECK ((b_k > (0)::double precision)),
    CONSTRAINT ck_thermo_wilhoit_cp0_ge_0 CHECK ((cp0_j_mol_k >= (0)::double precision)),
    CONSTRAINT ck_thermo_wilhoit_cp_inf_ge_0 CHECK ((cp_inf_j_mol_k >= (0)::double precision))
);
CREATE TABLE public.transition_state (
    id bigint NOT NULL,
    reaction_entry_id bigint NOT NULL,
    label text,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('ts_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE TABLE public.transition_state_entry (
    id bigint NOT NULL,
    transition_state_id bigint NOT NULL,
    charge smallint NOT NULL,
    multiplicity smallint NOT NULL,
    mol public.mol,
    unmapped_smiles text,
    status public.transition_state_entry_status DEFAULT 'optimized'::public.transition_state_entry_status NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('tse_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    CONSTRAINT ck_transition_state_entry_multiplicity_ge_1 CHECK ((multiplicity >= 1))
);
CREATE SEQUENCE public.transition_state_entry_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.transition_state_entry_id_seq OWNED BY public.transition_state_entry.id;
CREATE SEQUENCE public.transition_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.transition_state_id_seq OWNED BY public.transition_state.id;
CREATE TABLE public.transition_state_selection (
    id bigint NOT NULL,
    transition_state_id bigint NOT NULL,
    selection_kind public.transition_state_selection_kind NOT NULL,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint
);
CREATE SEQUENCE public.transition_state_selection_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.transition_state_selection_id_seq OWNED BY public.transition_state_selection.id;
CREATE TABLE public.transition_state_validation_energy (
    id bigint NOT NULL,
    evidence_id bigint NOT NULL,
    participant text NOT NULL,
    energy_kind text NOT NULL,
    energy_hartree double precision NOT NULL,
    source_calculation_id bigint NOT NULL,
    stored_energy_comparison text,
    not_compared_reason text,
    zpe_scale_factor double precision,
    CONSTRAINT ck_transition_state_validation_energy_energy_finite_le_zero CHECK (((energy_hartree <= (0)::double precision) AND (energy_hartree > '-Infinity'::double precision))),
    CONSTRAINT ck_transition_state_validation_energy_energy_kind CHECK ((energy_kind = ANY (ARRAY['electronic'::text, 'e0'::text]))),
    CONSTRAINT ck_transition_state_validation_energy_not_compared_reason_shape CHECK (((NOT (stored_energy_comparison IS DISTINCT FROM 'not_compared'::text)) = (not_compared_reason IS NOT NULL))),
    CONSTRAINT ck_transition_state_validation_energy_not_compared_reason_token CHECK (((not_compared_reason IS NULL) OR (not_compared_reason = ANY (ARRAY['stored_energy_not_stated'::text, 'zpe_not_stated'::text, 'no_electronic_energy_to_pair'::text, 'geometry_not_paired'::text, 'zpe_scaling_unstated'::text])))),
    CONSTRAINT ck_transition_state_validation_energy_participant_shape CHECK ((participant ~ '^(ts|reactant:[1-9][0-9]*|product:[1-9][0-9]*)$'::text)),
    CONSTRAINT ck_transition_state_validation_energy_stored_energy_comparison CHECK (((stored_energy_comparison IS NULL) OR (stored_energy_comparison = ANY (ARRAY['agrees'::text, 'not_compared'::text])))),
    CONSTRAINT ck_transition_state_validation_energy_zpe_scale_factor_e0 CHECK (((zpe_scale_factor IS NULL) OR ((energy_kind = 'e0'::text) AND (zpe_scale_factor > (0)::double precision) AND (zpe_scale_factor < 'Infinity'::double precision))))
);
CREATE SEQUENCE public.transition_state_validation_energy_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.transition_state_validation_energy_id_seq OWNED BY public.transition_state_validation_energy.id;
CREATE TABLE public.transition_state_validation_evidence (
    id bigint NOT NULL,
    transition_state_entry_id bigint NOT NULL,
    kind text NOT NULL,
    passed boolean NOT NULL,
    rationale text NOT NULL,
    reconstruction_calculation_id bigint,
    reactant_participant_mapping jsonb,
    product_participant_mapping jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    transition_state_geometry_id bigint,
    imaginary_frequency_count integer,
    imaginary_frequency_cm1 double precision,
    mode_displacement_agrees boolean,
    CONSTRAINT ck_transition_state_validation_evidence_imag_count_ge_0 CHECK (((imaginary_frequency_count IS NULL) OR (imaginary_frequency_count >= 0))),
    CONSTRAINT ck_transition_state_validation_evidence_imag_freq_negative CHECK (((imaginary_frequency_cm1 IS NULL) OR ((imaginary_frequency_cm1 < (0)::double precision) AND (imaginary_frequency_cm1 > '-Infinity'::double precision)))),
    CONSTRAINT ck_transition_state_validation_evidence_mapping_irc_only CHECK (((kind = 'irc'::text) OR ((COALESCE(jsonb_typeof(reactant_participant_mapping), 'null'::text) = 'null'::text) AND (COALESCE(jsonb_typeof(product_participant_mapping), 'null'::text) = 'null'::text)))),
    CONSTRAINT ck_transition_state_validation_evidence_mapping_names_geometry CHECK ((((COALESCE(jsonb_typeof(reactant_participant_mapping), 'null'::text) = 'null'::text) AND (COALESCE(jsonb_typeof(product_participant_mapping), 'null'::text) = 'null'::text)) OR (transition_state_geometry_id IS NOT NULL))),
    CONSTRAINT ck_transition_state_validation_evidence_mode_cols_imag_only CHECK (((kind = 'imaginary_mode'::text) OR ((imaginary_frequency_count IS NULL) AND (imaginary_frequency_cm1 IS NULL) AND (mode_displacement_agrees IS NULL)))),
    CONSTRAINT ck_transition_state_validation_evidence_source_calc_shape CHECK (((kind = 'energy_ordering'::text) = (reconstruction_calculation_id IS NULL))),
    CONSTRAINT ck_transition_state_validation_evidence_ts_validation_kind CHECK ((kind = ANY (ARRAY['irc'::text, 'energy_ordering'::text, 'imaginary_mode'::text])))
);
CREATE SEQUENCE public.transition_state_validation_evidence_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.transition_state_validation_evidence_id_seq OWNED BY public.transition_state_validation_evidence.id;
CREATE TABLE public.transport (
    id bigint NOT NULL,
    species_entry_id bigint NOT NULL,
    scientific_origin public.scientific_origin_kind NOT NULL,
    literature_id bigint,
    software_release_id bigint,
    workflow_tool_release_id bigint,
    sigma_angstrom double precision,
    epsilon_over_k_k double precision,
    dipole_debye double precision,
    polarizability_angstrom3 double precision,
    rotational_relaxation double precision,
    note text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    created_by bigint,
    public_ref character varying(40) DEFAULT ('trn_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL,
    CONSTRAINT ck_transport_epsilon_over_k_k_gt_0 CHECK (((epsilon_over_k_k IS NULL) OR (epsilon_over_k_k > (0)::double precision))),
    CONSTRAINT ck_transport_lj_pair_both_or_neither CHECK ((((sigma_angstrom IS NULL) AND (epsilon_over_k_k IS NULL)) OR ((sigma_angstrom IS NOT NULL) AND (epsilon_over_k_k IS NOT NULL)))),
    CONSTRAINT ck_transport_rotational_relaxation_ge_0 CHECK (((rotational_relaxation IS NULL) OR (rotational_relaxation >= (0)::double precision))),
    CONSTRAINT ck_transport_sigma_angstrom_gt_0 CHECK (((sigma_angstrom IS NULL) OR (sigma_angstrom > (0)::double precision)))
);
CREATE SEQUENCE public.transport_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.transport_id_seq OWNED BY public.transport.id;
CREATE TABLE public.transport_source_calculation (
    transport_id bigint NOT NULL,
    calculation_id bigint NOT NULL,
    role public.transport_calc_role NOT NULL
);
CREATE TABLE public.upload_job (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    status public.upload_job_status DEFAULT 'queued'::public.upload_job_status NOT NULL,
    kind public.upload_job_kind NOT NULL,
    payload jsonb NOT NULL,
    created_by bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    started_at timestamp without time zone,
    completed_at timestamp without time zone,
    result jsonb,
    error text,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    lease_expires_at timestamp without time zone,
    heartbeat_at timestamp without time zone,
    request_warnings jsonb
);
CREATE TABLE public.user_session (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    token_hash character(64) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    revoked_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);
CREATE SEQUENCE public.user_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.user_session_id_seq OWNED BY public.user_session.id;
CREATE TABLE public.workflow_tool (
    id bigint NOT NULL,
    name text NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('wft_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE SEQUENCE public.workflow_tool_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.workflow_tool_id_seq OWNED BY public.workflow_tool.id;
CREATE TABLE public.workflow_tool_release (
    id bigint NOT NULL,
    workflow_tool_id bigint NOT NULL,
    version text,
    git_commit character(40),
    release_date date,
    notes text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    public_ref character varying(40) DEFAULT ('wfr_'::text || "substring"(replace((gen_random_uuid())::text, '-'::text, ''::text), 1, 26)) NOT NULL
);
CREATE SEQUENCE public.workflow_tool_release_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.workflow_tool_release_id_seq OWNED BY public.workflow_tool_release.id;
ALTER TABLE ONLY public.accepted_science_repair ALTER COLUMN id SET DEFAULT nextval('public.accepted_science_repair_id_seq'::regclass);
ALTER TABLE ONLY public.accepted_science_repair_change ALTER COLUMN id SET DEFAULT nextval('public.accepted_science_repair_change_id_seq'::regclass);
ALTER TABLE ONLY public.api_key ALTER COLUMN id SET DEFAULT nextval('public.api_key_id_seq'::regclass);
ALTER TABLE ONLY public.app_user ALTER COLUMN id SET DEFAULT nextval('public.app_user_id_seq'::regclass);
ALTER TABLE ONLY public.applied_energy_correction ALTER COLUMN id SET DEFAULT nextval('public.applied_energy_correction_id_seq'::regclass);
ALTER TABLE ONLY public.applied_energy_correction_component ALTER COLUMN id SET DEFAULT nextval('public.applied_energy_correction_component_id_seq'::regclass);
ALTER TABLE ONLY public.applied_group_additivity ALTER COLUMN id SET DEFAULT nextval('public.applied_group_additivity_id_seq'::regclass);
ALTER TABLE ONLY public.applied_group_additivity_component ALTER COLUMN id SET DEFAULT nextval('public.applied_group_additivity_component_id_seq'::regclass);
ALTER TABLE ONLY public.artifact_integrity_event ALTER COLUMN id SET DEFAULT nextval('public.artifact_integrity_event_id_seq'::regclass);
ALTER TABLE ONLY public.artifact_storage_capacity_event ALTER COLUMN id SET DEFAULT nextval('public.artifact_storage_capacity_event_id_seq'::regclass);
ALTER TABLE ONLY public.author ALTER COLUMN id SET DEFAULT nextval('public.author_id_seq'::regclass);
ALTER TABLE ONLY public.calculation ALTER COLUMN id SET DEFAULT nextval('public.calculation_id_seq'::regclass);
ALTER TABLE ONLY public.calculation_artifact ALTER COLUMN id SET DEFAULT nextval('public.calculation_artifact_id_seq'::regclass);
ALTER TABLE ONLY public.calculation_parameter ALTER COLUMN id SET DEFAULT nextval('public.calculation_parameter_id_seq'::regclass);
ALTER TABLE ONLY public.chem_reaction ALTER COLUMN id SET DEFAULT nextval('public.chem_reaction_id_seq'::regclass);
ALTER TABLE ONLY public.composite_scheme ALTER COLUMN id SET DEFAULT nextval('public.composite_scheme_id_seq'::regclass);
ALTER TABLE ONLY public.composite_scheme_term ALTER COLUMN id SET DEFAULT nextval('public.composite_scheme_term_id_seq'::regclass);
ALTER TABLE ONLY public.composite_scheme_term_input ALTER COLUMN id SET DEFAULT nextval('public.composite_scheme_term_input_id_seq'::regclass);
ALTER TABLE ONLY public.conformer_assignment_scheme ALTER COLUMN id SET DEFAULT nextval('public.conformer_assignment_scheme_id_seq'::regclass);
ALTER TABLE ONLY public.conformer_group ALTER COLUMN id SET DEFAULT nextval('public.conformer_group_id_seq'::regclass);
ALTER TABLE ONLY public.conformer_group_merge_pending ALTER COLUMN id SET DEFAULT nextval('public.conformer_group_merge_pending_id_seq'::regclass);
ALTER TABLE ONLY public.conformer_observation ALTER COLUMN id SET DEFAULT nextval('public.conformer_observation_id_seq'::regclass);
ALTER TABLE ONLY public.conformer_selection ALTER COLUMN id SET DEFAULT nextval('public.conformer_selection_id_seq'::regclass);
ALTER TABLE ONLY public.curation_policy ALTER COLUMN id SET DEFAULT nextval('public.curation_policy_id_seq'::regclass);
ALTER TABLE ONLY public.dataset_release ALTER COLUMN id SET DEFAULT nextval('public.dataset_release_id_seq'::regclass);
ALTER TABLE ONLY public.deposit_warning ALTER COLUMN id SET DEFAULT nextval('public.deposit_warning_id_seq'::regclass);
ALTER TABLE ONLY public.deposit_warning_retirement ALTER COLUMN id SET DEFAULT nextval('public.deposit_warning_retirement_id_seq'::regclass);
ALTER TABLE ONLY public.energy_correction_scheme ALTER COLUMN id SET DEFAULT nextval('public.energy_correction_scheme_id_seq'::regclass);
ALTER TABLE ONLY public.execution_environment_manifest ALTER COLUMN id SET DEFAULT nextval('public.execution_environment_manifest_id_seq'::regclass);
ALTER TABLE ONLY public.external_source ALTER COLUMN id SET DEFAULT nextval('public.external_source_id_seq'::regclass);
ALTER TABLE ONLY public.external_source_record ALTER COLUMN id SET DEFAULT nextval('public.external_source_record_id_seq'::regclass);
ALTER TABLE ONLY public.frequency_scale_factor ALTER COLUMN id SET DEFAULT nextval('public.frequency_scale_factor_id_seq'::regclass);
ALTER TABLE ONLY public.geometry ALTER COLUMN id SET DEFAULT nextval('public.geometry_id_seq'::regclass);
ALTER TABLE ONLY public.group_additivity_scheme ALTER COLUMN id SET DEFAULT nextval('public.group_additivity_scheme_id_seq'::regclass);
ALTER TABLE ONLY public.idempotency_record ALTER COLUMN id SET DEFAULT nextval('public.idempotency_record_id_seq'::regclass);
ALTER TABLE ONLY public.kinetics ALTER COLUMN id SET DEFAULT nextval('public.kinetics_id_seq'::regclass);
ALTER TABLE ONLY public.kinetics_arrhenius_entry ALTER COLUMN id SET DEFAULT nextval('public.kinetics_arrhenius_entry_id_seq'::regclass);
ALTER TABLE ONLY public.kinetics_determination ALTER COLUMN id SET DEFAULT nextval('public.kinetics_determination_id_seq'::regclass);
ALTER TABLE ONLY public.kinetics_plog ALTER COLUMN id SET DEFAULT nextval('public.kinetics_plog_id_seq'::regclass);
ALTER TABLE ONLY public.kinetics_third_body_efficiency ALTER COLUMN id SET DEFAULT nextval('public.kinetics_third_body_efficiency_id_seq'::regclass);
ALTER TABLE ONLY public.level_of_theory ALTER COLUMN id SET DEFAULT nextval('public.level_of_theory_id_seq'::regclass);
ALTER TABLE ONLY public.literature ALTER COLUMN id SET DEFAULT nextval('public.literature_id_seq'::regclass);
ALTER TABLE ONLY public.machine_review_curator_task ALTER COLUMN id SET DEFAULT nextval('public.machine_review_curator_task_id_seq'::regclass);
ALTER TABLE ONLY public.molecular_property_observation ALTER COLUMN id SET DEFAULT nextval('public.molecular_property_observation_id_seq'::regclass);
ALTER TABLE ONLY public.network ALTER COLUMN id SET DEFAULT nextval('public.network_id_seq'::regclass);
ALTER TABLE ONLY public.network_channel ALTER COLUMN id SET DEFAULT nextval('public.network_channel_id_seq'::regclass);
ALTER TABLE ONLY public.network_channel_microreaction ALTER COLUMN id SET DEFAULT nextval('public.network_channel_microreaction_id_seq'::regclass);
ALTER TABLE ONLY public.network_kinetics ALTER COLUMN id SET DEFAULT nextval('public.network_kinetics_id_seq'::regclass);
ALTER TABLE ONLY public.network_kinetics_determination ALTER COLUMN id SET DEFAULT nextval('public.network_kinetics_determination_id_seq'::regclass);
ALTER TABLE ONLY public.network_solve ALTER COLUMN id SET DEFAULT nextval('public.network_solve_id_seq'::regclass);
ALTER TABLE ONLY public.network_solve_energy_transfer ALTER COLUMN id SET DEFAULT nextval('public.network_solve_energy_transfer_id_seq'::regclass);
ALTER TABLE ONLY public.network_state ALTER COLUMN id SET DEFAULT nextval('public.network_state_id_seq'::regclass);
ALTER TABLE ONLY public.reaction_atom_map ALTER COLUMN id SET DEFAULT nextval('public.reaction_atom_map_id_seq'::regclass);
ALTER TABLE ONLY public.reaction_atom_map_pair ALTER COLUMN id SET DEFAULT nextval('public.reaction_atom_map_pair_id_seq'::regclass);
ALTER TABLE ONLY public.reaction_entry ALTER COLUMN id SET DEFAULT nextval('public.reaction_entry_id_seq'::regclass);
ALTER TABLE ONLY public.reaction_entry_structure_participant ALTER COLUMN id SET DEFAULT nextval('public.reaction_entry_structure_participant_id_seq'::regclass);
ALTER TABLE ONLY public.reaction_family ALTER COLUMN id SET DEFAULT nextval('public.reaction_family_id_seq'::regclass);
ALTER TABLE ONLY public.record_machine_review ALTER COLUMN id SET DEFAULT nextval('public.record_machine_review_id_seq'::regclass);
ALTER TABLE ONLY public.record_reproducibility_assessment ALTER COLUMN id SET DEFAULT nextval('public.record_reproducibility_assessment_id_seq'::regclass);
ALTER TABLE ONLY public.record_review ALTER COLUMN id SET DEFAULT nextval('public.record_review_id_seq'::regclass);
ALTER TABLE ONLY public.record_review_event ALTER COLUMN id SET DEFAULT nextval('public.record_review_event_id_seq'::regclass);
ALTER TABLE ONLY public.release_artifact ALTER COLUMN id SET DEFAULT nextval('public.release_artifact_id_seq'::regclass);
ALTER TABLE ONLY public.release_manifest ALTER COLUMN id SET DEFAULT nextval('public.release_manifest_id_seq'::regclass);
ALTER TABLE ONLY public.release_selection ALTER COLUMN id SET DEFAULT nextval('public.release_selection_id_seq'::regclass);
ALTER TABLE ONLY public.scientific_record_supersession ALTER COLUMN id SET DEFAULT nextval('public.scientific_record_supersession_id_seq'::regclass);
ALTER TABLE ONLY public.software ALTER COLUMN id SET DEFAULT nextval('public.software_id_seq'::regclass);
ALTER TABLE ONLY public.software_release ALTER COLUMN id SET DEFAULT nextval('public.software_release_id_seq'::regclass);
ALTER TABLE ONLY public.software_version_attestation ALTER COLUMN id SET DEFAULT nextval('public.software_version_attestation_id_seq'::regclass);
ALTER TABLE ONLY public.software_version_attestation_calculation ALTER COLUMN id SET DEFAULT nextval('public.software_version_attestation_calculation_id_seq'::regclass);
ALTER TABLE ONLY public.species ALTER COLUMN id SET DEFAULT nextval('public.species_id_seq'::regclass);
ALTER TABLE ONLY public.species_entry ALTER COLUMN id SET DEFAULT nextval('public.species_entry_id_seq'::regclass);
ALTER TABLE ONLY public.species_entry_review ALTER COLUMN id SET DEFAULT nextval('public.species_entry_review_id_seq'::regclass);
ALTER TABLE ONLY public.species_name ALTER COLUMN id SET DEFAULT nextval('public.species_name_id_seq'::regclass);
ALTER TABLE ONLY public.statmech ALTER COLUMN id SET DEFAULT nextval('public.statmech_id_seq'::regclass);
ALTER TABLE ONLY public.statmech_electronic_level ALTER COLUMN id SET DEFAULT nextval('public.statmech_electronic_level_id_seq'::regclass);
ALTER TABLE ONLY public.statmech_model_declaration ALTER COLUMN id SET DEFAULT nextval('public.statmech_model_declaration_id_seq'::regclass);
ALTER TABLE ONLY public.statmech_model_member ALTER COLUMN id SET DEFAULT nextval('public.statmech_model_member_id_seq'::regclass);
ALTER TABLE ONLY public.statmech_model_source ALTER COLUMN id SET DEFAULT nextval('public.statmech_model_source_id_seq'::regclass);
ALTER TABLE ONLY public.statmech_torsion ALTER COLUMN id SET DEFAULT nextval('public.statmech_torsion_id_seq'::regclass);
ALTER TABLE ONLY public.structure_determination ALTER COLUMN id SET DEFAULT nextval('public.structure_determination_id_seq'::regclass);
ALTER TABLE ONLY public.structure_determination_source ALTER COLUMN id SET DEFAULT nextval('public.structure_determination_source_id_seq'::regclass);
ALTER TABLE ONLY public.structure_evidence_finding ALTER COLUMN id SET DEFAULT nextval('public.structure_evidence_finding_id_seq'::regclass);
ALTER TABLE ONLY public.submission ALTER COLUMN id SET DEFAULT nextval('public.submission_id_seq'::regclass);
ALTER TABLE ONLY public.submission_audit_event ALTER COLUMN id SET DEFAULT nextval('public.submission_audit_event_id_seq'::regclass);
ALTER TABLE ONLY public.submission_record_link ALTER COLUMN id SET DEFAULT nextval('public.submission_record_link_id_seq'::regclass);
ALTER TABLE ONLY public.submission_rights_attestation ALTER COLUMN id SET DEFAULT nextval('public.submission_rights_attestation_id_seq'::regclass);
ALTER TABLE ONLY public.thermo ALTER COLUMN id SET DEFAULT nextval('public.thermo_id_seq'::regclass);
ALTER TABLE ONLY public.thermo_nasa9_interval ALTER COLUMN id SET DEFAULT nextval('public.thermo_nasa9_interval_id_seq'::regclass);
ALTER TABLE ONLY public.transition_state ALTER COLUMN id SET DEFAULT nextval('public.transition_state_id_seq'::regclass);
ALTER TABLE ONLY public.transition_state_entry ALTER COLUMN id SET DEFAULT nextval('public.transition_state_entry_id_seq'::regclass);
ALTER TABLE ONLY public.transition_state_selection ALTER COLUMN id SET DEFAULT nextval('public.transition_state_selection_id_seq'::regclass);
ALTER TABLE ONLY public.transition_state_validation_energy ALTER COLUMN id SET DEFAULT nextval('public.transition_state_validation_energy_id_seq'::regclass);
ALTER TABLE ONLY public.transition_state_validation_evidence ALTER COLUMN id SET DEFAULT nextval('public.transition_state_validation_evidence_id_seq'::regclass);
ALTER TABLE ONLY public.transport ALTER COLUMN id SET DEFAULT nextval('public.transport_id_seq'::regclass);
ALTER TABLE ONLY public.user_session ALTER COLUMN id SET DEFAULT nextval('public.user_session_id_seq'::regclass);
ALTER TABLE ONLY public.workflow_tool ALTER COLUMN id SET DEFAULT nextval('public.workflow_tool_id_seq'::regclass);
ALTER TABLE ONLY public.workflow_tool_release ALTER COLUMN id SET DEFAULT nextval('public.workflow_tool_release_id_seq'::regclass);
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT mpo_dedupe_key UNIQUE NULLS NOT DISTINCT (species_entry_id, property_kind, scientific_origin, external_source_name, external_source_release, external_source_url, external_source_record_key, reference_label, scalar_value, temperature_k);
ALTER TABLE ONLY public.accepted_science_repair
    ADD CONSTRAINT pk_accepted_science_repair PRIMARY KEY (id);
ALTER TABLE ONLY public.accepted_science_repair_change
    ADD CONSTRAINT pk_accepted_science_repair_change PRIMARY KEY (id);
ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT pk_api_key PRIMARY KEY (id);
ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT pk_app_user PRIMARY KEY (id);
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT pk_applied_energy_correction PRIMARY KEY (id);
ALTER TABLE ONLY public.applied_energy_correction_component
    ADD CONSTRAINT pk_applied_energy_correction_component PRIMARY KEY (id);
ALTER TABLE ONLY public.applied_group_additivity
    ADD CONSTRAINT pk_applied_group_additivity PRIMARY KEY (id);
ALTER TABLE ONLY public.applied_group_additivity_component
    ADD CONSTRAINT pk_applied_group_additivity_component PRIMARY KEY (id);
ALTER TABLE ONLY public.artifact_integrity_event
    ADD CONSTRAINT pk_artifact_integrity_event PRIMARY KEY (id);
ALTER TABLE ONLY public.artifact_storage_capacity_event
    ADD CONSTRAINT pk_artifact_storage_capacity_event PRIMARY KEY (id);
ALTER TABLE ONLY public.author
    ADD CONSTRAINT pk_author PRIMARY KEY (id);
ALTER TABLE ONLY public.calc_composite_input
    ADD CONSTRAINT pk_calc_composite_input PRIMARY KEY (calculation_id, term_position, slot, input_calculation_id);
ALTER TABLE ONLY public.calc_composite_log_check
    ADD CONSTRAINT pk_calc_composite_log_check PRIMARY KEY (calculation_id, artifact_sha256, parser_version);
ALTER TABLE ONLY public.calc_composite_result
    ADD CONSTRAINT pk_calc_composite_result PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_composite_term
    ADD CONSTRAINT pk_calc_composite_term PRIMARY KEY (calculation_id, term_position);
ALTER TABLE ONLY public.calc_freq_mode
    ADD CONSTRAINT pk_calc_freq_mode PRIMARY KEY (calculation_id, mode_index);
ALTER TABLE ONLY public.calc_freq_result
    ADD CONSTRAINT pk_calc_freq_result PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_geometry_validation
    ADD CONSTRAINT pk_calc_geometry_validation PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_hessian
    ADD CONSTRAINT pk_calc_hessian PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_irc_point
    ADD CONSTRAINT pk_calc_irc_point PRIMARY KEY (calculation_id, point_index);
ALTER TABLE ONLY public.calc_irc_result
    ADD CONSTRAINT pk_calc_irc_result PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_opt_result
    ADD CONSTRAINT pk_calc_opt_result PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_path_search_point
    ADD CONSTRAINT pk_calc_path_search_point PRIMARY KEY (calculation_id, point_index);
ALTER TABLE ONLY public.calc_path_search_result
    ADD CONSTRAINT pk_calc_path_search_result PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_scan_coordinate
    ADD CONSTRAINT pk_calc_scan_coordinate PRIMARY KEY (calculation_id, coordinate_index);
ALTER TABLE ONLY public.calc_scan_point
    ADD CONSTRAINT pk_calc_scan_point PRIMARY KEY (calculation_id, point_index);
ALTER TABLE ONLY public.calc_scan_point_coordinate_value
    ADD CONSTRAINT pk_calc_scan_point_coordinate_value PRIMARY KEY (calculation_id, point_index, coordinate_index);
ALTER TABLE ONLY public.calc_scan_result
    ADD CONSTRAINT pk_calc_scan_result PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_scf_stability
    ADD CONSTRAINT pk_calc_scf_stability PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_sp_energy_component
    ADD CONSTRAINT pk_calc_sp_energy_component PRIMARY KEY (calculation_id, component);
ALTER TABLE ONLY public.calc_sp_result
    ADD CONSTRAINT pk_calc_sp_result PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_spin_diagnostic
    ADD CONSTRAINT pk_calc_spin_diagnostic PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calc_wavefunction_diagnostic
    ADD CONSTRAINT pk_calc_wavefunction_diagnostic PRIMARY KEY (calculation_id);
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT pk_calculation PRIMARY KEY (id);
ALTER TABLE ONLY public.calculation_artifact
    ADD CONSTRAINT pk_calculation_artifact PRIMARY KEY (id);
ALTER TABLE ONLY public.calculation_constraint
    ADD CONSTRAINT pk_calculation_constraint PRIMARY KEY (calculation_id, constraint_index);
ALTER TABLE ONLY public.calculation_dependency
    ADD CONSTRAINT pk_calculation_dependency PRIMARY KEY (parent_calculation_id, child_calculation_id);
ALTER TABLE ONLY public.calculation_input_geometry
    ADD CONSTRAINT pk_calculation_input_geometry PRIMARY KEY (calculation_id, input_order);
ALTER TABLE ONLY public.calculation_output_geometry
    ADD CONSTRAINT pk_calculation_output_geometry PRIMARY KEY (calculation_id, output_order);
ALTER TABLE ONLY public.calculation_parameter
    ADD CONSTRAINT pk_calculation_parameter PRIMARY KEY (id);
ALTER TABLE ONLY public.calculation_parameter_vocab
    ADD CONSTRAINT pk_calculation_parameter_vocab PRIMARY KEY (canonical_key);
ALTER TABLE ONLY public.chem_reaction
    ADD CONSTRAINT pk_chem_reaction PRIMARY KEY (id);
ALTER TABLE ONLY public.composite_scheme
    ADD CONSTRAINT pk_composite_scheme PRIMARY KEY (id);
ALTER TABLE ONLY public.composite_scheme_term
    ADD CONSTRAINT pk_composite_scheme_term PRIMARY KEY (id);
ALTER TABLE ONLY public.composite_scheme_term_input
    ADD CONSTRAINT pk_composite_scheme_term_input PRIMARY KEY (id);
ALTER TABLE ONLY public.conformer_assignment_scheme
    ADD CONSTRAINT pk_conformer_assignment_scheme PRIMARY KEY (id);
ALTER TABLE ONLY public.conformer_group
    ADD CONSTRAINT pk_conformer_group PRIMARY KEY (id);
ALTER TABLE ONLY public.conformer_group_merge_pending
    ADD CONSTRAINT pk_conformer_group_merge_pending PRIMARY KEY (id);
ALTER TABLE ONLY public.conformer_observation
    ADD CONSTRAINT pk_conformer_observation PRIMARY KEY (id);
ALTER TABLE ONLY public.conformer_selection
    ADD CONSTRAINT pk_conformer_selection PRIMARY KEY (id);
ALTER TABLE ONLY public.curation_policy
    ADD CONSTRAINT pk_curation_policy PRIMARY KEY (id);
ALTER TABLE ONLY public.dataset_release
    ADD CONSTRAINT pk_dataset_release PRIMARY KEY (id);
ALTER TABLE ONLY public.deposit_warning
    ADD CONSTRAINT pk_deposit_warning PRIMARY KEY (id);
ALTER TABLE ONLY public.deposit_warning_retirement
    ADD CONSTRAINT pk_deposit_warning_retirement PRIMARY KEY (id);
ALTER TABLE ONLY public.energy_correction_scheme
    ADD CONSTRAINT pk_energy_correction_scheme PRIMARY KEY (id);
ALTER TABLE ONLY public.energy_correction_scheme_atom_param
    ADD CONSTRAINT pk_energy_correction_scheme_atom_param PRIMARY KEY (scheme_id, element);
ALTER TABLE ONLY public.energy_correction_scheme_bond_param
    ADD CONSTRAINT pk_energy_correction_scheme_bond_param PRIMARY KEY (scheme_id, bond_key);
ALTER TABLE ONLY public.energy_correction_scheme_component_param
    ADD CONSTRAINT pk_energy_correction_scheme_component_param PRIMARY KEY (scheme_id, component_kind, key);
ALTER TABLE ONLY public.execution_environment_manifest
    ADD CONSTRAINT pk_execution_environment_manifest PRIMARY KEY (id);
ALTER TABLE ONLY public.external_source
    ADD CONSTRAINT pk_external_source PRIMARY KEY (id);
ALTER TABLE ONLY public.external_source_record
    ADD CONSTRAINT pk_external_source_record PRIMARY KEY (id);
ALTER TABLE ONLY public.frequency_scale_factor
    ADD CONSTRAINT pk_frequency_scale_factor PRIMARY KEY (id);
ALTER TABLE ONLY public.geometry
    ADD CONSTRAINT pk_geometry PRIMARY KEY (id);
ALTER TABLE ONLY public.geometry_atom
    ADD CONSTRAINT pk_geometry_atom PRIMARY KEY (geometry_id, atom_index);
ALTER TABLE ONLY public.group_additivity_scheme
    ADD CONSTRAINT pk_group_additivity_scheme PRIMARY KEY (id);
ALTER TABLE ONLY public.idempotency_record
    ADD CONSTRAINT pk_idempotency_record PRIMARY KEY (id);
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT pk_kinetics PRIMARY KEY (id);
ALTER TABLE ONLY public.kinetics_arrhenius_entry
    ADD CONSTRAINT pk_kinetics_arrhenius_entry PRIMARY KEY (id);
ALTER TABLE ONLY public.kinetics_bath_gas
    ADD CONSTRAINT pk_kinetics_bath_gas PRIMARY KEY (kinetics_id, species_entry_id);
ALTER TABLE ONLY public.kinetics_chebyshev
    ADD CONSTRAINT pk_kinetics_chebyshev PRIMARY KEY (kinetics_id);
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT pk_kinetics_determination PRIMARY KEY (id);
ALTER TABLE ONLY public.kinetics_falloff
    ADD CONSTRAINT pk_kinetics_falloff PRIMARY KEY (kinetics_id);
ALTER TABLE ONLY public.kinetics_interpretation_assignment
    ADD CONSTRAINT pk_kinetics_interpretation_assignment PRIMARY KEY (kinetics_id, subject_key);
ALTER TABLE ONLY public.kinetics_plog
    ADD CONSTRAINT pk_kinetics_plog PRIMARY KEY (id);
ALTER TABLE ONLY public.kinetics_source_calculation
    ADD CONSTRAINT pk_kinetics_source_calculation PRIMARY KEY (kinetics_id, calculation_id, role);
ALTER TABLE ONLY public.kinetics_third_body_efficiency
    ADD CONSTRAINT pk_kinetics_third_body_efficiency PRIMARY KEY (id);
ALTER TABLE ONLY public.kinetics_tunneling_application
    ADD CONSTRAINT pk_kinetics_tunneling_application PRIMARY KEY (kinetics_id);
ALTER TABLE ONLY public.level_of_theory
    ADD CONSTRAINT pk_level_of_theory PRIMARY KEY (id);
ALTER TABLE ONLY public.level_of_theory_composite
    ADD CONSTRAINT pk_level_of_theory_composite PRIMARY KEY (level_of_theory_id);
ALTER TABLE ONLY public.level_of_theory_merge
    ADD CONSTRAINT pk_level_of_theory_merge PRIMARY KEY (merged_lot_id);
ALTER TABLE ONLY public.literature
    ADD CONSTRAINT pk_literature PRIMARY KEY (id);
ALTER TABLE ONLY public.literature_author
    ADD CONSTRAINT pk_literature_author PRIMARY KEY (literature_id, author_id);
ALTER TABLE ONLY public.machine_review_curator_task
    ADD CONSTRAINT pk_machine_review_curator_task PRIMARY KEY (id);
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT pk_molecular_property_observation PRIMARY KEY (id);
ALTER TABLE ONLY public.network
    ADD CONSTRAINT pk_network PRIMARY KEY (id);
ALTER TABLE ONLY public.network_channel
    ADD CONSTRAINT pk_network_channel PRIMARY KEY (id);
ALTER TABLE ONLY public.network_channel_microreaction
    ADD CONSTRAINT pk_network_channel_microreaction PRIMARY KEY (id);
ALTER TABLE ONLY public.network_kinetics
    ADD CONSTRAINT pk_network_kinetics PRIMARY KEY (id);
ALTER TABLE ONLY public.network_kinetics_chebyshev
    ADD CONSTRAINT pk_network_kinetics_chebyshev PRIMARY KEY (network_kinetics_id);
ALTER TABLE ONLY public.network_kinetics_determination
    ADD CONSTRAINT pk_network_kinetics_determination PRIMARY KEY (id);
ALTER TABLE ONLY public.network_kinetics_plog
    ADD CONSTRAINT pk_network_kinetics_plog PRIMARY KEY (network_kinetics_id, pressure_bar, entry_index);
ALTER TABLE ONLY public.network_kinetics_point
    ADD CONSTRAINT pk_network_kinetics_point PRIMARY KEY (network_kinetics_id, temperature_k, pressure_bar);
ALTER TABLE ONLY public.network_reaction
    ADD CONSTRAINT pk_network_reaction PRIMARY KEY (network_id, reaction_entry_id);
ALTER TABLE ONLY public.network_solve
    ADD CONSTRAINT pk_network_solve PRIMARY KEY (id);
ALTER TABLE ONLY public.network_solve_bath_gas
    ADD CONSTRAINT pk_network_solve_bath_gas PRIMARY KEY (solve_id, species_entry_id);
ALTER TABLE ONLY public.network_solve_channel_barrier
    ADD CONSTRAINT pk_network_solve_channel_barrier PRIMARY KEY (solve_id, channel_id, reaction_entry_id, transition_state_entry_id);
ALTER TABLE ONLY public.network_solve_energy_transfer
    ADD CONSTRAINT pk_network_solve_energy_transfer PRIMARY KEY (id);
ALTER TABLE ONLY public.network_solve_source_calculation
    ADD CONSTRAINT pk_network_solve_source_calculation PRIMARY KEY (solve_id, calculation_id, role);
ALTER TABLE ONLY public.network_solve_state_energy
    ADD CONSTRAINT pk_network_solve_state_energy PRIMARY KEY (solve_id, state_id);
ALTER TABLE ONLY public.network_solve_state_energy_source
    ADD CONSTRAINT pk_network_solve_state_energy_source PRIMARY KEY (solve_id, state_id, species_entry_id);
ALTER TABLE ONLY public.network_species
    ADD CONSTRAINT pk_network_species PRIMARY KEY (network_id, species_entry_id, role);
ALTER TABLE ONLY public.network_state
    ADD CONSTRAINT pk_network_state PRIMARY KEY (id);
ALTER TABLE ONLY public.network_state_participant
    ADD CONSTRAINT pk_network_state_participant PRIMARY KEY (state_id, species_entry_id);
ALTER TABLE ONLY public.reaction_atom_map
    ADD CONSTRAINT pk_reaction_atom_map PRIMARY KEY (id);
ALTER TABLE ONLY public.reaction_atom_map_pair
    ADD CONSTRAINT pk_reaction_atom_map_pair PRIMARY KEY (id);
ALTER TABLE ONLY public.reaction_entry
    ADD CONSTRAINT pk_reaction_entry PRIMARY KEY (id);
ALTER TABLE ONLY public.reaction_entry_structure_participant
    ADD CONSTRAINT pk_reaction_entry_structure_participant PRIMARY KEY (id);
ALTER TABLE ONLY public.reaction_family
    ADD CONSTRAINT pk_reaction_family PRIMARY KEY (id);
ALTER TABLE ONLY public.reaction_participant
    ADD CONSTRAINT pk_reaction_participant PRIMARY KEY (reaction_id, species_id, role);
ALTER TABLE ONLY public.record_machine_review
    ADD CONSTRAINT pk_record_machine_review PRIMARY KEY (id);
ALTER TABLE ONLY public.record_reproducibility_assessment
    ADD CONSTRAINT pk_record_reproducibility_assessment PRIMARY KEY (id);
ALTER TABLE ONLY public.record_review
    ADD CONSTRAINT pk_record_review PRIMARY KEY (id);
ALTER TABLE ONLY public.record_review_event
    ADD CONSTRAINT pk_record_review_event PRIMARY KEY (id);
ALTER TABLE ONLY public.release_artifact
    ADD CONSTRAINT pk_release_artifact PRIMARY KEY (id);
ALTER TABLE ONLY public.release_manifest
    ADD CONSTRAINT pk_release_manifest PRIMARY KEY (id);
ALTER TABLE ONLY public.release_selection
    ADD CONSTRAINT pk_release_selection PRIMARY KEY (id);
ALTER TABLE ONLY public.scientific_record_supersession
    ADD CONSTRAINT pk_scientific_record_supersession PRIMARY KEY (id);
ALTER TABLE ONLY public.software
    ADD CONSTRAINT pk_software PRIMARY KEY (id);
ALTER TABLE ONLY public.software_release
    ADD CONSTRAINT pk_software_release PRIMARY KEY (id);
ALTER TABLE ONLY public.software_version_attestation
    ADD CONSTRAINT pk_software_version_attestation PRIMARY KEY (id);
ALTER TABLE ONLY public.software_version_attestation_calculation
    ADD CONSTRAINT pk_software_version_attestation_calculation PRIMARY KEY (id);
ALTER TABLE ONLY public.species
    ADD CONSTRAINT pk_species PRIMARY KEY (id);
ALTER TABLE ONLY public.species_entry
    ADD CONSTRAINT pk_species_entry PRIMARY KEY (id);
ALTER TABLE ONLY public.species_entry_review
    ADD CONSTRAINT pk_species_entry_review PRIMARY KEY (id);
ALTER TABLE ONLY public.species_name
    ADD CONSTRAINT pk_species_name PRIMARY KEY (id);
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT pk_statmech PRIMARY KEY (id);
ALTER TABLE ONLY public.statmech_electronic_level
    ADD CONSTRAINT pk_statmech_electronic_level PRIMARY KEY (id);
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT pk_statmech_model_declaration PRIMARY KEY (id);
ALTER TABLE ONLY public.statmech_model_member
    ADD CONSTRAINT pk_statmech_model_member PRIMARY KEY (id);
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT pk_statmech_model_source PRIMARY KEY (id);
ALTER TABLE ONLY public.statmech_source_calculation
    ADD CONSTRAINT pk_statmech_source_calculation PRIMARY KEY (statmech_id, calculation_id, role);
ALTER TABLE ONLY public.statmech_torsion
    ADD CONSTRAINT pk_statmech_torsion PRIMARY KEY (id);
ALTER TABLE ONLY public.statmech_torsion_definition
    ADD CONSTRAINT pk_statmech_torsion_definition PRIMARY KEY (torsion_id, coordinate_index);
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT pk_structure_determination PRIMARY KEY (id);
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT pk_structure_determination_source PRIMARY KEY (id);
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT pk_structure_evidence_finding PRIMARY KEY (id);
ALTER TABLE ONLY public.submission
    ADD CONSTRAINT pk_submission PRIMARY KEY (id);
ALTER TABLE ONLY public.submission_audit_event
    ADD CONSTRAINT pk_submission_audit_event PRIMARY KEY (id);
ALTER TABLE ONLY public.submission_record_link
    ADD CONSTRAINT pk_submission_record_link PRIMARY KEY (id);
ALTER TABLE ONLY public.submission_rights_attestation
    ADD CONSTRAINT pk_submission_rights_attestation PRIMARY KEY (id);
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT pk_thermo PRIMARY KEY (id);
ALTER TABLE ONLY public.thermo_nasa
    ADD CONSTRAINT pk_thermo_nasa PRIMARY KEY (thermo_id);
ALTER TABLE ONLY public.thermo_nasa9_interval
    ADD CONSTRAINT pk_thermo_nasa9_interval PRIMARY KEY (id);
ALTER TABLE ONLY public.thermo_point
    ADD CONSTRAINT pk_thermo_point PRIMARY KEY (thermo_id, temperature_k);
ALTER TABLE ONLY public.thermo_source_calculation
    ADD CONSTRAINT pk_thermo_source_calculation PRIMARY KEY (thermo_id, calculation_id, role);
ALTER TABLE ONLY public.thermo_wilhoit
    ADD CONSTRAINT pk_thermo_wilhoit PRIMARY KEY (thermo_id);
ALTER TABLE ONLY public.transition_state
    ADD CONSTRAINT pk_transition_state PRIMARY KEY (id);
ALTER TABLE ONLY public.transition_state_entry
    ADD CONSTRAINT pk_transition_state_entry PRIMARY KEY (id);
ALTER TABLE ONLY public.transition_state_selection
    ADD CONSTRAINT pk_transition_state_selection PRIMARY KEY (id);
ALTER TABLE ONLY public.transition_state_validation_energy
    ADD CONSTRAINT pk_transition_state_validation_energy PRIMARY KEY (id);
ALTER TABLE ONLY public.transition_state_validation_evidence
    ADD CONSTRAINT pk_transition_state_validation_evidence PRIMARY KEY (id);
ALTER TABLE ONLY public.transport
    ADD CONSTRAINT pk_transport PRIMARY KEY (id);
ALTER TABLE ONLY public.transport_source_calculation
    ADD CONSTRAINT pk_transport_source_calculation PRIMARY KEY (transport_id, calculation_id, role);
ALTER TABLE ONLY public.upload_job
    ADD CONSTRAINT pk_upload_job PRIMARY KEY (id);
ALTER TABLE ONLY public.user_session
    ADD CONSTRAINT pk_user_session PRIMARY KEY (id);
ALTER TABLE ONLY public.workflow_tool
    ADD CONSTRAINT pk_workflow_tool PRIMARY KEY (id);
ALTER TABLE ONLY public.workflow_tool_release
    ADD CONSTRAINT pk_workflow_tool_release PRIMARY KEY (id);
ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT uq_api_key_key_hash UNIQUE (key_hash);
ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT uq_app_user_email UNIQUE (email);
ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT uq_app_user_orcid UNIQUE (orcid);
ALTER TABLE ONLY public.app_user
    ADD CONSTRAINT uq_app_user_username UNIQUE (username);
ALTER TABLE ONLY public.applied_group_additivity
    ADD CONSTRAINT uq_applied_group_additivity_thermo_id UNIQUE (thermo_id);
ALTER TABLE ONLY public.author
    ADD CONSTRAINT uq_author_orcid UNIQUE (orcid);
ALTER TABLE ONLY public.calculation_input_geometry
    ADD CONSTRAINT uq_calculation_input_geometry_calculation_id UNIQUE (calculation_id, geometry_id);
ALTER TABLE ONLY public.calculation_output_geometry
    ADD CONSTRAINT uq_calculation_output_geometry_calculation_id UNIQUE (calculation_id, geometry_id);
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT uq_calculation_scope_observation UNIQUE (id, conformer_observation_id);
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT uq_calculation_scope_species UNIQUE (id, species_entry_id);
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT uq_calculation_scope_ts UNIQUE (id, transition_state_entry_id);
ALTER TABLE ONLY public.composite_scheme
    ADD CONSTRAINT uq_composite_scheme_definition_hash UNIQUE (definition_hash);
ALTER TABLE ONLY public.composite_scheme_term
    ADD CONSTRAINT uq_composite_scheme_term_scheme_id_position UNIQUE (scheme_id, "position");
ALTER TABLE ONLY public.conformer_assignment_scheme
    ADD CONSTRAINT uq_conformer_assignment_scheme_name UNIQUE (name, version);
ALTER TABLE ONLY public.conformer_group_merge_pending
    ADD CONSTRAINT uq_conformer_group_merge_pending_merge UNIQUE (kept_conformer_group_id, absorbed_conformer_group_id, conformer_observation_id);
ALTER TABLE ONLY public.conformer_group
    ADD CONSTRAINT uq_conformer_group_species_entry_id UNIQUE (species_entry_id, label);
ALTER TABLE ONLY public.conformer_selection
    ADD CONSTRAINT uq_conformer_selection_conformer_group_id UNIQUE NULLS NOT DISTINCT (conformer_group_id, assignment_scheme_id, selection_kind);
ALTER TABLE ONLY public.curation_policy
    ADD CONSTRAINT uq_curation_policy_name UNIQUE (name, version);
ALTER TABLE ONLY public.dataset_release
    ADD CONSTRAINT uq_dataset_release_tag UNIQUE (tag);
ALTER TABLE ONLY public.deposit_warning
    ADD CONSTRAINT uq_deposit_warning_identity UNIQUE NULLS NOT DISTINCT (submission_id, record_type, record_id, content_hash);
ALTER TABLE ONLY public.deposit_warning_retirement
    ADD CONSTRAINT uq_deposit_warning_retirement_warning UNIQUE (deposit_warning_id);
ALTER TABLE ONLY public.execution_environment_manifest
    ADD CONSTRAINT uq_execution_environment_manifest_content_digest UNIQUE (content_digest);
ALTER TABLE ONLY public.external_source
    ADD CONSTRAINT uq_external_source_name_release UNIQUE (source_name, source_release);
ALTER TABLE ONLY public.external_source_record
    ADD CONSTRAINT uq_external_source_record_identity UNIQUE (external_source_id, source_record_key, content_sha256, parser_name, parser_version, mapping_name, mapping_version);
ALTER TABLE ONLY public.geometry_atom
    ADD CONSTRAINT uq_geometry_atom_geometry_id_element UNIQUE (geometry_id, atom_index, element);
ALTER TABLE ONLY public.geometry
    ADD CONSTRAINT uq_geometry_geom_hash UNIQUE (geom_hash);
ALTER TABLE ONLY public.idempotency_record
    ADD CONSTRAINT uq_idempotency_record_user_method_endpoint_key UNIQUE (user_id, request_method, endpoint, idempotency_key);
ALTER TABLE ONLY public.kinetics_arrhenius_entry
    ADD CONSTRAINT uq_kinetics_arrhenius_entry UNIQUE (kinetics_id, entry_index);
ALTER TABLE ONLY public.kinetics_third_body_efficiency
    ADD CONSTRAINT uq_kinetics_collider UNIQUE (kinetics_id, collider_species_id);
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT uq_kinetics_determination_identity_hash UNIQUE (identity_hash);
ALTER TABLE ONLY public.kinetics_plog
    ADD CONSTRAINT uq_kinetics_plog_entry UNIQUE (kinetics_id, entry_index);
ALTER TABLE ONLY public.level_of_theory
    ADD CONSTRAINT uq_level_of_theory_lot_hash UNIQUE (lot_hash);
ALTER TABLE ONLY public.literature_author
    ADD CONSTRAINT uq_literature_author_literature_id UNIQUE (literature_id, author_order);
ALTER TABLE ONLY public.machine_review_curator_task
    ADD CONSTRAINT uq_machine_review_curator_task_identity UNIQUE (submission_id, record_type, record_id, finding_fingerprint);
ALTER TABLE ONLY public.network_channel
    ADD CONSTRAINT uq_network_channel_key UNIQUE (network_id, channel_key);
ALTER TABLE ONLY public.network_channel_microreaction
    ADD CONSTRAINT uq_network_channel_microreaction_path UNIQUE (channel_id, reaction_entry_id, transition_state_entry_id);
ALTER TABLE ONLY public.network_kinetics_determination
    ADD CONSTRAINT uq_network_kinetics_determination_identity_hash UNIQUE (identity_hash);
ALTER TABLE ONLY public.network_kinetics_determination
    ADD CONSTRAINT uq_network_kinetics_determination_key UNIQUE (solve_id, determination_key);
ALTER TABLE ONLY public.network_kinetics_determination
    ADD CONSTRAINT uq_network_kinetics_determination_scope UNIQUE (id, solve_id, channel_id);
ALTER TABLE ONLY public.network_solve_energy_transfer
    ADD CONSTRAINT uq_network_solve_energy_transfer_scope UNIQUE (solve_id, state_id, collider_species_entry_id);
ALTER TABLE ONLY public.network_state
    ADD CONSTRAINT uq_network_state_network_id UNIQUE (network_id, composition_hash);
ALTER TABLE ONLY public.reaction_atom_map
    ADD CONSTRAINT uq_reaction_atom_map_id UNIQUE (id, transition_state_geometry_id);
ALTER TABLE ONLY public.reaction_atom_map_pair
    ADD CONSTRAINT uq_reaction_atom_map_pair_atom_map_id UNIQUE (atom_map_id, structure_participant_id, atom_index);
ALTER TABLE ONLY public.reaction_atom_map_pair
    ADD CONSTRAINT uq_reaction_atom_map_pair_ts_atom_index UNIQUE (atom_map_id, side, ts_atom_index);
ALTER TABLE ONLY public.reaction_atom_map
    ADD CONSTRAINT uq_reaction_atom_map_reaction_entry_id UNIQUE (reaction_entry_id, transition_state_entry_id);
ALTER TABLE ONLY public.reaction_entry_structure_participant
    ADD CONSTRAINT uq_reaction_entry_structure_participant_id UNIQUE (id, role);
ALTER TABLE ONLY public.reaction_entry_structure_participant
    ADD CONSTRAINT uq_reaction_entry_structure_participant_reaction_entry_id UNIQUE (reaction_entry_id, role, participant_index);
ALTER TABLE ONLY public.reaction_family
    ADD CONSTRAINT uq_reaction_family_name UNIQUE (name);
ALTER TABLE ONLY public.record_review
    ADD CONSTRAINT uq_record_review_record UNIQUE (record_type, record_id);
ALTER TABLE ONLY public.release_artifact
    ADD CONSTRAINT uq_release_artifact_release_manifest_id UNIQUE (release_manifest_id, path);
ALTER TABLE ONLY public.release_manifest
    ADD CONSTRAINT uq_release_manifest_dataset_release_id UNIQUE (dataset_release_id);
ALTER TABLE ONLY public.release_selection
    ADD CONSTRAINT uq_release_selection_supersedes_selection_id UNIQUE (supersedes_selection_id);
ALTER TABLE ONLY public.scientific_record_supersession
    ADD CONSTRAINT uq_scientific_supersession_new_record UNIQUE (record_type, superseding_record_id);
ALTER TABLE ONLY public.scientific_record_supersession
    ADD CONSTRAINT uq_scientific_supersession_old_record UNIQUE (record_type, superseded_record_id);
ALTER TABLE ONLY public.software
    ADD CONSTRAINT uq_software_name UNIQUE (name);
ALTER TABLE ONLY public.species_entry_review
    ADD CONSTRAINT uq_species_entry_review_species_entry_id UNIQUE (species_entry_id, user_id, role);
ALTER TABLE ONLY public.statmech_electronic_level
    ADD CONSTRAINT uq_statmech_electronic_level UNIQUE (statmech_id, level_index);
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT uq_statmech_model_declaration_statmech_id UNIQUE (statmech_id);
ALTER TABLE ONLY public.statmech_model_member
    ADD CONSTRAINT uq_statmech_model_member_key UNIQUE (declaration_id, member_key);
ALTER TABLE ONLY public.statmech_model_member
    ADD CONSTRAINT uq_statmech_model_member_statmech UNIQUE (declaration_id, member_statmech_id);
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT uq_statmech_model_source_key UNIQUE (declaration_id, source_key);
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT uq_structure_determination_identity_hash UNIQUE (identity_hash);
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT uq_structure_determination_key UNIQUE NULLS NOT DISTINCT (species_entry_id, transition_state_entry_id, conformer_observation_id, literature_id, workflow_tool_release_id, determination_key);
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT uq_structure_determination_scope_observation UNIQUE (id, conformer_observation_id);
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT uq_structure_determination_scope_species UNIQUE (id, species_entry_id);
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT uq_structure_determination_scope_ts UNIQUE (id, transition_state_entry_id);
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT uq_structure_determination_source_role UNIQUE (determination_id, role, calculation_id);
ALTER TABLE ONLY public.submission_record_link
    ADD CONSTRAINT uq_submission_record_link_identity UNIQUE NULLS NOT DISTINCT (submission_id, record_type, record_id, role);
ALTER TABLE ONLY public.submission_rights_attestation
    ADD CONSTRAINT uq_submission_rights_attestation_supersedes_attestation_id UNIQUE (supersedes_attestation_id);
ALTER TABLE ONLY public.software_version_attestation_calculation
    ADD CONSTRAINT uq_sva_calculation_attestation_calculation UNIQUE (attestation_id, calculation_id);
ALTER TABLE ONLY public.thermo_nasa9_interval
    ADD CONSTRAINT uq_thermo_nasa9_interval_index UNIQUE (thermo_id, interval_index);
ALTER TABLE ONLY public.transition_state_selection
    ADD CONSTRAINT uq_transition_state_selection_transition_state_id UNIQUE (transition_state_id, selection_kind);
ALTER TABLE ONLY public.transition_state_validation_energy
    ADD CONSTRAINT uq_ts_validation_energy_slot UNIQUE (evidence_id, participant, energy_kind);
ALTER TABLE ONLY public.transition_state_validation_evidence
    ADD CONSTRAINT uq_ts_validation_evidence_kind UNIQUE (transition_state_entry_id, kind);
ALTER TABLE ONLY public.user_session
    ADD CONSTRAINT uq_user_session_token_hash UNIQUE (token_hash);
ALTER TABLE ONLY public.workflow_tool
    ADD CONSTRAINT uq_workflow_tool_name UNIQUE (name);
CREATE INDEX ix_accepted_science_repair_change_record ON public.accepted_science_repair_change USING btree (record_type, record_id);
CREATE INDEX ix_accepted_science_repair_change_repair_id ON public.accepted_science_repair_change USING btree (repair_id);
CREATE INDEX ix_accepted_science_repair_target ON public.accepted_science_repair USING btree (target_schema, target_table, xact_id);
CREATE INDEX ix_artifact_integrity_event_artifact_id ON public.artifact_integrity_event USING btree (artifact_id);
CREATE UNIQUE INDEX ix_artifact_integrity_event_public_ref ON public.artifact_integrity_event USING btree (public_ref);
CREATE INDEX ix_artifact_integrity_event_sha256 ON public.artifact_integrity_event USING btree (sha256);
CREATE INDEX ix_artifact_integrity_event_sha256_created_at ON public.artifact_integrity_event USING btree (sha256, created_at);
CREATE INDEX ix_artifact_storage_capacity_event_observation_id ON public.artifact_storage_capacity_event USING btree (observation, id);
CREATE INDEX ix_calc_composite_input_input_calculation_id ON public.calc_composite_input USING btree (input_calculation_id);
CREATE INDEX ix_calculation_artifact_calculation_id ON public.calculation_artifact USING btree (calculation_id);
CREATE INDEX ix_calculation_artifact_kind ON public.calculation_artifact USING btree (kind);
CREATE UNIQUE INDEX ix_calculation_artifact_public_ref ON public.calculation_artifact USING btree (public_ref);
CREATE INDEX ix_calculation_artifact_sha256 ON public.calculation_artifact USING btree (sha256);
CREATE INDEX ix_calculation_conformer_observation_id ON public.calculation USING btree (conformer_observation_id);
CREATE INDEX ix_calculation_execution_environment_manifest_id ON public.calculation USING btree (execution_environment_manifest_id);
CREATE INDEX ix_calculation_literature_id ON public.calculation USING btree (literature_id);
CREATE INDEX ix_calculation_lot_id ON public.calculation USING btree (lot_id);
CREATE INDEX ix_calculation_parameter_calculation_id ON public.calculation_parameter USING btree (calculation_id);
CREATE INDEX ix_calculation_parameter_canonical_key ON public.calculation_parameter USING btree (canonical_key);
CREATE INDEX ix_calculation_parameter_canonical_key_value ON public.calculation_parameter USING btree (canonical_key, canonical_value);
CREATE INDEX ix_calculation_parameter_raw_key_section ON public.calculation_parameter USING btree (raw_key, section);
CREATE INDEX ix_calculation_parameter_source ON public.calculation_parameter USING btree (calculation_id, source);
CREATE UNIQUE INDEX ix_calculation_public_ref ON public.calculation USING btree (public_ref);
CREATE INDEX ix_calculation_software_reconciliation_status ON public.calculation USING btree (software_reconciliation_status);
CREATE INDEX ix_calculation_software_release_id ON public.calculation USING btree (software_release_id);
CREATE INDEX ix_calculation_species_entry_id ON public.calculation USING btree (species_entry_id);
CREATE INDEX ix_calculation_transition_state_entry_id ON public.calculation USING btree (transition_state_entry_id);
CREATE INDEX ix_calculation_type_id ON public.calculation USING btree (type, id);
CREATE INDEX ix_calculation_workflow_tool_release_id ON public.calculation USING btree (workflow_tool_release_id);
CREATE INDEX ix_chem_reaction_merged_into_chem_reaction_id ON public.chem_reaction USING btree (merged_into_chem_reaction_id);
CREATE UNIQUE INDEX ix_chem_reaction_public_ref ON public.chem_reaction USING btree (public_ref);
CREATE UNIQUE INDEX ix_composite_scheme_public_ref ON public.composite_scheme USING btree (public_ref);
CREATE INDEX ix_composite_scheme_term_input_level_of_theory_id ON public.composite_scheme_term_input USING btree (level_of_theory_id);
CREATE UNIQUE INDEX ix_conformer_assignment_scheme_public_ref ON public.conformer_assignment_scheme USING btree (public_ref);
CREATE INDEX ix_conformer_group_merge_pending_open ON public.conformer_group_merge_pending USING btree (species_entry_id) WHERE (completed_at IS NULL);
CREATE UNIQUE INDEX ix_conformer_group_public_ref ON public.conformer_group USING btree (public_ref);
CREATE INDEX ix_conformer_group_species_entry_id ON public.conformer_group USING btree (species_entry_id);
CREATE INDEX ix_conformer_group_wide_basin ON public.conformer_group USING btree (id) WHERE (basin_is_clique IS FALSE);
CREATE INDEX ix_conformer_observation_conformer_group_id ON public.conformer_observation USING btree (conformer_group_id);
CREATE UNIQUE INDEX ix_conformer_observation_public_ref ON public.conformer_observation USING btree (public_ref);
CREATE UNIQUE INDEX ix_curation_policy_public_ref ON public.curation_policy USING btree (public_ref);
CREATE UNIQUE INDEX ix_dataset_release_public_ref ON public.dataset_release USING btree (public_ref);
CREATE INDEX ix_dataset_release_status ON public.dataset_release USING btree (status);
CREATE INDEX ix_deposit_warning_record ON public.deposit_warning USING btree (record_type, record_id);
CREATE UNIQUE INDEX ix_energy_correction_scheme_public_ref ON public.energy_correction_scheme USING btree (public_ref);
CREATE UNIQUE INDEX ix_frequency_scale_factor_public_ref ON public.frequency_scale_factor USING btree (public_ref);
CREATE UNIQUE INDEX ix_geometry_public_ref ON public.geometry USING btree (public_ref);
CREATE UNIQUE INDEX ix_group_additivity_scheme_public_ref ON public.group_additivity_scheme USING btree (public_ref);
CREATE INDEX ix_idempotency_record_expires_at ON public.idempotency_record USING btree (expires_at);
CREATE INDEX ix_kinetics_determination_id ON public.kinetics USING btree (determination_id);
CREATE UNIQUE INDEX ix_kinetics_determination_public_ref ON public.kinetics_determination USING btree (public_ref);
CREATE INDEX ix_kinetics_determination_reaction_entry_id ON public.kinetics_determination USING btree (reaction_entry_id);
CREATE INDEX ix_kinetics_energy_level_of_theory_id ON public.kinetics USING btree (energy_level_of_theory_id);
CREATE INDEX ix_kinetics_literature_id ON public.kinetics USING btree (literature_id);
CREATE INDEX ix_kinetics_network_kinetics_id ON public.kinetics USING btree (network_kinetics_id);
CREATE UNIQUE INDEX ix_kinetics_public_ref ON public.kinetics USING btree (public_ref);
CREATE INDEX ix_level_of_theory_composite_scheme_id ON public.level_of_theory_composite USING btree (scheme_id);
CREATE INDEX ix_level_of_theory_merge_into_lot_id ON public.level_of_theory_merge USING btree (into_lot_id);
CREATE UNIQUE INDEX ix_level_of_theory_public_ref ON public.level_of_theory USING btree (public_ref);
CREATE INDEX ix_literature_doi_normalized ON public.literature USING btree (lower(regexp_replace(doi, '^https?://(dx\.)?doi\.org/'::text, ''::text)));
CREATE INDEX ix_literature_isbn_normalized ON public.literature USING btree (regexp_replace(isbn, '[- ]'::text, ''::text, 'g'::text));
CREATE UNIQUE INDEX ix_literature_public_ref ON public.literature USING btree (public_ref);
CREATE INDEX ix_machine_review_curator_task_assigned_to ON public.machine_review_curator_task USING btree (assigned_to);
CREATE INDEX ix_machine_review_curator_task_record ON public.machine_review_curator_task USING btree (record_type, record_id);
CREATE INDEX ix_machine_review_curator_task_source_audit_event_id ON public.machine_review_curator_task USING btree (source_audit_event_id);
CREATE INDEX ix_machine_review_curator_task_state_severity ON public.machine_review_curator_task USING btree (workflow_state, highest_severity);
CREATE INDEX ix_machine_review_curator_task_submission_id ON public.machine_review_curator_task USING btree (submission_id);
CREATE INDEX ix_machine_review_curator_task_workflow_state ON public.machine_review_curator_task USING btree (workflow_state);
CREATE UNIQUE INDEX ix_molecular_property_observation_public_ref ON public.molecular_property_observation USING btree (public_ref);
CREATE INDEX ix_mpo_external_source_content_sha256 ON public.molecular_property_observation USING btree (external_source_content_sha256);
CREATE INDEX ix_mpo_external_source_release ON public.molecular_property_observation USING btree (external_source_name, external_source_release);
CREATE INDEX ix_mpo_property_kind_origin ON public.molecular_property_observation USING btree (property_kind, scientific_origin);
CREATE INDEX ix_mpo_species_entry_id ON public.molecular_property_observation USING btree (species_entry_id);
CREATE INDEX ix_network_kinetics_determination_id ON public.network_kinetics USING btree (determination_id);
CREATE UNIQUE INDEX ix_network_kinetics_determination_public_ref ON public.network_kinetics_determination USING btree (public_ref);
CREATE INDEX ix_network_kinetics_determination_solve_id ON public.network_kinetics_determination USING btree (solve_id);
CREATE UNIQUE INDEX ix_network_kinetics_public_ref ON public.network_kinetics USING btree (public_ref);
CREATE INDEX ix_network_literature_id ON public.network USING btree (literature_id);
CREATE UNIQUE INDEX ix_network_public_ref ON public.network USING btree (public_ref);
CREATE INDEX ix_network_solve_literature_id ON public.network_solve USING btree (literature_id);
CREATE UNIQUE INDEX ix_network_solve_public_ref ON public.network_solve USING btree (public_ref);
CREATE UNIQUE INDEX ix_reaction_entry_public_ref ON public.reaction_entry USING btree (public_ref);
CREATE INDEX ix_record_machine_review_context_hash ON public.record_machine_review USING btree (context_hash);
CREATE INDEX ix_record_machine_review_record_reviewed_at ON public.record_machine_review USING btree (record_type, record_id, reviewed_at DESC);
CREATE INDEX ix_record_machine_review_record_source_audit_event ON public.record_machine_review USING btree (record_type, record_id, source_audit_event_id DESC);
CREATE INDEX ix_record_machine_review_source_audit_event_id ON public.record_machine_review USING btree (source_audit_event_id);
CREATE INDEX ix_record_machine_review_source_submission_id ON public.record_machine_review USING btree (source_submission_id);
CREATE UNIQUE INDEX ix_record_reproducibility_assessment_public_ref ON public.record_reproducibility_assessment USING btree (public_ref);
CREATE INDEX ix_record_review_event_record_review_id ON public.record_review_event USING btree (record_review_id);
CREATE INDEX ix_record_review_record_lookup ON public.record_review USING btree (record_type, record_id) INCLUDE (status);
CREATE INDEX ix_record_review_status_record_type ON public.record_review USING btree (status, record_type);
CREATE INDEX ix_record_review_submission_id ON public.record_review USING btree (submission_id);
CREATE UNIQUE INDEX ix_release_manifest_public_ref ON public.release_manifest USING btree (public_ref);
CREATE UNIQUE INDEX ix_release_selection_public_ref ON public.release_selection USING btree (public_ref);
CREATE INDEX ix_release_selection_record ON public.release_selection USING btree (record_type, record_id);
CREATE INDEX ix_release_selection_release_subject ON public.release_selection USING btree (dataset_release_id, subject_type, subject_id);
CREATE INDEX ix_repro_assessment_assessor_user_id ON public.record_reproducibility_assessment USING btree (assessor_user_id);
CREATE INDEX ix_repro_assessment_context_hash ON public.record_reproducibility_assessment USING btree (context_hash);
CREATE INDEX ix_repro_assessment_record_latest ON public.record_reproducibility_assessment USING btree (record_type, record_id, assessed_at DESC, id DESC);
CREATE INDEX ix_repro_assessment_source_submission_id ON public.record_reproducibility_assessment USING btree (source_submission_id);
CREATE UNIQUE INDEX ix_software_public_ref ON public.software USING btree (public_ref);
CREATE UNIQUE INDEX ix_software_release_public_ref ON public.software_release USING btree (public_ref);
CREATE INDEX ix_software_version_attestation_calculation_calculation_id ON public.software_version_attestation_calculation USING btree (calculation_id);
CREATE INDEX ix_software_version_attestation_software_release_id ON public.software_version_attestation USING btree (software_release_id);
CREATE INDEX ix_species_entry_merged_into_species_entry_id ON public.species_entry USING btree (merged_into_species_entry_id);
CREATE INDEX ix_species_entry_mol_gist ON public.species_entry USING gist (mol);
CREATE UNIQUE INDEX ix_species_entry_public_ref ON public.species_entry USING btree (public_ref);
CREATE INDEX ix_species_entry_species_id ON public.species_entry USING btree (species_id);
CREATE INDEX ix_species_entry_stereo_smiles ON public.species_entry USING btree (stereo_smiles);
CREATE INDEX ix_species_formula_lookup ON public.species USING btree (((public.mol_formula(public.mol_from_smiles((smiles)::cstring)))::text));
CREATE INDEX ix_species_inchi_key ON public.species USING btree (inchi_key);
CREATE INDEX ix_species_merged_into_species_id ON public.species USING btree (merged_into_species_id);
CREATE INDEX ix_species_name_name_key ON public.species_name USING btree (name_key);
CREATE UNIQUE INDEX ix_species_name_public_ref ON public.species_name USING btree (public_ref);
CREATE INDEX ix_species_name_species_id ON public.species_name USING btree (species_id);
CREATE UNIQUE INDEX ix_species_public_ref ON public.species USING btree (public_ref);
CREATE INDEX ix_statmech_energy_level_of_theory_id ON public.statmech USING btree (energy_level_of_theory_id);
CREATE INDEX ix_statmech_literature_id ON public.statmech USING btree (literature_id);
CREATE INDEX ix_statmech_model_declaration_conformer_group_id ON public.statmech_model_declaration USING btree (conformer_group_id);
CREATE UNIQUE INDEX ix_statmech_model_declaration_public_ref ON public.statmech_model_declaration USING btree (public_ref);
CREATE INDEX ix_statmech_model_declaration_structure_determination_id ON public.statmech_model_declaration USING btree (structure_determination_id);
CREATE INDEX ix_statmech_model_member_declaration_id ON public.statmech_model_member USING btree (declaration_id);
CREATE INDEX ix_statmech_model_member_member_statmech_id ON public.statmech_model_member USING btree (member_statmech_id);
CREATE INDEX ix_statmech_model_source_calculation_id ON public.statmech_model_source USING btree (calculation_id);
CREATE INDEX ix_statmech_model_source_declaration_id ON public.statmech_model_source USING btree (declaration_id);
CREATE UNIQUE INDEX ix_statmech_public_ref ON public.statmech USING btree (public_ref);
CREATE INDEX ix_statmech_transition_state_entry_id ON public.statmech USING btree (transition_state_entry_id);
CREATE INDEX ix_structure_determination_conformer_observation_id ON public.structure_determination USING btree (conformer_observation_id);
CREATE INDEX ix_structure_determination_evaluated_geometry_id ON public.structure_determination USING btree (evaluated_geometry_id);
CREATE UNIQUE INDEX ix_structure_determination_public_ref ON public.structure_determination USING btree (public_ref);
CREATE INDEX ix_structure_determination_source_calculation_id ON public.structure_determination_source USING btree (calculation_id);
CREATE INDEX ix_structure_determination_source_determination_id ON public.structure_determination_source USING btree (determination_id);
CREATE INDEX ix_structure_determination_species_entry_id ON public.structure_determination USING btree (species_entry_id);
CREATE INDEX ix_structure_determination_transition_state_entry_id ON public.structure_determination USING btree (transition_state_entry_id);
CREATE UNIQUE INDEX ix_structure_evidence_finding_public_ref ON public.structure_evidence_finding USING btree (public_ref);
CREATE INDEX ix_structure_evidence_finding_subject_calculation_id ON public.structure_evidence_finding USING btree (subject_calculation_id);
CREATE INDEX ix_structure_evidence_finding_subject_determination_id ON public.structure_evidence_finding USING btree (subject_determination_id);
CREATE INDEX ix_structure_evidence_finding_subject_geometry_id ON public.structure_evidence_finding USING btree (subject_geometry_id);
CREATE INDEX ix_structure_evidence_finding_supersedes_finding_id ON public.structure_evidence_finding USING btree (supersedes_finding_id);
CREATE INDEX ix_submission_audit_event_event_kind ON public.submission_audit_event USING btree (event_kind);
CREATE INDEX ix_submission_audit_event_submission_id ON public.submission_audit_event USING btree (submission_id);
CREATE INDEX ix_submission_created_by ON public.submission USING btree (created_by);
CREATE UNIQUE INDEX ix_submission_public_ref ON public.submission USING btree (public_ref);
CREATE INDEX ix_submission_record_link_record ON public.submission_record_link USING btree (record_type, record_id);
CREATE INDEX ix_submission_record_link_submission_id ON public.submission_record_link USING btree (submission_id);
CREATE UNIQUE INDEX ix_submission_rights_attestation_public_ref ON public.submission_rights_attestation USING btree (public_ref);
CREATE INDEX ix_submission_rights_attestation_submission_id ON public.submission_rights_attestation USING btree (submission_id);
CREATE INDEX ix_submission_status_created_at ON public.submission USING btree (status, created_at);
CREATE INDEX ix_submission_upload_job_id ON public.submission USING btree (upload_job_id);
CREATE INDEX ix_thermo_energy_level_of_theory_id ON public.thermo USING btree (energy_level_of_theory_id);
CREATE INDEX ix_thermo_literature_id ON public.thermo USING btree (literature_id);
CREATE INDEX ix_thermo_nasa9_interval_thermo_id ON public.thermo_nasa9_interval USING btree (thermo_id);
CREATE UNIQUE INDEX ix_thermo_public_ref ON public.thermo USING btree (public_ref);
CREATE INDEX ix_thermo_statmech_id ON public.thermo USING btree (statmech_id);
CREATE INDEX ix_thermo_target_conformer_group_id ON public.thermo USING btree (target_conformer_group_id);
CREATE UNIQUE INDEX ix_transition_state_entry_public_ref ON public.transition_state_entry USING btree (public_ref);
CREATE UNIQUE INDEX ix_transition_state_public_ref ON public.transition_state USING btree (public_ref);
CREATE INDEX ix_transport_literature_id ON public.transport USING btree (literature_id);
CREATE UNIQUE INDEX ix_transport_public_ref ON public.transport USING btree (public_ref);
CREATE INDEX ix_ts_validation_energy_source_calc ON public.transition_state_validation_energy USING btree (source_calculation_id);
CREATE INDEX ix_upload_job_status_created_at ON public.upload_job USING btree (status, created_at);
CREATE INDEX ix_upload_job_status_lease_expires_at ON public.upload_job USING btree (status, lease_expires_at);
CREATE UNIQUE INDEX ix_workflow_tool_public_ref ON public.workflow_tool USING btree (public_ref);
CREATE UNIQUE INDEX ix_workflow_tool_release_public_ref ON public.workflow_tool_release USING btree (public_ref);
CREATE UNIQUE INDEX uq_applied_energy_correction_dedup ON public.applied_energy_correction USING btree (target_species_entry_id, target_reaction_entry_id, target_transition_state_entry_id, source_conformer_observation_id, scheme_id, frequency_scale_factor_id, application_role, temperature_k, source_calculation_id) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX uq_calc_composite_input_slot ON public.calc_composite_input USING btree (calculation_id, term_position, slot, cardinal_number) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX uq_calculation_dependency_child_calculation_id_freq_on ON public.calculation_dependency USING btree (child_calculation_id) WHERE (dependency_role = 'freq_on'::public.calculation_dependency_role);
CREATE UNIQUE INDEX uq_calculation_dependency_child_calculation_id_optimized_from ON public.calculation_dependency USING btree (child_calculation_id) WHERE (dependency_role = 'optimized_from'::public.calculation_dependency_role);
CREATE UNIQUE INDEX uq_calculation_dependency_child_calculation_id_scan_parent ON public.calculation_dependency USING btree (child_calculation_id) WHERE (dependency_role = 'scan_parent'::public.calculation_dependency_role);
CREATE UNIQUE INDEX uq_calculation_dependency_child_calculation_id_single_point_on ON public.calculation_dependency USING btree (child_calculation_id) WHERE (dependency_role = 'single_point_on'::public.calculation_dependency_role);
CREATE UNIQUE INDEX uq_chem_reaction_stoichiometry_hash ON public.chem_reaction USING btree (stoichiometry_hash) WHERE (merged_into_chem_reaction_id IS NULL);
CREATE UNIQUE INDEX uq_composite_scheme_term_input_term_slot_cardinal ON public.composite_scheme_term_input USING btree (term_id, slot, cardinal_number) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX uq_conformer_group_merge_pending_open_refile ON public.conformer_group_merge_pending USING btree (conformer_observation_id) WHERE ((kept_conformer_group_id IS NULL) AND (completed_at IS NULL));
CREATE UNIQUE INDEX uq_energy_correction_scheme_identity ON public.energy_correction_scheme USING btree (kind, name, level_of_theory_id, frequency_level_of_theory_id, source_literature_id, software_release_id, workflow_tool_release_id) NULLS NOT DISTINCT WHERE (data_revision IS NULL);
CREATE UNIQUE INDEX uq_energy_correction_scheme_identity_revised ON public.energy_correction_scheme USING btree (kind, name, level_of_theory_id, frequency_level_of_theory_id, source_literature_id, software_release_id, data_revision) NULLS NOT DISTINCT WHERE (data_revision IS NOT NULL);
CREATE UNIQUE INDEX uq_frequency_scale_factor_identity ON public.frequency_scale_factor USING btree (level_of_theory_id, software_release_id, scale_kind, value, source_literature_id, workflow_tool_release_id) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX uq_group_additivity_scheme_name_version ON public.group_additivity_scheme USING btree (name, version) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX uq_network_channel_microreaction_barrierless ON public.network_channel_microreaction USING btree (channel_id, reaction_entry_id) WHERE (transition_state_entry_id IS NULL);
CREATE UNIQUE INDEX uq_network_kinetics_representation_key ON public.network_kinetics USING btree (determination_id, ((representation_declaration ->> 'key'::text))) WHERE (determination_id IS NOT NULL);
CREATE UNIQUE INDEX uq_network_solve_energy_transfer_network_wide ON public.network_solve_energy_transfer USING btree (solve_id) WHERE (scope = 'network_wide'::public.network_energy_transfer_scope);
CREATE UNIQUE INDEX uq_software_release_software_id ON public.software_release USING btree (software_id, version, revision, build) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX uq_species_entry_species_id ON public.species_entry USING btree (species_id, stereo_descriptor, electronic_state_kind, electronic_state_label, term_symbol, isotope_key) NULLS NOT DISTINCT WHERE (merged_into_species_entry_id IS NULL);
CREATE UNIQUE INDEX uq_species_identity ON public.species USING btree (smiles, charge, multiplicity) WHERE (merged_into_species_id IS NULL);
CREATE UNIQUE INDEX uq_species_name_species_id_name_key_live ON public.species_name USING btree (species_id, name_key) WHERE (retired_at IS NULL);
CREATE UNIQUE INDEX uq_statmech_torsion_statmech_id_torsion_index ON public.statmech_torsion USING btree (statmech_id, torsion_index);
CREATE UNIQUE INDEX uq_workflow_tool_release_workflow_tool_id ON public.workflow_tool_release USING btree (workflow_tool_id, version, git_commit) NULLS NOT DISTINCT;
CREATE CONSTRAINT TRIGGER ct_network_solve_channel_barrier_computed_evidence AFTER DELETE OR UPDATE ON public.network_solve_channel_barrier DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.network_solve_computed_evidence();
CREATE CONSTRAINT TRIGGER ct_network_solve_computed_evidence AFTER INSERT OR UPDATE ON public.network_solve DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.network_solve_computed_evidence();
CREATE CONSTRAINT TRIGGER ct_network_solve_energy_transfer_computed_evidence AFTER DELETE OR UPDATE ON public.network_solve_energy_transfer DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.network_solve_computed_evidence();
CREATE CONSTRAINT TRIGGER ct_network_solve_state_energy_computed_evidence AFTER DELETE OR UPDATE ON public.network_solve_state_energy DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.network_solve_computed_evidence();
CREATE TRIGGER network_solve_channel_barrier_computed_evidence_truncate BEFORE TRUNCATE ON public.network_solve_channel_barrier FOR EACH STATEMENT EXECUTE FUNCTION public.network_solve_computed_evidence_truncate();
CREATE TRIGGER network_solve_energy_transfer_computed_evidence_truncate BEFORE TRUNCATE ON public.network_solve_energy_transfer FOR EACH STATEMENT EXECUTE FUNCTION public.network_solve_computed_evidence_truncate();
CREATE TRIGGER network_solve_state_energy_computed_evidence_truncate BEFORE TRUNCATE ON public.network_solve_state_energy FOR EACH STATEMENT EXECUTE FUNCTION public.network_solve_computed_evidence_truncate();
CREATE TRIGGER trg_accepted_science_repair_change_validate BEFORE INSERT ON public.accepted_science_repair_change FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_accepted_science_repair_change();
CREATE TRIGGER trg_accepted_science_repair_validate BEFORE INSERT ON public.accepted_science_repair FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_accepted_science_repair();
CREATE TRIGGER trg_append_only_accepted_science_repair BEFORE DELETE OR UPDATE ON public.accepted_science_repair FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation();
CREATE TRIGGER trg_append_only_accepted_science_repair_change BEFORE DELETE OR UPDATE ON public.accepted_science_repair_change FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation();
CREATE TRIGGER trg_append_only_record_review_event BEFORE DELETE OR UPDATE ON public.record_review_event FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation();
CREATE TRIGGER trg_append_only_scientific_record_supersession BEFORE DELETE OR UPDATE ON public.scientific_record_supersession FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation();
CREATE TRIGGER trg_as_child_00 BEFORE INSERT OR DELETE OR UPDATE ON public.calculation_input_geometry FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_01 BEFORE INSERT OR DELETE OR UPDATE ON public.calculation_output_geometry FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_02 BEFORE INSERT OR DELETE OR UPDATE ON public.calculation_dependency FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'parent_calculation_id', 'child_calculation_id');
CREATE TRIGGER trg_as_child_03 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_sp_result FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_04 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_opt_result FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_05 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_freq_result FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_06 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_freq_mode FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_07 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_scan_result FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_08 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_scan_coordinate FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_09 BEFORE INSERT OR DELETE OR UPDATE ON public.calculation_constraint FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_10 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_scan_point FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_11 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_scan_point_coordinate_value FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_12 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_irc_result FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_13 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_irc_point FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_14 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_path_search_result FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_15 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_path_search_point FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_16 BEFORE INSERT OR DELETE OR UPDATE ON public.calculation_artifact FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_17 BEFORE INSERT OR DELETE OR UPDATE ON public.calculation_parameter FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_18 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_geometry_validation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_19 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_scf_stability FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_20 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_hessian FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_21 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_wavefunction_diagnostic FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_22 BEFORE INSERT OR DELETE OR UPDATE ON public.calc_spin_diagnostic FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_23 BEFORE INSERT OR DELETE OR UPDATE ON public.thermo_point FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('thermo', 'thermo_id');
CREATE TRIGGER trg_as_child_24 BEFORE INSERT OR DELETE OR UPDATE ON public.thermo_nasa FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('thermo', 'thermo_id');
CREATE TRIGGER trg_as_child_25 BEFORE INSERT OR DELETE OR UPDATE ON public.thermo_nasa9_interval FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('thermo', 'thermo_id');
CREATE TRIGGER trg_as_child_26 BEFORE INSERT OR DELETE OR UPDATE ON public.thermo_wilhoit FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('thermo', 'thermo_id');
CREATE TRIGGER trg_as_child_27 BEFORE INSERT OR DELETE OR UPDATE ON public.thermo_source_calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('thermo', 'thermo_id');
CREATE TRIGGER trg_as_child_28 BEFORE INSERT OR DELETE OR UPDATE ON public.applied_group_additivity FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('thermo', 'thermo_id');
CREATE TRIGGER trg_as_child_29 BEFORE INSERT OR DELETE OR UPDATE ON public.statmech_electronic_level FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('statmech', 'statmech_id');
CREATE TRIGGER trg_as_child_30 BEFORE INSERT OR DELETE OR UPDATE ON public.statmech_source_calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('statmech', 'statmech_id');
CREATE TRIGGER trg_as_child_31 BEFORE INSERT OR DELETE OR UPDATE ON public.statmech_torsion FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('statmech', 'statmech_id');
CREATE TRIGGER trg_as_child_32 BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_source_calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_33 BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_falloff FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_34 BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_third_body_efficiency FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_35 BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_plog FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_36 BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_arrhenius_entry FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_37 BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_chebyshev FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_38 BEFORE INSERT OR DELETE OR UPDATE ON public.transport_source_calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('transport', 'transport_id');
CREATE TRIGGER trg_as_child_39 BEFORE INSERT OR DELETE OR UPDATE ON public.network_reaction FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network', 'network_id');
CREATE TRIGGER trg_as_child_40 BEFORE INSERT OR DELETE OR UPDATE ON public.network_species FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network', 'network_id');
CREATE TRIGGER trg_as_child_41 BEFORE INSERT OR DELETE OR UPDATE ON public.network_state FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network', 'network_id');
CREATE TRIGGER trg_as_child_42 BEFORE INSERT OR DELETE OR UPDATE ON public.network_channel FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network', 'network_id');
CREATE TRIGGER trg_as_child_43 BEFORE INSERT OR DELETE OR UPDATE ON public.network_solve_bath_gas FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_44 BEFORE INSERT OR DELETE OR UPDATE ON public.network_solve_energy_transfer FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_45 BEFORE INSERT OR DELETE OR UPDATE ON public.network_solve_source_calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_46 BEFORE INSERT OR DELETE OR UPDATE ON public.network_kinetics FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_47 BEFORE INSERT OR DELETE OR UPDATE ON public.applied_energy_correction_component FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('applied_energy_correction', 'applied_correction_id');
CREATE TRIGGER trg_as_child_calc_composite_input BEFORE INSERT OR DELETE OR UPDATE ON public.calc_composite_input FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_calc_composite_log_check BEFORE INSERT OR DELETE OR UPDATE ON public.calc_composite_log_check FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_calc_composite_result BEFORE INSERT OR DELETE OR UPDATE ON public.calc_composite_result FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_calc_composite_term BEFORE INSERT OR DELETE OR UPDATE ON public.calc_composite_term FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_calc_sp_energy_component BEFORE INSERT OR DELETE OR UPDATE ON public.calc_sp_energy_component FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('calculation', 'calculation_id');
CREATE TRIGGER trg_as_child_kinetics_bath_gas BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_bath_gas FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_kinetics_interpretation_assignment BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_interpretation_assignment FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_kinetics_tunneling_application BEFORE INSERT OR DELETE OR UPDATE ON public.kinetics_tunneling_application FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('kinetics', 'kinetics_id');
CREATE TRIGGER trg_as_child_network_kinetics_determination BEFORE INSERT OR DELETE OR UPDATE ON public.network_kinetics_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_network_solve_channel_barrier BEFORE INSERT OR DELETE OR UPDATE ON public.network_solve_channel_barrier FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_network_solve_state_energy BEFORE INSERT OR DELETE OR UPDATE ON public.network_solve_state_energy FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_network_solve_state_energy_source BEFORE INSERT OR DELETE OR UPDATE ON public.network_solve_state_energy_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('network_solve', 'solve_id');
CREATE TRIGGER trg_as_child_reaction_atom_map BEFORE INSERT OR DELETE OR UPDATE ON public.reaction_atom_map FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('transition_state_entry', 'transition_state_entry_id');
CREATE TRIGGER trg_as_child_statmech_model_declaration BEFORE INSERT OR DELETE OR UPDATE ON public.statmech_model_declaration FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('statmech', 'statmech_id');
CREATE TRIGGER trg_as_child_transition_state_validation_evidence BEFORE INSERT OR DELETE OR UPDATE ON public.transition_state_validation_evidence FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('transition_state_entry', 'transition_state_entry_id');
CREATE TRIGGER trg_as_geometry BEFORE DELETE OR UPDATE ON public.geometry FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_calculation_geometry();
CREATE TRIGGER trg_as_geometry_atom BEFORE INSERT OR DELETE OR UPDATE ON public.geometry_atom FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_calculation_geometry();
CREATE TRIGGER trg_as_root_applied_energy_correction BEFORE DELETE OR UPDATE ON public.applied_energy_correction FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('applied_energy_correction');
CREATE TRIGGER trg_as_root_calculation BEFORE DELETE OR UPDATE ON public.calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('calculation');
CREATE TRIGGER trg_as_root_conformer_observation BEFORE DELETE OR UPDATE ON public.conformer_observation FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('conformer_observation');
CREATE TRIGGER trg_as_root_kinetics BEFORE DELETE OR UPDATE ON public.kinetics FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('kinetics');
CREATE TRIGGER trg_as_root_network BEFORE DELETE OR UPDATE ON public.network FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('network');
CREATE TRIGGER trg_as_root_network_solve BEFORE DELETE OR UPDATE ON public.network_solve FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('network_solve');
CREATE TRIGGER trg_as_root_statmech BEFORE DELETE OR UPDATE ON public.statmech FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('statmech');
CREATE TRIGGER trg_as_root_thermo BEFORE DELETE OR UPDATE ON public.thermo FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('thermo');
CREATE TRIGGER trg_as_root_transition_state_entry BEFORE DELETE OR UPDATE ON public.transition_state_entry FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('transition_state_entry');
CREATE TRIGGER trg_as_root_transport BEFORE DELETE OR UPDATE ON public.transport FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_root('transport');
CREATE TRIGGER trg_as_truncate_00 BEFORE TRUNCATE ON public.applied_energy_correction FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_01 BEFORE TRUNCATE ON public.applied_energy_correction_component FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_02 BEFORE TRUNCATE ON public.applied_group_additivity FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_03 BEFORE TRUNCATE ON public.applied_group_additivity_component FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_04 BEFORE TRUNCATE ON public.calc_freq_mode FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_05 BEFORE TRUNCATE ON public.calc_freq_result FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_06 BEFORE TRUNCATE ON public.calc_geometry_validation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_07 BEFORE TRUNCATE ON public.calc_hessian FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_08 BEFORE TRUNCATE ON public.calc_irc_point FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_09 BEFORE TRUNCATE ON public.calc_irc_result FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_10 BEFORE TRUNCATE ON public.calc_opt_result FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_11 BEFORE TRUNCATE ON public.calc_path_search_point FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_12 BEFORE TRUNCATE ON public.calc_path_search_result FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_13 BEFORE TRUNCATE ON public.calc_scan_coordinate FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_14 BEFORE TRUNCATE ON public.calc_scan_point FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_15 BEFORE TRUNCATE ON public.calc_scan_point_coordinate_value FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_16 BEFORE TRUNCATE ON public.calc_scan_result FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_17 BEFORE TRUNCATE ON public.calc_scf_stability FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_18 BEFORE TRUNCATE ON public.calc_sp_result FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_19 BEFORE TRUNCATE ON public.calc_spin_diagnostic FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_20 BEFORE TRUNCATE ON public.calc_wavefunction_diagnostic FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_21 BEFORE TRUNCATE ON public.calculation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_22 BEFORE TRUNCATE ON public.calculation_artifact FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_23 BEFORE TRUNCATE ON public.calculation_constraint FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_24 BEFORE TRUNCATE ON public.calculation_dependency FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_25 BEFORE TRUNCATE ON public.calculation_input_geometry FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_26 BEFORE TRUNCATE ON public.calculation_output_geometry FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_27 BEFORE TRUNCATE ON public.calculation_parameter FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_28 BEFORE TRUNCATE ON public.conformer_observation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_29 BEFORE TRUNCATE ON public.geometry FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_30 BEFORE TRUNCATE ON public.geometry_atom FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_31 BEFORE TRUNCATE ON public.kinetics FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_32 BEFORE TRUNCATE ON public.kinetics_arrhenius_entry FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_33 BEFORE TRUNCATE ON public.kinetics_chebyshev FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_34 BEFORE TRUNCATE ON public.kinetics_falloff FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_35 BEFORE TRUNCATE ON public.kinetics_plog FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_36 BEFORE TRUNCATE ON public.kinetics_source_calculation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_37 BEFORE TRUNCATE ON public.kinetics_third_body_efficiency FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_38 BEFORE TRUNCATE ON public.network FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_39 BEFORE TRUNCATE ON public.network_channel FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_40 BEFORE TRUNCATE ON public.network_kinetics FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_41 BEFORE TRUNCATE ON public.network_kinetics_chebyshev FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_42 BEFORE TRUNCATE ON public.network_kinetics_plog FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_43 BEFORE TRUNCATE ON public.network_kinetics_point FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_44 BEFORE TRUNCATE ON public.network_reaction FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_45 BEFORE TRUNCATE ON public.network_solve FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_46 BEFORE TRUNCATE ON public.network_solve_bath_gas FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_47 BEFORE TRUNCATE ON public.network_solve_energy_transfer FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_48 BEFORE TRUNCATE ON public.network_solve_source_calculation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_49 BEFORE TRUNCATE ON public.network_species FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_50 BEFORE TRUNCATE ON public.network_state FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_51 BEFORE TRUNCATE ON public.network_state_participant FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_52 BEFORE TRUNCATE ON public.record_reproducibility_assessment FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_53 BEFORE TRUNCATE ON public.record_review FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_54 BEFORE TRUNCATE ON public.record_review_event FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_55 BEFORE TRUNCATE ON public.scientific_record_supersession FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_56 BEFORE TRUNCATE ON public.statmech FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_57 BEFORE TRUNCATE ON public.statmech_electronic_level FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_58 BEFORE TRUNCATE ON public.statmech_source_calculation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_59 BEFORE TRUNCATE ON public.statmech_torsion FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_60 BEFORE TRUNCATE ON public.statmech_torsion_definition FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_61 BEFORE TRUNCATE ON public.thermo FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_62 BEFORE TRUNCATE ON public.thermo_nasa FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_63 BEFORE TRUNCATE ON public.thermo_nasa9_interval FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_64 BEFORE TRUNCATE ON public.thermo_point FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_65 BEFORE TRUNCATE ON public.thermo_source_calculation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_66 BEFORE TRUNCATE ON public.thermo_wilhoit FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_67 BEFORE TRUNCATE ON public.transition_state_entry FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_68 BEFORE TRUNCATE ON public.transport FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_69 BEFORE TRUNCATE ON public.transport_source_calculation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_accepted_science_repair BEFORE TRUNCATE ON public.accepted_science_repair FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_accepted_science_repair_change BEFORE TRUNCATE ON public.accepted_science_repair_change FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_calc_composite_input BEFORE TRUNCATE ON public.calc_composite_input FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_calc_composite_log_check BEFORE TRUNCATE ON public.calc_composite_log_check FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_calc_composite_result BEFORE TRUNCATE ON public.calc_composite_result FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_calc_composite_term BEFORE TRUNCATE ON public.calc_composite_term FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_calc_sp_energy_component BEFORE TRUNCATE ON public.calc_sp_energy_component FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_kinetics_bath_gas BEFORE TRUNCATE ON public.kinetics_bath_gas FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_kinetics_interpretation_assignment BEFORE TRUNCATE ON public.kinetics_interpretation_assignment FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_kinetics_tunneling_application BEFORE TRUNCATE ON public.kinetics_tunneling_application FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_network_kinetics_determination BEFORE TRUNCATE ON public.network_kinetics_determination FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_network_solve_channel_barrier BEFORE TRUNCATE ON public.network_solve_channel_barrier FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_network_solve_state_energy BEFORE TRUNCATE ON public.network_solve_state_energy FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_network_solve_state_energy_source BEFORE TRUNCATE ON public.network_solve_state_energy_source FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_reaction_atom_map BEFORE TRUNCATE ON public.reaction_atom_map FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_reaction_atom_map_pair BEFORE TRUNCATE ON public.reaction_atom_map_pair FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_statmech_model_declaration BEFORE TRUNCATE ON public.statmech_model_declaration FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_statmech_model_member BEFORE TRUNCATE ON public.statmech_model_member FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_statmech_model_source BEFORE TRUNCATE ON public.statmech_model_source FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_transition_state_validation_energy BEFORE TRUNCATE ON public.transition_state_validation_energy FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_truncate_transition_state_validation_evidence BEFORE TRUNCATE ON public.transition_state_validation_evidence FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_as_via_00 BEFORE INSERT OR DELETE OR UPDATE ON public.applied_group_additivity_component FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('thermo', 'applied_group_additivity_id', 'applied_group_additivity', 'id', 'thermo_id');
CREATE TRIGGER trg_as_via_01 BEFORE INSERT OR DELETE OR UPDATE ON public.statmech_torsion_definition FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('statmech', 'torsion_id', 'statmech_torsion', 'id', 'statmech_id');
CREATE TRIGGER trg_as_via_02 BEFORE INSERT OR DELETE OR UPDATE ON public.network_state_participant FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('network', 'state_id', 'network_state', 'id', 'network_id');
CREATE TRIGGER trg_as_via_03 BEFORE INSERT OR DELETE OR UPDATE ON public.network_kinetics_chebyshev FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('network_solve', 'network_kinetics_id', 'network_kinetics', 'id', 'solve_id');
CREATE TRIGGER trg_as_via_04 BEFORE INSERT OR DELETE OR UPDATE ON public.network_kinetics_plog FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('network_solve', 'network_kinetics_id', 'network_kinetics', 'id', 'solve_id');
CREATE TRIGGER trg_as_via_05 BEFORE INSERT OR DELETE OR UPDATE ON public.network_kinetics_point FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('network_solve', 'network_kinetics_id', 'network_kinetics', 'id', 'solve_id');
CREATE TRIGGER trg_as_via_reaction_atom_map_pair BEFORE INSERT OR DELETE OR UPDATE ON public.reaction_atom_map_pair FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('transition_state_entry', 'atom_map_id', 'reaction_atom_map', 'id', 'transition_state_entry_id');
CREATE TRIGGER trg_as_via_statmech_model_member BEFORE INSERT OR DELETE OR UPDATE ON public.statmech_model_member FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('statmech', 'declaration_id', 'statmech_model_declaration', 'id', 'statmech_id');
CREATE TRIGGER trg_as_via_statmech_model_source BEFORE INSERT OR DELETE OR UPDATE ON public.statmech_model_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('statmech', 'declaration_id', 'statmech_model_declaration', 'id', 'statmech_id');
CREATE TRIGGER trg_as_via_transition_state_validation_energy BEFORE INSERT OR DELETE OR UPDATE ON public.transition_state_validation_energy FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('transition_state_entry', 'evidence_id', 'transition_state_validation_evidence', 'id', 'transition_state_entry_id');
CREATE TRIGGER trg_calculation_dependency_acyclic BEFORE INSERT OR UPDATE OF parent_calculation_id, child_calculation_id ON public.calculation_dependency FOR EACH ROW EXECUTE FUNCTION public.tckdb_calculation_dependency_acyclic();
CREATE TRIGGER trg_calculation_dependency_acyclic_lock BEFORE INSERT OR UPDATE OF parent_calculation_id, child_calculation_id ON public.calculation_dependency FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_calculation_dependency_acyclic_lock();
CREATE TRIGGER trg_calculation_execution_environment_binding BEFORE INSERT OR UPDATE OF execution_environment_manifest_id, software_release_id, workflow_tool_release_id ON public.calculation FOR EACH ROW EXECUTE FUNCTION public.validate_calculation_execution_environment_binding();
CREATE TRIGGER trg_calculation_lot_not_merged BEFORE INSERT OR UPDATE OF lot_id ON public.calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_calculation_lot_not_merged();
CREATE TRIGGER trg_chem_reaction_freeze_merged BEFORE UPDATE ON public.chem_reaction FOR EACH ROW EXECUTE FUNCTION public.tckdb_freeze_merged_row('merged_into_chem_reaction_id');
CREATE TRIGGER trg_chem_reaction_merge_marker BEFORE INSERT OR UPDATE OF merged_into_chem_reaction_id ON public.chem_reaction FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_merge_marker('merged_into_chem_reaction_id');
CREATE TRIGGER trg_deposit_warning_append_only BEFORE DELETE OR UPDATE ON public.deposit_warning FOR EACH ROW EXECUTE FUNCTION public.reject_deposit_warning_mutation();
CREATE TRIGGER trg_deposit_warning_no_truncate BEFORE TRUNCATE ON public.deposit_warning FOR EACH STATEMENT EXECUTE FUNCTION public.reject_deposit_warning_mutation();
CREATE TRIGGER trg_deposit_warning_retirement_append_only BEFORE DELETE OR UPDATE ON public.deposit_warning_retirement FOR EACH ROW EXECUTE FUNCTION public.reject_deposit_warning_retirement_mutation();
CREATE TRIGGER trg_deposit_warning_retirement_no_truncate BEFORE TRUNCATE ON public.deposit_warning_retirement FOR EACH STATEMENT EXECUTE FUNCTION public.reject_deposit_warning_retirement_mutation();
CREATE TRIGGER trg_execution_environment_manifest_immutable BEFORE DELETE OR UPDATE ON public.execution_environment_manifest FOR EACH ROW EXECUTE FUNCTION public.reject_execution_environment_manifest_mutation();
CREATE TRIGGER trg_guard_record_review BEFORE INSERT OR DELETE OR UPDATE ON public.record_review FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_record_review();
CREATE TRIGGER trg_guard_thermo_enthalpy_reference BEFORE INSERT OR UPDATE ON public.thermo FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_thermo_enthalpy_reference();
CREATE TRIGGER trg_kinetics_determination_immutable BEFORE UPDATE ON public.kinetics_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_kinetics_determination_immutable();
CREATE TRIGGER trg_lot_merge_guard BEFORE INSERT OR UPDATE ON public.level_of_theory_merge FOR EACH ROW EXECUTE FUNCTION public.tckdb_lot_merge_guard();
CREATE TRIGGER trg_mt_applied_energy_correction_target_species_entry_id BEFORE INSERT OR UPDATE OF target_species_entry_id ON public.applied_energy_correction FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'target_species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_calculation_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_conformer_group_merge_pending_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.conformer_group_merge_pending FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_conformer_group_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.conformer_group FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_kinetics_bath_gas_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.kinetics_bath_gas FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_kinetics_third_body_efficiency_collider_species_id BEFORE INSERT OR UPDATE OF collider_species_id ON public.kinetics_third_body_efficiency FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species', 'collider_species_id', 'merged_into_species_id');
CREATE TRIGGER trg_mt_molecular_property_observation_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.molecular_property_observation FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_network_solve_bath_gas_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.network_solve_bath_gas FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_network_solve_energy_transfer_collider_species_entry_id BEFORE INSERT OR UPDATE OF collider_species_entry_id ON public.network_solve_energy_transfer FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'collider_species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_network_solve_state_energy_source_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.network_solve_state_energy_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_network_species_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.network_species FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_network_state_participant_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.network_state_participant FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_reaction_entry_reaction_id BEFORE INSERT OR UPDATE OF reaction_id ON public.reaction_entry FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('chem_reaction', 'reaction_id', 'merged_into_chem_reaction_id');
CREATE TRIGGER trg_mt_reaction_entry_structure_participant_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.reaction_entry_structure_participant FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_reaction_participant_reaction_id BEFORE INSERT OR UPDATE OF reaction_id ON public.reaction_participant FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('chem_reaction', 'reaction_id', 'merged_into_chem_reaction_id');
CREATE TRIGGER trg_mt_reaction_participant_species_id BEFORE INSERT OR UPDATE OF species_id ON public.reaction_participant FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species', 'species_id', 'merged_into_species_id');
CREATE TRIGGER trg_mt_species_entry_review_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.species_entry_review FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_species_entry_species_id BEFORE INSERT OR UPDATE OF species_id ON public.species_entry FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species', 'species_id', 'merged_into_species_id');
CREATE TRIGGER trg_mt_species_name_species_id BEFORE INSERT OR UPDATE OF species_id ON public.species_name FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species', 'species_id', 'merged_into_species_id');
CREATE TRIGGER trg_mt_statmech_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.statmech FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_structure_determination_source_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.structure_determination_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_structure_determination_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.structure_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_thermo_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.thermo FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_mt_transport_species_entry_id BEFORE INSERT OR UPDATE OF species_entry_id ON public.transport FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('species_entry', 'species_entry_id', 'merged_into_species_entry_id');
CREATE TRIGGER trg_network_kinetics_determination_immutable BEFORE UPDATE ON public.network_kinetics_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_network_kinetics_determination_immutable();
CREATE TRIGGER trg_reaction_atom_map_source_immutable BEFORE UPDATE ON public.reaction_atom_map FOR EACH ROW EXECUTE FUNCTION public.tckdb_reaction_atom_map_source_immutable();
CREATE TRIGGER trg_release_artifact_immutable BEFORE DELETE OR UPDATE ON public.release_artifact FOR EACH ROW EXECUTE FUNCTION public.reject_release_artifact_mutation();
CREATE TRIGGER trg_release_artifact_immutable_truncate BEFORE TRUNCATE ON public.release_artifact FOR EACH STATEMENT EXECUTE FUNCTION public.reject_release_artifact_mutation();
CREATE TRIGGER trg_release_manifest_immutable BEFORE DELETE OR UPDATE ON public.release_manifest FOR EACH ROW EXECUTE FUNCTION public.reject_release_manifest_mutation();
CREATE TRIGGER trg_release_manifest_immutable_truncate BEFORE TRUNCATE ON public.release_manifest FOR EACH STATEMENT EXECUTE FUNCTION public.reject_release_manifest_mutation();
CREATE TRIGGER trg_release_selection_append_only BEFORE DELETE OR UPDATE ON public.release_selection FOR EACH ROW EXECUTE FUNCTION public.reject_release_selection_mutation();
CREATE TRIGGER trg_release_selection_append_only_truncate BEFORE TRUNCATE ON public.release_selection FOR EACH STATEMENT EXECUTE FUNCTION public.reject_release_selection_mutation();
CREATE TRIGGER trg_repro_assessment_append_only BEFORE DELETE OR UPDATE ON public.record_reproducibility_assessment FOR EACH ROW EXECUTE FUNCTION public.reject_reproducibility_assessment_mutation();
CREATE TRIGGER trg_scientific_supersession_validate BEFORE INSERT ON public.scientific_record_supersession FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_scientific_supersession();
CREATE TRIGGER trg_species_entry_freeze_merged BEFORE UPDATE ON public.species_entry FOR EACH ROW EXECUTE FUNCTION public.tckdb_freeze_merged_row('merged_into_species_entry_id');
CREATE TRIGGER trg_species_entry_merge_marker BEFORE INSERT OR UPDATE OF merged_into_species_entry_id ON public.species_entry FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_merge_marker('merged_into_species_entry_id');
CREATE TRIGGER trg_species_freeze_merged BEFORE UPDATE ON public.species FOR EACH ROW EXECUTE FUNCTION public.tckdb_freeze_merged_row('merged_into_species_id');
CREATE TRIGGER trg_species_merge_marker BEFORE INSERT OR UPDATE OF merged_into_species_id ON public.species FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_merge_marker('merged_into_species_id');
CREATE TRIGGER trg_statmech_model_declaration_guard BEFORE INSERT ON public.statmech_model_declaration FOR EACH ROW EXECUTE FUNCTION public.tckdb_statmech_model_declaration_guard();
CREATE TRIGGER trg_statmech_model_declaration_immutable BEFORE UPDATE ON public.statmech_model_declaration FOR EACH ROW EXECUTE FUNCTION public.tckdb_statmech_model_immutable();
CREATE TRIGGER trg_statmech_model_member_guard BEFORE INSERT ON public.statmech_model_member FOR EACH ROW EXECUTE FUNCTION public.tckdb_statmech_model_member_guard();
CREATE TRIGGER trg_statmech_model_member_immutable BEFORE UPDATE ON public.statmech_model_member FOR EACH ROW EXECUTE FUNCTION public.tckdb_statmech_model_immutable();
CREATE TRIGGER trg_statmech_model_source_guard BEFORE INSERT ON public.statmech_model_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_statmech_model_source_guard();
CREATE TRIGGER trg_statmech_model_source_immutable BEFORE UPDATE ON public.statmech_model_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_statmech_model_immutable();
CREATE TRIGGER trg_structure_determination_accepted_observation BEFORE INSERT OR DELETE OR UPDATE ON public.structure_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('conformer_observation', 'conformer_observation_id');
CREATE TRIGGER trg_structure_determination_accepted_ts_entry BEFORE INSERT OR DELETE OR UPDATE ON public.structure_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('transition_state_entry', 'transition_state_entry_id');
CREATE TRIGGER trg_structure_determination_guard BEFORE INSERT ON public.structure_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_structure_determination_guard();
CREATE TRIGGER trg_structure_determination_immutable BEFORE UPDATE ON public.structure_determination FOR EACH ROW EXECUTE FUNCTION public.tckdb_structure_row_immutable();
CREATE TRIGGER trg_structure_determination_source_accepted_observation BEFORE INSERT OR DELETE OR UPDATE ON public.structure_determination_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('conformer_observation', 'determination_id', 'structure_determination', 'id', 'conformer_observation_id');
CREATE TRIGGER trg_structure_determination_source_accepted_ts_entry BEFORE INSERT OR DELETE OR UPDATE ON public.structure_determination_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child('transition_state_entry', 'determination_id', 'structure_determination', 'id', 'transition_state_entry_id');
CREATE TRIGGER trg_structure_determination_source_delete_guard BEFORE DELETE ON public.structure_determination_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_structure_source_delete_guard();
CREATE TRIGGER trg_structure_determination_source_immutable BEFORE UPDATE ON public.structure_determination_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_structure_row_immutable();
CREATE TRIGGER trg_structure_determination_source_insert_guard BEFORE INSERT ON public.structure_determination_source FOR EACH ROW EXECUTE FUNCTION public.tckdb_structure_source_insert_guard();
CREATE TRIGGER trg_structure_determination_source_truncate BEFORE TRUNCATE ON public.structure_determination_source FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_structure_determination_truncate BEFORE TRUNCATE ON public.structure_determination FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_structure_evidence_finding_append_only BEFORE DELETE OR UPDATE ON public.structure_evidence_finding FOR EACH ROW EXECUTE FUNCTION public.tckdb_structure_finding_append_only();
CREATE TRIGGER trg_structure_evidence_finding_truncate BEFORE TRUNCATE ON public.structure_evidence_finding FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_submission_rights_attestation_immutable BEFORE DELETE OR UPDATE ON public.submission_rights_attestation FOR EACH ROW EXECUTE FUNCTION public.reject_submission_rights_attestation_mutation();
CREATE TRIGGER trg_submission_rights_attestation_immutable_truncate BEFORE TRUNCATE ON public.submission_rights_attestation FOR EACH STATEMENT EXECUTE FUNCTION public.reject_submission_rights_attestation_mutation();
CREATE TRIGGER trg_sva_append_only BEFORE DELETE OR UPDATE ON public.software_version_attestation FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation();
CREATE TRIGGER trg_sva_calculation_append_only BEFORE DELETE OR UPDATE ON public.software_version_attestation_calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation();
CREATE TRIGGER trg_sva_calculation_no_truncate BEFORE TRUNCATE ON public.software_version_attestation_calculation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_sva_calculation_validate BEFORE INSERT ON public.software_version_attestation_calculation FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_sva_calculation();
CREATE TRIGGER trg_sva_no_truncate BEFORE TRUNCATE ON public.software_version_attestation FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
CREATE TRIGGER trg_sva_validate BEFORE INSERT ON public.software_version_attestation FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_sva();
ALTER TABLE ONLY public.api_key
    ADD CONSTRAINT fk_api_key_user_id_app_user FOREIGN KEY (user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction_component
    ADD CONSTRAINT fk_applied_energy_correction_component_applied_correction_id FOREIGN KEY (applied_correction_id) REFERENCES public.applied_energy_correction(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_frequency_scale_factor_id FOREIGN KEY (frequency_scale_factor_id) REFERENCES public.frequency_scale_factor(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_scheme_id_energy_correction_scheme FOREIGN KEY (scheme_id) REFERENCES public.energy_correction_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_source_calculation_id_calculation FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_source_conformer_observation_id FOREIGN KEY (source_conformer_observation_id) REFERENCES public.conformer_observation(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_target_reaction_entry_id FOREIGN KEY (target_reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_target_species_entry_id FOREIGN KEY (target_species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_energy_correction
    ADD CONSTRAINT fk_applied_energy_correction_target_transition_state_entry_id FOREIGN KEY (target_transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_group_additivity_component
    ADD CONSTRAINT fk_applied_ga_component_applied_ga_id FOREIGN KEY (applied_group_additivity_id) REFERENCES public.applied_group_additivity(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_group_additivity
    ADD CONSTRAINT fk_applied_group_additivity_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_group_additivity
    ADD CONSTRAINT fk_applied_group_additivity_scheme_id_group_additivity_scheme FOREIGN KEY (scheme_id) REFERENCES public.group_additivity_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.applied_group_additivity
    ADD CONSTRAINT fk_applied_group_additivity_thermo_id_thermo FOREIGN KEY (thermo_id) REFERENCES public.thermo(id) DEFERRABLE;
ALTER TABLE ONLY public.artifact_integrity_event
    ADD CONSTRAINT fk_artifact_integrity_event_artifact_id_calculation_artifact FOREIGN KEY (artifact_id) REFERENCES public.calculation_artifact(id) DEFERRABLE;
ALTER TABLE ONLY public.artifact_integrity_event
    ADD CONSTRAINT fk_artifact_integrity_event_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.artifact_storage_capacity_event
    ADD CONSTRAINT fk_artifact_storage_capacity_event_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_composite_input
    ADD CONSTRAINT fk_calc_composite_input_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_composite_input
    ADD CONSTRAINT fk_calc_composite_input_input_calculation_id_calculation FOREIGN KEY (input_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_composite_log_check
    ADD CONSTRAINT fk_calc_composite_log_check_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_composite_result
    ADD CONSTRAINT fk_calc_composite_result_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_composite_term
    ADD CONSTRAINT fk_calc_composite_term_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_freq_mode
    ADD CONSTRAINT fk_calc_freq_mode_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_freq_result
    ADD CONSTRAINT fk_calc_freq_result_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_geometry_validation
    ADD CONSTRAINT fk_calc_geometry_validation_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_geometry_validation
    ADD CONSTRAINT fk_calc_geometry_validation_input_geometry_id_geometry FOREIGN KEY (input_geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_geometry_validation
    ADD CONSTRAINT fk_calc_geometry_validation_output_geometry_id_geometry FOREIGN KEY (output_geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_hessian
    ADD CONSTRAINT fk_calc_hessian_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_hessian
    ADD CONSTRAINT fk_calc_hessian_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_hessian
    ADD CONSTRAINT fk_calc_hessian_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_irc_point
    ADD CONSTRAINT fk_calc_irc_point_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_irc_point
    ADD CONSTRAINT fk_calc_irc_point_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_irc_result
    ADD CONSTRAINT fk_calc_irc_result_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_opt_result
    ADD CONSTRAINT fk_calc_opt_result_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_path_search_point
    ADD CONSTRAINT fk_calc_path_search_point_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_path_search_point
    ADD CONSTRAINT fk_calc_path_search_point_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_path_search_result
    ADD CONSTRAINT fk_calc_path_search_result_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scan_coordinate
    ADD CONSTRAINT fk_calc_scan_coordinate_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scan_point
    ADD CONSTRAINT fk_calc_scan_point_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scan_point
    ADD CONSTRAINT fk_calc_scan_point_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scan_result
    ADD CONSTRAINT fk_calc_scan_result_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scf_stability
    ADD CONSTRAINT fk_calc_scf_stability_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scf_stability
    ADD CONSTRAINT fk_calc_scf_stability_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scf_stability
    ADD CONSTRAINT fk_calc_scf_stability_source_artifact_id_calculation_artifact FOREIGN KEY (source_artifact_id) REFERENCES public.calculation_artifact(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scf_stability
    ADD CONSTRAINT fk_calc_scf_stability_source_calculation_id_calculation FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_sp_energy_component
    ADD CONSTRAINT fk_calc_sp_energy_component_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_sp_result
    ADD CONSTRAINT fk_calc_sp_result_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_spin_diagnostic
    ADD CONSTRAINT fk_calc_spin_diagnostic_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_spin_diagnostic
    ADD CONSTRAINT fk_calc_spin_diagnostic_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_wavefunction_diagnostic
    ADD CONSTRAINT fk_calc_wavefunction_diagnostic_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_wavefunction_diagnostic
    ADD CONSTRAINT fk_calc_wavefunction_diagnostic_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_artifact
    ADD CONSTRAINT fk_calculation_artifact_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_artifact
    ADD CONSTRAINT fk_calculation_artifact_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_conformer_observation_id_conformer_observation FOREIGN KEY (conformer_observation_id) REFERENCES public.conformer_observation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_constraint
    ADD CONSTRAINT fk_calculation_constraint_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_dependency
    ADD CONSTRAINT fk_calculation_dependency_child_calculation_id_calculation FOREIGN KEY (child_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_dependency
    ADD CONSTRAINT fk_calculation_dependency_parent_calculation_id_calculation FOREIGN KEY (parent_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_execution_environment_manifest FOREIGN KEY (execution_environment_manifest_id) REFERENCES public.execution_environment_manifest(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_input_geometry
    ADD CONSTRAINT fk_calculation_input_geometry_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_input_geometry
    ADD CONSTRAINT fk_calculation_input_geometry_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_lot_id_level_of_theory FOREIGN KEY (lot_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_output_geometry
    ADD CONSTRAINT fk_calculation_output_geometry_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_output_geometry
    ADD CONSTRAINT fk_calculation_output_geometry_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_parameter
    ADD CONSTRAINT fk_calculation_parameter_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation_parameter
    ADD CONSTRAINT fk_calculation_parameter_canonical_key FOREIGN KEY (canonical_key) REFERENCES public.calculation_parameter_vocab(canonical_key) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_software_release_id_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_transition_state_entry_id_transition_state_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.calculation
    ADD CONSTRAINT fk_calculation_workflow_tool_release_id_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.chem_reaction
    ADD CONSTRAINT fk_chem_reaction_merged_into_chem_reaction_id_chem_reaction FOREIGN KEY (merged_into_chem_reaction_id) REFERENCES public.chem_reaction(id) DEFERRABLE;
ALTER TABLE ONLY public.chem_reaction
    ADD CONSTRAINT fk_chem_reaction_reaction_family_id_reaction_family FOREIGN KEY (reaction_family_id) REFERENCES public.reaction_family(id) DEFERRABLE;
ALTER TABLE ONLY public.composite_scheme
    ADD CONSTRAINT fk_composite_scheme_frequency_level_of_theory_id FOREIGN KEY (frequency_level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.composite_scheme
    ADD CONSTRAINT fk_composite_scheme_geometry_level_of_theory_id FOREIGN KEY (geometry_level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.composite_scheme
    ADD CONSTRAINT fk_composite_scheme_source_literature_id_literature FOREIGN KEY (source_literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.composite_scheme_term_input
    ADD CONSTRAINT fk_composite_scheme_term_input_level_of_theory_id FOREIGN KEY (level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.composite_scheme_term_input
    ADD CONSTRAINT fk_composite_scheme_term_input_term_id_composite_scheme_term FOREIGN KEY (term_id) REFERENCES public.composite_scheme_term(id) DEFERRABLE;
ALTER TABLE ONLY public.composite_scheme_term
    ADD CONSTRAINT fk_composite_scheme_term_scheme_id_composite_scheme FOREIGN KEY (scheme_id) REFERENCES public.composite_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_assignment_scheme
    ADD CONSTRAINT fk_conformer_assignment_scheme_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group
    ADD CONSTRAINT fk_conformer_group_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group_merge_pending
    ADD CONSTRAINT fk_conformer_group_merge_pending_absorbed_group FOREIGN KEY (absorbed_conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group_merge_pending
    ADD CONSTRAINT fk_conformer_group_merge_pending_kept_group FOREIGN KEY (kept_conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group_merge_pending
    ADD CONSTRAINT fk_conformer_group_merge_pending_observation FOREIGN KEY (conformer_observation_id) REFERENCES public.conformer_observation(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group_merge_pending
    ADD CONSTRAINT fk_conformer_group_merge_pending_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group
    ADD CONSTRAINT fk_conformer_group_merged_into_conformer_group_id FOREIGN KEY (merged_into_conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group
    ADD CONSTRAINT fk_conformer_group_representative_observation_id FOREIGN KEY (representative_observation_id) REFERENCES public.conformer_observation(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_group
    ADD CONSTRAINT fk_conformer_group_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_observation
    ADD CONSTRAINT fk_conformer_observation_assignment_scheme_id FOREIGN KEY (assignment_scheme_id) REFERENCES public.conformer_assignment_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_observation
    ADD CONSTRAINT fk_conformer_observation_conformer_group_id_conformer_group FOREIGN KEY (conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_observation
    ADD CONSTRAINT fk_conformer_observation_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_selection
    ADD CONSTRAINT fk_conformer_selection_assignment_scheme_id FOREIGN KEY (assignment_scheme_id) REFERENCES public.conformer_assignment_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_selection
    ADD CONSTRAINT fk_conformer_selection_conformer_group_id_conformer_group FOREIGN KEY (conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.conformer_selection
    ADD CONSTRAINT fk_conformer_selection_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.calc_scan_point_coordinate_value
    ADD CONSTRAINT fk_cspcv_calc_id_coordinate_index FOREIGN KEY (calculation_id, coordinate_index) REFERENCES public.calc_scan_coordinate(calculation_id, coordinate_index) DEFERRABLE;
ALTER TABLE ONLY public.calc_scan_point_coordinate_value
    ADD CONSTRAINT fk_cspcv_calc_id_point_index FOREIGN KEY (calculation_id, point_index) REFERENCES public.calc_scan_point(calculation_id, point_index) DEFERRABLE;
ALTER TABLE ONLY public.curation_policy
    ADD CONSTRAINT fk_curation_policy_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.dataset_release
    ADD CONSTRAINT fk_dataset_release_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.dataset_release
    ADD CONSTRAINT fk_dataset_release_curation_policy_id_curation_policy FOREIGN KEY (curation_policy_id) REFERENCES public.curation_policy(id) DEFERRABLE;
ALTER TABLE ONLY public.deposit_warning_retirement
    ADD CONSTRAINT fk_deposit_warning_retirement_by FOREIGN KEY (retired_by_user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.deposit_warning_retirement
    ADD CONSTRAINT fk_deposit_warning_retirement_warning FOREIGN KEY (deposit_warning_id) REFERENCES public.deposit_warning(id) DEFERRABLE;
ALTER TABLE ONLY public.deposit_warning
    ADD CONSTRAINT fk_deposit_warning_submission FOREIGN KEY (submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.deposit_warning
    ADD CONSTRAINT fk_deposit_warning_written_by FOREIGN KEY (written_by_user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme_atom_param
    ADD CONSTRAINT fk_energy_correction_scheme_atom_param_scheme_id FOREIGN KEY (scheme_id) REFERENCES public.energy_correction_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme_bond_param
    ADD CONSTRAINT fk_energy_correction_scheme_bond_param_scheme_id FOREIGN KEY (scheme_id) REFERENCES public.energy_correction_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme_component_param
    ADD CONSTRAINT fk_energy_correction_scheme_component_param_scheme_id FOREIGN KEY (scheme_id) REFERENCES public.energy_correction_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme
    ADD CONSTRAINT fk_energy_correction_scheme_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme
    ADD CONSTRAINT fk_energy_correction_scheme_frequency_level_of_theory_id FOREIGN KEY (frequency_level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme
    ADD CONSTRAINT fk_energy_correction_scheme_level_of_theory_id_level_of_theory FOREIGN KEY (level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme
    ADD CONSTRAINT fk_energy_correction_scheme_software_release_id FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme
    ADD CONSTRAINT fk_energy_correction_scheme_source_literature_id_literature FOREIGN KEY (source_literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.energy_correction_scheme
    ADD CONSTRAINT fk_energy_correction_scheme_workflow_tool_release_id FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.execution_environment_manifest
    ADD CONSTRAINT fk_execution_environment_manifest_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.execution_environment_manifest
    ADD CONSTRAINT fk_execution_environment_manifest_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.external_source_record
    ADD CONSTRAINT fk_external_source_record_external_source_id FOREIGN KEY (external_source_id) REFERENCES public.external_source(id) DEFERRABLE;
ALTER TABLE ONLY public.frequency_scale_factor
    ADD CONSTRAINT fk_frequency_scale_factor_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.frequency_scale_factor
    ADD CONSTRAINT fk_frequency_scale_factor_level_of_theory_id_level_of_theory FOREIGN KEY (level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.frequency_scale_factor
    ADD CONSTRAINT fk_frequency_scale_factor_software_release_id FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.frequency_scale_factor
    ADD CONSTRAINT fk_frequency_scale_factor_source_literature_id_literature FOREIGN KEY (source_literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.frequency_scale_factor
    ADD CONSTRAINT fk_frequency_scale_factor_workflow_tool_release_id FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.geometry_atom
    ADD CONSTRAINT fk_geometry_atom_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id);
ALTER TABLE ONLY public.group_additivity_scheme
    ADD CONSTRAINT fk_group_additivity_scheme_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.group_additivity_scheme
    ADD CONSTRAINT fk_group_additivity_scheme_source_literature_id_literature FOREIGN KEY (source_literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.idempotency_record
    ADD CONSTRAINT fk_idempotency_record_user_id_app_user FOREIGN KEY (user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_arrhenius_entry
    ADD CONSTRAINT fk_kinetics_arrhenius_entry_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_bath_gas
    ADD CONSTRAINT fk_kinetics_bath_gas_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_bath_gas
    ADD CONSTRAINT fk_kinetics_bath_gas_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_chebyshev
    ADD CONSTRAINT fk_kinetics_chebyshev_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT fk_kinetics_determination_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_determination_id_kinetics_determination FOREIGN KEY (determination_id) REFERENCES public.kinetics_determination(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT fk_kinetics_determination_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT fk_kinetics_determination_reaction_entry_id_reaction_entry FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT fk_kinetics_determination_target_network_channel FOREIGN KEY (target_network_channel_id) REFERENCES public.network_channel(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT fk_kinetics_determination_target_ts_entry FOREIGN KEY (target_transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_determination
    ADD CONSTRAINT fk_kinetics_determination_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_energy_level_of_theory_id_level_of_theory FOREIGN KEY (energy_level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_falloff
    ADD CONSTRAINT fk_kinetics_falloff_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_interpretation_assignment
    ADD CONSTRAINT fk_kinetics_interp_conformer_selection FOREIGN KEY (conformer_selection_id) REFERENCES public.conformer_selection(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_interpretation_assignment
    ADD CONSTRAINT fk_kinetics_interp_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_interpretation_assignment
    ADD CONSTRAINT fk_kinetics_interpretation_assignment_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_interpretation_assignment
    ADD CONSTRAINT fk_kinetics_interpretation_assignment_statmech_id_statmech FOREIGN KEY (statmech_id) REFERENCES public.statmech(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_network_kinetics_id_network_kinetics FOREIGN KEY (network_kinetics_id) REFERENCES public.network_kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_plog
    ADD CONSTRAINT fk_kinetics_plog_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_reaction_entry_id_reaction_entry FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_software_release_id_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_source_calculation
    ADD CONSTRAINT fk_kinetics_source_calculation_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_source_calculation
    ADD CONSTRAINT fk_kinetics_source_calculation_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_third_body_efficiency
    ADD CONSTRAINT fk_kinetics_third_body_efficiency_collider_species_id_species FOREIGN KEY (collider_species_id) REFERENCES public.species(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_third_body_efficiency
    ADD CONSTRAINT fk_kinetics_third_body_efficiency_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_tunneling_application
    ADD CONSTRAINT fk_kinetics_tunneling_application_kinetics_id_kinetics FOREIGN KEY (kinetics_id) REFERENCES public.kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_tunneling_application
    ADD CONSTRAINT fk_kinetics_tunneling_result_artifact FOREIGN KEY (result_artifact_id) REFERENCES public.calculation_artifact(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_tunneling_application
    ADD CONSTRAINT fk_kinetics_tunneling_sct_artifact FOREIGN KEY (sct_path_integral_artifact_id) REFERENCES public.calculation_artifact(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_tunneling_application
    ADD CONSTRAINT fk_kinetics_tunneling_source_calculation FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics_tunneling_application
    ADD CONSTRAINT fk_kinetics_tunneling_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.kinetics
    ADD CONSTRAINT fk_kinetics_workflow_tool_release_id_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.level_of_theory_composite
    ADD CONSTRAINT fk_level_of_theory_composite_level_of_theory_id_level_of_theory FOREIGN KEY (level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.level_of_theory_composite
    ADD CONSTRAINT fk_level_of_theory_composite_scheme_id_composite_scheme FOREIGN KEY (scheme_id) REFERENCES public.composite_scheme(id) DEFERRABLE;
ALTER TABLE ONLY public.level_of_theory_merge
    ADD CONSTRAINT fk_level_of_theory_merge_into_lot_id_level_of_theory FOREIGN KEY (into_lot_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.level_of_theory_merge
    ADD CONSTRAINT fk_level_of_theory_merge_merged_lot_id_level_of_theory FOREIGN KEY (merged_lot_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.literature_author
    ADD CONSTRAINT fk_literature_author_author_id_author FOREIGN KEY (author_id) REFERENCES public.author(id) DEFERRABLE;
ALTER TABLE ONLY public.literature_author
    ADD CONSTRAINT fk_literature_author_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.machine_review_curator_task
    ADD CONSTRAINT fk_machine_review_curator_task_assigned_to_app_user FOREIGN KEY (assigned_to) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.machine_review_curator_task
    ADD CONSTRAINT fk_machine_review_curator_task_resolved_by_app_user FOREIGN KEY (resolved_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.machine_review_curator_task
    ADD CONSTRAINT fk_machine_review_curator_task_source_audit_event_id FOREIGN KEY (source_audit_event_id) REFERENCES public.submission_audit_event(id) DEFERRABLE;
ALTER TABLE ONLY public.machine_review_curator_task
    ADD CONSTRAINT fk_machine_review_curator_task_submission_id_submission FOREIGN KEY (submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT fk_molecular_property_observation_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT fk_molecular_property_observation_external_source_record_id FOREIGN KEY (external_source_record_id) REFERENCES public.external_source_record(id) DEFERRABLE;
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT fk_molecular_property_observation_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT fk_molecular_property_observation_software_release_id FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT fk_molecular_property_observation_source_calculation_id FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT fk_molecular_property_observation_species_entry_id FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.molecular_property_observation
    ADD CONSTRAINT fk_molecular_property_observation_workflow_tool_release_id FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.network_channel_microreaction
    ADD CONSTRAINT fk_network_channel_microreaction_channel_id_network_channel FOREIGN KEY (channel_id) REFERENCES public.network_channel(id) DEFERRABLE;
ALTER TABLE ONLY public.network_channel_microreaction
    ADD CONSTRAINT fk_network_channel_microreaction_reaction_entry FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_channel_microreaction
    ADD CONSTRAINT fk_network_channel_microreaction_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_channel
    ADD CONSTRAINT fk_network_channel_network_id_network FOREIGN KEY (network_id) REFERENCES public.network(id) DEFERRABLE;
ALTER TABLE ONLY public.network_channel
    ADD CONSTRAINT fk_network_channel_sink_state_id_network_state FOREIGN KEY (sink_state_id) REFERENCES public.network_state(id) DEFERRABLE;
ALTER TABLE ONLY public.network_channel
    ADD CONSTRAINT fk_network_channel_source_state_id_network_state FOREIGN KEY (source_state_id) REFERENCES public.network_state(id) DEFERRABLE;
ALTER TABLE ONLY public.network
    ADD CONSTRAINT fk_network_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics
    ADD CONSTRAINT fk_network_kinetics_channel_id_network_channel FOREIGN KEY (channel_id) REFERENCES public.network_channel(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics_chebyshev
    ADD CONSTRAINT fk_network_kinetics_chebyshev_network_kinetics_id FOREIGN KEY (network_kinetics_id) REFERENCES public.network_kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics_determination
    ADD CONSTRAINT fk_network_kinetics_determination_channel_id_network_channel FOREIGN KEY (channel_id) REFERENCES public.network_channel(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics_determination
    ADD CONSTRAINT fk_network_kinetics_determination_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics
    ADD CONSTRAINT fk_network_kinetics_determination_ref FOREIGN KEY (determination_id) REFERENCES public.network_kinetics_determination(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics
    ADD CONSTRAINT fk_network_kinetics_determination_scope FOREIGN KEY (determination_id, solve_id, channel_id) REFERENCES public.network_kinetics_determination(id, solve_id, channel_id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics_determination
    ADD CONSTRAINT fk_network_kinetics_determination_solve_id_network_solve FOREIGN KEY (solve_id) REFERENCES public.network_solve(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics_plog
    ADD CONSTRAINT fk_network_kinetics_plog_network_kinetics_id_network_kinetics FOREIGN KEY (network_kinetics_id) REFERENCES public.network_kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics_point
    ADD CONSTRAINT fk_network_kinetics_point_network_kinetics_id_network_kinetics FOREIGN KEY (network_kinetics_id) REFERENCES public.network_kinetics(id) DEFERRABLE;
ALTER TABLE ONLY public.network_kinetics
    ADD CONSTRAINT fk_network_kinetics_solve_id_network_solve FOREIGN KEY (solve_id) REFERENCES public.network_solve(id) DEFERRABLE;
ALTER TABLE ONLY public.network
    ADD CONSTRAINT fk_network_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.network_reaction
    ADD CONSTRAINT fk_network_reaction_network_id_network FOREIGN KEY (network_id) REFERENCES public.network(id) DEFERRABLE;
ALTER TABLE ONLY public.network_reaction
    ADD CONSTRAINT fk_network_reaction_reaction_entry_id_reaction_entry FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network
    ADD CONSTRAINT fk_network_software_release_id_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_bath_gas
    ADD CONSTRAINT fk_network_solve_bath_gas_solve_id_network_solve FOREIGN KEY (solve_id) REFERENCES public.network_solve(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_bath_gas
    ADD CONSTRAINT fk_network_solve_bath_gas_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_channel_barrier
    ADD CONSTRAINT fk_network_solve_channel_barrier_channel_id_network_channel FOREIGN KEY (channel_id) REFERENCES public.network_channel(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_channel_barrier
    ADD CONSTRAINT fk_network_solve_channel_barrier_reaction_entry FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_channel_barrier
    ADD CONSTRAINT fk_network_solve_channel_barrier_solve_id_network_solve FOREIGN KEY (solve_id) REFERENCES public.network_solve(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_channel_barrier
    ADD CONSTRAINT fk_network_solve_channel_barrier_source_calc FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_channel_barrier
    ADD CONSTRAINT fk_network_solve_channel_barrier_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve
    ADD CONSTRAINT fk_network_solve_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_energy_transfer
    ADD CONSTRAINT fk_network_solve_energy_transfer_collider FOREIGN KEY (collider_species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_energy_transfer
    ADD CONSTRAINT fk_network_solve_energy_transfer_solve_id_network_solve FOREIGN KEY (solve_id) REFERENCES public.network_solve(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_energy_transfer
    ADD CONSTRAINT fk_network_solve_energy_transfer_state FOREIGN KEY (state_id) REFERENCES public.network_state(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve
    ADD CONSTRAINT fk_network_solve_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve
    ADD CONSTRAINT fk_network_solve_network_id_network FOREIGN KEY (network_id) REFERENCES public.network(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve
    ADD CONSTRAINT fk_network_solve_software_release_id_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_source_calculation
    ADD CONSTRAINT fk_network_solve_source_calculation_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_source_calculation
    ADD CONSTRAINT fk_network_solve_source_calculation_solve_id_network_solve FOREIGN KEY (solve_id) REFERENCES public.network_solve(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy
    ADD CONSTRAINT fk_network_solve_state_energy_solve_id_network_solve FOREIGN KEY (solve_id) REFERENCES public.network_solve(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy
    ADD CONSTRAINT fk_network_solve_state_energy_source_calculation_id_calculation FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy
    ADD CONSTRAINT fk_network_solve_state_energy_state_id_network_state FOREIGN KEY (state_id) REFERENCES public.network_state(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve
    ADD CONSTRAINT fk_network_solve_workflow_tool_release_id_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.network_species
    ADD CONSTRAINT fk_network_species_network_id_network FOREIGN KEY (network_id) REFERENCES public.network(id) DEFERRABLE;
ALTER TABLE ONLY public.network_species
    ADD CONSTRAINT fk_network_species_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_state
    ADD CONSTRAINT fk_network_state_network_id_network FOREIGN KEY (network_id) REFERENCES public.network(id) DEFERRABLE;
ALTER TABLE ONLY public.network_state_participant
    ADD CONSTRAINT fk_network_state_participant_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_state_participant
    ADD CONSTRAINT fk_network_state_participant_state_id_network_state FOREIGN KEY (state_id) REFERENCES public.network_state(id) DEFERRABLE;
ALTER TABLE ONLY public.network
    ADD CONSTRAINT fk_network_workflow_tool_release_id_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy_source
    ADD CONSTRAINT fk_nsse_source_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy_source
    ADD CONSTRAINT fk_nsse_source_participant FOREIGN KEY (state_id, species_entry_id) REFERENCES public.network_state_participant(state_id, species_entry_id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy_source
    ADD CONSTRAINT fk_nsse_source_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy_source
    ADD CONSTRAINT fk_nsse_source_state_energy FOREIGN KEY (solve_id, state_id) REFERENCES public.network_solve_state_energy(solve_id, state_id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy_source
    ADD CONSTRAINT fk_nsse_source_state_id_network_state FOREIGN KEY (state_id) REFERENCES public.network_state(id) DEFERRABLE;
ALTER TABLE ONLY public.network_solve_state_energy_source
    ADD CONSTRAINT fk_nsse_source_zpe_calculation_id_calculation FOREIGN KEY (zpe_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map
    ADD CONSTRAINT fk_reaction_atom_map_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map_pair
    ADD CONSTRAINT fk_reaction_atom_map_pair_atom_map_id_reaction_atom_map FOREIGN KEY (atom_map_id, transition_state_geometry_id) REFERENCES public.reaction_atom_map(id, transition_state_geometry_id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map_pair
    ADD CONSTRAINT fk_reaction_atom_map_pair_geometry_id_geometry_atom FOREIGN KEY (geometry_id, atom_index, element) REFERENCES public.geometry_atom(geometry_id, atom_index, element) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map_pair
    ADD CONSTRAINT fk_reaction_atom_map_pair_structure_participant FOREIGN KEY (structure_participant_id, side) REFERENCES public.reaction_entry_structure_participant(id, role) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map_pair
    ADD CONSTRAINT fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom FOREIGN KEY (transition_state_geometry_id, ts_atom_index, ts_element) REFERENCES public.geometry_atom(geometry_id, atom_index, element) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map
    ADD CONSTRAINT fk_reaction_atom_map_reaction_entry FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map
    ADD CONSTRAINT fk_reaction_atom_map_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_atom_map
    ADD CONSTRAINT fk_reaction_atom_map_ts_geometry FOREIGN KEY (transition_state_geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_entry
    ADD CONSTRAINT fk_reaction_entry_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_entry
    ADD CONSTRAINT fk_reaction_entry_reaction_id_chem_reaction FOREIGN KEY (reaction_id) REFERENCES public.chem_reaction(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_entry_structure_participant
    ADD CONSTRAINT fk_reaction_entry_structure_participant_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_entry_structure_participant
    ADD CONSTRAINT fk_reaction_entry_structure_participant_reaction_entry_id FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_entry_structure_participant
    ADD CONSTRAINT fk_reaction_entry_structure_participant_species_entry_id FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_participant
    ADD CONSTRAINT fk_reaction_participant_reaction_id_chem_reaction FOREIGN KEY (reaction_id) REFERENCES public.chem_reaction(id) DEFERRABLE;
ALTER TABLE ONLY public.reaction_participant
    ADD CONSTRAINT fk_reaction_participant_species_id_species FOREIGN KEY (species_id) REFERENCES public.species(id) DEFERRABLE;
ALTER TABLE ONLY public.record_machine_review
    ADD CONSTRAINT fk_record_machine_review_source_audit_event_id FOREIGN KEY (source_audit_event_id) REFERENCES public.submission_audit_event(id) DEFERRABLE;
ALTER TABLE ONLY public.record_machine_review
    ADD CONSTRAINT fk_record_machine_review_source_submission_id_submission FOREIGN KEY (source_submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.record_review
    ADD CONSTRAINT fk_record_review_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.record_review_event
    ADD CONSTRAINT fk_record_review_event_actor_user_id_app_user FOREIGN KEY (actor_user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.record_review_event
    ADD CONSTRAINT fk_record_review_event_record_review_id_record_review FOREIGN KEY (record_review_id) REFERENCES public.record_review(id) DEFERRABLE;
ALTER TABLE ONLY public.record_review
    ADD CONSTRAINT fk_record_review_reviewed_by_app_user FOREIGN KEY (reviewed_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.record_review
    ADD CONSTRAINT fk_record_review_submission_id_submission FOREIGN KEY (submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.release_artifact
    ADD CONSTRAINT fk_release_artifact_release_manifest_id_release_manifest FOREIGN KEY (release_manifest_id) REFERENCES public.release_manifest(id) DEFERRABLE;
ALTER TABLE ONLY public.release_manifest
    ADD CONSTRAINT fk_release_manifest_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.release_manifest
    ADD CONSTRAINT fk_release_manifest_curation_policy_id_curation_policy FOREIGN KEY (curation_policy_id) REFERENCES public.curation_policy(id) DEFERRABLE;
ALTER TABLE ONLY public.release_manifest
    ADD CONSTRAINT fk_release_manifest_dataset_release_id_dataset_release FOREIGN KEY (dataset_release_id) REFERENCES public.dataset_release(id) DEFERRABLE;
ALTER TABLE ONLY public.release_selection
    ADD CONSTRAINT fk_release_selection_curation_policy_id_curation_policy FOREIGN KEY (curation_policy_id) REFERENCES public.curation_policy(id) DEFERRABLE;
ALTER TABLE ONLY public.release_selection
    ADD CONSTRAINT fk_release_selection_dataset_release_id_dataset_release FOREIGN KEY (dataset_release_id) REFERENCES public.dataset_release(id) DEFERRABLE;
ALTER TABLE ONLY public.release_selection
    ADD CONSTRAINT fk_release_selection_selected_by_app_user FOREIGN KEY (selected_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.release_selection
    ADD CONSTRAINT fk_release_selection_supersedes_selection_id_release_selection FOREIGN KEY (supersedes_selection_id) REFERENCES public.release_selection(id) DEFERRABLE;
ALTER TABLE ONLY public.accepted_science_repair_change
    ADD CONSTRAINT fk_repair_change_repair FOREIGN KEY (repair_id) REFERENCES public.accepted_science_repair(id);
ALTER TABLE ONLY public.record_reproducibility_assessment
    ADD CONSTRAINT fk_repro_assessment_assessor_user FOREIGN KEY (assessor_user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.record_reproducibility_assessment
    ADD CONSTRAINT fk_repro_assessment_source_submission FOREIGN KEY (source_submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.scientific_record_supersession
    ADD CONSTRAINT fk_scientific_record_supersession_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.software_release
    ADD CONSTRAINT fk_software_release_software_id_software FOREIGN KEY (software_id) REFERENCES public.software(id) DEFERRABLE;
ALTER TABLE ONLY public.species_entry
    ADD CONSTRAINT fk_species_entry_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.species_entry
    ADD CONSTRAINT fk_species_entry_merged_into_species_entry_id_species_entry FOREIGN KEY (merged_into_species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.species_entry_review
    ADD CONSTRAINT fk_species_entry_review_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.species_entry_review
    ADD CONSTRAINT fk_species_entry_review_user_id_app_user FOREIGN KEY (user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.species_entry
    ADD CONSTRAINT fk_species_entry_species_id_species FOREIGN KEY (species_id) REFERENCES public.species(id) DEFERRABLE;
ALTER TABLE ONLY public.species
    ADD CONSTRAINT fk_species_merged_into_species_id_species FOREIGN KEY (merged_into_species_id) REFERENCES public.species(id) DEFERRABLE;
ALTER TABLE ONLY public.species_name
    ADD CONSTRAINT fk_species_name_created_by_user_id_app_user FOREIGN KEY (created_by_user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.species_name
    ADD CONSTRAINT fk_species_name_external_source_id_external_source FOREIGN KEY (external_source_id) REFERENCES public.external_source(id) DEFERRABLE;
ALTER TABLE ONLY public.species_name
    ADD CONSTRAINT fk_species_name_species_id_species FOREIGN KEY (species_id) REFERENCES public.species(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_electronic_level
    ADD CONSTRAINT fk_statmech_electronic_level_statmech_id_statmech FOREIGN KEY (statmech_id) REFERENCES public.statmech(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_energy_level_of_theory_id_level_of_theory FOREIGN KEY (energy_level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_frequency_scale_factor_id_frequency_scale_factor FOREIGN KEY (frequency_scale_factor_id) REFERENCES public.frequency_scale_factor(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT fk_statmech_model_declaration_conformer_group FOREIGN KEY (conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT fk_statmech_model_declaration_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT fk_statmech_model_declaration_determination FOREIGN KEY (structure_determination_id) REFERENCES public.structure_determination(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT fk_statmech_model_declaration_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT fk_statmech_model_declaration_statmech_id_statmech FOREIGN KEY (statmech_id) REFERENCES public.statmech(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_declaration
    ADD CONSTRAINT fk_statmech_model_declaration_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_member
    ADD CONSTRAINT fk_statmech_model_member_conformer_group FOREIGN KEY (conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_member
    ADD CONSTRAINT fk_statmech_model_member_declaration FOREIGN KEY (declaration_id) REFERENCES public.statmech_model_declaration(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_member
    ADD CONSTRAINT fk_statmech_model_member_statmech FOREIGN KEY (member_statmech_id) REFERENCES public.statmech(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT fk_statmech_model_source_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT fk_statmech_model_source_declaration FOREIGN KEY (declaration_id) REFERENCES public.statmech_model_declaration(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT fk_statmech_model_source_frequency_scale_factor FOREIGN KEY (frequency_scale_factor_id) REFERENCES public.frequency_scale_factor(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT fk_statmech_model_source_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT fk_statmech_model_source_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_model_source
    ADD CONSTRAINT fk_statmech_model_source_member FOREIGN KEY (declaration_id, member_key) REFERENCES public.statmech_model_member(declaration_id, member_key) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_software_release_id_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_source_calculation
    ADD CONSTRAINT fk_statmech_source_calculation_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_source_calculation
    ADD CONSTRAINT fk_statmech_source_calculation_statmech_id_statmech FOREIGN KEY (statmech_id) REFERENCES public.statmech(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_torsion_definition
    ADD CONSTRAINT fk_statmech_torsion_definition_torsion_id_statmech_torsion FOREIGN KEY (torsion_id) REFERENCES public.statmech_torsion(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_torsion
    ADD CONSTRAINT fk_statmech_torsion_source_scan_calculation_id_calculation FOREIGN KEY (source_scan_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech_torsion
    ADD CONSTRAINT fk_statmech_torsion_statmech_id_statmech FOREIGN KEY (statmech_id) REFERENCES public.statmech(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_transition_state_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.statmech
    ADD CONSTRAINT fk_statmech_workflow_tool_release_id_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT fk_structure_determination_conformer_observation FOREIGN KEY (conformer_observation_id) REFERENCES public.conformer_observation(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT fk_structure_determination_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT fk_structure_determination_evaluated_geometry_id_geometry FOREIGN KEY (evaluated_geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT fk_structure_determination_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_determination FOREIGN KEY (determination_id) REFERENCES public.structure_determination(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_geometry_id_geometry FOREIGN KEY (geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_observation FOREIGN KEY (conformer_observation_id) REFERENCES public.conformer_observation(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_scope_calc_observation FOREIGN KEY (calculation_id, conformer_observation_id) REFERENCES public.calculation(id, conformer_observation_id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_scope_calc_species FOREIGN KEY (calculation_id, species_entry_id) REFERENCES public.calculation(id, species_entry_id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_scope_calc_ts FOREIGN KEY (calculation_id, transition_state_entry_id) REFERENCES public.calculation(id, transition_state_entry_id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_scope_det_observation FOREIGN KEY (determination_id, conformer_observation_id) REFERENCES public.structure_determination(id, conformer_observation_id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_scope_det_species FOREIGN KEY (determination_id, species_entry_id) REFERENCES public.structure_determination(id, species_entry_id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_scope_det_ts FOREIGN KEY (determination_id, transition_state_entry_id) REFERENCES public.structure_determination(id, transition_state_entry_id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination_source
    ADD CONSTRAINT fk_structure_determination_source_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT fk_structure_determination_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT fk_structure_determination_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_determination
    ADD CONSTRAINT fk_structure_determination_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT fk_structure_evidence_finding_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT fk_structure_evidence_finding_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT fk_structure_evidence_finding_source_calculation FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT fk_structure_evidence_finding_subject_calculation FOREIGN KEY (subject_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT fk_structure_evidence_finding_subject_determination FOREIGN KEY (subject_determination_id) REFERENCES public.structure_determination(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT fk_structure_evidence_finding_subject_geometry FOREIGN KEY (subject_geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.structure_evidence_finding
    ADD CONSTRAINT fk_structure_evidence_finding_supersedes FOREIGN KEY (supersedes_finding_id) REFERENCES public.structure_evidence_finding(id) DEFERRABLE;
ALTER TABLE ONLY public.submission
    ADD CONSTRAINT fk_submission_approved_by_app_user FOREIGN KEY (approved_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.submission_audit_event
    ADD CONSTRAINT fk_submission_audit_event_actor_user_id_app_user FOREIGN KEY (actor_user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.submission_audit_event
    ADD CONSTRAINT fk_submission_audit_event_related_submission_id_submission FOREIGN KEY (related_submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.submission_audit_event
    ADD CONSTRAINT fk_submission_audit_event_submission_id_submission FOREIGN KEY (submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.submission
    ADD CONSTRAINT fk_submission_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.submission_record_link
    ADD CONSTRAINT fk_submission_record_link_submission_id_submission FOREIGN KEY (submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.submission
    ADD CONSTRAINT fk_submission_rejected_by_app_user FOREIGN KEY (rejected_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.submission_rights_attestation
    ADD CONSTRAINT fk_submission_rights_attestation_attested_by_app_user FOREIGN KEY (attested_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.submission_rights_attestation
    ADD CONSTRAINT fk_submission_rights_attestation_submission_id_submission FOREIGN KEY (submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.submission_rights_attestation
    ADD CONSTRAINT fk_submission_rights_attestation_supersedes FOREIGN KEY (supersedes_attestation_id) REFERENCES public.submission_rights_attestation(id) DEFERRABLE;
ALTER TABLE ONLY public.submission
    ADD CONSTRAINT fk_submission_supersedes_submission_id_submission FOREIGN KEY (supersedes_submission_id) REFERENCES public.submission(id) DEFERRABLE;
ALTER TABLE ONLY public.submission
    ADD CONSTRAINT fk_submission_upload_job_id_upload_job FOREIGN KEY (upload_job_id) REFERENCES public.upload_job(id) DEFERRABLE;
ALTER TABLE ONLY public.software_version_attestation
    ADD CONSTRAINT fk_sva_attested_by FOREIGN KEY (attested_by) REFERENCES public.app_user(id);
ALTER TABLE ONLY public.software_version_attestation_calculation
    ADD CONSTRAINT fk_sva_calculation_after_release FOREIGN KEY (after_software_release_id) REFERENCES public.software_release(id);
ALTER TABLE ONLY public.software_version_attestation_calculation
    ADD CONSTRAINT fk_sva_calculation_attestation FOREIGN KEY (attestation_id) REFERENCES public.software_version_attestation(id);
ALTER TABLE ONLY public.software_version_attestation_calculation
    ADD CONSTRAINT fk_sva_calculation_before_release FOREIGN KEY (before_software_release_id) REFERENCES public.software_release(id);
ALTER TABLE ONLY public.software_version_attestation_calculation
    ADD CONSTRAINT fk_sva_calculation_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id);
ALTER TABLE ONLY public.software_version_attestation
    ADD CONSTRAINT fk_sva_covers_depositor FOREIGN KEY (covers_depositor) REFERENCES public.app_user(id);
ALTER TABLE ONLY public.software_version_attestation
    ADD CONSTRAINT fk_sva_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id);
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_energy_level_of_theory_id_level_of_theory FOREIGN KEY (energy_level_of_theory_id) REFERENCES public.level_of_theory(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo_nasa9_interval
    ADD CONSTRAINT fk_thermo_nasa9_interval_thermo_id_thermo FOREIGN KEY (thermo_id) REFERENCES public.thermo(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo_nasa
    ADD CONSTRAINT fk_thermo_nasa_thermo_id_thermo FOREIGN KEY (thermo_id) REFERENCES public.thermo(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo_point
    ADD CONSTRAINT fk_thermo_point_thermo_id_thermo FOREIGN KEY (thermo_id) REFERENCES public.thermo(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_software_release_id_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo_source_calculation
    ADD CONSTRAINT fk_thermo_source_calculation_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo_source_calculation
    ADD CONSTRAINT fk_thermo_source_calculation_thermo_id_thermo FOREIGN KEY (thermo_id) REFERENCES public.thermo(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_statmech_id_statmech FOREIGN KEY (statmech_id) REFERENCES public.statmech(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_target_conformer_group_id_conformer_group FOREIGN KEY (target_conformer_group_id) REFERENCES public.conformer_group(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo_wilhoit
    ADD CONSTRAINT fk_thermo_wilhoit_thermo_id_thermo FOREIGN KEY (thermo_id) REFERENCES public.thermo(id) DEFERRABLE;
ALTER TABLE ONLY public.thermo
    ADD CONSTRAINT fk_thermo_workflow_tool_release_id_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state
    ADD CONSTRAINT fk_transition_state_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_entry
    ADD CONSTRAINT fk_transition_state_entry_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_entry
    ADD CONSTRAINT fk_transition_state_entry_transition_state_id_transition_state FOREIGN KEY (transition_state_id) REFERENCES public.transition_state(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state
    ADD CONSTRAINT fk_transition_state_reaction_entry_id_reaction_entry FOREIGN KEY (reaction_entry_id) REFERENCES public.reaction_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_selection
    ADD CONSTRAINT fk_transition_state_selection_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_validation_evidence
    ADD CONSTRAINT fk_transition_state_validation_evidence_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.transport
    ADD CONSTRAINT fk_transport_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.transport
    ADD CONSTRAINT fk_transport_literature_id_literature FOREIGN KEY (literature_id) REFERENCES public.literature(id) DEFERRABLE;
ALTER TABLE ONLY public.transport
    ADD CONSTRAINT fk_transport_software_release_id_software_release FOREIGN KEY (software_release_id) REFERENCES public.software_release(id) DEFERRABLE;
ALTER TABLE ONLY public.transport_source_calculation
    ADD CONSTRAINT fk_transport_source_calculation_calculation_id_calculation FOREIGN KEY (calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.transport_source_calculation
    ADD CONSTRAINT fk_transport_source_calculation_transport_id_transport FOREIGN KEY (transport_id) REFERENCES public.transport(id) DEFERRABLE;
ALTER TABLE ONLY public.transport
    ADD CONSTRAINT fk_transport_species_entry_id_species_entry FOREIGN KEY (species_entry_id) REFERENCES public.species_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.transport
    ADD CONSTRAINT fk_transport_workflow_tool_release_id_workflow_tool_release FOREIGN KEY (workflow_tool_release_id) REFERENCES public.workflow_tool_release(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_selection
    ADD CONSTRAINT fk_ts_selection_transition_state FOREIGN KEY (transition_state_id) REFERENCES public.transition_state(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_validation_energy
    ADD CONSTRAINT fk_ts_validation_energy_evidence FOREIGN KEY (evidence_id) REFERENCES public.transition_state_validation_evidence(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_validation_energy
    ADD CONSTRAINT fk_ts_validation_energy_source_calc FOREIGN KEY (source_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_validation_evidence
    ADD CONSTRAINT fk_ts_validation_evidence_reconstruction_calc FOREIGN KEY (reconstruction_calculation_id) REFERENCES public.calculation(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_validation_evidence
    ADD CONSTRAINT fk_ts_validation_evidence_ts_entry FOREIGN KEY (transition_state_entry_id) REFERENCES public.transition_state_entry(id) DEFERRABLE;
ALTER TABLE ONLY public.transition_state_validation_evidence
    ADD CONSTRAINT fk_ts_validation_evidence_ts_geometry FOREIGN KEY (transition_state_geometry_id) REFERENCES public.geometry(id) DEFERRABLE;
ALTER TABLE ONLY public.upload_job
    ADD CONSTRAINT fk_upload_job_created_by_app_user FOREIGN KEY (created_by) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.user_session
    ADD CONSTRAINT fk_user_session_user_id_app_user FOREIGN KEY (user_id) REFERENCES public.app_user(id) DEFERRABLE;
ALTER TABLE ONLY public.workflow_tool_release
    ADD CONSTRAINT fk_workflow_tool_release_workflow_tool_id_workflow_tool FOREIGN KEY (workflow_tool_id) REFERENCES public.workflow_tool(id) DEFERRABLE;
SET LOCAL check_function_bodies = true;
'''

# Objects owned by the rdkit extension are left alone on downgrade.
_DROP_ALL_SQL = """
DO $drop$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND c.relname <> 'alembic_version'
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', r.relname);
    END LOOP;
    FOR r IN
        SELECT p.oid::regprocedure AS sig FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.prokind IN ('f', 'p')
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
    LOOP
        EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
    END LOOP;
    FOR r IN
        SELECT t.typname FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = t.oid AND d.deptype = 'e')
    LOOP
        EXECUTE format('DROP TYPE IF EXISTS public.%I CASCADE', r.typname);
    END LOOP;
    FOR r IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S'
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e')
    LOOP
        EXECUTE format('DROP SEQUENCE IF EXISTS public.%I CASCADE', r.relname);
    END LOOP;
END
$drop$;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS rdkit;")
    bind = op.get_bind()
    bind.execution_options(no_parameters=True).exec_driver_sql(_SCHEMA_SQL)

    op.bulk_insert(
        sa.table("reaction_family", sa.column("name", sa.Text())),
        [{"name": name} for name in _REACTION_FAMILIES],
    )
    op.bulk_insert(
        sa.table(
            "calculation_parameter_vocab",
            *(sa.column(name) for name in _PARAMETER_VOCAB_COLUMNS),
        ),
        [dict(zip(_PARAMETER_VOCAB_COLUMNS, row)) for row in _PARAMETER_VOCAB],
    )
    for name, version, scope, description, parameters_json, code_commit, is_default, public_ref in _CONFORMER_ASSIGNMENT_SCHEMES:
        bind.execute(
            sa.text(
                "INSERT INTO conformer_assignment_scheme"
                " (name, version, scope, description, parameters_json, code_commit, is_default, public_ref)"
                " VALUES (:name, :version, CAST(:scope AS conformer_assignment_scope_kind), :description,"
                " CAST(:parameters_json AS jsonb), :code_commit, :is_default, :public_ref)"
            ),
            {
                "name": name,
                "version": version,
                "scope": scope,
                "description": description,
                "parameters_json": parameters_json,
                "code_commit": code_commit,
                "is_default": is_default,
                "public_ref": public_ref,
            },
        )


def downgrade() -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(_DROP_ALL_SQL)
