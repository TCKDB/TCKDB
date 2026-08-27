import { useParams } from "react-router-dom"
import "../species-entry.css"
import type { ConformerProjection, SpeciesEntryProjection } from "../api/speciesEntryApi"
import { LevelsOfTheorySection, LineageSection } from "../components/SpeciesEntryEvidence"
import {
    AvailabilitySection,
    EntryIdentity,
    EntryNavigation,
} from "../components/SpeciesEntrySummary"
import { isEntrySection } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"
import { useSpeciesEntry } from "../hooks/useSpeciesEntry"

export default function SpeciesEntryPage() {
    const { entryRef = "", section } = useParams<{ entryRef: string; section?: string }>()
    const state = useSpeciesEntry(entryRef)

    if (!state || state.entryRef !== entryRef) return <LoadingEntry />
    if ("status" in state && state.status === "missing") return <MissingEntry entryRef={entryRef} />
    if ("status" in state && state.status === "malformed") return <MalformedEntry />
    if ("status" in state) return <UnavailableEntry />

    const activeSection: EntrySection = isEntrySection(section) ? section : "overview"
    return <EntryDocument entry={state.entry} conformers={state.conformers} activeSection={activeSection} />
}

function LoadingEntry() {
    return <section className="record-placeholder" aria-busy="true">
        <p className="eyebrow">Archive record</p><h1>Loading species entry</h1>
    </section>
}

function MissingEntry({ entryRef }: { entryRef: string }) {
    return <section className="record-placeholder">
        <p className="eyebrow">Archive record</p><h1>Entry not found</h1><code>{entryRef}</code>
        <p>No species entry with this stable reference is available in this archive projection.</p>
    </section>
}

function MalformedEntry() {
    return <section className="record-placeholder" role="alert">
        <p className="eyebrow">Archive record</p><h1>Entry data could not be read</h1>
        <p>The archive responded, but this page could not validate the scientific entry projection.</p>
    </section>
}

function UnavailableEntry() {
    return <section className="record-placeholder" role="alert">
        <p className="eyebrow">Archive record</p><h1>Entry unavailable</h1>
        <p>The archive service could not load this entry projection. Try again later.</p>
    </section>
}

function EntryDocument({ entry, conformers, activeSection }: {
    entry: SpeciesEntryProjection
    conformers: ConformerProjection[]
    activeSection: EntrySection
}) {
    return <section className="entry-page">
        <EntryIdentity entry={entry} />
        <EntryNavigation entryRef={entry.species_entry_ref} activeSection={activeSection} />
        {(activeSection === "overview" || activeSection === "conformers" || activeSection === "calculations") && (
            <LineageSection conformers={conformers} />
        )}
        {(activeSection === "overview" || activeSection === "calculations") && (
            <LevelsOfTheorySection conformers={conformers} />
        )}
        {(activeSection === "overview"
            || activeSection === "thermo"
            || activeSection === "statmech"
            || activeSection === "transport") && (
            <AvailabilitySection entry={entry} activeSection={activeSection} />
        )}
    </section>
}
