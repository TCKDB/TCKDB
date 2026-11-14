import { useParams } from "react-router-dom"
import { RecordLink } from "../components/RecordLink"
import { recordWords } from "../domain/recordWords"
import "../conformer-group.css"
import "../methods.css"
import { LevelLabel } from "../components/LevelLabel"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { Stored } from "../components/QuantityValue"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { ledeText } from "../domain/ledeText"
import { scaleKindLabel } from "../domain/frequencyScaleFormat"
import { recordRoute } from "../domain/recordRoute"
import { usageIntro } from "../domain/usageIntro"
import { deposited } from "../domain/quantityFormat"
import { softwareLabel, toolReleaseLabel, words } from "../domain/provenanceFormat"
import { useFrequencyScaleFactor } from "../hooks/useFrequencyScaleFactor"
import type { FrequencyScaleFactorRecord, FrequencyScaleFactorUsage } from "../api/methodsApi"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

const FREQUENCY_SCALE_FACTOR_LEDE = ledeText`A factor that scales computed vibrational frequencies.`

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/** `/methods/frequency-scale-factors/:fsfRef`: one deposited scale factor and the records that use it. */
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
    const shown = deposited(fsf.value)
    const valueText = shown?.value ?? String(fsf.value)
    const kindText = scaleKindLabel(fsf.scale_kind)
    const kind = `${kindText.charAt(0).toUpperCase()}${kindText.slice(1)} scale factor`
    useDocumentTitle("Frequency scale factor", `${kind} ${valueText}`)

    return (
        <section className="conformer-page methods-page">
            <PageShell
                crumbs={[{ label: "Methods", to: "/methods" }, { label: kind }]}
                contents="none"
                identity={(
                    <header className="basin-header">
                        <RecordIdentityHeader
                            kicker="Frequency scale factor"
                            lede={FREQUENCY_SCALE_FACTOR_LEDE}
                            title={<>{kind} <span><Stored full={shown?.full}>{valueText}</Stored></span></>}
                            ownRef={{ label: "Frequency scale factor ref", value: fsf.frequency_scale_factor_ref }}
                            ownFacts={(
                                <>
                                    <div>
                                        <dt>Level of theory</dt>
                                        <dd>
                                            {record.level_of_theory
                                                ? <LevelLabel levelOfTheory={record.level_of_theory} program={softwareLabel(record.software_release)} />
                                                : <span className="record-identity-absent-inline">not tied to a specific level of theory</span>}
                                        </dd>
                                    </div>
                                    <div><dt>Software</dt><dd>{softwareLabel(record.software_release) ?? "not recorded"}</dd></div>
                                    <div><dt>Workflow tool</dt><dd>{toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}</dd></div>
                                </>
                            )}
                        />
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(fsf.created_at)}</dd></div>
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
                intro={usageIntro(total, rows.length, "record", "records")}
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
                            {rows.map((row, index) => (
                                <tr key={row.record_ref}>
                                    <td data-label="Record type">{words(row.record_type)}</td>
                                    <td data-label="Record">
                                        {recordRoute(row.record_type, row.record_ref)
                                            ? <RecordLink kind="statmech" to={recordRoute(row.record_type, row.record_ref) ?? ""} label={recordWords(index + 1)} />
                                            : recordWords(index + 1)}
                                    </td>
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
