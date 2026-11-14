import type { EnergyProfileRecord } from "../api/reactionEnergyProfileApi"
import type { ReactionKineticsEntry } from "../api/reactionKineticsApi"
import { lotLabel, type LevelOfTheory } from "../api/scientificSchemas"
import { programText } from "./energyProfile"
import { aUnitsWord, degeneracyWord, directionWord, modelKindWord, originWord, pressureContextWord, tunnellingWord } from "./kineticsWords"
import { kineticsAt } from "./kineticsTable"
import { deposited, evaluated, formatQuantity, type QuantitySpecKey } from "./quantityFormat"
import { NOT_RECORDED, type CompareCell, type CompareRow } from "./speciesCompare"

/** Columns shown per group; the list below the table keeps every record. */
export const MAX_COLUMNS_PER_GROUP = 12
/** Reaction entries whose energy profile is read; the rest are named as not read. */
export const MAX_PROFILE_ENTRIES = 6
/** Temperatures k is offered at, each only where two or more records have it inside their own fitted range. */
export const K_TEMPERATURES = [300, 500, 1000, 1500, 2000] as const

export const OUTSIDE_FIT = "Outside this fit's range"
export const NO_RANGE = "Fitted range not recorded"
export const NOT_COMPUTED = "Not computed for this model kind"
export const NEEDS_PRESSURE = "Needs a pressure"
export const NO_UNITS = "Units not recorded"
export const NO_DELTA = "No difference for this row"
export const PER_TERM = "Per term, see the record"
export const PER_PRESSURE = "Per pressure, see the record"

type Record_ = ReactionKineticsEntry["record"]
export type KineticsColumn = { key: string; ref: string; entryRef: string; record: Record_ }

/**
 * Records that share an energy level of theory and a program with its version. A part the record does not state is its
 * own "Not recorded"; the level may be the one the depositor declared, which `levelDeclared` says.
 */
export type KineticsGroup = {
    key: string
    level: LevelOfTheory | null
    levelDeclared: boolean
    program: { name: string; version: string | null; text: string } | null
    columns: KineticsColumn[]
    entryRefs: string[]
}

export function kineticsColumns(records: readonly ReactionKineticsEntry[]): KineticsColumn[] {
    return records.map(({ entryRef, record }) => ({ key: `kinetics-${record.kinetics_ref}`, ref: record.kinetics_ref, entryRef, record }))
}

const levelKey = (level: LevelOfTheory | null) => (level ? (level.level_of_theory_ref ?? lotLabel(level)) : "")

/** Groups in the order their first record appears; records keep the archive's order inside a group. */
export function groupKinetics(columns: readonly KineticsColumn[]): KineticsGroup[] {
    const groups = new Map<string, KineticsGroup>()
    for (const column of columns) {
        const energy = column.record.levels?.energy ?? null
        const level = energy ?? column.record.levels?.declared_energy ?? null
        const levelDeclared = energy === null && level !== null
        const software = column.record.provenance.primary_software ?? null
        const key = `${levelKey(level)}|${levelDeclared ? "declared" : "derived"}|${software?.software ?? ""}|${software?.version ?? ""}`
        const group = groups.get(key) ?? {
            key,
            level,
            levelDeclared,
            program: software ? { name: software.software, version: software.version ?? null, text: programText({ program: software.software, version: software.version }) } : null,
            columns: [],
            entryRefs: [],
        }
        group.columns.push(column)
        if (!group.entryRefs.includes(column.entryRef)) group.entryRefs.push(column.entryRef)
        groups.set(key, group)
    }
    return [...groups.values()]
}

/** What the groups differ in: methods, programs or both; null for fewer than two groups. */
export function groupDifference(groups: readonly KineticsGroup[]): "methods" | "programs" | "both" | null {
    if (groups.length < 2) return null
    const methods = new Set(groups.map((group) => levelKey(group.level))).size > 1
    const programs = new Set(groups.map((group) => (group.program ? `${group.program.name}|${group.program.version ?? ""}` : ""))).size > 1
    return methods && programs ? "both" : methods ? "methods" : programs ? "programs" : null
}

