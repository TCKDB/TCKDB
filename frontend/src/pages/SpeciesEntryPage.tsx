import type { ReactNode } from "react"
import { Fragment } from "react"
import { Link, useParams } from "react-router-dom"
import "../species-entry.css"
import type { ConformerProjection, SpeciesEntryProjection } from "../api/speciesEntryApi"
import { EntryStatmechSection } from "../components/EntryStatmechSection"
import { EntryThermoSection } from "../components/EntryThermoSection"
import { EntryTransportSection } from "../components/EntryTransportSection"
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

// ---------------------------------------------------------------------------
// This entry page is one document in chapters, not a set of app tabs: the
// identity header above stays fixed across every chapter, and each chapter
// below is a region of that same document that becomes visible when its
// section is open — never an independently-mounted view. `CHAPTER_REGIONS`
// makes that plan literal: which sections a region belongs to, and what it
// renders, in one place, read top to bottom in on-page order. This replaced
// a chain of three independent `{condition && <Section/>}` checks whose
// only connection to each other was that they happened to sit next to each
// other in the file — the same visible result, but no single place said
// what a chapter *is*.
// ---------------------------------------------------------------------------
type ChapterRegion = { sections: readonly EntrySection[]; render: () => ReactNode }

function buildChapterRegions(entry: SpeciesEntryProjection, conformers: ConformerProjection[]): ChapterRegion[] {
    const entryRef = entry.species_entry_ref
    return [
        { sections: ["overview", "conformers", "calculations"], render: () => <LineageSection conformers={conformers} /> },
        { sections: ["overview", "calculations"], render: () => <LevelsOfTheorySection conformers={conformers} /> },
        { sections: ["overview"], render: () => <AvailabilitySection entry={entry} /> },
        { sections: ["thermo"], render: () => <EntryThermoSection entryRef={entryRef} /> },
        { sections: ["statmech"], render: () => <EntryStatmechSection entryRef={entryRef} /> },
        { sections: ["transport"], render: () => <EntryTransportSection entryRef={entryRef} /> },
    ]
}

function EntryDocument({ entry, conformers, activeSection }: {
    entry: SpeciesEntryProjection
    conformers: ConformerProjection[]
    activeSection: EntrySection
}) {
    const regions = buildChapterRegions(entry, conformers).filter((region) => region.sections.includes(activeSection))
    return <section className="entry-page">
        <nav className="record-breadcrumbs" aria-label="Breadcrumb">
            <Link to="/">TCKDB</Link>
            <span aria-hidden="true">/</span>
            <Link to={`/species/${entry.speciesRef}`}>Species</Link>
            <span aria-hidden="true">/</span>
            <span aria-current="page">Species entry</span>
        </nav>
        <EntryIdentity entry={entry} />
        <EntryNavigation entryRef={entry.species_entry_ref} activeSection={activeSection} />
        {regions.map((region, index) => <Fragment key={index}>{region.render()}</Fragment>)}
    </section>
}
