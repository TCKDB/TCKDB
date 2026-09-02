import type { ConformerProjection } from "../api/speciesEntryApi"
import type { ThermoRecord } from "../api/thermoApi"
import { conformerLabel, thermoConformerGroupRef } from "./conformerEvidence"
import {
    type CpUnit,
    type NasaBlock,
    convertCpForDisplay,
    isNasaFitUsable,
    nasaCpCurve,
} from "./thermoNasa"

export type CpDisplayPoint = { temperatureK: number; cpDisplay: number }

export type CpChartSeries = {
    thermoRef: string
    /** The conformer's own display label when this record traces to one
     * (`thermoConformerGroupRef`); the record's own ref otherwise -- never
     * a generic "Series N", so a reader can tell WHICH conformer (or which
     * unlinked record) a line belongs to without cross-referencing anything. */
    label: string
    /** True only when this record traces to the entry page's currently
     * selected conformer -- drives the highlight, never drives inclusion:
     * every record still gets its own series regardless of selection. */
    isSelected: boolean
    hasUsableFit: boolean
    hasMeasuredPoints: boolean
    measured: CpDisplayPoint[]
    fitted: { low: CpDisplayPoint[]; high: CpDisplayPoint[] } | null
}

/**
 * One chart series per DEPOSITED thermo record on this entry -- never
 * combined, never Boltzmann-averaged (see the module docstring on
 * `ThermoCpChart.tsx` for why). Order matches `records`' own order
 * one-to-one, so `series[i]` is always `records[i]`'s own series; nothing
 * here re-sorts or re-groups by conformer.
 */
export function buildCpChartSeries(
    records: readonly ThermoRecord[],
    conformers: readonly ConformerProjection[],
    selectedConformerGroupRef: string | null,
    unit: CpUnit,
): CpChartSeries[] {
    return records.map((record) => buildOneSeries(record, conformers, selectedConformerGroupRef, unit))
}

function seriesLabel(record: ThermoRecord, conformers: readonly ConformerProjection[]): string {
    const ref = thermoConformerGroupRef(record)
    // A bare `record.thermo_ref` here would be visually and textually
    // IDENTICAL to the `<code>{record.thermo_ref}</code>` the record's own
    // card heading already renders elsewhere on this same tab
    // (`ThermoRecordCard` in `EntryThermoSection.tsx`) -- two elements with
    // the exact same accessible text on one page, which is exactly the
    // "stale value indistinguishable from a real one" shape of bug this
    // slice was warned to avoid. Prefixed so a reader (and a test) can
    // still find the record but the two are never string-identical.
    if (!ref) return `Unlinked record ${record.thermo_ref}`
    const match = conformers.find((candidate) => candidate.conformer_group.conformer_group_ref === ref)
    return match ? conformerLabel(match) : ref
}

function buildOneSeries(
    record: ThermoRecord,
    conformers: readonly ConformerProjection[],
    selectedConformerGroupRef: string | null,
    unit: CpUnit,
): CpChartSeries {
    const nasa = (record.nasa ?? null) as NasaBlock | null
    const hasUsableFit = isNasaFitUsable(nasa)
    const points = record.points ?? null
    const measuredSource = (points ?? []).filter((point) => point.cp_j_mol_k != null)
    const hasMeasuredPoints = measuredSource.length > 0

    const measured: CpDisplayPoint[] = measuredSource.map((point) => ({
        temperatureK: point.temperature_k,
        cpDisplay: convertCpForDisplay(point.cp_j_mol_k as number, unit),
    }))

    const curve = nasaCpCurve(nasa)
    const fitted = curve && {
        low: curve.low.map((pt) => ({ temperatureK: pt.temperatureK, cpDisplay: convertCpForDisplay(pt.cpJMolK, unit) })),
        high: curve.high.map((pt) => ({ temperatureK: pt.temperatureK, cpDisplay: convertCpForDisplay(pt.cpJMolK, unit) })),
    }

    const linkedRef = thermoConformerGroupRef(record)
    return {
        thermoRef: record.thermo_ref,
        label: seriesLabel(record, conformers),
        isSelected: !!selectedConformerGroupRef && linkedRef === selectedConformerGroupRef,
        hasUsableFit,
        hasMeasuredPoints,
        measured,
        fitted,
    }
}
