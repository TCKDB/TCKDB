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
import { schemeKindLabel } from "../domain/correctionSchemeFormat"
import { softwareLabel, toolReleaseLabel, words } from "../domain/provenanceFormat"
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
    // Title from the archive's own controlled vocabulary (`scheme_kind`),
    // never the depositor's free-text `scheme.name` -- owner: "I kinda
    // don't want labels almost in general to never appear on the front
    // end". Measured live: both deposited schemes have `name` exactly
    // equal to `kind` (`atom_energy`/`bac_petersson`), so the old heading
    // was only ever repeating the kind back in the depositor's own
    // spelling -- never adding identity information `kind` alone does not
    // already carry. `schemeKindLabel` is shared with
    // `LevelOfTheoryPage.tsx`'s own per-LOT scheme heading
    // (`domain/correctionSchemeFormat.ts`) so the two pages can never label
    // the same scheme kind two different ways.
    const title = schemeKindLabel(scheme.scheme_kind)

    return (
        <section className="conformer-page methods-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to="/methods">Methods</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">{title}</span>
            </nav>

            <PageShell
                identity={(
                    <header className="basin-header">
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Energy-correction scheme · deposited evidence</span>
                            </div>
                            {/* Still no SOFTWARE in the TITLE here -- #439 (deployed)
                                added `software_id`/`workflow_tool_release_id` to
                                `energy_correction_scheme` and this page now renders
                                both (facts list below), but this standalone page's
                                own `<h1>` keeps titling from `scheme_kind` plus the
                                level of theory it is tied to, unchanged. Software
                                titles the PER-LOT box on `LevelOfTheoryPage.tsx`
                                instead (owner ruling: those boxes are "the software
                                or something", never the depositor's `name`) -- a
                                different surface with a different reason to need it:
                                a LOT page can hold two schemes of different kinds
                                at once, so software disambiguates two anonymous
                                boxes the reader has not yet opened. This page is
                                already keyed to one scheme; adding software to the
                                heading here would repeat a fact the facts list
                                already states, not disambiguate anything. */}
                            <h1 className="t-display-1 record-identity-title">
                                {title}
                                {record.level_of_theory && (
                                    <> <span className="value-pill"><LevelOfTheoryLink levelOfTheory={record.level_of_theory} /></span></>
                                )}
                            </h1>
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
                                    {/* Software and workflow-tool release, added by #439
                                        (deployed). Both are ALWAYS their own row, same
                                        rule as the literature row below: an absence here
                                        is a fact about this record that the reader needs
                                        to see was checked, not a row that silently isn't
                                        there. The two are null for genuinely different
                                        reasons on the live archive today -- software
                                        backfilled cleanly (both schemes: Gaussian);
                                        workflow-tool release stayed null because only 10
                                        of 416 calculations recorded one and no single
                                        release could be derived unambiguously, so the
                                        backfill deliberately left it absent rather than
                                        guess -- but this page states each absence
                                        plainly, without narrating a cause it cannot
                                        verify from the API response alone. Still not a
                                        link, but the reason has changed and the old one
                                        is no longer true: `software_release_ref` used to
                                        be measured empty on every live row, because the
                                        scheme stored a vendor rather than a release row
                                        to point at. Since the read layer joins a real
                                        `software_release` that ref resolves, so linking
                                        it is now possible and is PR 4's decision, not an
                                        impossibility. Until then `softwareLabel` renders
                                        name/version only, exactly like every other
                                        software_release consumer in this app.
                                        `FrequencyScaleFactor` does still have the old
                                        limitation (its release-grain revision is PR 6).
                                        Note both live rows carry no release at all
                                        today, so this renders "not recorded" either
                                        way. */}
                                    <div>
                                        <dt>Software</dt>
                                        <dd>{softwareLabel(record.software_release) ?? "not recorded"}</dd>
                                    </div>
                                    <div>
                                        <dt>Workflow-tool release</dt>
                                        <dd>{toolReleaseLabel(record.workflow_tool_release) ?? "not recorded"}</dd>
                                    </div>
                                    {/* Fetched (`loadCorrectionScheme` always requests
                                        `include=literature`) but never rendered before
                                        this fix -- the archive was hiding a citation it
                                        already had in hand. ALWAYS a row (unlike
                                        `CalculationDetailPage.tsx`'s literature row,
                                        which omits itself entirely when absent): the
                                        live archive holds ZERO literature rows for
                                        either deposited scheme today, so the reader
                                        needs to see that this was checked and found
                                        absent, not wonder whether the row was simply
                                        left out. Absent for a DIFFERENT reason than the
                                        two rows above (nobody has recorded one, not an
                                        archive-side derivation the field couldn't
                                        resolve) -- stated with the same plain "not
                                        recorded" text, never "not applicable" (a claim
                                        about the chemistry this archive cannot make). */}
                                    <div>
                                        <dt>Literature source</dt>
                                        <dd>
                                            {record.literature
                                                ? <>{record.literature.title ?? record.literature.literature_ref}{record.literature.year ? ` (${record.literature.year})` : ""}</>
                                                : "not recorded"}
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
