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

const lookup = (table: Readonly<Record<string, string>>, code: string): string | null => (Object.hasOwn(table, code) ? table[code] : null)

export const modelKindWord = (code: string) => lookup(MODEL_KIND_WORDS, code)
export const originWord = (code: string) => lookup(ORIGIN_WORDS, code)
export const tunnellingWord = (code: string) => lookup(TUNNELLING_WORDS, code)
