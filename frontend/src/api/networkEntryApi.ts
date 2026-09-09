import { z } from "zod"
import { recordReviewSchema } from "./scientificSchemas"
import { loadEntryThermo } from "./thermoApi"
import { parseScientificResponse, requestScientificJson } from "./scientificTransport"

/**
 * `GET /scientific/networks/{ref}?include=states,channels,solves` --
 * `backend/app/api/routes/scientific/networks.py`, schema
 * `backend/app/schemas/reads/scientific_network.py`. See
 * `docs/plans/pressure-dependent-network-surface.md` §3/§4 for the
 * information architecture; this module covers PR 2's slice only
 * (identity, evidence incl. the energy-coverage fact, reactions, review --
 * no diagram, no k(T,P) chart, both later PRs per the plan's §6 slicing).
 *
 * `states`/`channels`/`solves` are the three include tokens this page
 * actually needs:
 * - `states` -- each state's `composition.participants[]` is how the
 *   energy-coverage fact below finds the network's unique participant
 *   species entries (there is no lighter "just the species list" include).
 * - `channels` -- `channels[].microreactions[]` is how the Reactions
 *   section is built (`reactions/search?network_ref=...` is NOT a legal
 *   filter on that endpoint -- confirmed against the live archive and
 *   noted in the plan's own PR 0 mock, `docs/plans/mocks/network-entry.md`
 *   "Another gap found while building the Reactions table").
 * - `solves` -- `solves[0].network_solve_ref`/`me_method`/
 *   `interpolation_model` feed the identity header and the follow-up
 *   `network-solves/{ref}` fetch for the solve's own state-energy/
 *   channel-barrier figures.
 *
 * This one endpoint cannot answer "how much species-level thermo coverage
 * does this network have" or "what does channel_N's microreaction actually
 * say as an equation" on its own -- those need per-species-entry
 * (`/species-entries/{ref}/thermo`) and per-reaction-entry
 * (`/reaction-entries/{ref}/full`) follow-up requests, composed here in
 * `loadNetworkEntry` exactly the way the PR 0 mock's own build process
 * gathered them by hand (see that mock's "What was fetched" section).
 */

// ---------------------------------------------------------------------------
// Shared small shapes
// ---------------------------------------------------------------------------

