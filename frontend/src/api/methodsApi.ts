import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
import { levelOfTheorySchema } from "./scientificSchemas"

// ---------------------------------------------------------------------------
// The methods surface (`docs/plans/methods-surface.md` §5.1-5.1,
// `plan-methods-surface-v2`, not committed to this repo). Three read
// surfaces this client wraps here:
//
// - GET /scientific/level-of-theories/browse   (index)
// - GET /scientific/level-of-theories/{ref}     (record page)
// - GET /scientific/energy-correction-schemes/{ref}   (already shipped)
// - GET /scientific/frequency-scale-factors/{ref}     (already shipped)
//
// Shapes measured live against https://tckdb.homecalvin.com, 2026-09-09,
// cross-checked against `backend/app/schemas/reads/scientific_level_of_
// theory.py`, `scientific_level_of_theory_search.py`,
// `scientific_energy_correction_scheme.py`, `scientific_frequency_scale_
// factor.py`. All response objects use `.passthrough()` (this project's
// standing convention -- see `calculationApi.ts`, `geometryApi.ts`) so an
// additive field the backend adds later never breaks parsing here.
// ---------------------------------------------------------------------------

// --- shared fragments --------------------------------------------------

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

// The LOT summary embedded inside an ECS/FSF record -- a DIFFERENT, smaller
// shape than the LOT core block below (no `aux_basis`/`cabs_basis`/
// `solvent_model`/`keywords`/`lot_hash`/`created_at`/`level_of_theory_id`).
// Reuses `levelOfTheorySchema` (`api/scientificSchemas.ts`) -- the same
// summary shape every other calculation-adjacent surface already parses
// LOT summaries with -- rather than a fourth bespoke LOT schema in this
// file.
const lotSummarySchema = levelOfTheorySchema

// --- energy-correction-scheme record (also embedded in a LOT's
//     `correction_schemes`) ---------------------------------------------

const correctionTermSchema = z.object({
    correction_kind: z.string(),
    target: z.string(),
    value: z.number(),
    component_kind: z.string().nullable().optional(),
}).passthrough()

const ecsUsageSummarySchema = z.object({
    record_type: z.string(),
    record_ref: z.string(),
    record_id: z.number().nullable().optional(),
    endpoint: z.string(),
    applied_energy_correction_id: z.number().nullable().optional(),
    application_role: z.string(),
    applied_value: z.number(),
    applied_value_unit: z.string(),
    applied_value_hartree: z.number().nullable().optional(),
    temperature_k: z.number().nullable().optional(),
    applied_note: z.string().nullable().optional(),
    source_calculation_ref: z.string().nullable().optional(),
    source_calculation_endpoint: z.string().nullable().optional(),
    component_count: z.number(),
}).passthrough()

const ecsCoreSchema = z.object({
    energy_correction_scheme_id: z.number().nullable().optional(),
    energy_correction_scheme_ref: z.string(),
    name: z.string(),
    scheme_kind: z.string(),
    version: z.string().nullable().optional(),
    units: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
    created_at: z.string(),
}).passthrough()

const ecsEvidenceSummarySchema = z.object({
    atom_param_count: z.number(),
    bond_param_count: z.number(),
    component_param_count: z.number(),
    has_corrections: z.boolean(),
    applied_usage_count: z.number(),
    has_applied_usage: z.boolean(),
    has_literature_source: z.boolean(),
}).passthrough()

const ecsAvailableSectionsSchema = z.object({
    has_corrections: z.boolean(),
    has_used_by: z.boolean(),
    has_literature: z.boolean(),
}).passthrough()

export const energyCorrectionSchemeRecordSchema = z.object({
    energy_correction_scheme: ecsCoreSchema,
    level_of_theory: lotSummarySchema.nullable().optional(),
    // Added by #439 (deployed): backfilled from each scheme's own level of
    // theory (both live rows: Gaussian). `software_release_ref` is
    // measured live as `""` -- the row stores `software_id` (a vendor),
    // not a release id, so the backend synthesizes this summary shape
    // with no real release to link (see
    // `backend/app/services/scientific_read/energy_correction_schemes.py`
    // `_build_software_release_summary`, which mirrors
    // `FrequencyScaleFactor`'s identical limitation). Real backend
    // behaviour, not a serialisation artefact -- never build a link from
    // this ref; render through `softwareLabel` (name/version only) like
    // every other `software_release` consumer in this app already does.
    software_release: softwareReleaseSchema.nullable().optional(),
    // Stayed null on both live rows -- 10 of 416 calculations recorded a
    // workflow-tool release and no single one could be derived
    // unambiguously, so the backfill deliberately left this absent rather
    // than guess.
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
    literature: literatureSchema.nullable().optional(),
    evidence_summary: ecsEvidenceSummarySchema,
    available_sections: ecsAvailableSectionsSchema,
    corrections: z.array(correctionTermSchema).nullable().optional(),
    used_by: z.array(ecsUsageSummarySchema).nullable().optional(),
}).passthrough()

