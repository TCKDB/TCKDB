import { BROWSE_KIND_PATHS } from "../api/browseApi"
import { participantChips, type ParticipantFacets } from "../domain/participantFacets"
import type { Crumb } from "./Breadcrumbs"
import { Formula } from "./Formula"

/** Molecules, then the molecule, then the version of it that owns the record: the parents shared by every page below an entry. */
export function entryCrumbs({ speciesRef, speciesEntryRef, formula, smiles, facets }: {
    speciesRef: string
    speciesEntryRef: string
    formula?: string | null
    smiles?: string | null
    facets?: ParticipantFacets | null
}): Crumb[] {
    return [
        { label: "Molecules", to: BROWSE_KIND_PATHS.species },
        { label: formula ? <Formula value={formula} /> : (smiles ?? "This molecule"), to: `/species/${speciesRef}` },
        { label: participantChips(facets).join(" · ") || "This version", to: `/species-entries/${speciesEntryRef}` },
    ]
}
