import {
    scientificSpeciesSearchSchema,
    type ScientificSpeciesRecord,
} from "./scientificSpeciesSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

export type SpeciesOverview = ScientificSpeciesRecord

export async function loadSpeciesOverview(
    speciesRef: string,
    signal?: AbortSignal,
): Promise<SpeciesOverview | null> {
    const query = new URLSearchParams({ species_ref: speciesRef, limit: "1" })
    const payload = await requestScientificJson(`/api/v1/scientific/species/search?${query}`, signal)
    const response = parseScientificResponse(scientificSpeciesSearchSchema, payload, "species")
    return response.records.find((record) => record.species_ref === speciesRef) ?? null
}
