// What a public page may print of a literature record's title.
//
// Owner decision 2026-11-03: a title is TCKDB's to show only when it came from
// the DOI/ISBN registry (`title_source == "resolved_metadata"`). A title the
// depositor typed -- or one whose origin was never recorded (`null`, every row
// older than the column) -- is depositor text and stays off public pages. The
// citation is then the identifier, with a TCKDB phrase saying why there is no
// title.

export type CitationLiterature = {
    literature_ref?: string | null
    title?: string | null
    title_source?: string | null
    doi?: string | null
    isbn?: string | null
}

export const TITLE_NOT_VERIFIED = "Title not verified"

export interface PublicCitation {
    /** The registry-resolved title, or `null` when it may not be shown. */
    title: string | null
    /** Identifier text to show instead: DOI, else ISBN, else the public ref. */
    identifier: string | null
    /** A link target for the identifier, when one exists (DOIs only). */
    href: string | null
}

export function publicCitation(lit: CitationLiterature): PublicCitation {
    const verified = lit.title_source === "resolved_metadata" && !!lit.title
    const doi = lit.doi?.trim() || null
    const isbn = lit.isbn?.trim() || null
    return {
        title: verified ? (lit.title as string) : null,
        identifier: doi ? `doi:${doi}` : isbn ? `ISBN ${isbn}` : (lit.literature_ref ?? null),
        href: doi ? `https://doi.org/${encodeURI(doi)}` : null,
    }
}

/** Plain-string form for places that cannot hold a link (select options). */
export function publicCitationText(lit: CitationLiterature): string | null {
    const c = publicCitation(lit)
    return c.title ?? c.identifier
}
