import { z } from "zod"
import { levelOfTheorySchema, recordReviewSchema } from "./scientificSchemas"
import { parseScientificResponse, requestScientificJson, ScientificApiError } from "./scientificTransport"

/**
 * `GET /scientific/reaction-entries/{ref}/full` --
 * `backend/app/api/routes/scientific/provenance.py`, schema
 * `backend/app/schemas/reads/scientific_provenance.py`. See
 * `docs/plans/reaction-entry-page.md` §2/§3 for the information
 * architecture and the additive fields this schema tolerates being absent.
 *
 * Requests `include=species,kinetics,transition_states,calculations,networks`
 * in ONE round trip -- every section this page renders, matching
 * `transitionStateEntryApi.ts`'s own eager-fetch precedent. `networks` is a
 * §3C-additive include token (not deployed as of this PR): a pre-deployment
 * API either 422s on the unknown token or simply omits the key from its
 * response depending on how strictly it validates `include` -- either way
 * `networks` comes back `undefined` here, and `loadReactionEntryNetworks`
 * below is the documented one-request fallback
 * (`networks/search?reaction_entry_ref=...&include=reactions`) the page
 * falls back to when that happens. `formula`/`stoichiometry` on each
 * participant and `levels` on each kinetics record are likewise §3A/§3B
 * additive fields, marked optional here so today's (pre-PR-1) live API
 * still parses -- see `domain/reactionEquation.ts` and
 * `domain/productLevels.ts`'s `resolveProductLevels` for the client-side
 * fallbacks each absence triggers.
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

// `ScientificLevelsSummary` -- geometry/frequency/energy levels of theory
// plus `energy_source`, the SAME shape `productLevelsSchema`
// (`scientificSchemas.ts`) already models for statmech/thermo records; the
// backend schema's fields (`geometry`, `frequency`, `energy`,
// `energy_source`) line up field-for-field, so this reuses that schema
// rather than a duplicate.
const levelsSummarySchema = z.object({
    geometry: levelOfTheorySchema.nullable().optional(),
    frequency: levelOfTheorySchema.nullable().optional(),
    energy: levelOfTheorySchema.nullable().optional(),
    energy_source: z.string().nullable().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// reaction_entry header
// ---------------------------------------------------------------------------

const atomMapBadgeSchema = z.object({
    transition_state_entry_ref: z.string(),
    source: z.string(),
    equivalent_map_count: z.number().nullable().optional(),
    reactant_atoms_mapped: z.number(),
    product_atoms_mapped: z.number(),
    note: z.string().nullable().optional(),
}).passthrough()

const reactionEntrySummarySchema = z.object({
    reaction_entry_ref: z.string(),
    reaction_ref: z.string(),
    equation: z.string(),
    reversible: z.boolean(),
    family: z.string().nullable().optional(),
    review: recordReviewSchema,
    atom_maps: z.array(atomMapBadgeSchema).default([]),
}).passthrough()

// ---------------------------------------------------------------------------
// species
// ---------------------------------------------------------------------------

// `formula`/`stoichiometry` -- §3A, additive. `stoichiometry` defaults to
// `1` when the wire omits it entirely (a pre-deployment API): one displayed
// occurrence is the honest reading of "this deposit does not yet say a
// coefficient", never a fabricated guess at a real one.
const speciesParticipantSchema = z.object({
    species_entry_ref: z.string(),
    species_entry_label: z.string().nullable().optional(),
    smiles: z.string(),
    formula: z.string().nullable().optional(),
    stoichiometry: z.number().optional().default(1),
    participant_index: z.number(),
    review: recordReviewSchema,
}).passthrough()

const speciesSectionSchema = z.object({
    reactants: z.array(speciesParticipantSchema),
    products: z.array(speciesParticipantSchema),
}).passthrough()

// ---------------------------------------------------------------------------
// kinetics
// ---------------------------------------------------------------------------

const arrheniusParametersSchema = z.object({
    A: z.number().nullable().optional(),
    A_units: z.string().nullable().optional(),
    n: z.number().nullable().optional(),
    Ea_kj_mol: z.number().nullable().optional(),
}).passthrough()

const multiArrheniusTermSchema = z.object({
    entry_index: z.number(),
    A: z.number(),
    A_units: z.string().nullable().optional(),
    n: z.number().nullable().optional(),
    Ea_kj_mol: z.number().nullable().optional(),
}).passthrough()

const kineticsUncertaintySchema = z.object({
    A_uncertainty: z.number().nullable().optional(),
    A_uncertainty_kind: z.string().nullable().optional(),
    n_uncertainty: z.number().nullable().optional(),
    Ea_uncertainty_kj_mol: z.number().nullable().optional(),
}).passthrough()

const temperatureCoverageSchema = z.object({
    record_min_k: z.number().nullable().optional(),
    record_max_k: z.number().nullable().optional(),
}).passthrough()

const evidenceCompletenessSchema = z.object({
    score: z.number(),
    max: z.number(),
    checklist: z.record(z.string(), z.boolean()),
}).passthrough()

const kineticsProvenanceSchema = z.object({
    transition_state_entry_ref: z.string().nullable().optional(),
    ts_opt_calculation_ref: z.string().nullable().optional(),
    ts_freq_calculation_ref: z.string().nullable().optional(),
    ts_sp_calculation_ref: z.string().nullable().optional(),
    primary_level_of_theory: levelOfTheorySchema.nullable().optional(),
    primary_software: softwareReleaseSummarySchema.nullable().optional(),
    literature: literatureSummarySchema.nullable().optional(),
    software_release: softwareReleaseSummarySchema.nullable().optional(),
    workflow_tool_release: workflowToolReleaseSummarySchema.nullable().optional(),
    network_kinetics_ref: z.string().nullable().optional(),
}).passthrough()

const kineticsRecordSchema = z.object({
    kinetics_ref: z.string(),
    scientific_origin: z.string(),
    model_kind: z.string(),
    direction: z.string().nullable().optional(),
    review: recordReviewSchema,
    parameters: arrheniusParametersSchema,
    multi_arrhenius: z.array(multiArrheniusTermSchema).nullable().optional(),
    tunneling_model: z.string().nullable().optional(),
    is_third_body: z.boolean().optional().default(false),
    plog_entries: z.array(z.unknown()).nullable().optional(),
    chebyshev: z.unknown().nullable().optional(),
    falloff: z.unknown().nullable().optional(),
    uncertainty: kineticsUncertaintySchema,
    temperature_coverage: temperatureCoverageSchema.nullable().optional(),
    evidence_completeness: evidenceCompletenessSchema,
    // Additive (§3B) -- absent on a pre-deployment response. See
    // `domain/productLevels.ts`'s `resolveProductLevels`, which this page
    // feeds a TS-chain-derived fallback when this key is missing.
    levels: levelsSummarySchema.nullable().optional(),
    provenance: kineticsProvenanceSchema,
}).passthrough()

// ---------------------------------------------------------------------------
// transition_states
// ---------------------------------------------------------------------------

const tsEvidenceSummarySchema = z.object({
    calculation_count: z.number(),
    has_opt: z.boolean(),
    has_freq: z.boolean(),
    has_sp: z.boolean(),
    has_irc: z.boolean(),
    has_path_search: z.boolean(),
    has_geometry_validation: z.boolean(),
    has_scf_stability: z.boolean(),
    levels_of_theory: z.record(z.string(), z.array(levelOfTheorySchema)).optional().default({}),
}).passthrough()

const tsCalculationSlotSchema = z.object({
    calculation_ref: z.string(),
    type: z.string(),
    method: z.string().nullable().optional(),
}).passthrough()

const tsDependencySchema = z.object({
    parent_calculation_ref: z.string(),
    child_calculation_ref: z.string(),
    role: z.string(),
}).passthrough()

const transitionStateInFullSchema = z.object({
    transition_state_ref: z.string(),
    transition_state_entry_ref: z.string(),
    status: z.string().nullable().optional(),
    review: recordReviewSchema,
    evidence_summary: tsEvidenceSummarySchema,
    calculations: z.record(z.string(), tsCalculationSlotSchema).optional().default({}),
    dependencies: z.array(tsDependencySchema).optional().default([]),
}).passthrough()

// ---------------------------------------------------------------------------
// calculations (top-level evidence summaries -- used to resolve levels for
// a kinetics record whose `levels` was not served, per plan §7's live
// oddity note: a kinetics record's own `ts_*_calculation_ref` provenance
// can name a calculation this section does not carry, in which case that
// role's level falls back to `provenance.primary_level_of_theory` -- see
// `deriveKineticsLevelsFallback` in `pages/ReactionEntryPage.tsx`.)
// ---------------------------------------------------------------------------

const calculationEvidenceSummarySchema = z.object({
    calculation_ref: z.string().nullable().optional(),
    calculation_type: z.string(),
    level_of_theory: levelOfTheorySchema.nullable().optional(),
    // Also feeds `ReactionTransitionStatesSection`'s "Calculations by
    // stage" table (Software / workflow column), cross-referenced by
    // `calculation_ref` against each TS entry's own `calculations` slot
    // map -- `TransitionStateInFull.calculations[stage]` carries only
    // `{calculation_ref, type, method}`, no software of its own.
    software: softwareReleaseSummarySchema.nullable().optional(),
}).passthrough()

// ---------------------------------------------------------------------------
// networks (§3C, additive)
// ---------------------------------------------------------------------------

const networkMembershipSchema = z.object({
    network_ref: z.string(),
    name: z.string().nullable().optional(),
    solve_temperature_min_k: z.number().nullable().optional(),
    solve_temperature_max_k: z.number().nullable().optional(),
    solve_pressure_min_bar: z.number().nullable().optional(),
    solve_pressure_max_bar: z.number().nullable().optional(),
    channel_count: z.number(),
    review: recordReviewSchema,
}).passthrough()

export type NetworkMembership = z.infer<typeof networkMembershipSchema>

// ---------------------------------------------------------------------------
// review_summary
// ---------------------------------------------------------------------------

const reviewStatusSummarySchema = z.object({
    approved: z.number().default(0),
    under_review: z.number().default(0),
    not_reviewed: z.number().default(0),
    deprecated: z.number().default(0),
    rejected: z.number().default(0),
    total: z.number().default(0),
}).passthrough()

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

const reactionFullResponseSchema = z.object({
    reaction_entry: reactionEntrySummarySchema,
    review_summary: reviewStatusSummarySchema,
    species: speciesSectionSchema.nullable().optional(),
    kinetics: z.array(kineticsRecordSchema).nullable().optional(),
    transition_states: z.array(transitionStateInFullSchema).nullable().optional(),
    calculations: z.array(calculationEvidenceSummarySchema).nullable().optional(),
    // §3C -- undefined on a pre-deployment API (see module docstring above).
    networks: z.array(networkMembershipSchema).nullable().optional(),
}).passthrough()

export type ReactionFullRecord = z.infer<typeof reactionFullResponseSchema>
export type ReactionEntrySpeciesParticipant = z.infer<typeof speciesParticipantSchema>
export type ReactionKineticsRecord = z.infer<typeof kineticsRecordSchema>
export type ReactionTransitionStateInFull = z.infer<typeof transitionStateInFullSchema>
export type ReactionFullCalculationEvidence = z.infer<typeof calculationEvidenceSummarySchema>

const FULL_INCLUDE = ["species", "kinetics", "transition_states", "calculations", "networks"]

function fetchFull(ref: string, include: string[], signal?: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void): Promise<unknown> {
    const query = new URLSearchParams()
    for (const token of include) query.append("include", token)
    const endpoint = `/api/v1/scientific/reaction-entries/${encodeURIComponent(ref)}/full?${query}`
    return requestScientificJson(endpoint, signal, onRateLimited)
}

/**
 * Module-level, session-lifetime cache of whether THIS deployment accepts
 * `networks` as an `/full` include token -- `null` (unknown, try eagerly)
 * until the first request settles the question one way or the other, then
 * `true`/`false` for every subsequent call. See `loadReactionEntry`'s own
 * docstring for why this exists at all: without it, every navigation on a
 * pre-deployment archive would burn a wasted request finding out the same
 * fact again.
 */