export type EnergyCorrectionSchemeRecord = z.infer<typeof energyCorrectionSchemeRecordSchema>
export type CorrectionTerm = z.infer<typeof correctionTermSchema>
export type EnergyCorrectionSchemeUsage = z.infer<typeof ecsUsageSummarySchema>

const ecsDetailResponseSchema = z.object({
    record: energyCorrectionSchemeRecordSchema,
}).passthrough()

// --- frequency-scale-factor record (also embedded, in dedup-grouped
//     form, inside a LOT's `frequency_scale_factors`) -------------------

const fsfCoreSchema = z.object({
    frequency_scale_factor_id: z.number().nullable().optional(),
    frequency_scale_factor_ref: z.string(),
    scale_kind: z.string(),
    value: z.number(),
    note: z.string().nullable().optional(),
    created_at: z.string(),
}).passthrough()

const fsfEvidenceSummarySchema = z.object({
    has_literature_source: z.boolean(),
    has_workflow_tool_source: z.boolean(),
    has_software_dimension: z.boolean(),
    statmech_usage_count: z.number(),
    has_statmech_usage: z.boolean(),
}).passthrough()

const fsfAvailableSectionsSchema = z.object({
    has_used_by: z.boolean(),
    has_literature: z.boolean(),
}).passthrough()

const fsfUsageSummarySchema = z.object({
    record_type: z.string(),
    record_ref: z.string(),
    record_id: z.number().nullable().optional(),
    endpoint: z.string(),
}).passthrough()

export const frequencyScaleFactorRecordSchema = z.object({
    frequency_scale_factor: fsfCoreSchema,
    level_of_theory: lotSummarySchema.nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
    literature: literatureSchema.nullable().optional(),
    evidence_summary: fsfEvidenceSummarySchema,
    available_sections: fsfAvailableSectionsSchema,
    used_by: z.array(fsfUsageSummarySchema).nullable().optional(),
}).passthrough()

export type FrequencyScaleFactorRecord = z.infer<typeof frequencyScaleFactorRecordSchema>
export type FrequencyScaleFactorUsage = z.infer<typeof fsfUsageSummarySchema>

const fsfDetailResponseSchema = z.object({
    record: frequencyScaleFactorRecordSchema,
}).passthrough()

// --- level-of-theory core / record --------------------------------------

const levelOfTheoryCoreSchema = z.object({
    level_of_theory_id: z.number().nullable().optional(),
    level_of_theory_ref: z.string(),
    method: z.string(),
    basis: z.string().nullable().optional(),
    aux_basis: z.string().nullable().optional(),
    cabs_basis: z.string().nullable().optional(),
    dispersion: z.string().nullable().optional(),
    solvent: z.string().nullable().optional(),
    solvent_model: z.string().nullable().optional(),
    keywords: z.string().nullable().optional(),
    spin_treatment: z.string().nullable().optional(),
    lot_hash: z.string(),
    created_at: z.string(),
}).passthrough()

const lotEvidenceSummarySchema = z.object({
    calculation_usage_count: z.number(),
    has_correction_schemes: z.boolean(),
    has_frequency_scale_factors: z.boolean(),
    distinct_software_count: z.number(),
}).passthrough()

const lotAvailableSectionsSchema = z.object({
    has_correction_schemes: z.boolean(),
    has_frequency_scale_factors: z.boolean(),
    has_used_by: z.boolean(),
    has_software: z.boolean(),
}).passthrough()

const lotFsfProvenanceSchema = z.object({
    frequency_scale_factor_ref: z.string(),
    frequency_scale_factor_id: z.number().nullable().optional(),
    software_release: softwareReleaseSchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSchema.nullable().optional(),
    source_literature_ref: z.string().nullable().optional(),
}).passthrough()

const lotFsfGroupSchema = z.object({
    scale_kind: z.string(),
    value: z.number(),
    frequency_scale_factor_count: z.number(),
    frequency_scale_factors: z.array(lotFsfProvenanceSchema),
}).passthrough()

const lotUsageSummarySchema = z.object({
    calculation_ref: z.string(),
    calculation_id: z.number().nullable().optional(),
    endpoint: z.string(),
    type: z.string(),
    record_type: z.string().nullable().optional(),
    record_ref: z.string().nullable().optional(),
    record_endpoint: z.string().nullable().optional(),
}).passthrough()

const lotSoftwareUsageSchema = z.object({
    software: z.string(),
    version: z.string().nullable().optional(),
    calculation_count: z.number(),
}).passthrough()

