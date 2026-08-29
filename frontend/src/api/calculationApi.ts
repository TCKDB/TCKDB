import { z } from "zod"
import { levelOfTheorySchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// ---------------------------------------------------------------------------
// Include-token vocabulary
//
// Read off `CALCULATION_RECORD_SECTIONS` in
// backend/app/api/routes/scientific/_response.py:149 (measured 2026-08-29,
// live against https://tckdb.homecalvin.com). 19 public tokens; a 20th,
// `internal_ids`, exists but must never be requested by this client (it only
// widens the response when the deployment allows internal ids, which this
// public client has no use for). `trust` is a further, separate opt-in
// token (its own `TRUST_SECTION` table, not part of the 19) and is out of
// scope for this slice — see the page-level docstring.
//
// The mapping from token to response-field name is NOT the identity: the
// `review` token gates the `review_history` field. Every other token here
// names its own field.
// ---------------------------------------------------------------------------

export const EAGER_SECTION_TOKENS = [
    "results",
    "dependencies",
    "review",
    "input_geometries",
    "output_geometries",
] as const

export const ON_DEMAND_SECTION_TOKENS = [
    "energy_corrections",
    "artifacts",
    "geometry_validation",
    "scf_stability",
    "wavefunction_diagnostic",
    "spin_diagnostic",
    "parameters",
    "constraints",
    "freq_modes",
    "scan",
    "irc",
    "path_search",
    "execution_environment",
    "imaginary_mode_projections",
] as const

export type OnDemandSectionToken = typeof ON_DEMAND_SECTION_TOKENS[number]

/** Token -> response field name. Only `review` differs from its token. */
function sectionField(token: string): string {
    return token === "review" ? "review_history" : token
}

// ---------------------------------------------------------------------------
// Shared fragments
// ---------------------------------------------------------------------------

const reviewBadgeSchema = z.object({
    status: z.string(),
    reviewed_at: z.string().nullable().optional(),
    reviewer_kind: z.string().nullable().optional(),
}).passthrough()

const speciesEntryOwnerSchema = z.object({
    species_ref: z.string(),
    species_entry_ref: z.string(),
    species_entry_label: z.string().nullable().optional(),
    canonical_smiles: z.string(),
    inchi_key: z.string(),
    charge: z.number(),
    multiplicity: z.number(),
    species_entry_kind: z.string(),
    electronic_state_kind: z.string(),
}).passthrough()

const transitionStateEntryOwnerSchema = z.object({
    transition_state_ref: z.string(),
    transition_state_entry_ref: z.string(),
    label: z.string().nullable().optional(),
    charge: z.number(),
    multiplicity: z.number(),
    status: z.string(),
    reaction_entry_ref: z.string().nullable().optional(),
}).passthrough()

const ownerSchema = z.object({
    kind: z.enum(["species_entry", "transition_state_entry"]),
    species_entry: speciesEntryOwnerSchema.nullable().optional(),
    transition_state_entry: transitionStateEntryOwnerSchema.nullable().optional(),
}).passthrough()

const softwareReleaseSchema = z.object({
    software_release_ref: z.string(),
    software: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const workflowToolReleaseSchema = z.object({
    workflow_tool_release_ref: z.string(),
    workflow_tool: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const literatureSchema = z.object({
    literature_ref: z.string(),
    title: z.string().nullable().optional(),
    year: z.number().nullable().optional(),
    doi: z.string().nullable().optional(),
}).passthrough()

const provenanceSchema = z.object({
    has_result: z.boolean(),
    converged: z.boolean().nullable().optional(),
    geometry_validation_status: z.string(),
    scf_stability_status: z.string(),
    submission_ref: z.string().nullable().optional(),
}).passthrough()

const availableSectionsSchema = z.object({
    has_results: z.boolean(),
    has_dependencies: z.boolean(),
    has_parameters: z.boolean(),
    has_constraints: z.boolean(),
    has_artifacts: z.boolean(),
    has_input_geometries: z.boolean(),
    has_output_geometries: z.boolean(),
    has_geometry_validation: z.boolean(),
    has_scf_stability: z.boolean(),
    has_wavefunction_diagnostic: z.boolean(),
    has_spin_diagnostic: z.boolean(),
    has_freq_modes: z.boolean(),
    has_hessian: z.boolean(),
    has_scan: z.boolean(),
    has_irc: z.boolean(),
    has_path_search: z.boolean(),
    has_execution_environment: z.boolean(),
    has_energy_corrections: z.boolean(),
}).passthrough()

const calculationCoreSchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    quality: z.string(),
    created_at: z.string(),
    review: reviewBadgeSchema,
}).passthrough()

// ---------------------------------------------------------------------------
// include=results
// ---------------------------------------------------------------------------

const resultsSchema = z.object({
    kind: z.string(),
    sp: z.object({
        electronic_energy_hartree: z.number().nullable().optional(),
        electronic_energy_uncertainty_hartree: z.number().nullable().optional(),
    }).passthrough().nullable().optional(),
    opt: z.object({
        converged: z.boolean().nullable().optional(),
        n_steps: z.number().nullable().optional(),
        final_energy_hartree: z.number().nullable().optional(),
    }).passthrough().nullable().optional(),
    freq: z.object({
        n_imag: z.number().nullable().optional(),
        imag_freq_cm1: z.number().nullable().optional(),
        zpe_hartree: z.number().nullable().optional(),
        zpe_uncertainty_hartree: z.number().nullable().optional(),
        reaction_coordinate_mode_index: z.number().nullable().optional(),
        imaginary_mode_tau_cm1: z.number().nullable().optional(),
        imaginary_mode_tau_basis: z.string().nullable().optional(),
        imaginary_mode_structural_flag: z.boolean().nullable().optional(),
        n_imag_at_or_above_tau: z.number().nullable().optional(),
    }).passthrough().nullable().optional(),
    scan: z.object({
        dimension: z.number().nullable().optional(),
        is_relaxed: z.boolean().nullable().optional(),
        zero_energy_reference_hartree: z.number().nullable().optional(),
        note: z.string().nullable().optional(),
    }).passthrough().nullable().optional(),
    irc: z.object({
        direction: z.string().nullable().optional(),
        has_forward: z.boolean().nullable().optional(),
        has_reverse: z.boolean().nullable().optional(),
        ts_point_index: z.number().nullable().optional(),
        point_count: z.number().nullable().optional(),
        note: z.string().nullable().optional(),
    }).passthrough().nullable().optional(),
    path_search: z.object({
        method: z.string().nullable().optional(),
        is_double_ended: z.boolean().nullable().optional(),
        converged: z.boolean().nullable().optional(),
        n_points: z.number().nullable().optional(),
        note: z.string().nullable().optional(),
    }).passthrough().nullable().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// include=dependencies — the one graph this page is allowed to draw
// ---------------------------------------------------------------------------

const dependencySchema = z.object({
    role: z.string(),
    direction: z.enum(["parent", "child"]),
    parent_calculation_ref: z.string(),
    child_calculation_ref: z.string(),
}).passthrough()

// ---------------------------------------------------------------------------
// include=review
// ---------------------------------------------------------------------------

const reviewEntrySchema = z.object({
    status: z.string(),
    note: z.string().nullable().optional(),
    reviewed_at: z.string().nullable().optional(),
    submission_ref: z.string().nullable().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// include=input_geometries / include=output_geometries
// ---------------------------------------------------------------------------

const geometryLinkSchema = z.object({
    geometry_ref: z.string(),
    input_order: z.number().nullable().optional(),
    output_order: z.number().nullable().optional(),
    role: z.string().nullable().optional(),
    natoms: z.number().nullable().optional(),
    geom_hash: z.string().nullable().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// On-demand-only fragments (fetched one token at a time, see
// useCalculationSection)
// ---------------------------------------------------------------------------

const energyCorrectionComponentSchema = z.object({
    component_kind: z.string(),
    key: z.string(),
    multiplicity: z.number(),
    parameter_value: z.number(),
    contribution_value: z.number(),
}).passthrough()

const energyCorrectionSchema = z.object({
    application_role: z.string(),
    applied_value: z.number(),
    applied_value_unit: z.string(),
    applied_value_hartree: z.number().nullable().optional(),
    temperature_k: z.number().nullable().optional(),
    note: z.string().nullable().optional(),
    target_record_type: z.string(),
    target_record_ref: z.string().nullable().optional(),
    target_endpoint: z.string().nullable().optional(),
    energy_correction_scheme_ref: z.string().nullable().optional(),
    energy_correction_scheme_name: z.string().nullable().optional(),
    frequency_scale_factor_ref: z.string().nullable().optional(),
    component_count: z.number(),
    components_truncated: z.boolean().optional(),
    components: z.array(energyCorrectionComponentSchema).optional(),
}).passthrough()

const artifactSchema = z.object({
    artifact_ref: z.string().nullable().optional(),
    kind: z.string(),
    uri: z.string(),
    filename: z.string().nullable().optional(),
    sha256: z.string(),
    bytes: z.number(),
    created_at: z.string().nullable().optional(),
}).passthrough()

const geometryValidationSchema = z.object({
    input_geometry_ref: z.string().nullable().optional(),
    output_geometry_ref: z.string().nullable().optional(),
    species_smiles: z.string(),
    formula_matches: z.boolean(),
    rmsd: z.number().nullable().optional(),
    n_mappings: z.number().nullable().optional(),
    validation_status: z.string(),
    validation_reason: z.string().nullable().optional(),
    rmsd_warning_threshold: z.number().nullable().optional(),
}).passthrough()

const scfStabilitySchema = z.object({
    status: z.string(),
    lowest_eigenvalue: z.number().nullable().optional(),
    instability_count: z.number().nullable().optional(),
    instability_type: z.string().nullable().optional(),
    reoptimized_wavefunction: z.boolean().nullable().optional(),
    note: z.string().nullable().optional(),
    source_calculation_ref: z.string().nullable().optional(),
}).passthrough()

const wavefunctionDiagnosticSchema = z.object({
    t1_diagnostic: z.number().nullable().optional(),
    d1_diagnostic: z.number().nullable().optional(),
    t1_norm: z.number().nullable().optional(),
    largest_t2_amplitude: z.number().nullable().optional(),
    note: z.string().nullable().optional(),
}).passthrough()

const spinDiagnosticSchema = z.object({
    s_squared: z.number().nullable().optional(),
    s_squared_expected: z.number().nullable().optional(),
    s_squared_annihilated: z.number().nullable().optional(),
    note: z.string().nullable().optional(),
}).passthrough()

const parameterSchema = z.object({
    raw_key: z.string(),
    raw_value: z.string(),
    canonical_key: z.string().nullable().optional(),
    canonical_value: z.string().nullable().optional(),
    section: z.string().nullable().optional(),
    unit: z.string().nullable().optional(),
}).passthrough()

const constraintSchema = z.object({
    constraint_index: z.number(),
    constraint_kind: z.string(),
    atom_indices: z.array(z.number()),
    target_value: z.number().nullable().optional(),
}).passthrough()

const freqModeSchema = z.object({
    mode_index: z.number(),
    frequency_cm1: z.number(),
    is_imaginary: z.boolean(),
    reduced_mass_amu: z.number().nullable().optional(),
    force_constant_mdyne_angstrom: z.number().nullable().optional(),
    imaginary_disposition: z.string().nullable().optional(),
}).passthrough()

const scanCoordinateSchema = z.object({
    coordinate_index: z.number(),
    coordinate_kind: z.string(),
    atom_indices: z.array(z.number()),
    step_count: z.number().nullable().optional(),
    start_value: z.number().nullable().optional(),
    end_value: z.number().nullable().optional(),
}).passthrough()

const scanSchema = z.object({
    dimension: z.number(),
    is_relaxed: z.boolean().nullable().optional(),
    coordinate_count: z.number(),
    point_count: z.number(),
    coordinates: z.array(scanCoordinateSchema).optional(),
    min_electronic_energy_hartree: z.number().nullable().optional(),
    max_electronic_energy_hartree: z.number().nullable().optional(),
}).passthrough()

const ircSchema = z.object({
    direction: z.string(),
    has_forward: z.boolean(),
    has_reverse: z.boolean(),
    point_count: z.number().nullable().optional(),
    forward_point_count: z.number(),
    reverse_point_count: z.number(),
    ts_point_count: z.number(),
}).passthrough()

const pathSearchSchema = z.object({
    method: z.string(),
    is_double_ended: z.boolean().nullable().optional(),
    converged: z.boolean().nullable().optional(),
    n_points: z.number().nullable().optional(),
    stored_point_count: z.number(),
    ts_guess_count: z.number(),
    climbing_image_count: z.number(),
}).passthrough()

const executionEnvironmentSchema = z.object({
    environment_ref: z.string(),
    schema_version: z.string().optional(),
    runtime: z.object({ runtime_kind: z.string() }).passthrough().optional(),
    software_release: z.unknown().optional(),
    executable: z.object({ locator: z.string() }).passthrough().optional(),
    closure: z.array(z.unknown()).optional(),
}).passthrough()

const imaginaryModeEntrySchema = z.object({
    mode_index: z.number(),
    frequency_cm1: z.number(),
    declared_disposition: z.string().nullable().optional(),
    determination: z.string().nullable().optional(),
    agreement: z.string(),
}).passthrough()

const imaginaryModeProjectionSchema = z.object({
    status: z.string(),
    modes: z.array(imaginaryModeEntrySchema).optional(),
    conflict_count: z.number().optional(),
    natoms: z.number().nullable().optional(),
    is_linear: z.boolean().nullable().optional(),
    rigid_body_overlap_threshold: z.number().optional(),
    torsion_overlap_threshold: z.number().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// Top-level record
// ---------------------------------------------------------------------------

const calculationRecordSchema = z.object({
    calculation: calculationCoreSchema,
    owner: ownerSchema,
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
    literature: literatureSchema.nullable().optional(),
    provenance: provenanceSchema,
    available_sections: availableSectionsSchema,
    results: resultsSchema.nullable().optional(),
    energy_corrections: z.array(energyCorrectionSchema).nullable().optional(),
    dependencies: z.array(dependencySchema).nullable().optional(),
    artifacts: z.array(artifactSchema).nullable().optional(),
    execution_environment: executionEnvironmentSchema.nullable().optional(),
    input_geometries: z.array(geometryLinkSchema).nullable().optional(),
    output_geometries: z.array(geometryLinkSchema).nullable().optional(),
    geometry_validation: z.array(geometryValidationSchema).nullable().optional(),
    scf_stability: z.array(scfStabilitySchema).nullable().optional(),
    wavefunction_diagnostic: z.array(wavefunctionDiagnosticSchema).nullable().optional(),
    spin_diagnostic: z.array(spinDiagnosticSchema).nullable().optional(),
    parameters: z.array(parameterSchema).nullable().optional(),
    constraints: z.array(constraintSchema).nullable().optional(),
    review_history: z.array(reviewEntrySchema).nullable().optional(),
    freq_modes: z.array(freqModeSchema).nullable().optional(),
    imaginary_mode_projections: imaginaryModeProjectionSchema.nullable().optional(),
    scan: scanSchema.nullable().optional(),
    irc: ircSchema.nullable().optional(),
    path_search: pathSearchSchema.nullable().optional(),
}).passthrough()

const responseSchema = z.object({
    record: calculationRecordSchema,
})

export type CalculationRecord = z.infer<typeof calculationRecordSchema>
export type CalculationDependency = z.infer<typeof dependencySchema>
export type CalculationGeometryLink = z.infer<typeof geometryLinkSchema>
export type CalculationReviewEntry = z.infer<typeof reviewEntrySchema>
export type CalculationResults = z.infer<typeof resultsSchema>
export type CalculationEnergyCorrection = z.infer<typeof energyCorrectionSchema>
export type CalculationArtifact = z.infer<typeof artifactSchema>
export type CalculationGeometryValidation = z.infer<typeof geometryValidationSchema>
export type CalculationSCFStability = z.infer<typeof scfStabilitySchema>
export type CalculationWavefunctionDiagnostic = z.infer<typeof wavefunctionDiagnosticSchema>
export type CalculationSpinDiagnostic = z.infer<typeof spinDiagnosticSchema>
export type CalculationParameter = z.infer<typeof parameterSchema>
export type CalculationConstraint = z.infer<typeof constraintSchema>
export type CalculationFreqMode = z.infer<typeof freqModeSchema>
export type CalculationScan = z.infer<typeof scanSchema>
export type CalculationIRC = z.infer<typeof ircSchema>
export type CalculationPathSearch = z.infer<typeof pathSearchSchema>
export type CalculationExecutionEnvironment = z.infer<typeof executionEnvironmentSchema>
export type CalculationImaginaryModeProjection = z.infer<typeof imaginaryModeProjectionSchema>

function buildEndpoint(ref: string, includes: readonly string[]): string {
    const query = new URLSearchParams()
    for (const include of includes) query.append("include", include)
    return `/api/v1/scientific/calculations/${encodeURIComponent(ref)}?${query}`
}

/**
 * Load the calculation with the page's eager section set (see
 * `EAGER_SECTION_TOKENS`). This is the one call `useCalculation` makes.
 */
export async function loadCalculation(ref: string, signal?: AbortSignal): Promise<CalculationRecord> {
    const endpoint = buildEndpoint(ref, EAGER_SECTION_TOKENS)
    const payload = await requestScientificJson(endpoint, signal)
    return parseScientificResponse(responseSchema, payload, "calculation").record
}

/**
 * Load the calculation with exactly one additional heavy include, for a
 * disclosure the reader chose to open. Fetches the whole record shape (the
 * endpoint has no narrower response) but the caller reads only the one
 * field the token gates — see `sectionField`.
 */
export async function loadCalculationSection(
    ref: string, token: OnDemandSectionToken, signal?: AbortSignal,
): Promise<CalculationRecord> {
    const endpoint = buildEndpoint(ref, [token])
    const payload = await requestScientificJson(endpoint, signal)
    return parseScientificResponse(responseSchema, payload, "calculation").record
}

/** Read the field a resolved on-demand record's token actually populated. */
export function readSectionField<T>(record: CalculationRecord, token: OnDemandSectionToken): T {
    return (record as unknown as Record<string, unknown>)[sectionField(token)] as T
}
