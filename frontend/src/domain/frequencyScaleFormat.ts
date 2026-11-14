const SCALE_KIND_LABELS: Readonly<Record<string, string>> = {
    fundamental: "fundamental",
    zpe: "ZPE",
    enthalpy: "enthalpy",
    entropy: "entropy",
    heat_capacity: "heat capacity",
}

/** The wording for a scale kind; a kind with no wording falls back to its token with underscores as spaces. */
export function scaleKindLabel(kind: string): string {
    return Object.hasOwn(SCALE_KIND_LABELS, kind) ? SCALE_KIND_LABELS[kind] : kind.replaceAll("_", " ")
}
