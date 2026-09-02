import { useEffect, useRef, useState, type FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ScientificApiError, searchSpeciesExact, type SearchMatch } from "../api/scientificApi"
import { classifyIdentifier, resultPath, type IdentifierClassification } from "../domain/recordModel"
import { chargeDisplay, entryCountDisplay, spinDisplay } from "../domain/chemistryFormat"
import { Formula } from "./Formula"
import { SectionErrorBoundary } from "./SectionErrorBoundary"

export function IdentifierSearch() {
    const navigate = useNavigate()
    const [query, setQuery] = useState("")
    const [message, setMessage] = useState<string | null>(null)
    const [ambiguousInput, setAmbiguousInput] = useState<string | null>(null)
    const [matches, setMatches] = useState<Awaited<ReturnType<typeof searchSpeciesExact>>>([])
    const [isSearching, setIsSearching] = useState(false)
    const activeRequest = useRef<AbortController | null>(null)

    useEffect(() => () => { activeRequest.current?.abort(); activeRequest.current = null }, [])

    function abortActiveRequest() {
        activeRequest.current?.abort()
        activeRequest.current = null
        setIsSearching(false)
    }

    async function runSearch(classified: Extract<IdentifierClassification, { valid: true }>) {
        abortActiveRequest()
        const controller = new AbortController()
        activeRequest.current = controller
        setMatches([]); setMessage(null); setAmbiguousInput(null); setIsSearching(true)
        try {
            const matches = await searchSpeciesExact(classified.identifier, controller.signal)
            if (activeRequest.current !== controller || controller.signal.aborted) return
            if (matches.length === 0) setMessage(`No exact ${classified.label} record was found.`)
            else if (classified.identifier.kind === "species-ref" || classified.identifier.kind === "species-entry-ref") {
                navigate(resultPath(matches[0]))
            } else setMatches(matches)
        } catch (error) {
            if (activeRequest.current !== controller || controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return
            // An unparseable structure query (RDKit rejected the SMILES/InChI)
            // is a DIFFERENT fact from "the archive was searched and holds no
            // such record" -- the former says the input itself is malformed,
            // the latter says the input was understood and came up empty.
            // Collapsing them into one generic message would tell a chemist
            // their syntactically bad SMILES "was not found", which reads as
            // "this molecule is absent from the archive" -- exactly the wrong
            // answer this fix exists to stop giving. The archive's own `code`
            // (`app/api/error_contract.py`) distinguishes the two; see
            // `structure_search.py`'s `invalid_structure_query` raises.
            if (error instanceof ScientificApiError && error.code === "invalid_structure_query") {
                setMessage(`"${classified.identifier.value}" could not be parsed as a valid ${classified.label} -- check the syntax and try again.`)
            } else {
                setMessage("The archive could not complete that search. Check the identifier and try again.")
            }
        } finally { if (activeRequest.current === controller && !controller.signal.aborted) setIsSearching(false) }
    }

    function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const classified = classifyIdentifier(query)
        abortActiveRequest()
        if (!classified.valid) {
            setMatches([]); setMessage(classified.message); setAmbiguousInput(classified.ambiguousValue ?? null)
            return
        }
        void runSearch(classified)
    }

    function chooseAmbiguous(kind: "formula" | "smiles") {
        const value = query.trim()
        if (!ambiguousInput || value !== ambiguousInput) return
        const choice = classifyIdentifier(`${kind}:${value}`)
        if (choice.valid) void runSearch(choice)
    }

    return <form className="identifier-search" onSubmit={submit} noValidate>
        <label htmlFor="identifier">Exact species identifier</label>
        <div className="search-row">
            <span aria-hidden="true">⌕</span>
            <input id="identifier" value={query} onChange={(event) => {
                setQuery(event.target.value); setMessage(null); setAmbiguousInput(null)
            }}
                placeholder="SMILES, formula, spc_/spe_ ref, InChI, or InChIKey" autoComplete="off" />
            <button type="submit" aria-busy={isSearching}>Search</button>
        </div>
        <p className="search-help">Exact only · no common-name or external resolver lookup</p>
        {message && <p className="search-message" role="status">{message}</p>}
        {ambiguousInput && <fieldset className="identifier-choice">
            <legend>Search “{ambiguousInput}” as</legend>
            {/* SMILES leads: a structure string is the identifier a chemist
                reaches for first, and this archive is searched by structure far
                more often than by formula. Order is presentation only -- an
                ambiguous value is still ASKED about rather than resolved to
                either kind, because guessing would silently search for the
                wrong thing. */}
            <button type="button" onClick={() => chooseAmbiguous("smiles")}>SMILES</button>
            <button type="button" onClick={() => chooseAmbiguous("formula")}>Formula</button>
        </fieldset>}
        {matches.length > 0 && <section className="search-results" aria-label="Exact search results">
            <h2>Exact matches</h2>
            <ul>{matches.map((match) => {
                const ref = match.entryRef ?? match.speciesRef
                return <li className="search-result" key={ref}>
                    <SectionErrorBoundary fallback={<FallbackRow match={match} />}>
                        <MatchRow match={match} />
                    </SectionErrorBoundary>
                </li>
            })}</ul>
        </section>}
    </form>
}

