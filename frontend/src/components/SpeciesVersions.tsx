import { Link } from "react-router-dom"
import "../entry-numbers.css"
import type { SpeciesOverview } from "../api/speciesOverviewApi"
import { hiddenVersionsNote, versionFacts, versionHolds } from "../domain/entryVersions"
import { SectionHeading } from "./PageSections"
import { RecordGlyph } from "./RecordGlyph"
import { ReviewChip } from "./ReviewChip"

/** Every version (species entry) of a molecule: what it is, what it holds, its review status and a link. */
export function SpeciesVersions({ species }: { species: SpeciesOverview }) {
    const hidden = hiddenVersionsNote(species.entriesHiddenByDefault)
    return (
        <section className="entry-versions" aria-labelledby="species-versions">
            <SectionHeading
                id="species-versions"
                intro="Versions differ in electronic state, stereochemistry or isotopes. Each version holds its own records, and each record carries its own levels of theory."
            >
                Versions of this molecule
            </SectionHeading>
            {hidden && <p className="note" role="note">{hidden}</p>}
            {species.entries.length === 0 ? (
                <p className="empty-projection">No versions are currently listed for this molecule.</p>
            ) : (
                <ul className="entry-versions-list">
                    {species.entries.map((version) => {
                        const refId = `version-ref-${version.species_entry_ref}`
                        return (
                            <li key={version.species_entry_ref} className="entry-versions-item">
                                <p className="entry-versions-title">
                                    <RecordGlyph kind="species-entry" />{" "}
                                    <Link to={`/species-entries/${version.species_entry_ref}`} aria-describedby={refId}>{versionFacts(version)}</Link>
                                </p>
                                <p className="entry-versions-meta">{versionHolds(version)}</p>
                                <p className="entry-versions-meta">
                                    <ReviewChip status={version.review.status} explain={false} />{" "}
                                    <code id={refId} className="t-preserve-case">{version.species_entry_ref}</code>
                                </p>
                            </li>
                        )
                    })}
                </ul>
            )}
        </section>
    )
}
