import { lotLabel } from "../api/scientificSchemas"
import type { OrderEcho } from "../api/listEnvelope"
import type { StatmechRecord } from "../api/statmechApi"
import type { ThermoRecord } from "../api/thermoApi"
import type { TransportRecord } from "../api/transportApi"
import type { SourceLevel } from "../components/ValueSource"
import { displayOrderSentence } from "./paging"
import { fixed } from "./quantityFormat"
import { resolveProductLevels, type ProductLevels } from "./productLevels"
import { softwareLabel, toolReleaseLabel, words } from "./provenanceFormat"

export type NumberKind = "thermo" | "statmech" | "transport"

export type NumberValue = { label: string; value: string | null; unit: string | null }

/** What a thermo record says it describes, from `thermodynamic_target` only. */
export type Describes = { kind: "all" } | { kind: "one"; groupRef: string } | { kind: "unstated" }

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
}

export const NO_LEVEL_KEY = "No level of theory recorded"

/** Records read per list: pages of this size, at most this many pages. */
export const PAGE_LIMIT = 200
export const MAX_PAGES = 5

/** The id a row carries, so a link from this or another page can name one record. */
export function numberAnchor(kind: NumberKind, ref: string): string {
    return `${kind}-${ref}`
}

function number(label: string, raw: number | null | undefined, digits: number, unit: string | null, spread?: number | null): NumberValue {
    const main = fixed(raw, digits, null)
    if (!main) return { label, value: null, unit }
    const plus = fixed(spread, digits, null)
    return { label, value: main.value.replace("-", "−") + (plus ? ` ± ${plus.value}` : ""), unit }
}

function levelsOf(levels: ProductLevels): SourceLevel[] {
    const out: SourceLevel[] = []
    if (levels.geometry) out.push({ role: "geometry", label: lotLabel(levels.geometry) })
    if (levels.frequency) out.push({ role: "frequency", label: lotLabel(levels.frequency) })
    if (levels.energy) out.push({ role: "energy", label: lotLabel(levels.energy) })
    return out
}

function levelKeyOf(levels: SourceLevel[]): string {
    return levels.length === 0 ? NO_LEVEL_KEY : levels.map((level) => `${level.role} ${level.label}`).join(" · ")
}

function programsOf(...labels: (string | null | undefined)[]): string[] {
    return [...new Set(labels.filter((label): label is string => Boolean(label)))]
}

export function describesOf(record: ThermoRecord): Describes {
    const target = record.thermodynamic_target
    if (!target) return { kind: "unstated" }
    if (target.kind === "single_conformer" && target.conformer_group_ref) return { kind: "one", groupRef: target.conformer_group_ref }
    if (target.kind === "equilibrium_ensemble") return { kind: "all" }
    return { kind: "unstated" }
}

/** A thermo record's own levels: its `levels`, else its source calculations, else its single provenance level. */
export function thermoRecordProductLevels(record: ThermoRecord): ProductLevels {
    if (record.levels) return resolveProductLevels(record.levels, null)
    if (record.source_calculations && record.source_calculations.length > 0) {
        return resolveProductLevels(null, record.source_calculations)
    }
    const lot = record.provenance?.level_of_theory ?? null
    return { geometry: lot, frequency: lot, energy: lot, energy_source: lot ? "opt" : null }
}

export function thermoRow(record: ThermoRecord, levels: ProductLevels): NumberRow {
    const shown = levelsOf(levels)
    const formation = record.enthalpy_reference_kind === "formation_298k"
    const provenance = record.provenance
    return {
        kind: "thermo",
        ref: record.thermo_ref,
        review: record.review.status,
        values: [
            number(formation ? "Enthalpy of formation, 298 K" : "Enthalpy, 298 K", record.h298_kj_mol, 2, "kJ/mol", record.h298_uncertainty_kj_mol),
            number("Entropy, 298 K", record.s298_j_mol_k, 2, "J/mol·K", record.s298_uncertainty_j_mol_k),
            number("Enthalpy of formation, 0 K", record.enthalpy_formation_0k_kj_mol, 2, "kJ/mol", record.enthalpy_formation_0k_uncertainty_kj_mol),
        ],
        levels: shown,
        levelKey: levelKeyOf(shown),
        programs: programsOf(
            softwareLabel(provenance?.primary_calculation?.software),
            softwareLabel(provenance?.software_release),
            toolReleaseLabel(provenance?.workflow_tool_release),
        ),
        describes: describesOf(record),
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
            { label: "Treatment", value: words(core.statmech_treatment), unit: null },
            { label: "Point group", value: core.point_group ?? null, unit: null },
            number("External symmetry number", core.external_symmetry, 0, null),
            number("Optical isomers", core.optical_isomers, 0, null),
            number("Rotational constant A", core.rotational_constant_a_cm1, 4, "cm⁻¹"),
            number("Rotational constant B", core.rotational_constant_b_cm1, 4, "cm⁻¹"),
            number("Rotational constant C", core.rotational_constant_c_cm1, 4, "cm⁻¹"),
            number("Frequency scale factor", core.frequency_scale_factor_value, 4, null),
        ],
        levels: shown,
        levelKey: levelKeyOf(shown),
        programs: programsOf(softwareLabel(record.software_release), toolReleaseLabel(record.workflow_tool_release)),
        describes: null,
    }
}

export function transportRow(record: TransportRecord): NumberRow {
    const core = record.transport
    return {
        kind: "transport",
        ref: core.transport_ref,
        review: core.review.status,
        values: [
            number("Collision diameter", core.sigma_angstrom, 3, "Å"),
            number("Well depth over k", core.epsilon_over_k_k, 1, "K"),
            number("Dipole moment", core.dipole_debye, 3, "D"),
            number("Polarizability", core.polarizability_angstrom3, 3, "Å³"),
            number("Rotational relaxation", core.rotational_relaxation, 2, null),
        ],
        levels: [],
        levelKey: NO_LEVEL_KEY,
        programs: programsOf(softwareLabel(record.software_release), toolReleaseLabel(record.workflow_tool_release)),
        describes: null,
    }
}

/** Rows grouped by `levelKey`: groups in first-seen order, rows inside a group in their given order. */
export function groupByLevel(rows: readonly NumberRow[]): { key: string; rows: NumberRow[] }[] {
    const groups = new Map<string, NumberRow[]>()
    for (const row of rows) groups.set(row.levelKey, [...(groups.get(row.levelKey) ?? []), row])
    return [...groups].map(([key, members]) => ({ key, rows: members }))
}

/** The order each list was served in, from the archive's own echo; a list that stated none says so. */
export function numbersOrderSentence(orders: { thermo: OrderEcho | null; statmech: OrderEcho | null; transport: OrderEcho | null }): string {
    const part = (name: string, sort: OrderEcho | null) => {
        const sentence = displayOrderSentence(sort)
        if (!sentence) return `${name}: the archive did not state an order.`
        return `${name}: ${sentence.replace(/^Display order: /, "").replace(/\. An order for browsing.*$/, ".")}`
    }
    return `Thermochemistry first, then statistical mechanics, then transport. ${part("Thermochemistry", orders.thermo)} ${part("Statistical mechanics", orders.statmech)} ${part("Transport", orders.transport)}`
}