/**
 * A chemist scanning this list must be able to tell two matches apart by
 * their chemistry -- formula and SMILES carry the row, charge/spin/entry
 * count give context -- without decoding a base32 public reference. The
 * reference stays on the row (visible, selectable, in monospace) but
 * demoted below the chemistry, never standing in for it. See
 * `docs/plans/provenance-first-website.md`'s "stable public references
 * remain visible and copyable" and the sibling defect this project already
 * caught in the opposite direction: a label-or-ref fallback where a
 * present label made a real ref appear nowhere on the page. Neither
 * failure is acceptable, so both stay checked by
 * `IdentifierSearch.test.tsx`.
 */
function MatchRow({ match }: { match: SearchMatch }) {
    const ref = match.entryRef ?? match.speciesRef
    const context = [chargeContext(match), spinContext(match)]
    if (match.entryCount !== undefined) context.push(entryCountDisplay(match.entryCount))
    return <>
        <Link className="search-result-link" to={resultPath(match)}>
            <MatchHeadline match={match} />
            {" "}
            <span className="search-result-context">{context.join(" · ")}</span>
        </Link>
        <code className="search-result-ref">{ref}</code>
    </>
}

function chargeContext(match: SearchMatch) {
    return `charge ${chargeDisplay(match.charge)}`
}

function spinContext(match: SearchMatch) {
    return `spin ${spinDisplay(match.multiplicity)}`
}

/**
 * The row's headline. Formula, typeset with subscripts, leads when the
 * archive computed one; SMILES always follows it, because two isomers can
 * share a formula and only the structure string tells them apart.
 *
 * `formula` is nullable (#251: it is computed, and can legitimately come
 * back null) and structure-search matches never carry one at all -- see
 * `scientificApi.ts`'s `SearchMatch.formula`. Either way this says so
 * explicitly rather than leaving a blank where the formula would be, and
 * never falls back to the public reference: that fallback is the exact
 * defect this component exists to fix.
 */
function MatchHeadline({ match }: { match: SearchMatch }) {
    if (match.formula) {
        return <span className="search-result-headline">
            <span className="search-result-formula"><Formula value={match.formula} /></span>
            {match.smiles && <>{" "}<span className="search-result-smiles">{match.smiles}</span></>}
        </span>
    }
    if (match.smiles) {
        return <span className="search-result-headline">
            <span className="search-result-smiles search-result-smiles-primary">{match.smiles}</span>
            {" "}
            <span className="search-result-formula-missing">formula not available</span>
        </span>
    }
    // Neither a formula nor a SMILES is known for this match -- there is
    // nothing chemistry-shaped left to lead with. Showing the reference
    // here is honest (there genuinely is no chemistry data), which is a
    // different case from the defect this component fixes: that defect was
    // showing the reference *instead of* chemistry that existed.
    return <span className="search-result-headline search-result-headline-fallback">{match.entryRef ?? match.speciesRef}</span>
}

function FallbackRow({ match }: { match: SearchMatch }) {
    return <Link className="search-result-link" to={resultPath(match)}>{match.entryRef ?? match.speciesRef}</Link>
}
