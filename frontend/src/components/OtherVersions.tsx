import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { hiddenByDefaultNote } from "../domain/paging"
import { facetChips } from "../domain/recordFacets"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"
import { SectionHeading } from "./PageSections"
import { ReviewChip } from "./ReviewChip"

function holds(entry: ScientificSpeciesEntrySummary): string {
    const kinds = [
        entry.availability.has_conformers && "conformers",
        entry.availability.has_thermo && "thermochemistry",
        entry.availability.has_statmech && "statistical mechanics",
        entry.availability.has_transport && "transport",
    ].filter(Boolean)
    const count = entry.availability.calculation_count
    const calculations = `${count} ${count === 1 ? "calculation" : "calculations"}`
    return kinds.length > 0 ? `${calculations}; ${kinds.join(", ")}` : `${calculations}; no deposited results`
}

/** Every entry of this entry's molecule, this one marked. Labels come from codes, never from depositor text. */
export function OtherVersions({ speciesRef, entryRef }: { speciesRef: string; entryRef: string }) {
    const state = useSpeciesOverview(speciesRef)
    const species = state.status === "ready" && state.speciesRef === speciesRef ? state.species : null
    const others = species ? species.entries.filter((entry) => entry.species_entry_ref !== entryRef).length : 0
    const hidden = species ? hiddenByDefaultNote(species.entriesHiddenByDefault, "entries") : null
    return (
        <section className="entry-versions" aria-labelledby="entry-versions">
            <SectionHeading id="entry-versions" intro="A molecule can have several entries. Each is one set of results; they differ in electronic state, stereochemistry, isotopes or the levels of theory behind their values.">
                Other versions of this molecule
            </SectionHeading>
            {state.status === "loading" && <p className="note" role="status" aria-busy="true">Loading the other versions…</p>}
            {state.status !== "loading" && state.status !== "ready" && <p className="note" role="alert">The other versions of this molecule could not be loaded.</p>}
            {species && (
                <>
                    <p className="note">
                        {others === 0
                            ? "This is the only entry listed for this molecule."
                            : `This molecule has ${species.entries.length} entries; this page shows one of them.`}
                        {" "}<Link to={`/species/${species.species_ref}`}>Open the molecule page</Link>
                    </p>
                    {hidden && <p className="note" role="note">{hidden}</p>}
                    <ul className="entry-versions-list">
                        {species.entries.map((entry) => {
                            const current = entry.species_entry_ref === entryRef
                            const label = `${chargeDisplay(species.charge)} · ${spinDisplay(species.multiplicity)} · ${facetChips(entry).join(" · ")}`
                            return (
                                <li key={entry.species_entry_ref} className="entry-versions-item" aria-current={current ? "true" : undefined}>
                                    <p className="entry-versions-title">
                                        {current ? <>{label} <span className="entry-versions-here">(this page)</span></> : <Link to={`/species-entries/${entry.species_entry_ref}`}>{label}</Link>}
                                    </p>
                                    <p className="entry-versions-meta">{holds(entry)}</p>
                                    <p className="entry-versions-meta"><ReviewChip status={entry.review.status} explain={false} /> <code className="t-preserve-case">{entry.species_entry_ref}</code></p>
                                </li>
                            )
                        })}
                    </ul>
                </>
            )}
        </section>
    )
}