let networksIncludeSupported: boolean | null = null

/**
 * `networks` (§3C) is not yet a legal `include` token on the live,
 * pre-deployment API -- MEASURED against the real archive (verification
 * pass, 2026-09-07): asking for it does not silently omit the section the
 * way an unrecognised-but-tolerated token would; the endpoint rejects the
 * WHOLE request with HTTP 422 `unknown_include_token`. A naive "always ask
 * for five tokens" implementation therefore breaks this entire page
 * against today's archive, not just the Networks section -- caught by
 * this PR's own headless-Chrome verification pass, not assumed from the
 * schema docstring alone.
 *
 * So the eager fetch tries all five tokens first (the zero-extra-request
 * path once PR 1 deploys, and the path every call after the first uses
 * once `networksIncludeSupported` is known `true`). Only the FIRST call
 * against an archive that rejects the token pays for finding that out:
 * on that specific rejection, this retries once, immediately, with
 * `networks` dropped -- the four tokens every deployed archive to date
 * accepts -- and remembers the answer in `networksIncludeSupported` for
 * every later navigation in this session. Any OTHER error (a genuine 404,
 * a rate limit, an unrelated 422) is not retried and propagates as-is;
 * retrying blindly on every failure would double the request cost of a
 * rate limit for no benefit.
 */
