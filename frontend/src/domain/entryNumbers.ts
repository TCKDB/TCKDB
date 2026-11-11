import { lotLabel, type LevelOfTheory } from "../api/scientificSchemas"
import type { OrderEcho } from "../api/listEnvelope"
import type { StatmechRecord } from "../api/statmechApi"
import type { ThermoRecord } from "../api/thermoApi"
import type { TransportRecord } from "../api/transportApi"
import type { SourceLevel } from "../components/ValueSource"
import { orderWords } from "./paging"
import { formatQuantity, type QuantitySpecKey } from "./quantityFormat"
import { resolveProductLevels, type ProductLevels } from "./productLevels"
import { THERMO_MODEL_KIND_WORDS } from "./thermoModelKind"
import { softwareLabel, toolReleaseLabel, words } from "./provenanceFormat"
import { publicPointGroup } from "./publicText"

export type NumberKind = "thermo" | "statmech" | "transport"

/**
 * One value of a record. `number` links to its evidence; `text` and `pointGroup` are plain words; `code` is a stored
 * code TCKDB has no wording for. A null `value` is shown as `absent`, else "Not recorded".
 */
export type NumberValue = {
    label: string
    kind: "number" | "text" | "code" | "pointGroup"
    value: string | null
    unit: string | null
    exponent?: string
    /** The stored value, when the shown one is rounded. */
    full?: string
    absent?: string
}

/** What a thermo record says it describes, from `thermodynamic_target` only. */
export type Describes = {
    kind: "all" | "one" | "unnamed" | "unstated" | "unknown"
    groupRef?: string
    code?: string
    /** The target was implied by the CHEMKIN format, not declared by the depositor. */
    implied: boolean
}

export type NumberRow = {
    kind: NumberKind
    ref: string
    review: string
    values: NumberValue[]
    levels: SourceLevel[]
    /** The group key for "Group by level of theory": the record's own levels, never another record's. */
    levelKey: string
    programs: string[]
    describes: Describes | null
    /** The conformer group this record is traced to or declared for, so an evidence link lands where the record is listed. */
    conformerGroupRef: string | null
}

export const NO_LEVEL_KEY = "No level of theory recorded"

/** Records read per list at most; the first page is the server's own default window, later pages ask for this many. */
export const MAX_RECORDS = 1000
export const LATER_PAGE_LIMIT = 200

/** The id a row carries, so a link from this or another page can name one record. */
export function numberAnchor(kind: NumberKind, ref: string): string {
    return `${kind}-${ref}`
}

const TREATMENT_WORDS: Readonly<Record<string, string>> = {
    rrho: "Rigid rotor, harmonic oscillator",
    rrho_1d: "Rigid rotor, harmonic oscillator, one-dimensional hindered rotors",
    rrho_nd: "Rigid rotor, harmonic oscillator, multidimensional hindered rotors",
    rrho_1d_nd: "Rigid rotor, harmonic oscillator, one-dimensional and multidimensional hindered rotors",
    rrao: "Rigid rotor, anharmonic oscillator",
}

const ROTOR_WORDS: Readonly<Record<string, string>> = {
    atom: "Atom",
    linear: "Linear",
    spherical_top: "Spherical top",
    symmetric_top: "Symmetric top",
    asymmetric_top: "Asymmetric top",
}

const PHASE_WORDS: Readonly<Record<string, string>> = { gas: "Gas", liquid: "Liquid", solid: "Solid", aqueous: "Aqueous" }

function wordOrCode(label: string, code: string | null | undefined, wording: Readonly<Record<string, string>>): NumberValue {
    if (!code) return { label, kind: "text", value: null, unit: null }
    return Object.hasOwn(wording, code)
        ? { label, kind: "text", value: wording[code], unit: null }
        : { label, kind: "code", value: code, unit: null }
}

function yesNo(label: string, flag: boolean | null | undefined): NumberValue {
    return { label, kind: "text", value: flag == null ? null : flag ? "Yes" : "No", unit: null }
}

/** A number through its `QUANTITY_SPECS` entry; the spread, when given, is formatted by its own spec. */
function quantity(label: string, spec: QuantitySpecKey, raw: number | null | undefined, spread?: { spec: QuantitySpecKey; raw: number | null | undefined }): NumberValue {
    const main = formatQuantity(spec, raw)
    if (!main) return { label, kind: "number", value: null, unit: null }
    const plus = spread ? formatQuantity(spread.spec, spread.raw) : null
    const value = main.value.replace("-", "−") + (plus ? ` ± ${plus.value}` : "")
    return { label, kind: "number", value, unit: main.unit, exponent: main.exponent, ...(main.full && !plus && { full: main.full }) }
}

/** A whole number as stored, which has no rounding to apply. */
function count(label: string, raw: number | null | undefined): NumberValue {
    return { label, kind: "number", value: raw == null ? null : String(raw), unit: null }
}

