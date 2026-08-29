import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import { lotLabel } from "../api/scientificSchemas"
import {
    loadEntryStatmechSection,
    readStatmechSectionField,
    type StatmechListResponse,
    type StatmechRecord,
    type StatmechSectionToken,
} from "../api/statmechApi"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { formatQuantity } from "../domain/quantityFormat"
import { useEntryListSection, type EntryListSectionState } from "../hooks/useEntryListSection"
import { useEntryStatmech } from "../hooks/useEntryStatmech"
import { LazyRowBody } from "./LazyRowBody"
import { QuantityValue } from "./QuantityValue"
import { RecordStatus } from "./RecordStatus"
import { SectionErrorBoundary } from "./SectionErrorBoundary"
import { SourceCalculationsTable } from "./SourceCalculationsTable"
import { TorsionsTable } from "./StatmechTorsionsTable"
import { SupersessionNotice } from "./SupersessionNotice"

// ---------------------------------------------------------------------------
// Same entry-scoped-LIST shape as `EntryThermoSection.tsx`, but statmech has
// six real include-gated sections (`source_calculations`/`torsions`/
// `electronic_levels`/`frequencies`/`conformers`/`review`, token `review`
// gating field `review_history`) — see `api/statmechApi.ts` for the
// measured mapping and the live proof the gate is real at the list level.
//
// Each on-demand section below is fetched ONCE PER TOKEN, shared across
// every record on the entry (`useEntryListSection`'s whole point) — opening
// any one record's "Torsions" disclosure loads torsions for every record on
// this entry in one request, because `include=torsions` is a request-time
// decision on the LIST endpoint, not an address for one record. Each
// record's own row still reads only its own key from the resulting map, and
// a record whose own `available_sections.has_torsions` is false is never
// silently painted with another record's data.
// ---------------------------------------------------------------------------

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")
const boolLabel = (value: boolean | null | undefined) => (value === null || value === undefined ? "not recorded" : value ? "Yes" : "No")

function reviewSummaryText(summary: StatmechListResponse["review_summary"]) {
    const parts: string[] = []
    if (summary.approved) parts.push(`${summary.approved} approved`)
    if (summary.under_review) parts.push(`${summary.under_review} under review`)
    if (summary.not_reviewed) parts.push(`${summary.not_reviewed} not reviewed`)
    if (summary.deprecated) parts.push(`${summary.deprecated} deprecated`)
    if (summary.rejected) parts.push(`${summary.rejected} rejected`)
    return parts.length > 0 ? parts.join(" · ") : "no records"
}

export function EntryStatmechSection({ entryRef }: { entryRef: string }) {
    const state = useEntryStatmech(entryRef)
    if (state.status === "ready") {
        return (
            <SectionErrorBoundary
                fallback={(
                    <section className="ledger-section" aria-labelledby="statmech-heading">
                        <h2 id="statmech-heading">Statistical mechanics</h2>
                        <p className="empty-projection" role="alert">
                            This section could not be displayed. The rest of this entry is unaffected.
                        </p>
                    </section>
                )}
            >
                <StatmechList entryRef={entryRef} response={state.record} />
            </SectionErrorBoundary>
        )
    }
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="statistical mechanics"
            loadingDetail="Retrieving the deposited statistical-mechanics records for this entry."
        />
    )
}

function useStatmechSection<T>(entryRef: string, token: StatmechSectionToken) {
    return useEntryListSection<StatmechRecord, T, StatmechSectionToken>(
        entryRef,
        token,
        (ref, tok, signal) => loadEntryStatmechSection(ref, tok, signal),
        (record) => record.statmech.statmech_ref,
        (record) => readStatmechSectionField<T>(record, token),
    )
}

