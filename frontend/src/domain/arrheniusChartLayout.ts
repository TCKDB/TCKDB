import type { ReactionKineticsRecord } from "../api/reactionEntryApi"
import { domainWithPadding } from "./chartScale"
import { arrheniusTermK } from "./kineticsTable"
import {
    type ArrheniusUnitFamily,
    arrheniusUnitConversionFactor,
    arrheniusUnitFamily,
    familyUnits,
} from "./arrheniusUnits"

// Layout constants and domain-computation functions for `ArrheniusChart.tsx`,
// pulled into their own (non-component) module for the same reason
// `thermoCpChartLayout.ts` is: `eslint-plugin-react-refresh`'s
// `only-export-components` rule fires on a `.tsx` file exporting
// non-component values, and keeping the domain math here lets it be pinned
// directly against hand-computed values without mounting a component.
//
// `linearScale`/`domainWithPadding`/`formatTicks` come from `chartScale.ts`
// and `niceTicks`/`seriesColor` are imported straight from
// `thermoCpChartLayout.ts` at the call site in `ArrheniusChart.tsx` --
// genuinely generic (not Cp-specific) helpers, so this file does not fork
// them. The one k(T) primitive this module needs, `arrheniusTermK`, is
// imported from `domain/kineticsTable.ts` (PR 2's pinned maths) rather than
// re-derived -- see that module's own docstring for the hand-computed
// values this file's own tests reuse. The unit ALGEBRA itself (which units
// interconvert, and by what factor) lives in `domain/arrheniusUnits.ts` --
// this file only groups/samples/scales, it never invents a conversion
// factor of its own.

// SVG pinned to a FIXED pixel size (both a `width`/`height` attribute on
// the element, not a percentage) -- the mock's own defect (round 2 review):
// `width:100%; max-width:720px` on the Cp chart's `<svg>` scales its
// viewBox-driven text down whenever the container is narrower than 720px,
// and at a 680px viewport that took tick labels below the 11.52px
// accessibility floor. This chart never shrinks; `ArrheniusChart.tsx`
// wraps it in a `overflow-x: auto` container instead, so a narrow viewport
// scrolls the chart horizontally rather than shrinking its text.
export const ARRHENIUS_CHART_WIDTH = 720
export const ARRHENIUS_CHART_HEIGHT = 340
export const ARRHENIUS_CHART_MARGIN = { top: 16, right: 20, bottom: 40, left: 58 } as const

/** ~60 samples per curve, per plan §4/PR 3's own spec -- denser than the
 *  k(T) table's 12 points (`kineticsTable.ts`'s `TABLE_POINT_COUNT`), since
 *  a plotted curve needs more resolution than a text table's sampled rows,
 *  but the SAME underlying `arrheniusTermK` maths at every sample. */
export const ARRHENIUS_SAMPLE_COUNT = 60

/** `"temperature"`: x = T in kelvin, linear -- today's (and the default)
 *  view. `"inverse_temperature"`: x = 1000/T in K⁻¹, the canonical
 *  Arrhenius-plot axis (what RMG shows), on which a plain (single-term,
 *  unmodified-n) Arrhenius record draws as a straight line of slope
 *  -Ea/(2.303*R). One control governs EVERY panel on the page at once
 *  (`ArrheniusChart.tsx`'s single top-level `<select>`) -- this is a
 *  reading-convention choice ("which axis am I looking at"), not a
 *  per-record or per-panel fact the way the unit selector is, so unlike
 *  units there is exactly one control for it, not one per panel. */
export type ArrheniusXAxisMode = "temperature" | "inverse_temperature"

export interface ArrheniusPoint {
    temperatureK: number
    k: number
    log10k: number
}

