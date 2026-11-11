import { Link, useLocation } from "react-router-dom"
import { RecordPlaceholder } from "../components/RecordPlaceholder"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

/**
 * Catch-all for any path this router does not recognise -- see
 * `App.tsx`'s trailing wildcard route, and the two narrower wildcards
 * under `/species-entries/:entryRef/*` that route an unknown TAB segment
 * here too, rather than letting `SpeciesEntryPage` silently fall through
 * to its default tab (e.g. `/species-entries/<ref>/single-point`
 * shows this page, not the Geometry tab).
 *
 * This page says plainly that the address is wrong and names it, rather
 * than pretending it resolved to something.
 *
 * Shares `.record-placeholder` with `RecordStatus`'s own `Notice` --
 * both are "the archive did not serve a page here" states, styled the
 * same way.
 */
export default function NotFoundPage() {
    useDocumentTitle("Page not found")
    const location = useLocation()
    const attemptedPath = `${location.pathname}${location.search}${location.hash}`

    return (
        <RecordPlaceholder alert>
            <p className="eyebrow">Not found</p>
            <h1>No page at this address</h1>
            <code>{attemptedPath}</code>
            <p>Nothing in this archive is served at this address. Check the link, or go back to the archive home.</p>
            <p><Link to="/">Go to the archive home</Link></p>
        </RecordPlaceholder>
    )
}
