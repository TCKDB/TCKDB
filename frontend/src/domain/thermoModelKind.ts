/** TCKDB's wording for the thermo model kinds the read API sends (`nasa` is a 7-coefficient polynomial, `points` is tabulated). */
export const THERMO_MODEL_KIND_WORDS: Readonly<Record<string, string>> = {
    nasa: "NASA 7-coefficient polynomial",
    nasa9: "NASA 9-coefficient polynomial",
    wilhoit: "Wilhoit polynomial",
    points: "Tabulated values",
    scalar: "Single values",
}

/** The wording for a model kind; a code with no wording is spelled out from the code. */
export function thermoModelKindLabel(kind: string): string {
    return Object.hasOwn(THERMO_MODEL_KIND_WORDS, kind) ? THERMO_MODEL_KIND_WORDS[kind] : kind.replaceAll("_", " ")
}
