import { Link, useParams } from "react-router-dom"
import "../species-overview.css"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"

function token(value: string) {
    return value.replaceAll("_", " ")
}

function multiplicityLabel(value: number) {
    const labels: Record<number, string> = { 1: "singlet", 2: "doublet", 3: "triplet" }
    return labels[value] ? `${labels[value]} (${value})` : String(value)
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
    const title = species.formula ?? species.canonical_smiles
    return (
        <section className="species-overview">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Species</span>
            </nav>
            <header className="species-header">
                <p className="eyebrow">Species record · chemical identity</p>
                <h1>{title}</h1>
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
                        value={`${species.charge} / ${multiplicityLabel(species.multiplicity)}`}
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
                    Entries are distinct scientific records for this chemical identity. Their state, stereochemistry,
                    review, and available evidence can differ.
                </p>
                {species.entries.length ? (
                    <ul className="entry-cards">
                        {species.entries.map((entry) => <EntryCard entry={entry} key={entry.species_entry_ref} />)}
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
                        <h3><Link to={`/species-entries/${entry.species_entry_ref}`}>{label}</Link></h3>
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
                        <dd>{available.length ? available.join(" · ") : "No projected sections"}</dd>
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
