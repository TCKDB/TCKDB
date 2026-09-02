import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
export { ScientificApiError } from "./scientificTransport"

/**
 * Client for the two `/scientific/species/structure-search` modes that had
 * no UI: `substructure` (RDKit cartridge `@>`, SMILES or SMARTS) and
 * `similarity` (Tanimoto over Morgan-bit fingerprints, SMILES). `exact`
 * mode is `IdentifierSearch`'s concern (`scientificApi.ts`'s
 * `searchSpeciesExact`) -- this file is deliberately narrower than the
 * backend's three-mode endpoint because the two UIs answer different
 * reader questions ("what is this, exactly" vs. "what looks like this").
 *
 * See `backend/app/schemas/reads/scientific_structure_search.py` for the
 * response contract this schema is read defensively against.
 */

const structureMatchSchema = z.object({
    mode: z.enum(["substructure", "similarity", "exact"]),
    // Populated only for similarity records; absent/null otherwise -- kept
    // nullable rather than coerced to 0, since 0.0 is a real (if unlikely)
    // score and must not be confused with "no score computed".
    similarity_score: z.number().nullable().optional(),
}).passthrough()

const structureSearchRecordSchema = z.object({
    species_ref: z.string(),
    species_entry_ref: z.string(),
    smiles: z.string(),
    charge: z.number().nullable().optional(),
    multiplicity: z.number().nullable().optional(),
    species_entry_label: z.string().nullable().optional(),
    match: structureMatchSchema,
    review: z.object({ status: z.string() }).passthrough(),
}).passthrough()

const paginationSchema = z.object({
    offset: z.number(),
    limit: z.number(),
    returned: z.number(),
    total: z.number(),
}).passthrough()

const structureSearchResponseSchema = z.object({
    records: z.array(structureSearchRecordSchema),
    pagination: paginationSchema,
}).passthrough()

export type StructureSearchMode = "substructure" | "similarity"

export type StructureSearchRecord = {
    speciesRef: string
    entryRef: string
    smiles: string
    charge: number | null
    multiplicity: number | null
    label: string | null
    /** Present only when `mode === "similarity"`. */
    similarityScore: number | null
    reviewStatus: string
}

export type StructureSearchResult = {
    records: StructureSearchRecord[]
    total: number
}

export type StructureQuery =
    | { queryKind: "smiles"; value: string }
    | { queryKind: "smarts"; value: string }

export type StructureSearchParams = {
    query: StructureQuery
    mode: StructureSearchMode
    /** Used only when `mode === "similarity"`; ignored otherwise. */
    similarityThreshold?: number
    minReviewStatus?: string
    includeRejected?: boolean
    includeDeprecated?: boolean
    limit?: number
}

/**
 * Run a substructure or similarity search. SMARTS (`queryKind: "smarts"`)
 * is sent as `query_smarts`, never as `query_smiles` -- a SMARTS pattern
 * is not a molecule and the cartridge parses the two with different RDKit
 * calls (`qmol_from_smarts` vs. `mol_from_smiles`); see
 * `structure_search.py`'s `_run_substructure_query`.
 *
 * :raises ScientificApiError: `code === "invalid_structure_query"` when
 *     RDKit could not parse the query -- distinct from a resolved
 *     zero-record response, which this function returns normally.
 */
export async function searchStructure(
    params: StructureSearchParams,
    signal?: AbortSignal,
): Promise<StructureSearchResult> {
    const query = new URLSearchParams({ mode: params.mode, limit: String(params.limit ?? 50) })
    query.set(params.query.queryKind === "smarts" ? "query_smarts" : "query_smiles", params.query.value)
    if (params.mode === "similarity") {
        query.set("similarity_threshold", String(params.similarityThreshold ?? 0.5))
    }
    if (params.minReviewStatus) query.set("min_review_status", params.minReviewStatus)
    if (params.includeRejected) query.set("include_rejected", "true")
    if (params.includeDeprecated) query.set("include_deprecated", "true")

    const parsed = parseScientificResponse(
        structureSearchResponseSchema,
        await requestScientificJson(`/api/v1/scientific/species/structure-search?${query}`, signal),
        "structure search",
    )
    return {
        records: parsed.records.map((record) => ({
            speciesRef: record.species_ref,
            entryRef: record.species_entry_ref,
            smiles: record.smiles,
            charge: record.charge ?? null,
            multiplicity: record.multiplicity ?? null,
            label: record.species_entry_label ?? null,
            similarityScore: record.match.similarity_score ?? null,
            reviewStatus: record.review.status,
        })),
        total: parsed.pagination.total,
    }
}
