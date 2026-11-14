import { useId, useState } from "react"
import { calculationWords } from "../domain/recordWords"
import { RecordLink } from "../components/RecordLink"
import { GuideLink } from "../components/GuideLink"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../methods.css"
import { CompositeSchemeLink } from "../components/CompositeSchemeLink"
import { CorrectionSchemeTable } from "../components/CorrectionSchemeTable"
import { Disclosure } from "../components/Disclosure"
import { LevelLabel } from "../components/LevelLabel"
import { scaleKindLabel } from "../domain/frequencyScaleFormat"
import { recordRoute } from "../domain/recordRoute"
import { usageIntro } from "../domain/usageIntro"
import { ValueLink } from "../components/ValueLink"
import { deposited } from "../domain/quantityFormat"
import { PageShell } from "../components/PageShell"
import { RecordMap } from "../components/RelationshipMap"
import { levelOfTheoryNodes } from "../domain/recordMapNodes"
import { SectionHeading } from "../components/PageSections"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import { SCHEME_KIND_LABELS, schemeKindLabel, schemeParameterRollup } from "../domain/correctionSchemeFormat"
import { correctionSchemePath, frequencyScaleFactorPath } from "../domain/methodsLinks"
import { softwareLabel, toolReleaseLabel, words } from "../domain/provenanceFormat"
import { ledeText } from "../domain/ledeText"
import { useLevelOfTheory } from "../hooks/useLevelOfTheory"
import type {
    EnergyCorrectionSchemeRecord,
    LevelOfTheoryFrequencyScaleFactorGroup,
    LevelOfTheoryRecord,
    LevelOfTheorySoftwareUsage,
    LevelOfTheoryUsage,
    LevelOfTheoryWorkflowToolUsage,
} from "../api/methodsApi"
import { publicCitationText } from "../domain/literatureCitation"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

const LEVEL_OF_THEORY_LEDE = ledeText`How calculations were run: the method, the basis set and the other choices below.`

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/** `/methods/:lotRef`: one level of theory, with the software seen at it, its correction schemes, frequency scale factors and calculations. */
export default function LevelOfTheoryPage() {
    const { lotRef = "" } = useParams<{ lotRef: string }>()
    const state = useLevelOfTheory(lotRef)

    if (state.status === "ready") {
        return <LevelOfTheoryDetail key={state.record.level_of_theory.level_of_theory_ref} record={state.record} />
    }
    return (
        <RecordStatus
            state={state}
            ref={lotRef}
            kind="level of theory"
            loadingDetail="Retrieving this level of theory's identity, observed software, correction parameters, and usage."
        />
    )
}