export function pointGroupValue(raw: string | null | undefined): NumberValue {
    const shown = publicPointGroup(raw)
    if (!raw) return { label: "Point group", kind: "pointGroup", value: null, unit: null }
    return { label: "Point group", kind: "pointGroup", value: shown, unit: null, absent: "Not shown" }
}

/** A point group split into its letter and the subscript that follows it: `C2v` becomes `C` and `2v`. */
export function pointGroupParts(value: string): { symbol: string; subscript: string } {
    const match = /^([A-Z])(.*)$/.exec(value)
    if (!match) return { symbol: value, subscript: "" }
    return { symbol: match[1], subscript: match[2].replace(/^(?:inf|\*)/, "∞") }
}

function levelsOf(levels: ProductLevels): SourceLevel[] {
    const out: SourceLevel[] = []
    if (levels.geometry) out.push({ role: "geometry", label: lotLabel(levels.geometry) })
    if (levels.frequency) out.push({ role: "frequency", label: lotLabel(levels.frequency) })
    if (levels.energy) out.push({ role: "energy", label: lotLabel(levels.energy) })
    return out
}

/** Levels as "label (roles)" parts, one per distinct label, in first-seen order. */
export function levelParts(levels: readonly SourceLevel[]): { label: string; roles: string[] }[] {
    const parts: { label: string; roles: string[] }[] = []
    for (const level of levels) {
        const part = parts.find((candidate) => candidate.label === level.label)
        if (part) part.roles.push(level.role)
        else parts.push({ label: level.label, roles: [level.role] })
    }
    return parts
}

function levelKeyOf(levels: SourceLevel[]): string {
    if (levels.length === 0) return NO_LEVEL_KEY
    return levelParts(levels).map((part) => `${part.label} (${part.roles.join(", ")})`).join(" · ")
}

function programsOf(...labels: (string | null | undefined)[]): string[] {
    return [...new Set(labels.filter((label): label is string => Boolean(label)))]
}

/** The levels a thermo record carries itself: `levels`, else its source calculations, else its one provenance level. */
export function thermoRecordProductLevels(record: ThermoRecord): ProductLevels {
    if (record.levels) return resolveProductLevels(record.levels, null)
    if (record.source_calculations && record.source_calculations.length > 0) {
        return resolveProductLevels(null, record.source_calculations)
    }
    const lot = record.provenance?.level_of_theory ?? null
    return { geometry: lot, frequency: lot, energy: lot, energy_source: lot ? "opt" : null }
}

export function describesOf(record: ThermoRecord): Describes {
    const target = record.thermodynamic_target
    if (!target) return { kind: "unstated", implied: false }
    const implied = target.declared_by === "chemkin_format"
    if (target.kind === "equilibrium_ensemble") return { kind: "all", implied }
    if (target.kind === "single_conformer") {
        return target.conformer_group_ref ? { kind: "one", groupRef: target.conformer_group_ref, implied } : { kind: "unnamed", implied }
    }
    return { kind: "unknown", code: target.kind, implied }
}

export function thermoRow(record: ThermoRecord): NumberRow {
    const shown = levelsOf(thermoRecordProductLevels(record))
    const formation = record.enthalpy_reference_kind === "formation_298k"
    const provenance = record.provenance
    const coverage = record.temperature_coverage
    const range = coverage?.record_min_k != null && coverage?.record_max_k != null
        ? `${formatQuantity("thermo_temperature_k", coverage.record_min_k)?.value}–${formatQuantity("thermo_temperature_k", coverage.record_max_k)?.value}`
        : null
    const describes = describesOf(record)
    return {
        kind: "thermo",
        ref: record.thermo_ref,
        review: record.review.status,
        values: [
            quantity(formation ? "Enthalpy of formation, 298 K" : "Enthalpy, 298 K", "thermo_h298_kj_mol", record.h298_kj_mol, { spec: "thermo_h298_uncertainty_kj_mol", raw: record.h298_uncertainty_kj_mol }),
            quantity("Entropy, 298 K", "thermo_s298_j_mol_k", record.s298_j_mol_k, { spec: "thermo_s298_uncertainty_j_mol_k", raw: record.s298_uncertainty_j_mol_k }),
            quantity("Enthalpy of formation, 0 K", "thermo_enthalpy_formation_0k_kj_mol", record.enthalpy_formation_0k_kj_mol, { spec: "thermo_enthalpy_formation_0k_uncertainty_kj_mol", raw: record.enthalpy_formation_0k_uncertainty_kj_mol }),
            quantity("Reference pressure", "thermo_reference_pressure_bar", record.reference_pressure_bar),
            { label: "Temperature range covered", kind: "number", value: range, unit: range ? "K" : null },
            wordOrCode("Phase", record.phase, PHASE_WORDS),
            wordOrCode("Form of the model", record.model_kind, THERMO_MODEL_KIND_WORDS),
        ],
        levels: shown,
        levelKey: levelKeyOf(shown),
        programs: programsOf(
            softwareLabel(provenance?.primary_calculation?.software),
            softwareLabel(provenance?.software_release),
            toolReleaseLabel(provenance?.workflow_tool_release),
        ),
        describes,
        conformerGroupRef: provenance?.conformer_group_ref ?? describes.groupRef ?? null,
    }
}

