// What a public page may print of a literature record's title.
//
// A title is TCKDB's to show only when it came from
// the DOI/ISBN registry (`title_source == "resolved_metadata"`). A title the
// depositor typed -- or one whose origin was never recorded (`null`, every row
// older than the column) -- is depositor text and stays off public pages. The
// citation is then the identifier, with a TCKDB phrase saying why there is no
// title.

export type CitationLiterature = {
    literature_ref?: string | null
    title?: string | null
    title_source?: string | null
    doi?: string | null // accepted so callers can pass a whole record; deliberately never printed
    isbn?: string | null
}

export const TITLE_NOT_VERIFIED = "Title not verified"

export interface PublicCitation {
    /** The registry-resolved title, or `null` when it may not be shown. */
    title: string | null
    /** What to print instead: ISBN (validated server-side), else the TCKDB public ref. Never the DOI. */
    identifier: string | null
}

// A DOI is deliberately not an option here: the server only trims and lower-cases it, so it is depositor-typed
// text (`10.1/ visit spam.example`) and, like a typed title, must not reach a public page unverified.
export function publicCitation(lit: CitationLiterature): PublicCitation {
    const verified = lit.title_source === "resolved_metadata" && !!lit.title
    const isbn = lit.isbn?.trim() || null
    return {
        title: verified ? (lit.title as string) : null,
        identifier: isbn ? `ISBN ${isbn}` : (lit.literature_ref ?? null),
    }
}

/** Plain-string form for places that cannot hold markup (select options). */
export function publicCitationText(lit: CitationLiterature): string | null {
    const c = publicCitation(lit)
    return c.title ?? c.identifier
}
