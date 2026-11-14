import type { CalculationDependency } from "../api/calculationApi"
import { roleLabel } from "./dependencyWording"
import { ledeText, ledeWord, type LedeText } from "./ledeText"

/** What a calculation is, in words, for the page title. */
const TITLE_WORDS: Readonly<Record<string, string>> = {
    opt: "Optimisation",
    freq: "Frequency calculation",
    sp: "Single-point energy",
    irc: "IRC",
    scan: "Scan",
    path_search: "Path search",
    composite: "Composite energy",
    conf: "Conformer search",
}

/** The title words for a calculation type, or the code with spaces for a type this build has no wording for. */
export function calculationTitleWords(type: string): string {
    return Object.hasOwn(TITLE_WORDS, type) ? TITLE_WORDS[type] : type.replaceAll("_", " ")
}

/** The one sentence under the title, by calculation type. */
const LEDE_WORDS: Readonly<Record<string, string>> = {
    opt: "One geometry search at one level of theory.",
    freq: "Vibrational frequencies computed on one geometry.",
    sp: "One energy computed on one geometry.",
    irc: "One path followed downhill from a saddle point.",
    scan: "Energies along one scanned coordinate.",
    path_search: "One search for a reaction path.",
    composite: "One energy assembled from a composite recipe.",
    conf: "One conformer search.",
}

export function calculationLede(type: string): LedeText {
    return ledeText`${ledeWord(Object.hasOwn(LEDE_WORDS, type) ? type : "other", { ...LEDE_WORDS, other: "One deposited calculation." })}`
}

/** What the calculation on the other end of an edge is, read from the edge's role. */
const PARENT_WORDS: Readonly<Record<string, string>> = {
    optimized_from: "Starting geometry",
    freq_on: "Optimisation",
    single_point_on: "Optimisation",
    irc_start: "Optimisation",
    scan_parent: "Optimisation",
    irc_followup: "IRC",
    composite_input: "Energy input",
    arkane_source: "Arkane source",
}

const CHILD_WORDS: Readonly<Record<string, string>> = {
    optimized_from: "Optimisation",
    freq_on: "Frequency calculation",
    single_point_on: "Single-point energy",
    irc_start: "IRC",
    irc_followup: "IRC follow-up",
    scan_parent: "Scan",
    composite_input: "Composite energy",
    arkane_source: "Arkane run",
}

/** Upstream first: the order the calculations ran in. */
const ROLE_ORDER = [
    "optimized_from", "scan_parent", "freq_on", "single_point_on", "irc_start", "irc_followup", "composite_input", "arkane_source",
]

export interface LineageStep {
    ref: string
    role: string
    words: string
}

function orderOf(role: string): number {
    const index = ROLE_ORDER.indexOf(role)
    return index === -1 ? ROLE_ORDER.length : index
}

function steps(
    edges: readonly CalculationDependency[],
    pick: (edge: CalculationDependency) => string,
    words: Readonly<Record<string, string>>,
): LineageStep[] {
    return edges
        .map((edge) => ({
            ref: pick(edge),
            role: edge.role,
            words: Object.hasOwn(words, edge.role) ? words[edge.role] : roleLabel(edge.role),
        }))
        .sort((a, b) => orderOf(a.role) - orderOf(b.role) || a.ref.localeCompare(b.ref))
}

/** The calculations this one depends on, in the order they ran. */
export function dependsOn(dependencies: readonly CalculationDependency[]): LineageStep[] {
    return steps(dependencies.filter((edge) => edge.direction === "child"), (edge) => edge.parent_calculation_ref, PARENT_WORDS)
}

/** The calculations that depend on this one. */
export function usedBy(dependencies: readonly CalculationDependency[]): LineageStep[] {
    return steps(dependencies.filter((edge) => edge.direction === "parent"), (edge) => edge.child_calculation_ref, CHILD_WORDS)
}
