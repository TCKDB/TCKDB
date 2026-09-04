import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../conformer-group.css"
import "../entry-science.css"
import { lotLabel } from "../api/scientificSchemas"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { ThermoListResponse, ThermoRecord } from "../api/thermoApi"
import { conformerLabel, partitionByConformerLink, thermoConformerGroupRef } from "../domain/conformerEvidence"
import { groupByFingerprint, thermoRecordFingerprint } from "../domain/identicalRecordGroups"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { formatQuantity } from "../domain/quantityFormat"
import { useEntryThermo } from "../hooks/useEntryThermo"
import { useRegisteredSection } from "../hooks/usePageSections"
import { ConformerAttributionGroups } from "./ConformerAttributionGroups"
import { Disclosure } from "./Disclosure"
import { SectionHeading } from "./PageSections"
import { QuantityValue } from "./QuantityValue"
import { RecordStatus } from "./RecordStatus"
import { SectionErrorBoundary } from "./SectionErrorBoundary"
import { SupersessionNotice } from "./SupersessionNotice"
import { ThermoCpChart } from "./ThermoCpChart"

// ---------------------------------------------------------------------------
// The thermo/statmech/transport read surfaces are ENTRY-SCOPED LISTS
// (`GET /species-entries/{id}/thermo` etc.), not `thermo_ref`-addressed
// detail routes — there is no standalone `/thermo/:ref` page in this
// project and this slice does not build one (the API this endpoint serves
// has no matching per-record route to build a page around). This component
// therefore renders every deposited thermo record for the entry, each as
// its own independent card — never flattened into one merged answer, never
// reduced to "the first one" or "the best one". See `api/thermoApi.ts` for
// the measured wire shape and why nothing here is include-gated.
//
// PR #285 gave thermo a REAL per-record conformer link
// (`provenance.conformer_group_ref`), resolved server-side through the
// same primary calculation already used for `primary_calculation`/
// `level_of_theory`. When a conformer is selected, records partition
// three ways via `partitionByConformerLink` (shared with
// `EntryStatmechSection`): traced to the selected conformer, traced to a
// DIFFERENT named conformer, or carrying no conformer link at all
// (population B — a record with no resolvable primary calculation). A
// binary matched/unmatched split previously collapsed the middle case into
// "no link", which is false of a record the wire does attribute elsewhere
// — see the review that caught it. An earlier version of this file also
// tried to INFER the link from calculation-ref intersection before this
// field existed; that guess mislabeled population-B records as
// conformer-linked and was removed outright, not patched.
// ---------------------------------------------------------------------------

const MODEL_KIND_LABELS: Record<string, string> = {
    nasa: "NASA-7",
    nasa9: "NASA-9",
    wilhoit: "Wilhoit",
    points: "Point-based",
    scalar: "Scalar",
}
const modelKindLabel = (kind: string) => MODEL_KIND_LABELS[kind] ?? kind.replaceAll("_", " ")
const statusLabel = (status: string) => status.replaceAll("_", " ")

export function EntryThermoSection({ entryRef, conformer, conformers }: {
    entryRef: string
    conformer?: ConformerProjection | null
    conformers?: ConformerProjection[]
}) {
    const state = useEntryThermo(entryRef)
    if (state.status === "ready") {
        return (
            <SectionErrorBoundary
                fallback={(
                    <section className="ledger-section" aria-labelledby="thermo-heading">
                        <SectionHeading id="thermo-heading">Thermochemistry</SectionHeading>
                        <p className="empty-projection" role="alert">
                            This section could not be displayed. The rest of this entry is unaffected.
                        </p>
                    </section>
                )}
            >
                <ThermoList response={state.record} conformer={conformer} conformers={conformers ?? []} />
            </SectionErrorBoundary>
        )
    }
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="thermochemistry"
            loadingDetail="Retrieving the deposited thermochemistry records for this entry."
        />
    )
}

function reviewSummaryText(summary: ThermoListResponse["review_summary"]) {
    const parts: string[] = []
    if (summary.approved) parts.push(`${summary.approved} approved`)
    if (summary.under_review) parts.push(`${summary.under_review} under review`)
    if (summary.not_reviewed) parts.push(`${summary.not_reviewed} not reviewed`)
    if (summary.deprecated) parts.push(`${summary.deprecated} deprecated`)
    if (summary.rejected) parts.push(`${summary.rejected} rejected`)
    return parts.length > 0 ? parts.join(" · ") : "no records"
}

function thermoRecordFallback(record: ThermoRecord) {
    return (
        <article className="science-record card" role="alert">
            <p className="empty-projection">
                Record <code className="data">{record.thermo_ref}</code> could not be displayed. Other
                records on this page are unaffected.
            </p>
        </article>
    )
}

/**
 * Whether `record` actually carries the model-kind data its own
 * `model_kind` names -- distinct from `model_kind` itself, which is a
 * declared classification that can outlive the data (every one of
 * `NasaBlock`/`Nasa9Block`/`WilhoitBlock`/`PointsBlock` below already
 * renders defensively for exactly this case: a `model_kind` of `"nasa"`
 * with a null `nasa` field). A ToC entry pointing at a record whose model
 * block would just say "No NASA-7 polynomial recorded for this record" is
 * an empty destination -- see `thermoSectionLabels` below, which only
 * registers a record when this is true.
 */
function hasModelKindData(record: ThermoRecord): boolean {
    if (record.model_kind === "nasa") return record.nasa != null
    if (record.model_kind === "nasa9") return (record.nasa9?.length ?? 0) > 0
    if (record.model_kind === "wilhoit") return record.wilhoit != null
    if (record.model_kind === "points") return (record.points?.length ?? 0) > 0
    // "scalar" and any other declared kind: no model block on this page to
    // jump to at all.
    return false
}

