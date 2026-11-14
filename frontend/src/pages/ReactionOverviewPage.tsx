import { Link, Navigate, useParams } from "react-router-dom"
import { ParticipantChips } from "../components/ParticipantChips"
import "../conformer-group.css"
import "../record-identity-header.css"
import "../data-table.css"
import "../reaction-entry.css"
import type { ReactionOverviewParticipant, ReactionOverviewRecord } from "../api/reactionOverviewApi"
import { loadReactionOverview } from "../api/reactionOverviewApi"
import type { ListMeta } from "../api/listEnvelope"
import type { PageRequest } from "../domain/paging"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { EvidenceChecklist } from "../components/EvidenceChecklist"
import { Lede } from "../components/Lede"
import { RecordGlyph } from "../components/RecordGlyph"
import { ReviewChip } from "../components/ReviewChip"
import { reactionOverviewLede } from "../domain/reactionLede"
import { ListPager } from "../components/ListPager"
import { SpeciesFace } from "../components/Formula"
import { PageShell } from "../components/PageShell"
import { RecordMap } from "../components/RelationshipMap"
import { reactionOverviewNodes } from "../domain/recordMapNodes"
import { SectionHeading } from "../components/PageSections"
import { ReactionEquation } from "../components/ReactionEquation"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import { ParticipantLines, RecordLink } from "../components/RecordLink"
import { versionWords } from "../domain/recordWords"
import { participantChips } from "../domain/participantFacets"
import { listMetaOf } from "../api/listEnvelope"
import { chooserHeaderSourceNote } from "../domain/reactionChooser"
import { usePagedList } from "../hooks/usePagedList"
import { useScientificRecord } from "../hooks/useScientificRecord"
import { useDocumentTitle } from "../hooks/useDocumentTitle"
import { equationNeedsStructure, equationText } from "../domain/reactionEquation"

function statusLabel(status: string): string {
    return status.replaceAll("_", " ")
}

/**
 * `/reactions/:reactionRef` -- the chooser page between every `rxe_...`
 * entry deposited under one `rxn_...` reaction identity This
 * site never merges or silently picks between matching entries, so this page's ONE job is to list them
 * and let the reader pick.
 *
 * An `rxe_...` handed to this route redirects to `/reaction-entries/:ref`
 * by PREFIX CHECK ALONE -- no request -- the same self-healing pattern
 * `BrowsePage` already uses for a stray `?kind=` value.
 */
export default function ReactionOverviewPage() {
    const { reactionRef = "" } = useParams<{ reactionRef: string }>()

    if (reactionRef.startsWith("rxe_")) {
        return <Navigate to={`/reaction-entries/${reactionRef}`} replace />
    }

    return <ReactionChooser reactionRef={reactionRef} />
}

function ReactionChooser({ reactionRef }: { reactionRef: string }) {
    // The title and equation come from the FIRST page's first entry, always, so they do not change as the reader pages
    // through the entries; the table below is the page the reader is on.
    const firstPage = useScientificRecord(reactionRef, loadReactionOverview)
    const { state, meta, goTo } = usePagedList(reactionRef, loadReactionOverview)

    if (firstPage.status !== "ready") {
        return (
            <RecordStatus
                state={firstPage}
                ref={reactionRef}
                kind="reaction"
                loadingDetail="Retrieving every deposited entry under this reaction identity."
            />
        )
    }
    if (firstPage.record.records.length === 0) {
        return (
            <RecordStatus
                state={{ ref: reactionRef, status: "missing" }}
                ref={reactionRef}
                kind="reaction"
                loadingDetail=""
            />
        )
    }
    const shown = state.status === "ready" ? state.record : null
    return (
        <ChooserDocument
            reactionRef={reactionRef}
            headerRecord={firstPage.record.records[0]}
            firstPageEntries={firstPage.record.records.map((record) => ({ ref: record.reaction_entry_ref, position: record.deposit_position }))}
            total={firstPage.record.pagination.total}
            kineticsTotal={firstPage.record.records.reduce((sum, record) => sum + record.availability.kinetics_count, 0)}
            records={shown?.records ?? []}
            pageStatus={state.status}
            reviewSummary={shown?.review_summary ?? firstPage.record.review_summary}
            meta={meta ?? listMetaOf(firstPage.record)}
            onPage={goTo}
        />
    )
}

