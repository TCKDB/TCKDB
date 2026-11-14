import { typeLabel } from "./calculationTypeFormat"

/** The record kinds a link can name, and the noun each is announced as. */
export const RECORD_KIND_NOUN = {
    species: "Molecule",
    "species-entry": "Species entry",
    reaction: "Reaction",
    "reaction-entry": "Reaction entry",
    kinetics: "Rate record",
    thermo: "Thermo record",
    statmech: "Statmech record",
    transport: "Transport record",
    calculation: "Calculation",
    "transition-state": "Transition state",
    "conformer-group": "Conformer group",
    "conformer-observation": "Conformer observation",
    geometry: "Geometry",
    "level-of-theory": "Level of theory",
    network: "Network",
    "network-kinetics": "Network rate record",
    scheme: "Scheme",
} as const

export type RecordKind = keyof typeof RECORD_KIND_NOUN

/** A public ref: a short lowercase prefix, an underscore and a long lowercase run. */
const PUBLIC_REF = /^[a-z]{2,5}_[a-z0-9]{16,}$/

export const looksLikeRef = (text: string): boolean => PUBLIC_REF.test(text)

/** "Version 2": the position of a reaction entry in the list the page shows (1-based). */
export const versionWords = (position: number): string => `Version ${position}`

/** "Record 2": the position of a rate record among the columns the page shows (1-based). */
export const recordWords = (position: number): string => `Record ${position}`

/** A calculation by what it did: "Single-point energy", "Optimisation". */
const CALCULATION_WORDS: Readonly<Record<string, string>> = {
    sp: "Single-point energy",
    opt: "Optimisation",
    freq: "Frequency calculation",
}

export function calculationWords(type: string | null | undefined): string {
    if (!type) return "Calculation"
    return CALCULATION_WORDS[type] ?? `${typeLabel(type)} calculation`
}

/** "Geometry, 12 atoms". */
export function geometryWords(atomCount: number | null | undefined): string {
    if (atomCount === null || atomCount === undefined) return "Geometry"
    return `Geometry, ${atomCount} ${atomCount === 1 ? "atom" : "atoms"}`
}

/** "Conformer group 2" by position, or "Conformer group" when no list gives one. */
export const conformerGroupWords = (position?: number): string => (position ? `Conformer group ${position}` : "Conformer group")

/** "Observation 2" by position, or "Conformer observation". */
export const observationWords = (position?: number): string => (position ? `Observation ${position}` : "Conformer observation")

/** "Transition state 2" by position, or "Transition state". */
export const transitionStateWords = (position?: number): string => (position ? `Transition state ${position}` : "Transition state")
