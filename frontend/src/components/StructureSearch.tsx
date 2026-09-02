import { useState, type FormEvent } from "react"
import { Link } from "react-router-dom"
import { ScientificApiError, searchStructure, type StructureSearchMode, type StructureSearchRecord } from "../api/structureSearchApi"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"

// Mirrors `BrowseFilterForm`'s own list and labelling convention
// (`frontend/src/components/BrowseFilterForm.tsx`) rather than inventing a
// second one -- a reader who has used the browse page's review filter
// already knows what these mean.
const REVIEW_STATUSES = ["not_reviewed", "under_review", "approved", "deprecated", "rejected"]
const DEFAULT_SIMILARITY_THRESHOLD = 0.5

function token(value: string): string {
    return value.replaceAll("_", " ")
}

type RanSearch = {
    records: StructureSearchRecord[]
    total: number
    mode: StructureSearchMode
    threshold: number | null
    query: string
    isSmarts: boolean
}

type SearchState =
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "invalid"; message: string }
    | { kind: "error"; message: string }
    | ({ kind: "results" } & RanSearch)

/**
 * UI for the two RDKit-cartridge search modes `IdentifierSearch` never
 * exposed: `substructure` (accepts SMILES or SMARTS) and `similarity`
 * (Tanimoto over Morgan-bit fingerprints, with a caller-set threshold).
 * `exact` mode stays `IdentifierSearch`'s job -- see that component's own
 * doc comment and `scientificApi.ts`'s `searchSpeciesExact`.
 *
 * Every result is labelled with the mode that produced it (never left
 * implicit) because a substructure hit and a similarity hit mean
 * different things about how close the match actually is -- see the
 * design brief this component was built from.
 */
export function StructureSearch() {
    const [mode, setMode] = useState<StructureSearchMode>("substructure")
    const [query, setQuery] = useState("")
    const [isSmarts, setIsSmarts] = useState(false)
    const [threshold, setThreshold] = useState(DEFAULT_SIMILARITY_THRESHOLD)
    const [minReviewStatus, setMinReviewStatus] = useState("")
    const [includeRejected, setIncludeRejected] = useState(false)
    const [includeDeprecated, setIncludeDeprecated] = useState(false)
    const [state, setState] = useState<SearchState>({ kind: "idle" })

    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const value = query.trim()
        const smarts = mode === "substructure" && isSmarts
        if (!value) {
            setState({ kind: "invalid", message: `Enter a ${smarts ? "SMARTS pattern" : "SMILES string"} to search.` })
            return
        }
        setState({ kind: "loading" })
        try {
            const result = await searchStructure({
                query: smarts ? { queryKind: "smarts", value } : { queryKind: "smiles", value },
                mode,
                similarityThreshold: mode === "similarity" ? threshold : undefined,
                minReviewStatus: minReviewStatus || undefined,
                includeRejected,
                includeDeprecated,
            })
            setState({
                kind: "results",
                records: result.records,
                total: result.total,
                mode,
                threshold: mode === "similarity" ? threshold : null,
                query: value,
                isSmarts: smarts,
            })
        } catch (error) {
            // Distinct from a resolved zero-record response (`total === 0`
            // under `kind: "results"`): RDKit rejected the query outright,
            // which is a malformed-input fact, not an archive-content fact.
            // See `structure_search.py`'s `invalid_structure_query` raises
            // and `IdentifierSearch`'s matching fix for the same defect.
            if (error instanceof ScientificApiError && error.code === "invalid_structure_query") {
                setState({
                    kind: "invalid",
                    message: `"${value}" could not be parsed as a valid ${smarts ? "SMARTS pattern" : "SMILES structure"} -- check the syntax and try again.`,
                })
            } else {
                setState({ kind: "error", message: "The archive could not complete that search. Check the query and try again." })
            }
        }
    }

    return (
        <section className="structure-search" aria-label="Structure search">
            <h2>Structure search</h2>
            <p className="structure-search-intro">
                Find species by substructure containment or chemical similarity, backed by the RDKit cartridge --
                not an exact-identifier lookup. Use <Link to="/">exact species identifier</Link> search for that.
            </p>
            <form className="structure-search-form" onSubmit={submit} noValidate>
                <fieldset className="structure-mode-fieldset">
                    <legend>Search mode</legend>
                    <label>
                        <input
                            checked={mode === "substructure"}
                            name="structure-search-mode"
                            onChange={() => setMode("substructure")}
                            type="radio"
                            value="substructure"
                        />
                        Substructure
                    </label>
                    <label>
                        <input
                            checked={mode === "similarity"}
                            name="structure-search-mode"
                            onChange={() => setMode("similarity")}
                            type="radio"
                            value="similarity"
                        />
                        Similarity
                    </label>
                </fieldset>

                <div className="structure-search-field">
                    <label htmlFor="structure-search-query">{isSmarts && mode === "substructure" ? "SMARTS pattern" : "SMILES"}</label>
                    <input
                        autoComplete="off"
                        id="structure-search-query"
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder={isSmarts && mode === "substructure" ? "[#6]-[#8]" : "CCO"}
                        value={query}
                    />
                </div>

                {mode === "substructure" && (
                    <label className="structure-search-checkbox">
                        <input checked={isSmarts} onChange={(event) => setIsSmarts(event.target.checked)} type="checkbox" />
                        Treat the query as SMARTS, not SMILES
                    </label>
                )}

                {mode === "similarity" && (
                    <div className="structure-search-field">
                        <label htmlFor="structure-search-threshold">Similarity threshold (Tanimoto, 0.0-1.0)</label>
                        <input
                            id="structure-search-threshold"
                            max={1}
                            min={0}
                            onChange={(event) => setThreshold(Number(event.target.value))}
                            step={0.05}
                            type="number"
                            value={threshold}
                        />
                    </div>
                )}

                <div className="structure-search-field">
                    <label htmlFor="structure-search-min-review">Minimum review status</label>
                    <select
                        id="structure-search-min-review"
                        onChange={(event) => setMinReviewStatus(event.target.value)}
                        value={minReviewStatus}
                    >
                        <option value="">Any</option>
                        {REVIEW_STATUSES.map((status) => <option key={status} value={status}>{token(status)}</option>)}
                    </select>
                </div>
                <label className="structure-search-checkbox">
                    <input checked={includeRejected} onChange={(event) => setIncludeRejected(event.target.checked)} type="checkbox" />
                    Include rejected
                </label>
                <label className="structure-search-checkbox">
                    <input checked={includeDeprecated} onChange={(event) => setIncludeDeprecated(event.target.checked)} type="checkbox" />
                    Include deprecated
                </label>

                {/* Distinct accessible name from `IdentifierSearch`'s own "Search"
                    button -- both mount on the same home page (`ArchiveHomePage`),
                    and a shared name would make every `getByRole("button", {
                    name: "Search" })` query in that page's tests ambiguous. */}
                <button aria-busy={state.kind === "loading"} type="submit">Search structures</button>
            </form>
            <StructureSearchOutput state={state} />
        </section>
    )
}

