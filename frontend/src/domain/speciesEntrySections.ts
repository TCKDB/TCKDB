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

// Stale `:section` segments from the earlier chapter-nav design that this
// app still recognises and canonicalizes to `DEFAULT_SECTION`, rather than
// treating as a genuine 404. The ONE place this set is named -- `App.tsx`'s
// `SpeciesEntrySectionRoute` (which alias reaches `SpeciesEntryPage` at all
// vs. gets the not-found page) and `SpeciesEntryPage`'s own canonicalisation
// effect (which alias gets redirected to `DEFAULT_SECTION`) both read it
// from here instead of each spelling out `"calculations"` on its own --
// two copies of the same literal that would silently drift the moment a
// second legacy alias was added to only one of them.
export const LEGACY_ENTRY_SECTION_ALIASES: ReadonlySet<string> = new Set(["calculations"])
