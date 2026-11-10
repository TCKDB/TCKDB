import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * The five existing reads behind a species entry's relationship map. Each asks for one row and keeps the server's own
 * `pagination.total`, so a count shown on the map is what the read said and nothing more. Every loader takes its whole
 * query as one string key, which is what `dedupedFetch` caches on.
 */

const probeSchema = z.object({
    records: z.array(z.record(z.string(), z.unknown())),
    pagination: z.object({ total: z.number() }).passthrough().nullish(),
}).passthrough()

export type NeighbourProbe = {
    /** The server's total for this query; the number of rows returned when the read carries no pagination. */
    total: number
    /** The one row returned, with no claim that it is special among the others. */
    row: Record<string, unknown> | null
}

async function probe(path: string, query: URLSearchParams, label: string, signal?: AbortSignal, onRateLimited?: (s: number) => void): Promise<NeighbourProbe> {
    const payload = await requestScientificJson(`/api/v1/scientific/${path}?${query}`, signal, onRateLimited)
    const response = parseScientificResponse(probeSchema, payload, label)
    return { total: response.pagination?.total ?? response.records.length, row: response.records[0] ?? null }
}

type Loader = (key: string, signal?: AbortSignal, onRateLimited?: (s: number) => void) => Promise<NeighbourProbe>

/** `key` is the species entry ref. */
export const loadMoleculeProbe: Loader = (key, signal, onRateLimited) =>
    probe("species/search", new URLSearchParams({ species_entry_ref: key, limit: "1" }), "molecule", signal, onRateLimited)

/** `key` is the species entry ref. */
export const loadConformerProbe: Loader = (key, signal, onRateLimited) =>
    probe("conformers/search", new URLSearchParams({ species_entry_ref: key, limit: "1" }), "conformer", signal, onRateLimited)

/** `key` is the species entry ref. */
export const loadCalculationProbe: Loader = (key, signal, onRateLimited) =>
    probe("species-calculations/search", new URLSearchParams({ species_entry_ref: key, limit: "1" }), "calculation", signal, onRateLimited)

/** `key` is the species entry ref. */
export const loadNetworkProbe: Loader = (key, signal, onRateLimited) =>
    probe("networks/search", new URLSearchParams({ species_entry_ref: key, limit: "1" }), "network", signal, onRateLimited)

/** `key` is the molecule's SMILES; reactions with it on either side. */
export const loadReactionProbe: Loader = (key, signal, onRateLimited) =>
    probe("reactions/search", new URLSearchParams({ reactants: key, direction: "either", limit: "1" }), "reaction", signal, onRateLimited)
