import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
export { ScientificApiError } from "./scientificTransport"

const entrySchema = z.object({
    species_entry_ref: z.string(),
}).passthrough()

const speciesRecordSchema = z.object({
    species_ref: z.string(),
    entries: z.array(entrySchema),
}).passthrough()

const speciesSearchSchema = z.object({
    records: z.array(speciesRecordSchema),
}).passthrough()

const structureRecordSchema = z.object({
    species_ref: z.string(),
    species_entry_ref: z.string(),
}).passthrough()

const structureSearchSchema = z.object({
    records: z.array(structureRecordSchema),
}).passthrough()

export type SearchMatch = { speciesRef: string; entryRef?: string }
export type IdentifierSearch =
    | { kind: "formula"; value: string }
    | { kind: "species-ref"; value: string }
    | { kind: "species-entry-ref"; value: string }
    | { kind: "smiles"; value: string }
    | { kind: "inchi"; value: string }
    | { kind: "inchi-key"; value: string }

export async function searchSpeciesExact(
    identifier: IdentifierSearch,
    signal?: AbortSignal,
): Promise<SearchMatch[]> {
    const query = new URLSearchParams({ limit: "50" })
    if (identifier.kind === "formula") query.set("formula", identifier.value)
    if (identifier.kind === "species-ref") query.set("species_ref", identifier.value)
    if (identifier.kind === "species-entry-ref") query.set("species_entry_ref", identifier.value)
    if (identifier.kind === "formula" || identifier.kind === "species-ref" || identifier.kind === "species-entry-ref") {
        const parsed = parseScientificResponse(
            speciesSearchSchema,
            await requestScientificJson(`/api/v1/scientific/species/search?${query}`, signal),
            "scientific search",
        )
        if (identifier.kind === "species-entry-ref") {
            return parsed.records.flatMap((record) => record.entries.map((entry) => ({
                speciesRef: record.species_ref,
                entryRef: entry.species_entry_ref,
            })))
        }
        return parsed.records.map((record) => ({ speciesRef: record.species_ref }))
    }

    const field = identifier.kind === "smiles" ? "query_smiles"
        : identifier.kind === "inchi" ? "query_inchi" : "query_inchi_key"
    query.set(field, identifier.value)
    query.set("mode", "exact")
    const parsed = parseScientificResponse(structureSearchSchema,
        await requestScientificJson(`/api/v1/scientific/species/structure-search?${query}`, signal),
        "scientific search",
    )
    return parsed.records.map((record) => ({ speciesRef: record.species_ref, entryRef: record.species_entry_ref }))
}
