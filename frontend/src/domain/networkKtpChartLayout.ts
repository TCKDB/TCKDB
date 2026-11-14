import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import type { NetworkKtpFit } from "../api/networkKineticsEvalApi"
import { evenTicks } from "./chartScale"

/**
 * Helpers for the k(T,P) heat map: the request grid sent to `POST /scientific/networks/{ref}/kinetics/evaluate`
 * and the grouping of the served fits by channel. Nothing here evaluates a Chebyshev or PLOG fit; the values come
 * from the archive as served.
 */

// ---------------------------------------------------------------------------
// Request grid
// ---------------------------------------------------------------------------

/**
 * 36 temperatures x 5 pressures = 180 points per fit, comfortably under
 * the batch endpoint's per-fit cap (`EVALUATE_GRID_POINT_CAP`, 200 —
 * `backend/app/services/scientific_read/network_kinetics.py`) with
 * margin, and well under its aggregate cap
 * (`BATCH_EVALUATE_POINT_CAP = EVALUATE_GRID_POINT_CAP * public_max_limit`)
 * even at the live archive's 42 stored fits.
 */
export const KTP_TEMPERATURE_POINT_COUNT = 36
export const KTP_PRESSURE_POINT_COUNT = 5

export interface KtpRequestGrid {
    temperaturesK: number[]
    pressuresBar: number[]
}

/** Evenly spaced across `[min, max]`, log-spaced across `[min, max]` for
 * pressure (a network's solve pressure range routinely spans two-plus
 * decades -- 0.01-100 bar on the live archive -- so a linear pressure
 * grid would crowd every sampled curve into the low end). */
function logSpaced(min: number, max: number, count: number): number[] {
    if (count <= 1 || min === max) return [min]
    const logMin = Math.log10(min)
    const logMax = Math.log10(max)
    return Array.from({ length: count }, (_unused, i) => {
        const t = i / (count - 1)
        return Math.pow(10, logMin + (logMax - logMin) * t)
    })
}

/**
 * `null` when this network's solve does not carry a usable temperature or
 * pressure range to build a grid from -- the caller degrades honestly
 * rather than guessing a range (invariant 9: "if a network has no fits,
 * say so plainly"; the same stance applies when the range needed to ASK
 * for fits is itself unrecorded).
 */
export function buildKtpRequestGrid(
    temperatureMinK: number | null | undefined,
    temperatureMaxK: number | null | undefined,
    pressureMinBar: number | null | undefined,
    pressureMaxBar: number | null | undefined,
): KtpRequestGrid | null {
    if (temperatureMinK == null || temperatureMaxK == null || pressureMinBar == null || pressureMaxBar == null) return null
    if (!(temperatureMinK < temperatureMaxK)) return null
    if (!(pressureMinBar > 0) || !(pressureMaxBar > 0) || pressureMinBar > pressureMaxBar) return null
    return {
        temperaturesK: evenTicks([temperatureMinK, temperatureMaxK], KTP_TEMPERATURE_POINT_COUNT),
        pressuresBar: logSpaced(pressureMinBar, pressureMaxBar, KTP_PRESSURE_POINT_COUNT),
    }
}

// ---------------------------------------------------------------------------
// Grouping the served fits by channel -- chemistry label, never channel_key
// ---------------------------------------------------------------------------

export interface KtpChannelGroup {
    /** Data face only -- the caller may put this in a `data-*` attribute
     *  or the accessible table, never as the group's rendered label
     *  (invariant 4). */
    channelKey: string
    /** `"<source state label> to <sink state label>"`, built from
     *  `composition.state_label` the same way `NetworkDiagram.tsx`'s own
     *  edge `aria-label` is -- the ONLY safe chart-facing channel
     *  identity. */
    label: string
    channelKind: string
    /** Every stored fit for this channel -- a Chebyshev AND a PLOG fit on
     *  the same channel is the norm on the live archive (42 fits, 21
     *  channels), never collapsed to one (invariant: "show both"). */
    series: NetworkKtpFit[]
}

/**
 * Orders groups the same way `channels[]` (the network detail response) is
 * itself ordered -- so this chart's channel list reads in the same order
 * as the diagram's own accessible channel table, not an incidental
 * response order from a different endpoint. A fit whose `channel_key`
 * matches no known channel (should not happen against the live archive,
 * but the served schema does not forbid it) is still grouped, appended
 * after every matched group in sorted-key order, so no evaluated fit is
 * ever silently dropped from the page.
 */
export function groupKtpFitsByChannel(
    fits: readonly NetworkKtpFit[],
    channels: readonly NetworkChannel[],
    states: readonly NetworkState[],
): KtpChannelGroup[] {
    const stateLabelByHash = new Map(states.map((state) => [state.composition_hash, state.composition.state_label || "unnamed state"]))
    const channelByKey = new Map(channels.filter((channel) => channel.channel_key).map((channel) => [channel.channel_key as string, channel]))

    const fitsByChannelKey = new Map<string, NetworkKtpFit[]>()
    for (const fit of fits) {
        const list = fitsByChannelKey.get(fit.channel_key) ?? []
        list.push(fit)
        fitsByChannelKey.set(fit.channel_key, list)
    }

    const orderedKeys: string[] = []
    for (const channel of channels) {
        const key = channel.channel_key
        if (key && fitsByChannelKey.has(key) && !orderedKeys.includes(key)) orderedKeys.push(key)
    }
    for (const key of [...fitsByChannelKey.keys()].sort()) {
        if (!orderedKeys.includes(key)) orderedKeys.push(key)
    }

    return orderedKeys.map((key) => {
        const series = fitsByChannelKey.get(key) ?? []
        const channel = channelByKey.get(key)
        const sourceHash = channel?.source_state_composition_hash ?? series[0]?.source_state_composition_hash
        const sinkHash = channel?.sink_state_composition_hash ?? series[0]?.sink_state_composition_hash
        const sourceLabel = (sourceHash && stateLabelByHash.get(sourceHash)) || "unresolved state"
        const sinkLabel = (sinkHash && stateLabelByHash.get(sinkHash)) || "unresolved state"
        return {
            channelKey: key,
            label: `${sourceLabel} to ${sinkLabel}`,
            channelKind: channel?.kind ?? series[0]?.channel_kind ?? "unknown",
            series,
        }
    })
}

const MODEL_KIND_LABELS: Record<string, string> = {
    chebyshev: "Chebyshev",
    plog: "PLOG",
    tabulated: "Tabulated (point kinetics)",
}

export function modelKindLabel(modelKind: string): string {
    return MODEL_KIND_LABELS[modelKind] ?? modelKind.replaceAll("_", " ")
}
