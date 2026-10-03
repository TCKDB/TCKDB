import type { CompositeSchemeTerm, CompositeSchemeTermInput } from "../api/methodsApi"
import { lotLabel } from "../api/scientificSchemas"
import { words } from "./provenanceFormat"

// ---------------------------------------------------------------------------
// Wording and arithmetic display for a composite recipe (ADR 0021, P7a/P7b).
// The server derives each input's `coefficient` and each term's `linearity`;
// nothing here recomputes a weight. This module only SHOWS them: a number as
// an exact fraction where one exists, and a term as the sum it stands for.
// A non-linear term has no coefficients and is never given one.
// ---------------------------------------------------------------------------

/** The cardinal-number shorthand a chemist writes for a correlation-consistent
 *  basis (`3` -> `TZ`). Anything outside 2 to 6 has no standard letter pair and
 *  is shown as the number itself. */
const CARDINAL_LETTERS: Record<number, string> = { 2: "DZ", 3: "TZ", 4: "QZ", 5: "5Z", 6: "6Z" }

export function cardinalLabel(cardinal: number | null | undefined): string | null {
    if (cardinal === null || cardinal === undefined) return null
    return CARDINAL_LETTERS[cardinal] ?? `X=${cardinal}`
}

/** Best exact fraction for a closed-form weight (`-27/37`), or `null` when none
 *  with a denominator up to `maxDenominator` matches to 1e-9. Floats come off the
 *  wire, so an exact rational is recognised, never assumed. */
export function asFraction(value: number, maxDenominator = 1000): { numerator: number; denominator: number } | null {
    if (!Number.isFinite(value)) return null
    for (let denominator = 1; denominator <= maxDenominator; denominator += 1) {
        const numerator = Math.round(value * denominator)
        if (Math.abs(numerator / denominator - value) < 1e-9) return { numerator, denominator }
    }
    return null
}

const SIGN_MINUS = "−"

/** A coefficient as it should read: `+1`, `-1`, or `-27/37 (-0.729730)`; the
 *  fraction only where one matches exactly, and a signed decimal otherwise. */
export function formatCoefficient(value: number): string {
    const fraction = asFraction(value)
    const decimal = value.toFixed(6)
    if (fraction && fraction.denominator === 1) return fraction.numerator > 0 ? `+${fraction.numerator}` : `${fraction.numerator}`
    if (fraction) return `${fraction.numerator}/${fraction.denominator} (${decimal})`
    return value > 0 ? `+${decimal}` : decimal
}

function energyOf(input: CompositeSchemeTermInput): string {
    const cardinal = cardinalLabel(input.cardinal_number)
    return `E(${cardinal ?? lotLabel(input.level_of_theory)})`
}

function weightText(value: number): string {
    const fraction = asFraction(value)
    if (fraction) {
        const magnitude = Math.abs(fraction.numerator)
        return fraction.denominator === 1 ? `${magnitude}` : `${magnitude}/${fraction.denominator}`
    }
    return Math.abs(value).toFixed(6)
}

/** The sum a LINEAR term stands for, e.g. `-27/37 E(TZ) + 64/37 E(QZ)`; `null`
 *  for a term that is not linear or has an input with no coefficient (nothing
 *  is composed from a weight that was not given). */
export function termExpression(term: Pick<CompositeSchemeTerm, "linearity" | "inputs">): string | null {
    if (term.linearity !== "linear") return null
    if (term.inputs.length === 0) return null
    const parts: string[] = []
    for (const input of term.inputs) {
        if (input.coefficient === null || input.coefficient === undefined) return null
        const negative = input.coefficient < 0
        const weight = Math.abs(input.coefficient) === 1 ? "" : `${weightText(input.coefficient)} `
        const piece = `${weight}${energyOf(input)}`
        parts.push(parts.length === 0 ? `${negative ? SIGN_MINUS : ""}${piece}` : `${negative ? SIGN_MINUS : "+"} ${piece}`)
    }
    return parts.join(" ")
}

const FORMULA_WORDS: Record<string, string> = {
    inverse_power: "inverse power",
    inverse_power_shifted_half: "inverse power, shifted by one half",
    karton_martin_scf: "Karton-Martin (SCF)",
    exponential_three_point: "three-point exponential",
}

export function formulaLabel(formula: string | null | undefined): string | null {
    if (!formula) return null
    return FORMULA_WORDS[formula] ?? words(formula)
}

/** What a term's linearity means for a reader, in one line. A non-linear term
 *  says so and names its formula; nothing implies a weight exists. */
export function linearityText(term: Pick<CompositeSchemeTerm, "linearity" | "formula">): string {
    switch (term.linearity) {
        case "linear":
            return "linear: each input has a fixed weight"
        case "nonlinear": {
            const formula = formulaLabel(term.formula)
            return formula ? `non-linear (${formula}), no fixed coefficients` : "non-linear, no fixed coefficients"
        }
        case "not_applicable":
            return "not computed from inputs"
        default:
            return words(term.linearity) ?? "not recorded"
    }
}

/** The one-line summary for `linear_in_energies`: `true`, `false`, or `null`
 *  (a recipe with no stated terms, where nothing can be said). */
export function linearSummary(linear: boolean | null | undefined): string {
    if (linear === true) return "The total is a fixed weighted sum of the input energies."
    if (linear === false) return "The total is not a fixed weighted sum of the input energies: at least one term is non-linear."
    return "This recipe states no terms, so nothing can be said about how its total is built."
}

const OPERATION_WORDS: Record<string, string> = {
    base: "base value",
    extrapolation: "extrapolation to the basis-set limit",
    difference: "difference of two levels",
    value: "single value",
    empirical: "empirical term",
}

export function operationLabel(operation: string): string {
    return OPERATION_WORDS[operation] ?? words(operation) ?? operation
}

const COMPONENT_WORDS: Record<string, string> = {
    total: "total energy",
    reference: "reference (SCF) energy",
    correlation: "correlation energy",
    triples: "perturbative triples",
    dboc: "diagonal Born-Oppenheimer correction",
    scalar_relativistic: "scalar relativistic correction",
    correlation_excluding_triples: "correlation energy without the perturbative triples",
}

export function componentLabel(component: string): string {
    return COMPONENT_WORDS[component] ?? words(component) ?? component
}

const SLOT_WORDS: Record<string, string> = {
    value: "value",
    high: "high (larger basis)",
    low: "low (smaller basis)",
    cardinal: "cardinal",
}

export function slotLabel(slot: string): string {
    return SLOT_WORDS[slot] ?? words(slot) ?? slot
}

export function bindingSourceLabel(source: string): string {
    switch (source) {
        case "named_method_catalogue":
            return "named in the method catalogue"
        case "declared":
            return "declared by a depositor"
        default:
            return words(source) ?? source
    }
}
