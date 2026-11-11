import type { ThermoRecord } from "../api/thermoApi"
import type { SpeciesValues } from "../api/speciesValuesApi"
import { statmechRow, thermoRow, type NumberRow, type NumberValue } from "./entryNumbers"
import { formatQuantity } from "./quantityFormat"

/** A cell is a recorded value, or the words saying why there is none. Nothing is ever filled in from another record. */
export type CompareCell = { kind: "value"; value: NumberValue } | { kind: "absent"; words: string }

export type CompareRow = { key: string; label: string; unit: string | null; cells: CompareCell[] }

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

/** The enthalpy and entropy rows, in the order they are shown. Other thermo values are on the record's own page. */
const THERMO_LABELS = ["Enthalpy of formation, 298 K", "Enthalpy, 298 K", "Entropy, 298 K", "Enthalpy of formation, 0 K"]

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
            return { key: label, label, unit, cells }
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

/**
 * Heat capacity at each temperature at which two or more records have a deposited point. A record with no point there
 * says "Outside its range" when the temperature lies beyond its own range and "Not recorded" otherwise; no value is
 * interpolated, fitted or taken from another record.
 */
export function cpRows(columns: readonly CompareColumn[]): CompareRow[] {
    const counts = new Map<number, number>()
    for (const column of columns) {
        const temperatures = new Set((column.thermo?.points ?? []).filter((point) => point.cp_j_mol_k != null).map((point) => point.temperature_k))
        for (const temperature of temperatures) counts.set(temperature, (counts.get(temperature) ?? 0) + 1)
    }
    return [...counts]
        .filter(([, count]) => count >= 2)
        .map(([temperature]) => temperature)
        .sort((a, b) => a - b)
        .map((temperature) => {
            const label = `Heat capacity at ${formatQuantity("thermo_temperature_k", temperature)?.value} K`
            const cells = columns.map((column): CompareCell => {
                const point = (column.thermo?.points ?? []).find((candidate) => candidate.temperature_k === temperature && candidate.cp_j_mol_k != null)
                const quantity = formatQuantity("thermo_cp_j_mol_k", point?.cp_j_mol_k)
                if (quantity) return { kind: "value", value: { label, kind: "number", value: quantity.value.replace("-", "−"), unit: quantity.unit } }
                const range = column.thermo ? rangeOf(column.thermo) : null
                return { kind: "absent", words: range && (temperature < range[0] || temperature > range[1]) ? OUTSIDE_RANGE : NOT_RECORDED }
            })
            return { key: label, label, unit: "J/mol·K", cells }
        })
}

function cellText(cell: CompareCell): string | null {
    return cell.kind === "value" ? `${cell.value.value}|${cell.value.exponent ?? ""}` : null
}

/**
 * Which cells of a row differ from the others: a recorded cell whose text no other recorded cell in the row shares.
 * A row with fewer than two recorded cells marks nothing. This says the numbers are not the same, not which is right.
 */
export function differingCells(row: CompareRow): boolean[] {
    const texts = row.cells.map(cellText)
    const recorded = texts.filter((text) => text !== null)
    return texts.map((text) => text !== null && recorded.length >= 2 && recorded.filter((other) => other === text).length === 1)
}

/** The number a cell sorts by; null for words and absent cells. */
export function sortNumber(cell: CompareCell): number | null {
    if (cell.kind !== "value" || cell.value.kind !== "number" || cell.value.value === null) return null
    const parsed = Number.parseFloat(cell.value.value.replace("−", "-"))
    return Number.isFinite(parsed) ? parsed : null
}
