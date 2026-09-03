import type { ReactNode } from "react"
import { useEffect } from "react"
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
import type { ConformerProjection } from "../api/speciesEntryApi"
import { conformerLabel, partitionByConformerLink, statmechConformerGroupRefs } from "../domain/conformerEvidence"
import { groupByFingerprint, statmechRecordFingerprint } from "../domain/identicalRecordGroups"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { formatQuantity } from "../domain/quantityFormat"
import { deriveStatmechConformer } from "../domain/statmechConformerDerivation"
import { useEntryListSection, type EntryListSectionState } from "../hooks/useEntryListSection"
import { useEntryStatmech } from "../hooks/useEntryStatmech"
import { ConformerAttributionGroups } from "./ConformerAttributionGroups"
import { LazyRowBody } from "./LazyRowBody"
import { SectionHeading } from "./PageSections"
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

export function EntryStatmechSection({ entryRef, conformer, conformers }: {
    entryRef: string
    conformer?: ConformerProjection | null
    conformers?: ConformerProjection[]
}) {
    const state = useEntryStatmech(entryRef)
    if (state.status === "ready") {
        return (
            <SectionErrorBoundary
                fallback={(
                    <section className="ledger-section" aria-labelledby="statmech-heading">
                        <SectionHeading id="statmech-heading">Statistical mechanics</SectionHeading>
                        <p className="empty-projection" role="alert">
                            This section could not be displayed. The rest of this entry is unaffected.
                        </p>
                    </section>
                )}
            >
                <StatmechList entryRef={entryRef} response={state.record} conformer={conformer} conformers={conformers ?? []} />
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

function StatmechList({ entryRef, response, conformer, conformers }: {
    entryRef: string
    response: StatmechListResponse
    conformer?: ConformerProjection | null
    conformers: ConformerProjection[]
}) {
    const { records, review_summary: reviewSummary, pagination } = response

    const [sourceCalcsState, openSourceCalcs] = useStatmechSection<StatmechRecord["source_calculations"]>(entryRef, "source_calculations")
    const [torsionsState, openTorsions] = useStatmechSection<StatmechRecord["torsions"]>(entryRef, "torsions")
    const [electronicLevelsState, openElectronicLevels] = useStatmechSection<StatmechRecord["electronic_levels"]>(entryRef, "electronic_levels")
    const [frequenciesState, openFrequencies] = useStatmechSection<StatmechRecord["frequencies"]>(entryRef, "frequencies")
    const [conformersState, openConformers] = useStatmechSection<StatmechRecord["conformers"]>(entryRef, "conformers")
    const [reviewState, openReview] = useStatmechSection<StatmechRecord["review_history"]>(entryRef, "review")

    // Unlike the six disclosures below (opened only when a reader expands
    // them), a selected conformer needs this same `conformers` include
    // token up front -- it is the ONE real (not inferred) conformer link
    // this API exposes today, per `domain/conformerEvidence.ts`.
    useEffect(() => {
        if (conformer) openConformers()
    }, [conformer, openConformers])

    // `source_calculations` is fetched eagerly too (not only when the
    // "Source calculations" disclosure is opened) -- every record card now
    // derives its own conformer from this data
    // (`domain/statmechConformerDerivation.ts`), which the card shows by
    // default, not behind a click. One request, shared across every record
    // on the entry, the same tradeoff the `conformers` effect above already
    // makes.
    useEffect(() => {
        openSourceCalcs()
    }, [openSourceCalcs])

    // Frequencies too, and for the same reason `source_calculations` is
    // eager: the vibrational-frequency evidence is the CONTENT of a
    // statmech record, not an optional extra -- the owner's report was
    // exactly this, seven ~500px cards showing point group and symmetry
    // while the frequencies sat in a collapsed "Expand to load this
    // section" disclosure ~6000px below all of them. `FrequenciesBlock`
    // (on each card, below) now renders this by default, gated per-record
    // by that record's own `available_sections.has_frequencies` -- never a
    // separate global disclosure a reader has to find and open first.
    useEffect(() => {
        openFrequencies()
    }, [openFrequencies])

    return (
        <>
            <section className="ledger-section" aria-labelledby="statmech-heading">
                <div className="ledger-heading">
                    <p className="eyebrow">Deposited evidence</p>
                    <SectionHeading id="statmech-heading">Statistical mechanics</SectionHeading>
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
                ) : conformer ? (
                    <ConformerScopedStatmechRecords
                        conformer={conformer}
                        conformers={conformers}
                        records={records}
                        conformersState={conformersState}
                        sourceCalcsState={sourceCalcsState}
                        frequenciesState={frequenciesState}
                    />
                ) : (
                    renderStatmechRecords(records, conformers, sourceCalcsState, frequenciesState)
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

function statmechRecordFallback(record: StatmechRecord) {
    return (
        <article className="science-record" role="alert">
            <p className="empty-projection">
                Record <code>{record.statmech.statmech_ref}</code> could not be
                displayed. Other records on this page are unaffected.
            </p>
        </article>
    )
}

function renderStatmechRecord(
    record: StatmechRecord,
    conformers: ConformerProjection[],
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>,
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>,
) {
    return (
        <SectionErrorBoundary key={record.statmech.statmech_ref} fallback={statmechRecordFallback(record)}>
            <StatmechRecordCard record={record} conformers={conformers} sourceCalcsState={sourceCalcsState} frequenciesState={frequenciesState} />
        </SectionErrorBoundary>
    )
}

/**
 * Renders a WHOLE bucket of records at once, grouping it -- LOCALLY, per
 * call -- by scientific content (`groupByFingerprint`/
 * `statmechRecordFingerprint`) rather than mapping one record to one card.
 * Mirrors `EntryThermoSection.tsx`'s `makeThermoGroupedRenderer`: grouping
 * happens per bucket (once for the flat no-conformer-selected list, once
 * per `ConformerAttributionGroups` bucket) so a record traced to a
 * different conformer is never folded into another bucket's
 * identical-values card just because the two report the same numbers.
 */
function renderStatmechRecords(
    records: StatmechRecord[],
    conformers: ConformerProjection[],
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>,
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>,
): ReactNode {
    return groupByFingerprint(records, statmechRecordFingerprint).map((group) => {
        if (group.records.length === 1) {
            return renderStatmechRecord(group.records[0], conformers, sourceCalcsState, frequenciesState)
        }
        const representative = group.records[0]
        return (
            <SectionErrorBoundary key={representative.statmech.statmech_ref} fallback={statmechRecordFallback(representative)}>
                <IdenticalStatmechRecordsCard
                    records={group.records}
                    conformers={conformers}
                    sourceCalcsState={sourceCalcsState}
                    frequenciesState={frequenciesState}
                />
            </SectionErrorBoundary>
        )
    })
}

/**
 * Statmech's REAL conformer link (`include=conformers`, see
 * `domain/conformerEvidence.ts`) has to be fetched before it can be used to
 * partition anything -- but a failed or in-flight refetch of that ONE
 * optional include token must never delete records the list already has in
 * hand. While non-ready, this renders the flat, ungrouped record list
 * (identical to having no conformer selected) with a short status line
 * above it -- never zero records for a count line that says otherwise.
 */
function ConformerScopedStatmechRecords({ conformer, conformers, records, conformersState, sourceCalcsState, frequenciesState }: {
    conformer: ConformerProjection
    conformers: ConformerProjection[]
    records: StatmechRecord[]
    conformersState: EntryListSectionState<StatmechRecord["conformers"]>
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>
}) {
    if (conformersState.status !== "ready") {
        return (
            <>
                <p className="section-note" role="status">
                    {conformersState.status === "error"
                        ? `${conformersState.message} Showing every record for this entry, ungrouped, until the conformer link resolves.`
                        : "Resolving conformer links… showing every record for this entry, ungrouped, in the meantime."}
                </p>
                {renderStatmechRecords(records, conformers, sourceCalcsState, frequenciesState)}
            </>
        )
    }
    return (
        <ConformerAttributionGroups
            attribution={partitionByConformerLink(
                records,
                conformers,
                conformer.conformer_group.conformer_group_ref,
                (record) => statmechConformerGroupRefs(conformersState.dataByRef.get(record.statmech.statmech_ref)),
            )}
            selectedLabel={conformerLabel(conformer)}
            renderRecords={(bucketRecords) => renderStatmechRecords(bucketRecords, conformers, sourceCalcsState, frequenciesState)}
            thisConformerNote="Computed against this conformer's own basin, per the archive's own conformer link."
            thisConformerEmptyText="No statmech record is linked to this conformer yet."
            otherConformerNote="Computed against a different conformer's basin than the one selected above."
            noLinkNote="No conformer is linked to this record -- shown here regardless of which conformer is selected."
            noLinkEmptyText="No entry-level statmech record is deposited for this entry."
        />
    )
}

function StatmechRecordCard({ record, conformers, sourceCalcsState, frequenciesState }: {
    record: StatmechRecord
    conformers: ConformerProjection[]
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>
}) {
    const core = record.statmech
    return (
        <article className="science-record" aria-labelledby={`statmech-heading-${core.statmech_ref}`}>
            <div className="science-record-heading">
                <h3 id={`statmech-heading-${core.statmech_ref}`}>{statusLabel(core.scientific_origin)} statmech record</h3>
                <span className="review-badge">{statusLabel(core.review.status)}</span>
                <code>{core.statmech_ref}</code>
            </div>
            <StatmechRecordBody record={record} conformers={conformers} sourceCalcsState={sourceCalcsState} frequenciesState={frequenciesState} />
        </article>
    )
}

/**
 * Everything under a statmech card's own heading -- shared by the normal
 * one-record `StatmechRecordCard` above and `IdenticalStatmechRecordsCard`
 * below (which shows this body ONCE, from a representative record, for a
 * whole group of scientifically-identical deposits).
 *
 * `showRecordEvidence` is `false` only for the group's own shared body
 * (`IdenticalStatmechRecordsCard`) -- the finding measured live: the
 * representative's "Record software: Arkane · Workflow: ARC 1.1.0", its own
 * source-calculation count, and its single freq-calculation ref were all
 * printed as though they held for the whole group, when in fact six of the
 * seven ethene records cite different freq calculations and different
 * software. None of `evidence_summary`, `FrequenciesBlock`, or
 * `software_release`/`workflow_tool_release` participates in
 * `statmechRecordFingerprint` -- they are real per-record facts, not shared
 * ones -- so the group body hides them and `IdenticalStatmechGroupRefs`
 * lists every member's own opt/freq/sp source calculations, frequency
 * calculation, software, and workflow tool per ref instead. Every other
 * caller shows this, unchanged.
 */
function StatmechRecordBody({ record, conformers, sourceCalcsState, frequenciesState, showRecordEvidence = true }: {
    record: StatmechRecord
    conformers: ConformerProjection[]
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>
    showRecordEvidence?: boolean
}) {
    const core = record.statmech
    return (
        <>
            {record.supersession && <SupersessionNotice supersession={record.supersession} />}

            <dl className="kv-list">
                <div>
                    <dt>Statmech treatment</dt>
                    <dd>{core.statmech_treatment ? <span className="value-pill">{statusLabel(core.statmech_treatment)}</span> : "not recorded"}</dd>
                </div>
                <div>
                    <dt>Rigid-rotor kind</dt>
                    <dd>{core.rigid_rotor_kind ? <span className="value-pill">{statusLabel(core.rigid_rotor_kind)}</span> : "not recorded"}</dd>
                </div>
                <div><dt>Point group</dt><dd>{core.point_group ?? "not recorded"}</dd></div>
                <div><dt>External symmetry</dt><dd>{core.external_symmetry ?? "not recorded"}</dd></div>
                <div><dt>Linear molecule</dt><dd>{boolLabel(core.is_linear)}</dd></div>
                <div><dt>Optical isomers</dt><dd>{core.optical_isomers ?? "not recorded"}</dd></div>
                {/* `created_at` is excluded from `statmechRecordFingerprint` -- it's a
                    real per-record fact, not a shared one, so the group's shared body
                    (`showRecordEvidence={false}`) omits it rather than printing one
                    member's deposit date as though it held for the whole group. */}
                {showRecordEvidence && <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>}
            </dl>

            <SubjectLine record={record} />
            <DerivedConformerNote record={record} conformers={conformers} sourceCalcsState={sourceCalcsState} />

            {showRecordEvidence && (
                <>
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
                    <FrequenciesBlock record={record} state={frequenciesState} />
                    <p className="section-note">
                        Record software:{" "}
                        {softwareLabel(record.software_release) ?? "not recorded"}
                        {" · "}Workflow:{" "}
                        {toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}
                    </p>
                </>
            )}

            <FrequencyScaleFactorDetail core={core} fsf={record.frequency_scale_factor} />
        </>
    )
}

/**
 * The archive's own frequency evidence for this record, on the card itself
 * and loaded by default -- previously this lived ~6000px below all seven
 * record cards, behind a "Expand to load this section" disclosure, while
 * every card showed point group, symmetry, and provenance flags but never
 * the frequencies themselves. `record.available_sections.has_frequencies`
 * gates this per record -- `false` means genuinely nothing to show, so
 * this renders nothing at all (never a heading over an empty box).
 *
 * The raw per-mode frequency NUMBERS are not on this wire shape at all:
 * `frequencies` (`include=frequencies`) is a POINTER to the source freq
 * calculation(s) plus the resolved scale factor -- see
 * `StatmechFrequenciesSummary`'s own docstring in
 * `backend/app/schemas/reads/scientific_statmech.py` ("Frequencies are not
 * stored on statmech rows -- they live on calc_freq_result of the source
 * freq calculation"). This block surfaces exactly what this record's own
 * wire shape carries -- the source calculation(s) to follow for the
 * numbers -- not a frequency list this endpoint never returns. Any `note`
 * the server attaches is rendered as plain, reader-facing wording
 * (`statmechNoteText` below), never the server's own API-path phrasing --
 * a chemist reading this card should never see an endpoint URL.
 */
function FrequenciesBlock({ record, state }: {
    record: StatmechRecord
    state: EntryListSectionState<StatmechRecord["frequencies"]>
}) {
    if (!record.available_sections.has_frequencies) return null
    const ref = record.statmech.statmech_ref
    if (state.status === "idle" || state.status === "loading") {
        return <p className="section-note" role="status">Loading frequency source…</p>
    }
    if (state.status === "error") {
        return <p className="section-note" role="alert">{state.message}</p>
    }
    const summary = state.dataByRef.get(ref)
    if (!summary) {
        return (
            <p className="empty-projection">
                The archive marks this record as having frequency evidence; this view did not return it.
            </p>
        )
    }
    const freqRefs = summary.source_freq_calculation_refs ?? []
    return (
        <section aria-labelledby={`freq-${ref}`}>
            <h4 className="model-block-heading" id={`freq-${ref}`}>Frequencies</h4>
            <dl className="kv-list">
                <div>
                    <dt>Source frequency calculations</dt>
                    <dd>
                        {freqRefs.length > 0
                            ? freqRefs.map((freqRef, index) => (
                                <span key={freqRef}>
                                    {index > 0 && ", "}
                                    <Link to={`/calculations/${freqRef}`}>{freqRef}</Link>
                                </span>
                            ))
                            : "not recorded"}
                    </dd>
                </div>
            </dl>
            {/* The server's own `note` field carries developer-facing wording
                (an API path to fetch per-mode arrays) meant for a client
                author, not a chemist reading this card -- never rendered
                verbatim. This fixed, reader-facing line says the same thing
                in plain language instead. */}
            {freqRefs.length > 0 && (
                <p className="section-note">Per-mode frequency values are recorded on the source calculation, not summarized here.</p>
            )}
        </section>
    )
}

// Suffix applied to the group card's own heading id, so it never collides
// with the same representative record's plain `statmech-heading-<ref>` id
// when that record is rendered a second time, unmodified, inside "Show
// all" below.
const STATMECH_GROUP_ID_SUFFIX = "-group"

/**
 * One card for N deposited statmech records that report IDENTICAL
 * scientific content (`domain/identicalRecordGroups.ts`'s
 * `statmechRecordFingerprint`) -- the finding measured live: seven ethene
 * statmech records, all D2h, σ=4, 0.9990 fundamental, b3lyp/def2tzvp,
 * rendered as seven full ~500px cards. The shared scientific content
 * renders ONCE, from the group's first record; every member reports the
 * identical body by construction of the fingerprint -- via the same
 * `StatmechRecordBody` a single-record card uses. "Show all" mounts every
 * member's own full, unmodified card on demand.
 *
 * Record-level evidence is NOT part of that shared display:
 * `showRecordEvidence={false}` drops the representative's own source-
 * calculation evidence, frequency-calculation pointer, and record
 * software/workflow tool from the shared body (the live case this fix was
 * written against: seven ethene records citing SEVEN DIFFERENT freq
 * calculations, six with no recorded software and one Arkane -- printing
 * the representative's would attribute them to all seven, which is
 * false), and `IdenticalStatmechGroupRefs` prints every member's own
 * source calculations, frequency calculation, software, and workflow
 * tool, per ref, directly on the card -- never behind "Show all".
 */
function IdenticalStatmechRecordsCard({ records, conformers, sourceCalcsState, frequenciesState }: {
    records: StatmechRecord[]
    conformers: ConformerProjection[]
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>
}) {
    const representative = records[0]
    const anchorId = `statmech-heading-${representative.statmech.statmech_ref}${STATMECH_GROUP_ID_SUFFIX}`
    return (
        <article className="science-record identical-record-group" aria-labelledby={anchorId}>
            <div className="science-record-heading">
                <h3 id={anchorId}>{statusLabel(representative.statmech.scientific_origin)} statmech record</h3>
                <span className="review-badge">{records.length} records with identical values</span>
            </div>
            <p className="section-note">
                {records.length} deposited records report identical point group, symmetry, and frequency
                scale factor values — shown once below. Each record's own ref and provenance -- including
                which source calculations and frequency calculation it cites -- is listed per ref in the
                table below, never collapsed into one; every record stays individually reachable, and none
                was merged, averaged, or dropped in favor of another.
            </p>
            <StatmechRecordBody
                record={representative}
                conformers={conformers}
                sourceCalcsState={sourceCalcsState}
                frequenciesState={frequenciesState}
                showRecordEvidence={false}
            />
            <IdenticalStatmechGroupRefs records={records} sourceCalcsState={sourceCalcsState} frequenciesState={frequenciesState} />
            <details className="identical-record-group-detail">
                <summary>Show all {records.length} records individually</summary>
                {records.map((record) => (
                    <SectionErrorBoundary key={record.statmech.statmech_ref} fallback={statmechRecordFallback(record)}>
                        <StatmechRecordCard record={record} conformers={conformers} sourceCalcsState={sourceCalcsState} frequenciesState={frequenciesState} />
                    </SectionErrorBoundary>
                ))}
            </details>
        </article>
    )
}

/**
 * One calculation-ref cell: every ref this record's own source-calculation
 * or frequency evidence names for `role`/`kind`, linked, or "not recorded"
 * once the backing include has resolved and genuinely found none. While
 * the shared, entry-scoped `include` this cell depends on is still
 * loading, this says so rather than falsely reading as "not recorded".
 */
function RecordCalcRefsCell({ refs }: { refs: string[] | "loading" }) {
    if (refs === "loading") return <>loading…</>
    if (refs.length === 0) return <>not recorded</>
    return (
        <>
            {refs.map((ref, index) => (
                <span key={ref}>
                    {index > 0 && ", "}
                    <Link to={`/calculations/${ref}`}>{ref}</Link>
                </span>
            ))}
        </>
    )
}

function sourceCalcRefsByRole(
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>,
    statmechRef: string,
    role: string,
): string[] | "loading" {
    if (sourceCalcsState.status !== "ready") return "loading"
    return (sourceCalcsState.dataByRef.get(statmechRef) ?? [])
        .filter((calc) => calc.role === role)
        .map((calc) => calc.calculation_ref)
}

function freqCalcRefs(
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>,
    statmechRef: string,
): string[] | "loading" {
    if (frequenciesState.status !== "ready") return "loading"
    return frequenciesState.dataByRef.get(statmechRef)?.source_freq_calculation_refs ?? []
}

/**
 * Every ref in an identical-values group, with its OWN provenance -- see
 * `EntryThermoSection.tsx`'s `IdenticalThermoGroupRefs` for the identical
 * rule: grouping on scientific content must never collapse provenance
 * that genuinely differs record to record. The opt/freq/sp source
 * calculations come from the entry-scoped `source_calculations` include
 * (`sourceCalcsState`, filtered per record by `role`); the frequency
 * calculation comes from the separate `frequencies` include
 * (`frequenciesState`) -- both are fetched eagerly for the whole entry
 * already (see `StatmechList`), so no extra request is made rendering this
 * table.
 */
function IdenticalStatmechGroupRefs({ records, sourceCalcsState, frequenciesState }: {
    records: StatmechRecord[]
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>
    frequenciesState: EntryListSectionState<StatmechRecord["frequencies"]>
}) {
    const headingId = `identical-refs-${records[0].statmech.statmech_ref}`
    return (
        <section aria-labelledby={headingId}>
            <h4 className="model-block-heading" id={headingId}>Records in this group</h4>
            <div className="table-scroll table-scroll--compact">
                <table className="stage-table" aria-label="Records sharing these identical values">
                    <thead>
                        <tr>
                            <th scope="col">Ref</th>
                            <th scope="col">Review</th>
                            <th scope="col">Opt calc</th>
                            <th scope="col">Freq calc</th>
                            <th scope="col">SP calc</th>
                            <th scope="col">Frequencies</th>
                            <th scope="col">Record software</th>
                            <th scope="col">Workflow tool</th>
                        </tr>
                    </thead>
                    <tbody>
                        {records.map((record) => {
                            const ref = record.statmech.statmech_ref
                            return (
                                <tr key={ref}>
                                    <td data-label="Ref"><code>{ref}</code></td>
                                    <td data-label="Review">{statusLabel(record.statmech.review.status)}</td>
                                    <td data-label="Opt calc"><RecordCalcRefsCell refs={sourceCalcRefsByRole(sourceCalcsState, ref, "opt")} /></td>
                                    <td data-label="Freq calc"><RecordCalcRefsCell refs={sourceCalcRefsByRole(sourceCalcsState, ref, "freq")} /></td>
                                    <td data-label="SP calc"><RecordCalcRefsCell refs={sourceCalcRefsByRole(sourceCalcsState, ref, "sp")} /></td>
                                    <td data-label="Frequencies"><RecordCalcRefsCell refs={freqCalcRefs(frequenciesState, ref)} /></td>
                                    <td data-label="Record software">{softwareLabel(record.software_release) ?? "not recorded"}</td>
                                    <td data-label="Workflow tool">{toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}</td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
        </section>
    )
}

/**
 * The frequency scale factor together with its OWN provenance -- LoT and
 * software (with version), plus the scale kind as a pill and the factor's
 * own ref on its own row. Previously one run-on sentence
 * ("Frequency scale factor <ref>: <value> (<kind>) · <lot> · derived for
 * <software>") that put an identifier inline with everything else -- the
 * owner: "needs to better show the freq scale with the LoT and software
 * (with version) and its weird having the ref on the same line".
 *
 * `fsf.software` is the software the scale factor was DERIVED FOR -- a
 * harmonic scale factor is specific to level of theory AND to the
 * electronic-structure code that produced the frequencies it was fit
 * against (same LOT, different program, different factor). This is
 * distinct from `record.software_release` (the "Record software:" line
 * below this block), which is the software attached to the statmech
 * RECORD itself (often the analysis tool, e.g. Arkane) -- the two happen
 * to agree on every row in the archive today, so stacking them
 * undifferentiated would read as one fact where the schema actually
 * carries two. Labelled "Scale factor software" here, opposite "Record
 * software" below, so the two stay visually and textually distinguishable
 * rather than reading as the same fact repeated.
 *
 * The backend keeps `statmech.frequency_scale_factor_value` (the core
 * scalar) and this `frequency_scale_factor` provenance block in sync --
 * "always present when the row has a scale factor" per
 * `ScientificStatmechRecord`'s own docstring -- so this never shows the
 * bare scalar in one place and its LoT/software/kind in another; there is
 * exactly one "Frequency scale factor" row on this card. The `!fsf` branch
 * exists only as a defensive fallback should that invariant ever not hold,
 * not as a second normal path.
 */
function FrequencyScaleFactorDetail({ core, fsf }: {
    core: StatmechRecord["statmech"]
    fsf: StatmechRecord["frequency_scale_factor"]
}) {
    if (!fsf) {
        return (
            <dl className="kv-list">
                <div>
                    <dt>Frequency scale factor</dt>
                    <dd><QuantityValue value={formatQuantity("statmech_frequency_scale_factor", core.frequency_scale_factor_value)} /></dd>
                </div>
            </dl>
        )
    }
    const lot = fsf.level_of_theory ? lotLabel(fsf.level_of_theory) : null
    const fsfSoftwareLabel = fsf.software ? softwareLabel(fsf.software) : null
    return (
        <dl className="kv-list">
            <div>
                <dt>Frequency scale factor</dt>
                <dd>
                    <QuantityValue value={formatQuantity("statmech_frequency_scale_factor", fsf.value)} />{" "}
                    <span className="value-pill">{statusLabel(fsf.scale_kind)}</span>
                </dd>
            </div>
            {lot && <div><dt>Scale factor level of theory</dt><dd>{lot}</dd></div>}
            {fsfSoftwareLabel && <div><dt>Scale factor software</dt><dd>{fsfSoftwareLabel}</dd></div>}
            <div><dt>Frequency scale factor ref</dt><dd><code>{fsf.frequency_scale_factor_ref}</code></dd></div>
        </dl>
    )
}

/**
 * `statmech` carries no conformer column (entry-scoped, never
 * conformer-scoped) -- this is a READ-TIME DERIVATION from the record's own
 * source calculations, cross-referenced against the entry's loaded
 * conformer projections (`domain/statmechConformerDerivation.ts`), and is
 * labelled as derived rather than as a stored fact for exactly that reason.
 *
 * Answers the owner's own confusion directly: "it shows no conformer link -
 * so that means there is no conformer observation or something? kinda
 * confusing where there are source calcs." There IS one, knowable from
 * those very source calculations; it was just never surfaced. Measured
 * live (2026-09-02): all 101 archive statmech records with source
 * calculations resolve to exactly one conformer -- disagreement has never
 * happened here yet, but this still renders that case honestly (naming
 * every conformer involved) rather than picking one, since a silent first-
 * match here would be wrong the moment a real archive disagrees.
 */
function DerivedConformerNote({ record, conformers, sourceCalcsState }: {
    record: StatmechRecord
    conformers: ConformerProjection[]
    sourceCalcsState: EntryListSectionState<StatmechRecord["source_calculations"]>
}) {
    // Nothing to derive from -- already stated plainly by the evidence
    // summary's own "Source calculations: 0" row below; no note needed.
    if (record.evidence_summary.source_calculation_count === 0) return null
    if (sourceCalcsState.status === "idle" || sourceCalcsState.status === "loading") {
        return <p className="section-note" role="status">Deriving the conformer from this record's source calculations…</p>
    }
    if (sourceCalcsState.status === "error") return null

    const sourceCalcs = sourceCalcsState.dataByRef.get(record.statmech.statmech_ref)
    const derived = deriveStatmechConformer(sourceCalcs?.map((calc) => calc.calculation_ref), conformers)

    if (derived.kind === "unresolved") {
        return (
            <p className="section-note">
                Conformer: this record's source calculations do not trace to any conformer observation loaded for
                this entry.
            </p>
        )
    }
    if (derived.kind === "single") {
        return (
            <p className="section-note">
                Conformer (derived from source calculations):{" "}
                <Link to={`/conformer-groups/${derived.conformerGroupRef}`}>{derived.label}</Link>
            </p>
        )
    }
    // Disagreement: named individually, never collapsed to "the first one".
    return (
        <p className="section-note" role="alert">
            Conformer: this record's source calculations span more than one conformer --{" "}
            {derived.conformers.map((entry, index) => (
                <span key={entry.conformerGroupRef}>
                    {index > 0 && ", "}
                    <Link to={`/conformer-groups/${entry.conformerGroupRef}`}>{entry.label}</Link>
                </span>
            ))}
            {" "}-- so no single conformer is shown here.
        </p>
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
    // No record on the entry has this section at all -- `notAvailableText`
    // already names what's missing ("No electronic levels are recorded for
    // any statmech record on this entry."), so a full heading over a
    // dashed empty box repeats that in two places for nothing. Collapses
    // to the one line, unregistered with the ToC (no `SectionHeading`) --
    // there is no destination behind it to jump to.
    if (!available) return <p className="empty-projection">{notAvailableText}</p>
    return (
        <details
            className="ledger-section"
            onToggle={(event) => { if ((event.target as HTMLDetailsElement).open) onOpen() }}
        >
            <summary><SectionHeading id={headingId}>{heading}</SectionHeading></summary>
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
