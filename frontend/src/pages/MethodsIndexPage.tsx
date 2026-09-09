import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import "../browse.css"
import "../conformer-group.css"
import "../methods.css"
import { loadLevelOfTheoryBrowse, type LevelOfTheoryRecord } from "../api/methodsApi"
import { loadSoftwareNames, loadWorkflowToolNames, type VocabEntry } from "../api/vocabApi"
import { LevelOfTheoryLink } from "../components/LevelOfTheoryLink"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { words } from "../domain/provenanceFormat"

type LoadState<T> = { status: "loading" } | { status: "error" } | { status: "ready"; data: T }

function useLoad<T>(load: (signal: AbortSignal) => Promise<T>): LoadState<T> {
    const [state, setState] = useState<LoadState<T>>({ status: "loading" })
    useEffect(() => {
        let mounted = true
        const controller = new AbortController()
        setState({ status: "loading" })
        load(controller.signal)
            .then((data) => { if (mounted) setState({ status: "ready", data }) })
            .catch((error: unknown) => {
                if (!mounted) return
                if (error instanceof DOMException && error.name === "AbortError") return
                setState({ status: "error" })
            })
        return () => { mounted = false; controller.abort() }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
    return state
}

/**
 * `/methods` -- the archive's provenance-vocabulary index (methods-surface
 * plan §4.1, `plan-methods-surface-v2`, not committed to this repo).
 * Replaces `RecordPlaceholderPage kind="Methods"`. Three sections, each
 * its own `.data-table`: levels of theory (grouped by
 * `level_of_theory_ref`, per ruling 1 in §0 of that plan -- NEVER by
 * method/basis display text, which two distinct levels can share while
 * differing only in dispersion, solvent, or spin treatment), software,
 * and workflow tools -- unchanged in shape from what a bare vocabulary
 * index would show; what changed is where a level-of-theory row links
 * (§4.2's real record page, not a thin anchor).
 */
export default function MethodsIndexPage() {
    const lotState = useLoad(loadLevelOfTheoryBrowse)
    const softwareState = useLoad((signal) => loadSoftwareNames(undefined, signal))
    const workflowToolState = useLoad((signal) => loadWorkflowToolNames(undefined, signal))
    const [lotFilter, setLotFilter] = useState<LevelOfTheoryFilter>(EMPTY_LOT_FILTER)

    return (
        <section className="conformer-page methods-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Methods</span>
            </nav>
            <PageShell>
                <p className="eyebrow">Archive index</p>
                <h1 className="t-display-1">Methods</h1>
                <p className="t-body methods-index-intro">
                    The provenance vocabulary this archive records: the levels of theory, software, and workflow
                    tools attached to at least one deposited calculation. Levels of theory below link to their
                    correction parameters and observed software, where any are deposited.
                </p>

                <section className="ledger-section" aria-labelledby="methods-lot-heading">
                    <SectionHeading
                        id="methods-lot-heading"
                        kicker="Provenance vocabulary"
                        intro="One row per distinct level-of-theory identity — two levels can share a method and basis while differing in dispersion, solvent, or spin treatment, so this table never groups on that display text."
                    >
                        Levels of theory
                    </SectionHeading>
                    {lotState.status === "ready" && lotState.data.records.length > 0 && (
                        <LevelOfTheoryFilterFields filter={lotFilter} onChange={setLotFilter} />
                    )}
                    <LevelOfTheoryTable filter={lotFilter} state={lotState} />
                </section>

                <section className="ledger-section" aria-labelledby="methods-software-heading">
                    <SectionHeading id="methods-software-heading" kicker="Provenance vocabulary" intro="Software packages observed running at least one calculation in this archive.">
                        Software
                    </SectionHeading>
                    <VocabTable state={softwareState} nameLabel="Software" emptyText="No software usage is recorded in this archive." />
                </section>

                <section className="ledger-section" aria-labelledby="methods-workflow-heading">
                    <SectionHeading id="methods-workflow-heading" kicker="Provenance vocabulary" intro="Workflow tools observed orchestrating at least one calculation in this archive.">
                        Workflow tools
                    </SectionHeading>
                    <VocabTable state={workflowToolState} nameLabel="Workflow tool" emptyText="No workflow-tool usage is recorded in this archive." />
                </section>
            </PageShell>
        </section>
    )
}

/**
 * `dispersion`/`solvent` wording (owner: "the not recorded and solvent not
 * recorded ... like it suggests that someone missed it when rather these
 * are records without, so should be like none instead"):
 *
 * MEASURED before choosing a word, not assumed. The upload-facing
 * `LevelOfTheoryRef` fragment (`schemas/python/tckdb-schemas/tckdb_schemas/
 * fragments/refs.py`) declares `dispersion`/`solvent` as plain optional
 * strings with no separate "explicitly none" sentinel distinct from
 * "omitted" -- unlike `spin_treatment` on that SAME fragment, which DR-0034
 * gave a real `unknown` enum member specifically because every calculation
 * genuinely runs at SOME spin treatment even when the depositor does not
 * say which, so `NULL` there could not honestly mean "there is none".
 * Dispersion correction and an implicit solvent model are the opposite
 * case: both are OPTIONAL method choices a calculation can genuinely run
 * without, so DR-0034's authors adding a disambiguating enum for spin but
 * not for these two fields is itself evidence "none" was already
 * considered the unambiguous reading here. `calculation_resolution.py`'s
 * `resolve_level_of_theory_ref` writes `ref.dispersion`/`ref.solvent`
 * straight into the row with no fallback/defaulting logic, matching that.
 * All four levels of theory live on the archive today (measured live,
 * 2026-09-09) are ab initio/DFT gas-phase kinetics levels -- CCSD(T)-F12,
 * MRCI+Davidson, b3lyp -- with `dispersion: null, solvent: null` on every
 * one, consistent with "no correction / vacuum" rather than "forgotten".
 * "None" states the dispersion fact plainly; "gas phase" is the more
 * informative, equally factual reading for an absent solvent (no implicit
 * solvent model means the calculation ran in vacuum) -- chosen over a
 * second "none" so a reader gets the chemistry, not just the negation.
 * `basis` is UNCHANGED here: a method that has no basis set is a real,
 * separate possibility (semi-empirical methods) this brief was not asked
 * to adjudicate, so it keeps the plain absence word.
 */
const NO_DISPERSION_TEXT = "none"
const NO_SOLVENT_TEXT = "gas phase"

type LevelOfTheoryFilter = {
    query: string
    hasCorrectionSchemes: boolean
    hasFrequencyScaleFactors: boolean
}

const EMPTY_LOT_FILTER: LevelOfTheoryFilter = { query: "", hasCorrectionSchemes: false, hasFrequencyScaleFactors: false }

function isLotFilterActive(filter: LevelOfTheoryFilter): boolean {
    return filter.query.trim() !== "" || filter.hasCorrectionSchemes || filter.hasFrequencyScaleFactors
}

/**
 * Narrows the already-fetched rows in the browser rather than re-querying
 * `/level-of-theories/browse` per keystroke. MEASURED before designing:
 * that endpoint's `method`/`basis` filters are exact-match, not substring
 * (`_run_lot_query`, `backend/app/services/scientific_read/level_of_theory_
 * search.py`: `LevelOfTheory.method == request.method`) -- sending
 * partially-typed text as `method=` would silently return zero rows for
 * almost any real query (typing "wb97" would never match a stored
 * "wb97xd"), which is worse than no filter. `loadLevelOfTheoryBrowse`
 * already fetches the WHOLE usage-derived candidate set in one unfiltered
 * call (`limit=200`; this index "does not paginate" by its own design), so
 * every row this filter could ever narrow is already sitting in `records`
 * -- filtering client-side is both more correct (real substring matching)
 * and simpler than a network round trip for an archive with four rows.
 *
 * `hasCorrectionSchemes`/`hasFrequencyScaleFactors` mirror the SAME two
 * boolean params the browse endpoint accepts
 * (`has_correction_schemes`/`has_frequency_scale_factors`), applied here to
 * the identical `evidence_summary` booleans the fetch already returned --
 * so this predicate is provably what asking the server would answer, just
 * evaluated against data already in hand. Left out: `level_of_theory_ref`
 * (an internal id, not something a reader searches by), `dispersion`/
 * `solvent`/`spin_treatment` (every row on the live archive carries the
 * same null value for all three today -- a facet with one possible value
 * narrows nothing and just adds chrome), and `lot_hash` (an opaque digest,
 * never a reader-facing search key anywhere else in this app).
 */
function filterLevelOfTheoryRecords(records: LevelOfTheoryRecord[], filter: LevelOfTheoryFilter): LevelOfTheoryRecord[] {
    const query = filter.query.trim().toLowerCase()
    return records.filter((record) => {
        if (filter.hasCorrectionSchemes && !record.evidence_summary.has_correction_schemes) return false
        if (filter.hasFrequencyScaleFactors && !record.evidence_summary.has_frequency_scale_factors) return false
        if (query) {
            const method = record.level_of_theory.method.toLowerCase()
            const basis = (record.level_of_theory.basis ?? "").toLowerCase()
            if (!method.includes(query) && !basis.includes(query)) return false
        }
        return true
    })
}

/**
 * Proportionate to what four rows need (owner: "i do wonder the bigger the
 * LoT, how easy is it to search?"): one text field narrowing on method and
 * basis together, plus the two evidence facets that are genuinely useful
 * for finding which levels carry parameters -- not a full faceted-search
 * panel `BrowseFilterForm.tsx` builds for the archive's much larger
 * species/reaction/TS catalogues. This is a small, page-local control
 * rather than a reuse of that component: its fields are module-private
 * (only `BrowseFilterForm` itself is exported), and this filter's shape
 * (one text input, two checkboxes, no vocab fetch, no debounce) does not
 * need that component's parent-scoped-version/inline-explain machinery.
 * Reuses `browse.css`'s `.browse-filter-*` classes for a consistent look
 * rather than inventing a second filter-panel style.
 */
function LevelOfTheoryFilterFields({ filter, onChange }: {
    filter: LevelOfTheoryFilter
    onChange: (filter: LevelOfTheoryFilter) => void
}) {
    return (
        <div className="browse-filters">
            <div className="browse-filter-grid">
                <div className="browse-filter-field">
                    <label htmlFor="methods-lot-filter-query">Method or basis</label>
                    <input
                        id="methods-lot-filter-query"
                        onChange={(event) => onChange({ ...filter, query: event.target.value })}
                        placeholder="b3lyp, def2tzvp…"
                        type="text"
                        value={filter.query}
                    />
                </div>
                <fieldset className="browse-filter-evidence-group">
                    <legend>Show only levels with…</legend>
                    <div className="browse-filter-evidence-checks">
                        <label className="browse-filter-evidence-check">
                            <input
                                checked={filter.hasCorrectionSchemes}
                                onChange={(event) => onChange({ ...filter, hasCorrectionSchemes: event.target.checked })}
                                type="checkbox"
                            />
                            correction schemes
                        </label>
                        <label className="browse-filter-evidence-check">
                            <input
                                checked={filter.hasFrequencyScaleFactors}
                                onChange={(event) => onChange({ ...filter, hasFrequencyScaleFactors: event.target.checked })}
                                type="checkbox"
                            />
                            frequency scale factors
                        </label>
                    </div>
                </fieldset>
            </div>
        </div>
    )
}

function LevelOfTheoryTable({ state, filter }: { state: LoadState<{ records: LevelOfTheoryRecord[] }>; filter: LevelOfTheoryFilter }) {
    // Computed unconditionally, before any status-based early return below,
    // so this hook's call order never changes across renders.
    const records = useMemo(
        () => filterLevelOfTheoryRecords(state.status === "ready" ? state.data.records : [], filter),
        [state, filter],
    )

    if (state.status === "loading") return <p className="note" role="status">Loading levels of theory…</p>
    if (state.status === "error") return <p className="empty-projection" role="alert">The archive service could not load this list. Try again later.</p>
    if (state.data.records.length === 0) return <p className="empty-projection">No levels of theory have been deposited in this archive yet.</p>
    if (records.length === 0 && isLotFilterActive(filter)) {
        return <p className="empty-projection">No levels of theory match this filter.</p>
    }
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label="Levels of theory">
                <thead>
                    <tr>
                        <th scope="col">Level of theory</th>
                        <th scope="col">Dispersion</th>
                        <th scope="col">Solvent</th>
                        <th scope="col">Calculations</th>
                    </tr>
                </thead>
                <tbody>
                    {records.map((record) => (
                        <tr key={record.level_of_theory.level_of_theory_ref}>
                            {/* Both method AND basis are now inside the ONE link this row
                                represents (owner: "the highlighting of the Method but not
                                basis is weird" -- only Method used to be a `<Link>` while
                                Basis sat beside it as plain text, reading as though the
                                method alone were the record). `LevelOfTheoryLink` is the
                                app-wide component every OTHER surface already uses to
                                render a level of theory as one "method/basis" anchor into
                                `/methods/:lotRef` (`SourceCalculationsTable.tsx`,
                                `ProductLevels.tsx`, `CalculationDetailPage.tsx`, ...) --
                                this index was the one place still hand-rolling a
                                Method-only `<Link>` beside plain Basis text instead of
                                reusing it. A single `<a>` per row (never two, never
                                nested) keeps this a table-appropriate fix rather than
                                porting `SpeciesBrowseRow`/`ReactionBrowseRow`'s full-card
                                stretched-link mechanic, which those components need
                                because a `<li class="card">` has many other clickable-
                                looking children to disambiguate from; a `<td>` does not. */}
                            <td data-label="Level of theory">
                                <LevelOfTheoryLink levelOfTheory={record.level_of_theory} />
                            </td>
                            <td data-label="Dispersion">{record.level_of_theory.dispersion ?? NO_DISPERSION_TEXT}</td>
                            <td data-label="Solvent">{record.level_of_theory.solvent ?? NO_SOLVENT_TEXT}</td>
                            <td data-label="Calculations" className="num">{record.evidence_summary.calculation_usage_count}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}

function VocabTable({ state, nameLabel, emptyText }: { state: LoadState<VocabEntry[]>; nameLabel: string; emptyText: string }) {
    if (state.status === "loading") return <p className="note" role="status">Loading…</p>
    if (state.status === "error") return <p className="empty-projection" role="alert">The archive service could not load this list. Try again later.</p>
    const rows = state.data
    if (rows.length === 0) return <p className="empty-projection">{emptyText}</p>
    return (
        <div className="table-scroll">
            <table className="data-table" aria-label={nameLabel}>
                <thead>
                    <tr>
                        <th scope="col">{nameLabel}</th>
                        <th scope="col">Calculations</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={row.value}>
                            <td data-label={nameLabel}>{row.display_name ?? words(row.value)}</td>
                            <td data-label="Calculations" className="num">{row.count}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    )
}
