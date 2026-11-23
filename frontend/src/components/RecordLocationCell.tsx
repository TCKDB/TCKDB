import { Link } from "react-router-dom"
import { recordTypeWords, type RecordLocation } from "../domain/recordRoute"

/**
 * Screen-reader-only warning that a link opens in a new tab.
 *
 * Links out of the admin pages open in a new tab because the page holds a filter, an offset and half-typed forms that no
 * URL captures. A sighted user sees the tab open; a screen reader user gets no such cue without this.
 */
export function NewTabNote() {
    return <span className="admin-visually-hidden"> (opens in a new tab)</span>
}

/**
 * What a row shows for the record it concerns, and where to see it.
 *
 * Four outcomes, kept apart -- see `resolveRecordLocation` in `domain/recordRoute.ts`.
 */
export function RecordLocationCell({ location }: { location: RecordLocation }) {
    switch (location.kind) {
        case "record":
            return (
                <Link to={location.href} className="data" target="_blank" rel="noopener noreferrer">
                    {location.ref}
                    <NewTabNote />
                </Link>
            )
        case "container":
            return (
                <>
                    {/* The record's own name first, when it has one, so the record stays distinguishable from the thing
                        it is shown inside. */}
                    {location.ref !== null ? (
                        <span className="data">{location.ref}</span>
                    ) : (
                        <span className="admin-absent">(no reference of its own)</span>
                    )}{" "}
                    <span className="admin-absent" aria-hidden="true">&middot;</span>{" "}
                    <Link to={location.href} className="review-container-link" target="_blank" rel="noopener noreferrer">
                        shown on {recordTypeWords(location.containerType)}{" "}
                        <span className="data">{location.containerRef}</span>
                        <NewTabNote />
                    </Link>
                </>
            )
        case "no-page":
            // A record deleted between the two reads that name it and its container lands here, as does a type with no
            // page whose container is also unnamed.
            return (
                <>
                    <span className="data">{location.ref}</span>{" "}
                    <span className="admin-absent">
                        no page for this record type, and nothing it is shown on either
                    </span>
                </>
            )
        case "unnamed":
            return <span className="admin-absent">the archive has no reference for this record</span>
    }
}