/**
 * One ToC label per record that actually has model-kind data, computed
 * once over the FULL, stable-ordered `records` list this entry-scoped
 * list endpoint returned -- not recomputed per render call from whatever
 * order a partitioned (`ConformerAttributionGroups`) or flat
 * (`records.map`) render path happens to visit records in, so the
 * disambiguating index below is deterministic regardless of which path
 * rendered first. A record with no model-kind data maps to `null` (no ToC
 * entry -- "never register a placeholder for an absent model kind").
 *
 * Two records of the SAME model kind get a disambiguating index appended
 * ("NASA-7 thermo record 1" / "NASA-7 thermo record 2") -- a lone record
 * of a kind keeps the plain label ("NASA-7 thermo record"), matching the
 * design brief's own example.
 */
function thermoSectionLabels(records: ThermoRecord[]): Map<string, string> {
    const withData = records.filter(hasModelKindData)
    const countByBase = new Map<string, number>()
    for (const record of withData) {
        const base = `${modelKindLabel(record.model_kind)} thermo record`
        countByBase.set(base, (countByBase.get(base) ?? 0) + 1)
    }
    const seenByBase = new Map<string, number>()
    const labels = new Map<string, string>()
    for (const record of withData) {
        const base = `${modelKindLabel(record.model_kind)} thermo record`
        if ((countByBase.get(base) ?? 0) <= 1) {
            labels.set(record.thermo_ref, base)
            continue
        }
        const index = (seenByBase.get(base) ?? 0) + 1
        seenByBase.set(base, index)
        labels.set(record.thermo_ref, `${base} ${index}`)
    }
    return labels
}

/** Binds `renderThermoRecord` to one entry's worth of ToC labels (see
 *  `thermoSectionLabels`) so every render path below -- the flat list, and
 *  each `ConformerAttributionGroups` bucket -- registers the same record
 *  under the same label, computed once. */
function makeThermoRecordRenderer(sectionLabels: Map<string, string>) {
    return function renderThermoRecord(record: ThermoRecord) {
        return (
            <SectionErrorBoundary key={record.thermo_ref} fallback={thermoRecordFallback(record)}>
                <ThermoRecordCard record={record} sectionLabel={sectionLabels.get(record.thermo_ref) ?? null} />
            </SectionErrorBoundary>
        )
    }
}

/**
 * Renders a WHOLE bucket of records at once -- grouping it, locally, by
 * scientific content (`groupByFingerprint`/`thermoRecordFingerprint`) --
 * rather than mapping one record to one card. Grouping happens per call
 * (per `ConformerAttributionGroups` bucket, and once for the flat
 * no-conformer-selected list) so a record traced to a different conformer
 * is never folded into another bucket's identical-values card just because
 * the two report the same numbers -- see `ConformerAttributionGroups.tsx`'s
 * `renderRecords` docstring. `sectionLabels` is looked up against every
 * member of a LOCAL group, not only its first record, so a ToC entry
 * survives even if the record `thermoSectionLabels` originally chose to
 * label lands in a different position within a differently-grouped bucket.
 */
function makeThermoGroupedRenderer(sectionLabels: Map<string, string>) {
    const renderThermoRecord = makeThermoRecordRenderer(sectionLabels)
    return function renderThermoRecords(records: ThermoRecord[]): ReactNode {
        return groupByFingerprint(records, thermoRecordFingerprint).map((group) => {
            if (group.records.length === 1) return renderThermoRecord(group.records[0])
            const representative = group.records[0]
            const sectionLabel = group.records
                .map((record) => sectionLabels.get(record.thermo_ref))
                .find((label): label is string => label != null) ?? null
            return (
                <SectionErrorBoundary key={representative.thermo_ref} fallback={thermoRecordFallback(representative)}>
                    <IdenticalThermoRecordsCard records={group.records} sectionLabel={sectionLabel} />
                </SectionErrorBoundary>
            )
        })
    }
}

function ThermoList({ response, conformer, conformers }: {
    response: ThermoListResponse
    conformer?: ConformerProjection | null
    conformers: ConformerProjection[]
}) {
    const { records, review_summary: reviewSummary, pagination } = response
    // Computed once, over the full deposited list's GROUP REPRESENTATIVES
    // (see `thermoSectionLabels`'s own docstring for why this must not be
    // recomputed per render path) -- a group of N identical records
    // contributes exactly ONE ToC entry, never N near-duplicate entries
    // ("NASA-7 thermo record 1".."NASA-7 thermo record 7") for content a
    // reader only needs to visit once.
    const representativeRecords = groupByFingerprint(records, thermoRecordFingerprint).map((group) => group.records[0])
    const sectionLabels = thermoSectionLabels(representativeRecords)
    const renderThermoRecords = makeThermoGroupedRenderer(sectionLabels)
    return (
        <section className="ledger-section" aria-labelledby="thermo-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Deposited evidence</p>
                <SectionHeading id="thermo-heading">Thermochemistry</SectionHeading>
                <p>
                    Every thermo record deposited for this entry, each shown independently. Multiple deposits
                    are never merged, averaged, or reduced to one preferred value on this page.
                </p>
            </div>
            <p className="note records-note">
                {pagination.total} record{pagination.total === 1 ? "" : "s"}
                {pagination.total > pagination.returned ? ` (showing ${pagination.returned})` : ""}
                {" · review: "}{reviewSummaryText(reviewSummary)}
            </p>
            {records.length > 0 && (
                // Driven from EVERY deposited record, not the conformer
                // partition below -- comparing conformers against each other
                // is the whole point (see the module docstring on
                // `ThermoCpChart.tsx`), so this never filters to "this
                // conformer's records" the way the list below does.
                // `conformer` is only ever used here to decide which
                // series gets highlighted, never which are shown.
                <ThermoCpChart
                    records={records}
                    conformers={conformers}
                    selectedConformerGroupRef={conformer?.conformer_group.conformer_group_ref ?? null}
                />
            )}
            {records.length === 0 ? (
                <p className="empty-projection">No thermochemistry records are deposited for this entry.</p>
            ) : conformer ? (
                <ConformerAttributionGroups
                    attribution={partitionByConformerLink(
                        records,
                        conformers,
                        conformer.conformer_group.conformer_group_ref,
                        // Thermo's link is genuinely single-valued (one
                        // primary calculation, one basin) -- wrapped in a
                        // one-element array for `partitionByConformerLink`'s
                        // set-membership contract, shared with statmech's
                        // genuinely multi-valued link.
                        (record) => {
                            const ref = thermoConformerGroupRef(record)
                            return ref ? [ref] : []
                        },
                    )}
                    selectedLabel={conformerLabel(conformer)}
                    renderRecords={renderThermoRecords}
                    thisConformerNote="Traced to this conformer's own primary calculation."
                    thisConformerEmptyText="No thermo record traces to this conformer yet."
                    otherConformerNote="Traced to a different conformer than the one selected above."
                    noLinkNote="No resolvable primary calculation to trace a conformer through — shown here at entry level, not flagged as missing anything."
                    noLinkEmptyText="No entry-level thermo record is deposited for this entry."
                />
            ) : (
                renderThermoRecords(records)
            )}
        </section>
    )
}

