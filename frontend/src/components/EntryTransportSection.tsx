import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import { lotLabel } from "../api/scientificSchemas"
import {
    loadEntryTransportSection,
    readTransportSectionField,
    type TransportListResponse,
    type TransportRecord,
    type TransportSectionToken,
} from "../api/transportApi"
import { useEntryListSection, type EntryListSectionState } from "../hooks/useEntryListSection"
import { useEntryTransport } from "../hooks/useEntryTransport"
import { RecordStatus } from "./RecordStatus"
import { SectionErrorBoundary } from "./SectionErrorBoundary"
import { SupersessionNotice } from "./SupersessionNotice"

// ---------------------------------------------------------------------------
// Same entry-scoped-LIST design as `EntryStatmechSection.tsx`, with only two
// real include-gated sections (`source_calculations`, `review` -> field
// `review_history`) — see `api/transportApi.ts`.
//
// This is the surface the brief calls "the gift": `spe_bcbdjwkip75yoziblpntwzblzu`
// ([CH3]) has ZERO transport records, live. A zero-record `records: []`
// with `pagination.total: 0` is NOT a load failure — `useEntryTransport`
// only reaches `status: "ready"` on a successful 200 with a
// schema-conformant body, so an empty array here is a genuine "nothing
// deposited yet" answer, rendered by `TransportList` below, and stays
// entirely distinct from `RecordStatus`'s five non-ready states (loading /
// missing / invalid / unprocessable / malformed / unavailable) that this
// component renders instead whenever the request itself did not succeed.
// ---------------------------------------------------------------------------

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "Not recorded")
const boolLabel = (value: boolean | null | undefined) => (value === null || value === undefined ? "Not recorded" : value ? "Yes" : "No")

function reviewSummaryText(summary: TransportListResponse["review_summary"]) {
    const parts: string[] = []
    if (summary.approved) parts.push(`${summary.approved} approved`)
    if (summary.under_review) parts.push(`${summary.under_review} under review`)
    if (summary.not_reviewed) parts.push(`${summary.not_reviewed} not reviewed`)
    if (summary.deprecated) parts.push(`${summary.deprecated} deprecated`)
    if (summary.rejected) parts.push(`${summary.rejected} rejected`)
    return parts.length > 0 ? parts.join(" · ") : "no records"
}

export function EntryTransportSection({ entryRef }: { entryRef: string }) {
    const state = useEntryTransport(entryRef)
    if (state.status === "ready") {
        return (
            <SectionErrorBoundary
                fallback={(
                    <section className="ledger-section" aria-labelledby="transport-heading">
                        <h2 id="transport-heading">Transport</h2>
                        <p className="empty-projection" role="alert">
                            This section could not be displayed. The rest of this entry is unaffected.
                        </p>
                    </section>
                )}
            >
                <TransportList entryRef={entryRef} response={state.record} />
            </SectionErrorBoundary>
        )
    }
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="transport"
            loadingDetail="Retrieving the deposited transport records for this entry."
        />
    )
}

function useTransportSection<T>(entryRef: string, token: TransportSectionToken) {
    return useEntryListSection<TransportRecord, T, TransportSectionToken>(
        entryRef,
        token,
        (ref, tok, signal) => loadEntryTransportSection(ref, tok, signal),
        (record) => record.transport.transport_ref,
        (record) => readTransportSectionField<T>(record, token),
    )
}

