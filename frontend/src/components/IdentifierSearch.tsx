import { useEffect, useRef, useState, type FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"
import { searchSpeciesExact } from "../api/scientificApi"
import { classifyIdentifier, resultPath, type IdentifierClassification } from "../domain/recordModel"

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
            setMessage("The archive could not complete that search. Check the identifier and try again.")
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
                placeholder="Formula, spc_/spe_ ref, SMILES, InChI, or InChIKey" autoComplete="off" />
            <button type="submit" aria-busy={isSearching}>Search</button>
        </div>
        <p className="search-help">Exact only · no common-name or external resolver lookup</p>
        {message && <p className="search-message" role="status">{message}</p>}
        {ambiguousInput && <fieldset className="identifier-choice">
            <legend>Search “{ambiguousInput}” as</legend>
            <button type="button" onClick={() => chooseAmbiguous("formula")}>Formula</button>
            <button type="button" onClick={() => chooseAmbiguous("smiles")}>SMILES</button>
        </fieldset>}
        {matches.length > 0 && <section className="search-results" aria-label="Exact search results">
            <h2>Exact matches</h2><ul>{matches.map((match) => <li key={match.entryRef ?? match.speciesRef}>
                <Link to={resultPath(match)}>{match.entryRef ?? match.speciesRef}</Link>
            </li>)}</ul>
        </section>}
    </form>
}
