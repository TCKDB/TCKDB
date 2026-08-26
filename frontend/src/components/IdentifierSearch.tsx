import { useEffect, useRef, useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { searchSpeciesExact } from "../api/scientificApi"
import { classifyIdentifier, resultPath } from "../domain/recordModel"

export function IdentifierSearch() {
    const navigate = useNavigate()
    const [query, setQuery] = useState("")
    const [message, setMessage] = useState<string | null>(null)
    const [matches, setMatches] = useState<Awaited<ReturnType<typeof searchSpeciesExact>>>([])
    const [isSearching, setIsSearching] = useState(false)
    const activeRequest = useRef<AbortController | null>(null)
    const mounted = useRef(true)

    useEffect(() => () => { mounted.current = false; activeRequest.current?.abort() }, [])

    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const classified = classifyIdentifier(query)
        activeRequest.current?.abort()
        if (!classified.valid) { setMatches([]); setMessage(classified.message); return }
        const controller = new AbortController()
        activeRequest.current = controller
        setMatches([]); setMessage(null); setIsSearching(true)
        try {
            const matches = await searchSpeciesExact(classified.identifier, controller.signal)
            if (!mounted.current || controller.signal.aborted) return
            if (matches.length === 0) setMessage(`No exact ${classified.label} record was found.`)
            else if (classified.identifier.kind === "species-ref" || classified.identifier.kind === "species-entry-ref") {
                navigate(resultPath(matches[0]))
            } else setMatches(matches)
        } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") return
            setMessage("The archive could not complete that search. Check the identifier and try again.")
        } finally { if (mounted.current && activeRequest.current === controller) setIsSearching(false) }
    }

    return <form className="identifier-search" onSubmit={submit} noValidate>
        <label htmlFor="identifier">Exact species identifier</label>
        <div className="search-row">
            <span aria-hidden="true">⌕</span>
            <input id="identifier" value={query} onChange={(event) => setQuery(event.target.value)}
                placeholder="Formula, spec_/se_ ref, SMILES, InChI, or InChIKey" autoComplete="off" />
            <button type="submit" aria-busy={isSearching}>Search</button>
        </div>
        <p className="search-help">Exact only · no common-name or external resolver lookup</p>
        {message && <p className="search-message" role="status">{message}</p>}
        {matches.length > 0 && <section className="search-results" aria-label="Exact search results">
            <h2>Exact matches</h2><ul>{matches.map((match) => <li key={match.entryRef ?? match.speciesRef}>
                <a href={resultPath(match)}>{match.entryRef ?? match.speciesRef}</a>
            </li>)}</ul>
        </section>}
    </form>
}
