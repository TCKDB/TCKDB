import { Link } from "react-router-dom"
import { Formula } from "./Formula"
import { stereoChip } from "../domain/recordFacets"

/**
 * The "Species entry" link every record page that owns a species (the
 * calculation, geometry, and conformer-observation pages) points at
 * `/species-entries/:ref`.
 *
 * Owner report ("record-page residuals" re-review, item 4): this used to
 * render `species_entry_label` ALONE as the link text (`identity.label ??
 * identity.speciesEntryRef` / `species.species_entry_label ??
 * species.species_entry_ref`) — a bare "R" on a sampled live entry, with
 * no context, linking to a page about a C9H9 radical. The owner could not
 * tell what it meant.
 *
 * Correction mid-fix, worth keeping here since the first draft of this
 * component got it wrong: `species_entry_label` is NOT free text a
 * depositor typed into a label field (unlike a transition-state or
 * conformer-group label, both plain stored strings with no
 * transformation — see the PR body's "Other depositor strings still
 * rendered" list). It is computed server-side
 * (`app.services.scientific_read.species_identity.species_entry_label`)
 * as a compact DISCRIMINATOR built from whichever of stereo_label /
 * electronic_state_kind / electronic_state_label / term_symbol /
 * isotope_key actually differ from this species' default — see
 * `../domain/recordFacets.ts`'s own module docstring for the fuller
 * explanation (`facetChips`/`stereoChip` already document this). "R" on
 * the live example is that entry's `stereo_label` ("R" enantiomer),
 * not an ARC-submitted name.
 *
 * So the fix is not to suppress it (that would drop a real, if terse,
 * scientific fact) but to never let it stand ALONE as the entire link
 * text. Link text is the formula (RDKit-derived, TCKDB's own computed
 * fact — the same one this record's own title/h1 already renders
 * elsewhere on the page) followed by the label run through `stereoChip`
 * (the one existing `recordFacets.ts` expansion that applies to a bare
 * compact string like this — `facetChips` needs the four raw axes
 * separately, which none of these three pages' wire shapes serve; only
 * the already-joined `species_entry_label` string reaches this
 * component). `stereoChip("R")` -> "R enantiomer"; anything it does not
 * recognise (a term symbol, an isotope key, a multi-part discriminator)
 * passes through unchanged -- still shown, next to the formula, never as
 * the sole text. "Species entry" is the base text only when there is
 * neither a formula nor a label to show.
 */
export function SpeciesEntryLink({ speciesEntryRef, formula, speciesEntryLabel }: {
    speciesEntryRef: string
    formula?: string | null
    speciesEntryLabel?: string | null
}) {
    const base = formula ? <Formula value={formula} /> : "Species entry"
    return (
        <Link to={`/species-entries/${speciesEntryRef}`}>
            {base}
            {speciesEntryLabel && <> · {stereoChip(speciesEntryLabel)}</>}
        </Link>
    )
}
