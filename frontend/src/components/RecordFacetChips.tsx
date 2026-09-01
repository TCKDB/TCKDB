import "../record-facet-chips.css"
import { facetChips } from "../domain/recordFacets"
import type { EntryFacetAxes } from "../domain/recordFacets"

/**
 * One pill per identity axis a species entry carries (kind / electronic
 * state / stereochemistry / isotopologue) -- see `domain/recordFacets.ts`
 * for the chip text itself and the bug this replaces. Each chip is
 * labelled by what it IS ("R enantiomer", not a bare "R"), and an axis
 * that is not set on this entry renders no chip at all.
 */
export function RecordFacetChips({ entry }: { entry: EntryFacetAxes }) {
    const chips = facetChips(entry)
    return (
        <ul className="record-facet-chips">
            {chips.map((chip) => <li className="record-facet-chip" key={chip}>{chip}</li>)}
        </ul>
    )
}