function StatmechList({ entryRef, response }: { entryRef: string; response: StatmechListResponse }) {
    const { records, review_summary: reviewSummary, pagination } = response

    const [sourceCalcsState, openSourceCalcs] = useStatmechSection<StatmechRecord["source_calculations"]>(entryRef, "source_calculations")
    const [torsionsState, openTorsions] = useStatmechSection<StatmechRecord["torsions"]>(entryRef, "torsions")
    const [electronicLevelsState, openElectronicLevels] = useStatmechSection<StatmechRecord["electronic_levels"]>(entryRef, "electronic_levels")
    const [frequenciesState, openFrequencies] = useStatmechSection<StatmechRecord["frequencies"]>(entryRef, "frequencies")
    const [conformersState, openConformers] = useStatmechSection<StatmechRecord["conformers"]>(entryRef, "conformers")
    const [reviewState, openReview] = useStatmechSection<StatmechRecord["review_history"]>(entryRef, "review")

    return (
        <>
            <section className="ledger-section" aria-labelledby="statmech-heading">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited evidence</p>
                    <h2 id="statmech-heading">Statistical mechanics</h2>
                    <p>
                        Every statmech record deposited for this entry, each shown independently. Multiple
                        deposits are never merged, averaged, or reduced to one preferred value on this page.
                    </p>
                </div>
                <p className="records-note">
                    {pagination.total} record{pagination.total === 1 ? "" : "s"}
                    {pagination.total > pagination.returned ? ` (showing ${pagination.returned})` : ""}
                    {" · review: "}{reviewSummaryText(reviewSummary)}
                </p>
                {records.length === 0 ? (
                    <p className="empty-projection">No statistical-mechanics records are deposited for this entry.</p>
                ) : (
                    records.map((record) => (
                        <SectionErrorBoundary
                            key={record.statmech.statmech_ref}
                            fallback={(
                                <article className="science-record" role="alert">
                                    <p className="empty-projection">
                                        Record <code>{record.statmech.statmech_ref}</code> could not be
                                        displayed. Other records on this page are unaffected.
                                    </p>
                                </article>
                            )}
                        >
                            <StatmechRecordCard record={record} />
                        </SectionErrorBoundary>
                    ))
                )}
            </section>

            <StatmechLazySection
                heading="Source calculations"
                records={records}
                available={records.some((record) => record.available_sections.has_source_calculations)}
                notAvailableText="No source calculations are recorded for any statmech record on this entry."
                state={sourceCalcsState}
                onOpen={openSourceCalcs}
                rowState={(record, data) => arrayRowState(record.available_sections.has_source_calculations, data)}
            >
                {(_record, rows) => <SourceCalculationsTable rows={rows} />}
            </StatmechLazySection>

            <StatmechLazySection
                heading="Torsions"
                records={records}
                available={records.some((record) => record.available_sections.has_torsions)}
                notAvailableText="No torsions are recorded for any statmech record on this entry."
                state={torsionsState}
                onOpen={openTorsions}
                rowState={(record, data) => arrayRowState(record.available_sections.has_torsions, data)}
            >
                {(_record, rows) => <TorsionsTable rows={rows} />}
            </StatmechLazySection>

            <StatmechLazySection
                heading="Electronic levels"
                records={records}
                available={records.some((record) => record.available_sections.has_electronic_levels)}
                notAvailableText="No electronic levels are recorded for any statmech record on this entry."
                state={electronicLevelsState}
                onOpen={openElectronicLevels}
                rowState={(record, data) => arrayRowState(record.available_sections.has_electronic_levels, data)}
            >
                {(_record, rows) => (rows && rows.length > 0 ? (
                    <table className="stage-table" aria-label="Electronic levels">
                        <thead><tr><th scope="col">Level</th><th scope="col">Energy (cm⁻¹)</th><th scope="col">Degeneracy</th></tr></thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={`level-${row.level_index}`}>
                                    <td data-label="Level">{row.level_index}</td>
                                    <td data-label="Energy (cm⁻¹)">{row.energy_cm1}</td>
                                    <td data-label="Degeneracy">{row.degeneracy}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : <p className="empty-projection">The archive returned no electronic-level rows.</p>)}
            </StatmechLazySection>

            <StatmechLazySection
                heading="Frequencies"
                records={records}
                available={records.some((record) => record.available_sections.has_frequencies)}
                notAvailableText="No source frequency calculation is recorded for any statmech record on this entry."
                state={frequenciesState}
                onOpen={openFrequencies}
                rowState={(record, data) => scalarRowState(record.available_sections.has_frequencies, data)}
            >
                {(_record, summary) => summary ? (
                    <dl className="kv-list">
                        <div>
                            <dt>Source frequency calculations</dt>
                            <dd>
                                {(summary.source_freq_calculation_refs ?? []).length > 0
                                    ? (summary.source_freq_calculation_refs ?? []).map((ref, index) => (
                                        <span key={ref}>
                                            {index > 0 && ", "}
                                            <Link to={`/calculations/${ref}`}>{ref}</Link>
                                        </span>
                                    ))
                                    : "not recorded"}
                            </dd>
                        </div>
                        <div>
                            <dt>Frequency scale factor</dt>
                            <dd><QuantityValue value={formatQuantity("statmech_frequency_scale_factor", summary.frequency_scale_factor_value)} /></dd>
                        </div>
                        <div><dt>Note</dt><dd>{summary.note ?? "not recorded"}</dd></div>
                    </dl>
                ) : <p className="empty-projection">The archive returned no frequencies summary.</p>}
            </StatmechLazySection>

            <StatmechLazySection
                heading="Conformer context"
                records={records}
                available={records.some((record) => record.available_sections.has_conformers)}
                notAvailableText="No conformer-basin context is recorded for any statmech record on this entry."
                state={conformersState}
                onOpen={openConformers}
                rowState={(record, data) => arrayRowState(record.available_sections.has_conformers, data)}
            >
                {(_record, rows) => (rows && rows.length > 0 ? (
                    <ul className="checklist">
                        {rows.map((row) => (
                            <li key={row.conformer_group_ref}>
                                <Link to={`/conformer-groups/${row.conformer_group_ref}`}>{row.label ?? row.conformer_group_ref}</Link>
                                {" "}(<code>{row.conformer_group_ref}</code>)
                            </li>
                        ))}
                    </ul>
                ) : <p className="empty-projection">The archive returned no conformer-context rows.</p>)}
            </StatmechLazySection>

            <StatmechLazySection
                heading="Review history"
                records={records}
                available={records.some((record) => record.available_sections.has_review)}
                notAvailableText="No review history is recorded for any statmech record on this entry."
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
                                    <td data-label="Note">{row.note ?? "not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                ) : <p className="empty-projection">The archive returned no review-history rows.</p>)}
            </StatmechLazySection>
        </>
    )
}

function StatmechRecordCard({ record }: { record: StatmechRecord }) {
    const core = record.statmech
    return (
        <article className="science-record" aria-labelledby={`statmech-heading-${core.statmech_ref}`}>
            <div className="science-record-heading">
                <h3 id={`statmech-heading-${core.statmech_ref}`}>{statusLabel(core.scientific_origin)} statmech record</h3>
                <span className="review-badge">{statusLabel(core.review.status)}</span>
                <code>{core.statmech_ref}</code>
            </div>

            {record.supersession && <SupersessionNotice supersession={record.supersession} />}

            <dl className="kv-list">
                <div><dt>Statmech treatment</dt><dd>{core.statmech_treatment ? statusLabel(core.statmech_treatment) : "not recorded"}</dd></div>
                <div><dt>Rigid-rotor kind</dt><dd>{core.rigid_rotor_kind ? statusLabel(core.rigid_rotor_kind) : "not recorded"}</dd></div>
                <div><dt>Point group</dt><dd>{core.point_group ?? "not recorded"}</dd></div>
                <div><dt>External symmetry</dt><dd>{core.external_symmetry ?? "not recorded"}</dd></div>
                <div><dt>Linear molecule</dt><dd>{boolLabel(core.is_linear)}</dd></div>
                <div><dt>Optical isomers</dt><dd>{core.optical_isomers ?? "not recorded"}</dd></div>
                <div>
                    <dt>Frequency scale factor</dt>
                    <dd><QuantityValue value={formatQuantity("statmech_frequency_scale_factor", core.frequency_scale_factor_value)} /></dd>
                </div>
                <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
            </dl>

            <SubjectLine record={record} />

            <dl className="kv-list">
                <div><dt>Source calculations</dt><dd>{record.evidence_summary.source_calculation_count}</dd></div>
                <div>
                    <dt>Has opt / freq / sp</dt>
                    <dd>
                        {boolLabel(record.evidence_summary.has_opt_calculation)} / {boolLabel(record.evidence_summary.has_freq_calculation)}
                        {" / "}{boolLabel(record.evidence_summary.has_sp_calculation)}
                    </dd>
                </div>
                <div><dt>SP from optimization</dt><dd>{boolLabel(record.evidence_summary.sp_from_optimization)}</dd></div>
                <div><dt>Rotor scans</dt><dd>{boolLabel(record.evidence_summary.has_rotor_scans)} ({record.evidence_summary.torsion_count} torsions)</dd></div>
            </dl>

            {record.frequency_scale_factor && (
                <p className="section-note">
                    Frequency scale factor <code>{record.frequency_scale_factor.frequency_scale_factor_ref}</code>:
                    {/* `record.frequency_scale_factor.value` is a non-nullable `z.number()`
                        (`api/statmechApi.ts:120`), so `formatQuantity` can never return `null`
                        here -- a `?? record.frequency_scale_factor.value` fallback would be
                        dead code that, if it ever DID fire, would silently reprint the exact
                        unrounded-double defect this file exists to fix. The `!` documents the
                        invariant instead of hiding a false safety net behind it. */}
                    {` ${formatQuantity("statmech_frequency_scale_factor", record.frequency_scale_factor.value)!.value} (${statusLabel(record.frequency_scale_factor.scale_kind)})`}
                    {record.frequency_scale_factor.level_of_theory ? ` · ${lotLabel(record.frequency_scale_factor.level_of_theory)}` : ""}
                </p>
            )}

            <p className="section-note">
                Software:{" "}
                {softwareLabel(record.software_release) ?? "not recorded"}
                {" · "}Workflow:{" "}
                {toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}
            </p>
        </article>
    )
}

function SubjectLine({ record }: { record: StatmechRecord }) {
    if (record.species) {
        return (
            <p className="section-note">
                Species entry: <Link to={`/species-entries/${record.species.species_entry_ref}`}>{record.species.species_entry_ref}</Link>
                {record.species.canonical_smiles ? ` (${record.species.canonical_smiles})` : ""}
            </p>
        )
    }
    if (record.transition_state) {
        return (
            <p className="section-note">
                Transition-state entry: <code>{record.transition_state.transition_state_entry_ref}</code>
                {" — no dedicated page exists yet for this record kind."}
            </p>
        )
    }
    return null
}

function arrayRowState<T>(hasFlag: boolean, data: T[] | null | undefined): "not-present" | "empty" | "populated" {
    if (!hasFlag) return "not-present"
    if (!data || data.length === 0) return "empty"
    return "populated"
}

function scalarRowState<T>(hasFlag: boolean, data: T | null | undefined): "not-present" | "empty" | "populated" {
    if (!hasFlag) return "not-present"
    if (data === null || data === undefined) return "empty"
    return "populated"
}

function StatmechLazySection<T>({
    heading, records, available, notAvailableText, state, onOpen, rowState, children,
}: {
    heading: string
    records: StatmechRecord[]
    available: boolean
    notAvailableText: string
    state: EntryListSectionState<T>
    onOpen: () => void
    rowState: (record: StatmechRecord, data: T | undefined) => "not-present" | "empty" | "populated"
    children: (record: StatmechRecord, data: T) => ReactNode
}) {
    const headingId = `section-${heading.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`
    // An entry with no statmech records at all has nothing for six separate
    // "No X are recorded…" sections to say beyond what the eager empty
    // message above already said once. Mirrors `TransportLazySection`.
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
                const ref = record.statmech.statmech_ref
                const data = state.dataByRef.get(ref)
                const status = rowState(record, data)
                return (
                    <div key={ref} className="science-record">
                        <div className="science-record-heading">
                            <h3>{ref}</h3>
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
        </details>
    )
}
