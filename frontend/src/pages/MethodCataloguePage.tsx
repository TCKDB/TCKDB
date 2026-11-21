import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../methods.css"
import { loadBasisSetCatalogueEntry, loadMethodCatalogueEntry, type CatalogueEntryRecord } from "../api/catalogueApi"
import { CatalogueReference } from "../components/CatalogueReference"
import { OrderNote } from "../components/OrderNote"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordMap } from "../components/RelationshipMap"
import { RecordStatus } from "../components/RecordStatus"
import { levelOfTheoryPath } from "../domain/methodsLinks"
import { cardFamilyText, referencesByRole } from "../domain/methodCatalogue"
import { catalogueEntryNodes } from "../domain/recordMapNodes"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { useScientificRecord } from "../hooks/useScientificRecord"

type EntryKind = CatalogueEntryRecord["kind"]

const LOADERS = { method: loadMethodCatalogueEntry, basis: loadBasisSetCatalogueEntry } as const

/** `/methods/method/:key` and `/methods/basis/:key`: one catalogue entry, its references and the levels of theory that use it. */
export default function MethodCataloguePage({ kind }: { kind: EntryKind }) {
    const { key = "" } = useParams<{ key: string }>()
    const state = useScientificRecord(key, LOADERS[kind])
    if (state.status === "ready") {
        return <CatalogueEntryDetail key={state.record.card.catalogue_ref} record={state.record} />
    }
    return (
        <RecordStatus
            state={state}
            ref={key}
            kind={kind === "method" ? "method" : "basis set"}
            loadingDetail="Retrieving this catalogue entry, its references and the levels of theory that use it."
        />
    )
}

function entryKicker(record: CatalogueEntryRecord): string {
    if (record.kind === "basis") return "Basis set"
    return record.card.kind === "dispersion_correction" ? "Dispersion correction" : "Method"
}

function CatalogueEntryDetail({ record }: { record: CatalogueEntryRecord }) {
    const card = record.card
    const kicker = entryKicker(record)
    const noun = kicker.toLowerCase()
    useDocumentTitle(kicker, card.short_name)
    const groups = referencesByRole(card.references)
    const listCrumb = record.kind === "method"
        ? { label: "Method catalogue", to: "/methods#methods-catalogue-heading" }
        : { label: "Basis-set catalogue", to: "/methods#basis-catalogue-heading" }
    const nodes = catalogueEntryNodes({ levelCount: record.levels.length })

    return (
        <section className="conformer-page methods-page">
            <PageShell
                side={<RecordMap selfKind="level-of-theory" selfLabel={`This ${noun}`} nodes={nodes} />}
                crumbs={[{ label: "Methods", to: "/methods" }, listCrumb, { label: card.short_name }]}
                identity={(
                    <header className="basin-header">
                        <RecordIdentityHeader
                            kicker={kicker}
                            glyph="level-of-theory"
                            guide={{ to: "method-catalogue", question: "What is the method and basis-set catalogue?" }}
                            title={card.short_name}
                            subtitle={card.full_name !== card.short_name ? card.full_name : undefined}
                            intro={card.description}
                            ownRef={{ label: "Catalogue ref", value: card.catalogue_ref }}
                            ownFacts={(
                                <>
                                    <div><dt>Family</dt><dd>{cardFamilyText(card)}</dd></div>
                                    <div>
                                        <dt>Spellings seen</dt>
                                        <dd>{card.spellings_seen.length > 0 ? card.spellings_seen.join(" · ") : "none on a listed level"}</dd>
                                    </div>
                                    <div><dt>Keys</dt><dd>{card.keys.length > 0 ? card.keys.join(", ") : card.key}</dd></div>
                                    <div>
                                        <dt>Used by</dt>
                                        <dd>
                                            {card.usage.level_usage_count} {card.usage.level_usage_count === 1 ? "level of theory" : "levels of theory"}
                                            {" · "}
                                            {card.usage.calculation_usage_count} {card.usage.calculation_usage_count === 1 ? "calculation" : "calculations"}
                                        </dd>
                                    </div>
                                </>
                            )}
                        />
                    </header>
                )}
            >
                <section className="ledger-section" aria-labelledby="catalogue-references-heading">
                    <SectionHeading id="catalogue-references-heading">References</SectionHeading>
                    {groups.length > 0 ? (
                        <dl className="catalogue-reference-groups">
                            {groups.map((group) => (
                                <div key={group.role}>
                                    <dt>{group.heading}</dt>
                                    <dd>
                                        <ul className="catalogue-reference-list">
                                            {group.items.map((reference) => (
                                                <li key={`${reference.literature_ref}-${reference.position}`}>
                                                    <CatalogueReference reference={reference} withTitle />
                                                </li>
                                            ))}
                                        </ul>
                                    </dd>
                                </div>
                            ))}
                        </dl>
                    ) : (
                        <p className="empty-projection">No reference is recorded for this entry.</p>
                    )}
                </section>

                <section className="ledger-section" aria-labelledby="catalogue-levels-heading">
                    <SectionHeading id="catalogue-levels-heading">{`Levels of theory using this ${noun}`}</SectionHeading>
                    {record.levels.length > 0 ? (
                        <>
                            <OrderNote order="Listed by label." />
                            <div className="table-scroll">
                                <table className="data-table" aria-label={`Levels of theory using this ${noun}`}>
                                    <thead>
                                        <tr>
                                            <th scope="col">Level of theory</th>
                                            <th scope="col">Calculations</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {record.levels.map((level) => (
                                            <tr key={level.level_of_theory_ref}>
                                                <td data-label="Level of theory" className="composite-label">
                                                    <Link to={levelOfTheoryPath(level.level_of_theory_ref)}>{level.label}</Link>
                                                </td>
                                                <td data-label="Calculations" className="num">{level.calculation_usage_count}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    ) : (
                        <p className="empty-projection">No level of theory with a listed calculation uses this {noun}.</p>
                    )}
                </section>
            </PageShell>
        </section>
    )
}
