import { domainWithPadding } from "./chartScale"
import type { CpChartSeries } from "./thermoCpSeries"

// Layout constants and domain-computation functions for `ThermoCpChart.tsx`,
// pulled into their own (non-component) module so
// `eslint-plugin-react-refresh`'s `only-export-components` rule does not
// fire on a `.tsx` file exporting non-component values (see the same note
// on `domain/geometryXyz.ts`).

export type CpChartMode = "raw" | "fit" | "both"

export const CP_CHART_WIDTH = 720
export const CP_CHART_HEIGHT = 340
export const CHART_MARGIN = { top: 16, right: 20, bottom: 40, left: 58 } as const

/** One of eight hand-picked categorical hues (`theme.css`), cycling if
 * there are ever more than eight deposited records for one entry. */
export function seriesColor(index: number): string {
    return `var(--chart-series-${(index % 8) + 1})`
}

/**
 * Every temperature any series actually plots (measured or fitted) — the
 * SHARED x-domain for both panels, computed once regardless of the
 * RAW/FIT/BOTH toggle so switching that toggle never rescales the axes.
 *
 * Clamped at [0, maxT], never padded outward: this used to run through the
 * same `domainWithPadding` helper the Cp axis uses, which pads BOTH ends
 * proportionally to the data's own span — harmless for a value axis, but on
 * a temperature axis starting near 0 K (this archive's NASA-7 fits commonly
 * state `t_low` around 10 K) the padding pushed the low end negative (a
 * review finding: "-110, 698, 1505, 2312, 3120" on a live page). Kelvin has
 * a real zero; the axis honours it rather than an aesthetic pad.
 */
export function computeTemperatureDomain(series: readonly CpChartSeries[]): [number, number] {
    const temperatures: number[] = []
    for (const item of series) {
        for (const point of item.measured) temperatures.push(point.temperatureK)
        if (item.fitted) {
            for (const point of item.fitted.low) temperatures.push(point.temperatureK)
            for (const point of item.fitted.high) temperatures.push(point.temperatureK)
        }
    }
    if (temperatures.length === 0) return [0, 1]
    const maxTemperature = Math.max(...temperatures)
    return [0, maxTemperature > 0 ? maxTemperature : 1]
}

/** Every Cp value (display units) any series actually plots. */
export function computeCpValueDomain(series: readonly CpChartSeries[]): [number, number] {
    const values: number[] = []
    for (const item of series) {
        for (const point of item.measured) values.push(point.cpDisplay)
        if (item.fitted) {
            for (const point of item.fitted.low) values.push(point.cpDisplay)
            for (const point of item.fitted.high) values.push(point.cpDisplay)
        }
    }
    return domainWithPadding(values, 0.12)
}

// --- "nice" round-number ticks ---------------------------------------------
// `chartScale.ts`'s `evenTicks` slices a domain into `count` EQUAL pieces,
// which is correct for a scale but produces unreadable axis labels whenever
// the domain itself isn't a round number ("20.8, 49.7, 78.7, 108, 137" was
// the y-axis half of the same review finding the temperature-domain fix
// above addresses). This is the standard D3 `tickStep`/`ticks` approach:
// pick a step from {1, 2, 5} times a power of ten closest to
// `span / approxCount`, then emit every multiple of that step that falls
// inside the domain. Used for BOTH axes so every tick a reader sees is a
// number they'd actually round to by eye.
const NICE_FACTOR_10 = Math.sqrt(50)
const NICE_FACTOR_5 = Math.sqrt(10)
const NICE_FACTOR_2 = Math.sqrt(2)

function niceStep(span: number, approxCount: number): number {
    const rawStep = span / Math.max(1, approxCount)
    const power = Math.floor(Math.log10(rawStep))
    const magnitude = 10 ** power
    const error = rawStep / magnitude
    const factor = error >= NICE_FACTOR_10 ? 10 : error >= NICE_FACTOR_5 ? 5 : error >= NICE_FACTOR_2 ? 2 : 1
    return factor * magnitude
}

/**
 * Round-number ticks within `[domain[0], domain[1]]` — never outside it, so
 * a tick label never implies the axis draws further than it does. Can
 * return fewer than `approxCount` ticks when the domain doesn't divide
 * evenly by a nice step (e.g. 4 ticks at 0/1000/2000/3000, not 5 uneven
 * ones) — an honest axis, never a padded one. Mirrors `evenTicks`'s
 * degenerate-domain behaviour (`[domain[0]]`) for a zero-span domain.
 */
