import { GuideLink } from "../components/GuideLink"
import { baseLabel } from "../domain/levelLabel"
import { RecordLink } from "../components/RecordLink"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../methods.css"
import { CorrectionSchemeTable } from "../components/CorrectionSchemeTable"
import { LevelLabel } from "../components/LevelLabel"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { energyUnitLabel, ENERGY_DISPLAY_UNITS, type EnergyDisplayUnit } from "../domain/energyUnits"
import { recordRoute } from "../domain/recordRoute"
import { usageIntro } from "../domain/usageIntro"
import { QuantityValue } from "../components/QuantityValue"
import { deposited } from "../domain/quantityFormat"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import { ledeText } from "../domain/ledeText"
import { schemeKindLabel } from "../domain/correctionSchemeFormat"
import { levelOfTheoryPath } from "../domain/methodsLinks"
import { softwareLabel, toolReleaseLabel, words } from "../domain/provenanceFormat"
import { useCorrectionScheme } from "../hooks/useCorrectionScheme"
import type { EnergyCorrectionSchemeRecord, EnergyCorrectionSchemeUsage } from "../api/methodsApi"
import { LiteratureCitation } from "../components/LiteratureCitation"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

const CORRECTION_SCHEME_LEDE = ledeText`Parameters that adjust computed energies, and the records they were applied to.`

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/** `/methods/schemes/:ecsRef`: one deposited correction scheme, with its parameters and where it was applied. */
export default function CorrectionSchemePage() {
    const { ecsRef = "" } = useParams<{ ecsRef: string }>()
    const state = useCorrectionScheme(ecsRef)

    if (state.status === "ready") {
        return <CorrectionSchemeDetail key={state.record.energy_correction_scheme.energy_correction_scheme_ref} record={state.record} />
    }
    return (
        <RecordStatus
            state={state}
            ref={ecsRef}
            kind="energy-correction scheme"
            loadingDetail="Retrieving this scheme's parameters, level of theory, and application list."
        />
    )
}

function CorrectionSchemeDetail({ record }: { record: EnergyCorrectionSchemeRecord }) {
    const scheme = record.energy_correction_scheme
    const usage = record.used_by ?? []
    // Titled from the scheme kind and its software, never the depositor's own name for it.
    const software = softwareLabel(record.software_release)
    const title = software ? `${schemeKindLabel(scheme.scheme_kind)}, ${software}` : schemeKindLabel(scheme.scheme_kind)
    const levelLabel = record.level_of_theory ? baseLabel(record.level_of_theory) : null
    useDocumentTitle("Energy-correction scheme", levelLabel ? `${title} at ${levelLabel}` : title)

    return (
        <section className="conformer-page methods-page">
            <PageShell
                crumbs={[
                    { label: "Methods", to: "/methods" },
                    ...(record.level_of_theory && levelLabel
                        ? [{ label: levelLabel, to: levelOfTheoryPath(record.level_of_theory.level_of_theory_ref ?? "") }]
                        : []),
                    { label: title },
                ]}
                identity={(
                    <header className="basin-header">
                        <RecordIdentityHeader
                            kicker="Energy-correction scheme"
                            lede={CORRECTION_SCHEME_LEDE}
                            title={title}
                            ownRef={{ label: "Scheme ref", value: scheme.energy_correction_scheme_ref }}
                            ownFacts={(
                                <>
                                    <div>
                                        <dt>Level of theory</dt>
                                        <dd>
                                            {record.level_of_theory
                                                ? <LevelLabel levelOfTheory={record.level_of_theory} />
                                                : <span className="record-identity-absent-inline">not tied to a specific level of theory</span>}
                                        </dd>
                                    </div>
                                    <div>
                                        <dt>Frequency level</dt>
                                        <dd>
                                            {record.frequency_level_of_theory
                                                ? <LevelLabel levelOfTheory={record.frequency_level_of_theory} />
                                                : "not recorded"}
                                        </dd>
                                    </div>
                                    {record.software_release?.software_release_ref && (
                                        <div>
                                            <dt>Software release ref</dt>
                                            <dd className="record-identity-fact-copyable">
                                                <code className="data">{record.software_release.software_release_ref}</code>
                                                <CopyButton value={record.software_release.software_release_ref} label="Software release ref" srLabel="value" />
                                            </dd>
                                        </div>
                                    )}
                                    <div><dt>Workflow-tool release</dt><dd>{toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}</dd></div>
                                    <div>
                                        <dt>Literature source</dt>
                                        <dd>{record.literature ? <LiteratureCitation literature={record.literature} /> : "not recorded"}</dd>
                                    </div>
                                </>
                            )}
                        />
                        <p><GuideLink to="correction-scheme" /></p>
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(scheme.created_at)}</dd></div>
                        </dl>
                    </header>
                )}
            >
                <section className="ledger-section" aria-labelledby="ecs-parameters-heading">
                    <SectionHeading id="ecs-parameters-heading">
                        Correction parameters
                    </SectionHeading>
                    {record.available_sections.has_corrections && (record.corrections?.length ?? 0) > 0 ? (
                        <CorrectionSchemeTable corrections={record.corrections ?? []} units={scheme.units} />
                    ) : (
                        <p className="empty-projection">No correction parameters are recorded for this scheme.</p>
                    )}
                </section>

                <UsageSection rows={usage} available={record.available_sections.has_used_by} total={record.evidence_summary.applied_usage_count} />
            </PageShell>
        </section>
    )
}

function UsageSection({ rows, available, total }: { rows: EnergyCorrectionSchemeUsage[]; available: boolean; total: number }) {
    return (
        <section className="ledger-section" aria-labelledby="ecs-usage-heading">
            <SectionHeading
                id="ecs-usage-heading"
                intro={usageIntro(total, rows.length, "record", "records")}
            >
                Applications
            </SectionHeading>
            {available && rows.length > 0 ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Applications of this correction scheme">
                        <thead>
                            <tr>
                                <th scope="col">Record</th>
                                <th scope="col">Role</th>
                                <th scope="col">Applied value</th>
                                <th scope="col">Source calculation</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((row, index) => (
                                <tr key={`${row.record_ref}-${index}`}>
                                    <td data-label="Record"><UsageRecord type={row.record_type} recordRef={row.record_ref} /></td>
                                    <td data-label="Role">{words(row.application_role)}</td>
                                    <td data-label="Applied value" className="num"><QuantityValue value={deposited(row.applied_value, unitText(row.applied_value_unit))} /></td>
                                    <td data-label="Source calculation">
                                        {row.source_calculation_ref
                                            ? <RecordLink kind="calculation" to={`/calculations/${row.source_calculation_ref}`} label="Calculation" />
                                            : "not recorded"}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="empty-projection">No applications of this scheme are recorded.</p>
            )}
        </section>
    )
}

function unitText(unit: string | null | undefined): string | null {
    if (!unit) return null
    return (ENERGY_DISPLAY_UNITS as readonly string[]).includes(unit) ? energyUnitLabel(unit as EnergyDisplayUnit) : unit
}

function UsageRecord({ type, recordRef }: { type: string; recordRef: string }) {
    const to = recordRoute(type, recordRef)
    return to ? <Link to={to} aria-label={`${words(type) ?? "Record"}`}>{words(type) ?? "Record"}</Link> : <span>{words(type) ?? "Record"}</span>
}