export interface ArrheniusSeries {
    kinetics_ref: string
    /** The record's OWN deposited `A_units` token, exactly as served (or
     *  `null` when unrecorded) -- kept on every series regardless of which
     *  unit its panel is CURRENTLY displaying, so a reader can always
     *  recover what was actually deposited (this PR's own invariant: "a
     *  record's deposited units remain visible somewhere on its own
     *  surface"). `ArrheniusChart.tsx`'s legend states it explicitly. */
    depositedUnits: string | null
    /** k(T) points, ALWAYS in `depositedUnits` -- never pre-converted. This
     *  is the one ground-truth array every display-unit conversion starts
     *  from (`convertArrheniusSeriesUnits` below); a component renders the
     *  CONVERTED series, never mutates this one in place. */
    points: ArrheniusPoint[]
    minK: number
    maxK: number
}

export interface ArrheniusExclusion {
    kinetics_ref: string
    reasons: string[]
}

export interface ArrheniusPanel {
    /** The order family (1/2/3) shared by EVERY series in this panel, or
     *  `null` when the panel groups records with unrecorded units, or a raw
     *  `A_units` token this file doesn't recognise (not one of
     *  `ArrheniusAUnits`'s seven values). Either way, `availableUnits` is
     *  empty and no conversion is ever offered for this panel -- `null`
     *  here is not "assume order 2", it is "unknown, refuse to guess". */
    orderFamily: ArrheniusUnitFamily | null
    /** Every unit belonging to `orderFamily`, in `arrheniusUnits.ts`'s
     *  canonical order -- the FULL set `ArrheniusChart.tsx`'s per-panel
     *  `<select>` offers, regardless of which of them any record in this
     *  panel actually happens to be deposited in. Empty when `orderFamily`
     *  is `null`: there is no known convertible sibling unit to offer, and
     *  (per plan §4) a control with fewer than two options is never
     *  rendered at all. */
    availableUnits: readonly string[]
    /** The selector's initial value -- the unit MOST of this panel's own
     *  series were deposited in. Tie-break (deliberately NOT alphabetical
     *  or `arrheniusUnits.ts`'s own family-table order, to stay consistent
     *  with this module's "served order, never re-sorted" convention
     *  elsewhere): the tied unit whose FIRST deposit appears earliest among
     *  this panel's own series, in the order `buildArrheniusChartData`
     *  received them. `undefined` when `availableUnits` is empty -- for an
     *  unrecorded-units panel there is no unit to default to; for an
     *  unrecognised-token panel every series shares that one raw token by
     *  construction (that token IS the grouping key), so this still names
     *  it even though no selector is offered to switch away from it. */
    defaultUnits: string | undefined
    series: ArrheniusSeries[]
}

export interface ArrheniusChartData {
    panels: ArrheniusPanel[]
    excluded: ArrheniusExclusion[]
}

// `ArrheniusAUnits` (`backend/app/db/models/common.py`) -> typeset unit
// string -- the SAME mapping `ReactionKineticsSection.tsx`'s private
// `aUnitLabel` uses (duplicated here, not imported: that helper is
// module-private and this file must not reach into a page-scoped component
// module for a formatting table; plan §4's own PR-slicing note sanctions
// "otherwise duplicate one function and note it" for exactly this case).
// The k(T) TABLE'S own header text (`ReactionEntryPage.test.tsx:521`'s "the
// k(T) table header names the unit" test, unmoved by this PR) must keep
// reading the same string, so this map is kept byte-identical to that one.
const A_UNIT_LABELS: Record<string, string> = {
    per_s: "s⁻¹",
    cm3_mol_s: "cm³ mol⁻¹ s⁻¹",
    cm3_molecule_s: "cm³ molecule⁻¹ s⁻¹",
    m3_mol_s: "m³ mol⁻¹ s⁻¹",
    cm6_mol2_s: "cm⁶ mol⁻² s⁻¹",
    cm6_molecule2_s: "cm⁶ molecule⁻² s⁻¹",
    m6_mol2_s: "m⁶ mol⁻² s⁻¹",
}

export function arrheniusUnitLabel(units: string | null | undefined): string {
    if (!units) return "unrecorded units"
    return A_UNIT_LABELS[units] ?? units.replaceAll("_", " ")
}