/**
 * `sectionLabel` is `null` for a record with no actual model-kind data
 * (`hasModelKindData` false -- a `model_kind` that outlives its own data,
 * or a kind this page has no ToC-worthy block for at all) and a string
 * otherwise (see `thermoSectionLabels`). `useRegisteredSection` is called
 * UNCONDITIONALLY regardless -- passing `id: null` through it is the
 * sanctioned no-op (see that hook's own docstring) that keeps this a
 * valid, always-in-the-same-order hook call rather than a conditional one.
 */
function ThermoRecordCard({ record, sectionLabel }: { record: ThermoRecord; sectionLabel: string | null }) {
    useRegisteredSection(sectionLabel ? `thermo-heading-${record.thermo_ref}` : null, sectionLabel ?? "")
    return (
        <article className="science-record card" aria-labelledby={`thermo-heading-${record.thermo_ref}`}>
            <div className="science-record-heading">
                <h3 id={`thermo-heading-${record.thermo_ref}`}>{modelKindLabel(record.model_kind)} thermo record</h3>
                <span className="value-pill--muted">{statusLabel(record.review.status)}</span>
                <code className="data">{record.thermo_ref}</code>
            </div>
            <ThermoRecordBody record={record} />
        </article>
    )
}

/**
 * Everything under a thermo card's own heading -- shared by the normal
 * one-record `ThermoRecordCard` above and `IdenticalThermoRecordsCard`
 * below (which shows this body ONCE, from a representative record, for a
 * whole group of scientifically-identical deposits).
 *
 * "Model kind" is never its own row here: the card's `<h3>` already reads
 * "{kind} thermo record" (`ThermoRecordCard` above, or
 * `IdenticalThermoRecordsCard`'s own heading) -- a `<dd>` repeating the
 * exact word the heading just used is not a second fact.
 *
 * `idSuffix` distinguishes the ids this body mints from the ids the SAME
 * record mints again inside a group's "Show all" disclosure
 * (`IdenticalThermoRecordsCard` renders every member, including the
 * representative, a second time via the plain `ThermoRecordCard` path) --
 * without it, the group's own `coverage-<ref>`/`nasa7-<ref>`/
 * `completeness-<ref>` ids collide with that same representative's ids
 * inside the disclosure, breaking `aria-labelledby` and the ToC anchor
 * (both resolve to whichever DOM node happens to match first). Empty for
 * every normal, non-grouped card.
 *
 * `showProvenance` is `false` only for the group's own shared body
 * (`IdenticalThermoRecordsCard`) -- provenance is the one thing this body
 * is NOT safe to show once for a whole group: the representative's
 * calculation/statmech refs are its own, not the group's (see
 * `IdenticalThermoGroupRefs`, which lists every member's provenance
 * per-ref instead). Every other caller shows it, unchanged.
 */
