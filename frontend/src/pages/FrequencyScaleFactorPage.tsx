import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import "../methods.css"
import { LevelOfTheoryLink } from "../components/LevelOfTheoryLink"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import { softwareLabel, toolReleaseLabel, words } from "../domain/provenanceFormat"
import { useFrequencyScaleFactor } from "../hooks/useFrequencyScaleFactor"
import type { FrequencyScaleFactorRecord, FrequencyScaleFactorUsage } from "../api/methodsApi"

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/**
 * `/methods/frequency-scale-factors/:fsfRef` -- the thin standalone FSF
 * page (methods-surface plan §4.3, `plan-methods-surface-v2`, not
 * committed to this repo), the link target for the dead-ref site §2.4
 * found (`EntryStatmechSection.tsx`'s `FrequencyScaleFactorDetail`, which
 * used to print `fsf.frequency_scale_factor_ref` in an unlinked `<code>`)
 * and for every distinct provenance row a LOT page's own "Frequency scale
 * factor" disclosure lists.
 */
export default function FrequencyScaleFactorPage() {
    const { fsfRef = "" } = useParams<{ fsfRef: string }>()
    const state = useFrequencyScaleFactor(fsfRef)

    if (state.status === "ready") {
        return <FrequencyScaleFactorDetail key={state.record.frequency_scale_factor.frequency_scale_factor_ref} record={state.record} />
    }
    return (
        <RecordStatus
            state={state}
            ref={fsfRef}
            kind="frequency scale factor"
            loadingDetail="Retrieving this scale factor's value, level of theory, and usage."
        />
    )
}

function FrequencyScaleFactorDetail({ record }: { record: FrequencyScaleFactorRecord }) {
    const fsf = record.frequency_scale_factor
    const usage = record.used_by ?? []

    return (
        <section className="conformer-page methods-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to="/methods">Methods</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Frequency scale factor {fsf.value}</span>
            </nav>

            <PageShell
                identity={(
                    <header className="basin-header">
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Frequency scale factor · deposited evidence</span>
                            </div>
                            <h1 className="t-display-1 record-identity-title">
                                {fsf.value} <span className="value-pill">{words(fsf.scale_kind)}</span>
                            </h1>
                            <div className="record-identity-known">
                                <dl className="kv-list record-identity-facts">
                                    <div>
                                        <dt>Frequency scale factor ref</dt>
                                        <dd className="record-identity-fact-copyable">
                                            <code className="data">{fsf.frequency_scale_factor_ref}</code>
                                            <CopyButton value={fsf.frequency_scale_factor_ref} label="Frequency scale factor ref" srLabel="value" />
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Level of theory</dt>
                                        <dd>
                                            {record.level_of_theory
                                                ? <LevelOfTheoryLink levelOfTheory={record.level_of_theory} />
                                                : <span className="record-identity-absent-inline">not tied to a specific level of theory</span>}
                                        </dd>
                                    </div>
                                    <div><dt>Software</dt><dd>{softwareLabel(record.software_release) ?? "not recorded"}</dd></div>
                                    <div><dt>Workflow tool</dt><dd>{toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}</dd></div>
                                    {fsf.note && <div><dt>Note</dt><dd>{fsf.note}</dd></div>}
                                </dl>
                            </div>
                        </div>
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(fsf.created_at)}</dd></div>
                            <div><dt>Used by</dt><dd>{record.evidence_summary.statmech_usage_count} statmech records</dd></div>
                        </dl>
                    </header>
                )}
            >
                <UsageSection rows={usage} available={record.available_sections.has_used_by} total={record.evidence_summary.statmech_usage_count} />
            </PageShell>
        </section>
    )
}

function UsageSection({ rows, available, total }: { rows: FrequencyScaleFactorUsage[]; available: boolean; total: number }) {
    return (
        <section className="ledger-section" aria-labelledby="fsf-usage-heading">
            <SectionHeading
                id="fsf-usage-heading"
                kicker="Deposited evidence"
                intro={`${total} statmech record${total === 1 ? "" : "s"} use this scale factor.`}
            >
                Used by
            </SectionHeading>
            {available && rows.length > 0 ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Statmech records using this frequency scale factor">
                        <thead>
                            <tr>
                                <th scope="col">Record type</th>
                                <th scope="col">Record</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={row.record_ref}>
                                    <td data-label="Record type">{words(row.record_type)}</td>
                                    <td data-label="Record"><span className="data">{row.record_ref}</span></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="empty-projection">No usage is recorded for this frequency scale factor.</p>
            )}
        </section>
    )
}