/**
 * ~60-point k(T) curve for one plain `arrhenius`/`modified_arrhenius`
 * (single `A`) or `multi_arrhenius` (summed) record, sampled evenly across
 * ITS OWN `record_min_k..record_max_k` -- never a shared/union range, per
 * plan §4: "each curve drawn only within its own record_min_k..record_max_k".
 * `null` when the record lacks a usable fitted range or rate parameters
 * (same absence contract as `kineticsTable.ts`'s `computeKineticsTable`).
 * A non-positive `k` (pathological parameters) is skipped rather than
 * plotted as an undefined `log10`.
 *
 * Sampling stays evenly-spaced in T (never resampled in 1000/T) regardless
 * of the CURRENT x-axis mode -- `arrheniusPointX` below re-projects each
 * already-computed point's x-coordinate at render time, so a record's own
 * fitted range is honoured identically in either mode without this
 * function needing to know which one is active.
 */
export function computeArrheniusSeries(
    record: Pick<ReactionKineticsRecord, "kinetics_ref" | "temperature_coverage" | "multi_arrhenius" | "parameters">,
): ArrheniusSeries | null {
    const min = record.temperature_coverage?.record_min_k
    const max = record.temperature_coverage?.record_max_k
    if (min == null || max == null || !(max > min)) return null

    const terms = record.multi_arrhenius && record.multi_arrhenius.length > 0
        ? record.multi_arrhenius
        : record.parameters.A != null
            ? [{ A: record.parameters.A, n: record.parameters.n, Ea_kj_mol: record.parameters.Ea_kj_mol }]
            : null
    if (!terms) return null

    const points: ArrheniusPoint[] = []
    for (let i = 0; i < ARRHENIUS_SAMPLE_COUNT; i++) {
        const temperatureK = min + (i * (max - min)) / (ARRHENIUS_SAMPLE_COUNT - 1)
        const k = terms.reduce((sum, term) => sum + arrheniusTermK(term.A, term.n, term.Ea_kj_mol, temperatureK), 0)
        if (!(k > 0)) continue
        points.push({ temperatureK, k, log10k: Math.log10(k) })
    }
    if (points.length === 0) return null
    return {
        kinetics_ref: record.kinetics_ref,
        depositedUnits: record.parameters.A_units ?? null,
        points,
        minK: min,
        maxK: max,
    }
}

/** Why a record is never plotted -- named per plan §4: "records carrying
 *  `plog_entries`, `chebyshev`, `falloff`, or a third-body flag are
 *  EXCLUDED with a note naming the ref and the reason". A record can carry
 *  more than one of these at once; every applicable reason is named, none
 *  silently dropped in favour of "the first match". */
function exclusionReasons(record: Pick<ReactionKineticsRecord, "plog_entries" | "chebyshev" | "falloff" | "is_third_body">): string[] {
    const reasons: string[] = []
    if (record.plog_entries && record.plog_entries.length > 0) {
        reasons.push("PLOG-fitted -- pressure-dependent, no single k(T) curve without a stated pressure")
    }
    if (record.chebyshev) {
        reasons.push("Chebyshev-fitted -- pressure-dependent, no single k(T) curve without a stated pressure")
    }
    if (record.falloff) {
        reasons.push("falloff-fitted -- pressure-dependent, no single k(T) curve without a stated pressure")
    }
    if (record.is_third_body) {
        reasons.push("third-body reaction -- rate depends on bath-gas concentration, not on temperature alone")
    }
    return reasons
}

const UNRECORDED_KEY = "\0unrecorded"

/** The tied-unit tie-break `ArrheniusPanel.defaultUnits` documents: the
 *  modal (most-deposited) unit among `series`, breaking a count tie by
 *  which candidate's FIRST occurrence comes earliest in `series`' own
 *  order. Only called with a non-empty `series` all sharing a known family
 *  (so `depositedUnits` is a non-null string on every entry). */