function LevelOfTheoryDetail({ record }: { record: LevelOfTheoryRecord }) {
    const lot = record.level_of_theory
    // The full label tells all-electron and frozen-core levels of one method and basis apart.
    const title = lot.label ?? (lot.basis ? `${lot.method}/${lot.basis}` : lot.method)
    useDocumentTitle("Level of theory", title)

    const mapNodes = levelOfTheoryNodes({
        calculationCount: record.evidence_summary.calculation_usage_count,
        compositeScheme: lot.composite_scheme
            ? { ref: lot.composite_scheme.composite_scheme_ref, label: words(lot.composite_scheme.kind) ?? "recipe" }
            : null,
        schemeCount: record.available_sections.has_correction_schemes && record.correction_schemes ? record.correction_schemes.length : null,
        scaleFactorCount: record.available_sections.has_frequency_scale_factors && record.frequency_scale_factors
            ? record.frequency_scale_factors.reduce((sum, group) => sum + group.frequency_scale_factor_count, 0)
            : null,
    })

    return (
        <section className="conformer-page methods-page">
            <PageShell
                side={<RecordMap selfKind="level-of-theory" selfLabel="This level of theory" nodes={mapNodes} />}
                crumbs={[{ label: "Methods", to: "/methods" }, { label: title }]}
                identity={(
                    <header className="basin-header">
                        <RecordIdentityHeader
                            kicker="Level of theory"
                            glyph="level-of-theory"
                            lede={LEVEL_OF_THEORY_LEDE}
                            title={title}
                            ownRef={{ label: "Level of theory ref", value: lot.level_of_theory_ref }}
                            ownFacts={(
                                <>
                                    <div><dt>Method</dt><dd>{lot.method}</dd></div>
                                    <div><dt>Basis</dt><dd>{lot.basis ?? "not recorded"}</dd></div>
                                    <div><dt>Dispersion</dt><dd>{lot.dispersion ?? "none"}</dd></div>
                                    <div><dt>Solvent</dt><dd>{lot.solvent ?? "gas phase"}{lot.solvent_model ? ` (${lot.solvent_model})` : ""}</dd></div>
                                    <div><dt>Spin treatment</dt><dd>{lot.spin_treatment ? words(lot.spin_treatment) : "not recorded"}</dd></div>
                                    <div><dt>Core treatment</dt><dd>{lot.core_treatment ? words(lot.core_treatment) : "not recorded"}</dd></div>
                                    {lot.aux_basis && <div><dt>Auxiliary basis</dt><dd>{lot.aux_basis}</dd></div>}
                                    {lot.cabs_basis && <div><dt>CABS basis</dt><dd>{lot.cabs_basis}</dd></div>}
                                    {lot.relativistic_hamiltonian && <div><dt>Relativistic Hamiltonian</dt><dd>{lot.relativistic_hamiltonian}</dd></div>}
                                    {lot.pno_truncation && <div><dt>PNO truncation</dt><dd>{lot.pno_truncation}</dd></div>}
                                    {lot.ri_approximation && <div><dt>RI approximation</dt><dd>{lot.ri_approximation}</dd></div>}
                                    {lot.composite_scheme && (
                                        <div>
                                            <dt>Composite recipe</dt>
                                            <dd>
                                                <CompositeSchemeLink scheme={lot.composite_scheme} />{" "}
                                                <span className="value-pill value-pill--muted">{words(lot.composite_scheme.kind)}</span>
                                            </dd>
                                        </div>
                                    )}
                                </>
                            )}
                        />
                        <p><GuideLink to="level-of-theory" /></p>
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(lot.created_at)}</dd></div>
                        </dl>
                        <Disclosure summary="Level-of-theory hash" defaultOpen={false} className="lot-hash-disclosure">
                            <p className="note">A hash of the fields above. Two levels that read alike but differ in hash differ in a field not shown.</p>
                            <div className="ref-item">
                                <span className="ref-item-label">lot_hash</span>
                                <code className="data">{lot.lot_hash}</code>
                                <CopyButton value={lot.lot_hash} label="lot_hash" />
                            </div>
                        </Disclosure>
                    </header>
                )}
            >
                <SoftwareSection breakdown={record.software ?? null} available={record.available_sections.has_software} />
                <CorrectionSchemesSection schemes={record.correction_schemes ?? null} available={record.available_sections.has_correction_schemes} />
                <FrequencyScaleFactorSection groups={record.frequency_scale_factors ?? null} available={record.available_sections.has_frequency_scale_factors} />
                <UsageSection rows={record.used_by ?? null} available={record.available_sections.has_used_by} total={record.evidence_summary.calculation_usage_count} />
            </PageShell>
        </section>
    )
}

