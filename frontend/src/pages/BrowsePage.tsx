import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import "../browse.css"
import {
    BROWSE_KIND_LABELS,
    DEFAULT_BROWSE_KIND,
    EMPTY_BROWSE_FILTERS,
    clearInapplicableFilters,
    hasActiveFilters,
    isBrowseKind,
} from "../api/browseApi"
import type { BrowseFilters, BrowseKind } from "../api/browseApi"
import { BrowseFilterForm } from "../components/BrowseFilterForm"
import { BrowseKindSelector } from "../components/BrowseKindSelector"
import { PageShell } from "../components/PageShell"
import { SpeciesBrowseRow } from "../components/SpeciesBrowseRow"
import { TransitionStateBrowseRow } from "../components/TransitionStateBrowseRow"
import { archiveEmptyMessage, filteredEmptyMessage, pagedPastEndMessage } from "../domain/browseEmptyState"
import type { BrowseState } from "../hooks/useBrowse"
import { useBrowse } from "../hooks/useBrowse"

const PAGE_SIZE = 20

/**
 * The archive index: /species/browse and /transition-states/browse have
 * always existed on the backend (composition filters included) with no UI
 * ever calling them -- this page is that UI, not a new capability. See the
 * design brief for the full gap measurement.
 */
export default function BrowsePage() {
    const [searchParams, setSearchParams] = useSearchParams()
    const requestedKind = searchParams.get("kind")
    const kind: BrowseKind = isBrowseKind(requestedKind) ? requestedKind : DEFAULT_BROWSE_KIND

    // Self-heal: an absent or unrecognised `?kind=` becomes the canonical
    // value once resolved, the same pattern `?conformer=` uses on
    // `SpeciesEntryPage` -- the address bar always names what is actually
    // selected, so a reload or a shared link lands on the same view.
    useEffect(() => {
        if (requestedKind === kind) return
        const next = new URLSearchParams(searchParams)
        next.set("kind", kind)
        setSearchParams(next, { replace: true })
        // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when the resolved kind or the raw param changes, not on every searchParams object identity change
    }, [kind, requestedKind])

    // Seeded ONCE from the URL on mount (a lazy initializer, not an effect
    // that keeps re-syncing) -- the one deep-link case this page needs to
    // serve today is `SpeciesEntryPage`'s "Transition states for reactions
    // of this species" link, which arrives as a fresh navigation (a
    // different route, so `BrowsePage` mounts fresh and this runs with the
    // real query params) carrying `?kind=transition_state&participant_
    // smiles=...`. Not a general filters<->URL sync for every field --
    // only `participant_smiles` has an external linker today, so only it
    // is read back out.
    const [filters, setFilters] = useState<BrowseFilters>(() => ({
        ...EMPTY_BROWSE_FILTERS,
        participantSmiles: kind === "transition_state" ? (searchParams.get("participant_smiles") ?? "") : "",
    }))
    const [offset, setOffset] = useState(0)

    function selectKind(nextKind: BrowseKind) {
        if (nextKind === kind) return
        setFilters((current) => clearInapplicableFilters(nextKind, current))
        setOffset(0)
        const next = new URLSearchParams(searchParams)
        next.set("kind", nextKind)
        setSearchParams(next, { replace: true })
    }

    function updateFilters(patch: Partial<BrowseFilters>) {
        setFilters((current) => ({ ...current, ...patch }))
        setOffset(0)
    }

    const state = useBrowse(kind, filters, offset, PAGE_SIZE)

    return (
        <section className="browse-page">
            <nav aria-label="Breadcrumb" className="record-breadcrumbs">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Browse</span>
            </nav>
            <PageShell>
            <header className="browse-header">
                <p className="eyebrow">Archive index</p>
                <h1>Browse the archive</h1>
                <p className="browse-intro">
                    Read what is deposited without needing an identifier first. Choose what to browse, then narrow it
                    down by composition, review status, or evidence.
                </p>
            </header>

            <BrowseKindSelector kind={kind} onSelect={selectKind} />
            <BrowseFilterForm filters={filters} kind={kind} onChange={updateFilters} />

            <BrowseResults filters={filters} kind={kind} offset={offset} setOffset={setOffset} state={state} />
            </PageShell>
        </section>
    )
}

function BrowseResults({ kind, filters, offset, setOffset, state }: {
    kind: BrowseKind
    filters: BrowseFilters
    offset: number
    setOffset: (updater: (current: number) => number) => void
    state: BrowseState
}) {
    if (state.status === "loading") {
        return (
            <p aria-busy="true" className="browse-status">
                Loading {BROWSE_KIND_LABELS[kind].toLowerCase()} records…
            </p>
        )
    }
    // Three distinct FAILURE reasons -- an invalid request (a bad filter
    // value, or an offset past the archive's cap) must never share copy
    // with a malformed response (an archive-side schema bug) or a
    // transient outage (5xx/network, where "try again later" is honest
    // advice). See `useBrowse`'s doc comment for the full classification.
    if (state.status === "invalid") {
        return <p className="browse-status" role="alert">{state.detail}</p>
    }
    if (state.status === "malformed") {
        return (
            <p className="browse-status" role="alert">
                The archive responded, but this listing could not be validated. That is an archive-side issue, not a
                connection problem.
            </p>
        )
    }
    if (state.status === "unavailable") {
        return <p className="browse-status" role="alert">The archive service could not load this listing. Try again later.</p>
    }

    const { result } = state
    const { pagination } = result
    if (result.records.length === 0) {
        // FOUR states, never collapsed: this branch alone covers three of
        // them. `pagination.total > 0` is checked FIRST -- a nonzero total
        // with zero returned records means the reader paged past the end
        // of the archive, which is neither "nothing of this kind exists"
        // nor "filters excluded everything" and must not be reported as
        // either. Only once that is ruled out does `hasActiveFilters`
        // decide between the other two. See `domain/browseEmptyState.ts`.
        // The failed-request states above cover the fourth.
        const message = pagination.total > 0
            ? pagedPastEndMessage(kind)
            : hasActiveFilters(kind, filters) ? filteredEmptyMessage(kind) : archiveEmptyMessage(kind)
        return <>
            <p className="browse-empty">{message}</p>
            {pagination.total > 0 && (
                <div className="browse-pagination">
                    <button disabled={offset === 0} onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))} type="button">
                        Previous
                    </button>
                    <button disabled type="button">Next</button>
                </div>
            )}
        </>
    }

    const rangeStart = pagination.offset + 1
    const rangeEnd = pagination.offset + result.records.length
    const hasNextPage = rangeEnd < pagination.total

    return <>
        <p className="browse-count">
            {pagination.total} {pagination.total === 1 ? "record" : "records"} · showing {rangeStart}–{rangeEnd}
        </p>
        <ul className="browse-rows">
            {result.kind === "transition_state"
                ? result.records.map((record) => (
                    <TransitionStateBrowseRow key={record.transition_state_entry.transition_state_entry_ref} record={record} />
                ))
                : result.records.map((record) => <SpeciesBrowseRow key={record.species_ref} record={record} />)}
        </ul>
        <div className="browse-pagination">
            <button disabled={offset === 0} onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))} type="button">
                Previous
            </button>
            <button disabled={!hasNextPage} onClick={() => setOffset((current) => current + PAGE_SIZE)} type="button">
                Next
            </button>
        </div>
    </>
}
