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
import { useEntryListSection, type EntryListSectionState } from "../hooks/useEntryListSection"
import { useEntryStatmech } from "../hooks/useEntryStatmech"
import { RecordStatus } from "./RecordStatus"
import { SectionErrorBoundary } from "./SectionErrorBoundary"
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
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "Not recorded")
const boolLabel = (value: boolean | null | undefined) => (value === null || value === undefined ? "Not recorded" : value ? "Yes" : "No")

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
                {(_record, rows) => (rows && rows.length > 0 ? (
                    <div className="table-scroll">
                        <table className="stage-table" aria-label="Torsions">
                            <thead>
                                <tr>
                                    <th scope="col">Index</th>
                                    <th scope="col">Treatment</th>
                                    <th scope="col">Dimension</th>
                                    <th scope="col">Symmetry number</th>
                                    <th scope="col">Source scan</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((row) => (
                                    <tr key={`torsion-${row.torsion_index}`}>
                                        <td data-label="Index">{row.torsion_index}</td>
                                        <td data-label="Treatment">{row.treatment_kind ? statusLabel(row.treatment_kind) : "Not recorded"}</td>
                                        <td data-label="Dimension">{row.dimension}</td>
                                        <td data-label="Symmetry number">{row.symmetry_number ?? "Not recorded"}</td>
                                        <td data-label="Source scan">
                                            {row.source_scan_calculation_ref
                                                ? <Link to={`/calculations/${row.source_scan_calculation_ref}`}>{row.source_scan_calculation_ref}</Link>
                                                : "Not recorded"}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                ) : <p className="empty-projection">The archive returned no torsion rows.</p>)}
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
                        <thead><tr><th scope="col">Level</th><th scope="col">Energy (cm-1)</th><th scope="col">Degeneracy</th></tr></thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={`level-${row.level_index}`}>
                                    <td data-label="Level">{row.level_index}</td>
                                    <td data-label="Energy (cm-1)">{row.energy_cm1}</td>
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
                                    : "Not recorded"}
                            </dd>
                        </div>
                        <div><dt>Frequency scale factor</dt><dd>{summary.frequency_scale_factor_value ?? "Not recorded"}</dd></div>
                        <div><dt>Note</dt><dd>{summary.note ?? "Not recorded"}</dd></div>
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
                                    <td data-label="Note">{row.note ?? "Not recorded"}</td>
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
                <div><dt>Statmech treatment</dt><dd>{core.statmech_treatment ? statusLabel(core.statmech_treatment) : "Not recorded"}</dd></div>
                <div><dt>Rigid-rotor kind</dt><dd>{core.rigid_rotor_kind ? statusLabel(core.rigid_rotor_kind) : "Not recorded"}</dd></div>
                <div><dt>Point group</dt><dd>{core.point_group ?? "Not recorded"}</dd></div>
                <div><dt>External symmetry</dt><dd>{core.external_symmetry ?? "Not recorded"}</dd></div>
                <div><dt>Linear molecule</dt><dd>{boolLabel(core.is_linear)}</dd></div>
                <div><dt>Optical isomers</dt><dd>{core.optical_isomers ?? "Not recorded"}</dd></div>
                <div><dt>Frequency scale factor</dt><dd>{core.frequency_scale_factor_value ?? "Not recorded"}</dd></div>
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
                    {` ${record.frequency_scale_factor.value} (${statusLabel(record.frequency_scale_factor.scale_kind)})`}
                    {record.frequency_scale_factor.level_of_theory ? ` · ${lotLabel(record.frequency_scale_factor.level_of_theory)}` : ""}
                </p>
            )}

            <p className="section-note">
                Software:{" "}
                {record.software_release
                    ? `${record.software_release.software}${record.software_release.version ? ` ${record.software_release.version}` : ""}`
                    : "Not recorded"}
                {" · "}Workflow:{" "}
                {record.workflow_tool_release
                    ? `${record.workflow_tool_release.workflow_tool}${record.workflow_tool_release.version ? ` ${record.workflow_tool_release.version}` : ""}`
                    : "Not recorded"}
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