function StructureSearchOutput({ state }: { state: SearchState }) {
    if (state.kind === "idle") return null
    if (state.kind === "loading") return <p aria-busy="true" className="structure-search-status" role="status">Searching…</p>
    if (state.kind === "invalid") return <p className="structure-search-status structure-search-invalid" role="alert">{state.message}</p>
    if (state.kind === "error") return <p className="structure-search-status" role="alert">{state.message}</p>

    const { records, total, mode, threshold, query, isSmarts } = state
    const modeLabel = mode === "substructure" ? "Substructure search" : "Similarity search"

    return (
        <section aria-label="Structure search results" className="structure-search-results">
            <p className="structure-search-summary" role="status">
                <strong>{modeLabel}</strong> for <code>{query}</code>{isSmarts && " (SMARTS)"}
                {mode === "similarity" && threshold !== null && <> · threshold ≥ {threshold.toFixed(2)}</>}
                {" · "}{total} {total === 1 ? "match" : "matches"}
            </p>
            {records.length === 0
                ? <p className="structure-search-empty">No {mode} matches were found for this query.</p>
                : <ul className="structure-search-list">
                    {records.map((record) => <StructureSearchRow key={record.entryRef} record={record} />)}
                </ul>}
        </section>
    )
}

function StructureSearchRow({ record }: { record: StructureSearchRecord }) {
    return (
        <li className="structure-search-row">
            <Link className="structure-search-row-link" to={`/species-entries/${encodeURIComponent(record.entryRef)}`}>
                <span className="structure-search-row-smiles">{record.smiles}</span>
                {" "}
                <span className="structure-search-row-context">
                    charge {chargeDisplay(record.charge)} · spin {spinDisplay(record.multiplicity)}
                </span>
            </Link>
            {record.similarityScore !== null && (
                <span className="structure-search-row-score">Tanimoto {record.similarityScore.toFixed(3)}</span>
            )}
            <span className="structure-search-row-review">{token(record.reviewStatus)}</span>
            <code className="structure-search-row-ref">{record.entryRef}</code>
        </li>
    )
}
