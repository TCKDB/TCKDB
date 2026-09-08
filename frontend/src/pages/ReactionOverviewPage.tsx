import { Link, Navigate, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import "../reaction-entry.css"
import type { ReactionOverviewParticipant, ReactionOverviewRecord } from "../api/reactionOverviewApi"
import { loadReactionOverview } from "../api/reactionOverviewApi"
import { EvidenceChecklist } from "../components/EvidenceChecklist"
import { Formula } from "../components/Formula"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { ReactionEquation } from "../components/ReactionEquation"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import { stereoChip } from "../domain/recordFacets"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { useScientificRecord } from "../hooks/useScientificRecord"

function statusLabel(status: string): string {
    return status.replaceAll("_", " ")
}

/**
 * `/reactions/:reactionRef` -- the chooser page between every `rxe_...`
 * entry deposited under one `rxn_...` reaction identity (plan §2). This
 * site never merges or silently picks between matching entries (the
 * owner's ruling, live-data-justified: `rxn_naeqmg4l5wyqex5cl5tir2vt2y`
 * carries four separate deposits), so this page's ONE job is to list them
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
    const state = useScientificRecord(reactionRef, loadReactionOverview)

    if (state.status !== "ready") {
        return (
            <RecordStatus
                state={state}
                ref={reactionRef}
                kind="reaction"
                loadingDetail="Retrieving every deposited entry under this reaction identity."
            />
        )
    }
    if (state.record.records.length === 0) {
        return (
            <RecordStatus
                state={{ ref: reactionRef, status: "missing" }}
                ref={reactionRef}
                kind="reaction"
                loadingDetail=""
            />
        )
    }
    return <ChooserDocument reactionRef={reactionRef} records={state.record.records} reviewSummary={state.record.review_summary} />
}

function ChooserDocument({ reactionRef, records, reviewSummary }: {
    reactionRef: string
    records: ReactionOverviewRecord[]
    reviewSummary: { approved: number; under_review: number; not_reviewed: number; deprecated: number; rejected: number; total: number }
}) {
    const first = records[0]
    return (
        <section className="conformer-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Reaction</span>
            </nav>
            <PageShell
                identity={(
                    <header className="basin-header">
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Reaction · chooses between deposited entries</span>
                            </div>
                            <h1 className="t-display-2 record-identity-title">
                                <ReactionEquation reactants={first.reactants} products={first.products} reversible={first.reversible} />
                            </h1>
                            <p className="t-body section-intro">
                                {records.length > 1
                                    ? `${records.length} separate deposits share this reaction identity. This site never merges or silently chooses between matching entries — pick the record you mean below.`
                                    : "One deposited entry carries this reaction identity."}
                            </p>
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
                )}
            >
                <section className="ledger-section" aria-labelledby="entries-heading">
                    <p className="t-kicker section-kicker">Deposited records</p>
                    <SectionHeading id="entries-heading">Reaction entries</SectionHeading>
                    <div className="table-scroll">
                        <table className="data-table" aria-label={`Reaction entries for ${reactionRef}`}>
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
                                        <td data-label="Entry"><Link to={`/reaction-entries/${record.reaction_entry_ref}`}>{record.reaction_entry_ref}</Link></td>
                                        <td data-label="Reactants">
                                            {record.reactants.map((p, i) => (
                                                <ParticipantCell key={p.species_entry_ref} participant={p} separator={i > 0} />
                                            ))}
                                        </td>
                                        <td data-label="Products">
                                            {record.products.map((p, i) => (
                                                <ParticipantCell key={p.species_entry_ref} participant={p} separator={i > 0} />
                                            ))}
                                        </td>
                                        <td data-label="Review"><span className={reviewPillClass(record.review.status)}>{statusLabel(record.review.status)}</span></td>
                                        <td data-label="Has kinetics">
                                            <span className={record.availability.has_kinetics ? "value-pill" : "value-pill value-pill--muted"}>
                                                {record.availability.has_kinetics ? "yes" : "no"}
                                            </span>
                                        </td>
                                        <td data-label="Has TS">
                                            <span className={record.availability.has_transition_state ? "value-pill" : "value-pill value-pill--muted"}>
                                                {record.availability.has_transition_state ? "yes" : "no"}
                                            </span>
                                        </td>
                                        <td className="num" data-label="Kinetics count">{record.availability.kinetics_count}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </section>

                <section className="ledger-section" aria-labelledby="review-heading">
                    <p className="t-kicker section-kicker">Curation status</p>
                    <SectionHeading id="review-heading">Review, across all entries</SectionHeading>
                    <EvidenceChecklist
                        heading={`Review counts across the ${records.length} ${records.length === 1 ? "entry" : "entries"} under this reaction`}
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
 * One reactant/product cell: formula (or SMILES fallback) link, the
 * participant's own `spe_` ref with a copy button, and its
 * `species_entry_label` chip (e.g. "Z") when served -- the same four
 * facts the PR 0 mock's own chooser table rendered for every participant
 * (post-review fix: this row previously showed only the formula link,
 * dropping the ref/copy-button/label the mock had).
 */
function ParticipantCell({ participant, separator }: { participant: ReactionOverviewParticipant; separator: boolean }) {
    return (
        // No `className` here -- there is no CSS rule for this wrapper
        // (round-2 review: a styling hook with nothing hooked to it), and
        // this component needs no styling of its own beyond the inline
        // flow its children already produce.
        <span>
            {separator && " · "}
            <Link to={`/species-entries/${participant.species_entry_ref}`}>
                {participant.formula ? <Formula value={participant.formula} /> : participant.smiles}
            </Link>
            {" "}
            <code className="data">{participant.species_entry_ref}</code>
            <CopyButton value={participant.species_entry_ref} label="Species entry" srLabel="reference" />
            {/* Parenthesised, NOT another " · " -- the between-participant
                separator above is ALSO " · ", so a stereo-label chip using
                the same glyph read as a third product in a multi-
                participant cell ("H2 ... · H2N2 ... · Z isomer"). Round-2
                review finding: parentheses keep the chip visually bound
                to the participant it immediately follows. */}
            {participant.species_entry_label && <> ({stereoChip(participant.species_entry_label)})</>}
        </span>
    )
}
