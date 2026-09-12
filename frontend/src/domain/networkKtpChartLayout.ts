import type { NetworkChannel, NetworkState } from "../api/networkEntryApi"
import type { NetworkKtpEvaluatedPoint, NetworkKtpFit } from "../api/networkKineticsEvalApi"
import { evenTicks } from "./chartScale"
import { seriesColor } from "./thermoCpChartLayout"
import { type ArrheniusUnitFamily, arrheniusUnitConversionFactor, arrheniusUnitFamily, familyUnits } from "./arrheniusUnits"

/**
 * PR 4 of `docs/plans/pressure-dependent-network-surface.md` (§2.4/§5),
 * extended by the stacked-panel/unit-control follow-up (owner: "shouldn't
 * they all stack on one graph... why is there no control for changing the
 * y axis"): layout/shaping helpers for the k(T,P) chart. This module does
 * NOT evaluate Chebyshev or PLOG -- it only builds the request grid this
 * page sends to `POST /scientific/networks/{ref}/kinetics/evaluate`
 * (`api/networkKineticsEvalApi.ts`) and shapes the ALREADY-EVALUATED
 * `points[]` that endpoint returns for plotting (grouping by channel,
 * grouping channels by unit FAMILY, converting an already-served `k` between
 * sibling units, filtering to one pressure, splitting into solid/dashed run
 * segments). Never a polynomial, never a log-interpolation -- see this
 * repo's own invariant 2 ("no Chebyshev or PLOG math in the frontend,
 * ever").
 *
 * Two grouping levels, not one: `groupKtpFitsByChannel` (channel identity,
 * chemistry label, unchanged from PR 4) and, layered on top of it,
 * `groupKtpChannelsByUnitFamily` (which of those channels can share a
 * y-axis at all). `per_s` (unimolecular) and `cm3_mol_s` (bimolecular) are
 * different PHYSICAL DIMENSIONS -- overlaying them on one axis would not
 * merely look odd, it would silently compare two incomparable quantities.
 * "Stack every selected channel on one graph" is therefore only ever true
 * WITHIN a family; `NetworkKtpChart.tsx` renders one panel per family
 * `groupKtpChannelsByUnitFamily` returns (at most two on the live hydrazine
 * network), each overlaying every selected channel that belongs to it.
 * Unit conversion within a family reuses `arrheniusUnits.ts` verbatim (the
 * SAME cm<->m<->molecule factors the Arrhenius chart already uses) --
 * this file never derives its own factor.
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
// Model-kind labelling. Overlaying channels on one panel (below) means
// COLOUR is now spent on channel identity, not model kind -- `modelKindColor`
// stays as a fixed, kind-only hue used ONLY for the one shared "how to read
// this" legend swatch (never for an actual plotted line any more); the real
// per-line encoding is `channelSeriesColor` (hue = channel) tinted by
// `modelKindStrokeColor` and widened/thinned by `modelKindStrokeWidth` (both
// below), which is what a `<polyline>` actually renders.
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

/** Kind-only fixed hue -- legend illustration only, see header comment
 *  above. Never assigned to a `<polyline>`'s own `stroke` on the overlaid
 *  chart (that is `channelSeriesColor` + `modelKindStrokeColor`). */
export function modelKindColor(modelKind: string): string {
    const index = MODEL_KIND_ORDER.indexOf(modelKind)
    return seriesColor(index >= 0 ? index : MODEL_KIND_ORDER.length)
}

// ---------------------------------------------------------------------------
// Overlaid-channel series encoding -- colour is CHANNEL identity, model kind
// is a tint + stroke-width pair layered on that same hue. Chosen over the
// brief's other option (colour by channel, model kind as weight/opacity
// alone) because opacity alone reads poorly once dashed (out-of-range)
// segments are layered on top -- a thin, already-faded PLOG dash becomes
// very hard to see against a busy multi-channel panel. Tinting the hue
// toward white keeps every model-kind variant fully opaque (so a dashed
// segment stays exactly as visible as a solid one of the same kind) while
// still reading as "the same colour family, a lighter member of it" next to
// its Chebyshev sibling -- and the redundant stroke-width difference (2.25px
// vs 1.25px) means the two are still tell-apart-able even for a reader who
// cannot perceive the tint difference at all (colour-vision deficiency, a
// black-and-white printout, ...). Dash is UNTOUCHED by any of this -- it
// still, and only, means in_range vs extrapolated, per segment (invariant 4).
// ---------------------------------------------------------------------------

/** Stable per-channel hue, indexed by a channel's position in the FULL
 *  (unfiltered) channel-group list -- so a channel's colour never changes
 *  when a DIFFERENT channel is toggled on or off elsewhere in the fieldset
 *  (the same "fixed, not per-panel-index" principle the old per-model-kind
 *  colour followed, now applied to channel identity instead). Cycles
 *  through `seriesColor`'s 8 tokens; a selection carrying more than 8
 *  channels at once reuses hues -- an intentional trade against the
 *  alternative (an unbounded, eventually indistinguishable palette), and
 *  exactly why a sensible caller keeps the default selection small (see
 *  `NetworkKtpChart.tsx`'s own initial-selection comment). */
