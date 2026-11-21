import { z } from "zod"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// The method and basis-set catalogue: what a stored spelling names, and the articles that define it.
//
// - GET /scientific/methods                 (whole catalogue, key order, unpaged)
// - GET /scientific/methods/{handle}        (one entry and the levels of theory that use it)
// - GET /scientific/basis-sets
// - GET /scientific/basis-sets/{handle}
// - the `catalogue` section of a level-of-theory record (`include=catalogue`)
//
// Shapes from `backend/app/schemas/reads/scientific_method_catalogue.py`. Enum-like fields stay plain strings so a
// token this client has not met still parses; `domain/methodCatalogue.ts` words the known ones.

export const catalogueCitationSchema = z.object({
    literature_ref: z.string(),
    role: z.string(),
    position: z.number(),
    title: z.string(),
    title_source: z.string().nullable().optional(),
    journal: z.string().nullable().optional(),
    year: z.number().nullable().optional(),
    volume: z.string().nullable().optional(),
    issue: z.string().nullable().optional(),
    pages: z.string().nullable().optional(),
    authors: z.array(z.string()).default([]),
    // Served only when the registry resolved the citation.
    doi: z.string().nullable().optional(),
}).passthrough()

const catalogueUsageSchema = z.object({
    level_usage_count: z.number().default(0),
    calculation_usage_count: z.number().default(0),
}).passthrough()

const cardBaseShape = {
    catalogue_ref: z.string(),
    key: z.string(),
    keys: z.array(z.string()).default([]),
    short_name: z.string(),
    full_name: z.string(),
    description: z.string(),
    references: z.array(catalogueCitationSchema).default([]),
    spellings_seen: z.array(z.string()).default([]),
    usage: catalogueUsageSchema.default({ level_usage_count: 0, calculation_usage_count: 0 }),
}

export const methodCatalogueCardSchema = z.object({
    ...cardBaseShape,
    kind: z.string(),
    // Derived from the method-family classification; null for a dispersion correction.
    family: z.string().nullable().optional(),
    dft_rung: z.number().nullable().optional(),
}).passthrough()

export const basisSetCatalogueCardSchema = z.object({
    ...cardBaseShape,
    role: z.string(),
    family: z.string(),
}).passthrough()

/** The catalogue section of a level-of-theory record; a slot is `null` when its spelling keys to no entry. */
export const levelOfTheoryCatalogueSchema = z.object({
    method: methodCatalogueCardSchema.nullable().optional(),
    dispersion: methodCatalogueCardSchema.nullable().optional(),
    basis: basisSetCatalogueCardSchema.nullable().optional(),
    aux_basis: basisSetCatalogueCardSchema.nullable().optional(),
    cabs_basis: basisSetCatalogueCardSchema.nullable().optional(),
}).passthrough()

const catalogueLevelUsageSchema = z.object({
    level_of_theory_ref: z.string(),
    label: z.string(),
    calculation_usage_count: z.number(),
}).passthrough()

export type CatalogueCitation = z.infer<typeof catalogueCitationSchema>
export type MethodCatalogueCard = z.infer<typeof methodCatalogueCardSchema>
export type BasisSetCatalogueCard = z.infer<typeof basisSetCatalogueCardSchema>
export type CatalogueCard = MethodCatalogueCard | BasisSetCatalogueCard
export type LevelOfTheoryCatalogue = z.infer<typeof levelOfTheoryCatalogueSchema>
export type CatalogueLevelUsage = z.infer<typeof catalogueLevelUsageSchema>

/** One catalogue entry and the levels of theory that use it, whichever surface it came from. */
export type CatalogueEntryRecord =
    | { kind: "method"; card: MethodCatalogueCard; levels: CatalogueLevelUsage[] }
    | { kind: "basis"; card: BasisSetCatalogueCard; levels: CatalogueLevelUsage[] }

const methodBrowseResponseSchema = z.object({ records: z.array(methodCatalogueCardSchema) }).passthrough()
const basisBrowseResponseSchema = z.object({ records: z.array(basisSetCatalogueCardSchema) }).passthrough()

const methodDetailResponseSchema = z.object({
    record: z.object({
        method: methodCatalogueCardSchema,
        levels: z.array(catalogueLevelUsageSchema).default([]),
    }).passthrough(),
}).passthrough()

const basisDetailResponseSchema = z.object({
    record: z.object({
        basis_set: basisSetCatalogueCardSchema,
        levels: z.array(catalogueLevelUsageSchema).default([]),
    }).passthrough(),
}).passthrough()

/** Every method and dispersion correction in the catalogue, in key order. */
export async function loadMethodCatalogue(signal?: AbortSignal): Promise<MethodCatalogueCard[]> {
    const payload = await requestScientificJson("/api/v1/scientific/methods", signal)
    return parseScientificResponse(methodBrowseResponseSchema, payload, "method catalogue").records
}

/** Every basis set in the catalogue, in key order. */
export async function loadBasisSetCatalogue(signal?: AbortSignal): Promise<BasisSetCatalogueCard[]> {
    const payload = await requestScientificJson("/api/v1/scientific/basis-sets", signal)
    return parseScientificResponse(basisBrowseResponseSchema, payload, "basis-set catalogue").records
}

/** `/methods/method/:key`: a catalogue ref (`mcat_...`) or any spelling that keys to the entry. */
export async function loadMethodCatalogueEntry(
    handle: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<CatalogueEntryRecord> {
    const endpoint = `/api/v1/scientific/methods/${encodeURIComponent(handle)}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    const record = parseScientificResponse(methodDetailResponseSchema, payload, "method").record
    return { kind: "method", card: record.method, levels: record.levels }
}

/** `/methods/basis/:key`: a catalogue ref (`bcat_...`) or any spelling that keys to the entry. */
export async function loadBasisSetCatalogueEntry(
    handle: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<CatalogueEntryRecord> {
    const endpoint = `/api/v1/scientific/basis-sets/${encodeURIComponent(handle)}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    const record = parseScientificResponse(basisDetailResponseSchema, payload, "basis set").record
    return { kind: "basis", card: record.basis_set, levels: record.levels }
}
