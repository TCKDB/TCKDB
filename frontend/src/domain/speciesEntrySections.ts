export const sectionLabels = {
    conformers: "Conformers",
    calculations: "Calculations",
    thermo: "Thermochemistry",
    statmech: "Statistical mechanics",
    transport: "Transport",
} as const

export type EntrySection = "overview" | keyof typeof sectionLabels

export function isEntrySection(section: string | undefined): section is keyof typeof sectionLabels {
    return section !== undefined && Object.hasOwn(sectionLabels, section)
}
