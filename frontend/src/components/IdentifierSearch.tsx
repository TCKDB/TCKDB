import { useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { searchSpeciesExact } from "../api/scientificApi"
import { classifyIdentifier, resultPath } from "../domain/recordModel"

export function IdentifierSearch() {
    const navigate = useNavigate()
    const [query, setQuery] = useState("")
    const [message, setMessage] = useState<string | null>(null)
    const [isSearching, setIsSearching] = useState(false)

    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        const classified = classifyIdentifier(query)
        if (!classified.valid) { setMessage(classified.message); return }
        const controller = new AbortController()
        setMessage(null); setIsSearching(true)
        try {
            const matches = await searchSpeciesExact(classified.identifier, controller.signal)
            if (matches.length === 0) setMessage(`No exact ${classified.label} record was found.`)
            else navigate(resultPath(matches[0]))
        } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") return
            setMessage("The archive could not complete that search. Check the identifier and try again.")
        } finally { setIsSearching(false) }
    }

    return <form className="identifier-search" onSubmit={submit} noValidate>
        <label htmlFor="identifier">Exact species identifier</label>
        <div className="search-row">
            <span aria-hidden="true">⌕</span>
            <input id="identifier" value={query} onChange={(event) => setQuery(event.target.value)}
                placeholder="Formula, spec_/se_ ref, SMILES, InChI, or InChIKey" autoComplete="off" />
            <button type="submit" disabled={isSearching}>{isSearching ? "Searching" : "Search"}</button>
        </div>
        <p className="search-help">Exact only · no common-name or external resolver lookup</p>
        {message && <p className="search-message" role="status">{message}</p>}
    </form>
}