function modalDepositedUnit(series: readonly ArrheniusSeries[]): string {
    const counts = new Map<string, number>()
    const firstIndex = new Map<string, number>()
    series.forEach((entry, index) => {
        const units = entry.depositedUnits!
        counts.set(units, (counts.get(units) ?? 0) + 1)
        if (!firstIndex.has(units)) firstIndex.set(units, index)
    })
    let best = series[0].depositedUnits!
    let bestCount = -1
    let bestFirstIndex = Infinity
    for (const [units, count] of counts) {
        const first = firstIndex.get(units)!
        if (count > bestCount || (count === bestCount && first < bestFirstIndex)) {
            best = units
            bestCount = count
            bestFirstIndex = first
        }
    }
    return best
}

/**
 * Splits `records` into per-ORDER-FAMILY panels (a record in `cm3_mol_s`
 * and one in `m3_mol_s` share ONE panel now -- they are the same physical
 * quantity, 1e6 apart, and the whole point of a per-panel unit selector is
 * to make them directly comparable; only a genuine dimensional difference,
 * e.g. `per_s` beside `cm3_mol_s`, still gets two panels) and an excluded
 * list naming every record left out and why. A record with unrecorded
 * units, or a raw `A_units` token this file doesn't recognise, still gets
 * its own panel exactly as before -- grouped with other records sharing
 * that SAME absence/token, never merged into a family panel it cannot be
 * shown to convert within. Panel order and series order within a panel
 * both follow the served array's own order -- never re-sorted.
 */
export function buildArrheniusChartData(records: readonly ReactionKineticsRecord[]): ArrheniusChartData {
    const excluded: ArrheniusExclusion[] = []
    const seriesByKey = new Map<string, ArrheniusSeries[]>()
    const familyByKey = new Map<string, ArrheniusUnitFamily | null>()
    const keyOrder: string[] = []

    for (const record of records) {
        const reasons = exclusionReasons(record)
        if (reasons.length > 0) {
            excluded.push({ kinetics_ref: record.kinetics_ref, reasons })
            continue
        }
        const series = computeArrheniusSeries(record)
        if (!series) {
            excluded.push({
                kinetics_ref: record.kinetics_ref,
                reasons: ["no usable rate parameters or fitted temperature range on file for this record"],
            })
            continue
        }
        const family = arrheniusUnitFamily(series.depositedUnits)
        const key = series.depositedUnits == null
            ? UNRECORDED_KEY
            : family != null
                ? `family:${family}`
                : `unit:${series.depositedUnits}`
        if (!seriesByKey.has(key)) {
            seriesByKey.set(key, [])
            familyByKey.set(key, family)
            keyOrder.push(key)
        }
        seriesByKey.get(key)!.push(series)
    }

    const panels: ArrheniusPanel[] = keyOrder.map((key) => {
        const series = seriesByKey.get(key)!
        const orderFamily = familyByKey.get(key)!
        const availableUnits = orderFamily != null ? familyUnits(orderFamily) : []
        const defaultUnits = availableUnits.length > 0 ? modalDepositedUnit(series) : (series[0]?.depositedUnits ?? undefined)
        return { orderFamily, availableUnits, defaultUnits, series }
    })

    return { panels, excluded }
}

/** A stable per-panel identity, for use as BOTH a React list key
 *  (`ArrheniusChart.tsx`) and the key into its `selectedUnitsByPanel` /
 *  `xAxisMode` state maps -- so a panel's own current unit selection
 *  survives a re-render as long as its identity (family, or unrecorded/raw
 *  token) is unchanged. Mirrors the exact grouping key
 *  `buildArrheniusChartData` used to bucket this panel's series in the
 *  first place. */
export function arrheniusPanelKey(panel: ArrheniusPanel): string {
    if (panel.orderFamily != null) return `family:${panel.orderFamily}`
    const firstDeposited = panel.series[0]?.depositedUnits
    return firstDeposited == null ? UNRECORDED_KEY : `unit:${firstDeposited}`
}

