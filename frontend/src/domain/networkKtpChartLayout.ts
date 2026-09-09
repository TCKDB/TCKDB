import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import type { NetworkKtpEvaluatedPoint, NetworkKtpFit } from "../api/networkKineticsEvalApi"
import { evenTicks } from "./chartScale"
import { seriesColor } from "./thermoCpChartLayout"

/**
 * PR 4 of `docs/plans/pressure-dependent-network-surface.md` (§2.4/§5):
 * layout/shaping helpers for the k(T,P) chart. This module does NOT
 * evaluate Chebyshev or PLOG -- it only builds the request grid this
 * page sends to `POST /scientific/networks/{ref}/kinetics/evaluate`
 * (`api/networkKineticsEvalApi.ts`) and shapes the ALREADY-EVALUATED
 * `points[]` that endpoint returns for plotting (grouping by channel,
 * filtering to one pressure, splitting into solid/dashed run segments).
 * Never a polynomial, never a log-interpolation -- see this repo's own
 * invariant 2 ("no Chebyshev or PLOG math in the frontend, ever").
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

// ---------------------------------------------------------------------------
// Model-kind display -- fixed colour per model kind, GLOBALLY (not per
// panel/index), so "blue is always Chebyshev" holds across every channel
// on the page, not just within one panel.
// ---------------------------------------------------------------------------

const MODEL_KIND_ORDER = ["chebyshev", "plog", "tabulated"]

const MODEL_KIND_LABELS: Record<string, string> = {
    chebyshev: "Chebyshev",
    plog: "PLOG",
    tabulated: "Tabulated (point kinetics)",
}

export function modelKindLabel(modelKind: string): string {
    return MODEL_KIND_LABELS[modelKind] ?? modelKind.replaceAll("_", " ")
}

export function modelKindColor(modelKind: string): string {
    const index = MODEL_KIND_ORDER.indexOf(modelKind)
    return seriesColor(index >= 0 ? index : MODEL_KIND_ORDER.length)
}

// ---------------------------------------------------------------------------
// Plotting at one selected pressure
// ---------------------------------------------------------------------------

export interface KtpPlottedPoint {
    temperatureK: number
    /** The served `k`, unrounded, carried straight through so the table
     *  never has to reconstruct it via `10 ** log10k` (a display-only
     *  inverse that would lose precision for no reason). */
    k: number
    log10k: number
    inRange: boolean
}

/** The tolerance a served `pressure_bar` must match the caller's own
 * requested value within, to survive a JSON round trip through the API
 * without a spurious mismatch -- never re-derives or rounds the served
 * value itself. */
function pressureMatches(servedPressureBar: number, selectedPressureBar: number): boolean {
    const tolerance = Math.max(Math.abs(selectedPressureBar) * 1e-9, 1e-9)
    return Math.abs(servedPressureBar - selectedPressureBar) <= tolerance
}

/**
 * Every point of one fit's `points[]` at exactly `pressureBar`, sorted by
 * temperature. `k <= 0` is skipped (undefined `log10`) rather than
 * plotted as `NaN` — matches `arrheniusChartLayout.ts`'s own stance on a
 * non-positive rate coefficient. `Math.log10` here is display math on an
 * ALREADY-SERVED `k`, not a re-evaluation of the fit itself.
 */
export function ktpPlottedPoints(points: readonly NetworkKtpEvaluatedPoint[], pressureBar: number): KtpPlottedPoint[] {
    return points
        .filter((point) => pressureMatches(point.pressure_bar, pressureBar) && point.k > 0)
        .map((point) => ({ temperatureK: point.temperature_k, k: point.k, log10k: Math.log10(point.k), inRange: point.in_range }))
        .sort((a, b) => a.temperatureK - b.temperatureK)
}

export interface KtpLineSegment {
    points: KtpPlottedPoint[]
    /** `true` when BOTH endpoints of every edge in this run are
     *  `in_range` — drawn solid. `false` drawn dashed (invariant 3: a
     *  point outside a fit's own validity range must be visually
     *  distinct, never silently presented as interpolated). */
    solid: boolean
}

/**
 * Splits a temperature-sorted point run into contiguous solid/dashed
 * segments, one polyline per segment, sharing the boundary point between
 * adjacent segments so the drawn line stays continuous across a
 * solid<->dashed transition (never a visible gap).
 */
export function ktpLineSegments(points: readonly KtpPlottedPoint[]): KtpLineSegment[] {
    if (points.length === 0) return []
    if (points.length === 1) return [{ points: [points[0]], solid: points[0].inRange }]

    const segments: KtpLineSegment[] = []
    let current: KtpPlottedPoint[] = [points[0]]
    let currentSolid = points[0].inRange && points[1].inRange

    for (let i = 1; i < points.length; i++) {
        const edgeSolid = points[i - 1].inRange && points[i].inRange
        if (edgeSolid !== currentSolid) {
            segments.push({ points: current, solid: currentSolid })
            current = [points[i - 1]]
            currentSolid = edgeSolid
        }
        current.push(points[i])
    }
    segments.push({ points: current, solid: currentSolid })
    return segments
}
