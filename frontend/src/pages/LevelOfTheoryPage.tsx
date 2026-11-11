import { GuideLink } from "../components/GuideLink"
import { useId, useState } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../record-identity-header.css"
import "../methods.css"
import { CompositeSchemeLink } from "../components/CompositeSchemeLink"
import { CorrectionSchemeTable } from "../components/CorrectionSchemeTable"
import { Disclosure } from "../components/Disclosure"
import { LevelOfTheoryLink } from "../components/LevelOfTheoryLink"
import { PageShell } from "../components/PageShell"
import { RecordMap } from "../components/RelationshipMap"
import { levelOfTheoryNodes } from "../domain/recordMapNodes"
import { SectionHeading } from "../components/PageSections"
import { RecordStatus } from "../components/RecordStatus"
import { CopyButton } from "../components/RefsDisclosure"
import { SCHEME_KIND_LABELS, schemeKindLabel, schemeParameterRollup } from "../domain/correctionSchemeFormat"
import { correctionSchemePath, frequencyScaleFactorPath } from "../domain/methodsLinks"
import { softwareLabel, toolReleaseLabel, words } from "../domain/provenanceFormat"
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

const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/**
 * The level-of-theory record page This is the
 * record for a method and basis with its software and version, specific
 * frequencies for those params, and corrections
 * of AEC and BAC -- not a bare identity page: identity, observed
 * software, correction-scheme parameter tables, frequency scale
 * factor(s), and usage, in that order.
 *
 * No review pill -- a level of theory carries no per-row review state
 * (non-reviewable provenance vocabulary, same as energy-correction
 * schemes and frequency scale factors), so this page never synthesises
 * one.
 */
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
    // The server's full label names the level the way its identity reads, so the
    // all-electron and frozen-core pages of one method and basis are told apart in the heading and
    // the breadcrumb. Older payloads have none: they keep `method/basis`.
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
                        <div className="record-identity-header">
                            <div className="record-identity-kicker-row">
                                <span className="t-kicker record-identity-kicker">Level of theory · provenance vocabulary</span>
                            </div>
                            <h1 className="t-display-2 record-identity-title">{title}</h1>
                            <p><GuideLink to="level-of-theory" /></p>
                            <div className="record-identity-known">
                                <dl className="kv-list record-identity-facts">
                                    <div>
                                        <dt>Level of theory ref</dt>
                                        <dd className="record-identity-fact-copyable">
                                            <code className="data">{lot.level_of_theory_ref}</code>
                                            <CopyButton value={lot.level_of_theory_ref} label="Level of theory ref" srLabel="value" />
                                        </dd>
                                    </div>
                                    <div><dt>Method</dt><dd>{lot.method}</dd></div>
                                    <div><dt>Basis</dt><dd>{lot.basis ?? "not recorded"}</dd></div>
                                    {/* "none"/"gas phase", not "not recorded" -- see
                                        `MethodsIndexPage.tsx`'s own doc comment above its
                                        `NO_DISPERSION_TEXT`/`NO_SOLVENT_TEXT` constants for the
                                        full measurement this wording is based on: dispersion
                                        correction and an implicit solvent model are optional
                                        method choices a calculation can genuinely run without,
                                        unlike `basis` (kept as "not recorded" here, unchanged --
                                        a real gap for methods that do take one) or
                                        `spin_treatment` below (which has an actual
                                        `unknown` state because it is NOT this kind of
                                        optional-and-absent field). */}
                                    <div><dt>Dispersion</dt><dd>{lot.dispersion ?? "none"}</dd></div>
                                    <div><dt>Solvent</dt><dd>{lot.solvent ?? "gas phase"}{lot.solvent_model ? ` (${lot.solvent_model})` : ""}</dd></div>
                                    <div><dt>Spin treatment</dt><dd>{lot.spin_treatment ? words(lot.spin_treatment) : "not recorded"}</dd></div>
                                    {/* Core treatment is stated-or-absent: always
                                        a row, like spin treatment, and "not recorded" rather than
                                        "all electron" for a level that does not say. */}
                                    <div><dt>Core treatment</dt><dd>{lot.core_treatment ? words(lot.core_treatment) : "not recorded"}</dd></div>
                                    {lot.aux_basis && <div><dt>Auxiliary basis</dt><dd>{lot.aux_basis}</dd></div>}
                                    {lot.cabs_basis && <div><dt>CABS basis</dt><dd>{lot.cabs_basis}</dd></div>}
                                    {/* Method qualifiers are shown only when the deposit states them: there is
                                        no "not recorded" row for each, so a plain level stays short. They
                                        are the program's own tokens (dkh2, rijcosx), so they print as stored. */}
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
                                </dl>
                            </div>
                        </div>
                        <dl className="kv-list basin-context">
                            <div><dt>Deposited</dt><dd>{isoDate(lot.created_at)}</dd></div>
                            <div><dt>Calculations at this level</dt><dd>{record.evidence_summary.calculation_usage_count}</dd></div>
                        </dl>
                        <Disclosure summary="Level-of-theory hash" defaultOpen={false} className="lot-hash-disclosure">
                            <p className="note">
                                A content hash over method, basis, auxiliary/CABS basis, dispersion, solvent,
                                solvent model, spin treatment, and core treatment, relativistic Hamiltonian, PNO truncation and RI approximation (when stated) — two deposits that read alike above
                                but have a different hash differ in a field not shown
                                inline (most often spin treatment). Not an identifier a reader looks up by; shown
                                for exact-identity comparison only.
                            </p>
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

