import { z } from "zod"
import { paginationSchema } from "./listEnvelope"
import { levelOfTheorySchema } from "./scientificSchemas"
import { nextPageRequest, pageQuery } from "../domain/paging"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// ---------------------------------------------------------------------------
// GET /api/v1/scientific/species-calculations/search -- the one endpoint on
// this archive whose per-record shape actually carries an electronic energy
// alongside its owning calculation ref (see
// `backend/app/schemas/reads/scientific_species_calculations.py`,
// `SpeciesCalculationsSearchRecord.energy`). The species entry page uses
// this, filtered to `calculation_type=sp`, to surface the single-point
// energy directly on the entry page rather than only
// reachable by following a link to the calculation detail page -- the
// `conformers/search` shape the rest of the entry page reads from
// (`api/speciesEntryApi.ts`) deliberately does NOT carry `results` on its
// embedded calculation summaries (see `ConformerCalculationSummary`'s own
// docstring: "Heavy include sections (results, ...) ... are not surfaced
// here").
//
// Only the fields this page reads are typed below (`.passthrough()` keeps
// the rest); this is not a general client for the endpoint's full filter
// surface.
// ---------------------------------------------------------------------------

const energyBlockSchema = z.object({
    energy_hartree: z.number().nullable().optional(),
    energy_kind: z.string(),
}).passthrough()

const conformerContextSchema = z.object({
    conformer_observation_ref: z.string(),
    conformer_group_ref: z.string(),
}).passthrough()

const recordSchema = z.object({
    calculation: z.object({
        calculation_ref: z.string(),
        calculation_type: z.string(),
    }).passthrough(),
    energy: energyBlockSchema.nullable().optional(),
    conformer: conformerContextSchema.nullable().optional(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    software_release: z.object({ software: z.string(), version: z.string().nullish() }).passthrough().nullable().optional(),
}).passthrough()

const responseSchema = z.object({
    records: z.array(recordSchema),
    pagination: paginationSchema.optional(),
}).passthrough()

export type SpeciesCalculationEnergyRecord = z.infer<typeof recordSchema>

export type SinglePointEnergies = {
    records: SpeciesCalculationEnergyRecord[]
    /** More energies exist than the walk read (the page cap was reached); the missing ones are NOT absent in the archive. */
    capped: boolean
}

export const SINGLE_POINT_PAGE_SIZE = 200
export const SINGLE_POINT_MAX_PAGES = 50

async function walkEnergies(
    entryRef: string,
    calculationType: "sp" | "opt",
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<SinglePointEnergies> {
    const records: SpeciesCalculationEnergyRecord[] = []
    let request = { offset: 0, limit: SINGLE_POINT_PAGE_SIZE }
    for (let page = 0; page < SINGLE_POINT_MAX_PAGES; page += 1) {
        const query = new URLSearchParams({ species_entry_ref: entryRef, calculation_type: calculationType })
        for (const [key, value] of pageQuery(request)) query.set(key, value)
        const payload = await requestScientificJson(`/api/v1/scientific/species-calculations/search?${query}`, signal, onRateLimited)
        const response = parseScientificResponse(responseSchema, payload, "species calculation energy")
        records.push(...response.records)
        const next = response.pagination ? nextPageRequest(response.pagination) : null
        if (!next) return { records, capped: false }
        request = next
    }
    return { records, capped: true }
}

/**
 * Every `sp` calculation's electronic energy for one species entry, read page by page up to `SINGLE_POINT_MAX_PAGES`;
 * past that it says it stopped (`capped`) instead of presenting the rest as absent. A failed read rejects, so a caller
 * can say the energies could not be read instead of showing them as missing.
 */
export function loadSpeciesSinglePointEnergies(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<SinglePointEnergies> {
    return walkEnergies(entryRef, "sp", signal, onRateLimited)
}

/** The final energy of every `opt` calculation of one entry, read the same way as the single-point energies. */
export function loadSpeciesOptimisationEnergies(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<SinglePointEnergies> {
    return walkEnergies(entryRef, "opt", signal, onRateLimited)
}
