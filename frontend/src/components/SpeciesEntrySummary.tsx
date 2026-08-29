import { Link } from "react-router-dom"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { words } from "../domain/provenanceFormat"
import { sectionLabels } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"
import { Formula } from "./Formula"

// `words` returns `null` for a missing/empty token (a case none of the
// tokens on this component's own wire type can actually hit -- `species_entry_kind`,
// `electronic_state_kind` and `review.status` are all non-nullable enum
// strings) but the fallback keeps this call site total rather than
// asserting a non-null enum shape it doesn't otherwise depend on.
function displayToken(value: string) {
    return words(value) ?? value
}

export function EntryIdentity({ entry }: { entry: SpeciesEntryProjection }) {
    return <header className="entry-header">
        <p className="eyebrow">Species entry · deposited scientific record</p>
        <div className="entry-title">
            <h1>{entry.formula ? <Formula value={entry.formula} /> : entry.canonicalSmiles}</h1>
            <span className="state-mark">{entry.electronic_state_kind}</span>
        </div>
        <dl className="identity-grid">
            <div>
                <dt>Species ref</dt>
                <dd><Link to={`/species/${entry.speciesRef}`}>{entry.speciesRef}</Link></dd>
            </div>
            <div><dt>Entry ref</dt><dd>{entry.species_entry_ref}</dd></div>
            <div><dt>SMILES</dt><dd>{entry.canonicalSmiles}</dd></div>
            <div><dt>InChIKey</dt><dd>{entry.inchiKey}</dd></div>
            <div>
                <dt>Entry kind / state</dt>
                <dd>{displayToken(entry.species_entry_kind)} / {displayToken(entry.electronic_state_kind)}</dd>
            </div>
            <div>
                <dt>Charge / multiplicity</dt>
                <dd>{chargeDisplay(entry.charge)} / {spinDisplay(entry.multiplicity)}</dd>
            </div>
            <div><dt>Review</dt><dd>{displayToken(entry.review.status)}</dd></div>
            <div>
                <dt>Archive availability</dt>
                <dd>
                    {entry.availability.has_conformers ? "Conformers" : "No conformers"}
                    {entry.availability.has_thermo ? " · thermo" : ""}
                    {entry.availability.has_statmech ? " · statmech" : ""}
                    {entry.availability.has_transport ? " · transport" : ""}
                </dd>
            </div>
        </dl>
    </header>
}

export function EntryNavigation({ entryRef, activeSection }: { entryRef: string; activeSection: EntrySection }) {
    return <nav className="entry-tabs" aria-label="Entry sections">
        <Link aria-current={activeSection === "overview" ? "page" : undefined} to={`/species-entries/${entryRef}`}>
            Overview
        </Link>
        {Object.entries(sectionLabels).map(([path, label]) => (
            <Link
                key={path}
                aria-current={activeSection === path ? "page" : undefined}
                to={`/species-entries/${entryRef}/${path}`}
            >
                {label}
            </Link>
        ))}
    </nav>
}

/**
 * The overview tab's boolean summary card for thermo/statmech/transport —
 * ONLY ever rendered on the overview tab (`SpeciesEntryPage.tsx` calls this
 * exclusively when `activeSection === "overview"`; the record tabs render
 * `EntryThermoSection`/`EntryStatmechSection`/`EntryTransportSection`
 * instead). Previously took an `activeSection` prop and rendered a narrower
 * per-tab summary on the record tabs themselves, with a "Detailed records
 * will be added in a later vertical slice" placeholder for an available-but-
 * unbuilt section — that promise is false as of this slice (the three
 * record tabs are built), so the branch and the prop that reached it were
 * both dropped rather than left as unreachable dead code.
 */
export function AvailabilitySection({ entry }: { entry: SpeciesEntryProjection }) {
    return <section className="availability-grid">
        {(["thermo", "statmech", "transport"] as const).map((path) => <Availability
            key={path}
            label={sectionLabels[path]}
            available={availabilityFor(entry, path)}
            path={path}
            entryRef={entry.species_entry_ref}
        />)}
    </section>
}

function availabilityFor(entry: SpeciesEntryProjection, path: "thermo" | "statmech" | "transport") {
    if (path === "thermo") return entry.availability.has_thermo
    if (path === "statmech") return entry.availability.has_statmech
    return entry.availability.has_transport
}

function Availability({ label, available, path, entryRef }: {
    label: string
    available: boolean
    path: "thermo" | "statmech" | "transport"
    entryRef: string
}) {
    return <section className="availability">
        <p className="eyebrow">{label}</p>
        <strong>{available ? "Available in this entry" : "Unavailable in this entry"}</strong>
        {available
            ? <Link to={`/species-entries/${entryRef}/${path}`}>View record section</Link>
            : <p>No {label.toLowerCase()} record is projected for this entry.</p>}
    </section>
}