function ChooserDocument({ reactionRef, headerRecord, firstPageEntries, total, kineticsTotal, records, pageStatus, reviewSummary, meta, onPage }: {
    reactionRef: string
    /** The first entry of the first page: the source of the title and equation, whatever page the table is on. */
    headerRecord: ReactionOverviewRecord
    /** The first page of entries with their version numbers, the same page the header comes from. */
    firstPageEntries: readonly { ref: string; position: number }[]
    total: number
    /** Kinetics records across the first page of entries; the compare link is offered from two. */
    kineticsTotal: number
    pageStatus: string
    meta: ListMeta | null
    onPage: (request: PageRequest) => void
    records: ReactionOverviewRecord[]
    reviewSummary: { approved: number; under_review: number; not_reviewed: number; deprecated: number; rejected: number; total: number }
}) {
    const first = headerRecord
    const headerSourceNote = chooserHeaderSourceNote(total, first.deposit_position)
    const formulaOnly = !equationNeedsStructure(first.reactants, first.products)
    useDocumentTitle("Reaction", equationText(first.reactants, first.products, first.reversible))
    const mapParticipant = (p: (typeof first.reactants)[number]) => ({ entryRef: p.species_entry_ref, formula: p.formula, smiles: p.smiles })
    const mapNodes = reactionOverviewNodes({
        entries: firstPageEntries,
        total,
        reactants: first.reactants.map(mapParticipant),
        products: first.products.map(mapParticipant),
    })
    return (
        <section className="conformer-page">
            <PageShell
                side={<RecordMap selfKind="reaction" selfLabel="This reaction" nodes={mapNodes} />}
                identity={(
                    <>
                        <Breadcrumbs crumbs={[
                            { label: "Reactions", to: BROWSE_KIND_PATHS.reaction },
                            { label: <ReactionEquation reactants={first.reactants} products={first.products} reversible={first.reversible} linkParticipants={false} formulaOnly={formulaOnly} /> },
                        ]} />
                        <header className="basin-header">
                            <div className="record-identity-header">
                                <div className="record-identity-kicker-row">
                                    <RecordGlyph kind="reaction" size={16} />
                                    <span className="t-kicker record-identity-kicker">Reaction · all deposited versions</span>
                                </div>
                                <h1 className="t-display-2 record-identity-title">
                                    <ReactionEquation reactants={first.reactants} products={first.products} reversible={first.reversible} formulaOnly={formulaOnly} />
                                </h1>
                                <Lede>{reactionOverviewLede(total)}</Lede>
                                {headerSourceNote && <p className="note reaction-header-source">{headerSourceNote}</p>}
                                <div className="record-identity-known">
                                    <dl className="kv-list record-identity-facts">
                                        <div>
                                            <dt>Reaction ref</dt>
                                            <dd className="record-identity-fact-copyable">
                                                <code className="data">{reactionRef}</code>
                                                <CopyButton value={reactionRef} label="Reaction ref" srLabel="value" />
                                            </dd>
                                        </div>
                                        <div><dt>Family</dt><dd>{first.family ? statusLabel(first.family) : <span className="record-identity-absent-inline">not recorded</span>}</dd></div>
                                        <div><dt>Reversible</dt><dd>{first.reversible ? "yes" : "no"}</dd></div>
                                        <div className="record-identity-fact-wide">
                                            <dt>Equation (as deposited)</dt>
                                            <dd><code className="data">{first.equation}</code></dd>
                                        </div>
                                    </dl>
                                </div>
                            </div>
                        </header>
                    </>
                )}
            >
                <section className="ledger-section" aria-labelledby="entries-heading">
                    <SectionHeading id="entries-heading">Reaction entries</SectionHeading>
                    {/* kineticsTotal counts the rate records of the entries on the first page of this list. */}
                    {kineticsTotal >= 2 && <p><Link className="compare-link" to={`/reactions/${reactionRef}/compare`}>Compare these values</Link></p>}
                    {pageStatus !== "ready" && <p className="note" role="status">{pageStatus === "loading" || pageStatus === "retrying" ? "Loading this page of entries…" : "This page of entries could not be loaded."}</p>}
                    {meta && <ListPager meta={meta} noun="reaction entry" nounPlural="reaction entries" onPage={onPage} label="Reaction entries paging" />}
                    <div className="table-scroll">
                        <table className="data-table reaction-entries-table" aria-label="Reaction entries">
                            <thead>
                                <tr>
                                    <th scope="col">Entry</th>
                                    <th scope="col">Reactants</th>
                                    <th scope="col">Products</th>
                                    <th scope="col">Review</th>
                                    <th scope="col">Has kinetics</th>
                                    <th scope="col">Has TS</th>
                                    <th scope="col">Kinetics count</th>
                                </tr>
                            </thead>
                            <tbody>
                                {records.map((record) => (
                                    <tr key={record.reaction_entry_ref}>
                                        <td data-label="Entry"><RecordLink kind="reaction-entry" to={`/reaction-entries/${record.reaction_entry_ref}`} label={versionWords(record.deposit_position)} /></td>
                                        <td data-label="Reactants">
                                            <ParticipantLines>
                                                {record.reactants.map((p) => <ParticipantCell key={p.species_entry_ref} participant={p} />)}
                                            </ParticipantLines>
                                        </td>
                                        <td data-label="Products">
                                            <ParticipantLines>
                                                {record.products.map((p) => <ParticipantCell key={p.species_entry_ref} participant={p} />)}
                                            </ParticipantLines>
                                        </td>
                                        <td data-label="Review"><ReviewChip status={record.review.status} explain={false} /></td>
                                        <td data-label="Has kinetics">
                                            {record.availability.has_kinetics ? "yes" : "no"}
                                        </td>
                                        <td data-label="Has TS">
                                            {record.availability.has_transition_state ? "yes" : "no"}
                                        </td>
                                        <td className="num" data-label="Kinetics count">{record.availability.kinetics_count}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </section>

                <section className="ledger-section" aria-labelledby="review-heading">
                    <SectionHeading id="review-heading">Review, across all entries</SectionHeading>
                    <EvidenceChecklist
                        heading="Review counts"
                        // No `tone` on these rows -- a true collapsed-summary
                        // roll-up (the real total, not the fixed 6-row
                        // count) must come from the caller. See
                        // `EvidenceChecklist`'s own docstring.
                        summary={`${reviewSummary.total} joined records`}
                        rows={[
                            { label: "Approved", value: reviewSummary.approved },
                            { label: "Under review", value: reviewSummary.under_review },
                            { label: "Not reviewed", value: reviewSummary.not_reviewed },
                            { label: "Deprecated", value: reviewSummary.deprecated },
                            { label: "Rejected", value: reviewSummary.rejected },
                            { label: "Total", value: reviewSummary.total },
                        ]}
                    />
                </section>
            </PageShell>
        </section>
    )
}

/**
 * One reactant/product: its SMILES-leads-formula link (two entries of one
 * reaction can be different isomers of the same formula) and its facet
 * chips in parentheses.
 */
function ParticipantCell({ participant }: { participant: ReactionOverviewParticipant }) {
    const face = [participant.smiles, participant.formula && `(${participant.formula})`].filter(Boolean).join(" ")
    return (
        <>
            <RecordLink kind="species-entry" to={`/species-entries/${participant.species_entry_ref}`} label={face || "Species"}>
                <SpeciesFace smiles={participant.smiles} formula={participant.formula} />
            </RecordLink>
            <ParticipantChips chips={participantChips(participant)} style="paren" />
        </>
    )
}
