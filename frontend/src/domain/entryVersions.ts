import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import { chargeText, spinDisplay } from "./chemistryFormat"
import { facetChips } from "./recordFacets"
import type { LedeText } from "./ledeText"
import { ledeText, ledeWord } from "./ledeText"

/** What a version is, built from codes only: charge, spin, then electronic state, stereochemistry and isotopes. */
export function versionLabel(molecule: { charge: number; multiplicity: number }, version: ScientificSpeciesEntrySummary): string {
    return `charge ${chargeText(molecule.charge) ?? "not recorded"} · ${spinDisplay(molecule.multiplicity)} · ${facetChips(version).join(" · ")}`
}

/** What a version holds: its calculation count and the kinds of result deposited. */
export function versionHolds(entry: ScientificSpeciesEntrySummary): string {
    const kinds = [
        entry.availability.has_conformers && "conformers",
        entry.availability.has_thermo && "thermochemistry",
        entry.availability.has_statmech && "statistical mechanics",
        entry.availability.has_transport && "transport",
    ].filter(Boolean)
    const count = entry.availability.calculation_count
    const calculations = `${count} ${count === 1 ? "calculation" : "calculations"}`
    return kinds.length > 0 ? `${calculations}; ${kinds.join(", ")}` : `${calculations}; no deposited results`
}

/** The one sentence under a molecule's title. */
export function moleculeLede(versionCount: number): LedeText {
    const count = ledeWord("count", { count: `${versionCount} ${versionCount === 1 ? "version" : "versions"}` })
    return ledeText`A molecule with ${count}; each version holds its own records.`
}
