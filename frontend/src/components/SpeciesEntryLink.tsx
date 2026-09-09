import { Link } from "react-router-dom"
import { SpeciesFace } from "./Formula"
import { stereoChip } from "../domain/recordFacets"

/**
 * The "Species entry" link every record page that owns a species (the
 * calculation and geometry pages via `RecordIdentityHeader`, and the
 * conformer-group and conformer-observation pages directly) points at
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
 * text. Link text is `SpeciesFace` (`./Formula.tsx`: SMILES leads, the
 * RDKit-derived formula — the same one this record's own title/h1
 * already renders elsewhere on the page — follows in parentheses)
 * followed by the label run through `stereoChip` (the one existing
 * `recordFacets.ts` expansion that applies to a bare compact string like
 * this — `facetChips` needs the four raw axes separately, which none of
 * these three pages' wire shapes serve; only the already-joined
 * `species_entry_label` string reaches this component). `stereoChip("R")`
 * -> "R enantiomer"; anything it does not recognise (a term symbol, an
 * isotope key, a multi-part discriminator) passes through unchanged --
 * still shown, next to the SMILES/formula, never as the sole text.
 *
 * SMILES leads (owner ruling, applied here even though this link is not
 * the collision-prone case the ruling's own reported defect was -- two
 * DIFFERENT species entries never appear side by side through this
 * component the way two reaction participants can): a bare formula can
 * still describe more than one structure, so it is never the honest
 * identity fact on its own, wherever a species is shown.
 *
 * Post-review fix: when there is neither a SMILES nor a formula (an
 * unparseable species SMILES with formula also absent, on any caller),
 * the base text falls back to the entry's own ref as `<code
 * className="data">` -- the same treatment every OTHER ref on these pages
 * gets -- NOT the literal words "Species entry". That fallback existed in
 * an earlier draft and produced "SPECIES ENTRY / Species entry · R
 * enantiomer": the `<dt>` beside this `<dd>` already says "Species
 * entry", so repeating it as the value said nothing a reader didn't
 * already have. A ref is a real, if terse, identifier the same way
 * `EntryStatmechSection.tsx`'s "Species entry: spe_…" rows already treat
 * one.
 *
 * Every caller now reaches the intended "[CH2]SO (CH3OS) · R enantiomer"
 * shape: `ConformerObservationPage`, `ConformerGroupPage`, and
 * `RecordIdentityHeader` (used by the calculation and geometry pages)
 * all pass this component their own `smiles`/`formula`. The conformer
 * surfaces' `formula` field was a backend gap (their
 * `ConformerSpeciesContext` carried no formula at all, unlike the
 * calculation/geometry payloads' `formula`-bearing shapes) closed
 * alongside this component -- see
 * `backend/app/services/scientific_read/conformers.py`'s
 * `_build_species_context`. `RecordIdentityHeader` and
 * `ConformerGroupPage` were also switched to call this component
 * directly rather than re-deriving the same expression locally (a
 * duplicate flagged on #375's review).
 */
export function SpeciesEntryLink({ speciesEntryRef, smiles, formula, speciesEntryLabel }: {
    speciesEntryRef: string
    smiles?: string | null
    formula?: string | null
    speciesEntryLabel?: string | null
}) {
    const base = (smiles || formula)
        ? <SpeciesFace smiles={smiles} formula={formula} />
        : <code className="data">{speciesEntryRef}</code>
    return (
        <Link to={`/species-entries/${speciesEntryRef}`}>
            {base}
            {speciesEntryLabel && <> · {stereoChip(speciesEntryLabel)}</>}
        </Link>
    )
}