/**
 * Zero, one, or two parameter tables (§4.2 item 3). Per the task brief's
 * central rule: "no scheme deposited" (a fact about the archive) must
 * read differently from "no correction is needed" (a claim about
 * chemistry the archive cannot make) -- so an absent scheme is stated
 * plainly, once per possible scheme kind this archive's own vocabulary
 * recognises, never as "not applicable" and never as a blank row.
 *
 * `SCHEME_KIND_LABELS`/`schemeKindLabel` live in
 * `domain/correctionSchemeFormat.ts`, shared with
 * `CorrectionSchemePage.tsx`'s own heading; there is no `scheme.name`
 * fallback.
 *
 * Schemes are grouped by `scheme_kind`. A kind with exactly one deposited
 * scheme renders one collapsible box, titled `{scheme_kind label}
 * {software}`, via `CorrectionSchemeBox` below. A kind with MORE than one
 * scheme (a software release or a citation distinguishes two schemes of the
 * same kind at this level of theory) renders that same single box plus one
 * small control above it (`CorrectionSchemeSelect`) that swaps which
 * scheme's box is shown -- never two boxes of the same kind side by side.
 *
 * Titling never reads `energy_correction_scheme.name` (a `name` fallback
 * would repeat the kind back in the depositor's own spelling -- see
 * `correctionSchemeFormat.ts` for why this app never titles a public page
 * from `name` at all). Software comes from `scheme.software_release` via
 * the shared `softwareLabel` formatter, which already prints the
 * version the moment the release carries one -- no separate "gains a
 * version" formatting code needed here. `software_release_ref` is a real
 * release ref, rendered as copyable text,
 * never a link: `GET /api/v1/software-releases/{id}` sits behind
 * `require_auth_for_legacy_reads` (`router.py`), so a link here would 401
 * for exactly the anonymous reader this page serves.
 *
 * **A scheme with no recorded software** falls back to the text "software
 * not recorded", styled through `.value-pill--muted` rather than the
 * plain `.value-pill` a real name gets -- the same present/absent
 * pill-tone split `EvidenceChecklist.tsx`'s own `RowValue` already uses.
 * Chosen over falling back to `name` (ruled off public pages entirely)
 * and over falling back to silence (an unlabelled box reads as a bug, not
 * an absence) -- the muted pill reads as "checked, not found" rather than
 * as a second real identity sitting beside the real one. A release
 * recorded WITHOUT a version renders the program name alone -- no
 * separate "version not recorded" text; inventing one would make an
 * honest partial deposit look deficient.
 *
 * **Collapsed by default**, like `EvidenceChecklist.tsx`. A closed box that
 * says nothing but its title answers nothing, so the collapsed summary also
 * carries a true roll-up, `schemeParameterRollup(evidence_summary)`
 * ("8 parameters" / "45 parameters" on the live archive), computed from
 * the SAME counts the open parameter table itself renders from.
 */

