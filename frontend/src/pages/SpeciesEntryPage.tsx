import { useEffect } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import "../species-entry.css"
import type { ConformerProjection, SpeciesEntryProjection } from "../api/speciesEntryApi"
import { ConformerGeometryTab } from "../components/ConformerGeometryTab"
import { ConformerSelector } from "../components/ConformerSelector"
import { ConformerSinglePointTab } from "../components/ConformerSinglePointTab"
import { EntryStatmechSection } from "../components/EntryStatmechSection"
import { EntryTabs } from "../components/EntryTabs"
import { EntryThermoSection } from "../components/EntryThermoSection"
import { EntryTransportSection } from "../components/EntryTransportSection"
import { EntryIdentity } from "../components/SpeciesEntrySummary"
import { DEFAULT_SECTION, isEntrySection } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"
import { useSpeciesEntry } from "../hooks/useSpeciesEntry"

export default function SpeciesEntryPage() {
    const { entryRef = "", section } = useParams<{ entryRef: string; section?: string }>()
    const state = useSpeciesEntry(entryRef)

    if (!state || state.entryRef !== entryRef) return <LoadingEntry />
    if ("status" in state && state.status === "missing") return <MissingEntry entryRef={entryRef} />
    if ("status" in state && state.status === "malformed") return <MalformedEntry />
    if ("status" in state) return <UnavailableEntry />

    const activeSection: EntrySection = isEntrySection(section) ? section : DEFAULT_SECTION
    return <EntryDocument entry={state.entry} conformers={state.conformers} activeSection={activeSection} entryRef={entryRef} />
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
// Conformer-first: pick a basin, then read its geometry, single-point
// energy, statistical mechanics and thermochemistry in tab blocks beneath
// it -- the shape the owner asked for directly. The selected conformer is
// carried in the `?conformer=` query param (not just component state) so a
// reload lands back on the same basin, the same way `:section` already
// carries the active tab; an unset or stale param self-heals to the first
// conformer via `useEffect` below rather than erroring.
// ---------------------------------------------------------------------------
function EntryDocument({ entry, conformers, activeSection, entryRef }: {
    entry: SpeciesEntryProjection
    conformers: ConformerProjection[]
    activeSection: EntrySection
    entryRef: string
}) {
    const [searchParams, setSearchParams] = useSearchParams()
    const requestedRef = searchParams.get("conformer")
    const requestedConformer = conformers.find((conformer) => conformer.conformer_group.conformer_group_ref === requestedRef)
    const selectedConformer = requestedConformer ?? conformers[0] ?? null

    // Self-heal the URL to name what's actually selected: an empty/stale
    // `conformer` param becomes the first conformer's ref, once conformers
    // are known. Never fires when there is nothing to select.
    useEffect(() => {
        if (!selectedConformer) return
        const canonicalRef = selectedConformer.conformer_group.conformer_group_ref
        if (requestedRef === canonicalRef) return
        const next = new URLSearchParams(searchParams)
        next.set("conformer", canonicalRef)
        setSearchParams(next, { replace: true })
        // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when the resolved conformer identity changes, not on every searchParams object identity change
    }, [selectedConformer?.conformer_group.conformer_group_ref])

    const conformerQuery = selectedConformer
        ? `?conformer=${encodeURIComponent(selectedConformer.conformer_group.conformer_group_ref)}`
        : ""

    function selectConformer(conformerGroupRef: string) {
        const next = new URLSearchParams(searchParams)
        next.set("conformer", conformerGroupRef)
        setSearchParams(next, { replace: true })
    }

    return <section className="entry-page">
        <nav className="record-breadcrumbs" aria-label="Breadcrumb">
            <Link to="/">TCKDB</Link>
            <span aria-hidden="true">/</span>
            <Link to={`/species/${entry.speciesRef}`}>Species</Link>
            <span aria-hidden="true">/</span>
            <span aria-current="page">Species entry</span>
        </nav>
        <EntryIdentity entry={entry} />

        <ConformerSelector
            conformers={conformers}
            selectedRef={selectedConformer?.conformer_group.conformer_group_ref ?? null}
            onSelect={selectConformer}
        />

        <EntryTabs entryRef={entryRef} activeSection={activeSection} conformerQuery={conformerQuery} />
        <TabPanel section={activeSection} entryRef={entryRef} conformer={selectedConformer} />
    </section>
}

// The tab itself already carries the section's name (`EntryTabs`, id
// `tab-${section}`) -- `aria-labelledby` points there per the standard
// tabpanel pattern, so the panel does not repeat it as its own heading.
// Each panel body supplies its own `<h2>` instead, matching the
// `ledger-section` shape `EntryThermoSection`/`EntryStatmechSection`/
// `EntryTransportSection` already use.
function TabPanel({ section, entryRef, conformer }: {
    section: EntrySection
    entryRef: string
    conformer: ConformerProjection | null
}) {
    return (
        <div className="tab-panel" role="tabpanel" id={`panel-${section}`} aria-labelledby={`tab-${section}`} tabIndex={0}>
            <TabPanelBody section={section} entryRef={entryRef} conformer={conformer} />
        </div>
    )
}

function TabPanelBody({ section, entryRef, conformer }: {
    section: EntrySection
    entryRef: string
    conformer: ConformerProjection | null
}) {
    if (section === "geometry") {
        return conformer
            ? <ConformerGeometryTab conformer={conformer} />
            : <p className="empty-projection">No conformer basins are projected for this entry, so there is no geometry evidence to show.</p>
    }
    if (section === "sp") {
        return conformer
            ? <ConformerSinglePointTab conformer={conformer} />
            : <p className="empty-projection">No conformer basins are projected for this entry, so there is no single-point evidence to show.</p>
    }
    if (section === "statmech") return <EntryStatmechSection entryRef={entryRef} conformer={conformer} />
    if (section === "thermo") return <EntryThermoSection entryRef={entryRef} />
    return <EntryTransportSection entryRef={entryRef} />
}
