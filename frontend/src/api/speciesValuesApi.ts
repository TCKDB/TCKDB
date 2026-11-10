import { z } from "zod"
import { listRequestEchoSchema, paginationSchema } from "./listEnvelope"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
import { statmechListResponseSchema, type StatmechRecord } from "./statmechApi"
import { thermoRecordSchema, type ThermoRecord } from "./thermoApi"
import { walk, type AllPages, type Loader, type WalkControl } from "./entryNumbersApi"
import { pageQuery, parsePagedRef } from "../domain/paging"

const thermoSearchSchema = z.object({
    records: z.array(z.object({
        species: z.object({ species_ref: z.string(), species_entry_ref: z.string() }).passthrough(),
        thermo: thermoRecordSchema,
    }).passthrough()),
    pagination: paginationSchema,
    request: listRequestEchoSchema.optional(),
}).passthrough()

/** A thermo record with the version (species entry) it belongs to, as `thermo/search` serves it. */
export type SpeciesThermoRecord = { entryRef: string; record: ThermoRecord }
/** A statmech record with the version it belongs to, read from the record's own `species` block. */
export type SpeciesStatmechRecord = { entryRef: string | null; record: StatmechRecord }

function endpoint(kind: "thermo" | "statmech", pagedRef: string): string {
    const { ref, request } = parsePagedRef(pagedRef)
    const query = request ? pageQuery(request) : new URLSearchParams()
    query.set("species_ref", ref)
    return `/api/v1/scientific/${kind}/search?${query}`
}

export const loadSpeciesThermo: Loader<SpeciesThermoRecord> = async (pagedRef, signal, onRateLimited) => {
    const payload = await requestScientificJson(endpoint("thermo", pagedRef), signal, onRateLimited)
    const response = parseScientificResponse(thermoSearchSchema, payload, "thermo")
    return {
        ...response,
        records: response.records.map((row) => ({ entryRef: row.species.species_entry_ref, record: row.thermo })),
    }
}

export const loadSpeciesStatmech: Loader<SpeciesStatmechRecord> = async (pagedRef, signal, onRateLimited) => {
    const payload = await requestScientificJson(endpoint("statmech", pagedRef), signal, onRateLimited)
    const response = parseScientificResponse(statmechListResponseSchema, payload, "statmech")
    return {
        ...response,
        records: response.records.map((record) => ({ entryRef: record.species?.species_entry_ref ?? null, record })),
    }
}

export type SpeciesValues = {
    thermo: AllPages<SpeciesThermoRecord>
    statmech: AllPages<SpeciesStatmechRecord>
}

/** Every thermo and statmech record of every version of one molecule, each list walked up to the cap. */
export async function loadSpeciesValues(speciesRef: string, control: WalkControl): Promise<SpeciesValues> {
    const [thermo, statmech] = await Promise.all([
        walk(speciesRef, loadSpeciesThermo, control),
        walk(speciesRef, loadSpeciesStatmech, control),
    ])
    return { thermo, statmech }
}
