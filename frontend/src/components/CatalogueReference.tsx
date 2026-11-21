import type { CatalogueCitation } from "../api/catalogueApi"
import { doiHref, publicCitation } from "../domain/literatureCitation"
import { authorsText, sourceText } from "../domain/methodCatalogue"
import { LiteratureCitation } from "./LiteratureCitation"

/**
 * One article a catalogue entry cites: authors, optionally the title, then journal, year, volume and pages, with a
 * DOI link only when the server served one for a registry-resolved row. A row the registry did not resolve falls back
 * to `LiteratureCitation` (identifier and "Title not verified"), so no depositor text is printed.
 */
export function CatalogueReference({ reference, withTitle = false }: { reference: CatalogueCitation; withTitle?: boolean }) {
    const citation = publicCitation(reference)
    if (citation.title === null) {
        return <span className="catalogue-reference"><LiteratureCitation literature={reference} /></span>
    }
    const authors = authorsText(reference.authors)
    const source = sourceText(reference)
    return (
        <span className="catalogue-reference">
            {authors && <span className="catalogue-reference-authors">{authors.endsWith(".") ? authors : `${authors}.`} </span>}
            {withTitle && <span className="catalogue-reference-title">{citation.title}. </span>}
            {source && <span className="catalogue-reference-source">{source}.</span>}
            {citation.doi !== null && (
                <>
                    {" "}
                    <a className="catalogue-reference-doi" href={doiHref(citation.doi)} target="_blank" rel="noopener noreferrer">
                        doi:{citation.doi}
                    </a>
                </>
            )}
        </span>
    )
}
