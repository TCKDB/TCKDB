import type { ReactionKineticsRecord } from "../api/reactionEntryApi"
import { domainWithPadding } from "./chartScale"
import { arrheniusTermK } from "./kineticsTable"

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
// values this file's own tests reuse.

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

export interface ArrheniusPoint {
    temperatureK: number
    k: number
    log10k: number
}

export interface ArrheniusSeries {
    kinetics_ref: string
    points: ArrheniusPoint[]
    minK: number
    maxK: number
}

export interface ArrheniusExclusion {
    kinetics_ref: string
    reasons: string[]
}

export interface ArrheniusPanel {
    /** The record's own raw `A_units` token, or `null` when unrecorded --
     *  the grouping key. */
    aUnits: string | null
    /** Typeset label for `aUnits`, e.g. "cm³ mol⁻¹ s⁻¹". */
    unitLabel: string
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
// The k(T) TABLE'S own header text (`ReactionKineticsSection.test.tsx`'s
// "the k(T) table header names the unit" test, unmoved by this PR) must
// keep reading the same string, so this map is kept byte-identical to that
// one.
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
    return { kinetics_ref: record.kinetics_ref, points, minK: min, maxK: max }
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

/**
 * Splits `records` into per-`A_units` panels (plan §4: "one panel per
 * distinct A_units -- a page mixing per_s and cm3_mol_s gets two panels")
 * and an excluded list naming every record left out and why. Panel order
 * and series order within a panel both follow the served array's own
 * order -- never re-sorted.
 */
export function buildArrheniusChartData(records: readonly ReactionKineticsRecord[]): ArrheniusChartData {
    const excluded: ArrheniusExclusion[] = []
    const seriesByUnits = new Map<string, ArrheniusSeries[]>()
    const unitsOrder: string[] = []
    const UNRECORDED_KEY = "\0unrecorded"

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
        const unitsKey = record.parameters.A_units ?? UNRECORDED_KEY
        if (!seriesByUnits.has(unitsKey)) {
            seriesByUnits.set(unitsKey, [])
            unitsOrder.push(unitsKey)
        }
        seriesByUnits.get(unitsKey)!.push(series)
    }

    const panels: ArrheniusPanel[] = unitsOrder.map((unitsKey) => {
        const aUnits = unitsKey === UNRECORDED_KEY ? null : unitsKey
        return { aUnits, unitLabel: arrheniusUnitLabel(aUnits), series: seriesByUnits.get(unitsKey)! }
    })

    return { panels, excluded }
}

/** x-domain for one panel -- the UNION of every plotted series' own fitted
 *  range (plan §4: "x = T in K, linear, over the union of the fitted
 *  ranges"), unpadded: padding outward would draw axis space beyond any
 *  curve's own stated validity. */
export function panelTemperatureDomain(panel: ArrheniusPanel): [number, number] {
    const mins = panel.series.map((series) => series.minK)
    const maxs = panel.series.map((series) => series.maxK)
    return [Math.min(...mins), Math.max(...maxs)]
}

/** y-domain for one panel -- every plotted log10(k) value across every
 *  series, lightly padded so a curve's own extremes are never drawn flush
 *  against the plot's own border. */
export function panelLog10KDomain(panel: ArrheniusPanel): [number, number] {
    const values = panel.series.flatMap((series) => series.points.map((point) => point.log10k))
    return domainWithPadding(values, 0.1)
}
