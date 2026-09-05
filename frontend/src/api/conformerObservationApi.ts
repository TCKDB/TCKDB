import { z } from "zod"
import {
    calculationSummarySchema,
    geometrySummarySchema,
    levelOfTheorySchema,
    recordReviewSchema,
} from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

const geometryLinkSchema = z.object({
    calculation_ref: z.string(),
    geometry: geometrySummarySchema,
}).passthrough()

const speciesContextSchema = z.object({
    species_ref: z.string(),
    species_entry_ref: z.string(),
    species_entry_label: z.string().nullable().optional(),
    canonical_smiles: z.string().nullable().optional(),
    inchi_key: z.string().nullable().optional(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
}).passthrough()

const conformerGroupContextSchema = z.object({
    conformer_group_ref: z.string(),
    label: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
    review: recordReviewSchema,
}).passthrough()

const assignmentSchemeSchema = z.object({
    assignment_scheme_ref: z.string().nullable().optional(),
    name: z.string(),
    version: z.string().nullable().optional(),
    scope: z.string().nullable().optional(),
    is_default: z.boolean().nullable().optional(),
}).passthrough()

const evidenceSummarySchema = z.object({
    observation_count: z.number().nullable().optional(),
    calculation_count: z.number(),
    has_opt: z.boolean(),
    has_freq: z.boolean(),
    has_sp: z.boolean(),
    has_geometry_validation: z.boolean(),
    has_scf_stability: z.boolean(),
    geometry_count: z.number(),
    levels_of_theory: z.record(z.string(), z.array(levelOfTheorySchema)),
}).passthrough()

const availableSectionsSchema = z.object({
    has_observations: z.boolean(),
    has_selections: z.boolean(),
    has_calculations: z.boolean(),
    has_geometries: z.boolean(),
    has_review: z.boolean(),
}).passthrough()

const reviewEntrySchema = z.object({
    status: z.string(),
    reviewed_at: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
}).passthrough()

const selectionSchema = z.object({
    conformer_selection_id: z.number().nullable().optional(),
    selection_kind: z.string(),
    note: z.string().nullable().optional(),
    created_at: z.string().nullable().optional(),
    assignment_scheme: assignmentSchemeSchema.nullable().optional(),
}).passthrough()

const observationCoreSchema = z.object({
    conformer_observation_ref: z.string(),
    scientific_origin: z.string().nullable().optional(),
    note: z.string().nullable().optional(),
    created_at: z.string().nullable().optional(),
    review: recordReviewSchema,
}).passthrough()

// The observation record embeds a list of sibling observations under
// `observations` (the whole basin, this record included) using the same
// shape recursively — see `ScientificConformerObservationRecord` in
// backend/app/schemas/reads/scientific_conformer.py. Zod needs an
// explicit type + z.lazy() to express that self-reference.
export interface ConformerObservation {
    conformer_observation: z.infer<typeof observationCoreSchema>
    conformer_group: z.infer<typeof conformerGroupContextSchema>
    species: z.infer<typeof speciesContextSchema>
    assignment_scheme?: z.infer<typeof assignmentSchemeSchema> | null
    evidence_summary: z.infer<typeof evidenceSummarySchema>
    available_sections: z.infer<typeof availableSectionsSchema>
    observations?: ConformerObservation[] | null
    selections?: z.infer<typeof selectionSchema>[] | null
    calculations?: z.infer<typeof calculationSummarySchema>[] | null
    geometries?: z.infer<typeof geometryLinkSchema>[] | null
    review_history?: z.infer<typeof reviewEntrySchema>[] | null
}

const observationRecordSchema: z.ZodType<ConformerObservation> = z.lazy(() => z.object({
    conformer_observation: observationCoreSchema,
    conformer_group: conformerGroupContextSchema,
    species: speciesContextSchema,
    assignment_scheme: assignmentSchemeSchema.nullable().optional(),
    evidence_summary: evidenceSummarySchema,
    available_sections: availableSectionsSchema,
    observations: z.array(observationRecordSchema).nullable().optional(),
    selections: z.array(selectionSchema).nullable().optional(),
    calculations: z.array(calculationSummarySchema).nullable().optional(),
    geometries: z.array(geometryLinkSchema).nullable().optional(),
    review_history: z.array(reviewEntrySchema).nullable().optional(),
}).passthrough())

const response = z.object({
    record: observationRecordSchema,
})

export async function loadConformerObservation(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ConformerObservation> {
    const query = new URLSearchParams()
    for (const include of ["observations", "selections", "calculations", "geometries", "review"]) {
        query.append("include", include)
    }
    const endpoint = `/api/v1/scientific/conformer-observations/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    return parseScientificResponse(response, payload, "conformer observation").record
}