function ThermoRecordBody({ record, idSuffix = "", showProvenance = true }: {
    record: ThermoRecord
    idSuffix?: string
    showProvenance?: boolean
}) {
    return (
        <>
            {record.supersession && <SupersessionNotice supersession={record.supersession} />}

            <dl className="kv-list">
                <div><dt>Scientific origin</dt><dd>{record.scientific_origin}</dd></div>
                <div><dt>H298</dt><dd><QuantityValue value={formatQuantity("thermo_h298_kj_mol", record.h298_kj_mol)} /></dd></div>
                <div>
                    <dt>H298 uncertainty</dt>
                    <dd><QuantityValue value={formatQuantity("thermo_h298_uncertainty_kj_mol", record.h298_uncertainty_kj_mol)} /></dd>
                </div>
                <div><dt>S298</dt><dd><QuantityValue value={formatQuantity("thermo_s298_j_mol_k", record.s298_j_mol_k)} /></dd></div>
                <div>
                    <dt>S298 uncertainty</dt>
                    <dd><QuantityValue value={formatQuantity("thermo_s298_uncertainty_j_mol_k", record.s298_uncertainty_j_mol_k)} /></dd>
                </div>
            </dl>

            <TemperatureCoverageBlock coverage={record.temperature_coverage ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />
            <ModelBlock record={record} idSuffix={idSuffix} />
            <EvidenceCompletenessBlock completeness={record.evidence_completeness ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />
            {showProvenance && <ProvenanceBlock provenance={record.provenance ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />}
            <GroupAdditivityBlock groupAdditivity={record.group_additivity ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />
        </>
    )
}

/**
 * Renders exactly the ONE model-kind block this record's own `model_kind`
 * names -- never all four. A `nasa` (computed) record used to also print
 * an empty "NASA-9 polynomial" box, an empty "Wilhoit form" box, and an
 * empty "Evaluated points" box beneath its real NASA-7 table -- three
 * dashed "not recorded" boxes for model shapes the record was never going
 * to have, on every single card. `model_kind` values this page has no
 * dedicated block for (e.g. `"scalar"`) render nothing here, matching
 * `hasModelKindData`'s own "no ToC-worthy destination" verdict above --
 * still defensive against a declared kind with no matching data (a `nasa`
 * record whose `nasa` field is null still renders `NasaBlock`'s own "No
 * NASA-7 polynomial recorded" line, never silently nothing).
 */
function ModelBlock({ record, idSuffix = "" }: { record: ThermoRecord; idSuffix?: string }) {
    switch (record.model_kind) {
        case "nasa": return <NasaBlock nasa={record.nasa ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />
        case "nasa9": return <Nasa9Block nasa9={record.nasa9 ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />
        case "wilhoit": return <WilhoitBlock wilhoit={record.wilhoit ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />
        case "points": return <PointsBlock points={record.points ?? null} thermoRef={record.thermo_ref} idSuffix={idSuffix} />
        default: return null
    }
}

// Suffix applied to every id the group card's OWN shared body mints, so
// they never collide with the same representative record's ids when it is
// rendered a second time, unmodified, inside "Show all" below (see
// `ThermoRecordBody`'s own docstring).
const GROUP_ID_SUFFIX = "-group"

/**
 * One card for N deposited thermo records that report IDENTICAL scientific
 * content (`domain/identicalRecordGroups.ts`'s `thermoRecordFingerprint`)
 * -- the finding measured live: seven ethene thermo records, all H298 =
 * 62.84 kJ/mol, S298 = 218.80 J/mol·K, identical NASA-7 coefficients,
 * rendered as seven full cards. The shared scientific content renders ONCE
 * -- from the group's first record; every member reports the identical
 * body by construction of the fingerprint -- via the same `ThermoRecordBody`
 * a single-record card uses, so nothing about the science itself is a
 * special, second rendering path. "Show all" mounts every member's own
 * full, unmodified card on demand; nothing here replaces a record with a
 * summary, it only collapses a default DISPLAY that would otherwise repeat
 * the same numbers N times.
 *
 * Provenance is NOT part of that shared display: the live case this fix
 * was written against is seven ethene records citing SEVEN DIFFERENT
 * primary/single-point calculations and seven different statmech refs --
 * `showProvenance={false}` below drops the representative's own
 * `ProvenanceBlock` from the shared body (rendering only one record's
 * calculation/statmech refs there would attribute them to all seven, which
 * is false), and `IdenticalThermoGroupRefs` prints every member's own
 * provenance, per ref, directly on the card -- never behind "Show all".
 */
function IdenticalThermoRecordsCard({ records, sectionLabel }: { records: ThermoRecord[]; sectionLabel: string | null }) {
    const representative = records[0]
    const anchorId = `thermo-heading-${representative.thermo_ref}${GROUP_ID_SUFFIX}`
    useRegisteredSection(sectionLabel ? anchorId : null, sectionLabel ?? "")
    return (
        <article className="science-record identical-record-group card" aria-labelledby={anchorId}>
            <div className="science-record-heading">
                <h3 id={anchorId}>{modelKindLabel(representative.model_kind)} thermo record</h3>
                <span className="value-pill--muted">{records.length} records with identical values</span>
            </div>
            <p className="note">
                {records.length} deposited records report identical H298, S298 and model-form values — shown
                once below. Each record's own ref and provenance — including which calculation and statmech
                record it cites — is listed per ref in the table below, never collapsed into one; every
                record stays individually reachable, and none was merged, averaged, or dropped in favor of
                another.
            </p>
            <ThermoRecordBody record={representative} idSuffix={GROUP_ID_SUFFIX} showProvenance={false} />
            {/* Level of theory is part of the identity fingerprint, so every
                record in this group shares it -- showing it once here is
                safe, and a chemist reading H298 needs the LoT beside it. The
                rest of provenance differs per record and lives in the table
                below; never lift it up here. */}
            <dl className="kv-list" aria-label="Shared level of theory">
                <div>
                    <dt>Level of theory</dt>
                    <dd>{representative.provenance?.level_of_theory ? lotLabel(representative.provenance.level_of_theory) : "not recorded"}</dd>
                </div>
            </dl>
            <IdenticalThermoGroupRefs records={records} />
            <Disclosure className="identical-record-group-detail" summary={`Show all ${records.length} records individually`}>
                {records.map((record) => (
                    <SectionErrorBoundary key={record.thermo_ref} fallback={thermoRecordFallback(record)}>
                        <ThermoRecordCard record={record} sectionLabel={null} />
                    </SectionErrorBoundary>
                ))}
            </Disclosure>
        </article>
    )
}

/**
 * Every ref in an identical-values group, with its OWN provenance --
 * "Provenance that differs across identical-value records must still be
 * visible … list it per ref inside the group, never collapse provenance"
 * is the finding's own requirement. The live ethene case this was written
 * against: seven records citing seven different primary/single-point
 * calculations and seven different statmech refs, alongside six saying
 * "Record software: not recorded" and one saying "Arkane" -- every one of
 * those differences gets its own column, its own row, per ref; nothing
 * here is summarized from "the group" or from any one representative.
 */
function IdenticalThermoGroupRefs({ records }: { records: ThermoRecord[] }) {
    const headingId = `identical-refs-${records[0].thermo_ref}`
    return (
        <section aria-labelledby={headingId}>
            <h4 className="model-block-heading" id={headingId}>Records in this group</h4>
            <div className="table-scroll table-scroll--compact">
                <table className="data-table" aria-label="Records sharing these identical values">
                    <thead>
                        <tr>
                            <th scope="col">Ref</th>
                            <th scope="col">Review</th>
                            <th scope="col">Primary calculation</th>
                            <th scope="col">Freq calculation</th>
                            <th scope="col">SP calculation</th>
                            <th scope="col">Statmech ref</th>
                            <th scope="col">Software</th>
                            <th scope="col">Workflow tool</th>
                        </tr>
                    </thead>
                    <tbody>
                        {records.map((record) => {
                            const provenance = record.provenance ?? null
                            const primaryRef = provenance?.primary_calculation?.calculation_ref ?? null
                            return (
                                <tr key={record.thermo_ref}>
                                    <td data-label="Ref"><code className="data">{record.thermo_ref}</code></td>
                                    <td data-label="Review">{statusLabel(record.review.status)}</td>
                                    <td data-label="Primary calculation">
                                        <CalculationRefCell calculationRef={primaryRef} />
                                    </td>
                                    <td data-label="Freq calculation">
                                        <CalculationRefCell calculationRef={provenance?.freq_calculation_ref ?? null} primaryRef={primaryRef} />
                                    </td>
                                    <td data-label="SP calculation">
                                        <CalculationRefCell calculationRef={provenance?.sp_calculation_ref ?? null} primaryRef={primaryRef} />
                                    </td>
                                    <td data-label="Statmech ref">{provenance?.statmech_ref ?? "not recorded"}</td>
                                    <td data-label="Software">{softwareLabel(provenance?.software_release) ?? "not recorded"}</td>
                                    <td data-label="Workflow tool">{toolReleaseLabel(provenance?.workflow_tool_release) ?? "not recorded"}</td>
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
 * One calculation-ref cell in `IdenticalThermoGroupRefs`' table: a link
 * when the ref is present, plain "not recorded" text otherwise.
 *
 * `primaryRef`, when passed, is the SAME dedup this table's Primary
 * column always shows in full -- if this cell's own ref equals it, the
 * cell says "same as primary" instead of repeating the identical ref a
 * second time. Measured against the live archive (every deposited thermo
 * record across every species entry, 65 records / 8 multi-record groups,
 * curled 2026-09-03): `sp_calculation_ref` equals `primary_calculation
 * .calculation_ref` on EVERY record (an SP-from-optimization record
 * citing its own opt/sp job for both roles, same finding as
 * `provenanceCalculationRows` below already applies to the single-record
 * provenance block) -- `freq_calculation_ref` never does (a frequency job
 * is structurally a separate calculation run, distinct from the
 * electronic-energy job either role above cites). `primaryRef` is still
 * threaded through the Freq column too, rather than hard-coding "the SP
 * column is the one that collapses": a future record whose freq calc
 * genuinely reuses its primary job must collapse the same way, and a
 * differing case -- true for every sampled Freq cell today -- must never
 * be hidden, only the identical one.
 */
function CalculationRefCell({ calculationRef, primaryRef = null }: { calculationRef: string | null; primaryRef?: string | null }) {
    if (!calculationRef) return <>not recorded</>
    // Plain text, not a link -- like the "not recorded" branch above, this
    // cell has no calculation of its own to point at; the ref is already
    // linked from the Primary column in the same row.
    if (primaryRef && calculationRef === primaryRef) return <>same as primary</>
    return <Link className="data" to={`/calculations/${calculationRef}`}>{calculationRef}</Link>
}

/**
 * `record_min_k`/`record_max_k` are a fact of the RECORD -- always shown.
 * `requested_min_k`/`requested_max_k`/`covers_requested_range`/
 * `extrapolation_distance_k` describe the REQUEST that produced this
 * response -- a temperature filter nobody applied on this page (this slice
 * never sends one) is not a fact about the record, and printing
 * "Requested range (K): No temperature filter applied" / "Covers requested
 * range: Yes" / "Extrapolation distance (K): 0" on every card presents
 * query metadata as though it were something the record itself claims.
 * These three rows render only when a range was genuinely requested
 * (either bound non-null); when neither bound is set, they are omitted
 * entirely rather than printed with a "not applicable" stand-in for a
 * question this page never asked.
 */
function TemperatureCoverageBlock({ coverage, thermoRef, idSuffix = "" }: {
    coverage: ThermoRecord["temperature_coverage"] | null
    thermoRef: string
    idSuffix?: string
}) {
    const wasRequested = coverage != null && (coverage.requested_min_k != null || coverage.requested_max_k != null)
    return (
        <section aria-labelledby={`coverage-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`coverage-${thermoRef}${idSuffix}`}>Temperature coverage</h4>
            {coverage ? (
                <dl className="kv-list">
                    <div>
                        <dt>Record range (K)</dt>
                        <dd>{coverage.record_min_k ?? "not recorded"}–{coverage.record_max_k ?? "not recorded"}</dd>
                    </div>
                    {wasRequested && (
                        <>
                            <div>
                                <dt>Requested range (K)</dt>
                                <dd>{coverage.requested_min_k ?? "?"}–{coverage.requested_max_k ?? "?"}</dd>
                            </div>
                            <div><dt>Covers requested range</dt><dd>{coverage.covers_requested_range ? "Yes" : "No"}</dd></div>
                            <div><dt>Extrapolation distance (K)</dt><dd>{coverage.extrapolation_distance_k}</dd></div>
                        </>
                    )}
                </dl>
            ) : <p className="empty-projection">No temperature coverage computed for this record.</p>}
        </section>
    )
}

function NasaBlock({ nasa, thermoRef, idSuffix = "" }: { nasa: ThermoRecord["nasa"] | null; thermoRef: string; idSuffix?: string }) {
    return (
        <section aria-labelledby={`nasa7-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`nasa7-${thermoRef}${idSuffix}`}>NASA-7 polynomial</h4>
            {nasa ? (
                <>
                    <dl className="kv-list">
                        <div><dt>T low (K)</dt><dd>{nasa.t_low ?? "not recorded"}</dd></div>
                        <div><dt>T mid (K)</dt><dd>{nasa.t_mid ?? "not recorded"}</dd></div>
                        <div><dt>T high (K)</dt><dd>{nasa.t_high ?? "not recorded"}</dd></div>
                    </dl>
                    <div className="table-scroll">
                        <table className="data-table" aria-label={`NASA-7 coefficients for ${thermoRef}`}>
                            <thead>
                                <tr>
                                    <th scope="col">Range</th>
                                    {Array.from({ length: 7 }, (_, index) => <th scope="col" key={`a${index + 1}`}>a{index + 1}</th>)}
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td data-label="Range">Low</td>
                                    {(nasa.low_temperature_coefficients ?? []).map((coefficient, index) => (
                                        <td className="num" data-label={`a${index + 1}`} key={`low-${index}`}>{coefficient ?? "not recorded"}</td>
                                    ))}
                                </tr>
                                <tr>
                                    <td data-label="Range">High</td>
                                    {(nasa.high_temperature_coefficients ?? []).map((coefficient, index) => (
                                        <td className="num" data-label={`a${index + 1}`} key={`high-${index}`}>{coefficient ?? "not recorded"}</td>
                                    ))}
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </>
            ) : <p className="empty-projection">No NASA-7 polynomial recorded for this record.</p>}
        </section>
    )
}

function Nasa9Block({ nasa9, thermoRef, idSuffix = "" }: { nasa9: ThermoRecord["nasa9"] | null; thermoRef: string; idSuffix?: string }) {
    return (
        <section aria-labelledby={`nasa9-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`nasa9-${thermoRef}${idSuffix}`}>NASA-9 polynomial</h4>
            {nasa9 && nasa9.length > 0 ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label={`NASA-9 intervals for ${thermoRef}`}>
                        <thead>
                            <tr>
                                <th scope="col">Interval</th>
                                <th scope="col">T min (K)</th>
                                <th scope="col">T max (K)</th>
                                {Array.from({ length: 9 }, (_, index) => <th scope="col" key={`a${index + 1}`}>a{index + 1}</th>)}
                            </tr>
                        </thead>
                        <tbody>
                            {nasa9.map((interval) => (
                                <tr key={`nasa9-${thermoRef}-${interval.interval_index}`}>
                                    <td className="num" data-label="Interval">{interval.interval_index}</td>
                                    <td className="num" data-label="T min (K)">{interval.t_min_k}</td>
                                    <td className="num" data-label="T max (K)">{interval.t_max_k}</td>
                                    <td className="num" data-label="a1">{interval.a1}</td>
                                    <td className="num" data-label="a2">{interval.a2}</td>
                                    <td className="num" data-label="a3">{interval.a3}</td>
                                    <td className="num" data-label="a4">{interval.a4}</td>
                                    <td className="num" data-label="a5">{interval.a5}</td>
                                    <td className="num" data-label="a6">{interval.a6}</td>
                                    <td className="num" data-label="a7">{interval.a7}</td>
                                    <td className="num" data-label="a8">{interval.a8}</td>
                                    <td className="num" data-label="a9">{interval.a9}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : <p className="empty-projection">No NASA-9 polynomial recorded for this record.</p>}
        </section>
    )
}

function WilhoitBlock({ wilhoit, thermoRef, idSuffix = "" }: { wilhoit: ThermoRecord["wilhoit"] | null; thermoRef: string; idSuffix?: string }) {
    return (
        <section aria-labelledby={`wilhoit-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`wilhoit-${thermoRef}${idSuffix}`}>Wilhoit form</h4>
            {wilhoit ? (
                <dl className="kv-list">
                    <div><dt>Cp0 (J/mol·K)</dt><dd>{wilhoit.cp0_j_mol_k}</dd></div>
                    <div><dt>Cp∞ (J/mol·K)</dt><dd>{wilhoit.cp_inf_j_mol_k}</dd></div>
                    <div><dt>B (K)</dt><dd>{wilhoit.b_k}</dd></div>
                    <div><dt>a0 / a1 / a2 / a3</dt><dd>{wilhoit.a0}, {wilhoit.a1}, {wilhoit.a2}, {wilhoit.a3}</dd></div>
                    <div><dt>H0 (kJ/mol)</dt><dd>{wilhoit.h0_kj_mol ?? "not recorded"}</dd></div>
                    <div><dt>S0 (J/mol·K)</dt><dd>{wilhoit.s0_j_mol_k ?? "not recorded"}</dd></div>
                </dl>
            ) : <p className="empty-projection">No Wilhoit fit recorded for this record.</p>}
        </section>
    )
}

function PointsBlock({ points, thermoRef, idSuffix = "" }: { points: ThermoRecord["points"] | null; thermoRef: string; idSuffix?: string }) {
    return (
        <section aria-labelledby={`points-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`points-${thermoRef}${idSuffix}`}>Evaluated points</h4>
            {points && points.length > 0 ? (
                <Disclosure summary={`${points.length} temperature point${points.length === 1 ? "" : "s"}`}>
                    <div className="table-scroll table-scroll--compact">
                        <table className="data-table" aria-label={`Evaluated thermo points for ${thermoRef}`}>
                            <thead>
                                <tr>
                                    <th scope="col">T (K)</th>
                                    <th scope="col">Cp (J/mol·K)</th>
                                    <th scope="col">H (kJ/mol)</th>
                                    <th scope="col">S (J/mol·K)</th>
                                    <th scope="col">G (kJ/mol)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {points.map((point, index) => (
                                    <tr key={`${thermoRef}-point-${index}`}>
                                        <td className="num" data-label="T (K)">{point.temperature_k}</td>
                                        <td className="num" data-label="Cp (J/mol·K)">{point.cp_j_mol_k ?? "not recorded"}</td>
                                        <td className="num" data-label="H (kJ/mol)">{point.h_kj_mol ?? "not recorded"}</td>
                                        <td className="num" data-label="S (J/mol·K)">{point.s_j_mol_k ?? "not recorded"}</td>
                                        <td className="num" data-label="G (kJ/mol)">{point.g_kj_mol ?? "not recorded"}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Disclosure>
            ) : <p className="empty-projection">No evaluated points recorded for this record.</p>}
        </section>
    )
}

// Real labels, not field names with underscores swapped for spaces --
// "SCF stability check" reads; "scf stability" does not. Any checklist key
// this map hasn't seen yet still renders (a stripped/spaced fallback,
// `evidenceCheckLabel` below) rather than being silently dropped.
const EVIDENCE_CHECK_LABELS: Record<string, string> = {
    has_source_calculations: "source calculations",
    has_statmech_source: "statmech source",
    has_frequency_evidence: "frequency evidence",
    has_sp_or_energy_evidence: "SP or energy evidence",
    has_temperature_dependent_model: "temperature-dependent model",
    has_uncertainty: "uncertainty",
    has_geometry_validation: "geometry validation",
    has_scf_stability: "SCF stability check",
}

function evidenceCheckLabel(key: string): string {
    return EVIDENCE_CHECK_LABELS[key] ?? key.replace(/^has_/, "").replaceAll("_", " ")
}

function EvidenceCompletenessBlock({ completeness, thermoRef, idSuffix = "" }: {
    completeness: ThermoRecord["evidence_completeness"] | null
    thermoRef: string
    idSuffix?: string
}) {
    // Consistent with `NasaBlock`/`Nasa9Block`/`WilhoitBlock`/`PointsBlock`:
    // an absent block renders its own heading and an explicit "not
    // recorded" line, never a silently missing section. In practice this
    // field is always present on the wire (`evidence_completeness:
    // EvidenceCompletenessBreakdown`, not `| None`, per
    // `scientific_thermo.py`) — this branch is defensive, not a real
    // absence this client expects to see.
    if (!completeness) {
        return (
            <section aria-labelledby={`completeness-${thermoRef}${idSuffix}`}>
                <h4 className="model-block-heading" id={`completeness-${thermoRef}${idSuffix}`}>Evidence completeness</h4>
                <p className="empty-projection">No evidence-completeness breakdown recorded for this record.</p>
            </section>
        )
    }
    // The score already conveys how many of eight booleans are satisfied;
    // enumerating every "Present" row alongside it repeats that number one
    // check at a time and buries the two checks that matter. Name only
    // what's FALSE by default -- the full checklist stays reachable behind
    // a disclosure, never enumerated by default.
    const missing = Object.entries(completeness.checklist).filter(([, value]) => !value).map(([key]) => key)
    return (
        <section aria-labelledby={`completeness-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`completeness-${thermoRef}${idSuffix}`}>
                Evidence completeness ({completeness.score} / {completeness.max})
            </h4>
            {missing.length === 0 ? (
                <p className="note">Every evidence-completeness check is satisfied.</p>
            ) : (
                <p className="evidence-missing">
                    Missing:{" "}
                    {missing.map((key) => <span key={key} className="value-pill--muted">{evidenceCheckLabel(key)}</span>)}
                </p>
            )}
            <Disclosure className="evidence-full-checklist" summary={`Full checklist (${Object.keys(completeness.checklist).length})`}>
                <ul className="checklist">
                    {Object.entries(completeness.checklist).map(([key, value]) => (
                        <li key={key}>{value ? "Present" : "Absent"} — {evidenceCheckLabel(key)}</li>
                    ))}
                </ul>
            </Disclosure>
        </section>
    )
}

type CalcRefRow = { labels: string[]; ref: string }

/**
 * Primary/frequency/single-point calculation refs, merged into one row per
 * DISTINCT calculation. An SP-from-optimization record routinely cites the
 * SAME calculation as both its `primary_calculation` and its
 * `sp_calculation_ref` (see `feedback_sp_vs_opt_energy`: "use opt energy as
 * SP when LOTs match") -- printing that one calculation ref under two
 * separate headings reads as though the record cited two different
 * calculations that simply happen to match, not one calculation serving
 * two roles. Each DISTINCT ref gets exactly one row here, labelled with
 * every role it fills, joined "/"; a role whose own ref is `null` keeps
 * its own separate row, stating "not recorded" plainly rather than being
 * folded into a group it was never actually part of.
 */
function provenanceCalculationRows(provenance: NonNullable<ThermoRecord["provenance"]>): { rows: CalcRefRow[]; missing: string[] } {
    const fields: Array<{ label: string; ref: string | null }> = [
        { label: "Primary calculation", ref: provenance.primary_calculation?.calculation_ref ?? null },
        { label: "Frequency calculation", ref: provenance.freq_calculation_ref ?? null },
        { label: "Single-point calculation", ref: provenance.sp_calculation_ref ?? null },
    ]
    const rows: CalcRefRow[] = []
    const missing: string[] = []
    for (const field of fields) {
        if (!field.ref) { missing.push(field.label); continue }
        const existing = rows.find((row) => row.ref === field.ref)
        if (existing) existing.labels.push(field.label)
        else rows.push({ labels: [field.label], ref: field.ref })
    }
    return { rows, missing }
}

function CalculationProvenanceRows({ provenance }: { provenance: NonNullable<ThermoRecord["provenance"]> }) {
    const { rows, missing } = provenanceCalculationRows(provenance)
    return (
        <>
            {rows.map((row) => (
                <div key={row.ref}>
                    <dt>{row.labels.join(" / ")}</dt>
                    <dd><Link className="data" to={`/calculations/${row.ref}`}>{row.ref}</Link></dd>
                </div>
            ))}
            {missing.map((label) => (
                <div key={label}><dt>{label}</dt><dd>not recorded</dd></div>
            ))}
        </>
    )
}

function ProvenanceBlock({ provenance, thermoRef, idSuffix = "" }: {
    provenance: ThermoRecord["provenance"] | null
    thermoRef: string
    idSuffix?: string
}) {
    // Same consistency rule as `EvidenceCompletenessBlock` above — always
    // present on the wire per `ThermoProvenance` (not `| None`), so this
    // branch is defensive.
    if (!provenance) {
        return (
            <section aria-labelledby={`provenance-${thermoRef}${idSuffix}`}>
                <h4 className="model-block-heading" id={`provenance-${thermoRef}${idSuffix}`}>Provenance</h4>
                <p className="empty-projection">No provenance block recorded for this record.</p>
            </section>
        )
    }
    return (
        <section aria-labelledby={`provenance-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`provenance-${thermoRef}${idSuffix}`}>Provenance</h4>
            <dl className="kv-list">
                <div>
                    <dt>Level of theory</dt>
                    <dd>{provenance.level_of_theory ? lotLabel(provenance.level_of_theory) : "not recorded"}</dd>
                </div>
                <div><dt>Level of theory ref</dt><dd>{provenance.level_of_theory?.level_of_theory_ref ?? "not recorded"}</dd></div>
                <div>
                    <dt>Software</dt>
                    <dd>{softwareLabel(provenance.software_release) ?? "not recorded"}</dd>
                </div>
                <div>
                    <dt>Workflow tool</dt>
                    <dd>{toolReleaseLabel(provenance.workflow_tool_release) ?? "not recorded"}</dd>
                </div>
                <CalculationProvenanceRows provenance={provenance} />
                {/* No dedicated statmech detail page exists in this project (see the
                    module docstring), so this stays plain text rather than a dead link. */}
                <div><dt>Statmech ref</dt><dd>{provenance.statmech_ref ?? "not recorded"}</dd></div>
                <div>
                    <dt>Conformer</dt>
                    <dd>
                        {provenance.conformer_group_ref
                            ? <Link className="data" to={`/conformer-groups/${provenance.conformer_group_ref}`}>{provenance.conformer_group_ref}</Link>
                            : "not recorded"}
                    </dd>
                </div>
            </dl>
        </section>
    )
}

function GroupAdditivityBlock({ groupAdditivity, thermoRef, idSuffix = "" }: {
    groupAdditivity: ThermoRecord["group_additivity"] | null
    thermoRef: string
    idSuffix?: string
}) {
    // `group_additivity` genuinely is `null` on the wire for any record
    // that isn't an estimated thermo with an attached GA breakdown. Unlike
    // nasa/nasa9/wilhoit/points -- which are the four possible SHAPES of
    // the one model every thermo record declares via its own `model_kind`,
    // so an empty box for the other three is at least pointing at a real
    // classification the record chose not to use -- group-additivity is a
    // scheme only an estimated record ever has at all. An empty
    // "Group-additivity estimation" box on a `nasa`/computed record (the
    // common case) answers a question that record was never going to
    // answer; this section renders nothing rather than that box.
    if (!groupAdditivity) return null
    return (
        <section aria-labelledby={`ga-${thermoRef}${idSuffix}`}>
            <h4 className="model-block-heading" id={`ga-${thermoRef}${idSuffix}`}>Group-additivity estimation</h4>
            <dl className="kv-list">
                <div>
                    <dt>Scheme</dt>
                    <dd>{groupAdditivity.scheme_name}{groupAdditivity.scheme_version ? ` (${groupAdditivity.scheme_version})` : ""}</dd>
                </div>
                <div><dt>Scheme ref</dt><dd>{groupAdditivity.scheme_ref}</dd></div>
                <div><dt>Code commit</dt><dd>{groupAdditivity.code_commit ?? "not recorded"}</dd></div>
            </dl>
            {groupAdditivity.components && groupAdditivity.components.length > 0 && (
                <div className="table-scroll table-scroll--compact">
                    <table className="data-table" aria-label="Group-additivity components">
                        <thead>
                            <tr>
                                <th scope="col">Group</th>
                                <th scope="col">Kind</th>
                                <th scope="col">Count</th>
                                <th scope="col">H298 contribution (kJ/mol)</th>
                                <th scope="col">S298 contribution (J/mol·K)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groupAdditivity.components.map((component, index) => (
                                <tr key={`${thermoRef}-ga-${index}`}>
                                    <td data-label="Group">{component.group_label}</td>
                                    <td data-label="Kind">{component.component_kind}</td>
                                    <td className="num" data-label="Count">{component.count}</td>
                                    <td className="num" data-label="H298 contribution (kJ/mol)">{component.h298_contribution_kj_mol ?? "not recorded"}</td>
                                    <td className="num" data-label="S298 contribution (J/mol·K)">{component.s298_contribution_j_mol_k ?? "not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    )
}
