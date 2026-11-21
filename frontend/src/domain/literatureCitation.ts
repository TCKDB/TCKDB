// What a public page may print of a literature record's title and DOI.
//
// Both are TCKDB's to show only when they came from the DOI/ISBN registry
// (`title_source == "resolved_metadata"`). A title or DOI the depositor typed --
// or one whose origin was never recorded (`null`, every row older than the
// column) -- is depositor text and stays off public pages. The citation is then
// the identifier, with a TCKDB phrase saying why there is no title.

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
    /** What to print instead: ISBN (validated server-side), else the TCKDB public ref. Never the DOI. */
    identifier: string | null
    /** The registry-resolved DOI, or `null` when it may not be shown. */
    doi: string | null
}

// A DOI's shape: `10.<registrant>/<suffix>` with no whitespace or markup characters.
const DOI_SHAPE = /^10\.\d{4,9}\/[^\s"<>]+$/

// The server serves `doi` only for a registry-resolved row; the page checks the same condition and the DOI's
// shape again, so a depositor-typed DOI (`10.1/ visit spam.example`) never reaches a public page.
export function publicCitation(lit: CitationLiterature): PublicCitation {
    const resolved = lit.title_source === "resolved_metadata"
    const isbn = lit.isbn?.trim() || null
    const doi = lit.doi?.trim() || null
    return {
        title: resolved && lit.title ? lit.title : null,
        identifier: isbn ? `ISBN ${isbn}` : (lit.literature_ref ?? null),
        doi: resolved && doi !== null && DOI_SHAPE.test(doi) ? doi : null,
    }
}

/** The resolver link for a DOI that `publicCitation` returned. */
export function doiHref(doi: string): string {
    return `https://doi.org/${doi}`
}

/** Plain-string form for places that cannot hold markup (select options). */
export function publicCitationText(lit: CitationLiterature): string | null {
    const c = publicCitation(lit)
    return c.title ?? c.identifier
}
