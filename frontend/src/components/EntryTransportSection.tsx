import type { ReactNode } from "react"
import { RecordNumbersProvider } from "./RecordNumbers"
import { numberedWords } from "../domain/recordNumbers"
import { useRecordNumbers } from "../hooks/useRecordNumbers"
import { RecordLink } from "./RecordLink"
import { RefsDisclosure } from "./RefsDisclosure"
import { supersessionRefs } from "../domain/supersession"
import "../conformer-group.css"
import "../entry-science.css"
import {
    loadEntryTransportSection,
    readTransportSectionField,
    type TransportListResponse,
    type TransportRecord,
    type TransportSectionToken,
} from "../api/transportApi"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { formatQuantity } from "../domain/quantityFormat"
import { useEntryListSection, type EntryListSectionState } from "../hooks/useEntryListSection"
import { useEntryTransport } from "../hooks/useEntryTransport"
import { everyOrThisPage } from "../domain/paging"
import { ListPager } from "./ListPager"
import type { ListMeta } from "../api/listEnvelope"
import type { PageRequest } from "../domain/paging"
import { Disclosure } from "./Disclosure"
import { LazyRowBody } from "./LazyRowBody"
import { SectionHeading } from "./PageSections"
import { QuantityValue, ROUNDED_NOTE } from "./QuantityValue"
import { RecordStatus } from "./RecordStatus"
import { SectionErrorBoundary } from "./SectionErrorBoundary"
import { SourceCalculationsTable } from "./SourceCalculationsTable"
import { SupersessionNotice } from "./SupersessionNotice"
import { LiteratureCitation } from "./LiteratureCitation"

