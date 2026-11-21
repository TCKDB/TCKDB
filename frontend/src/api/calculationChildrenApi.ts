import { z } from "zod"
import { resultsSchema } from "./calculationApi"
import { levelOfTheorySchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * The calculations that depend on one calculation, each with its own result and level of theory, in one search read
 * (`parent_calculation_ref`, `include=results`). Every value belongs to the child it is listed under.
 */
const childSchema = z.object({
    calculation: z.object({ calculation_ref: z.string(), type: z.string() }).passthrough(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: z.object({ software: z.string(), version: z.string().nullable().optional() }).passthrough().nullable().optional(),
    results: resultsSchema.nullable().optional(),
}).passthrough()

const searchSchema = z.object({ records: z.array(childSchema) }).passthrough()

export type CalculationChild = z.infer<typeof childSchema>

export async function loadCalculationChildren(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<CalculationChild[]> {
    const query = new URLSearchParams({ parent_calculation_ref: ref, include: "results", limit: "50" })
    const payload = await requestScientificJson(`/api/v1/scientific/calculations/search?${query}`, signal, onRateLimited)
    return parseScientificResponse(searchSchema, payload, "dependent calculations").records
}
