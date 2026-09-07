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
