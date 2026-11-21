import { Link } from "react-router-dom"
import type { CatalogueCard as CatalogueCardRecord } from "../api/catalogueApi"
import { cardFamilyText, cataloguePath, isMethodCard, referencesByRole, usageText } from "../domain/methodCatalogue"
import { CatalogueReference } from "./CatalogueReference"

/**
 * "About this method": one catalogue entry as the catalogue states it -- names, family, definition, the articles that
 * define it, the spellings seen and its usage -- with a link to the entry's own page. Nothing here comes from any
 * other record.
 */
export function CatalogueCard({ card, noun, headingId }: { card: CatalogueCardRecord; noun: string; headingId: string }) {
    const groups = referencesByRole(card.references)
    const entryNoun = isMethodCard(card) ? (card.kind === "dispersion_correction" ? "dispersion correction" : "method") : "basis set"
    return (
        <section className="card catalogue-card" aria-labelledby={headingId}>
            <h3 className="t-kicker catalogue-card-kicker" id={headingId}>About this {noun}</h3>
            <p className="catalogue-card-name">
                <span className="t-heading-2 catalogue-card-short">{card.short_name}</span>
                {card.full_name !== card.short_name && <span className="catalogue-card-full">{card.full_name}</span>}
            </p>
            <dl className="kv-list catalogue-card-facts">
                <div><dt>Family</dt><dd>{cardFamilyText(card)}</dd></div>
                <div>
                    <dt>Spellings seen</dt>
                    <dd>{card.spellings_seen.length > 0 ? card.spellings_seen.join(" · ") : "none on a listed level"}</dd>
                </div>
            </dl>
            <p className="catalogue-card-description">{card.description}</p>
            {groups.length > 0 ? groups.map((group) => (
                <div key={group.role} className="catalogue-card-references">
                    <p className="catalogue-card-references-heading">{group.heading}</p>
                    <ul className="catalogue-reference-list">
                        {group.items.map((reference) => (
                            <li key={`${reference.literature_ref}-${reference.position}`}><CatalogueReference reference={reference} /></li>
                        ))}
                    </ul>
                </div>
            )) : <p className="note">No reference is recorded for this entry.</p>}
            <p className="catalogue-card-footer">
                <span className="catalogue-card-usage">{usageText(card.usage)}</span>
                <Link to={cataloguePath(card)}>Open the {entryNoun} page</Link>
            </p>
        </section>
    )
}

/** A spelling on this level that keys to no catalogue entry. Said plainly; nothing is guessed. */
export function NotCatalogued({ noun, spelling }: { noun: string; spelling: string }) {
    return (
        <p className="catalogue-not-catalogued">
            <span className="catalogue-not-catalogued-noun">{noun[0].toUpperCase() + noun.slice(1)}</span>{" "}
            <code className="data">{spelling}</code>: not in the catalogue.
        </p>
    )
}
