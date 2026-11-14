import { versionWords } from "./recordWords"
import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import { chargeText, spinDisplay } from "./chemistryFormat"
import { facetChips } from "./recordFacets"
import type { LedeText } from "./ledeText"
import { ledeText, ledeWord } from "./ledeText"
import { hiddenByDefaultNote } from "./paging"
import type { HiddenByDefault } from "../api/listEnvelope"

/** What a version is, built from codes only: charge, spin, then electronic state, stereochemistry and isotopes. */
export function versionLabel(molecule: { charge: number; multiplicity: number }, version: ScientificSpeciesEntrySummary): string {
    return `charge ${chargeText(molecule.charge) ?? "not recorded"} · ${spinDisplay(molecule.multiplicity)} · ${facetChips(version).join(" · ")}`
}

/** The facts that tell versions of one molecule apart (state, stereochemistry, isotopes), without the charge and spin they share. */
export function versionFacts(version: ScientificSpeciesEntrySummary): string {
    return facetChips(version).join(" · ")
}

/** "Version 2 · minimum · ground state": the version's position in the list the page shows, then the facts that tell it apart. */
export function versionName(version: ScientificSpeciesEntrySummary, position: number): string {
    const facts = versionFacts(version)
    return facts ? `${versionWords(position)} · ${facts}` : versionWords(position)
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
export function moleculeLede(versionCount: number, someHidden: boolean): LedeText {
    const noun = `${someHidden ? "listed " : ""}${versionCount === 1 ? "version" : "versions"}`
    const count = ledeWord("count", { count: `${versionCount} ${noun}` })
    return ledeText`A molecule with ${count}.`
}

/** The note for versions the default visibility left out, worded as versions whatever the server called them. */
export function hiddenVersionsNote(hidden: HiddenByDefault | null | undefined): string | null {
    return hiddenByDefaultNote(hidden ? { ...hidden, counted: null } : hidden, "versions", "version")
}
