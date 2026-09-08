import { useEffect, useState } from "react"
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom"
import "../browse.css"
import {
    BROWSE_KIND_CONTENT,
    BROWSE_KIND_LABELS,
    BROWSE_KIND_PATHS,
    DEFAULT_BROWSE_KIND,
    EMPTY_BROWSE_FILTERS,
    browseKindForPath,
    clearInapplicableFilters,
    hasActiveFilters,
    isBrowseKind,
    seedFiltersFromUrl,
} from "../api/browseApi"
import type { BrowseFilters, BrowseKind } from "../api/browseApi"
import { BrowseFilterForm } from "../components/BrowseFilterForm"
import { BrowseKindSelector } from "../components/BrowseKindSelector"
import { PageShell } from "../components/PageShell"
import { ReactionBrowseRow } from "../components/ReactionBrowseRow"
import { SpeciesBrowseRow } from "../components/SpeciesBrowseRow"
import { TransitionStateBrowseRow } from "../components/TransitionStateBrowseRow"
import { archiveEmptyMessage, filteredEmptyMessage, pagedPastEndMessage } from "../domain/browseEmptyState"
import { formatWaitSeconds } from "../domain/rateLimitFormat"
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
    const location = useLocation()
    const navigate = useNavigate()
    const [searchParams] = useSearchParams()

    // The kind is fixed by the ROUTE now, not read from `?kind=` -- see
    // `BROWSE_KIND_PATHS`/`browseKindForPath` (`api/browseApi.ts`) and
    // `App.tsx`, which mounts this exact component at all four kind paths.
    // `routeKind` falls back to the default only defensively; every real
    // route this page is mounted at resolves.
    const routeKind = browseKindForPath(location.pathname) ?? DEFAULT_BROWSE_KIND

    // Backward compatibility: every browse link before this change pointed
    // at `/species?kind=...` -- `/species` is the one path that still
    // interprets that query param, so an old link keeps working. A
    // recognised, non-default kind is resolved HERE (synchronously, on the
    // very same render) rather than only in the redirect effect below, so
    // the first paint already shows the right kind's content/request --
    // without this, the page would flash species content (and fire a
    // species/browse request) for one render before redirecting. `kind=
    // species` or no `kind` at all needs no special casing (`/species` IS
    // species already); an unrecognised value falls through to `routeKind`,
    // preserving today's fallback -- `/species` already renders species,
    // so there is nothing to redirect to.
    const requestedKind = searchParams.get("kind")
    const legacyKind: BrowseKind | null =
        routeKind === DEFAULT_BROWSE_KIND && isBrowseKind(requestedKind) ? requestedKind : null
    const kind: BrowseKind = legacyKind ?? routeKind

    // The actual redirect: replace (never push -- the Back button must not
    // bounce through the old `?kind=` URL) to the resolved kind's own path,
    // carrying every OTHER query parameter forward untouched (filters,
    // pagination, whatever a bookmarked/shared link carried -- only `kind`
    // itself is stripped, since the path now says that).
    useEffect(() => {
        if (legacyKind === null || legacyKind === DEFAULT_BROWSE_KIND) return
        const next = new URLSearchParams(searchParams)
        next.delete("kind")
        const query = next.toString()
        const target = BROWSE_KIND_PATHS[legacyKind]
        navigate(query ? `${target}?${query}` : target, { replace: true })
        // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only when the resolved legacy kind or the location itself changes, not on every searchParams object identity change
    }, [legacyKind, location.pathname, location.search])

    // Seeded ONCE from the URL on mount (a lazy initializer, not an effect
    // that keeps re-syncing) -- arrives as a fresh navigation (a different
    // route, so `BrowsePage` mounts fresh and this runs with the real query
    // params). See `seedFiltersFromUrl`'s own doc comment for which kinds
    // seed what, and why this stays a hand-listed per-kind step rather than
    // a general filters<->URL sync.
    const [filters, setFilters] = useState<BrowseFilters>(() => ({
        ...EMPTY_BROWSE_FILTERS,
        ...seedFiltersFromUrl(kind, searchParams),
    }))
    const [offset, setOffset] = useState(0)

    // Runs alongside a real `<Link>` navigation now (`BrowseKindSelector`'s
    // own doc comment), not a programmatic `navigate()` call this function
    // makes itself -- the click handler only needs to clear whatever filters
    // do not apply to `nextKind` and reset pagination before the browser's
    // own navigation lands. `BrowsePage` is the exact same component
    // reference at all four kind paths (see `App.tsx`'s doc comment), so
    // React Router keeps this component instance across the navigation
    // instead of remounting it: `filters`/`offset` state below survives the
    // path change untouched, and `clearInapplicableFilters` still drops
    // whatever no longer applies to `nextKind` -- exactly the same
    // filter-carrying contract as before. The equality guard is defensive
    // only -- `BrowseKindSelector` never renders a link to the CURRENT kind
    // (see its own doc comment), so `nextKind === kind` cannot fire from a
    // real click today, but a stale/duplicated call must still no-op rather
    // than clear filters and reset pagination for no navigation at all.
    function selectKind(nextKind: BrowseKind) {
        if (nextKind === kind) return
        setFilters((current) => clearInapplicableFilters(nextKind, current))
        setOffset(0)
    }

    function updateFilters(patch: Partial<BrowseFilters>) {
        setFilters((current) => ({ ...current, ...patch }))
        setOffset(0)
    }

    const state = useBrowse(kind, filters, offset, PAGE_SIZE)
    const content = BROWSE_KIND_CONTENT[kind]

    return (
        <section className="browse-page">
            {/* Per-kind trail end, not the literal string "Browse" on all
                four paths -- a breadcrumb is supposed to say where you ARE,
                and "Browse" said the same thing regardless of which of the
                four archives `location.pathname` actually named. */}
            <nav aria-label="Breadcrumb" className="record-breadcrumbs">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">{content.breadcrumbLabel}</span>
            </nav>
            <PageShell>
            {/* Each kind's own identity now (`BROWSE_KIND_CONTENT`,
                `api/browseApi.ts`) -- was the SAME "Browse the archive" /
                "Archive index" / "Choose what to browse, then narrow it
                down…" on all four paths, which is the exact complaint the
                owner raised three times ("Why is the browse reactions with
                the species/transition/vanderwaals browsing page?"). The
                heading now names the kind directly, and the intro states
                what this specific index holds -- including, for species/
                vdw, the one thing it deliberately does NOT (they hit the
                same `/species/browse` endpoint under the hood, a distinction
                no reader can see from the URL alone). */}
            <header className="browse-header">
                <p className="eyebrow">{content.eyebrow}</p>
                <h1>{content.heading}</h1>
                <p className="browse-intro">{content.intro}</p>
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
    if (state.status === "rate-limited") {
        return (
            <p className="browse-status" role="alert">
                The archive is receiving too many requests right now. Wait {formatWaitSeconds(state.retryAfterSeconds)} and reload the page.
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
            {result.kind === "transition_state" && result.records.map((record) => (
                <TransitionStateBrowseRow key={record.transition_state_entry.transition_state_entry_ref} record={record} />
            ))}
            {result.kind === "reaction" && result.records.map((record) => (
                <ReactionBrowseRow key={record.reaction_entry_ref} record={record} />
            ))}
            {(result.kind === "species" || result.kind === "vdw") && result.records.map((record) => (
                <SpeciesBrowseRow key={record.species_ref} record={record} />
            ))}
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