/** The program line's label: the energy calculation's program when every record cites one, else the record's primary source calculation. */
export function programLabel(group: KineticsGroup): string {
    return programFromSource(group) ? "Program of the source calculation" : "Program of the energy calculation"
}

/** True when the group's program is the primary source calculation's, not the energy calculation's. */
export const programFromSource = (group: KineticsGroup): boolean => !group.columns.every((column) => column.record.provenance.ts_sp_calculation_ref)

const sameLevel = (a: LevelOfTheory, b: LevelOfTheory) => (a.level_of_theory_ref && b.level_of_theory_ref ? a.level_of_theory_ref === b.level_of_theory_ref : lotLabel(a) === lotLabel(b))

/** The profiles of a group: same level (by ref when both have one), same program and a version the group's own program states; never another's. */
export function profilesOfGroup(group: KineticsGroup, profiles: readonly EnergyProfileRecord[]): EnergyProfileRecord[] {
    const { level, program } = group
    if (!level || !program) return []
    return profiles.filter((profile) => sameLevel(profile.level_of_theory, level) && profile.program === program.name && profile.versions.includes(program.version))
}

const absent = (words: string = NOT_RECORDED): CompareCell => ({ kind: "absent", words })

function wordCell(label: string, code: string | null | undefined, word: (code: string) => string | null, missing: string = NOT_RECORDED): CompareCell {
    if (!code) return absent(missing)
    const text = word(code)
    return { kind: "value", value: text ? { label, kind: "text", value: text, unit: null } : { label, kind: "code", value: code, unit: null } }
}

function withFull(label: string, quantity: ReturnType<typeof formatQuantity>, value: number): CompareCell {
    if (!quantity) return absent()
    const shown = Number(quantity.exponent ? `${quantity.value}e${quantity.exponent.replace("−", "-")}` : quantity.value)
    return {
        kind: "value",
        value: { label, kind: "number", value: quantity.value.replace("-", "−"), unit: quantity.unit, exponent: quantity.exponent, ...(shown !== value && { full: String(value) }) },
    }
}

/** A number through its `QUANTITY_SPECS` entry; the stored value rides along when the shown one is shorter. */
function numberCell(label: string, spec: QuantitySpecKey, value: number | null | undefined, unit?: string | null): CompareCell {
    return value == null ? absent() : withFull(label, formatQuantity(spec, value, unit), value)
}

/** A computed k at 6 significant figures; the served value rides along when the shown one is shorter. */
function evaluatedCell(label: string, value: number, unit: string | null): CompareCell {
    const quantity = evaluated(value, unit)
    if (!quantity) return absent()
    return { kind: "value", value: { label, kind: "number", value: quantity.value, unit: quantity.unit, exponent: quantity.exponent, ...(quantity.full !== undefined && { full: quantity.full }) } }
}

type Form = "simple" | "multi" | "pressure" | "other"

function formOf(record: Record_): Form {
    if (record.model_kind === "arrhenius" || record.model_kind === "modified_arrhenius") return "simple"
    if (record.model_kind === "multi_arrhenius") return "multi"
    return Object.hasOwn({ plog: 1, chebyshev: 1, troe: 1, lindemann: 1, sri: 1 }, record.model_kind) || record.is_third_body ? "pressure" : "other"
}

/** The words for a parameter a record does not state as one number: per term or per pressure, by its model kind. */
function parameterGap(record: Record_): string {
    const form = formOf(record)
    return form === "multi" ? PER_TERM : form === "pressure" ? PER_PRESSURE : NOT_RECORDED
}

function parameterCell(label: string, spec: QuantitySpecKey, record: Record_, value: number | null | undefined, unit?: string | null): CompareCell {
    return value == null ? absent(parameterGap(record)) : numberCell(label, spec, value, unit)
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
    if (k === null) return absent(formOf(column.record) === "pressure" ? NEEDS_PRESSURE : NOT_COMPUTED)
    const units = aUnits(column)
    if (!units) return absent(NO_UNITS)
    const cell = evaluatedCell(label, k, aUnitsWord(units))
    const degeneracy = column.record.reaction_path_degeneracy
    if (cell.kind === "value" && degeneracy) {
        const g = deposited(degeneracy.value)?.value ?? degeneracy.value
        if (degeneracy.convention === "not_applied") return { ...cell, note: `k excludes reaction-path degeneracy g = ${g}` }
        if (degeneracy.convention !== "already_applied") return { ...cell, note: `whether k includes reaction-path degeneracy g = ${g} is not stated` }
    }
    return cell
}

