import { useEffect } from "react"
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom"
import "../species-entry.css"
import type { ConformerProjection, SpeciesEntryProjection } from "../api/speciesEntryApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"
import { ConformerEvidenceLinkage } from "../components/ConformerEvidenceLinkage"
import { sortConformersForDisplay } from "../domain/conformerEvidence"
import { ConformerGeometryTab } from "../components/ConformerGeometryTab"
import { ConformerSelector } from "../components/ConformerSelector"
import { ConformerSinglePointTab } from "../components/ConformerSinglePointTab"
import { EntryStatmechSection } from "../components/EntryStatmechSection"
import { EntryTabs } from "../components/EntryTabs"
import { EntryThermoSection } from "../components/EntryThermoSection"
import { EntryTransportSection } from "../components/EntryTransportSection"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { EntryIdentity } from "../components/SpeciesEntrySummary"
import { DEFAULT_SECTION, isEntrySection, LEGACY_ENTRY_SECTION_ALIASES } from "../domain/speciesEntrySections"
import type { EntrySection } from "../domain/speciesEntrySections"
import { useSpeciesEntry } from "../hooks/useSpeciesEntry"

export default function SpeciesEntryPage() {
    const { entryRef = "", section } = useParams<{ entryRef: string; section?: string }>()
    const location = useLocation()
    const navigate = useNavigate()
    const state = useSpeciesEntry(entryRef)

    // Canonicalize a known legacy section alias (e.g. a stale
    // `/calculations` link from the earlier chapter-nav design --
    // `LEGACY_ENTRY_SECTION_ALIASES`, the same set `App.tsx`'s
    // `SpeciesEntrySectionRoute` already used to let this alias reach this
    // page instead of the not-found one) to the default tab's own path.
    // `isEntrySection` below already falls back to `DEFAULT_SECTION` for
    // what RENDERS; the address bar must say the same thing that's on
    // screen, not silently keep showing a path for content that isn't
    // there. `?conformer=` already self-heals on its own effect below --
    // this preserves it rather than dropping it.
    useEffect(() => {
        if (section !== undefined && !isEntrySection(section) && LEGACY_ENTRY_SECTION_ALIASES.has(section)) {
            navigate(`/species-entries/${entryRef}/${DEFAULT_SECTION}${location.search}`, { replace: true })
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when the entry/section identity changes, not on every navigate/location re-render
    }, [entryRef, section])

    if (!state || state.entryRef !== entryRef) return <LoadingEntry />
    if ("status" in state && state.status === "missing") return <MissingEntry entryRef={entryRef} />
    if ("status" in state && state.status === "malformed") return <MalformedEntry />
    if ("status" in state) return <UnavailableEntry />

    const activeSection: EntrySection = isEntrySection(section) ? section : DEFAULT_SECTION
    return (
        <EntryDocument
            entry={state.entry}
            conformers={state.conformers}
            spEnergies={state.spEnergies}
            activeSection={activeSection}
            entryRef={entryRef}
        />
    )
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
// Whether this entry has ANYTHING to show below the hero -- reads the same
// `availability` flags the hero's own "Archive availability" fact row
// already carries (`SpeciesEntrySummary.tsx`), never a second, independent
// check. When every flag is false (the empty-N2 case: no conformers, no
// thermo, no statmech, no transport) the picker/evidence/tab strip below
// would otherwise each restate that same emptiness in their own words --
// three empty states instead of one. When at least one flag is true, the
// full picker+tabs UI renders as normal; `EntryTabs` marks which of ITS
// tabs are populated using these same flags.
function hasAnyEvidence(entry: SpeciesEntryProjection): boolean {
    const availability = entry.availability
    return availability.has_conformers || availability.has_thermo || availability.has_statmech || availability.has_transport
}

// The ONE empty state for an entry with nothing recorded at all -- replaces
// what used to be three: the conformer picker's own "no conformer basins"
// message, the evidence panel (which never even mounted, since there was
// no selected conformer), and the active tab panel's own "no conformer
// basins" fallback. Reuses the picker's own container shape (`.conformer-
// picker`, the `conformer-picker-title` heading id) for visual continuity
// -- `ConformerSelector` itself never renders in this branch, so there is
// no id collision.
function EmptyEntryEvidence() {
    return (
        <section className="conformer-picker" aria-labelledby="conformer-picker-title">
            <p className="eyebrow">Conformers</p>
            <SectionHeading id="conformer-picker-title">Conformers</SectionHeading>
            <p className="empty-projection">No conformers are recorded for this entry.</p>
        </section>
    )
}

function EntryDocument({ entry, conformers, spEnergies, activeSection, entryRef }: {
    entry: SpeciesEntryProjection
    conformers: ConformerProjection[]
    spEnergies: SpeciesCalculationEnergyRecord[]
    activeSection: EntrySection
    entryRef: string
}) {
    const [searchParams, setSearchParams] = useSearchParams()
    const requestedRef = searchParams.get("conformer")
    const requestedConformer = conformers.find((conformer) => conformer.conformer_group.conformer_group_ref === requestedRef)
    // Default to the FIRST CARD AS DISPLAYED, not the archive's top-ranked
    // conformer. `conformers/search` orders by review rank, so `conformers[0]`
    // is meaningful -- but the cards render in numeric label order, and a
    // highlighted card that is not the first one reads as a bug rather than as
    // a ranking signal. If that ranking is worth surfacing it needs to be
    // visible on the card, not encoded in which one starts selected.
    const selectedConformer = requestedConformer ?? sortConformersForDisplay(conformers)[0] ?? null

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
        {/* `EntryIdentity` now routes through `PageShell`'s `identity` slot
            (previously it rendered directly above `<PageShell>`, spanning
            full width above the ToC/content flex row -- an earlier version
            of this comment called that placement intentional; it was
            superseded by the ToC-top-alignment change, which puts every
            record page's header through the same slot so the ToC rail
            starts level with the header, not below it). */}
        <PageShell identity={<EntryIdentity entry={entry} />}>
        {/* Item 5: one line pointing at the TS browse, participant filter
            prefilled with this species' own canonical SMILES
            (`participant_smiles`, exact-match against either side of the
            reaction -- see `TransitionStatesBrowseRequest`). Placed as the
            first child here (below the identity block: eyebrow, h1,
            SMILES/InChIKey, entry facts -- `PageShell` renders `identity`
            BEFORE `children`, see its own doc comment), NOT above the
            breadcrumb-adjacent identity block the way an earlier revision
            had it (measured: rendered above the eyebrow and h1 entirely,
            at the very top of the page). Always rendered, not gated behind
            a count check: a second request just to decide whether to show
            one line would add latency to every entry-page load for a link
            that, even at zero matches, lands on a well-formed empty state
            (`filteredEmptyMessage`/`archiveEmptyMessage` on `BrowsePage`)
            rather than a broken page -- the cost of "sometimes links to
            zero results" is lower than the cost of an extra round trip on
            every page view. No new CSS class: this file's stylesheet
            (`species-entry.css`) is out of scope for this change (see the
            file-ownership note in the PR), so this one line renders with
            plain default link/paragraph styling rather than reaching into
            a stylesheet this change does not otherwise touch. */}
        <p>
            <Link to={`/species?kind=transition_state&participant_smiles=${encodeURIComponent(entry.canonicalSmiles)}`}>
                Transition states for reactions of this species
            </Link>
        </p>
        {hasAnyEvidence(entry) ? (
            <>
                <ConformerSelector
                    conformers={conformers}
                    selectedRef={selectedConformer?.conformer_group.conformer_group_ref ?? null}
                    onSelect={selectConformer}
                />
                {selectedConformer && <ConformerEvidenceLinkage conformer={selectedConformer} />}

                <EntryTabs
                    entryRef={entryRef}
                    activeSection={activeSection}
                    conformerQuery={conformerQuery}
                    availability={entry.availability}
                />
                <TabPanel
                    section={activeSection}
                    entryRef={entryRef}
                    conformer={selectedConformer}
                    conformers={conformers}
                    spEnergies={spEnergies}
                />
            </>
        ) : (
            <EmptyEntryEvidence />
        )}
        </PageShell>
    </section>
}

// The tab itself already carries the section's name (`EntryTabs`, id
// `tab-${section}`) -- `aria-labelledby` points there per the standard
// tabpanel pattern, so the panel does not repeat it as its own heading.
// Each panel body supplies its own `<h2>` instead, matching the
// `ledger-section` shape `EntryThermoSection`/`EntryStatmechSection`/
// `EntryTransportSection` already use.
function TabPanel({ section, entryRef, conformer, conformers, spEnergies }: {
    section: EntrySection
    entryRef: string
    conformer: ConformerProjection | null
    conformers: ConformerProjection[]
    spEnergies: SpeciesCalculationEnergyRecord[]
}) {
    return (
        <div className="tab-panel" role="tabpanel" id={`panel-${section}`} aria-labelledby={`tab-${section}`} tabIndex={0}>
            <TabPanelBody section={section} entryRef={entryRef} conformer={conformer} conformers={conformers} spEnergies={spEnergies} />
        </div>
    )
}

function TabPanelBody({ section, entryRef, conformer, conformers, spEnergies }: {
    section: EntrySection
    entryRef: string
    conformer: ConformerProjection | null
    conformers: ConformerProjection[]
    spEnergies: SpeciesCalculationEnergyRecord[]
}) {
    if (section === "geometry") {
        return conformer
            ? <ConformerGeometryTab conformer={conformer} />
            : <p className="empty-projection">No conformers are recorded for this entry, so there is no geometry evidence to show.</p>
    }
    if (section === "sp") {
        return conformer
            ? <ConformerSinglePointTab conformer={conformer} spEnergies={spEnergies} />
            : <p className="empty-projection">No conformers are recorded for this entry, so there is no single-point evidence to show.</p>
    }
    if (section === "statmech") return <EntryStatmechSection entryRef={entryRef} conformer={conformer} conformers={conformers} />
    if (section === "thermo") return <EntryThermoSection entryRef={entryRef} conformer={conformer} conformers={conformers} />
    return <EntryTransportSection entryRef={entryRef} />
}
