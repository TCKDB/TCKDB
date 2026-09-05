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

const observation = z.object({
    conformer_observation: z.object({
        conformer_observation_ref: z.string(), scientific_origin: z.string().nullable().optional(),
        note: z.string().nullable().optional(), review: recordReviewSchema,
    }).passthrough(),
    evidence_summary: z.object({
        calculation_count: z.number(), geometry_count: z.number(), has_opt: z.boolean(),
        has_freq: z.boolean(), has_sp: z.boolean(),
        levels_of_theory: z.record(z.string(), z.array(levelOfTheorySchema)),
    }).passthrough(),
    calculations: z.array(calculationSummarySchema).nullable().optional(),
    geometries: z.array(geometryLinkSchema).nullable().optional(),
}).passthrough()
const response = z.object({
    record: z.object({
        conformer_group: z.object({
            conformer_group_ref: z.string(),
            label: z.string().nullable(),
            note: z.string().nullable().optional(),
            review: recordReviewSchema,
        }).passthrough(),
        species: z.object({
            species_ref: z.string(),
            species_entry_ref: z.string(),
            species_entry_label: z.string().nullable().optional(),
            canonical_smiles: z.string().nullable().optional(),
        }).passthrough(),
        observations_summary: z.object({
            total: z.number(),
            by_scientific_origin: z.record(z.string(), z.number()),
        }).passthrough(),
        evidence_summary: z.object({
            calculation_count: z.number(),
            optimization_chain_count: z.number(),
            geometry_count: z.number(),
            evidence_coverage: z.object({
                opt: z.number(),
                freq: z.number(),
                sp: z.number(),
            }).passthrough(),
        }).passthrough(),
        observations: z.array(observation).nullable().optional(),
        calculations: z.array(calculationSummarySchema).nullable().optional(),
        geometries: z.array(geometryLinkSchema).nullable().optional(),
    }).passthrough(),
})

export type ConformerGroup = z.infer<typeof response>["record"]

export async function loadConformerGroup(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ConformerGroup> {
    const query = new URLSearchParams()
    for (const include of ["observations", "calculations", "geometries"]) query.append("include", include)
    const endpoint = `/api/v1/scientific/conformer-groups/${encodeURIComponent(ref)}?${query}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    return parseScientificResponse(response, payload, "conformer group").record
}
