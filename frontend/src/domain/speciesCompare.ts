import type { ThermoRecord } from "../api/thermoApi"
import type { SpeciesValues } from "../api/speciesValuesApi"
import { statmechRow, thermoRow, type NumberRow, type NumberValue } from "./entryNumbers"
import { formatQuantity } from "./quantityFormat"

/** A cell is a recorded value, or the words saying why there is none. Nothing is ever filled in from another record. */
export type CompareCell = { kind: "value"; value: NumberValue } | { kind: "absent"; words: string }

/** An `exact` row is a setting that is either the same or not; a `continuous` row is a measured or computed number, never marked. */
export type CompareRow = { key: string; label: string; unit: string | null; compare: "exact" | "continuous"; cells: CompareCell[] }

/** One record of the molecule as a column: its own row of values, and the record behind it for heat capacity. */
export type CompareColumn = {
    key: string
    kind: "thermo" | "statmech"
    ref: string
    entryRef: string | null
    row: NumberRow
    thermo: ThermoRecord | null
}

export const NOT_RECORDED = "Not recorded"
export const OUTSIDE_RANGE = "Outside its range"
/** Columns shown per version group; the list below the tables keeps every record. */
export const MAX_COLUMNS_PER_VERSION = 12

const EXACT_LABELS: ReadonlySet<string> = new Set([
    "Treatment", "Rigid-rotor kind", "Point group", "External symmetry number", "Optical isomers", "Linear molecule",
    "Projected frequencies used", "Frequency scale factor", "Phase", "Reference pressure",
])

/** The thermo rows shown, in order. Other thermo values are on the record's own page. */
const THERMO_LABELS = [
    "Enthalpy of formation, 298 K", "Enthalpy, 298 K", "Entropy, 298 K", "Enthalpy of formation, 0 K",
    "Temperature range covered", "Phase", "Reference pressure",
]

export function compareColumns(values: SpeciesValues): { thermo: CompareColumn[]; statmech: CompareColumn[] } {
    return {
        thermo: values.thermo.records.map(({ entryRef, record }) => {
            const row = thermoRow(record)
            return { key: `thermo-${row.ref}`, kind: "thermo" as const, ref: row.ref, entryRef, row, thermo: record }
        }),
        statmech: values.statmech.records.map(({ entryRef, record }) => {
            const row = statmechRow(record)
            return { key: `statmech-${row.ref}`, kind: "statmech" as const, ref: row.ref, entryRef, row, thermo: null }
        }),
    }
}

export type VersionGroup = { key: string; entryRef: string | null; columns: CompareColumn[] }

/** Columns grouped by version in the molecule's version order, versions the listing does not name after them; archive order inside a group. */
export function groupByVersion(columns: readonly CompareColumn[], versionOrder: readonly string[]): VersionGroup[] {
    const groups = new Map<string, VersionGroup>()
    for (const column of columns) {
        const key = column.entryRef ?? "none"
        const group = groups.get(key) ?? { key, entryRef: column.entryRef, columns: [] }
        group.columns.push(column)
        groups.set(key, group)
    }
    const rank = (group: VersionGroup) => (group.entryRef && versionOrder.includes(group.entryRef) ? versionOrder.indexOf(group.entryRef) : versionOrder.length)
    return [...groups.values()].sort((a, b) => rank(a) - rank(b))
}

function valueCell(value: NumberValue | undefined): CompareCell {
    if (!value) return { kind: "absent", words: NOT_RECORDED }
    if (value.value === null) return { kind: "absent", words: value.absent ?? NOT_RECORDED }
    return { kind: "value", value }
}

/** One row per label some column records, in the order of `labels` first, then first seen. */
export function valueRows(columns: readonly CompareColumn[], labels: readonly string[] = []): CompareRow[] {
    const order = [...labels]
    for (const column of columns) for (const value of column.row.values) if (!order.includes(value.label)) order.push(value.label)
    return order
        .filter((label) => columns.some((column) => column.row.values.some((value) => value.label === label && value.value !== null)))
        .map((label) => {
            const cells = columns.map((column) => valueCell(column.row.values.find((value) => value.label === label)))
            const unit = cells.flatMap((cell) => (cell.kind === "value" && cell.value.unit ? [cell.value.unit] : []))[0] ?? null
            return { key: label, label, unit, compare: EXACT_LABELS.has(label) ? "exact" : "continuous", cells }
        })
}