// Every scheme kind this archive's vocabulary recognises (`EnergyCorrectionSchemeKind`,
// `backend/app/db/models/common.py`) that CAN be tied to a level of theory --
// `atom_hf`/`atom_thermal`/`soc` are element-only, never LOT-scoped (§2.3/§2.7
// of the plan), so they are deliberately excluded from this per-LOT absence
// list; a scheme of one of those kinds belongs on its own standalone page
// (`/methods/schemes/:ecsRef`), reached only via a real `energy_correction_
// scheme_ref` this archive actually recorded, never guessed at from here.
const LOT_SCOPED_SCHEME_KINDS = ["atom_energy", "bac_petersson", "bac_melius"] as const

// Groups preserve first-occurrence order of each kind -- the same order
// as the input rows -- rather than sorting kinds
// alphabetically or by count, so an archive that always deposits AEC
// before BAC keeps reading that way.
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
                // The legibility fix correction-scheme-provenance v2 §8 asks
                // for: "Observed software" above and a correction scheme's
                // own software answer two different questions -- which
                // program ran this level of theory's calculations, versus
                // which program computed one scheme's own parameter values
                // -- and nothing on the page said so before this sentence.
                // True regardless of whether a given scheme's release is
                // recorded, and regardless of whether it matches the
                // observed software above or not; it states the
                // distinction, not an absence.
                <p className="note">A scheme's software computed its parameters; it is not the software that ran these calculations.</p>
            )}
            {hasSchemes ? (
                groupSchemesByKind(rows).map(([kind, group]) => (
                    <CorrectionSchemeKindGroup key={kind} schemes={group} />
                ))
            ) : (
                <p className="empty-projection">
                    Not deposited: {LOT_SCOPED_SCHEME_KINDS.filter((kind) => !depositedKinds.has(kind)).map((kind) => SCHEME_KIND_LABELS[kind].toLowerCase()).join(", ")}.
                </p>
            )}
        </section>
    )
}

/**
 * One kind's worth of deposited schemes. Exactly one scheme: renders
 * `CorrectionSchemeBox` alone, no control -- must degrade to the
 * pre-selector appearance byte-for-byte. More than one: renders the same
 * single box for whichever scheme is currently selected, plus
 * `CorrectionSchemeSelect` above it. The select is a SIBLING of the
 * `Disclosure`, never nested inside its `<summary>` -- a `<select>`
 * inside a `<details><summary>` would toggle the box open/closed on the
 * same click that opens the dropdown, before an option can even be
 * chosen.
 */