/**
 * `series`, with every point's `k`/`log10k` re-expressed in `toUnits` --
 * the SAME conversion factor (`arrheniusUnitConversionFactor`,
 * `domain/arrheniusUnits.ts`) the k(T) TABLE applies to its own k column
 * (`kineticsTable.ts`'s `convertKineticsTableRows`), so the chart and its
 * text equivalent never disagree about what unit is shown.
 *
 * A series whose `depositedUnits` is `null`, or whose factor is refused
 * (cross-family, or either unit unrecognised -- `null` from
 * `arrheniusUnitConversionFactor`), is returned UNCHANGED rather than
 * guessed at: this function is never the mechanism that decides whether a
 * conversion is legal, only the one that PERFORMS an already-legal one
 * (`ArrheniusPanel.availableUnits` is what stops an illegal one from ever
 * being requested in the first place).
 */
export function convertArrheniusSeriesUnits(series: ArrheniusSeries, toUnits: string): ArrheniusSeries {
    if (series.depositedUnits == null) return series
    const factor = arrheniusUnitConversionFactor(series.depositedUnits, toUnits)
    if (factor == null || factor === 1) return series
    return {
        ...series,
        points: series.points.map((point) => {
            const k = point.k * factor
            return { ...point, k, log10k: Math.log10(k) }
        }),
    }
}

/** x-domain for one panel -- the UNION of every plotted series' own fitted
 *  range (plan §4: "x = T in K, linear, over the union of the fitted
 *  ranges"), unpadded: padding outward would draw axis space beyond any
 *  curve's own stated validity. Takes the panel's SERIES directly (not the
 *  whole `ArrheniusPanel`) so it composes with a panel's current unit
 *  selection without needing to know about it -- the temperature domain
 *  never depends on which A_units the panel happens to be displaying. */
export function panelTemperatureDomain(series: readonly ArrheniusSeries[]): [number, number] {
    const mins = series.map((entry) => entry.minK)
    const maxs = series.map((entry) => entry.maxK)
    return [Math.min(...mins), Math.max(...maxs)]
}

/** The x-VALUE plotted for one point, in the given axis mode -- kelvin
 *  unchanged, or 1000/T in K⁻¹. Never resamples; only re-projects a point
 *  `computeArrheniusSeries` already computed in T, so a record's own
 *  fitted-range clipping is identical in either mode. */
export function arrheniusPointX(point: Pick<ArrheniusPoint, "temperatureK">, mode: ArrheniusXAxisMode): number {
    return mode === "temperature" ? point.temperatureK : 1000 / point.temperatureK
}

/**
 * x-domain for one panel in the given axis mode. Under `"temperature"` this
 * is exactly `panelTemperatureDomain`. Under `"inverse_temperature"` the
 * domain is `[1000/maxK, 1000/minK]` -- NOT `[1000/minK, 1000/maxK]`: 1000/T
 * is a strictly DECREASING function of T, so the panel's highest
 * temperature maps to the SMALLEST 1000/T value. Handing that smaller value
 * as `domain[0]` (which `linearScale`, `chartScale.ts`, always places at the
 * LOW end of the pixel range) is what puts high temperature on the left --
 * the correct, expected reading of this axis -- entirely as a consequence
 * of 1000/T's own monotonicity, with no separate "reverse the axis" branch
 * anywhere in this file or the component.
 */
export function panelXDomain(series: readonly ArrheniusSeries[], mode: ArrheniusXAxisMode): [number, number] {
    const [minK, maxK] = panelTemperatureDomain(series)
    if (mode === "temperature") return [minK, maxK]
    return [1000 / maxK, 1000 / minK]
}

/** y-domain for one panel -- every plotted log10(k) value across every
 *  series, lightly padded so a curve's own extremes are never drawn flush
 *  against the plot's own border. Takes the panel's (possibly
 *  unit-converted) SERIES directly, for the same reason
 *  `panelTemperatureDomain` does -- this must be computed AFTER
 *  `convertArrheniusSeriesUnits`, never before, or the axis and the curve
 *  it's meant to bound would be in two different units. */
export function panelLog10KDomain(series: readonly ArrheniusSeries[]): [number, number] {
    const values = series.flatMap((entry) => entry.points.map((point) => point.log10k))
    return domainWithPadding(values, 0.1)
}