function SoftwareSection({ breakdown, available }: {
    breakdown: { software: LevelOfTheorySoftwareUsage[]; workflow_tools: LevelOfTheoryWorkflowToolUsage[] } | null
    available: boolean
}) {
    const software = breakdown?.software ?? []
    const workflowTools = breakdown?.workflow_tools ?? []
    return (
        <section className="ledger-section" aria-labelledby="lot-software-heading">
            <SectionHeading
                id="lot-software-heading"
                intro="Software seen at this level, with counts."
            >
                Observed software
            </SectionHeading>
            {available && (software.length > 0 || workflowTools.length > 0) ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Software and workflow tools observed at this level of theory">
                        <thead>
                            <tr>
                                <th scope="col">Kind</th>
                                <th scope="col">Name</th>
                                <th scope="col">Version</th>
                                <th scope="col">Calculations</th>
                            </tr>
                        </thead>
                        <tbody>
                            {software.map((row) => (
                                <tr key={`software-${row.software}-${row.version ?? ""}`}>
                                    <td data-label="Kind">Software</td>
                                    <td data-label="Name">{row.software}</td>
                                    <td data-label="Version">{row.version ?? "not recorded"}</td>
                                    <td data-label="Calculations" className="num">{row.calculation_count}</td>
                                </tr>
                            ))}
                            {workflowTools.map((row) => (
                                <tr key={`workflow-tool-${row.workflow_tool}-${row.version ?? ""}`}>
                                    <td data-label="Kind">Workflow tool</td>
                                    <td data-label="Name">{row.workflow_tool}</td>
                                    <td data-label="Version">{row.version ?? "not recorded"}</td>
                                    <td data-label="Calculations" className="num">{row.calculation_count}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="empty-projection">No software usage is recorded for this level of theory.</p>
            )}
        </section>
    )
}

// Kinds that can be tied to a level of theory; element-only kinds have their own scheme page.
const LOT_SCOPED_SCHEME_KINDS = ["atom_energy", "bac_petersson", "bac_melius"] as const

// Kinds keep the order in which they first appear.
function groupSchemesByKind(rows: EnergyCorrectionSchemeRecord[]): [string, EnergyCorrectionSchemeRecord[]][] {
    const groups = new Map<string, EnergyCorrectionSchemeRecord[]>()
    for (const row of rows) {
        const kind = row.energy_correction_scheme.scheme_kind
        const group = groups.get(kind)
        if (group) group.push(row)
        else groups.set(kind, [row])
    }
    return [...groups.entries()]
}

function CorrectionSchemesSection({ schemes, available }: { schemes: EnergyCorrectionSchemeRecord[] | null; available: boolean }) {
    const rows = schemes ?? []
    const depositedKinds = new Set(rows.map((row) => row.energy_correction_scheme.scheme_kind))
    const hasSchemes = available && rows.length > 0
    return (
        <section className="ledger-section" aria-labelledby="lot-schemes-heading">
            <SectionHeading
                id="lot-schemes-heading"
            >
                Correction schemes
            </SectionHeading>
            {hasSchemes && (
                <p className="note">A scheme's software computed its parameters; it is not the software that ran these calculations.</p>
            )}
            {hasSchemes ? (
                groupSchemesByKind(rows).map(([kind, group]) => (
                    <CorrectionSchemeKindGroup key={kind} schemes={group} />
                ))
            ) : (
                <p className="empty-projection">
                    Not deposited: {LOT_SCOPED_SCHEME_KINDS.filter((kind) => !depositedKinds.has(kind)).map((kind) => SCHEME_KIND_LABELS[kind]).join(", ")}.
                </p>
            )}
        </section>
    )
}

/** One scheme box per kind; a kind with several schemes gets a select above it, never two boxes. */
function CorrectionSchemeKindGroup({ schemes }: { schemes: EnergyCorrectionSchemeRecord[] }) {
    // Until the reader picks one, the first listed scheme is shown.
    const [chosenRef, setChosenRef] = useState<string | null>(null)
    const selected = schemes.find(
        (scheme) => scheme.energy_correction_scheme.energy_correction_scheme_ref === chosenRef,
    ) ?? schemes[0]
    return (
        <div className="correction-scheme-kind-group">
            {schemes.length > 1 && (
                <CorrectionSchemeSelect schemes={schemes} selectedRef={selected.energy_correction_scheme.energy_correction_scheme_ref} onSelect={setChosenRef} />
            )}
            {schemes.length > 1 && chosenRef === null && (
                <p className="note correction-scheme-default-note">
                    {schemes.length} schemes of this kind are deposited. The first listed is shown until you choose one; it is not preferred over the others.
                </p>
            )}
            <CorrectionSchemeBox key={selected.energy_correction_scheme.energy_correction_scheme_ref} scheme={selected} />
        </div>
    )
}

/** Software release, then citation, then ref: the first that tells two schemes of one kind apart. */
function correctionSchemeSelectorLabel(scheme: EnergyCorrectionSchemeRecord, siblings: EnergyCorrectionSchemeRecord[]): string {
    const releaseLabel = softwareLabel(scheme.software_release) ?? "software not recorded"
    const sharesReleaseLabel = siblings.filter(
        (sibling) => (softwareLabel(sibling.software_release) ?? "software not recorded") === releaseLabel,
    ).length > 1
    if (!sharesReleaseLabel) return releaseLabel
    const citation = scheme.literature ? publicCitationText(scheme.literature) : null
    return citation
        ? `${releaseLabel} · ${citation}`
        : `${releaseLabel} · ${scheme.energy_correction_scheme.energy_correction_scheme_ref}`
}

function CorrectionSchemeSelect({ schemes, selectedRef, onSelect }: {
    schemes: EnergyCorrectionSchemeRecord[]
    selectedRef: string
    onSelect: (ref: string) => void
}) {
    const selectId = useId()
    return (
        <label className="correction-scheme-select" htmlFor={selectId}>
            <span className="correction-scheme-select-label">Software</span>
            <select
                id={selectId}
                className="correction-scheme-select-input"
                value={selectedRef}
                onChange={(event) => onSelect(event.target.value)}
            >
                {schemes.map((scheme) => {
                    const ref = scheme.energy_correction_scheme.energy_correction_scheme_ref
                    return <option key={ref} value={ref}>{correctionSchemeSelectorLabel(scheme, schemes)}</option>
                })}
            </select>
        </label>
    )
}

function CorrectionSchemeBox({ scheme }: { scheme: EnergyCorrectionSchemeRecord }) {
    const ref = scheme.energy_correction_scheme.energy_correction_scheme_ref
    const software = softwareLabel(scheme.software_release)
    const releaseRef = scheme.software_release?.software_release_ref
    return (
        <Disclosure
            id={`scheme-${ref}`}
            className="correction-scheme-block"
            defaultOpen={false}
            summary={(
                <span className="correction-scheme-summary">
                    <span className="t-heading-2">{schemeKindLabel(scheme.energy_correction_scheme.scheme_kind)}</span>
                    {software
                        ? <span className="value-pill">{software}</span>
                        : <span className="value-pill value-pill--muted">software not recorded</span>}
                    <span className="correction-scheme-summary-rollup">{schemeParameterRollup(scheme.evidence_summary)}</span>
                </span>
            )}
        >
            <dl className="kv-list">
                <div>
                    <dt>Scheme ref</dt>
                    <dd><RecordLink kind="scheme" to={correctionSchemePath(ref)} label="Energy-correction scheme" /></dd>
                </div>
                {releaseRef && (
                    <div>
                        <dt>Software release ref</dt>
                        <dd className="record-identity-fact-copyable">
                            <code className="data">{releaseRef}</code>
                            <CopyButton value={releaseRef} label="Software release ref" srLabel="value" />
                        </dd>
                    </div>
                )}
                <div>
                    <dt>Frequency level</dt>
                    <dd>
                        {scheme.frequency_level_of_theory
                            ? <LevelLabel levelOfTheory={scheme.frequency_level_of_theory} />
                            : "not recorded"}
                    </dd>
                </div>
                <div>
                    <dt>Applied to</dt>
                    <dd>
                        <Link to={`${correctionSchemePath(ref)}#ecs-usage-heading`}>
                            {scheme.evidence_summary.applied_usage_count} {scheme.evidence_summary.applied_usage_count === 1 ? "entry" : "entries"}
                        </Link>
                    </dd>
                </div>
            </dl>
            <CorrectionSchemeTable
                corrections={scheme.corrections ?? []}
                units={scheme.energy_correction_scheme.units}
            />
            <p className="note"><Link to={correctionSchemePath(ref)}>Open this scheme</Link> for its full parameters and where it was applied.</p>
        </Disclosure>
    )
}

/** One value per distinct kind and value; the refs that produced it are listed under Provenance. */
function FrequencyScaleFactorSection({ groups, available }: { groups: LevelOfTheoryFrequencyScaleFactorGroup[] | null; available: boolean }) {
    const rows = groups ?? []
    return (
        <section className="ledger-section" aria-labelledby="lot-fsf-heading">
            <SectionHeading
                id="lot-fsf-heading"
            >
                Frequency scale factor
            </SectionHeading>
            {available && rows.length > 0 ? (
                rows.map((group, index) => (
                    <div key={`${group.scale_kind}-${group.value}`} className="fsf-group">
                        <p>
                            <FactorValue value={group.value} to={group.frequency_scale_factors.length === 1 ? frequencyScaleFactorPath(group.frequency_scale_factors[0].frequency_scale_factor_ref) : `#fsf-provenance-${index}`} /> <span className="value-pill">{scaleKindLabel(group.scale_kind)}</span>
                        </p>
                        <Disclosure
                            id={`fsf-provenance-${index}`}
                            summary="Provenance"
                            count={group.frequency_scale_factor_count}
                            defaultOpen={false}
                        >
                            {group.frequency_scale_factor_count > 1 && (
                                <p className="note">{group.frequency_scale_factor_count} deposits gave this value. Each keeps its own ref below.</p>
                            )}
                            <div className="table-scroll">
                                <table className="data-table" aria-label={`Frequency scale factor provenance for ${deposited(group.value)?.value ?? group.value}`}>
                                    <thead>
                                        <tr>
                                            <th scope="col">Ref</th>
                                            <th scope="col">Software</th>
                                            <th scope="col">Workflow tool</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {group.frequency_scale_factors.map((item, position) => (
                                            <tr key={item.frequency_scale_factor_ref}>
                                                <td data-label="Scale factor">
                                                    <RecordLink kind="scheme" to={frequencyScaleFactorPath(item.frequency_scale_factor_ref)} label={`Frequency scale factor ${position + 1}`} />
                                                </td>
                                                <td data-label="Software">{softwareLabel(item.software_release) ?? "not recorded"}</td>
                                                <td data-label="Workflow tool">{toolReleaseLabel(item.workflow_tool_release) ?? "not recorded"}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </Disclosure>
                    </div>
                ))
            ) : (
                <p className="empty-projection">No frequency scale factor is deposited for this level of theory.</p>
            )}
        </section>
    )
}

function FactorValue({ value, to }: { value: number; to: string | null }) {
    const shown = deposited(value)
    return <ValueLink value={shown?.value ?? String(value)} full={shown?.full} to={to} />
}

function UsageSection({ rows, available, total }: { rows: LevelOfTheoryUsage[] | null; available: boolean; total: number }) {
    const list = rows ?? []
    return (
        <section className="ledger-section" aria-labelledby="lot-usage-heading">
            <SectionHeading
                id="lot-usage-heading"
                intro={usageIntro(total, list.length, "calculation", "calculations")}
            >
                Calculations at this level of theory
            </SectionHeading>
            {available && list.length > 0 ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Calculations at this level of theory">
                        <thead>
                            <tr>
                                <th scope="col">Calculation</th>
                                <th scope="col">Type</th>
                                <th scope="col">Owner</th>
                            </tr>
                        </thead>
                        <tbody>
                            {list.map((row, position) => (
                                <tr key={row.calculation_ref}>
                                    <td data-label="Calculation"><RecordLink kind="calculation" to={`/calculations/${row.calculation_ref}`} label={calculationWords(row.type)} context={`number ${position + 1}`} /></td>
                                    <td data-label="Type">{words(row.type)}</td>
                                    <td data-label="Owner">
                                        {row.record_ref && row.record_type
                                            ? <OwnerRecord type={row.record_type} recordRef={row.record_ref} />
                                            : "not recorded"}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : (
                <p className="empty-projection">No calculations are recorded at this level of theory.</p>
            )}
        </section>
    )
}

function OwnerRecord({ type, recordRef }: { type: string; recordRef: string }) {
    const to = recordRoute(type, recordRef)
    return to ? <Link className="data" to={to}>{recordRef}</Link> : <span className="data">{recordRef}</span>
}
