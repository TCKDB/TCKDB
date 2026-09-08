import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import "../methods.css"
import { CorrectionSchemeTable } from "../components/CorrectionSchemeTable"
import { LevelOfTheoryLink } from "../components/LevelOfTheoryLink"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import { words } from "../domain/provenanceFormat"
import { useCorrectionScheme } from "../hooks/useCorrectionScheme"
import type { EnergyCorrectionSchemeRecord, EnergyCorrectionSchemeUsage } from "../api/methodsApi"

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/**
 * `/methods/schemes/:ecsRef` -- the thin standalone scheme page
 * (methods-surface plan §4.3, `plan-methods-surface-v2`, not committed to
 * this repo). This is the link target for the two dead-ref sites §2.4 of
 * that plan found (`CalculationDetailPage.tsx`'s `EnergyCorrectionsSection`,
 * and every LOT page's own "Correction schemes" section), and the only
 * home a scheme with `level_of_theory_id IS NULL` (an `atom_hf`/
 * `atom_thermal`/`soc` scheme, none deposited yet) can ever have -- it is
 * not keyed to a level of theory at all, so it has no LOT-page section to
 * fold into.
 */
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

    return (
        <section className="conformer-page methods-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to="/methods">Methods</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">{scheme.name}</span>
            </nav>

            <PageShell
                identity={(
                    <header className="basin-header">
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Energy-correction scheme · deposited evidence</span>
                            </div>
                            <h1 className="t-display-1 record-identity-title">{scheme.name}</h1>
                            <div className="record-identity-known">
                                <dl className="kv-list record-identity-facts">
                                    <div>
                                        <dt>Scheme ref</dt>
                                        <dd className="record-identity-fact-copyable">
                                            <code className="data">{scheme.energy_correction_scheme_ref}</code>
                                            <CopyButton value={scheme.energy_correction_scheme_ref} label="Scheme ref" srLabel="value" />
                                        </dd>
                                    </div>
                                    <div><dt>Scheme kind</dt><dd>{words(scheme.scheme_kind)}</dd></div>
                                    <div>
                                        <dt>Level of theory</dt>
                                        <dd>
                                            {record.level_of_theory
                                                ? <LevelOfTheoryLink levelOfTheory={record.level_of_theory} />
                                                : <span className="record-identity-absent-inline">not tied to a specific level of theory</span>}
                                        </dd>
                                    </div>
                                    {scheme.note && <div><dt>Note</dt><dd>{scheme.note}</dd></div>}
                                </dl>
                            </div>
                        </div>
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(scheme.created_at)}</dd></div>
                            <div><dt>Applied to</dt><dd>{record.evidence_summary.applied_usage_count} entries</dd></div>
                        </dl>
                    </header>
                )}
            >
                <section className="ledger-section" aria-labelledby="ecs-parameters-heading">
                    <SectionHeading id="ecs-parameters-heading" kicker="Deposited evidence" intro="This scheme's full parameter table, exactly as deposited — never summarised or truncated.">
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
                kicker="Deposited evidence"
                intro={`${total} record${total === 1 ? "" : "s"} carry this scheme's applied value. Up to 50 are listed below.`}
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
                                    <td data-label="Record"><span className="data">{row.record_ref}</span></td>
                                    <td data-label="Role">{words(row.application_role)}</td>
                                    <td data-label="Applied value" className="num">{row.applied_value} {row.applied_value_unit}</td>
                                    <td data-label="Source calculation">
                                        {row.source_calculation_ref
                                            ? <Link className="data" to={`/calculations/${row.source_calculation_ref}`}>{row.source_calculation_ref}</Link>
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
