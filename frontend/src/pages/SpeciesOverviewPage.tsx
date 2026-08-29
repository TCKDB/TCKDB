import { Link, useParams } from "react-router-dom"
import "../species-overview.css"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import { Formula } from "../components/Formula"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"

function token(value: string) {
    return value.replaceAll("_", " ")
}

function stateLabel(state: string) {
    return `${token(state)} electronic state`
}

function groupEntriesByState(entries: ScientificSpeciesEntrySummary[]) {
    return entries.reduce<Map<string, ScientificSpeciesEntrySummary[]>>((groups, entry) => {
        const grouped = groups.get(entry.electronic_state_kind)
        if (grouped) {
            grouped.push(entry)
        } else {
            groups.set(entry.electronic_state_kind, [entry])
        }
        return groups
    }, new Map())
}

export default function SpeciesOverviewPage() {
    const { speciesRef = "" } = useParams<{ speciesRef: string }>()
    const state = useSpeciesOverview(speciesRef)

    if (state.speciesRef !== speciesRef || state.status === "loading") {
        return <State title="Loading species record…" busy />
    }
    if (state.status === "missing") return <State title="Species not found" ref={speciesRef} />
    if (state.status !== "ready") {
        const title = state.status === "malformed" ? "Species data could not be read" : "Species unavailable"
        return <State title={title} alert />
    }
    return <SpeciesDocument species={state.species} />
}

function State({ title, ref, busy, alert }: {
    title: string
    ref?: string
    busy?: boolean
    alert?: boolean
}) {
    const message = busy
        ? "Retrieving the species identity and its electronic-state entries."
        : alert
            ? "The archive response could not be read. Try again later."
            : "No species with this stable reference is available in this archive projection."
    return (
        <section className="record-placeholder" aria-busy={busy} role={alert ? "alert" : undefined}>
            <p className="eyebrow">Archive record</p>
            <h1>{title}</h1>
            {ref && <code>{ref}</code>}
            <p>{message}</p>
        </section>
    )
}

function SpeciesDocument({ species }: { species: SpeciesOverview }) {
    const entryGroups = groupEntriesByState(species.entries)
    return (
        <section className="species-overview">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Species</span>
            </nav>
            <header className="species-header">
                <p className="eyebrow">Species record · chemical identity</p>
                {/* `formula` typeset with subscripts when the archive computed
                    one, matching `IdentifierSearch.tsx`'s headline rule; the
                    SMILES fallback is not chemistry-formula text, so it is
                    never run through `Formula`. */}
                <h1>{species.formula ? <Formula value={species.formula} /> : species.canonical_smiles}</h1>
                <p className="species-intro">
                    This identity may have more than one electronic-state entry. Select the entry you mean before
                    reading its conformer or calculation evidence.
                </p>
                <dl className="species-identity-grid">
                    <Identity label="Species ref" value={species.species_ref} />
                    <Identity label="SMILES" value={species.canonical_smiles} />
                    <Identity label="InChIKey" value={species.inchi_key} />
                    <Identity
                        label="Charge / multiplicity"
                        value={`${chargeDisplay(species.charge)} / ${spinDisplay(species.multiplicity)}`}
                    />
                </dl>
            </header>
            <section className="entry-index" aria-labelledby="electronic-state-entries">
                <div className="entry-index-heading">
                    <div>
                        <p className="eyebrow">State-specific records</p>
                        <h2 id="electronic-state-entries">Electronic-state entries</h2>
                    </div>
                    <p>{species.entries.length} {species.entries.length === 1 ? "entry" : "entries"}</p>
                </div>
                <p className="entry-index-intro">
                    Entries are separate deposited records. They are grouped by electronic state so that repeated
                    ground-state records stay distinct without reading as interchangeable duplicates.
                </p>
                {species.entries.length ? (
                    <ul className="entry-state-groups">
                        {[...entryGroups].map(([state, entries]) => (
                            <EntryStateGroup entries={entries} key={state} state={state} />
                        ))}
                    </ul>
                ) : (
                    <p className="empty-projection">
                        No electronic-state entries are currently projected for this species.
                    </p>
                )}
            </section>
        </section>
    )
}

function EntryStateGroup({
    entries,
    state,
}: {
    entries: ScientificSpeciesEntrySummary[]
    state: string
}) {
    const groupId = `entry-state-${state}`
    const count = entries.length
    return (
        <li className="entry-state-group">
            <h3 id={groupId}>
                {stateLabel(state)}
                <span>{count} {count === 1 ? "entry" : "entries"}</span>
            </h3>
            <details className="entry-state-disclosure" open>
                <summary aria-describedby={groupId}>
                    Deposited records
                </summary>
                <p>
                    Each row is a separate record. Review status, available data, and its stable entry reference
                    help distinguish records with the same state classification.
                </p>
                <ul className="entry-rows">
                    {entries.map((entry) => <EntryCard entry={entry} key={entry.species_entry_ref} />)}
                </ul>
            </details>
        </li>
    )
}

function Identity({ label, value }: { label: string; value: string }) {
    return <div><dt>{label}</dt><dd>{value}</dd></div>
}

function EntryCard({ entry }: { entry: ScientificSpeciesEntrySummary }) {
    const label = entry.species_entry_label
        ?? entry.electronic_state_label
        ?? `${token(entry.species_entry_kind)} · ${token(entry.electronic_state_kind)}`
    const available = [
        entry.availability.has_conformers && "conformers",
        entry.availability.has_thermo && "thermo",
        entry.availability.has_statmech && "statmech",
        entry.availability.has_transport && "transport",
    ].filter(Boolean)

    return (
        <li>
            <article className="entry-card">
                <div className="entry-card-heading">
                    <div>
                        <p className="entry-card-type">{token(entry.species_entry_kind)}</p>
                        <h4><Link to={`/species-entries/${entry.species_entry_ref}`}>{label}</Link></h4>
                    </div>
                    <span className="entry-review">{token(entry.review.status)}</span>
                </div>
                <dl className="entry-facts">
                    <div><dt>State</dt><dd>{token(entry.electronic_state_kind)}</dd></div>
                    {entry.stereo_label && <div><dt>Stereochemistry</dt><dd>{entry.stereo_label}</dd></div>}
                    {entry.term_symbol && <div><dt>Term symbol</dt><dd>{entry.term_symbol}</dd></div>}
                    <div><dt>Calculations</dt><dd>{entry.availability.calculation_count}</dd></div>
                    <div>
                        <dt>Available data</dt>
                        <dd>{available.length ? available.join(" · ") : "None projected"}</dd>
                    </div>
                </dl>
                <Link className="entry-card-action" to={`/species-entries/${entry.species_entry_ref}`}>
                    Open state-specific record <span aria-hidden="true">→</span>
                </Link>
                <code>{entry.species_entry_ref}</code>
            </article>
        </li>
    )
}
