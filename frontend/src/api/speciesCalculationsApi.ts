import { z } from "zod"
import { paginationSchema } from "./listEnvelope"
import { nextPageRequest, pageQuery } from "../domain/paging"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

// ---------------------------------------------------------------------------
// GET /api/v1/scientific/species-calculations/search -- the one endpoint on
// this archive whose per-record shape actually carries an electronic energy
// alongside its owning calculation ref (see
// `backend/app/schemas/reads/scientific_species_calculations.py`,
// `SpeciesCalculationsSearchRecord.energy`). The species entry page uses
// this, filtered to `calculation_type=sp`, to surface the single-point
// energy the owner asked to see directly on the entry page rather than only
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

/**
 * Loads every `sp` calculation's electronic energy for one species entry,
 * keyed by `calculation_ref` for `ConformerSinglePointTab` to join against
 * the calculations it already has. It is a join index, not a list the reader
 * pages, so it reads every page, up to `SINGLE_POINT_MAX_PAGES`; past that it
 * says it stopped (`capped`) instead of presenting the rest as absent. It runs
 * only when the single-point tab is open (`useSpeciesSinglePoints`), never as
 * part of loading the entry.
 *
 * Returns `[]` (not a thrown error) on any network/parse failure -- this is
 * a "nice to have" enrichment of a page that otherwise renders fine without
 * it (the calculation ref link still works).
 */
export async function loadSpeciesSinglePointEnergies(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<SinglePointEnergies> {
    try {
        const records: SpeciesCalculationEnergyRecord[] = []
        let request = { offset: 0, limit: SINGLE_POINT_PAGE_SIZE }
        for (let page = 0; page < SINGLE_POINT_MAX_PAGES; page += 1) {
            const query = new URLSearchParams({ species_entry_ref: entryRef, calculation_type: "sp" })
            for (const [key, value] of pageQuery(request)) query.set(key, value)
            const payload = await requestScientificJson(`/api/v1/scientific/species-calculations/search?${query}`, signal, onRateLimited)
            const response = parseScientificResponse(responseSchema, payload, "species calculation energy")
            records.push(...response.records)
            const next = response.pagination ? nextPageRequest(response.pagination) : null
            if (!next) return { records, capped: false }
            request = next
        }
        return { records, capped: true }
    } catch {
        // A caller-initiated abort (route navigated away) is not a failure to swallow silently forever, but both
        // callers already ignore an aborted fetch's result, so an empty result is a harmless value either way.
        return { records: [], capped: false }
    }
}