export function channelColorIndex(allGroups: readonly KtpChannelGroup[]): Map<string, number> {
    return new Map(allGroups.map((group, index) => [group.channelKey, index]))
}

export function channelSeriesColor(index: number): string {
    return seriesColor(index)
}

/** Percentage of white mixed into a channel's own hue, per model kind --
 *  `0` (Chebyshev) leaves the hue untouched; `plog`'s 45% keeps it a
 *  visibly lighter, still fully-opaque member of the same hue family (see
 *  header comment above for why opacity was rejected). An unrecognised
 *  future model kind gets a middling tint rather than either extreme, so it
 *  neither silently impersonates Chebyshev's exact hue nor vanishes. */
const MODEL_KIND_TINT_PERCENT: Record<string, number> = {
    chebyshev: 0,
    plog: 45,
    tabulated: 22,
}

/** `baseColor` (a `channelSeriesColor` result, or any CSS colour) tinted
 *  toward white by this model kind's own fixed percentage. `color-mix()` is
 *  evaluated by the browser at paint time -- this function only builds the
 *  string, it never resolves `baseColor`'s own `var(--chart-series-N)`
 *  itself, so the SAME conversion works unchanged across the light/dark
 *  theme swap that token already handles. */
export function modelKindStrokeColor(baseColor: string, modelKind: string): string {
    const tint = MODEL_KIND_TINT_PERCENT[modelKind] ?? 30
    if (tint <= 0) return baseColor
    return `color-mix(in srgb, ${baseColor}, white ${tint}%)`
}

/** Stroke width, px -- Chebyshev drawn heavier than PLOG so the two stay
 *  tell-apart-able even where the tint above is hard to perceive (see
 *  header comment). Not itself a dash/opacity change, so it never competes
 *  with the in_range dash encoding (invariant 4) or the tint encoding. */
const MODEL_KIND_STROKE_WIDTH: Record<string, number> = {
    chebyshev: 2.25,
    plog: 1.25,
    tabulated: 1.75,
}

export function modelKindStrokeWidth(modelKind: string): number {
    return MODEL_KIND_STROKE_WIDTH[modelKind] ?? 1.75
}

// ---------------------------------------------------------------------------
// One panel per UNIT FAMILY -- the scientific constraint this PR's brief
// leads with: `per_s` (s^-1) and `cm3_mol_s` (cm^3 mol^-1 s^-1) are
// different DIMENSIONS and can never share a y-axis, so "all selected
// channels on one graph" is only true WITHIN one family. Mirrors
// `arrheniusChartLayout.ts`'s own `buildArrheniusChartData` almost exactly
// (group-by-family, modal-unit default, `availableUnits`/`defaultUnits` on
// the panel) -- the one structural difference is the grouping key: Arrhenius
// groups one entry per RECORD (one deposited `A_units` each), this groups
// one entry per (channel, FIT) pair, since a single channel can legitimately
// carry fits in more than one units token in principle (never observed on
// the live archive, where a channel's Chebyshev and PLOG fits always agree,
// but nothing in the served schema guarantees it) -- so a channel whose own
// fits happen to disagree on family is still split correctly, one fit
// staying in each of two panels, rather than this file assuming agreement
// and mis-filing the whole channel by its first fit's units alone.
// ---------------------------------------------------------------------------

export interface KtpFamilyPanel {
    /** `"family:<n>"` for a recognised order family, `"unit:<token>"` for a
     *  raw `k_units` token outside any known family -- mirrors
     *  `arrheniusChartLayout.ts`'s own panel-key scheme (`k_units` is never
     *  unrecorded on the served schema, unlike Arrhenius's `A_units`, so
     *  there is no `unrecorded` branch here). */
    key: string
    orderFamily: ArrheniusUnitFamily | null
    /** Every unit belonging to `orderFamily`, canonical order -- empty ONLY
     *  when `orderFamily` is `null` (an unrecognised `k_units` token). For a
     *  single-member family (`per_s`, order 1: a unimolecular rate has no
     *  concentration unit to convert to) this is `["per_s"]`, length ONE,
     *  mirroring `arrheniusChartLayout.ts`'s own `ArrheniusPanel.availableUnits`
     *  verbatim. `NetworkKtpChart.tsx` renders no unit control at all when
     *  `.length <= 1` -- per this PR's brief, a single-option panel gets no
     *  control, not a disabled one (unlike the Arrhenius chart's own
     *  always-shown-disabled convention) -- but that gate lives in the
     *  COMPONENT, not as an extra blanking rule in this field itself. */
    availableUnits: readonly string[]
    /** The modal (most-common) `k_units` among this panel's own fits,
     *  tie-broken by first occurrence -- same rule as
     *  `arrheniusChartLayout.ts`'s `modalDepositedUnit`. Always defined in
     *  practice (every fit carries a non-null `k_units`), typed optional
     *  only to mirror `ArrheniusPanel.defaultUnits`'s own shape. */
    defaultUnits: string | undefined
    /** Every SELECTED channel with at least one fit in this family, each
     *  scoped to ONLY the fits that actually belong here (see header
     *  comment) -- order follows `groups`' own order, never re-sorted. */
    groups: KtpChannelGroup[]
}

