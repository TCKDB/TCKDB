import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * The type of each calculation on the other end of this calculation's dependency edges, from the existing
 * calculation search: `child_calculation_ref` returns the parents of that calculation and `parent_calculation_ref`
 * returns its children. The map is ref to calculation type; a calculation the read does not return is simply absent.
 */
const searchSchema = z.object({
    records: z.array(z.object({
        calculation: z.object({ calculation_ref: z.string(), type: z.string() }).passthrough(),
    }).passthrough()),
}).passthrough()

export type CalculationTypes = Readonly<Record<string, string>>

async function neighbourTypes(query: URLSearchParams, signal?: AbortSignal, onRateLimited?: (s: number) => void): Promise<CalculationTypes> {
    query.set("limit", "200")
    const payload = await requestScientificJson(`/api/v1/scientific/calculations/search?${query}`, signal, onRateLimited)
    const response = parseScientificResponse(searchSchema, payload, "calculation neighbours")
    return Object.fromEntries(response.records.map((row) => [row.calculation.calculation_ref, row.calculation.type]))
}

type Loader = (key: string, signal?: AbortSignal, onRateLimited?: (s: number) => void) => Promise<CalculationTypes>

/** `key` is this calculation's ref; the calculations it depends on. */
export const loadParentTypes: Loader = (key, signal, onRateLimited) =>
    neighbourTypes(new URLSearchParams({ child_calculation_ref: key }), signal, onRateLimited)

/** `key` is this calculation's ref; the calculations that depend on it. */
export const loadChildTypes: Loader = (key, signal, onRateLimited) =>
    neighbourTypes(new URLSearchParams({ parent_calculation_ref: key }), signal, onRateLimited)
