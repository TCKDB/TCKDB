import type { EnergyProfileRecord } from "../api/reactionEnergyProfileApi"
import type { ReactionKineticsEntry } from "../api/reactionKineticsApi"
import { lotLabel, type LevelOfTheory } from "../api/scientificSchemas"
import { programText } from "./energyProfile"
import { aUnitsWord, modelKindWord, originWord, tunnellingWord } from "./kineticsWords"
import { kineticsAt } from "./kineticsTable"
import { formatQuantity, type QuantitySpecKey } from "./quantityFormat"
import { NOT_RECORDED, type CompareCell, type CompareRow } from "./speciesCompare"

/** Columns shown per group; the list below the table keeps every record. */
export const MAX_COLUMNS_PER_GROUP = 12
/** Reaction entries whose energy profile is read; the rest are named as not read. */
export const MAX_PROFILE_ENTRIES = 6
/** Temperatures k is offered at, each only where two or more records have it inside their own fitted range. */
export const K_TEMPERATURES = [300, 500, 1000, 1500, 2000] as const

export const OUTSIDE_FIT = "Outside this fit's range"
export const NO_RANGE = "Fitted range not recorded"
export const NOT_COMPUTED = "Not computed for this form"
export const NO_UNITS = "Units not recorded"

export type KineticsColumn = { key: string; ref: string; entryRef: string; record: ReactionKineticsEntry["record"] }

/** Records that share an energy level of theory and a program with its version; a part the record does not state is its own "Not recorded". */
export type KineticsGroup = {
    key: string
    level: LevelOfTheory | null
    program: { name: string; text: string } | null
    columns: KineticsColumn[]
    entryRefs: string[]
}

export function kineticsColumns(records: readonly ReactionKineticsEntry[]): KineticsColumn[] {
    return records.map(({ entryRef, record }) => ({ key: `kinetics-${record.kinetics_ref}`, ref: record.kinetics_ref, entryRef, record }))
}

/** Groups in the order their first record appears; records keep the archive's order inside a group. */
export function groupKinetics(columns: readonly KineticsColumn[]): KineticsGroup[] {
    const groups = new Map<string, KineticsGroup>()
    for (const column of columns) {
        const level = column.record.levels?.energy ?? null
        const software = column.record.provenance.primary_software ?? null
        const key = `${level ? lotLabel(level) : ""}|${level?.level_of_theory_ref ?? ""}|${software?.software ?? ""}|${software?.version ?? ""}`
        const group = groups.get(key) ?? {
            key,
            level,
            program: software ? { name: software.software, text: programText({ program: software.software, version: software.version }) } : null,
            columns: [],
            entryRefs: [],
        }
        group.columns.push(column)
        if (!group.entryRefs.includes(column.entryRef)) group.entryRefs.push(column.entryRef)
        groups.set(key, group)
    }
    return [...groups.values()]
}

/** The profiles of a group: same energy level and same program name as the group's records, never another's. */
export function profilesOfGroup(group: KineticsGroup, profiles: readonly EnergyProfileRecord[]): EnergyProfileRecord[] {
    if (!group.level || !group.program) return []
    const level = lotLabel(group.level)
    return profiles.filter((profile) => lotLabel(profile.level_of_theory) === level && profile.program === group.program?.name)
}

const absent = (words: string = NOT_RECORDED): CompareCell => ({ kind: "absent", words })

function wordCell(label: string, code: string | null | undefined, word: (code: string) => string | null): CompareCell {
    if (!code) return absent()
    const text = word(code)
    return { kind: "value", value: text ? { label, kind: "text", value: text, unit: null } : { label, kind: "code", value: code, unit: null } }
}

/** A number through its spec; the stored value rides along when the shown one is shorter. */
function numberCell(label: string, spec: QuantitySpecKey, value: number | null | undefined, unit?: string | null): CompareCell {
    const quantity = formatQuantity(spec, value, unit)
    if (!quantity || value == null) return absent()
    const shown = Number(quantity.exponent ? `${quantity.value}e${quantity.exponent.replace("−", "-")}` : quantity.value)
    return {
        kind: "value",
        value: { label, kind: "number", value: quantity.value.replace("-", "−"), unit: quantity.unit, exponent: quantity.exponent, ...(shown !== value && { full: String(value) }) },
    }
}

const aUnits = (column: KineticsColumn) => column.record.parameters.A_units ?? column.record.multi_arrhenius?.[0]?.A_units ?? null

function range(column: KineticsColumn): [number, number] | null {
    const { record_min_k: low, record_max_k: high } = column.record.temperature_coverage ?? {}
    return low != null && high != null ? [low, high] : null
}

