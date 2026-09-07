import { z } from "zod"
import { recordReviewSchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * `GET /scientific/reactions/search?reaction_ref=...` --
 * `backend/app/api/routes/scientific/reactions.py`, schema
 * `backend/app/schemas/reads/scientific_reactions.py`. This is the
 * `/reactions/:reactionRef` CHOOSER page's one request: every
 * `reaction_entry` deposited under one `rxn_...` reaction identity, per the
 * owner's ruling (plan §2) that this site never merges or silently picks
 * between them.
 *
 * `formula`/`stoichiometry` on each participant are the SAME §3A-additive
 * fields `reactionEntryApi.ts` marks optional, for the same reason (a
 * pre-deployment API omits them). `reactions/search` serves no
 * `created_at`/deposited-date field for a reaction-entry row (verified live,
 * plan §2/§8) -- this schema does not invent one; the chooser's own table
 * omits a "Deposited" column rather than show a fabricated value.
 */

const participantSchema = z.object({
    species_entry_ref: z.string(),
    species_entry_label: z.string().nullable().optional(),
    smiles: z.string(),
    formula: z.string().nullable().optional(),
    stoichiometry: z.number().optional().default(1),
    participant_index: z.number(),
}).passthrough()

const availabilitySchema = z.object({
    has_kinetics: z.boolean(),
    has_transition_state: z.boolean(),
    kinetics_count: z.number(),
}).passthrough()

const reactionRecordSchema = z.object({
    reaction_ref: z.string(),
    reaction_entry_ref: z.string(),
    equation: z.string(),
    reversible: z.boolean(),
    family: z.string().nullable().optional(),
    review: recordReviewSchema,
    reactants: z.array(participantSchema),
    products: z.array(participantSchema),
    availability: availabilitySchema,
}).passthrough()

const reviewStatusSummarySchema = z.object({
    approved: z.number().default(0),
    under_review: z.number().default(0),
    not_reviewed: z.number().default(0),
    deprecated: z.number().default(0),
    rejected: z.number().default(0),
    total: z.number().default(0),
}).passthrough()

const searchResponseSchema = z.object({
    review_summary: reviewStatusSummarySchema,
    records: z.array(reactionRecordSchema),
}).passthrough()

export type ReactionOverviewRecord = z.infer<typeof reactionRecordSchema>
export type ReactionOverviewParticipant = z.infer<typeof participantSchema>
export type ReactionOverview = z.infer<typeof searchResponseSchema>

export async function loadReactionOverview(
    reactionRef: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ReactionOverview> {
    const query = new URLSearchParams()
    query.set("reaction_ref", reactionRef)
    const endpoint = `/api/v1/scientific/reactions/search?${query}`
    const payload = await requestScientificJson(endpoint, signal, onRateLimited)
    return parseScientificResponse(searchResponseSchema, payload, "reaction")
}