function kRows(columns: readonly KineticsColumn[]): CompareRow[] {
    return K_TEMPERATURES
        .filter((temperature) => columns.filter((column) => { const bounds = range(column); return bounds && temperature >= bounds[0] && temperature <= bounds[1] && kineticsAt(column.record, temperature) !== null }).length >= 2)
        .map((temperature) => {
            const label = `Rate coefficient k at ${temperature} K, from the record's own parameters`
            return { key: `k-${temperature}`, label, unit: null, compare: "continuous", cells: columns.map((column) => kCell(column, temperature, label)) }
        })
}

/** Why a group of two records gets no difference, or null when every setting that gives the numbers meaning matches. */
export function deltaReason(group: KineticsGroup): string | null {
    if (group.columns.length !== 2) return "Differences are formed only for a group of two records"
    const [a, b] = [group.columns[0], group.columns[1]]
    const [x, y] = [a.record, b.record]
    if (!x.direction || !y.direction) return "Direction is not stated in both records"
    if (x.direction !== y.direction) return "The records have different directions"
    if (x.model_kind !== y.model_kind) return "The records have different model kinds"
    if (formOf(x) !== "simple") return "Differences are formed only for single-term records"
    if ((x.parameters.T0_k ?? null) !== (y.parameters.T0_k ?? null)) return "The records have different reference temperatures"
    if (aUnits(a) !== aUnits(b)) return "The records give A in different units"
    if ((x.pressure_context ?? null) !== (y.pressure_context ?? null) || (x.pressure_bar ?? null) !== (y.pressure_bar ?? null)) return "The records have different pressure contexts"
    return null
}

/** The two records a group's difference is between, second first as written "second − first"; null unless the group has exactly two. */
export function deltaNames(group: KineticsGroup, name: (column: KineticsColumn) => string = (column) => column.ref): [string, string] | null {
    return group.columns.length === 2 ? [name(group.columns[1]), name(group.columns[0])] : null
}

/** Signed difference of two stored numbers through a spec; null unless both are stated. */
function difference(spec: QuantitySpecKey, first: number | null | undefined, second: number | null | undefined, label: string, unit?: string | null): CompareCell | null {
    if (first == null || second == null) return null
    const delta = second - first
    const cell = numberCell(label, spec, delta, unit)
    if (cell.kind !== "value") return null
    return delta > 0 ? { ...cell, value: { ...cell.value, value: `+${cell.value.value}` } } : cell
}

function plainNumber(label: string, value: number | null | undefined): CompareCell {
    return value == null ? absent() : withFull(label, deposited(value), value)
}

/**
 * One row per quantity, one cell per record. Settings (origin, direction, model kind, pressure, degeneracy, tunnelling,
 * reference temperature) are the `exact` rows. A group of exactly two records closes with a difference (second minus
 * first) in Ea and n, only when the settings that give those numbers their meaning match; otherwise the cell says why
 * there is none. No difference is ever formed between records of different groups.
 */