const lotWorkflowToolUsageSchema = z.object({
    workflow_tool: z.string(),
    version: z.string().nullable().optional(),
    calculation_count: z.number(),
}).passthrough()

const lotSoftwareBreakdownSchema = z.object({
    software: z.array(lotSoftwareUsageSchema),
    workflow_tools: z.array(lotWorkflowToolUsageSchema),
}).passthrough()

const levelOfTheoryRecordSchema = z.object({
    level_of_theory: levelOfTheoryCoreSchema,
    evidence_summary: lotEvidenceSummarySchema,
    available_sections: lotAvailableSectionsSchema,
    correction_schemes: z.array(energyCorrectionSchemeRecordSchema).nullable().optional(),
    frequency_scale_factors: z.array(lotFsfGroupSchema).nullable().optional(),
    used_by: z.array(lotUsageSummarySchema).nullable().optional(),
    software: lotSoftwareBreakdownSchema.nullable().optional(),
}).passthrough()

export type LevelOfTheoryRecord = z.infer<typeof levelOfTheoryRecordSchema>
export type LevelOfTheoryFrequencyScaleFactorGroup = z.infer<typeof lotFsfGroupSchema>
export type LevelOfTheoryFrequencyScaleFactorProvenance = z.infer<typeof lotFsfProvenanceSchema>
export type LevelOfTheoryUsage = z.infer<typeof lotUsageSummarySchema>
export type LevelOfTheorySoftwareUsage = z.infer<typeof lotSoftwareUsageSchema>
export type LevelOfTheoryWorkflowToolUsage = z.infer<typeof lotWorkflowToolUsageSchema>

const lotDetailResponseSchema = z.object({
    record: levelOfTheoryRecordSchema,
}).passthrough()

const paginationSchema = z.object({
    offset: z.number(),
    limit: z.number(),
    returned: z.number(),
    total: z.number(),
}).passthrough()

const lotBrowseResponseSchema = z.object({
    records: z.array(levelOfTheoryRecordSchema),
    pagination: paginationSchema,
}).passthrough()

export type LevelOfTheoryBrowseResult = { records: LevelOfTheoryRecord[]; pagination: z.infer<typeof paginationSchema> }

// ---------------------------------------------------------------------------
// Loaders
// ---------------------------------------------------------------------------

const LEVEL_OF_THEORY_DETAIL_INCLUDES = ["correction_schemes", "frequency_scale_factors", "software", "used_by"]

/**
 * `/methods` index data -- unfiltered, every level of theory with >=1
 * attributing calculation (§4.1). `limit=200` (the endpoint's own cap) is
 * enough headroom for this archive's 4 rows and any near-term growth;
 * this index does not paginate (§4.1: "the index itself stays exactly as
 * small as v1 planned").
 */
export async function loadLevelOfTheoryBrowse(signal?: AbortSignal): Promise<LevelOfTheoryBrowseResult> {
    const query = new URLSearchParams({ limit: "200" })
    const endpoint = `/api/v1/scientific/level-of-theories/browse?${query}`
    const payload = await requestScientificJson(endpoint, signal)
    const parsed = parseScientificResponse(lotBrowseResponseSchema, payload, "level-of-theory browse")
    return { records: parsed.records, pagination: parsed.pagination }
}

/**
 * `/methods/:lotRef` record page data -- one round trip, every heavy
 * section requested eagerly (§6's "Data loading" note: unlike v1's
 * software/workflow-tool index sections, this page never grows a 1+3+1+1
 * on-demand pattern).
 */
export async function loadLevelOfTheory(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<LevelOfTheoryRecord> {
    const query = new URLSearchParams()
    for (const include of LEVEL_OF_THEORY_DETAIL_INCLUDES) query.append("include", include)
    const endpoint = `/api/v1/scientific/level-of-theories/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    return parseScientificResponse(lotDetailResponseSchema, payload, "level of theory").record
}

/** `/methods/schemes/:ecsRef` -- the standalone scheme page (§4.3). */
export async function loadCorrectionScheme(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<EnergyCorrectionSchemeRecord> {
    const query = new URLSearchParams()
    for (const include of ["corrections", "used_by", "literature"]) query.append("include", include)
    const endpoint = `/api/v1/scientific/energy-correction-schemes/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    return parseScientificResponse(ecsDetailResponseSchema, payload, "energy correction scheme").record
}

/** `/methods/frequency-scale-factors/:fsfRef` -- the standalone FSF page (§4.3). */
export async function loadFrequencyScaleFactor(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<FrequencyScaleFactorRecord> {
    const query = new URLSearchParams()
    for (const include of ["used_by", "literature"]) query.append("include", include)
    const endpoint = `/api/v1/scientific/frequency-scale-factors/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    return parseScientificResponse(fsfDetailResponseSchema, payload, "frequency scale factor").record
}
