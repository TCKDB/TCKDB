import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { ScientificSpeciesEntrySummary } from "../api/scientificSpeciesSchemas"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { chargeText, spinDisplay } from "../domain/chemistryFormat"
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

/** Every version (entry) of this entry's molecule, this one marked even when the listing leaves it out. Labels come from codes. */
export function OtherVersions({ entry }: { entry: SpeciesEntryProjection }) {
    const state = useSpeciesOverview(entry.speciesRef)
    const species = state.status === "ready" && state.speciesRef === entry.speciesRef ? state.species : null
    const listed = species?.entries ?? []
    const versions = listed.some((candidate) => candidate.species_entry_ref === entry.species_entry_ref) ? listed : [entry, ...listed]
    const hidden = species ? hiddenByDefaultNote(species.entriesHiddenByDefault, "versions") : null
    return (
        <section className="entry-versions" aria-labelledby="entry-versions">
            <SectionHeading id="entry-versions" intro="A molecule can have several versions, which differ in electronic state, stereochemistry or isotopes. Each version holds its own records, and each record carries its own levels of theory.">
                Other versions of this molecule
            </SectionHeading>
            {state.status === "loading" && <p className="note" role="status" aria-busy="true">Loading the other versions…</p>}
            {state.status !== "loading" && state.status !== "ready" && <p className="note" role="alert">The other versions of this molecule could not be loaded.</p>}
            {species && (
                <>
                    <p className="note">
                        {versions.length === 1
                            ? "This is the only version listed for this molecule."
                            : `This molecule has ${versions.length} versions listed; this page shows one of them.`}
                        {" "}<Link to={`/species/${species.species_ref}`}>Open the molecule page</Link>
                    </p>
                    {hidden && <p className="note" role="note">{hidden}</p>}
                    <ul className="entry-versions-list">
                        {versions.map((version) => {
                            const current = version.species_entry_ref === entry.species_entry_ref
                            const label = `charge ${chargeText(species.charge) ?? "not recorded"} · ${spinDisplay(species.multiplicity)} · ${facetChips(version).join(" · ")}`
                            return (
                                <li key={version.species_entry_ref} className="entry-versions-item" aria-current={current ? "true" : undefined}>
                                    <p className="entry-versions-title">
                                        {current ? <>{label} <span className="entry-versions-here">(this page)</span></> : <Link to={`/species-entries/${version.species_entry_ref}`}>{label}</Link>}
                                    </p>
                                    <p className="entry-versions-meta">{holds(version)}</p>
                                    <p className="entry-versions-meta"><ReviewChip status={version.review.status} explain={false} /> <code className="t-preserve-case">{version.species_entry_ref}</code></p>
                                </li>
                            )
                        })}
                    </ul>
                </>
            )}
        </section>
    )
}