export function thermoValueRows(columns: readonly CompareColumn[]): CompareRow[] {
    return valueRows(columns, THERMO_LABELS).filter((row) => THERMO_LABELS.includes(row.label))
}

/** The temperatures a record's own range covers: its stated coverage, else the span of its points. */
function rangeOf(record: ThermoRecord): [number, number] | null {
    const coverage = record.temperature_coverage
    if (coverage?.record_min_k != null && coverage?.record_max_k != null) return [coverage.record_min_k, coverage.record_max_k]
    const temperatures = (record.points ?? []).map((point) => point.temperature_k)
    return temperatures.length > 0 ? [Math.min(...temperatures), Math.max(...temperatures)] : null
}

/** The record's deposited heat capacity points that lie inside its own stated range. */
function pointsInRange(record: ThermoRecord | null): { temperature_k: number; cp_j_mol_k: number }[] {
    if (!record) return []
    const range = rangeOf(record)
    return (record.points ?? []).flatMap((point) => (point.cp_j_mol_k != null && range && point.temperature_k >= range[0] && point.temperature_k <= range[1]
        ? [{ temperature_k: point.temperature_k, cp_j_mol_k: point.cp_j_mol_k }]
        : []))
}

/**
 * Heat capacity at each temperature at which two or more records have a deposited point inside their own range. A record
 * with no such point there says "Outside its range" when the temperature lies beyond its own range and "Not recorded"
 * otherwise; no value is interpolated, fitted or taken from another record.
 */
export function cpRows(columns: readonly CompareColumn[]): CompareRow[] {
    const counts = new Map<number, number>()
    for (const column of columns) {
        for (const temperature of new Set(pointsInRange(column.thermo).map((point) => point.temperature_k))) counts.set(temperature, (counts.get(temperature) ?? 0) + 1)
    }
    return [...counts]
        .filter(([, count]) => count >= 2)
        .map(([temperature]) => temperature)
        .sort((a, b) => a - b)
        .map((temperature) => {
            const label = `Heat capacity at ${formatQuantity("thermo_temperature_k", temperature)?.value} K`
            const cells = columns.map((column): CompareCell => {
                const point = pointsInRange(column.thermo).find((candidate) => candidate.temperature_k === temperature)
                const quantity = formatQuantity("thermo_cp_j_mol_k", point?.cp_j_mol_k)
                if (quantity) return { kind: "value", value: { label, kind: "number", value: quantity.value.replace("-", "−"), unit: quantity.unit, exponent: quantity.exponent, full: quantity.full } }
                const range = column.thermo ? rangeOf(column.thermo) : null
                return { kind: "absent", words: range && (temperature < range[0] || temperature > range[1]) ? OUTSIDE_RANGE : NOT_RECORDED }
            })
            return { key: `cp-${temperature}`, label, unit: "J/mol·K", compare: "continuous", cells }
        })
}

function cellText(cell: CompareCell): string | null {
    return cell.kind === "value" ? (cell.value.full ?? `${cell.value.value}|${cell.value.exponent ?? ""}`) : null
}

/**
 * Whether a setting is not the same in every record of some version group. `spans` are the group sizes in column order.
 * Only an `exact` row can differ; a group with fewer than two recorded cells says nothing. This is not a verdict.
 */
export function rowDiffers(row: CompareRow, spans: readonly number[]): boolean {
    if (row.compare !== "exact") return false
    let start = 0
    for (const span of spans) {
        const texts = row.cells.slice(start, start + span).map(cellText).filter((text) => text !== null)
        if (texts.length >= 2 && new Set(texts).size > 1) return true
        start += span
    }
    return false
}

/** A row label with a no-break space before the K, so "298 K" never splits across lines. */
export function displayLabel(label: string): string {
    return label.replace(/(?<=\d) K$/, " K")
}

/** The number a cell sorts by, exponent included; null for words and absent cells. */
export function sortNumber(cell: CompareCell): number | null {
    if (cell.kind !== "value" || cell.value.kind !== "number" || cell.value.value === null) return null
    if (cell.value.full !== undefined && Number.isFinite(Number(cell.value.full))) return Number(cell.value.full)
    const mantissa = cell.value.value.replace("−", "-").replace(/\s*±.*$/, "")
    const exponent = (cell.value.exponent ?? "").replace("−", "-")
    const parsed = exponent ? Number(`${mantissa}e${exponent}`) : Number.parseFloat(mantissa)
    return Number.isFinite(parsed) ? parsed : null
}
