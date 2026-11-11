import type {
    EnergyProfileCitedCalculation,
    EnergyProfileRead,
    EnergyProfileRecord,
} from "../api/reactionEnergyProfileApi"
import { lotLabel } from "../api/scientificSchemas"
import { hiddenByDefaultNote } from "./paging"

const MINUS = "−"

/** Why a transition-state entry has no profile, in TCKDB's own words. A code with no wording is shown as the code. */
export const UNAVAILABLE_WORDS: Readonly<Record<string, string>> = {
    no_reactant_energy_at_level: "Not every reactant has an energy at this level of theory from this program.",
    no_product_energy_at_level: "Not every product has an energy at this level of theory from this program.",
    points_from_different_programs: "The energies come from different programs, so they are not combined.",
    no_ts_energy: "This transition state has no single-point or optimisation energy that can be used.",
    no_program_recorded: "The only energies come from calculations with no recorded program, so no program can be named.",
}

const ENERGY_TYPE_WORDS: Readonly<Record<string, string>> = {
    single_point: "single-point energy",
    optimization: "optimisation final energy",
}

const ROLE_WORDS: Readonly<Record<string, string>> = {
    reactant_energy: "Reactant energy",
    product_energy: "Product energy",
    ts_energy: "Transition-state energy",
}

/** The kind of energy in words; a kind this build does not know is spelled out from its code. */
export function energyTypeWord(code: string | null | undefined): string {
    if (!code) return "not recorded"
    return ENERGY_TYPE_WORDS[code] ?? code.replaceAll("_", " ")
}

export function roleWord(code: string): string {
    return ROLE_WORDS[code] ?? code.replaceAll("_", " ")
}

/** A program with its version, or the program alone with "version not recorded". */
export function programText(software: { program: string; version?: string | null } | null | undefined): string {
    if (!software) return "not recorded"
    return software.version ? `${software.program} ${software.version}` : `${software.program} (version not recorded)`
}

/** Hartree as stored to 6 decimals, with a true minus sign. */
export function hartreeText(value: number): string {
    return value.toFixed(6).replace("-", MINUS)
}

/** kJ/mol relative to the reactants: one decimal, always signed except zero. */
export function relativeKjText(value: number): string {
    const rounded = Number(value.toFixed(1))
    if (rounded === 0) return "0.0"
    return `${rounded > 0 ? "+" : MINUS}${Math.abs(rounded).toFixed(1)}`
}

/** One profile's choice in the picker: the transition-state entry only when there is more than one, then level and program. */
export function profileLabel(profile: EnergyProfileRecord, withEntry: boolean): string {
    return `${withEntry ? `${profile.transition_state_entry_ref}, ` : ""}${lotLabel(profile.level_of_theory)}, ${profile.program}`
}

/** Picker labels for every profile; two profiles that would read the same get their level ref added. */
export function profileLabels(profiles: readonly EnergyProfileRecord[]): string[] {
    const withEntry = new Set(profiles.map((profile) => profile.transition_state_entry_ref)).size > 1
    const labels = profiles.map((profile) => profileLabel(profile, withEntry))
    return labels.map((label, index) => labels.filter((other) => other === label).length > 1
        ? `${label} [${profiles[index].level_of_theory.level_of_theory_ref ?? "level not named"}]`
        : label)
}

export const MIN_SPAN_KJ_MOL = 50

export type ProfileScale = { min: number; max: number; ticks: number[] }

function niceStep(raw: number): number {
    const power = 10 ** Math.floor(Math.log10(raw))
    const fraction = raw / power
    return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power
}

/**
 * A linear axis for one profile: at least MIN_SPAN_KJ_MOL tall, so a small difference is not drawn as a big one,
 * padded out to round tick values (at most five).
 */
export function profileScale(values: readonly number[]): ProfileScale {
    const low = Math.min(...values)
    const high = Math.max(...values)
    const span = Math.max(high - low, MIN_SPAN_KJ_MOL)
    const extra = (span - (high - low)) / 2
    const step = niceStep(span / 4)
    const min = Math.floor((low - extra - span * 0.05) / step) * step
    const max = Math.ceil((high + extra + span * 0.05) / step) * step
    const ticks: number[] = []
    for (let tick = min; tick <= max + step / 1000; tick += step) ticks.push(Number(tick.toFixed(6)))
    return { min, max, ticks }
}

export function hasSeveralTransitionStates(read: EnergyProfileRead): boolean {
    return new Set(read.profiles.map((profile) => profile.transition_state_entry_ref)).size > 1
}

/** True when the profile's points differ in the kind of energy or in program version. */
export function isMixed(profile: EnergyProfileRecord): boolean {
    return profile.energy_types.length > 1 || profile.versions.length > 1
}

/** The profile's points as drawn: three levels, each with its kJ/mol relative to the reactants. */
export function levelsOf(profile: EnergyProfileRecord): { key: "reactants" | "transition_state" | "products"; label: string; kj: number }[] {
    return [
        { key: "reactants", label: "Reactants", kj: profile.relative_kj_mol.reactants },
        { key: "transition_state", label: "Transition state", kj: profile.relative_kj_mol.transition_state },
        { key: "products", label: "Products", kj: profile.relative_kj_mol.products },
    ]
}

const SCOPE_NOUNS: Readonly<Record<string, [string, string]>> = {
    transition_state_entries: ["transition-state entries", "transition-state entry"],
    kinetics_records: ["kinetics records", "kinetics record"],
}

/** Notes for what the default view left out, split by kind when the read says how. */
export function hiddenNotes(read: EnergyProfileRead): string[] {
    const byScope = read.request.hidden_by_default_by_scope
    const notes = Object.entries(byScope).flatMap(([scope, counts]) => {
        const [plural, singular] = SCOPE_NOUNS[scope] ?? [scope.replaceAll("_", " "), scope.replaceAll("_", " ")]
        const note = hiddenByDefaultNote({ counts }, plural, singular)
        return note ? [note] : []
    })
    if (notes.length > 0) return notes
    const total = hiddenByDefaultNote(read.request.hidden_by_default, "records", "record")
    return total ? [total] : []
}

/** A cited calculation's energy line parts: what it is the energy of, by the refs the read carries. */
export function citedSubjectRef(calculation: EnergyProfileCitedCalculation): string | null {
    return calculation.species_entry_ref ?? calculation.transition_state_entry_ref ?? null
}
