import { z } from "zod"
import { walk, type AllPages, type Loader, type WalkControl } from "./entryNumbersApi"
import { listRequestEchoSchema, paginationSchema } from "./listEnvelope"
import { kineticsRecordSchema, type ReactionKineticsRecord } from "./reactionEntryApi"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"
import { pageQuery, parsePagedRef } from "../domain/paging"
import { depositWarningsEnvelopeShape, pickDepositWarnings, type DepositWarnings } from "./depositWarningsSchema"

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

const assessmentsListSchema = z.object({
    ...depositWarningsEnvelopeShape,
    records: z.array(kineticsRecordSchema),
}).passthrough()

/** What `GET /reaction-entries/{ref}/kinetics?include=trust&include=assessments` says about each record, by `kinetics_ref`. */
export type ReactionKineticsAssessments = {
    byKineticsRef: Map<string, { trust: ReactionKineticsRecord["trust"]; assessments: ReactionKineticsRecord["assessments"] }>
    depositWarnings: DepositWarnings
}

/**
 * One supplementary read per reaction entry: trust, reproducibility and deposit warnings for its kinetics records. The
 * `/full` read the page is built from carries trust at most, and never warnings. Records the list does not return are
 * simply absent from the map; callers say so rather than borrow a sibling's values.
 */
export async function loadReactionEntryKineticsAssessments(
    entryRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ReactionKineticsAssessments> {
    const query = new URLSearchParams()
    query.append("include", "trust")
    query.append("include", "assessments")
    query.set("limit", "200")
    const payload = await requestScientificJson(`/api/v1/scientific/reaction-entries/${encodeURIComponent(entryRef)}/kinetics?${query}`, signal, onRateLimited)
    const parsed = parseScientificResponse(assessmentsListSchema, payload, "kinetics")
    return {
        byKineticsRef: new Map(parsed.records.map((record) => [record.kinetics_ref, { trust: record.trust, assessments: record.assessments }])),
        depositWarnings: pickDepositWarnings(parsed),
    }
}
