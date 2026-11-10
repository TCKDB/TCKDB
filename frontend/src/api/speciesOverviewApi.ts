import {
    scientificSpeciesSearchSchema,
    type ScientificSpeciesRecord,
} from "./scientificSpeciesSchemas"
import type { HiddenByDefault } from "./listEnvelope"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * The species record, plus what the archive said about the entries it left out of it: `entriesHiddenByDefault` is the
 * server's own count of rejected / deprecated entries the default visibility did not list (`null` when it hid none or
 * made no statement), so a species with unlisted entries is not shown as if it had only the ones below.
 */
export type SpeciesOverview = ScientificSpeciesRecord & { entriesHiddenByDefault: HiddenByDefault | null }

export async function loadSpeciesOverview(
    speciesRef: string,
    signal?: AbortSignal,
): Promise<SpeciesOverview | null> {
    const query = new URLSearchParams({ species_ref: speciesRef, limit: "1" })
    const payload = await requestScientificJson(`/api/v1/scientific/species/search?${query}`, signal)
    const response = parseScientificResponse(scientificSpeciesSearchSchema, payload, "species")
    const record = response.records.find((candidate) => candidate.species_ref === speciesRef)
    return record ? { ...record, entriesHiddenByDefault: response.request?.hidden_by_default ?? null } : null
}