export function statmechRow(record: StatmechRecord): NumberRow {
    const core = record.statmech
    const shown = levelsOf(resolveProductLevels(record.levels, record.source_calculations))
    return {
        kind: "statmech",
        ref: core.statmech_ref,
        review: core.review.status,
        values: [
            wordOrCode("Treatment", core.statmech_treatment, TREATMENT_WORDS),
            wordOrCode("Rigid-rotor kind", core.rigid_rotor_kind, ROTOR_WORDS),
            pointGroupValue(core.point_group),
            count("External symmetry number", core.external_symmetry),
            count("Optical isomers", core.optical_isomers),
            yesNo("Linear molecule", core.is_linear),
            yesNo("Projected frequencies used", core.uses_projected_frequencies),
            quantity("Rotational constant A", "statmech_rotational_constant_cm1", core.rotational_constant_a_cm1),
            quantity("Rotational constant B", "statmech_rotational_constant_cm1", core.rotational_constant_b_cm1),
            quantity("Rotational constant C", "statmech_rotational_constant_cm1", core.rotational_constant_c_cm1),
            quantity("Frequency scale factor", "statmech_frequency_scale_factor", core.frequency_scale_factor_value),
        ],
        levels: shown,
        levelKey: levelKeyOf(shown),
        programs: programsOf(softwareLabel(record.software_release), toolReleaseLabel(record.workflow_tool_release)),
        describes: null,
        conformerGroupRef: null,
    }
}

/** The levels of a transport record's own source calculations, each under the role it played. */
function transportLevels(record: TransportRecord): SourceLevel[] {
    const out: SourceLevel[] = []
    for (const calculation of record.source_calculations ?? []) {
        const level: LevelOfTheory | null | undefined = calculation.level_of_theory
        if (!level) continue
        const next = { role: words(calculation.role) ?? calculation.role, label: lotLabel(level) }
        if (!out.some((existing) => existing.role === next.role && existing.label === next.label)) out.push(next)
    }
    return out
}

export function transportRow(record: TransportRecord): NumberRow {
    const core = record.transport
    const shown = transportLevels(record)
    const calculations = record.source_calculations ?? []
    return {
        kind: "transport",
        ref: core.transport_ref,
        review: core.review.status,
        values: [
            quantity("Collision diameter", "transport_sigma_angstrom", core.sigma_angstrom),
            quantity("Well depth over k", "transport_epsilon_over_k_k", core.epsilon_over_k_k),
            quantity("Dipole moment", "transport_dipole_debye", core.dipole_debye),
            quantity("Polarizability", "transport_polarizability_angstrom3", core.polarizability_angstrom3),
            quantity("Rotational relaxation", "transport_rotational_relaxation", core.rotational_relaxation),
        ],
        levels: shown,
        levelKey: levelKeyOf(shown),
        programs: programsOf(
            softwareLabel(record.software_release),
            toolReleaseLabel(record.workflow_tool_release),
            ...calculations.map((calculation) => softwareLabel(calculation.software_release)),
        ),
        describes: null,
        conformerGroupRef: null,
    }
}

/** Rows grouped by `levelKey`: groups in first-seen order, rows inside a group in their given order. */
export function groupByLevel<R extends NumberRow>(rows: readonly R[]): { key: string; rows: R[] }[] {
    const groups = new Map<string, R[]>()
    for (const row of rows) groups.set(row.levelKey, [...(groups.get(row.levelKey) ?? []), row])
    return [...groups].map(([key, members]) => ({ key, rows: members }))
}

/** How many distinct level keys the rows carry; the toggle is offered only when there are two or more. */
export function distinctLevelCount(rows: readonly NumberRow[]): number {
    return new Set(rows.map((row) => row.levelKey)).size
}

const KIND_NAMES: Record<NumberKind, string> = { thermo: "Thermochemistry", statmech: "Statistical mechanics", transport: "Transport" }

/**
 * The order the lists were served in, from the archive's own echo. Only lists with rows are named; identical orders
 * are said once, and a list that stated none says so.
 */
export function numbersOrderSentence(lists: { kind: NumberKind; sort: OrderEcho | null }[]): string {
    if (lists.length === 0) return ""
    if (lists.length === 1) return `${KIND_NAMES[lists[0].kind]}: ${orderWords(lists[0].sort) ?? "the archive did not state an order"}.`
    const named = lists.map((list) => ({ name: KIND_NAMES[list.kind], words: orderWords(list.sort) }))
    const first = named[0].words
    const together = named.map((list) => list.name)
    const sequence = together.length === 1
        ? together[0]
        : `${together[0]}, then ${together.slice(1).map((name) => name.toLowerCase()).join(", then ")}`
    if (first && named.every((list) => list.words === first)) return `${sequence}; ${first}.`
    return `${sequence}. ${named.map((list) => `${list.name}: ${list.words ?? "the archive did not state an order"}.`).join(" ")}`
}
