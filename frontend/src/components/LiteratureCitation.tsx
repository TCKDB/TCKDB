import { publicCitation, TITLE_NOT_VERIFIED, type CitationLiterature } from "../domain/literatureCitation"

/**
 * A literature record as a public page may show it: the registry-resolved title,
 * or -- when the title is depositor-typed or of unrecorded origin -- the DOI/ISBN
 * (a link where one exists) and "Title not verified". See `literatureCitation.ts`.
 */
export function LiteratureCitation({ literature }: { literature: CitationLiterature }) {
    const citation = publicCitation(literature)
    if (citation.title !== null) return <>{citation.title}</>
    return (
        <>
            {citation.identifier === null ? null : citation.href === null ? (
                citation.identifier
            ) : (
                <a href={citation.href} target="_blank" rel="noreferrer noopener">{citation.identifier}</a>
            )}
            {" "}
            <span className="record-identity-absent-inline">({TITLE_NOT_VERIFIED})</span>
        </>
    )
}