export async function loadReactionEntry(
    ref: string,
    signal?: AbortSignal,
    onRateLimited?: (retryAfterSeconds: number) => void,
): Promise<ReactionFullRecord> {
    const include = networksIncludeSupported === false ? FULL_INCLUDE.filter((token) => token !== "networks") : FULL_INCLUDE
    let payload: unknown
    try {
        payload = await fetchFull(ref, include, signal, onRateLimited)
        if (include === FULL_INCLUDE) networksIncludeSupported = true
    } catch (error) {
        if (include === FULL_INCLUDE && error instanceof ScientificApiError && error.code === "unknown_include_token") {
            networksIncludeSupported = false
            payload = await fetchFull(ref, FULL_INCLUDE.filter((token) => token !== "networks"), signal, onRateLimited)
        } else {
            throw error
        }
    }
    return parseScientificResponse(reactionFullResponseSchema, payload, "reaction entry")
}

// ---------------------------------------------------------------------------
// §3C fallback -- `networks/search?reaction_entry_ref=...&include=reactions`
// against an API that does not yet serve `include=networks` on `/full`.
// One request, only made when the eager `/full` fetch above came back with
// `networks === undefined` -- see `ReactionEntryPage.tsx`'s call site.
// ---------------------------------------------------------------------------

const networkSearchRecordSchema = z.object({
    network: z.object({
        network_ref: z.string(),
        name: z.string().nullable().optional(),
        solve_temperature_min_k: z.number().nullable().optional(),
        solve_temperature_max_k: z.number().nullable().optional(),
        solve_pressure_min_bar: z.number().nullable().optional(),
        solve_pressure_max_bar: z.number().nullable().optional(),
        review: recordReviewSchema,
    }).passthrough(),
    evidence_summary: z.object({
        channel_count: z.number(),
    }).passthrough(),
}).passthrough()

const networkSearchResponseSchema = z.object({
    records: z.array(networkSearchRecordSchema),
}).passthrough()

export async function loadReactionEntryNetworksFallback(
    reactionEntryRef: string,
    signal?: AbortSignal,
): Promise<NetworkMembership[]> {
    const query = new URLSearchParams()
    query.set("reaction_entry_ref", reactionEntryRef)
    query.append("include", "reactions")
    const endpoint = `/api/v1/scientific/networks/search?${query}`
    const payload = await requestScientificJson(endpoint, signal)
    const parsed = parseScientificResponse(networkSearchResponseSchema, payload, "network membership")
    return parsed.records.map((record) => ({
        network_ref: record.network.network_ref,
        name: record.network.name ?? null,
        solve_temperature_min_k: record.network.solve_temperature_min_k ?? null,
        solve_temperature_max_k: record.network.solve_temperature_max_k ?? null,
        solve_pressure_min_bar: record.network.solve_pressure_min_bar ?? null,
        solve_pressure_max_bar: record.network.solve_pressure_max_bar ?? null,
        channel_count: record.evidence_summary.channel_count,
        review: record.network.review,
    }))
}
