/**
 * `calculation.type` / `results.kind` -> human label. Shared by
 * `CalculationDetailPage.tsx` (kicker, title, headline) and
 * `CalculationDependencyGraph.tsx` (the centre node's type pill -- the
 * only node in the dependency graph with a known type; see that
 * component's own docstring) so the two surfaces can never drift onto
 * two different words for the same `type` token.
 */
export const CALC_TYPE_LABELS: Record<string, string> = {
    opt: "Optimisation",
    freq: "Frequency",
    sp: "Single-point",
    irc: "IRC",
    scan: "Scan",
    path_search: "Path search",
    conf: "Conformer",
}

export const typeLabel = (type: string) => CALC_TYPE_LABELS[type] ?? type.replaceAll("_", " ")

/** What a calculation did, for a line like "Source calc_x · frequency". Same words as `CALC_TYPE_LABELS`, lower case where it is a common noun. */
const CALC_TYPE_DID: Readonly<Record<string, string>> = {
    opt: "optimisation",
    freq: "frequency",
    sp: "single-point",
    irc: "IRC",
    scan: "scan",
    path_search: "path search",
    conf: "conformer",
    composite: "composite",
}

/** The wording for a calculation type code, or null for a code this build does not know. */
export function calculationDid(type: string): string | null {
    return Object.hasOwn(CALC_TYPE_DID, type) ? CALC_TYPE_DID[type] : null
}
