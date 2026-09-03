import { Link, useLocation } from "react-router-dom"

/**
 * Catch-all for any path this router does not recognise -- see
 * `App.tsx`'s trailing wildcard route, and the two narrower wildcards
 * under `/species-entries/:entryRef/*` that route an unknown TAB segment
 * here too, rather than letting `SpeciesEntryPage` silently fall through
 * to its default tab (finding #12: `/species-entries/<ref>/single-point`
 * used to render the Geometry tab with no signal the URL was wrong).
 *
 * Previously the router's catch-all was `<Navigate to="/" replace />`:
 * an unknown URL rendered the home page with no message at all, so a
 * stale or mistyped link looked exactly like a working site. This page
 * says plainly that the address is wrong and names it, rather than
 * pretending it resolved to something.
 *
 * Shares `.record-placeholder` with `RecordPlaceholderPage` -- both are
 * "the archive did not serve a page here" states, styled the same way.
 */
export default function NotFoundPage() {
    const location = useLocation()
    const attemptedPath = `${location.pathname}${location.search}${location.hash}`

    return (
        <section className="record-placeholder" role="alert">
            <p className="eyebrow">Archive record</p>
            <h1>No page at this address</h1>
            <code>{attemptedPath}</code>
            <p>Nothing in this archive is served at this address. Check the link, or go back to the archive home.</p>
            <p><Link to="/">Go to the archive home</Link></p>
        </section>
    )
}
