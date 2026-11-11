/** The kind each record route puts in its title, in the loading and missing states and again once loaded. */
export const ROUTE_TITLE_KINDS = {
    "/species/:ref": "Molecule",
    "/species/:ref/compare": "Compare",
    "/species-entries/:ref": "Molecule",
    "/conformer-groups/:ref": "Conformer",
    "/conformer-observations/:ref": "Conformer observation",
    "/calculations/:ref": "Calculation",
    "/geometries/:ref": "Geometry",
    "/transition-state-entries/:ref": "Transition state",
    "/reaction-entries/:ref": "Reaction entry",
    "/reactions/:ref": "Reaction",
    "/networks/:ref": "Pressure-dependent network",
    "/methods/:ref": "Level of theory",
    "/methods/schemes/:ref": "Energy-correction scheme",
    "/methods/composite-schemes/:ref": "Composite scheme",
    "/methods/frequency-scale-factors/:ref": "Frequency scale factor",
} as const

export type RouteTitleKindKey = keyof typeof ROUTE_TITLE_KINDS

/** A loaded title is "<kind> · TCKDB" or "<name> · <kind> · TCKDB". */
export function titleHasKind(title: string, key: RouteTitleKindKey): boolean {
    const suffix = ` · ${ROUTE_TITLE_KINDS[key]} · TCKDB`
    return title === `${ROUTE_TITLE_KINDS[key]} · TCKDB` || title.endsWith(suffix)
}
