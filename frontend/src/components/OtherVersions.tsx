import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { SpeciesEntryProjection } from "../api/speciesEntryApi"
import { hiddenVersionsNote, versionHolds, versionLabel } from "../domain/entryVersions"
import { useSpeciesOverview } from "../hooks/useSpeciesOverview"
import { SectionHeading } from "./PageSections"
import { ReviewChip } from "./ReviewChip"

/** How many versions are listed, not counting this page's own when the listing left it out. */
function countSentence(listed: number, currentAdded: boolean): string {
    if (currentAdded) {
        if (listed === 0) return "This is the only version listed for this molecule."
        return `This molecule has ${listed} other ${listed === 1 ? "version" : "versions"} listed, plus this one.`
    }
    if (listed === 1) return "This is the only version listed for this molecule."
    return `This molecule has ${listed} versions listed; this page shows one of them.`
}

/** Every version (entry) of this entry's molecule, this one marked even when the listing leaves it out. Labels come from codes. */
export function OtherVersions({ entry }: { entry: SpeciesEntryProjection }) {
    const state = useSpeciesOverview(entry.speciesRef)
    const species = state.status === "ready" && state.speciesRef === entry.speciesRef ? state.species : null
    const listed = species?.entries ?? []
    const versions = listed.some((candidate) => candidate.species_entry_ref === entry.species_entry_ref) ? listed : [entry, ...listed]
    const hidden = species ? hiddenVersionsNote(species.entriesHiddenByDefault) : null
    return (
        <section className="entry-versions" aria-labelledby="entry-versions">
            <SectionHeading id="entry-versions">Other versions of this molecule</SectionHeading>
            {state.status === "loading" && <p className="note" role="status" aria-busy="true">Loading the other versions…</p>}
            {state.status !== "loading" && state.status !== "ready" && <p className="note" role="alert">The other versions of this molecule could not be loaded.</p>}
            {species && (
                <>
                    <p className="note">
                        {countSentence(listed.length, versions.length > listed.length)}
                        {" "}<Link to={`/species/${species.species_ref}`}>Open the molecule page</Link>
                    </p>
                    {hidden && <p className="note" role="note">{hidden}</p>}
                    {versions.length > 1 && <ul className="entry-versions-list">
                        {versions.map((version) => {
                            const current = version.species_entry_ref === entry.species_entry_ref
                            const label = versionLabel(species, version)
                            return (
                                <li key={version.species_entry_ref} className="entry-versions-item" aria-current={current ? "true" : undefined}>
                                    <p className="entry-versions-title">
                                        {current ? <>{label} <span className="entry-versions-here">(this page)</span></> : <Link to={`/species-entries/${version.species_entry_ref}`}>{label}</Link>}
                                    </p>
                                    <p className="entry-versions-meta">{versionHolds(version)}</p>
                                    <p className="entry-versions-meta"><ReviewChip status={version.review.status} explain={false} /> <code className="t-preserve-case">{version.species_entry_ref}</code></p>
                                </li>
                            )
                        })}
                    </ul>}
                </>
            )}
        </section>
    )
}
