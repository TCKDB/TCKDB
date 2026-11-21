import { doiHref, publicCitation, TITLE_NOT_VERIFIED, type CitationLiterature } from "../domain/literatureCitation"

/**
 * A literature record as a public page may show it: the registry-resolved title with its DOI link, or -- when the
 * title is depositor-typed or of unrecorded origin -- the ISBN or TCKDB public ref and "Title not verified", with
 * no DOI. The year, when given, follows the citation. See `literatureCitation.ts`.
 */
export function LiteratureCitation({ literature }: { literature: CitationLiterature & { year?: number | null } }) {
    const citation = publicCitation(literature)
    const year = literature.year ? ` (${literature.year})` : ""
    if (citation.title !== null) {
        return (
            <>
                {citation.title}{year}
                {citation.doi === null ? null : (
                    <>
                        {" · "}
                        <a href={doiHref(citation.doi)} target="_blank" rel="noopener noreferrer">doi:{citation.doi}</a>
                    </>
                )}
            </>
        )
    }
    return (
        <>
            {citation.identifier === null ? null : <>{citation.identifier}{year}{" · "}</>}
            <span className="record-identity-absent-inline">{TITLE_NOT_VERIFIED}</span>
        </>
    )
}