const softwareReleaseSummarySchema = z.object({
    software_release_ref: z.string().optional(),
    software: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const workflowToolReleaseSummarySchema = z.object({
    workflow_tool_release_ref: z.string().optional(),
    workflow_tool: z.string(),
    version: z.string().nullable().optional(),
}).passthrough()

const literatureSummarySchema = z.object({
    literature_ref: z.string().optional(),
    title: z.string().nullable().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// network core + evidence
// ---------------------------------------------------------------------------

const networkCoreSchema = z.object({
    network_ref: z.string(),
    name: z.string().nullable().optional(),
    description: z.string().nullable().optional(),
    solve_temperature_min_k: z.number().nullable().optional(),
    solve_temperature_max_k: z.number().nullable().optional(),
    solve_pressure_min_bar: z.number().nullable().optional(),
    solve_pressure_max_bar: z.number().nullable().optional(),
    review: recordReviewSchema,
}).passthrough()

const evidenceSummarySchema = z.object({
    species_count: z.number(),
    reaction_count: z.number(),
    state_count: z.number(),
    channel_count: z.number(),
    solve_count: z.number(),
    kinetics_count: z.number(),
    source_calculation_count: z.number(),
    has_chebyshev: z.boolean(),
    has_plog: z.boolean(),
    has_point_kinetics: z.boolean(),
}).passthrough()

// ---------------------------------------------------------------------------
// states (include=states)
// ---------------------------------------------------------------------------

const stateParticipantSchema = z.object({
    species_entry_ref: z.string(),
    species_ref: z.string(),
    canonical_smiles: z.string(),
    species_entry_label: z.string().nullable().optional(),
    stoichiometry: z.number(),
}).passthrough()

const stateCompositionSchema = z.object({
    participants: z.array(stateParticipantSchema).default([]),
    participant_count_total: z.number().default(0),
    participants_truncated: z.boolean().default(false),
    // Server-computed, safe to render (never `network_state.label`, which is
    // depositor free text and null on every state measured against the live
    // archive) -- see `backend/app/schemas/reads/scientific_network_composition.py`.
    state_label: z.string().default(""),
}).passthrough()

const stateSchema = z.object({
    composition_hash: z.string(),
    kind: z.string(),
    label: z.string().nullable().optional(),
    participant_count: z.number(),
    composition: stateCompositionSchema,
}).passthrough()

// ---------------------------------------------------------------------------
// channels (include=channels)
// ---------------------------------------------------------------------------

const microreactionSchema = z.object({
    reaction_entry_ref: z.string(),
    transition_state_entry_ref: z.string().nullable().optional(),
    path_kind: z.string(),
}).passthrough()

const channelSchema = z.object({
    channel_key: z.string().nullable().optional(),
    kind: z.string(),
    mechanism: z.string(),
    source_state_composition_hash: z.string(),
    sink_state_composition_hash: z.string(),
    has_kinetics: z.boolean(),
    microreactions: z.array(microreactionSchema).default([]),
}).passthrough()

// ---------------------------------------------------------------------------
// solves (include=solves)
// ---------------------------------------------------------------------------

const solveSummarySchema = z.object({
    network_solve_ref: z.string(),
    kind: z.string(),
    me_method: z.string().nullable().optional(),
    interpolation_model: z.string().nullable().optional(),
    tmin_k: z.number().nullable().optional(),
    tmax_k: z.number().nullable().optional(),
    pmin_bar: z.number().nullable().optional(),
    pmax_bar: z.number().nullable().optional(),
    review: recordReviewSchema,
}).passthrough()

// ---------------------------------------------------------------------------
// network detail envelope
// ---------------------------------------------------------------------------

const reviewStatusSummarySchema = z.object({
    approved: z.number().default(0),
    under_review: z.number().default(0),
    not_reviewed: z.number().default(0),
    deprecated: z.number().default(0),
    rejected: z.number().default(0),
    total: z.number().default(0),
}).passthrough()

const networkDetailResponseSchema = z.object({
    review_summary: reviewStatusSummarySchema,
    record: z.object({
        network: networkCoreSchema,
        software_release: softwareReleaseSummarySchema.nullable().optional(),
        workflow_tool_release: workflowToolReleaseSummarySchema.nullable().optional(),
        literature: literatureSummarySchema.nullable().optional(),
        evidence_summary: evidenceSummarySchema,
        states: z.array(stateSchema).nullable().optional(),
        channels: z.array(channelSchema).nullable().optional(),
        solves: z.array(solveSummarySchema).nullable().optional(),
    }).passthrough(),
}).passthrough()

// ---------------------------------------------------------------------------
// network-solve detail envelope (state_energies / channel_barriers)
// ---------------------------------------------------------------------------

const stateEnergySchema = z.object({
    state_composition_hash: z.string(),
    energy_kj_mol: z.number(),
    energy_zero_convention: z.string(),
    correction_convention: z.string(),
    convention_note: z.string().nullable().optional(),
    source_calculation_ref: z.string().nullable().optional(),
}).passthrough()

const channelBarrierSchema = z.object({
    channel_key: z.string(),
    reaction_entry_ref: z.string(),
    transition_state_entry_ref: z.string(),
    forward_barrier_kj_mol: z.number(),
    reverse_barrier_kj_mol: z.number(),
    energy_zero_convention: z.string(),
    correction_convention: z.string(),
    convention_note: z.string().nullable().optional(),
    source_calculation_ref: z.string().nullable().optional(),
}).passthrough()

const solveDetailResponseSchema = z.object({
    record: z.object({
        network_solve: z.object({ network_solve_ref: z.string() }).passthrough(),
        state_energies: z.array(stateEnergySchema).nullable().optional(),
        channel_barriers: z.array(channelBarrierSchema).nullable().optional(),
    }).passthrough(),
}).passthrough()

// ---------------------------------------------------------------------------
// reaction-entry equation lookup (no include tokens -- see module docstring)
// ---------------------------------------------------------------------------

const reactionEntryLiteSchema = z.object({
    record: z.unknown().optional(),
    reaction_entry: z.object({
        reaction_entry_ref: z.string(),
        equation: z.string(),
        review: recordReviewSchema,
    }).passthrough(),
}).passthrough()

export type ReactionEntryLite = { reaction_entry_ref: string; equation: string; review: { status: string } }

async function fetchReactionEntryLite(ref: string, signal?: AbortSignal): Promise<ReactionEntryLite> {
    const endpoint = `/api/v1/scientific/reaction-entries/${encodeURIComponent(ref)}/full`
    const payload = await requestScientificJson(endpoint, signal)
    const parsed = parseScientificResponse(reactionEntryLiteSchema, payload, "reaction entry")
    return parsed.reaction_entry
}

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type NetworkState = z.infer<typeof stateSchema>
export type NetworkChannel = z.infer<typeof channelSchema>
export type NetworkSolveSummary = z.infer<typeof solveSummarySchema>
export type NetworkStateEnergy = z.infer<typeof stateEnergySchema>
export type NetworkChannelBarrier = z.infer<typeof channelBarrierSchema>

export type NetworkFullRecord = {
    network: z.infer<typeof networkCoreSchema>
    review_summary: z.infer<typeof reviewStatusSummarySchema>
    software_release: z.infer<typeof softwareReleaseSummarySchema> | null
    workflow_tool_release: z.infer<typeof workflowToolReleaseSummarySchema> | null
    literature: z.infer<typeof literatureSummarySchema> | null
    evidence_summary: z.infer<typeof evidenceSummarySchema>
    states: NetworkState[]
    channels: NetworkChannel[]
    solves: NetworkSolveSummary[]
    /** Every unique participant species entry ref across all states. */
    speciesEntryRefs: string[]
    /** `species_entry_ref` -> whether at least one thermo record was found for it. */
    speciesThermoPresence: Record<string, boolean>
    /** `reaction_entry_ref` -> the equation/review this network's channels reference. */
    reactionEntries: Record<string, ReactionEntryLite>
    /** `null` when this network has no deposited solve to fetch. */
    stateEnergies: NetworkStateEnergy[] | null
    channelBarriers: NetworkChannelBarrier[] | null
    /**
     * True when a solve exists but its energies could not be read. Distinct
     * from `stateEnergies === null`, which means no solve was deposited at
     * all. The page must not let the two collapse: rendering an unreadable
     * solve as an empty one silently understates the archive.
     */
    solveEnergiesUnavailable: boolean
}

type SolveFetch =
    | { status: "absent" }
    | { status: "unavailable" }
    | { status: "ok"; detail: z.infer<typeof solveDetailResponseSchema> }

const NETWORK_INCLUDE = ["states", "channels", "solves"]

/**
 * Composes everything `NetworkEntryPage`'s PR 2 sections need into one
 * `useScientificRecord`-shaped load: the network detail itself, the first
 * solve's own state-energy/channel-barrier figures (if a solve exists), a
 * thermo-presence check for every unique participant species entry (the
 * energy-coverage fact's species half), and the equation/review for every
 * unique reaction entry a channel's microreaction names (the Reactions
 * section). A failure in the network detail, the thermo presence checks, or
 * the reaction-entry lookups fails the whole load -- there is no backend
 * surface that joins them, and each one feeds a *counted* claim ("2 of 9
 * participants carry thermo", "6 of 21 channels carry a reaction"), so a
 * partial success would render a number that is quietly wrong. One honest
 * "unavailable" state beats a false count (`RecordStatus`'s own contract).
 *
 * The solve detail is the deliberate exception. It is supplementary: it
 * feeds one optional disclosure, and the page already renders correctly
 * without it (a network with no deposited solve). Letting it fail the whole
 * load meant a single malformed barrier row blanked the identity header,
 * the reactions table and the review section -- all of which came from a
 * different, well-formed request. So it degrades instead, and reports
 * `solveEnergiesUnavailable` so the page can say the energies were
 * unreadable rather than render as though none exist.
 */
export async function loadNetworkEntry(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<NetworkFullRecord> {
    const query = new URLSearchParams()
    for (const token of NETWORK_INCLUDE) query.append("include", token)
    const detailPayload = await requestScientificJson(
        `/api/v1/scientific/networks/${encodeURIComponent(ref)}?${query}`,
        signal,
        onRateLimited,
    )
    const detail = parseScientificResponse(networkDetailResponseSchema, detailPayload, "network")
    const { record } = detail

    const states = record.states ?? []
    const channels = record.channels ?? []
    const solves = record.solves ?? []

    const speciesEntryRefs = Array.from(new Set(
        states.flatMap((state) => state.composition.participants.map((p) => p.species_entry_ref)),
    ))
    const reactionEntryRefs = Array.from(new Set(
        channels.flatMap((channel) => channel.microreactions.map((m) => m.reaction_entry_ref)),
    ))

    const [thermoResults, reactionResults, solveDetail] = await Promise.all([
        Promise.all(speciesEntryRefs.map(async (entryRef) => {
            const thermo = await loadEntryThermo(entryRef, signal)
            return [entryRef, thermo.records.length > 0] as const
        })),
        Promise.all(reactionEntryRefs.map(async (entryRef) => {
            const entry = await fetchReactionEntryLite(entryRef, signal)
            return [entryRef, entry] as const
        })),
        solves[0]
            ? (async (): Promise<SolveFetch> => {
                const solveQuery = new URLSearchParams()
                solveQuery.append("include", "state_energies")
                solveQuery.append("include", "channel_barriers")
                try {
                    const payload = await requestScientificJson(
                        `/api/v1/scientific/network-solves/${encodeURIComponent(solves[0].network_solve_ref)}?${solveQuery}`,
                        signal,
                    )
                    return { status: "ok", detail: parseScientificResponse(solveDetailResponseSchema, payload, "network solve") }
                } catch (error) {
                    // An abort means the component unmounted or the ref
                    // changed. That is not an archive failure and must
                    // propagate, or a cancelled request would render as
                    // unreadable data.
                    if (signal?.aborted) throw error
                    if (error instanceof DOMException && error.name === "AbortError") throw error
                    return { status: "unavailable" }
                }
            })()
            : Promise.resolve<SolveFetch>({ status: "absent" }),
    ])

    return {
        network: record.network,
        review_summary: detail.review_summary,
        software_release: record.software_release ?? null,
        workflow_tool_release: record.workflow_tool_release ?? null,
        literature: record.literature ?? null,
        evidence_summary: record.evidence_summary,
        states,
        channels,
        solves,
        speciesEntryRefs,
        speciesThermoPresence: Object.fromEntries(thermoResults),
        reactionEntries: Object.fromEntries(reactionResults),
        stateEnergies: solveDetail.status === "ok" ? (solveDetail.detail.record.state_energies ?? null) : null,
        channelBarriers: solveDetail.status === "ok" ? (solveDetail.detail.record.channel_barriers ?? null) : null,
        solveEnergiesUnavailable: solveDetail.status === "unavailable",
    }
}
