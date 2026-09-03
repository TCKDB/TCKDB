import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import type { TransitionStateEntryRecord } from "../api/transitionStateEntryApi"
import { loadTransitionStateEntry } from "../api/transitionStateEntryApi"
import { lotLabel } from "../api/scientificSchemas"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { RefsDisclosure } from "../components/RefsDisclosure"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import type { TransitionStateIdentity } from "../domain/recordIdentity"
import { useScientificRecord } from "../hooks/useScientificRecord"

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

type Calculation = NonNullable<TransitionStateEntryRecord["calculations"]>[number]

// Three states an include-gated section can be in, kept distinct per the
// house rule this app already applies on `ConformerObservationPage`:
// absence describes the request, null/empty describes the data.
type SectionAvailability = "not-requested" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

/**
 * `/transition-state-entries/:entryRef` -- the record view 34 browsable
 * transition-state entries had no page to land on before this (browse
 * only linked to `/reactions/:ref`, a placeholder). Modeled on
 * `ConformerObservationPage`'s shell (self-contained record, no
 * conformer-picker state to carry), not `SpeciesEntryPage`'s tabbed one --
 * a TS entry has no conformer basin to select between.
 *
 * Renders only what `GET /scientific/transition-state-entries/{ref}`
 * actually serves: identity, reaction classification, evidence summary
 * (booleans + levels of theory, never a boolean upgraded to a claim about
 * *quality*), IRC validation status, calculations, geometries, and review
 * history. It does NOT render a literature/citations section -- the
 * endpoint (see `TRANSITION_STATE_RECORD_SECTIONS` in
 * `backend/app/api/routes/scientific/_response.py`) has no such include
 * token to ask for one; there is nothing to omit by choice, only nothing
 * on the wire to show. Per-calculation `energy` (served under
 * `include=calculations`) is likewise left off the calculation table below
 * to match the column set every sibling record page (`ConformerGroupPage`,
 * `ConformerObservationPage`) already uses for the same table shape.
 */
export default function TransitionStateEntryPage() {
    const { entryRef = "" } = useParams<{ entryRef: string }>()
    const state = useScientificRecord(entryRef, loadTransitionStateEntry)

    if (state.status === "ready") return <EntryDetail record={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="transition state entry"
            loadingDetail="Retrieving the deposited saddle-point entry and its reaction context."
        />
    )
}

