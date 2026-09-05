import { Link } from "react-router-dom"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import type { SpeciesIdentity } from "../domain/recordIdentity"
import { words } from "../domain/provenanceFormat"
import { stereoChip } from "../domain/recordFacets"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { Formula } from "./Formula"
import { RecordIdentityHeader } from "./RecordIdentityHeader"
import { RefsDisclosure } from "./RefsDisclosure"

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
 * SHOULD-FIX-4 (species-entry/browse/chrome residuals re-review): this used
 * to build its own bespoke hero -- an off-scale `clamp(3rem, 7.5vw, 5.5rem)`
 * (88px) h1 vs. `--type-display-1`'s 64px everywhere else, SMILES/InChIKey
 * as a second, accent-tinted pill family (`.identifier-chip`) next to
 * `.value-pill`, and "REVIEW not reviewed" folded into a plain kv fact
 * where every other record page shows it as the header's own kicker-row
 * pill. `RecordIdentityHeader` (design/foundations PR B, already used by
 * `CalculationDetailPage`/`GeometryDetailPage`/`ConformerGroupPage`/
 * `ConformerObservationPage`/`TransitionStateEntryPage`) now owns the
 * kicker + h1 + review pill + SMILES/InChIKey/charge-multiplicity facts,
 * so this page's hero finally matches every other record page's -- see
 * that component's own docstring for the shared header order.
 *
 * `RecordIdentityHeader`'s own identity tier renders no copy button on
 * SMILES/InChIKey (neither does any other page that composes it, e.g.
 * `GeometryDetailPage`) -- losing this page's own copy affordance there is
 * the direct cost of that consistency, not an oversight; the stable refs
 * below (species, entry) stay copyable via `RefsDisclosure`'s own
 * `CopyButton`, unchanged.
 *
 * What stays local to this page: the four classification axes
 * (`species_entry_kind`/`electronic_state_kind`/label/term symbol),
 * stereochemistry and isotopologue, and "Archive availability" -- none of
 * these are part of `RecordIdentity`'s shared vocabulary (a geometry or
 * calculation owner never carries them), so they render in their own
 * `.entry-facts` list below the shared header, exactly as before.
 *
 * No pill boxes there either. The electronic state used to render three
 * times on this page (a `.state-chip` beside the `<h1>`, the "Entry kind /
 * state" row below, and a `RecordFacetChips` pill row) -- the owner's own
 * complaint: "where do entry kind/state but then have pill boxes as well
 * of the same info? no pill boxes." Every fact here now appears exactly
 * once, as a labelled row in `entry-facts` -- including the three axes
 * (label, term symbol, stereochemistry) that ONLY the pill row used to
 * carry: dropping the pills without adding these back would have silently
 * deleted facts a reader could see nowhere else (the owner caught this on
 * `spe_n5nt4fz3ztsfh2otwlyyvvl2je`: "why ... does it not show ... E isomer
 * like it does for Review etc."). Each of the three is rendered only when
 * the entry actually carries it -- an absent stereo label means this entry
 * is not stereochemically distinguished, not "unknown", so it gets no row
 * at all rather than a "not recorded" placeholder.
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
    return <header className="entry-hero">
        <RecordIdentityHeader
            kicker="Species entry · deposited scientific record"
            pill={<span className={reviewPillClass(entry.review.status)}>{displayToken(entry.review.status)}</span>}
            titleVariant="display-1"
            title={entry.formula ? <Formula value={entry.formula} /> : entry.canonicalSmiles}
            identity={identity}
        />
        <ul className="entry-facts" aria-label="Record facts">
            <FactItem label="Entry kind / state" value={`${displayToken(entry.species_entry_kind)} / ${displayToken(entry.electronic_state_kind)}`} />
            <FactItem label="Archive availability" value={availabilityText(entry)} />
            {entry.electronic_state_label && <FactItem label="Electronic state label" value={entry.electronic_state_label} />}
            {entry.term_symbol && <FactItem label="Term symbol" value={entry.term_symbol} />}
            {entry.stereo_label && <FactItem label="Stereochemistry" value={stereoChip(entry.stereo_label)} />}
            {entry.isotope_key && <FactItem label="Isotopologue" value={entry.isotope_key} />}
        </ul>
        {/* BLOCKING-1 (species-entry/browse/chrome residuals re-review):
            this used to be a bare `<p><Link>` with no class -- `index.css`'s
            `a { color: inherit; text-decoration: none }` left it reading as
            a stray, unstyled 16px heading with ~60px of empty space below
            (MEASURED). `.note` (design-system.css) gives it the house
            muted-line treatment, and `.note a` gives the link itself the
            same underline/accent every other in-primitive link gets; the
            page-local `entry-ts-browse-note` class below supplies only the
            spacing that ties it to the identity block above, with no
            trailing gap. */}
        <p className="note entry-ts-browse-note">
            <Link to={`/species?kind=transition_state&participant_smiles=${encodeURIComponent(entry.canonicalSmiles)}`}>
                Transition states for reactions of this species
            </Link>
        </p>
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

function FactItem({ label, value }: { label: string; value: string }) {
    return <li><span>{label}</span><strong>{value}</strong></li>
}
