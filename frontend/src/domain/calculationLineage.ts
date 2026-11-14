import type { CalculationDependency } from "../api/calculationApi"
import type { CalculationTypes } from "../api/calculationNeighboursApi"
import { roleLabel } from "./dependencyWording"
import { ledeText, ledeWord, type LedeText } from "./ledeText"
import { OPTIMISATION_STAGE_WORDS } from "./optimisationStage"

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
    other: "One deposited calculation.",
}

export function calculationLede(type: string): LedeText {
    return ledeText`${ledeWord(Object.hasOwn(LEDE_WORDS, type) ? type : "other", LEDE_WORDS)}`
}

/** What the edge says about the other calculation when its type is not known: no type is claimed. */
const PARENT_FALLBACK: Readonly<Record<string, string>> = {
    optimized_from: "Starting geometry from",
    freq_on: "Geometry from",
    single_point_on: "Geometry from",
    irc_start: "Geometry from",
    scan_parent: "Geometry from",
    irc_followup: "Continues",
    composite_input: "Energy input from",
    arkane_source: "Arkane source",
}

const CHILD_FALLBACK: Readonly<Record<string, string>> = {
    optimized_from: "Started from this",
    freq_on: "Ran on this",
    single_point_on: "Ran on this",
    irc_start: "Ran on this",
    scan_parent: "Ran on this",
    irc_followup: "Continued by",
    composite_input: "Used by",
    arkane_source: "Used by Arkane",
}

export interface LineageStep {
    ref: string
    role: string
    /** The calculation's real type in words once the read has said it, else wording that claims no type. */
    words: string
}

type Side = "parent" | "child"

function stepWords(role: string, side: Side, type: string | undefined): string {
    if (type === undefined) {
        const table = side === "parent" ? PARENT_FALLBACK : CHILD_FALLBACK
        return Object.hasOwn(table, role) ? table[role] : roleLabel(role)
    }
    // An optimisation on the earlier end of an optimized_from edge is the coarse pass, on the later end the fine pass.
    if (role === "optimized_from" && type === "opt") return OPTIMISATION_STAGE_WORDS[side === "parent" ? "coarse" : "fine"].label
    return calculationTitleWords(type)
}

function steps(edges: readonly CalculationDependency[], side: Side, types: CalculationTypes | null): LineageStep[] {
    const seen = new Set<string>()
    const out: LineageStep[] = []
    for (const edge of edges) {
        const ref = side === "parent" ? edge.parent_calculation_ref : edge.child_calculation_ref
        const key = `${edge.role}\u0000${ref}`
        if (seen.has(key)) continue
        seen.add(key)
        out.push({ ref, role: edge.role, words: stepWords(edge.role, side, types?.[ref]) })
    }
    // By words (the real type once known), then ref; no sequence is implied between them.
    return out.sort((a, b) => a.words.localeCompare(b.words) || a.ref.localeCompare(b.ref))
}

/** The calculations this one depends on. */
export function dependsOn(dependencies: readonly CalculationDependency[], types: CalculationTypes | null = null): LineageStep[] {
    return steps(dependencies.filter((edge) => edge.direction === "child"), "parent", types)
}

/** The calculations that depend on this one. */
export function usedBy(dependencies: readonly CalculationDependency[], types: CalculationTypes | null = null): LineageStep[] {
    return steps(dependencies.filter((edge) => edge.direction === "parent"), "child", types)
}
