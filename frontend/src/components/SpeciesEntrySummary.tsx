import { Link } from "react-router-dom"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import type { SpeciesIdentity } from "../domain/recordIdentity"
import { stereoDescription } from "../domain/recordFacets"
import { termSymbolSpoken } from "../domain/electronicState"
import { publicElectronicStateLabel, publicTermSymbol } from "../domain/publicText"
import { entryLede } from "../domain/entryLede"
import { Formula } from "./Formula"
import { RecordIdentityHeader } from "./RecordIdentityHeader"
import { RefsDisclosure } from "./RefsDisclosure"

function availabilityText(entry: SpeciesEntryProjection) {
    return `${entry.availability.has_conformers ? "Conformers" : "No conformers"}`
        + `${entry.availability.has_thermo ? " · Thermochemistry" : ""}`
        + `${entry.availability.has_statmech ? " · Statistical mechanics" : ""}`
        + `${entry.availability.has_transport ? " · Transport" : ""}`
}

/**
 * `RecordIdentityHeader` (also used by
 * `CalculationDetailPage`/`GeometryDetailPage`/`ConformerGroupPage`/
 * `ConformerObservationPage`/`TransitionStateEntryPage`) owns the
 * kicker + h1 + review pill + SMILES/InChIKey/charge-multiplicity facts,
 * so this page's hero matches every other record page's -- see
 * that component's own docstring for the shared header order.
 *
 * What stays local to this page: the four classification axes
 * (`species_entry_kind`/`electronic_state_kind`/label/term symbol),
 * stereochemistry and isotopologue, and "Archive availability" -- none of
 * these are part of `RecordIdentity`'s shared vocabulary (a geometry or
 * calculation owner never carries them), so they render in their own
 * `.entry-facts` list below the shared header, exactly as before.
 *
 * No pill boxes there either. Every fact appears exactly once, as a
 * labelled row in `entry-facts`, including the three axes (label, term
 * symbol, stereochemistry). Each of the three is rendered only when
 * the entry actually carries it. The Stereochemistry row is read from the
 * descriptor and its resolution, never from the display
 * `stereo_label` alone: that label is NULL for an entry whose stereo is
 * wholly unknown, so a missing label is NOT "not stereochemically
 * distinguished". "Achiral" is said only for a NULL descriptor; partial and
 * unresolved entries say so; an allene axis says "axial stereo not
 * represented".
 */
export function EntryIdentity({ entry }: { entry: SpeciesEntryProjection }) {
    // No `speciesEntryRef`/`speciesEntryLabel` here -- unlike every other
    // `RecordIdentityHeader` caller (which renders an OWNER's identity,
    // with a "Species entry" fact linking OUT to this very page), this
    // component IS that entry's own page. A fact linking to itself would
    // be a dead end this header's other callers never have to special-case.
    const identity: SpeciesIdentity = {
        kind: "species_entry",
        formula: entry.formula,
        canonicalSmiles: entry.canonicalSmiles,
        inchiKey: entry.inchiKey,
        charge: entry.charge,
        multiplicity: entry.multiplicity,
    }
    // Says "Achiral" only for a NULL descriptor; an entry whose stereo is partial or unresolved
    // (display `stereo_label` NULL) is shown as that, not as "not distinguished".
    const stereo = stereoDescription(entry)
    return <header className="entry-hero">
        {/* Deliberately NOT SMILES-leads-formula-in-brackets (the form used
            everywhere else a species is shown): this h1
            IS the species-entry record page's own title, one page's own
            identity rather than two participants a reader could confuse.
            `entry.canonicalSmiles` already renders two lines below, in
            this same header's own identity block (`RecordIdentityHeader`'s
            "SMILES" fact), so a long SMILES string in the h1 too would
            only degrade the title (a 40-character SMILES reads badly as
            a page heading) for no disambiguation this page did not
            already have. */}
        <RecordIdentityHeader
            kicker="One version of a molecule"
            lede={entryLede(entry)}
            glyph="species-entry"
            review={entry.review.status}
            title={entry.formula ? <Formula value={entry.formula} /> : entry.canonicalSmiles}
            identity={identity}
            ownRef={{ label: "Entry ref", value: entry.species_entry_ref }}
        />
        <ul className="entry-facts" aria-label="Record facts">
            <FactItem label="Archive availability" value={availabilityText(entry)} />
            {/* The state label and term symbol only when they are in the grammar the API enforces on write
                (`electronicState.ts`); anything else is a free-text value and stays hidden (no depositor-typed
                text is printed). The term symbol carries a plain-words reading for a screen reader. */}
            {publicElectronicStateLabel(entry.electronic_state_label) && (
                <FactItem label="State label" value={publicElectronicStateLabel(entry.electronic_state_label)!} />
            )}
            {publicTermSymbol(entry.term_symbol) && (
                <FactItem label="Term symbol" value={publicTermSymbol(entry.term_symbol)!} spoken={termSymbolSpoken(entry.term_symbol)} />
            )}
            {stereo && <FactItem label="Stereochemistry" value={stereo} />}
            {entry.stereo_smiles && <FactItem label="Stereo SMILES" value={entry.stereo_smiles} />}
            {entry.isotope_key && <FactItem label="Isotopologue" value={entry.isotope_key} />}
        </ul>
        {/* `index.css`'s `a { color: inherit; text-decoration: none }` would leave
            a bare link unstyled. `.note` (design-system.css) gives it the
            muted-line treatment, and `.note a` gives the link the
            same underline/accent every other in-primitive link gets; the
            page-local `entry-ts-browse-note` class below supplies only the
            spacing that ties it to the identity block above, with no
            trailing gap. */}
        <p className="note entry-ts-browse-note">
            <Link to={`${BROWSE_KIND_PATHS.transition_state}?participant_smiles=${encodeURIComponent(entry.canonicalSmiles)}`}>
                Transition states for reactions of this molecule
            </Link>
        </p>
        {/* Collapsed by default (see `RefsDisclosure`) -- nothing else on
            this page needs to distinguish two species entries at rest; the
            formula/SMILES/InChIKey above already does that job, so every
            ref here can collapse without losing anything a reader needs at
            a glance. This entry's OWN ref is not in this list: it renders
            first, inline, in the identity
            header above via `ownRef`; only the RELATED parent-species ref
            stays here, per `RefsDisclosure.tsx`'s own docstring. */}
        <RefsDisclosure refs={[
            { label: "Species", value: entry.speciesRef, to: `/species/${entry.speciesRef}` },
        ]} />
    </header>
}

function FactItem({ label, value, spoken }: { label: string; value: string; spoken?: string | null }) {
    // `spoken` is the value in words for a screen reader (`²Π` is "doublet pi"); the printed symbol stays as stored.
    return <li><span>{label}</span><strong role={spoken ? "img" : undefined} aria-label={spoken ?? undefined}>{value}</strong></li>
}