function kCell(column: KineticsColumn, temperature: number, label: string): CompareCell {
    const bounds = range(column)
    if (!bounds) return absent(NO_RANGE)
    if (temperature < bounds[0] || temperature > bounds[1]) return absent(OUTSIDE_FIT)
    const k = kineticsAt(column.record, temperature)
    if (k === null) return absent(NOT_COMPUTED)
    const units = aUnits(column)
    return units ? numberCell(label, "kinetics_k", k, aUnitsWord(units)) : absent(NO_UNITS)
}

function kRows(columns: readonly KineticsColumn[]): CompareRow[] {
    return K_TEMPERATURES
        .filter((temperature) => columns.filter((column) => { const bounds = range(column); return bounds && temperature >= bounds[0] && temperature <= bounds[1] && kineticsAt(column.record, temperature) !== null }).length >= 2)
        .map((temperature) => {
            const label = `Rate coefficient k at ${temperature} K, from the record's own parameters`
            return { key: `k-${temperature}`, label, unit: null, compare: "continuous", cells: columns.map((column) => kCell(column, temperature, label)) }
        })
}

/** Signed difference of two stored numbers through a spec; null unless both are stated. */
function difference(spec: QuantitySpecKey, first: number | null | undefined, second: number | null | undefined, label: string, unit?: string | null): CompareCell | null {
    if (first == null || second == null) return null
    const delta = second - first
    const cell = numberCell(label, spec, delta, unit)
    if (cell.kind !== "value") return null
    return delta > 0 ? { kind: "value", value: { ...cell.value, value: `+${cell.value.value}` } } : cell
}

/**
 * One row per quantity, one cell per record. Settings (origin, model kind, tunnelling, reference temperature) are the
 * `exact` rows. A group of exactly two records closes with a difference (second minus first) in Ea and n; no
 * difference is ever formed between records of different groups.
 */
export function reactionRows(groups: readonly KineticsGroup[]): CompareRow[] {
    const columns = groups.flatMap((group) => group.columns)
    const cells = (make: (column: KineticsColumn) => CompareCell) => columns.map(make)
    const deltaOf = (read: (column: KineticsColumn) => number | null | undefined, make: (first: number | null | undefined, second: number | null | undefined) => CompareCell | null) =>
        groups.map((group) => (group.columns.length === 2 ? make(read(group.columns[0]), read(group.columns[1])) : null))
    const hasT0 = columns.some((column) => column.record.parameters.T0_k != null && column.record.parameters.T0_k !== 1)
    const range_ = (column: KineticsColumn): CompareCell => {
        const bounds = range(column)
        if (!bounds) return absent(NO_RANGE)
        const low = formatQuantity("thermo_temperature_k", bounds[0])
        const high = formatQuantity("thermo_temperature_k", bounds[1])
        return low && high ? { kind: "value", value: { label: "Fitted temperature range", kind: "number", value: `${low.value}–${high.value}`, unit: "K" } } : absent(NO_RANGE)
    }
    const rows: CompareRow[] = [
        { key: "origin", label: "Origin", unit: null, compare: "exact", cells: cells((c) => wordCell("Origin", c.record.scientific_origin, originWord)) },
        { key: "model", label: "Model kind", unit: null, compare: "exact", cells: cells((c) => wordCell("Model kind", c.record.model_kind, modelKindWord)) },
        { key: "a", label: "Pre-exponential factor A, unit as deposited", unit: null, compare: "continuous", cells: cells((c) => numberCell("A", "kinetics_a", c.record.parameters.A, aUnitsWord(aUnits(c)) || null)) },
        { key: "n", label: "Temperature exponent n", unit: null, compare: "continuous", cells: cells((c) => numberCell("n", "kinetics_n", c.record.parameters.n)), deltas: deltaOf((c) => c.record.parameters.n, (a, b) => difference("kinetics_n", a, b, "Difference in n")) },
        ...(hasT0 ? [{ key: "t0", label: "Reference temperature T0", unit: "K", compare: "exact" as const, cells: cells((c) => numberCell("T0", "thermo_temperature_k", c.record.parameters.T0_k)) }] : []),
        { key: "ea", label: "Activation energy Ea", unit: "kJ/mol", compare: "continuous", cells: cells((c) => numberCell("Ea", "kinetics_ea_kj_mol", c.record.parameters.Ea_kj_mol)), deltas: deltaOf((c) => c.record.parameters.Ea_kj_mol, (a, b) => difference("kinetics_ea_kj_mol", a, b, "Difference in Ea")) },
        { key: "range", label: "Fitted temperature range", unit: "K", compare: "continuous", cells: cells(range_) },
        { key: "tunnelling", label: "Tunnelling model", unit: null, compare: "exact", cells: cells((c) => wordCell("Tunnelling model", c.record.tunneling_model, tunnellingWord)) },
        ...kRows(columns),
    ]
    return rows
}

export const DELTA_FORMULA = "Difference = second record minus first record of the group, in the archive's order. Ea in kJ/mol, n without a unit."
