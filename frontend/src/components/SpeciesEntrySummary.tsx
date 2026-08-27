import { Link } from "react-router-dom"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { sectionLabels } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"

function multiplicityLabel(multiplicity: number) {
    const names: Record<number, string> = { 1: "singlet", 2: "doublet", 3: "triplet" }
    return names[multiplicity] ? `${names[multiplicity]} (${multiplicity})` : String(multiplicity)
}

function displayToken(value: string) {
    return value.replaceAll("_", " ")
}

export function EntryIdentity({ entry }: { entry: SpeciesEntryProjection }) {
    return <header className="entry-header">
        <p className="eyebrow">Species entry · deposited scientific record</p>
        <div className="entry-title">
            <h1>{entry.formula ?? entry.canonicalSmiles}</h1>
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
            <div><dt>Charge / multiplicity</dt><dd>{entry.charge} / {multiplicityLabel(entry.multiplicity)}</dd></div>
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

export function AvailabilitySection({ entry, activeSection }: {
    entry: SpeciesEntryProjection
    activeSection: EntrySection
}) {
    const sections = activeSection === "overview"
        ? (["thermo", "statmech", "transport"] as const)
        : [activeSection as "thermo" | "statmech" | "transport"]
    return <section className="availability-grid">
        {sections.map((path) => <Availability
            key={path}
            label={sectionLabels[path]}
            available={availabilityFor(entry, path)}
            path={path}
            entryRef={entry.species_entry_ref}
            summary={activeSection === "overview"}
        />)}
    </section>
}

function availabilityFor(entry: SpeciesEntryProjection, path: "thermo" | "statmech" | "transport") {
    if (path === "thermo") return entry.availability.has_thermo
    if (path === "statmech") return entry.availability.has_statmech
    return entry.availability.has_transport
}

function Availability({ label, available, path, entryRef, summary }: {
    label: string
    available: boolean
    path: "thermo" | "statmech" | "transport"
    entryRef: string
    summary: boolean
}) {
    return <section className="availability">
        <p className="eyebrow">{label}</p>
        <strong>{available ? "Available in this entry" : "Unavailable in this entry"}</strong>
        {available && summary
            ? <Link to={`/species-entries/${entryRef}/${path}`}>View record section</Link>
            : available
                ? <p>Detailed {label.toLowerCase()} records will be added in a later vertical slice.</p>
                : <p>No {label.toLowerCase()} record is projected for this entry.</p>}
    </section>
}
