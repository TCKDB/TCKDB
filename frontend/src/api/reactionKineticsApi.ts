import { z } from "zod"
import { walk, type AllPages, type Loader, type WalkControl } from "./entryNumbersApi"
import { listRequestEchoSchema, paginationSchema } from "./listEnvelope"
import { kineticsRecordSchema, type ReactionKineticsRecord } from "./reactionEntryApi"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
import { pageQuery, parsePagedRef } from "../domain/paging"

/** `GET /scientific/kinetics/search?reaction_ref`: every kinetics record of every deposited version of one reaction. */
const searchSchema = z.object({
    records: z.array(z.object({
        reaction: z.object({ reaction_ref: z.string(), reaction_entry_ref: z.string(), deposit_position: z.number().int() }).passthrough(),
        kinetics: kineticsRecordSchema,
    }).passthrough()),
    pagination: paginationSchema,
    request: listRequestEchoSchema.optional(),
}).passthrough()

/** A kinetics record with the version (reaction entry) it belongs to and that version's deposit position. */
export type ReactionKineticsEntry = { entryRef: string; entryPosition: number; record: ReactionKineticsRecord }

export const loadReactionKineticsPage: Loader<ReactionKineticsEntry> = async (pagedRef, signal, onRateLimited) => {
    const { ref, request } = parsePagedRef(pagedRef)
    const query = request ? pageQuery(request) : new URLSearchParams()
    query.set("reaction_ref", ref)
    query.set("include", "provenance")
    const payload = await requestScientificJson(`/api/v1/scientific/kinetics/search?${query}`, signal, onRateLimited)
    const response = parseScientificResponse(searchSchema, payload, "kinetics")
    return { ...response, records: response.records.map((row) => ({ entryRef: row.reaction.reaction_entry_ref, entryPosition: row.reaction.deposit_position, record: row.kinetics })) }
}

/** Every kinetics record of one reaction, walked up to the cap. */
export function loadReactionKinetics(reactionRef: string, control: WalkControl): Promise<AllPages<ReactionKineticsEntry>> {
    return walk(reactionRef, loadReactionKineticsPage, control)
}