function TransportList({ entryRef, response }: { entryRef: string; response: TransportListResponse }) {
    const { records, review_summary: reviewSummary, pagination } = response

    const [sourceCalcsState, openSourceCalcs] = useTransportSection<TransportRecord["source_calculations"]>(entryRef, "source_calculations")
    const [reviewState, openReview] = useTransportSection<TransportRecord["review_history"]>(entryRef, "review")

    return (
        <>
            <section className="ledger-section" aria-labelledby="transport-heading">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited evidence</p>
                    <h2 id="transport-heading">Transport</h2>
                    <p>
                        Every transport record deposited for this entry, each shown independently. Multiple
                        deposits are never merged, averaged, or reduced to one preferred value on this page.
                    </p>
                </div>
                <p className="records-note">
                    {pagination.total} record{pagination.total === 1 ? "" : "s"}
                    {pagination.total > pagination.returned ? ` (showing ${pagination.returned})` : ""}
                    {" · review: "}{reviewSummaryText(reviewSummary)}
                </p>
                {records.length === 0 ? (
                    <p className="empty-projection">
                        No transport records are deposited for this entry. This is the archive's own answer —
                        not a failed request — so nothing further will load if you retry.
                    </p>
                ) : (
                    records.map((record) => (
                        <SectionErrorBoundary
                            key={record.transport.transport_ref}
                            fallback={(
                                <article className="science-record" role="alert">
                                    <p className="empty-projection">
                                        Record <code>{record.transport.transport_ref}</code> could not be
                                        displayed. Other records on this page are unaffected.
                                    </p>
                                </article>
                            )}
                        >
                            <TransportRecordCard record={record} />
                        </SectionErrorBoundary>
                    ))
                )}
            </section>

            <TransportLazySection
                heading="Source calculations"
                records={records}
                available={records.some((record) => record.available_sections.has_source_calculations)}
                notAvailableText="No source calculations are recorded for any transport record on this entry."
                state={sourceCalcsState}
                onOpen={openSourceCalcs}
                rowState={(record, data) => arrayRowState(record.available_sections.has_source_calculations, data)}
            >
                {(_record, rows) => (rows && rows.length > 0 ? (
                    <div className="table-scroll">
                        <table className="stage-table" aria-label="Source calculations">
                            <thead>
                                <tr>
                                    <th scope="col">Role</th>
                                    <th scope="col">Calculation</th>
                                    <th scope="col">Type</th>
                                    <th scope="col">Level of theory</th>
                                    <th scope="col">Review</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row, index) => (
                                    <tr key={`source-${row.calculation_ref}-${index}`}>
                                        <td data-label="Role">{statusLabel(row.role)}</td>
                                        <td data-label="Calculation"><Link to={`/calculations/${row.calculation_ref}`}>{row.calculation_ref}</Link></td>
                                        <td data-label="Type">{statusLabel(row.calculation_type)}</td>
                                        <td data-label="Level of theory">{row.level_of_theory ? lotLabel(row.level_of_theory) : "Not recorded"}</td>
                                        <td data-label="Review">{statusLabel(row.review.status)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                ) : <p className="empty-projection">The archive returned no source-calculation rows.</p>)}
            </TransportLazySection>

            <TransportLazySection
                heading="Review history"
                records={records}
                available={records.some((record) => record.available_sections.has_review)}
                notAvailableText="No review history is recorded for any transport record on this entry."
                state={reviewState}
                onOpen={openReview}
                rowState={(record, data) => arrayRowState(record.available_sections.has_review, data)}
            >
                {(_record, rows) => (rows && rows.length > 0 ? (
                    <table className="stage-table" aria-label="Review history">
                        <thead><tr><th scope="col">Status</th><th scope="col">Reviewed at</th><th scope="col">Note</th></tr></thead>
                        <tbody>
                            {rows.map((row, index) => (
                                <tr key={`review-${index}`}>
                                    <td data-label="Status">{statusLabel(row.status)}</td>
                                    <td data-label="Reviewed at">{isoDate(row.reviewed_at)}</td>
                                    <td data-label="Note">{row.note ?? "Not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : <p className="empty-projection">The archive returned no review-history rows.</p>)}
            </TransportLazySection>
        </>
    )
}

function TransportRecordCard({ record }: { record: TransportRecord }) {
    const core = record.transport
    return (
        <article className="science-record" aria-labelledby={`transport-heading-${core.transport_ref}`}>
            <div className="science-record-heading">
                <h3 id={`transport-heading-${core.transport_ref}`}>{statusLabel(core.scientific_origin)} transport record</h3>
                <span className="review-badge">{statusLabel(core.review.status)}</span>
                <code>{core.transport_ref}</code>
            </div>

            {record.supersession && <SupersessionNotice supersession={record.supersession} />}

            <p className="section-note">
                Species entry: <Link to={`/species-entries/${record.species.species_entry_ref}`}>{record.species.species_entry_ref}</Link>
                {record.species.canonical_smiles ? ` (${record.species.canonical_smiles})` : ""}
            </p>

            <dl className="kv-list">
                <div><dt>Sigma (Å)</dt><dd>{core.sigma_angstrom ?? "Not recorded"}</dd></div>
                <div><dt>Epsilon / k (K)</dt><dd>{core.epsilon_over_k_k ?? "Not recorded"}</dd></div>
                <div><dt>Dipole (Debye)</dt><dd>{core.dipole_debye ?? "Not recorded"}</dd></div>
                <div><dt>Polarizability (Å³)</dt><dd>{core.polarizability_angstrom3 ?? "Not recorded"}</dd></div>
                <div><dt>Rotational relaxation</dt><dd>{core.rotational_relaxation ?? "Not recorded"}</dd></div>
                <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
            </dl>

            <dl className="kv-list">
                <div><dt>Source calculations</dt><dd>{record.evidence_summary.source_calculation_count}</dd></div>
                <div><dt>LJ parameters</dt><dd>{boolLabel(record.evidence_summary.has_lj_parameters)}</dd></div>
                <div><dt>Dipole moment</dt><dd>{boolLabel(record.evidence_summary.has_dipole_moment)}</dd></div>
                <div><dt>Polarizability</dt><dd>{boolLabel(record.evidence_summary.has_polarizability)}</dd></div>
                <div><dt>Rotational relaxation recorded</dt><dd>{boolLabel(record.evidence_summary.has_rotational_relaxation)}</dd></div>
                <div><dt>Literature source</dt><dd>{boolLabel(record.evidence_summary.has_literature_source)}</dd></div>
            </dl>

            <p className="section-note">
                Software:{" "}
                {record.software_release
                    ? `${record.software_release.software}${record.software_release.version ? ` ${record.software_release.version}` : ""}`
                    : "Not recorded"}
                {" · "}Workflow:{" "}
                {record.workflow_tool_release
                    ? `${record.workflow_tool_release.workflow_tool}${record.workflow_tool_release.version ? ` ${record.workflow_tool_release.version}` : ""}`
                    : "Not recorded"}
                {" · "}Literature:{" "}
                {record.literature ? (record.literature.title ?? record.literature.literature_ref) : "Not recorded"}
            </p>
        </article>
    )
}

function arrayRowState<T>(hasFlag: boolean, data: T[] | null | undefined): "not-present" | "empty" | "populated" {
    if (!hasFlag) return "not-present"
    if (!data || data.length === 0) return "empty"
    return "populated"
}

function TransportLazySection<T>({
    heading, records, available, notAvailableText, state, onOpen, rowState, children,
}: {
    heading: string
    records: TransportRecord[]
    available: boolean
    notAvailableText: string
    state: EntryListSectionState<T>
    onOpen: () => void
    rowState: (record: TransportRecord, data: T | undefined) => "not-present" | "empty" | "populated"
    children: (record: TransportRecord, data: T) => ReactNode
}) {
    const headingId = `section-${heading.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`
    if (records.length === 0) return null
    if (!available) {
        return (
            <section className="ledger-section" aria-labelledby={headingId}>
                <h2 id={headingId}>{heading}</h2>
                <p className="empty-projection">{notAvailableText}</p>
            </section>
        )
    }
    return (
        <details
            className="ledger-section"
            onToggle={(event) => { if ((event.target as HTMLDetailsElement).open) onOpen() }}
        >
            <summary><h2 id={headingId}>{heading}</h2></summary>
            <p className="section-note" role="status">
                {state.status === "idle" && "Expand to load this section from the archive."}
                {state.status === "loading" && "Loading…"}
                {state.status === "error" && state.message}
                {state.status === "ready" && `${heading} loaded.`}
            </p>
            {state.status === "ready" && records.map((record) => {
                const ref = record.transport.transport_ref
                const data = state.dataByRef.get(ref)
                const status = rowState(record, data)
                return (
                    <div key={ref} className="science-record">
                        <div className="science-record-heading">
                            <h3>{ref}</h3>
                        </div>
                        {status === "populated" && data !== undefined
                            ? children(record, data)
                            : (
                                <p className="empty-projection">
                                    {status === "not-present"
                                        ? "Not present for this record."
                                        : "The archive marks this record as having recorded evidence here; this view did not return it."}
                                </p>
                            )}
                    </div>
                )
            })}
        </details>
    )
}