export function reactionRows(groups: readonly KineticsGroup[]): CompareRow[] {
    const columns = groups.flatMap((group) => group.columns)
    const cells = (make: (column: KineticsColumn) => CompareCell) => columns.map(make)
    const deltaOf = (read: (column: KineticsColumn) => number | null | undefined, make: (first: number | null | undefined, second: number | null | undefined) => CompareCell | null): (CompareCell | null)[] =>
        groups.map((group) => {
            if (group.columns.length !== 2) return null
            const reason = deltaReason(group)
            return reason ? absent(reason) : (make(read(group.columns[0]), read(group.columns[1])) ?? absent("Not stated in both records"))
        })
    const blank = groups.map((group) => (group.columns.length === 2 ? absent(NO_DELTA) : null))
    const hasT0 = columns.some((column) => column.record.parameters.T0_k != null && column.record.parameters.T0_k !== 1)
    const rangeCell = (column: KineticsColumn): CompareCell => {
        const bounds = range(column)
        if (!bounds) return absent(NO_RANGE)
        const low = formatQuantity("thermo_temperature_k", bounds[0])
        const high = formatQuantity("thermo_temperature_k", bounds[1])
        return low && high ? { kind: "value", value: { label: "Fitted temperature range", kind: "number", value: `${low.value}–${high.value}`, unit: "K" } } : absent(NO_RANGE)
    }
    const rows: CompareRow[] = [
        { key: "origin", label: "Origin", unit: null, compare: "exact", cells: cells((c) => wordCell("Origin", c.record.scientific_origin, originWord)) },
        { key: "direction", label: "Direction", unit: null, compare: "exact", cells: cells((c) => wordCell("Direction", c.record.direction, directionWord, "Not stated")) },
        { key: "model", label: "Model kind", unit: null, compare: "exact", cells: cells((c) => wordCell("Model kind", c.record.model_kind, modelKindWord)) },
        { key: "pressure-context", label: "Pressure context", unit: null, compare: "exact", cells: cells((c) => wordCell("Pressure context", c.record.pressure_context, pressureContextWord, "Not stated")) },
        { key: "pressure", label: "Pressure", unit: "bar", compare: "exact", cells: cells((c) => (c.record.pressure_bar == null ? absent("Not stated") : numberCell("Pressure", "thermo_reference_pressure_bar", c.record.pressure_bar))) },
        { key: "degeneracy", label: "Reaction-path degeneracy g", unit: null, compare: "exact", cells: cells((c) => (c.record.reaction_path_degeneracy ? plainNumber("g", c.record.reaction_path_degeneracy.value) : absent("Not stated"))) },
        { key: "degeneracy-in-k", label: "Degeneracy and the reported k", unit: null, compare: "exact", cells: cells((c) => wordCell("Degeneracy and the reported k", c.record.reaction_path_degeneracy?.convention, degeneracyWord, "Not stated")) },
        { key: "a", label: "Pre-exponential factor A, unit as deposited", unit: null, compare: "continuous", cells: cells((c) => parameterCell("A", "kinetics_a", c.record, c.record.parameters.A, aUnitsWord(aUnits(c)) || null)) },
        { key: "n", label: "Temperature exponent n", unit: null, compare: "continuous", cells: cells((c) => parameterCell("n", "kinetics_n", c.record, c.record.parameters.n)), deltas: deltaOf((c) => c.record.parameters.n, (a, b) => difference("kinetics_n", a, b, "Difference in n")) },
        ...(hasT0 ? [{ key: "t0", label: "Reference temperature T0", unit: "K", compare: "exact" as const, cells: cells((c) => numberCell("T0", "thermo_temperature_k", c.record.parameters.T0_k)) }] : []),
        { key: "ea", label: "Activation energy Ea", unit: "kJ/mol", compare: "continuous", cells: cells((c) => parameterCell("Ea", "kinetics_ea_kj_mol", c.record, c.record.parameters.Ea_kj_mol)), deltas: deltaOf((c) => c.record.parameters.Ea_kj_mol, (a, b) => difference("kinetics_ea_kj_mol", a, b, "Difference in Ea")) },
        { key: "range", label: "Fitted temperature range", unit: "K", compare: "continuous", cells: cells(rangeCell) },
        { key: "tunnelling", label: "Tunnelling model", unit: null, compare: "exact", cells: cells((c) => wordCell("Tunnelling model", c.record.tunneling_model, tunnellingWord)) },
        ...kRows(columns),
    ]
    return rows.map((row) => (row.deltas ? row : { ...row, deltas: blank }))
}

export const DELTA_FORMULA = "Difference = second record minus first record of a group of two, in the archive's order. Ea in kJ/mol, n without a unit. It is formed only when direction, model kind, reference temperature, unit of A and pressure context match."
