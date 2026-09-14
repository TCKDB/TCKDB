import { z } from "zod"
import { parseScientificResponse, requestScientificJsonPost } from "./scientificTransport"

/**
 * `POST /scientific/networks/{ref}/kinetics/evaluate` --
 * `backend/app/api/routes/scientific/networks.py`, schema
 * `backend/app/schemas/reads/scientific_network_kinetics_batch_evaluate.py`.
 * PR 4 of `docs/plans/pressure-dependent-network-surface.md` (§2.4/§4 A):
 * evaluates EVERY stored k(T,P) fit belonging to one network at a shared
 * `(temperature_k, pressure_bar)` grid, in one request -- the k(T,P)
 * chart's whole reason for existing as a network-scoped batch endpoint
 * rather than one call per fit (42 on the live hydrazine network).
 *
 * `fits[]` is keyed by `network_kinetics_ref`, NOT by channel: a channel
 * routinely carries more than one fit (a Chebyshev AND a PLOG
 * parameterization of the same channel is the norm on the live archive,
 * not an edge case), and this module never picks, prefers, averages, or
 * otherwise collapses them -- `domain/networkKtpChartLayout.ts`'s
 * `groupKtpFitsByChannel` is where multiple fits sharing one channel are
 * grouped for display, and it keeps every one of them.
 *
 * No Chebyshev/PLOG math lives here or anywhere in this frontend --
 * `points[].k` is the server's own already-evaluated value, rendered
 * as-is (invariant 2 of this PR's brief).
 */

const evaluatedPointSchema = z.object({
    temperature_k: z.number(),
    pressure_bar: z.number(),
    k: z.number(),
    in_range: z.boolean(),
}).passthrough()

const batchFitSchema = z.object({
    network_kinetics_ref: z.string(),
    channel_key: z.string(),
    channel_kind: z.string(),
    source_state_composition_hash: z.string(),
    sink_state_composition_hash: z.string(),
    network_solve_ref: z.string(),
    model_kind: z.string(),
    k_units: z.string(),
    tmin_k: z.number().nullable().optional(),
    tmax_k: z.number().nullable().optional(),
    pmin_bar: z.number().nullable().optional(),
    pmax_bar: z.number().nullable().optional(),
    points: z.array(evaluatedPointSchema).default([]),
}).passthrough()

const batchEvaluateResponseSchema = z.object({
    network_ref: z.string().optional(),
    fits: z.array(batchFitSchema).default([]),
}).passthrough()

export type NetworkKtpEvaluatedPoint = z.infer<typeof evaluatedPointSchema>
export type NetworkKtpFit = z.infer<typeof batchFitSchema>

/**
 * One request, always -- this is the only call site for this endpoint on
 * `NetworkKtpChart.tsx`, fired once per mount (invariant 1: "one request
 * per page load. Not one per channel, not one per fit.").
 */
export async function loadNetworkKtpEvaluation(
    networkRef: string,
    temperaturesK: readonly number[],
    pressuresBar: readonly number[],
    signal?: AbortSignal,
): Promise<{ fits: NetworkKtpFit[] }> {
    const endpoint = `/api/v1/scientific/networks/${encodeURIComponent(networkRef)}/kinetics/evaluate`
    const payload = await requestScientificJsonPost(
        endpoint,
        { temperature_k: [...temperaturesK], pressure_bar: [...pressuresBar] },
        signal,
    )
    const parsed = parseScientificResponse(batchEvaluateResponseSchema, payload, "network k(T,P) evaluation")
    return { fits: parsed.fits }
}