function EntryDetail({ record }: { record: TransitionStateEntryRecord }) {
    const {
        transition_state_entry: entry,
        transition_state: ts,
        reaction,
        evidence_summary: evidence,
        validation,
        available_sections: available,
    } = record

    const identity: TransitionStateIdentity = {
        kind: "transition_state_entry",
        // Never served on this endpoint (see `TransitionStateEntryCoreBlock`)
        // -- a TS has no molecular-graph formula the way a species does.
        formula: null,
        unmappedSmiles: entry.unmapped_smiles ?? null,
        charge: entry.charge,
        multiplicity: entry.multiplicity,
        transitionStateRef: ts.transition_state_ref,
        transitionStateEntryRef: entry.transition_state_entry_ref,
        label: ts.label,
    }

    const calculationsAvailability = sectionAvailability(record.calculations)
    const calculations = record.calculations ?? []

    const geometriesAvailability = sectionAvailability(record.geometries)
    const geometries = [...(record.geometries ?? [])]
        .sort((a, b) => (a.output_order ?? a.input_order ?? 0) - (b.output_order ?? b.input_order ?? 0))

    const reviewAvailability = sectionAvailability(record.review_history)
    const reviewHistory = record.review_history ?? []

    const stages = Object.entries(evidence.levels_of_theory)
    const refs = [
        { label: "Transition state entry", value: entry.transition_state_entry_ref },
        { label: "Transition state", value: ts.transition_state_ref },
        ...(reaction.reaction_ref ? [{ label: "Reaction", value: reaction.reaction_ref, to: `/reactions/${reaction.reaction_ref}` }] : []),
        ...(reaction.reaction_entry_ref ? [{ label: "Reaction entry", value: reaction.reaction_entry_ref }] : []),
    ]

    return (
        <section className="conformer-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to="/species?kind=transition_state">Browse</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Transition state entry</span>
            </nav>
            <PageShell
                identity={(
                    <header className="basin-header">
                        <p className="eyebrow">Transition state entry · deposited scientific record</p>
                        <div className="basin-title">
                            <h1>{ts.label ?? "Unlabeled transition state"}</h1>
                            <span className="review-badge">{statusLabel(entry.review.status)}</span>
                        </div>
                        <p className="basin-intro">
                            One candidate saddle point for the reaction below. A transition state has no canonical
                            SMILES or InChIKey the way a species does; identity here is the deposited unmapped
                            SMILES, the reaction it connects, and the evidence attached to this entry.
                        </p>
                        <RecordIdentityHeader identity={identity} />
                        <dl className="basin-context">
                            <div><dt>Entry status</dt><dd>{statusLabel(entry.status)}</dd></div>
                            <div><dt>Charge / multiplicity</dt><dd>{chargeDisplay(entry.charge)} / {spinDisplay(entry.multiplicity)}</dd></div>
                            <div><dt>Deposited</dt><dd>{isoDate(entry.created_at)}</dd></div>
                            <div><dt>Transition state review</dt><dd>{statusLabel(ts.review.status)}</dd></div>
                            {ts.note && <div><dt>Transition state note</dt><dd>{ts.note}</dd></div>}
                        </dl>
                        <RefsDisclosure refs={refs} />
                    </header>
                )}
            >
            <section className="ledger-section" aria-labelledby="reaction-context">
                <div className="ledger-heading">
                    <p className="eyebrow">Reaction context</p>
                    <SectionHeading id="reaction-context">Reaction</SectionHeading>
                    <p>A transition state is identified by the reaction it connects, not a molecular graph of its own.</p>
                </div>
                <dl className="basin-context">
                    <div><dt>Equation</dt><dd>{reaction.equation ?? "not recorded"}</dd></div>
                    <div><dt>Family</dt><dd>{reaction.family ? statusLabel(reaction.family) : "not recorded"}</dd></div>
                    <div><dt>Reversible</dt><dd>{reaction.reversible === null || reaction.reversible === undefined ? "not recorded" : (reaction.reversible ? "yes" : "no")}</dd></div>
                    {reaction.reaction_ref && (
                        <div><dt>Reaction record</dt><dd><Link to={`/reactions/${reaction.reaction_ref}`}>{reaction.reaction_ref}</Link></dd></div>
                    )}
                </dl>
            </section>

            {/* Four slots -- `.ledger-summary`'s grid is `repeat(3, 1fr)
                1.8fr` (see `conformer-group.css`, shared with
                `ConformerObservationPage`'s own summary strip), so three
                narrow cards plus one wide one is the shape it expects. The
                two coverage cards below carry the longer prose, so the
                wider trailing slot goes to whichever needs it. */}
            <section className="ledger-summary" aria-label="Entry evidence summary">
                <Metric label="Calculation rows" value={evidence.calculation_count} />
                <Metric label="Stored geometries" value={geometries.length} />
                <div className="coverage-card">
                    <span>IRC validation</span>
                    <strong>{ircValidationLabel(validation.irc)}</strong>
                    <p>
                        {validation.irc === "present" && "A passed IRC evidence record was deposited for this entry."}
                        {validation.irc === "failed" && "An IRC evidence record was deposited for this entry, but it did not pass."}
                        {validation.irc === "absent" && (
                            evidence.has_irc
                                ? "An IRC calculation exists on this entry, but no structured pass/fail evidence record was deposited for it."
                                : "No IRC evidence was deposited for this entry."
                        )}
                        {validation.irc !== "present" && validation.irc !== "failed" && validation.irc !== "absent"
                            && "The archive returned an unrecognised validation status."}
                    </p>
                </div>
                <div className="coverage-card">
                    <span>Evidence present on this entry</span>
                    <strong>
                        opt {evidence.has_opt ? "yes" : "no"} · freq {evidence.has_freq ? "yes" : "no"} · sp
                        {` ${evidence.has_sp ? "yes" : "no"}`} · irc {evidence.has_irc ? "yes" : "no"} · path search
                        {` ${evidence.has_path_search ? "yes" : "no"}`} · geometry validation
                        {` ${evidence.has_geometry_validation ? "recorded" : "not recorded"}`} · SCF stability
                        {` ${evidence.has_scf_stability ? "recorded" : "not recorded"}`}
                    </strong>
                    <p>Presence says this entry carries that stage, not that its result was favourable.</p>
                </div>
            </section>

            <section className="ledger-section" aria-labelledby="lot-by-stage">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited provenance</p>
                    <SectionHeading id="lot-by-stage">Levels of theory by stage</SectionHeading>
                    <p>Each stage keeps its own method. Differing levels across stages are never flattened.</p>
                </div>
                {stages.length ? (
                    <dl className="basin-context">
                        {stages.map(([stage, levels]) => (
                            <div key={stage}>
                                <dt>{stage}</dt>
                                <dd>{levels.map((level) => lotLabel(level)).join(", ") || "not recorded"}</dd>
                            </div>
                        ))}
                    </dl>
                ) : (
                    <p className="empty-projection">No levels of theory were recorded for this entry.</p>
                )}
            </section>

            <section className="ledger-section" aria-labelledby="calc-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Machine detail</p>
                    <SectionHeading id="calc-ledger">Calculation evidence</SectionHeading>
                    <p>Rows are listed in the order the archive returned them.</p>
                </div>
                {calculationsAvailability === "populated" ? (
                    <CalculationTable calculations={calculations} entryRef={entry.transition_state_entry_ref} />
                ) : (
                    <SectionEmptyMessage
                        availability={calculationsAvailability}
                        emptyText="No calculation rows were returned for this entry."
                        contradicted={calculationsAvailability === "empty" && available.has_calculations}
                    />
                )}
            </section>

            <section className="ledger-section geometry-ledger" aria-labelledby="geometry-ledger">
                <p className="eyebrow">Stored coordinates</p>
                <SectionHeading id="geometry-ledger">Geometry records</SectionHeading>
                <p>
                    These are stored geometry objects linked from this entry's calculation output -- the saddle
                    point itself and, where an IRC ran, the reaction-path points either side of it.
                </p>
                {geometriesAvailability === "populated" ? (
                    <div className="geometry-links">
                        {geometries.map((geometry) => (
                            <div className="geometry-link" key={geometry.geometry_ref}>
                                <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                                <span>
                                    {geometry.role ? statusLabel(geometry.role) : "role not recorded"}
                                    {geometry.natoms != null ? ` · ${geometry.natoms} atoms` : ""}
                                </span>
                            </div>
                        ))}
                    </div>
                ) : (
                    <SectionEmptyMessage
                        availability={geometriesAvailability}
                        emptyText="No stored geometry links were returned for this entry."
                        contradicted={geometriesAvailability === "empty" && available.has_geometries}
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="review-ledger">
                <div className="ledger-heading">
                    <p className="eyebrow">Review &amp; trust</p>
                    <SectionHeading id="review-ledger">Review history</SectionHeading>
                    <p>The current status is {statusLabel(entry.review.status)}. This is the record of how it got there.</p>
                </div>
                {reviewAvailability === "populated" ? (
                    <table className="stage-table" aria-label={`Review history for ${entry.transition_state_entry_ref}`}>
                        <thead>
                            <tr>
                                <th scope="col">Status</th>
                                <th scope="col">Reviewed at</th>
                                <th scope="col">Note</th>
                            </tr>
                        </thead>
                        <tbody>
                            {reviewHistory.map((historyEntry, index) => (
                                <tr key={`review-entry-${index}`}>
                                    <td data-label="Status">{statusLabel(historyEntry.status)}</td>
                                    <td data-label="Reviewed at">{isoDate(historyEntry.reviewed_at)}</td>
                                    <td data-label="Note">{historyEntry.note ?? "not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : (
                    <SectionEmptyMessage
                        availability={reviewAvailability}
                        emptyText="No review history was returned for this entry."
                        contradicted={reviewAvailability === "empty" && available.has_review}
                    />
                )}
            </section>
            </PageShell>
        </section>
    )
}

function ircValidationLabel(irc: string): string {
    if (irc === "present") return "passed"
    if (irc === "failed") return "failed"
    if (irc === "absent") return "not established"
    return statusLabel(irc)
}

function Metric({ label, value }: { label: string; value: number }) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
    )
}

function SectionEmptyMessage({ availability, emptyText, contradicted }: {
    availability: SectionAvailability
    emptyText: string
    contradicted?: boolean
}) {
    if (availability === "not-requested") {
        return <p className="empty-projection">This section was not requested for this view.</p>
    }
    return (
        <p className="empty-projection">
            {emptyText}
            {contradicted
                ? " The archive marks this entry as having recorded evidence here; this view did not return it."
                : ""}
        </p>
    )
}

function CalculationTable({ calculations, entryRef }: { calculations: Calculation[]; entryRef: string }) {
    return (
        <table className="stage-table" aria-label={`Calculations for ${entryRef}`}>
            <thead>
                <tr>
                    <th scope="col">Stage</th>
                    <th scope="col">Level of theory</th>
                    <th scope="col">Software / workflow</th>
                    <th scope="col">Review</th>
                    <th scope="col">Record</th>
                </tr>
            </thead>
            <tbody>
                {calculations.map((calculation) => (
                    <tr key={calculation.calculation_ref}>
                        <td data-label="Stage">{calculation.type}</td>
                        <td data-label="Level of theory">
                            {calculation.level_of_theory ? lotLabel(calculation.level_of_theory) : "not recorded"}
                        </td>
                        <td data-label="Software / workflow">
                            {calculation.software_release?.software ?? "not recorded"}
                            {calculation.workflow_tool_release?.workflow_tool
                                ? ` · ${calculation.workflow_tool_release.workflow_tool}`
                                : ""}
                        </td>
                        <td data-label="Review">
                            {calculation.review ? statusLabel(calculation.review.status) : "not recorded"}
                        </td>
                        <td data-label="Record">
                            <Link to={`/calculations/${calculation.calculation_ref}`}>{calculation.calculation_ref}</Link>
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
    )
}
