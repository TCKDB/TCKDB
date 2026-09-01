import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { words } from "../domain/provenanceFormat"
import { Formula } from "./Formula"
import { RecordFacetChips } from "./RecordFacetChips"
import { CopyButton, RefsDisclosure } from "./RefsDisclosure"

// `words` returns `null` for a missing/empty token (a case none of the
// tokens on this component's own wire type can actually hit -- `species_entry_kind`,
// `electronic_state_kind` and `review.status` are all non-nullable enum
// strings) but the fallback keeps this call site total rather than
// asserting a non-null enum shape it doesn't otherwise depend on.
function displayToken(value: string) {
    return words(value) ?? value
}

function availabilityText(entry: SpeciesEntryProjection) {
    return `${entry.availability.has_conformers ? "Conformers" : "No conformers"}`
        + `${entry.availability.has_thermo ? " · thermo" : ""}`
        + `${entry.availability.has_statmech ? " · statmech" : ""}`
        + `${entry.availability.has_transport ? " · transport" : ""}`
}

/**
 * Chemistry leads, references follow. The formula is the largest thing on
 * the page; SMILES and InChIKey sit directly beneath it as "chemistry"
 * facts, not database fields, and stay ALWAYS visible -- never behind the
 * References disclosure ("INCHI with SMILES should always be shown"). Only
 * the InChIKey is served anywhere on this record (verified against
 * `ScientificSpeciesRecord` in `backend/app/schemas/reads/scientific_species.py`
 * and the `inchi=` filter's own comment: "inchi has no stored/derivable
 * column ... an inchi-only query returns an empty result set" -- no full
 * InChI string exists in this archive to show), so the label says
 * "InChIKey", never "InChI" -- the two are different identifiers and
 * showing one under the other's name would misrepresent the data. Stable
 * public refs (`spc_…`, `spe_…`) stay in a quiet strip below the fact row —
 * still visible, still copyable (`CopyButton`), never competing with the
 * science for the reader's first look. See the design brief: "it shows the
 * public ref but that's terrible to show" was about equal billing, not
 * about hiding it.
 */
export function EntryIdentity({ entry }: { entry: SpeciesEntryProjection }) {
    return <header className="entry-hero">
        <p className="eyebrow">Species entry · deposited scientific record</p>
        <div className="entry-formula-row">
            <h1>{entry.formula ? <Formula value={entry.formula} /> : entry.canonicalSmiles}</h1>
            <span className="state-chip">{displayToken(entry.electronic_state_kind)}</span>
        </div>
        <ul className="entry-identifiers" aria-label="Chemical identifiers">
            <IdentifierItem label="SMILES" value={entry.canonicalSmiles} />
            <IdentifierItem label="InChIKey" value={entry.inchiKey} />
        </ul>
        <ul className="entry-facts" aria-label="Record facts">
            <FactItem label="Entry kind / state" value={`${displayToken(entry.species_entry_kind)} / ${displayToken(entry.electronic_state_kind)}`} />
            <FactItem label="Charge / multiplicity" value={`${chargeDisplay(entry.charge)} / ${spinDisplay(entry.multiplicity)}`} />
            <FactItem label="Review" value={displayToken(entry.review.status)} />
            <FactItem label="Archive availability" value={availabilityText(entry)} />
        </ul>
        {/* Classification facets -- identity, then facets, then provenance
            (the shared record-page header order; see the design brief).
            `RecordFacetChips` reads the four raw axes directly rather than
            the collapsed `species_entry_label` discriminator string --
            see `domain/recordFacets.ts` for the bug this replaces. */}
        <RecordFacetChips entry={entry} />
        {/* Collapsed by default (see `RefsDisclosure`) -- nothing else on
            this page needs to distinguish two species entries at rest; the
            formula/SMILES/InChIKey above already does that job, so every
            ref here can collapse without losing anything a reader needs at
            a glance. */}
        <RefsDisclosure refs={[
            { label: "Species", value: entry.speciesRef, to: `/species/${entry.speciesRef}` },
            { label: "Entry", value: entry.species_entry_ref },
        ]} />
    </header>
}

function IdentifierItem({ label, value }: { label: string; value: string }) {
    return <li className="identifier-chip">
        <span className="identifier-chip-label">{label}</span>
        <code>{value}</code>
        <CopyButton value={value} label={label} />
    </li>
}

function FactItem({ label, value }: { label: string; value: string }) {
    return <li><span>{label}</span><strong>{value}</strong></li>
}