// ---------------------------------------------------------------------------
// Same entry-scoped-LIST design as `EntryStatmechSection.tsx`, with only two
// real include-gated sections (`source_calculations`, `review` -> field
// `review_history`) — see `api/transportApi.ts`.
//
// For example, `spe_bcbdjwkip75yoziblpntwzblzu`
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
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")
const boolLabel = (value: boolean | null | undefined) => (value === null || value === undefined ? "not recorded" : value ? "Yes" : "No")

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
    const { state, listKey, meta, goTo, linkedNote } = useEntryTransport(entryRef)
    if (state.status === "ready") {
        return (
            <SectionErrorBoundary
                fallback={(
                    <section className="ledger-section" aria-labelledby="transport-heading">
                        <SectionHeading id="transport-heading">Transport</SectionHeading>
                        <p className="empty-projection" role="alert">
                            This section could not be displayed. The rest of this entry is unaffected.
                        </p>
                    </section>
                )}
            >
                <TransportList listKey={listKey} meta={meta} onPage={goTo} linkedNote={linkedNote} response={state.record} />
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

/** `carried` are the records on screen: a token the list was asked for is read from them instead of requested again. */
function useTransportSection<T>(entryRef: string, token: TransportSectionToken, carried?: TransportRecord[]) {
    return useEntryListSection<TransportRecord, T, TransportSectionToken>(
        entryRef,
        token,
        (ref, tok, signal) => (carried ? Promise.resolve(carried) : loadEntryTransportSection(ref, tok, signal)),
        (record) => record.transport.transport_ref,
        (record) => readTransportSectionField<T>(record, token),
    )
}

function TransportList({ listKey, meta, onPage, linkedNote, response }: {
    linkedNote: string | null
    /** Names the page on screen: the on-demand section hooks below key on it, so their data is never read across pages. */
    listKey: string
    meta: ListMeta | null
    onPage: (request: PageRequest) => void
    response: TransportListResponse
}) {
    const { records, review_summary: reviewSummary, pagination } = response

    const [sourceCalcsState, openSourceCalcs] = useTransportSection<TransportRecord["source_calculations"]>(listKey, "source_calculations", records)
    const [reviewState, openReview] = useTransportSection<TransportRecord["review_history"]>(listKey, "review")

    return (
        <RecordNumbersProvider refs={records.map((record) => record.transport.transport_ref)}>
            <section className="ledger-section" aria-labelledby="transport-heading">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited evidence</p>
                    <SectionHeading id="transport-heading">Transport</SectionHeading>
                    <p>
                        {everyOrThisPage(pagination, "transport record")}, each shown independently. Multiple
                        deposits are never merged, averaged, or reduced to one preferred value on this page.
                    </p>
                </div>
                <p className="note records-note">
                    {pagination.total} record{pagination.total === 1 ? "" : "s"}
                    {" · review: "}{reviewSummaryText(reviewSummary)}
                </p>
                {meta && <ListPager meta={meta} noun="transport record" onPage={onPage} note={linkedNote} label="Transport records paging" />}
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
                                <article className="science-record card" role="alert">
                                    <p className="empty-projection">
                                        A transport record could not be
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
                {(_record, rows) => <SourceCalculationsTable rows={rows} />}
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
                    <div className="table-scroll table-scroll--compact">
                        <table className="data-table" aria-label="Review history">
                            <thead><tr><th scope="col">Status</th><th scope="col">Reviewed at</th><th scope="col">Note</th></tr></thead>
                            <tbody>
                                {rows.map((row, index) => (
                                    <tr key={`review-${index}`}>
                                        <td data-label="Status">{statusLabel(row.status)}</td>
                                        <td data-label="Reviewed at">{isoDate(row.reviewed_at)}</td>
                                        <td data-label="Note">{row.note ?? "not recorded"}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                ) : <p className="empty-projection">The archive returned no review-history rows.</p>)}
            </TransportLazySection>
        </RecordNumbersProvider>
    )
}

function TransportRecordCard({ record }: { record: TransportRecord }) {
    const core = record.transport
    const numbers = useRecordNumbers()
    return (
        <article className="science-record card" aria-labelledby={`transport-heading-${core.transport_ref}`}>
            <div className="science-record-heading">
                <h3 id={`transport-heading-${core.transport_ref}`}>{numberedWords(`${statusLabel(core.scientific_origin)} transport record`, core.transport_ref, numbers)}</h3>
                <span className="value-pill value-pill--muted">{statusLabel(core.review.status)}</span>
            </div>

            {record.supersession && <SupersessionNotice kind="transport" supersession={record.supersession} />}

            <p className="note">
                Species entry: <RecordLink kind="species-entry" to={`/species-entries/${record.species.species_entry_ref}`} label="Species entry" />
                {record.species.canonical_smiles ? ` (${record.species.canonical_smiles})` : ""}
            </p>

            {/* The label already carries the unit in parens ("Sigma (Å)"), so
                the value itself is formatted with the unit suppressed
                (`unitOverride: null`) -- printing "3.800 Å" next to a
                "Sigma (Å)" label would say the unit twice. Only the
                precision half of the digits table applies here. */}
            {/* `.t-preserve-case` keeps these symbols and units in their own case. */}
            <dl className="kv-list">
                <div><dt className="t-preserve-case">Sigma (Å)</dt><dd><QuantityValue value={formatQuantity("transport_sigma_angstrom", core.sigma_angstrom, null)} /></dd></div>
                <div><dt className="t-preserve-case">Epsilon / k (K)</dt><dd><QuantityValue value={formatQuantity("transport_epsilon_over_k_k", core.epsilon_over_k_k, null)} /></dd></div>
                <div><dt className="t-preserve-case">Dipole (Debye)</dt><dd><QuantityValue value={formatQuantity("transport_dipole_debye", core.dipole_debye, null)} /></dd></div>
                <div><dt className="t-preserve-case">Polarizability (Å³)</dt><dd><QuantityValue value={formatQuantity("transport_polarizability_angstrom3", core.polarizability_angstrom3, null)} /></dd></div>
                <div><dt>Rotational relaxation</dt><dd><QuantityValue value={formatQuantity("transport_rotational_relaxation", core.rotational_relaxation, null)} /></dd></div>
                <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
            </dl>
            {[formatQuantity("transport_polarizability_angstrom3", core.polarizability_angstrom3), formatQuantity("transport_rotational_relaxation", core.rotational_relaxation)].some((quantity) => quantity?.full !== undefined) && <p className="note">{ROUNDED_NOTE}</p>}

            <dl className="kv-list">
                <div><dt>Source calculations</dt><dd>{record.evidence_summary.source_calculation_count}</dd></div>
                <div><dt>LJ parameters</dt><dd>{boolLabel(record.evidence_summary.has_lj_parameters)}</dd></div>
                <div><dt>Dipole moment</dt><dd>{boolLabel(record.evidence_summary.has_dipole_moment)}</dd></div>
                <div><dt>Polarizability</dt><dd>{boolLabel(record.evidence_summary.has_polarizability)}</dd></div>
                <div><dt>Rotational relaxation recorded</dt><dd>{boolLabel(record.evidence_summary.has_rotational_relaxation)}</dd></div>
                <div><dt>Literature source</dt><dd>{boolLabel(record.evidence_summary.has_literature_source)}</dd></div>
            </dl>

            <p className="note">
                Software:{" "}
                {softwareLabel(record.software_release) ?? "not recorded"}
                {" · "}Workflow:{" "}
                {toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}
                {" · "}Literature:{" "}
                {record.literature ? <LiteratureCitation literature={record.literature} /> : "not recorded"}
            </p>
            <RefsDisclosure refs={[{ label: "Transport record", value: core.transport_ref }, ...supersessionRefs(record.supersession)]} />
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
    const numbers = useRecordNumbers()
    const headingId = `section-${heading.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`
    if (records.length === 0) return null
    if (!available) {
        return (
            <section className="ledger-section" aria-labelledby={headingId}>
                <SectionHeading id={headingId}>{heading}</SectionHeading>
                <p className="empty-projection">{notAvailableText}</p>
            </section>
        )
    }
    // Shared `Disclosure` component, plain-text summary -- see the
    // matching note in `EntryStatmechSection.tsx`'s `StatmechLazySection`
    // for why: "Source calculations" here names a shared, cross-record
    // fetch trigger, not a section of already-present content, so it was
    // never a valid `SectionHeading`/ToC destination even before this was
    // fixed to stop nesting an `<h2>` inside a `<summary>`.
    return (
        <Disclosure
            id={headingId}
            className="ledger-section"
            summary={heading}
            onToggle={(open) => { if (open) onOpen() }}
        >
            <p className="note" role="status">
                {state.status === "idle" && "Expand to load this section from the archive."}
                {state.status === "loading" && "Loading…"}
                {state.status === "error" && state.message}
                {state.status === "ready" && `${heading} loaded.`}
            </p>
            {state.status === "ready" && records.map((record, index) => {
                const ref = record.transport.transport_ref
                const data = state.dataByRef.get(ref)
                const status = rowState(record, data)
                return (
                    <div key={ref} className="science-record card">
                        <div className="science-record-heading">
                            <h3>{`Transport record ${numbers.get(ref) ?? index + 1}`}</h3>
                        </div>
                        {status === "populated" && data !== undefined
                            ? (
                                <SectionErrorBoundary
                                    fallback={(
                                        <p className="empty-projection" role="alert">
                                            This row could not be displayed. Other records and sections on
                                            this page are unaffected.
                                        </p>
                                    )}
                                >
                                    <LazyRowBody record={record} data={data} render={children} />
                                </SectionErrorBoundary>
                            )
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
        </Disclosure>
    )
}
