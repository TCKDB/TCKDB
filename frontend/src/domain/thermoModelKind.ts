/** TCKDB's wording for the backend `ThermoModelKind` codes. */
export const THERMO_MODEL_KIND_WORDS: Readonly<Record<string, string>> = {
    nasa7: "NASA 7-coefficient polynomial",
    nasa9: "NASA 9-coefficient polynomial",
    wilhoit: "Wilhoit polynomial",
    tabulated: "Tabulated values",
    scalar: "Single values",
}

/** The wording for a model kind; a code with no wording is spelled out from the code. */
export function thermoModelKindLabel(kind: string): string {
    return Object.hasOwn(THERMO_MODEL_KIND_WORDS, kind) ? THERMO_MODEL_KIND_WORDS[kind] : kind.replaceAll("_", " ")
}
