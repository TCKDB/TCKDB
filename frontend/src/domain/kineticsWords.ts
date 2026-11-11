/** TCKDB's wording for the codes a kinetics record carries. A code with no wording returns null; the page shows it as code. */
const MODEL_KIND_WORDS: Readonly<Record<string, string>> = {
    arrhenius: "Arrhenius",
    modified_arrhenius: "Modified Arrhenius",
    multi_arrhenius: "Sum of modified Arrhenius terms",
    lindemann: "Lindemann falloff",
    troe: "Troe falloff",
    sri: "SRI falloff",
    plog: "PLOG",
    chebyshev: "Chebyshev",
}

const ORIGIN_WORDS: Readonly<Record<string, string>> = {
    computed: "computed",
    experimental: "experimental",
    estimated: "estimated",
}

const TUNNELLING_WORDS: Readonly<Record<string, string>> = {
    none: "none",
    wigner: "Wigner",
    eckart: "Eckart",
    sct: "small-curvature",
    other: "other",
}

const DIRECTION_WORDS: Readonly<Record<string, string>> = { forward: "Forward", reverse: "Reverse", net: "Net" }

const PRESSURE_CONTEXT_WORDS: Readonly<Record<string, string>> = {
    high_p_limit: "High-pressure limit",
    apparent_at_pressure: "Apparent at a stated pressure",
    pressure_dependent: "Pressure dependent",
}

const DEGENERACY_WORDS: Readonly<Record<string, string>> = {
    already_applied: "Included in the reported k",
    not_applied: "Not included in the reported k",
    unknown: "Not stated",
}

const lookup = (table: Readonly<Record<string, string>>, code: string): string | null => (Object.hasOwn(table, code) ? table[code] : null)

export const modelKindWord = (code: string) => lookup(MODEL_KIND_WORDS, code)
export const originWord = (code: string) => lookup(ORIGIN_WORDS, code)
export const tunnellingWord = (code: string) => lookup(TUNNELLING_WORDS, code)

const A_UNIT_WORDS: Readonly<Record<string, string>> = {
    per_s: "s⁻¹",
    cm3_mol_s: "cm³ mol⁻¹ s⁻¹",
    cm3_molecule_s: "cm³ molecule⁻¹ s⁻¹",
    m3_mol_s: "m³ mol⁻¹ s⁻¹",
    cm6_mol2_s: "cm⁶ mol⁻² s⁻¹",
    cm6_molecule2_s: "cm⁶ molecule⁻² s⁻¹",
    m6_mol2_s: "m⁶ mol⁻² s⁻¹",
}

/** The typeset unit of a record's A (and of its k); a code with no wording is spelled out with spaces. */
export function aUnitsWord(code: string | null | undefined): string {
    if (!code) return ""
    return lookup(A_UNIT_WORDS, code) ?? code.replaceAll("_", " ")
}

export const directionWord = (code: string) => lookup(DIRECTION_WORDS, code)
export const pressureContextWord = (code: string) => lookup(PRESSURE_CONTEXT_WORDS, code)
export const degeneracyWord = (code: string) => lookup(DEGENERACY_WORDS, code)
