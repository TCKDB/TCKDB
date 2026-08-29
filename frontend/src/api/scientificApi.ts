import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
export { ScientificApiError } from "./scientificTransport"

const entrySchema = z.object({
    species_entry_ref: z.string(),
}).passthrough()

const speciesRecordSchema = z.object({
    species_ref: z.string(),
    // Nullable, not just optional: computed by the archive (#251) and can
    // come back `null` for a real record. `canonical_smiles`/`charge`/
    // `multiplicity` are core identity fields the live API always sends,
    // but are still read defensively since a search-results row must
    // survive a fixture or a future response that omits them, not throw.
    formula: z.string().nullable().optional(),
    canonical_smiles: z.string().nullable().optional(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
    entries: z.array(entrySchema),
}).passthrough()

const speciesSearchSchema = z.object({
    records: z.array(speciesRecordSchema),
}).passthrough()

const structureRecordSchema = z.object({
    species_ref: z.string(),
    species_entry_ref: z.string(),
    // The structure-search endpoint never returns `formula` at all -- it
    // answers with the matched structure, not the species projection --
    // so `formula` is intentionally absent from this schema. See
    // `SearchMatch.formula` below.
    smiles: z.string().nullable().optional(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
}).passthrough()

const structureSearchSchema = z.object({
    records: z.array(structureRecordSchema),
}).passthrough()

export type SearchMatch = {
    speciesRef: string
    entryRef?: string
    /**
     * `null` when the archive computed no formula for this record (#251);
     * `undefined` only for a structure-search match, whose endpoint never
     * projects a formula at all. Both render the same honest fallback --
     * see `IdentifierSearch`'s `MatchHeadline`.
     */
    formula?: string | null
    smiles?: string | null
    charge?: number | null
    multiplicity?: number | null
    /** Present only for species-grain matches (formula/ref search); a structure-search match is already one entry. */
    entryCount?: number
}
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
                formula: record.formula ?? null,
                smiles: record.canonical_smiles ?? null,
                charge: record.charge ?? null,
                multiplicity: record.multiplicity ?? null,
                entryCount: record.entries.length,
            })))
        }
        return parsed.records.map((record) => ({
            speciesRef: record.species_ref,
            formula: record.formula ?? null,
            smiles: record.canonical_smiles ?? null,
            charge: record.charge ?? null,
            multiplicity: record.multiplicity ?? null,
            entryCount: record.entries.length,
        }))
    }

    const field = identifier.kind === "smiles" ? "query_smiles"
        : identifier.kind === "inchi" ? "query_inchi" : "query_inchi_key"
    query.set(field, identifier.value)
    query.set("mode", "exact")
    const parsed = parseScientificResponse(structureSearchSchema,
        await requestScientificJson(`/api/v1/scientific/species/structure-search?${query}`, signal),
        "scientific search",
    )
    // The structure-search endpoint never returns a formula (see the schema
    // comment above): `null` here is a known-absent value, not "not fetched".
    return parsed.records.map((record) => ({
        speciesRef: record.species_ref,
        entryRef: record.species_entry_ref,
        formula: null,
        smiles: record.smiles ?? null,
        charge: record.charge ?? null,
        multiplicity: record.multiplicity ?? null,
    }))
}