function CorrectionSchemeKindGroup({ schemes }: { schemes: EnergyCorrectionSchemeRecord[] }) {
    // `null` until the reader picks one: until then the first listed scheme is shown, and the page says that is all it is.
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

/**
 * What actually distinguishes two schemes of the same kind at this level
 * of theory, post `units`/`version` removal (§3/§8 of the plan): a
 * software release, or -- where two schemes share the same release label
 * (including both being "software not recorded") -- the citation. Falls
 * back to the scheme's own ref only when neither release nor citation
 * separates them, so the option list is never two identical strings.
 */
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
                <>
                    <span className="t-heading-2">{schemeKindLabel(scheme.energy_correction_scheme.scheme_kind)}</span>
                    {software
                        ? <span className="value-pill">{software}</span>
                        : <span className="value-pill value-pill--muted">software not recorded</span>}
                    <span className="correction-scheme-summary-rollup">{schemeParameterRollup(scheme.evidence_summary)}</span>
                </>
            )}
        >
            <dl className="kv-list">
                <div>
                    <dt>Scheme ref</dt>
                    <dd><Link to={correctionSchemePath(ref)}><code className="data">{ref}</code></Link></dd>
                </div>
                {/* Real once a release resolves (correction-scheme-provenance
                    v2 §4) -- but never a link, see this section's own
                    doc comment above. Copyable text only, same as
                    `CalculationDetailPage.tsx`'s own `RefsDisclosure`
                    refs -- visually distinct from the `Link` above it. */}
                {releaseRef && (
                    <div>
                        <dt>Software release ref</dt>
                        <dd className="record-identity-fact-copyable">
                            <code className="data">{releaseRef}</code>
                            <CopyButton value={releaseRef} label="Software release ref" srLabel="value" />
                        </dd>
                    </div>
                )}
                {/* No `energy_correction_scheme.note`: depositor free text (house rule). */}
                {/* The level the scheme's own frequencies were
                    computed at is part of its identity. Always a row: an
                    unstated one reads "not recorded", never the scheme's
                    energy level standing in for it. */}
                <div>
                    <dt>Frequency level</dt>
                    <dd>
                        {scheme.frequency_level_of_theory
                            ? <LevelOfTheoryLink levelOfTheory={scheme.frequency_level_of_theory} />
                            : "not recorded"}
                    </dd>
                </div>
                <div><dt>Applied to</dt><dd>{scheme.evidence_summary.applied_usage_count} entries</dd></div>
            </dl>
            <CorrectionSchemeTable
                corrections={scheme.corrections ?? []}
                units={scheme.energy_correction_scheme.units}
            />
            <p className="note">
                The full recipe and its application list live on this scheme's own page —{" "}
                <Link to={correctionSchemePath(ref)}>
                    open {ref}
                </Link>.
            </p>
        </Disclosure>
    )
}

/**
 * §4.2 item 4: b3lyp/def2tzvp genuinely has 10 distinct FSF rows that all
 * display `0.999`/`fundamental` -- differing only in which ARC
 * workflow-tool-release row produced them. Rendered as ONE value with a
 * provenance disclosure listing the distinct refs, never as 10
 * identical-looking rows (the mutation this section's own test guards).
 */
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
                rows.map((group) => (
                    <div key={`${group.scale_kind}-${group.value}`} className="fsf-group">
                        <p>
                            <QuantityFact value={group.value} /> <span className="value-pill">{words(group.scale_kind)}</span>
                        </p>
                        <Disclosure
                            summary="Provenance"
                            count={group.frequency_scale_factor_count}
                            defaultOpen={false}
                        >
                            {group.frequency_scale_factor_count > 1 && (
                                <p className="note">
                                    {group.frequency_scale_factor_count} separate depositor actions produced this
                                    same value — each keeps its own ref and provenance below, not collapsed into
                                    one.
                                </p>
                            )}
                            <div className="table-scroll">
                                <table className="data-table" aria-label={`Frequency scale factor provenance for ${group.value}`}>
                                    <thead>
                                        <tr>
                                            <th scope="col">Ref</th>
                                            <th scope="col">Software</th>
                                            <th scope="col">Workflow tool</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {group.frequency_scale_factors.map((item) => (
                                            <tr key={item.frequency_scale_factor_ref}>
                                                <td data-label="Ref">
                                                    <Link to={frequencyScaleFactorPath(item.frequency_scale_factor_ref)}>
                                                        <code className="data">{item.frequency_scale_factor_ref}</code>
                                                    </Link>
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

function QuantityFact({ value }: { value: number }) {
    return <span className="data">{value}</span>
}

function UsageSection({ rows, available, total }: { rows: LevelOfTheoryUsage[] | null; available: boolean; total: number }) {
    const list = rows ?? []
    return (
        <section className="ledger-section" aria-labelledby="lot-usage-heading">
            <SectionHeading
                id="lot-usage-heading"
                intro={`${total} calculation${total === 1 ? "" : "s"}; first 50 listed. All on calculation search.`}
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
                            {list.map((row) => (
                                <tr key={row.calculation_ref}>
                                    <td data-label="Calculation"><Link className="data" to={`/calculations/${row.calculation_ref}`}>{row.calculation_ref}</Link></td>
                                    <td data-label="Type">{words(row.type)}</td>
                                    <td data-label="Owner">
                                        {row.record_ref && row.record_type
                                            ? <span className="data">{row.record_ref}</span>
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