/** Tie-break identical to `arrheniusChartLayout.ts`'s own `modalDepositedUnit`:
 *  most-common value wins; a count tie goes to whichever value's FIRST
 *  occurrence comes earliest in `units`' own order. */
function modalUnits(units: readonly string[]): string {
    const counts = new Map<string, number>()
    const firstIndex = new Map<string, number>()
    units.forEach((unit, index) => {
        counts.set(unit, (counts.get(unit) ?? 0) + 1)
        if (!firstIndex.has(unit)) firstIndex.set(unit, index)
    })
    let best = units[0]
    let bestCount = -1
    let bestFirstIndex = Infinity
    for (const [unit, count] of counts) {
        const first = firstIndex.get(unit)!
        if (count > bestCount || (count === bestCount && first < bestFirstIndex)) {
            best = unit
            bestCount = count
            bestFirstIndex = first
        }
    }
    return best
}

/**
 * Splits `groups` (already filtered to the CALLER's current channel
 * selection) into per-unit-family panels. A channel whose fits all share one
 * family (the norm) appears in exactly one output panel, carrying every one
 * of its fits (invariant: never collapse a channel's two fits, and never
 * drop one silently just because grouping happens at the fit level here).
 */
export function groupKtpChannelsByUnitFamily(groups: readonly KtpChannelGroup[]): KtpFamilyPanel[] {
    interface Entry {
        group: KtpChannelGroup
        fit: NetworkKtpFit
    }
    const entriesByKey = new Map<string, Entry[]>()
    const familyByKey = new Map<string, ArrheniusUnitFamily | null>()
    const keyOrder: string[] = []

    for (const group of groups) {
        for (const fit of group.series) {
            const family = arrheniusUnitFamily(fit.k_units)
            const key = family != null ? `family:${family}` : `unit:${fit.k_units}`
            if (!entriesByKey.has(key)) {
                entriesByKey.set(key, [])
                familyByKey.set(key, family)
                keyOrder.push(key)
            }
            entriesByKey.get(key)!.push({ group, fit })
        }
    }

    return keyOrder.map((key) => {
        const entries = entriesByKey.get(key)!
        const orderFamily = familyByKey.get(key)!
        const availableUnits = orderFamily != null ? familyUnits(orderFamily) : []
        const defaultUnits = availableUnits.length > 0 ? modalUnits(entries.map((entry) => entry.fit.k_units)) : entries[0]?.fit.k_units

        const seriesByChannelKey = new Map<string, NetworkKtpFit[]>()
        for (const { group, fit } of entries) {
            const list = seriesByChannelKey.get(group.channelKey) ?? []
            list.push(fit)
            seriesByChannelKey.set(group.channelKey, list)
        }
        const panelGroups = groups
            .filter((group) => seriesByChannelKey.has(group.channelKey))
            .map((group) => ({ ...group, series: seriesByChannelKey.get(group.channelKey)! }))

        return { key, orderFamily, availableUnits, defaultUnits, groups: panelGroups }
    })
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

/**
 * `points` (already `ktpPlottedPoints`-filtered to one pressure, in
 * `fromUnits`) re-expressed in `toUnits` -- unit CONVERSION of an
 * already-served value, via `arrheniusUnits.ts`'s own
 * `arrheniusUnitConversionFactor`, never a re-evaluation of the fit
 * (invariant 3 stays satisfied: no Chebyshev/PLOG math anywhere in this
 * module, this function only multiplies an already-computed `k` by ONE
 * fixed dimensional factor, computed once, not per point). Identical in
 * shape and behaviour to `arrheniusChartLayout.ts`'s own
 * `convertArrheniusSeriesUnits`: a `null` or `1` factor (identical units,
 * OR a refused cross-family/unrecognised pair -- `arrheniusUnitConversionFactor`
 * does not distinguish the two in its return type, and this function must
 * not guess which one it was) returns `points` UNCHANGED rather than
 * dropping them, and a converted `k` that is not strictly positive is
 * skipped rather than plotted as an `-Infinity` `log10k` that would poison
 * the whole panel's y-domain.
 */
export function convertKtpPlottedPoints(points: readonly KtpPlottedPoint[], fromUnits: string, toUnits: string): KtpPlottedPoint[] {
    const factor = arrheniusUnitConversionFactor(fromUnits, toUnits)
    if (factor == null || factor === 1) return [...points]
    const converted: KtpPlottedPoint[] = []
    for (const point of points) {
        const k = point.k * factor
        if (!(k > 0)) continue
        converted.push({ ...point, k, log10k: Math.log10(k) })
    }
    return converted
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
