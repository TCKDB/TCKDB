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

/**
 * Loads every `sp` calculation's electronic energy for one species entry,
 * keyed by `calculation_ref` for `ConformerSinglePointTab` to join against
 * the calculations it already has. Returns `[]` (not a thrown error) on any
 * network/parse failure -- this is a "nice to have" enrichment of a page
 * that otherwise renders fine without it (the calculation ref link still
 * works), so a failure here must not take down the whole entry page.
 */
export async function loadSpeciesSinglePointEnergies(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<SpeciesCalculationEnergyRecord[]> {
    // This is a join index (calculation ref -> energy) behind the single-point tab, not a list the reader pages, so it
    // reads EVERY page: stopping at the first would leave the energies of later calculations silently missing.
    const MAX_PAGES = 50
    try {
        const records: SpeciesCalculationEnergyRecord[] = []
        let request = { offset: 0, limit: 200 }
        for (let page = 0; page < MAX_PAGES; page += 1) {
            const query = new URLSearchParams({ species_entry_ref: entryRef, calculation_type: "sp" })
            for (const [key, value] of pageQuery(request)) query.set(key, value)
            const payload = await requestScientificJson(`/api/v1/scientific/species-calculations/search?${query}`, signal, onRateLimited)
            const response = parseScientificResponse(responseSchema, payload, "species calculation energy")
            records.push(...response.records)
            const next = response.pagination ? nextPageRequest(response.pagination) : null
            if (!next) break
            request = next
        }
        return records
    } catch {
        // A caller-initiated abort (route navigated away) is not a failure
        // to swallow silently forever, but re-throwing it here would make
        // `useSpeciesEntry`'s `Promise.all` treat "the reader navigated
        // away" the same as "the archive is broken" -- both this helper's
        // callers already ignore an aborted fetch's result, so [] is a
        // harmless value to resolve with either way.
        return []
    }
}