export function niceTicks(domain: readonly [number, number], approxCount = 5): number[] {
    const [d0, d1] = domain
    if (d1 <= d0) return [d0]
    const step = niceStep(d1 - d0, approxCount)
    if (!Number.isFinite(step) || step <= 0) return [d0, d1]
    const first = Math.ceil(d0 / step) * step
    const ticks: number[] = []
    // `+ step * 1e-9`: float slop guard so a domain edge that's an exact
    // multiple of `step` (the common case) isn't dropped by a
    // `1500.0000000000002 <= 1500` miss.
    for (let tick = first; tick <= d1 + step * 1e-9; tick += step) {
        ticks.push(Math.round(tick / step) * step)
    }
    return ticks
}

// --- collapsing series that plot as the exact same line --------------------
// A species entry can carry several deposited thermo records that all trace
// to the SAME conformer group and share the SAME NASA-7 fit and points (seen
// live on `spe_5nr24y2ssxokxlhsvymdwbpwmm`: seven `thm_` records, digit-for-
// digit identical coefficients and points). Drawing each as its own line
// used to produce seven perfectly overlapping curves and seven legend chips
// that read "Conformer Group 1 SELECTED", distinguished only by dot colour
// — a review finding. Grouping runs on the ACTUAL plotted values, never on
// `thermoRef`/label/conformer link alone, so two records that merely share a
// conformer but genuinely differ in their fit still render as two lines.

export type CpChartRenderGroup = {
    /** The one member whose data is actually drawn / whose testids the
     * plotted line and legend chip carry. */
    representative: CpChartSeries
    /** Every record folded into this rendered line, in original order.
     * Length 1 for an ordinary, non-duplicated record. */
    members: CpChartSeries[]
}

function plotSignature(item: CpChartSeries): string {
    // Measured + fitted fully determine what the line/markers look like;
    // `thermoRef`/`label`/`isSelected` deliberately excluded so grouping
    // reflects what's drawn, not which record happened to be uploaded
    // first.
    return JSON.stringify([item.measured, item.fitted])
}

/** Groups `series` by what they'd actually draw, preserving first-seen order. */
export function groupIdenticalSeries(series: readonly CpChartSeries[]): CpChartRenderGroup[] {
    const groups: CpChartRenderGroup[] = []
    const bySignature = new Map<string, CpChartRenderGroup>()
    for (const item of series) {
        const signature = plotSignature(item)
        const existing = bySignature.get(signature)
        if (existing) {
            existing.members.push(item)
        } else {
            const group: CpChartRenderGroup = { representative: item, members: [item] }
            bySignature.set(signature, group)
            groups.push(group)
        }
    }
    return groups
}

/**
 * The legend/plot label for one rendered group — always names WHAT
 * distinguishes it, never leaves colour as the only cue (the other half of
 * the same review finding). A collapsed group says how many identical
 * records it stands for rather than pretending to be one record. A lone
 * record whose label collides with a DIFFERENT group's (same conformer
 * link, genuinely different fit — e.g. a later reprocessing) is
 * disambiguated by its own `thermo_ref`, the one field guaranteed unique
 * per record on this wire (no deposit-date field exists on
 * `ThermoRecord` today; see `api/thermoApi.ts`).
 */
export function groupLegendLabel(group: CpChartRenderGroup, allGroups: readonly CpChartRenderGroup[]): string {
    if (group.members.length > 1) {
        return `${group.representative.label} — ${group.members.length} identical records`
    }
    const collidesWithAnotherGroup = allGroups.some(
        (other) => other !== group && other.representative.label === group.representative.label,
    )
    return collidesWithAnotherGroup
        ? `${group.representative.label} (${group.representative.thermoRef})`
        : group.representative.label
}

export function seriesAbsenceNote(series: CpChartSeries): string | null {
    if (series.hasUsableFit && !series.hasMeasuredPoints) {
        return `${series.label}: no measured points on file for this record — the fitted curve is drawn without markers.`
    }
    if (!series.hasUsableFit && series.hasMeasuredPoints) {
        return `${series.label}: no usable NASA-7 fit on file for this record — measured points only, no curve.`
    }
    if (!series.hasUsableFit && !series.hasMeasuredPoints) {
        return `${series.label}: no measured points or usable NASA-7 fit on file for this record — nothing plotted.`
    }
    return null
}
