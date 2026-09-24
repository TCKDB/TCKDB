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
    formula: z.string().nullable().optional(),
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

// A sibling in the `observations` list is a lean ref+review projection
// (`ConformerObservationSiblingSummary` in
// backend/app/schemas/reads/scientific_conformer.py), not a nested copy
// of this whole record. It used to be: every sibling carried its own
// calculations/geometries/selections/review_history (and a duplicated
// copy of `conformer_group`/`species`), which made an observation
// detail response scale linearly with basin size for a sibling ledger
// that renders only a ref and, sometimes, a review pill (issue #269).
// The full record for any one sibling is one hop away, at that
// sibling's own detail endpoint.
const siblingObservationSchema = z.object({
    conformer_observation: z.object({
        conformer_observation_ref: z.string(),
        review: recordReviewSchema,
    }).passthrough(),
}).passthrough()

export type ConformerObservationSibling = z.infer<typeof siblingObservationSchema>

export interface ConformerObservation {
    conformer_observation: z.infer<typeof observationCoreSchema>
    conformer_group: z.infer<typeof conformerGroupContextSchema>
    species: z.infer<typeof speciesContextSchema>
    assignment_scheme?: z.infer<typeof assignmentSchemeSchema> | null
    evidence_summary: z.infer<typeof evidenceSummarySchema>
    available_sections: z.infer<typeof availableSectionsSchema>
    observations?: ConformerObservationSibling[] | null
    selections?: z.infer<typeof selectionSchema>[] | null
    calculations?: z.infer<typeof calculationSummarySchema>[] | null
    geometries?: z.infer<typeof geometryLinkSchema>[] | null
    review_history?: z.infer<typeof reviewEntrySchema>[] | null
}

const observationRecordSchema: z.ZodType<ConformerObservation> = z.object({
    conformer_observation: observationCoreSchema,
    conformer_group: conformerGroupContextSchema,
    species: speciesContextSchema,
    assignment_scheme: assignmentSchemeSchema.nullable().optional(),
    evidence_summary: evidenceSummarySchema,
    available_sections: availableSectionsSchema,
    observations: z.array(siblingObservationSchema).nullable().optional(),
    selections: z.array(selectionSchema).nullable().optional(),
    calculations: z.array(calculationSummarySchema).nullable().optional(),
    geometries: z.array(geometryLinkSchema).nullable().optional(),
    review_history: z.array(reviewEntrySchema).nullable().optional(),
}).passthrough()

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
