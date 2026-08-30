// Conformer-first tabs: chosen once a conformer is selected, each panel
// answers one chemistry question about it rather than exposing a generic
// "calculations" bucket of refs. "geometry" and "sp" both read off the
// conformer projection already loaded for the picker; "statmech"/"thermo"
// read their own entry-scoped lists and partition by the selected
// conformer; "transport" has no per-conformer link in the data model and is
// always entry-level.
export const sectionLabels = {
    geometry: "Geometry",
    sp: "Single-point energy",
    statmech: "Statistical mechanics",
    thermo: "Thermochemistry",
    transport: "Transport",
} as const

export type EntrySection = keyof typeof sectionLabels

export const DEFAULT_SECTION: EntrySection = "geometry"

export function isEntrySection(section: string | undefined): section is EntrySection {
    return section !== undefined && Object.hasOwn(sectionLabels, section)
}
