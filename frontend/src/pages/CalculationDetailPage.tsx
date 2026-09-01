import type { ReactNode } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../calculation-detail.css"
import { lotLabel } from "../api/scientificSchemas"
import {
    type CalculationArtifact,
    type CalculationConstraint,
    type CalculationDependency,
    type CalculationEnergyCorrection,
    type CalculationExecutionEnvironment,
    type CalculationFreqMode,
    type CalculationGeometryLink,
    type CalculationGeometryValidation,
    type CalculationIRC,
    type CalculationImaginaryModeProjection,
    type CalculationParameter,
    type CalculationPathSearch,
    type CalculationRecord,
    type CalculationSCFStability,
    type CalculationScan,
    type CalculationSpinDiagnostic,
    type CalculationWavefunctionDiagnostic,
    type OnDemandSectionToken,
} from "../api/calculationApi"
import { EnergyDisplay } from "../components/EnergyDisplay"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { QuantityValue } from "../components/QuantityValue"
import { RecordStatus } from "../components/RecordStatus"
import { chargeDisplay, spinDisplay } from "../domain/chemistryFormat"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { formatQuantity } from "../domain/quantityFormat"
import { useCalculation } from "../hooks/useCalculation"
import { useCalculationSection, type CalculationSectionState } from "../hooks/useCalculationSection"

// ---------------------------------------------------------------------------
// Section-loading design (see the brief's "design problem" — recorded here
// so the choice travels with the code):
//
// The archive advertises 19 public opt-in sections via `include=<token>`
// (a 20th, `internal_ids`, is never requested by a public client; `trust`
// is a further, separate opt-in kept out of this slice). Fetching all 19
// on every page load is exactly what issue #269 charges the sibling
// surface for. Fetching none until every disclosure is clicked would make
// dependency arrows — the one thing this slice exists to get right — an
// extra click away from a page whose entire point is showing them.
//
// So the fetch is split in two, matched to the values above:
//
// - EAGER (one request, bundled with the default record): `results`,
//   `dependencies`, `review`, `input_geometries`, `output_geometries`.
//   `dependencies` is eager because it is the rule this slice is graded
//   on — a dependency arrow must never be one click further away than the
//   page itself, or a reader who doesn't click still sees calculation type
//   alone and may reconstruct the wrong graph in their head. `results` is
//   eager because it is the number the calculation exists to report.
//   `review` and the two geometry links are eager because they are small,
//   already-loaded-elsewhere-on-this-surface link lists the IA trail
//   depends on (owner entry / geometries), not separate heavy payloads.
//
// - ON DEMAND (14 remaining tokens, one `<details>` each): every other
//   heavy section. `available_sections` (18 booleans, one per token that
//   has one — see `readSectionField`) tells the page which of these would
//   return data *before* asking, so a section known empty renders a
//   static "not present" line with no request at all, and a section that
//   may have data renders as an expandable disclosure that fetches its
//   own token, and only its own token, the first time it opens.
//
// `imaginary_mode_projections` has no `has_imaginary_mode_projections`
// flag (it's a computed-at-read-time projection, not a stored table) —
// `available_sections.has_hessian` is used instead, per its own docstring
// in the service layer: without a Hessian the projection is not merely
// absent, it is not determinable, and that is a different, and more
// honest, message than "not present".
//
// `trust` is excluded from this slice entirely: it is not one of the 19
// tokens `CALCULATION_RECORD_SECTIONS` gates, and unlike every token
// above it, no probe on the default record says whether it would return
// anything.
// ---------------------------------------------------------------------------

const CALC_TYPE_LABELS: Record<string, string> = {
    opt: "Optimisation",
    freq: "Frequency",
    sp: "Single-point",
    irc: "IRC",
    scan: "Scan",
    path_search: "Path search",
    conf: "Conformer",
}

const DEPENDENCY_ROLE_LABELS: Record<string, string> = {
    optimized_from: "optimized from",
    freq_on: "frequency on",
    single_point_on: "single point on",
    arkane_source: "Arkane source",
    irc_start: "IRC start",
    irc_followup: "IRC follow-up",
    scan_parent: "scan parent",
}

const typeLabel = (type: string) => CALC_TYPE_LABELS[type] ?? type.replaceAll("_", " ")
const roleLabel = (role: string) => DEPENDENCY_ROLE_LABELS[role] ?? role.replaceAll("_", " ")
const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

/**
 * The one headline energy figure this page promotes into its header —
 * see the design brief's "Promote the answer". Only `sp` (electronic
 * energy) and `opt` (final energy) calculations have a single number
 * that answers "what did this calculation compute" — `freq`/`scan`/
 * `irc`/`path_search` results are multi-valued or process-shaped and get
 * no headline here, never a guessed stand-in.
 *
 * Reads `results.kind` (never `type`, see `ResultsSection`'s own
 * comment) so the headline and the "Result" section below always agree
 * about which calculation's result they are describing — this function
 * takes the SAME `calculation.results` object the page already fetched,
 * never a second, independently-selected record.
 */
function headlineEnergy(results: CalculationRecord["results"]): { label: string; valueHartree: number | null } | null {
    if (!results) return null
    if (results.kind === "sp") return { label: "Electronic energy", valueHartree: results.sp?.electronic_energy_hartree ?? null }
    if (results.kind === "opt") return { label: "Final energy", valueHartree: results.opt?.final_energy_hartree ?? null }
    return null
}

// Three states an include-gated eager section can be in, kept distinct per
// the house rule (see ConformerObservationPage.tsx): absence describes the
// request, null/[] describes the data. This client's eager include set is
// fixed (EAGER_SECTION_TOKENS), so "not-requested" should never actually
// fire for these five fields — but the wire type is `T | null | undefined`,
// and a field that silently dropped out of a future response must not be
// reported as "returned and empty".
type SectionAvailability = "not-requested" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

function scalarAvailability<T>(value: T | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null) return "empty"
    return "populated"
}

/**
 * Shared empty/absent rendering for an eager section. `contradicted` is
 * only meaningful where an `available_sections` flag actually exists for
 * this field (results / dependencies / input_geometries /
 * output_geometries) — unlike the conformer-observation surface, this
 * page's `available_sections` is genuinely measured (see the calculation
 * detail report), so a contradiction here is worth surfacing rather than
 * silently absorbing. `review_history` has no matching flag and is never
 * passed one.
 */
function SectionEmptyMessage({ availability, emptyText, contradicted }: {
    availability: SectionAvailability
    emptyText: string
    contradicted?: boolean
}) {
    if (availability === "not-requested") {
        return <p className="empty-projection">This section was not requested for this view.</p>
    }
    return (
        <p className="empty-projection">
            {emptyText}
            {contradicted
                ? " The archive marks this calculation as having recorded evidence here; this view did not return it."
                : ""}
        </p>
    )
}

export default function CalculationDetailPage() {
    const { calculationRef = "" } = useParams<{ calculationRef: string }>()
    const state = useCalculation(calculationRef)

    if (state.status === "ready") {
        // Keyed by the resolved record's own ref so every on-demand
        // section's fetch-once state resets when navigating from one
        // calculation straight to another (a dependency link, say)
        // without an intervening unmount of this route.
        return <CalculationDetail key={state.record.calculation.calculation_ref} calculation={state.record} />
    }
    return (
        <RecordStatus
            state={state}
            ref={calculationRef}
            kind="calculation"
            loadingDetail="Retrieving the calculation record and its opt-in results and dependency evidence."
        />
    )
}

function CalculationDetail({ calculation }: { calculation: CalculationRecord }) {
    const {
        calculation: core,
        owner,
        level_of_theory: lot,
        software_release: software,
        workflow_tool_release: workflow,
        literature,
        provenance,
        available_sections: available,
    } = calculation

    const resultsAvailability = scalarAvailability(calculation.results)
    const dependenciesAvailability = sectionAvailability(calculation.dependencies)
    const inputGeometriesAvailability = sectionAvailability(calculation.input_geometries)
    const outputGeometriesAvailability = sectionAvailability(calculation.output_geometries)
    const reviewAvailability = sectionAvailability(calculation.review_history)

    const dependencies = calculation.dependencies ?? []
    const inputGeometries = calculation.input_geometries ?? []
    const outputGeometries = calculation.output_geometries ?? []
    const reviewHistory = calculation.review_history ?? []

    const ownerSpecies = owner.kind === "species_entry" ? (owner.species_entry ?? null) : null
    const ownerTS = owner.kind === "transition_state_entry" ? (owner.transition_state_entry ?? null) : null
    const headline = headlineEnergy(calculation.results)
    // `provenance.submission_ref` is `string | null | undefined` on the
    // wire: `undefined` means the KEY ITSELF was omitted (an anonymous
    // caller — see `CalculationEvidenceProvenanceSummary`'s own
    // docstring), `null` means an authenticated caller was told there is
    // no linked submission, and a string is the ref. Only the first case
    // renders no row at all; `null` still renders as "not recorded" so
    // an authenticated reader can tell "checked, none" from "not told".
    const submissionRefKeyPresent = "submission_ref" in provenance

    return (
        <section className="calc-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                {ownerSpecies && (
                    <>
                        <span aria-hidden="true">/</span>
                        <Link to={`/species/${ownerSpecies.species_ref}`}>Species</Link>
                        <span aria-hidden="true">/</span>
                        <Link to={`/species-entries/${ownerSpecies.species_entry_ref}`}>Species entry</Link>
                    </>
                )}
                <span aria-hidden="true">/</span>
                <span aria-current="page">Calculation</span>
            </nav>

            <PageShell
                identity={(
                    <header className="record-header">
                        <p className="eyebrow">Calculation · deposited evidence</p>
                        <div className="record-title">
                            <h1>{typeLabel(core.type)} calculation</h1>
                            <span className="review-badge">{statusLabel(core.review.status)}</span>
                        </div>
                        <p className="record-intro">
                            One calculation record. Its heavy sections — results, dependencies, artifacts, per-mode
                            and per-point data — are opt-in on this endpoint; this page loads a few small ones up
                            front and leaves the rest behind disclosures you can open.
                        </p>

                        {/* The answer this page exists to give, promoted to the
                            largest weight on the page — previously it sat inside the
                            "Result" section below at the same visual weight as the
                            dependency graph and review history. Only sp/opt
                            calculations have a single headline energy; every other
                            calculation type renders nothing here rather than a
                            fabricated or misleading figure. */}
                        {headline && (
                            <div className="calc-headline-energy">
                                <EnergyDisplay valueHartree={headline.valueHartree} label={headline.label} size="headline" />
                            </div>
                        )}

                        {/* Shared header order: identity, then classification
                            facets, then provenance — see `RecordIdentityHeader`'s
                            own docstring. `OwnerCard` below is this page's identity
                            tier (kept as its own component rather than folded into
                            `RecordIdentityHeader` — it renders owner LINKS this
                            endpoint does not model) and, since #322, is also where
                            the classification facets live: an "Entry kind" row
                            beside `OwnerCard`'s own "Electronic state" row, not a
                            pill row repeating the same two facts a second time
                            ("no pill boxes" — see `SpeciesEntrySummary.tsx`'s
                            `EntryIdentity` for the report this fixes elsewhere on
                            the same shared header pattern). */}
                        <OwnerCard ownerSpecies={ownerSpecies} ownerTS={ownerTS} />

                        <dl className="record-context">
                            <div><dt>Calculation ref</dt><dd>{core.calculation_ref}</dd></div>
                            <div><dt>Quality</dt><dd>{core.quality}</dd></div>
                            <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
                            <div><dt>Level of theory</dt><dd>{lot ? lotLabel(lot) : "not recorded"}</dd></div>
                            {/* The compact label above can be identical for two different rows —
                                it omits dispersion, solvent and level_of_theory_ref on purpose (see
                                the schema comment on `levelOfTheorySchema`). Those are the fields
                                that actually distinguish them, so they get their own rows rather
                                than being folded into the label. */}
                            <div><dt>Level of theory ref</dt><dd>{lot?.level_of_theory_ref ?? "not recorded"}</dd></div>
                            <div><dt>Dispersion</dt><dd>{lot?.dispersion ?? "not recorded"}</dd></div>
                            <div><dt>Solvent</dt><dd>{lot?.solvent ?? "not recorded"}</dd></div>
                            <div>
                                <dt>Software</dt>
                                <dd>{softwareLabel(software) ?? "not recorded"}</dd>
                            </div>
                            <div><dt>Software release ref</dt><dd>{software?.software_release_ref ?? "not recorded"}</dd></div>
                            <div>
                                <dt>Workflow tool</dt>
                                <dd>{toolReleaseLabel(workflow) ?? "not recorded"}</dd>
                            </div>
                            <div><dt>Workflow tool release ref</dt><dd>{workflow?.workflow_tool_release_ref ?? "not recorded"}</dd></div>
                            {/* No row at all when the key itself is absent (anonymous
                                caller) — see `submissionRefKeyPresent` above. An
                                anonymous reader is never told "not recorded" for a
                                question they were never allowed to ask. */}
                            {submissionRefKeyPresent && (
                                <div><dt>Submission ref</dt><dd>{provenance.submission_ref ?? "not recorded"}</dd></div>
                            )}
                            <div>
                                <dt>Literature</dt>
                                <dd>{literature ? `${literature.title ?? literature.literature_ref}${literature.year ? ` (${literature.year})` : ""}` : "not recorded"}</dd>
                            </div>
                            <div><dt>Literature ref</dt><dd>{literature?.literature_ref ?? "not recorded"}</dd></div>
                        </dl>
                    </header>
                )}
            >
            <section className="ledger-summary" aria-label="Calculation evidence summary">
                <Metric label="Input geometries" value={inputGeometries.length} />
                <Metric label="Output geometries" value={outputGeometries.length} />
                <Metric label="Dependency edges" value={dependencies.length} />
                <div className="coverage-card">
                    <span>Evidence on this calculation</span>
                    <strong>
                        result {provenance.has_result ? "recorded" : "not recorded"} · geometry validation
                        {` ${provenance.geometry_validation_status === "not_present" ? "not recorded" : "recorded"}`} · SCF
                        stability
                        {` ${provenance.scf_stability_status === "not_present" ? "not recorded" : "recorded"}`} · convergence
                        {` ${provenance.converged === null || provenance.converged === undefined ? "not recorded" : provenance.converged ? "converged" : "not converged"}`}
                    </strong>
                    <p>
                        Presence says a check exists on this record, not that it passed — the actual outcome,
                        where one was recorded, is in the matching section below.
                    </p>
                </div>
            </section>

            <ResultsSection
                results={calculation.results ?? null}
                type={core.type}
                availability={resultsAvailability}
                contradicted={resultsAvailability === "empty" && available.has_results}
            />

            <GeometriesSection
                input={inputGeometries}
                output={outputGeometries}
                inputAvailability={inputGeometriesAvailability}
                inputContradicted={inputGeometriesAvailability === "empty" && available.has_input_geometries}
                outputAvailability={outputGeometriesAvailability}
                outputContradicted={outputGeometriesAvailability === "empty" && available.has_output_geometries}
            />

            <OnDemandSections calculation={calculation} available={available} />

            {/* Demoted below every evidence section (results, geometries,
                the on-demand disclosures): the graph and the review log are
                provenance ABOUT this record, not the scientific evidence
                itself, and the owner's complaint was that they were
                competing with the evidence for the same visual weight. */}
            <DependenciesSection
                dependencies={dependencies}
                ownRef={core.calculation_ref}
                availability={dependenciesAvailability}
                contradicted={dependenciesAvailability === "empty" && available.has_dependencies}
            />

            <ReviewHistorySection
                entries={reviewHistory}
                currentStatus={core.review.status}
                availability={reviewAvailability}
            />
            </PageShell>
        </section>
    )
}

function OwnerCard({
    ownerSpecies, ownerTS,
}: {
    ownerSpecies: NonNullable<CalculationRecord["owner"]["species_entry"]> | null
    ownerTS: NonNullable<CalculationRecord["owner"]["transition_state_entry"]> | null
}) {
    if (ownerSpecies) {
        return (
            <section className="owner-card" aria-labelledby="owner-heading-species-entry">
                <SectionHeading id="owner-heading-species-entry">Owner</SectionHeading>
                <dl>
                    <div>
                        <dt>Species</dt>
                        <dd><Link to={`/species/${ownerSpecies.species_ref}`}>{ownerSpecies.species_ref}</Link></dd>
                    </div>
                    <div>
                        <dt>Species entry</dt>
                        <dd>
                            <Link to={`/species-entries/${ownerSpecies.species_entry_ref}`}>
                                {ownerSpecies.species_entry_label ?? ownerSpecies.species_entry_ref}
                            </Link>
                        </dd>
                    </div>
                    {/* The linked text above prefers the human label; the stable ref stays
                        visible on its own line regardless, per the "never label ?? ref" rule —
                        a label must never be the only way to see the ref. */}
                    <div><dt>Species entry ref</dt><dd>{ownerSpecies.species_entry_ref}</dd></div>
                    <div><dt>Structure</dt><dd>{ownerSpecies.canonical_smiles}</dd></div>
                    <div><dt>InChIKey</dt><dd>{ownerSpecies.inchi_key}</dd></div>
                    <div>
                        <dt>Charge / multiplicity</dt>
                        <dd>{chargeDisplay(ownerSpecies.charge)} / {spinDisplay(ownerSpecies.multiplicity)}</dd>
                    </div>
                    <div><dt>Entry kind</dt><dd>{statusLabel(ownerSpecies.species_entry_kind)}</dd></div>
                    <div><dt>Electronic state</dt><dd>{ownerSpecies.electronic_state_kind}</dd></div>
                </dl>
            </section>
        )
    }
    if (ownerTS) {
        return (
            <section className="owner-card" aria-labelledby="owner-heading-transition-state-entry">
                <SectionHeading id="owner-heading-transition-state-entry">Owner</SectionHeading>
                <p className="section-note">
                    This calculation belongs to a transition-state entry. That record does not yet have a
                    dedicated page on this archive projection, so its reference is shown without a link.
                </p>
                <dl>
                    <div><dt>Transition state</dt><dd>{ownerTS.transition_state_ref}</dd></div>
                    <div><dt>Transition state entry</dt><dd>{ownerTS.label ?? ownerTS.transition_state_entry_ref}</dd></div>
                    <div><dt>Transition state entry ref</dt><dd>{ownerTS.transition_state_entry_ref}</dd></div>
                    <div>
                        <dt>Charge / multiplicity</dt>
                        <dd>{chargeDisplay(ownerTS.charge)} / {spinDisplay(ownerTS.multiplicity)}</dd>
                    </div>
                    <div><dt>Status</dt><dd>{statusLabel(ownerTS.status)}</dd></div>
                    <div><dt>Reaction entry</dt><dd>{ownerTS.reaction_entry_ref ?? "not recorded"}</dd></div>
                </dl>
            </section>
        )
    }
    return null
}

function Metric({ label, value }: { label: string; value: number }) {
    return (
        <div className="metric">
            <span>{label}</span>
            <strong>{value}</strong>
        </div>
    )
}

// ---------------------------------------------------------------------------
// Eager sections
// ---------------------------------------------------------------------------

function ResultsSection({ results, type, availability, contradicted }: {
    results: CalculationRecord["results"]
    type: string
    availability: SectionAvailability
    contradicted: boolean
}) {
    // The heading names the same source ResultBody dispatches on
    // (`results.kind`) rather than `type`, so the two can never disagree —
    // falling back to `type` only when there is no result to read a kind
    // from at all.
    const kindLabel = results ? typeLabel(results.kind) : typeLabel(type)
    return (
        <section className="ledger-section" aria-labelledby="results-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Deposited evidence</p>
                <SectionHeading id="results-heading">Result</SectionHeading>
                <p>The primary scientific result for this {kindLabel.toLowerCase()} calculation.</p>
            </div>
            {availability === "populated" && results ? <ResultBody results={results} /> : (
                <SectionEmptyMessage
                    availability={availability}
                    emptyText="No result row is recorded for this calculation."
                    contradicted={contradicted}
                />
            )}
        </section>
    )
}

function ResultBody({ results }: { results: NonNullable<CalculationRecord["results"]> }) {
    const pairs: [string, ReactNode][] = []
    if (results.kind === "sp" && results.sp) {
        // 6dp, matching `landing.py`'s `calculationView` headline
        // (`fixed(energy.energy_hartree, 6, "hartree")`) -- the one
        // electronic-energy precision the ported digits table actually
        // specifies. `unitOverride: null` because the label already says
        // "(hartree)". `Uncertainty` has no matching entry in that table,
        // so it stays a raw pass-through rather than a guessed precision --
        // same reason `zpe_hartree` below stays raw: it is a different
        // quantity than `energy_hartree`/`final_energy_hartree` and
        // `landing.py` never gives it a precision to port.
        pairs.push(["Electronic energy (hartree)", <QuantityValue value={formatQuantity("calculation_electronic_energy_hartree", results.sp.electronic_energy_hartree, null)} />])
        pairs.push(["Uncertainty (hartree)", results.sp.electronic_energy_uncertainty_hartree ?? "not recorded"])
    } else if (results.kind === "opt" && results.opt) {
        pairs.push(["Converged", boolLabel(results.opt.converged)])
        pairs.push(["Steps", results.opt.n_steps ?? "not recorded"])
        // Same physical quantity, same unit, as the sp branch's "Electronic
        // energy" above -- an `opt` calculation's final energy is still an
        // electronic energy in hartree, so it gets the same 6dp spec rather
        // than a raw double under an identically-styled heading.
        pairs.push(["Final energy (hartree)", <QuantityValue value={formatQuantity("calculation_electronic_energy_hartree", results.opt.final_energy_hartree, null)} />])
    } else if (results.kind === "freq" && results.freq) {
        pairs.push(["Imaginary modes (n_imag)", results.freq.n_imag ?? "not recorded"])
        pairs.push(["Imaginary frequency (cm-1)", results.freq.imag_freq_cm1 ?? "not recorded"])
        pairs.push(["ZPE (hartree)", results.freq.zpe_hartree ?? "not recorded"])
        pairs.push(["Reaction-coordinate mode", results.freq.reaction_coordinate_mode_index ?? "not designated"])
        pairs.push(["n_imag at or above tau", results.freq.n_imag_at_or_above_tau ?? "not determinable"])
    } else if (results.kind === "scan" && results.scan) {
        pairs.push(["Dimension", results.scan.dimension ?? "not recorded"])
        pairs.push(["Relaxed scan", boolLabel(results.scan.is_relaxed)])
    } else if (results.kind === "irc" && results.irc) {
        pairs.push(["Direction", results.irc.direction ?? "not recorded"])
        pairs.push(["Has forward leg", boolLabel(results.irc.has_forward)])
        pairs.push(["Has reverse leg", boolLabel(results.irc.has_reverse)])
    } else if (results.kind === "path_search" && results.path_search) {
        pairs.push(["Method", results.path_search.method ?? "not recorded"])
        pairs.push(["Converged", boolLabel(results.path_search.converged)])
        pairs.push(["Points", results.path_search.n_points ?? "not recorded"])
    }
    if (pairs.length === 0) {
        // `results.kind` names a type this page has no branch for, or the
        // matching sub-object is missing — a payload shape this build
        // doesn't recognise, not an honest "nothing recorded". Say so
        // rather than rendering a heading and prose over an empty list.
        return <p className="empty-projection" role="alert">
            This result's shape (kind “{results.kind}”) is not recognised by this view.
        </p>
    }
    return <dl className="kv-list">{pairs.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
    ))}</dl>
}

function boolLabel(value: boolean | null | undefined) {
    if (value === null || value === undefined) return "not recorded"
    return value ? "Yes" : "No"
}

/**
 * Renders exactly, and only, the edges the archive returned under
 * `include=dependencies`. No edge here is inferred from `type`, timestamps,
 * or ref ordering — see the module docstring above; this is the one rule
 * the whole slice is graded on. Each row's relationship, role and related
 * ref all come from that one row's own `dep` — see
 * `CalculationDetailPage.test.tsx`'s per-row binding test, which exists
 * specifically because a fixture with only one edge cannot tell a correct
 * per-row read apart from "always show the first row" or "guess the role
 * from the direction".
 */
function DependenciesSection({ dependencies, ownRef, availability, contradicted }: {
    dependencies: CalculationDependency[]
    ownRef: string
    availability: SectionAvailability
    contradicted: boolean
}) {
    return (
        <section className="ledger-section" aria-labelledby="dependencies-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Deposited provenance</p>
                <SectionHeading id="dependencies-heading">Dependency graph</SectionHeading>
                <p>
                    Every edge here comes from a stored <code>calculation_dependency</code> row. Nothing is
                    inferred from calculation type, timestamps, or reference ordering.
                </p>
            </div>
            {availability === "populated" ? (
                <table className="stage-table" aria-label={`Dependency edges for ${ownRef}`}>
                    <thead>
                        <tr>
                            <th scope="col">Relationship</th>
                            <th scope="col">Role</th>
                            <th scope="col">Related calculation</th>
                        </tr>
                    </thead>
                    <tbody>
                        {dependencies.map((dep, index) => {
                            const otherRef = dep.direction === "parent" ? dep.child_calculation_ref : dep.parent_calculation_ref
                            const relationship = dep.direction === "parent" ? "feeds into" : "depends on"
                            return (
                                <tr key={`${dep.role}-${dep.direction}-${otherRef}-${index}`}>
                                    <td data-label="Relationship">{relationship}</td>
                                    <td data-label="Role">{roleLabel(dep.role)}</td>
                                    <td data-label="Related calculation">
                                        <Link to={`/calculations/${otherRef}`}>{otherRef}</Link>
                                    </td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            ) : (
                <SectionEmptyMessage
                    availability={availability}
                    emptyText="No dependency edges are recorded for this calculation."
                    contradicted={contradicted}
                />
            )}
        </section>
    )
}

function GeometriesSection({
    input, output, inputAvailability, inputContradicted, outputAvailability, outputContradicted,
}: {
    input: CalculationGeometryLink[]
    output: CalculationGeometryLink[]
    inputAvailability: SectionAvailability
    inputContradicted: boolean
    outputAvailability: SectionAvailability
    outputContradicted: boolean
}) {
    return (
        <section className="ledger-section geometry-ledger" aria-labelledby="geometries-heading">
            <p className="eyebrow">Stored coordinates</p>
            <SectionHeading id="geometries-heading">Geometries</SectionHeading>
            <p>Links to the full coordinate records this calculation consumed and produced.</p>
            <GeometryLinkList
                title="Input" links={input} emptyText="No input geometries are recorded."
                availability={inputAvailability} contradicted={inputContradicted}
            />
            <GeometryLinkList
                title="Output" links={output} emptyText="No output geometries are recorded."
                availability={outputAvailability} contradicted={outputContradicted}
            />
        </section>
    )
}

function GeometryLinkList({ title, links, emptyText, availability, contradicted }: {
    title: string
    links: CalculationGeometryLink[]
    emptyText: string
    availability: SectionAvailability
    contradicted: boolean
}) {
    return (
        <div>
            <h3 className="ledger-kicker">{title}</h3>
            {availability === "populated" ? (
                <div className="geometry-links">
                    {links.map((link) => (
                        <div className="geometry-link" key={`${title}-${link.geometry_ref}`}>
                            <Link to={`/geometries/${link.geometry_ref}`}>{link.geometry_ref}</Link>
                            <span>
                                {link.role ? `${statusLabel(link.role)} · ` : ""}
                                {link.natoms != null ? `${link.natoms} atoms` : "atom count not recorded"}
                            </span>
                        </div>
                    ))}
                </div>
            ) : <SectionEmptyMessage availability={availability} emptyText={emptyText} contradicted={contradicted} />}
        </div>
    )
}

function ReviewHistorySection({ entries, currentStatus, availability }: {
    entries: CalculationRecord["review_history"]
    currentStatus: string
    availability: SectionAvailability
}) {
    const rows = entries ?? []
    return (
        <section className="ledger-section" aria-labelledby="review-heading">
            <div className="ledger-heading">
                <p className="eyebrow">Review &amp; trust</p>
                <SectionHeading id="review-heading">Review history</SectionHeading>
                <p>The current status is {statusLabel(currentStatus)}. This is the record of how it got there.</p>
            </div>
            {availability === "populated" ? (
                <table className="stage-table" aria-label="Review history">
                    <thead>
                        <tr>
                            <th scope="col">Status</th>
                            <th scope="col">Reviewed at</th>
                            <th scope="col">Note</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((entry, index) => (
                            <tr key={`review-${index}`}>
                                <td data-label="Status">{statusLabel(entry.status)}</td>
                                <td data-label="Reviewed at">{isoDate(entry.reviewed_at)}</td>
                                <td data-label="Note">{entry.note ?? "not recorded"}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : (
                // No `has_review` flag exists on `available_sections` (unlike
                // the other four eager sections), so there is nothing to
                // cross-check a contradiction against here.
                <SectionEmptyMessage
                    availability={availability}
                    emptyText="No review history is recorded for this calculation."
                />
            )}
        </section>
    )
}

// ---------------------------------------------------------------------------
// On-demand sections
// ---------------------------------------------------------------------------

/**
 * One disclosure gated on an `available_sections` flag (or, for
 * `imaginary_mode_projections`, on `has_hessian` — see the module
 * docstring). `available === false` renders a static, request-free "not
 * present" line; otherwise renders an expandable `<details>` that fetches
 * its own token, once, the first time it opens.
 *
 * This is the first surface in the project where content arrives after a
 * user gesture rather than on page load, so there is no accessible-live-
 * region precedent to inherit here. The live region is a short status
 * *sentence*, not a wrapper around the fetched payload:
 * `role="status"` carries an implicit `aria-atomic="true"`, so a live
 * region that contained the payload itself would have an assistive
 * technology re-speak the *entire* region on every change — for
 * `freq_modes` on a large molecule, that is dozens of rows of numbers
 * read aloud unprompted the moment a fetch resolves. Announcing "<heading>
 * loaded." and rendering the actual table/list as an ordinary sibling
 * (outside the live region) gives the same "something happened" signal
 * without forcing a full read-out of a payload the user is about to
 * navigate as a table on their own terms.
 *
 * The error state also uses `role="status"` (polite), not `role="alert"`
 * (assertive) nested inside it: nesting a live region inside another is
 * discouraged — announcement behaviour is implementation-defined across
 * assistive technologies and double-announcement is a common outcome —
 * and an assertive interrupt is not warranted here anyway, since the user
 * just requested this section and is already attending to it.
 */
function LazySection<T>({
    heading, available, notAvailableText, state, onOpen, children,
}: {
    heading: string
    available: boolean
    notAvailableText: string
    state: CalculationSectionState<T>
    onOpen: () => void
    children: (data: T) => ReactNode
}) {
    const headingId = `section-${heading.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`
    if (!available) {
        return (
            <section className="ledger-section" aria-labelledby={headingId}>
                <SectionHeading id={headingId}>{heading}</SectionHeading>
                <p className="empty-projection">{notAvailableText}</p>
            </section>
        )
    }
    return (
        <details
            className="ledger-section"
            onToggle={(event) => {
                if ((event.target as HTMLDetailsElement).open) onOpen()
            }}
        >
            <summary><SectionHeading id={headingId}>{heading}</SectionHeading></summary>
            <p className="section-note" role="status">
                {state.status === "idle" && "Expand to load this section from the archive."}
                {state.status === "loading" && "Loading…"}
                {state.status === "error" && state.message}
                {state.status === "ready" && `${heading} loaded.`}
            </p>
            {state.status === "ready" && children(state.data)}
        </details>
    )
}

function KVList({ pairs }: { pairs: [string, ReactNode][] }) {
    return <dl className="kv-list">{pairs.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value ?? "not recorded"}</dd></div>
    ))}</dl>
}

function OnDemandSections({ calculation, available }: {
    calculation: CalculationRecord
    available: CalculationRecord["available_sections"]
}) {
    return (
        <>
            <EnergyCorrectionsSection calculationRef={calculation.calculation.calculation_ref} available={available.has_energy_corrections} />
            <GeometryValidationSection calculationRef={calculation.calculation.calculation_ref} available={available.has_geometry_validation} />
            <SCFStabilitySection calculationRef={calculation.calculation.calculation_ref} available={available.has_scf_stability} />
            <WavefunctionDiagnosticSection calculationRef={calculation.calculation.calculation_ref} available={available.has_wavefunction_diagnostic} />
            <SpinDiagnosticSection calculationRef={calculation.calculation.calculation_ref} available={available.has_spin_diagnostic} />
            <ParametersSection calculationRef={calculation.calculation.calculation_ref} available={available.has_parameters} />
            <ConstraintsSection calculationRef={calculation.calculation.calculation_ref} available={available.has_constraints} />
            <FreqModesSection calculationRef={calculation.calculation.calculation_ref} available={available.has_freq_modes} />
            <ImaginaryModeProjectionsSection calculationRef={calculation.calculation.calculation_ref} hessianAvailable={available.has_hessian} />
            <ScanSection calculationRef={calculation.calculation.calculation_ref} available={available.has_scan} />
            <IRCSection calculationRef={calculation.calculation.calculation_ref} available={available.has_irc} />
            <PathSearchSection calculationRef={calculation.calculation.calculation_ref} available={available.has_path_search} />
            <ArtifactsSection calculationRef={calculation.calculation.calculation_ref} available={available.has_artifacts} />
            <ExecutionEnvironmentSection calculationRef={calculation.calculation.calculation_ref} available={available.has_execution_environment} />
        </>
    )
}

function useSection<T>(calculationRef: string, token: OnDemandSectionToken) {
    return useCalculationSection<T>(calculationRef, token)
}

function EnergyCorrectionsSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationEnergyCorrection[] | null>(calculationRef, "energy_corrections")
    return (
        <LazySection
            heading="Energy corrections"
            available={available}
            notAvailableText="No applied energy correction cites this calculation as its source."
            state={state}
            onOpen={open}
        >
            {(rows) => (rows?.length ? (
                <table className="stage-table" aria-label="Applied energy corrections">
                    <thead>
                        <tr>
                            <th scope="col">Role</th>
                            <th scope="col">Applied value</th>
                            <th scope="col">Target</th>
                            <th scope="col">Scheme</th>
                            <th scope="col">Scheme ref</th>
                            <th scope="col">Frequency scale factor ref</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, index) => (
                            <tr key={`ec-${index}`}>
                                <td data-label="Role">{statusLabel(row.application_role)}</td>
                                <td data-label="Applied value">{row.applied_value} {row.applied_value_unit}</td>
                                <td data-label="Target">{row.target_record_ref ?? "not recorded"}</td>
                                <td data-label="Scheme">{row.energy_correction_scheme_name ?? "not recorded"}</td>
                                <td data-label="Scheme ref">{row.energy_correction_scheme_ref ?? "not recorded"}</td>
                                <td data-label="Frequency scale factor ref">{row.frequency_scale_factor_ref ?? "not recorded"}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : <p className="empty-projection">The archive returned no correction rows.</p>)}
        </LazySection>
    )
}

function GeometryValidationSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationGeometryValidation[] | null>(calculationRef, "geometry_validation")
    return (
        <LazySection
            heading="Geometry validation"
            available={available}
            notAvailableText="No geometry-validation check is recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => {
                const row = rows?.[0]
                if (!row) return <p className="empty-projection">The archive returned no validation row.</p>
                return (
                    <>
                        <p className="section-note">
                            <code>is_isomorphic</code> and <code>formula_matches</code> below are the same
                            stored verdict under two names — despite the name, neither claims full
                            structural isomorphism, only that per-element atom counts match the declared
                            formula. It can read true for a rearranged or dissociated structure; prefer
                            reading it as <code>formula_matches</code>.
                        </p>
                        <KVList pairs={[
                            ["Status", statusLabel(row.validation_status)],
                            ["Formula matches", boolLabel(row.formula_matches)],
                            ["is_isomorphic (legacy name, same value)", boolLabel(row.is_isomorphic ?? row.formula_matches)],
                            ["RMSD", row.rmsd ?? "not recorded"],
                            ["Reason", row.validation_reason ?? "not recorded"],
                            [
                                "Input geometry",
                                row.input_geometry_ref
                                    ? <Link to={`/geometries/${row.input_geometry_ref}`}>{row.input_geometry_ref}</Link>
                                    : "not recorded",
                            ],
                            [
                                "Output geometry",
                                row.output_geometry_ref
                                    ? <Link to={`/geometries/${row.output_geometry_ref}`}>{row.output_geometry_ref}</Link>
                                    : "not recorded",
                            ],
                        ]} />
                    </>
                )
            }}
        </LazySection>
    )
}

function SCFStabilitySection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationSCFStability[] | null>(calculationRef, "scf_stability")
    return (
        <LazySection
            heading="SCF stability"
            available={available}
            notAvailableText="No SCF stability analysis is recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => {
                const row = rows?.[0]
                if (!row) return <p className="empty-projection">The archive returned no stability row.</p>
                return (
                    <>
                        {row.source_calculation_ref && (
                            <p className="section-note">
                                The source calculation below is the analysis's own provenance, and is not
                                necessarily this calculation.
                            </p>
                        )}
                        <KVList pairs={[
                            ["Status", statusLabel(row.status)],
                            ["Lowest eigenvalue", row.lowest_eigenvalue ?? "not recorded"],
                            ["Instability count", row.instability_count ?? "not recorded"],
                            ["Re-optimized wavefunction", boolLabel(row.reoptimized_wavefunction)],
                            [
                                "Source calculation",
                                row.source_calculation_ref
                                    ? <Link to={`/calculations/${row.source_calculation_ref}`}>{row.source_calculation_ref}</Link>
                                    : "not recorded",
                            ],
                        ]} />
                    </>
                )
            }}
        </LazySection>
    )
}

function WavefunctionDiagnosticSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationWavefunctionDiagnostic[] | null>(calculationRef, "wavefunction_diagnostic")
    return (
        <LazySection
            heading="Wavefunction diagnostic"
            available={available}
            notAvailableText="No wavefunction diagnostic (T1/D1) is recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => {
                const row = rows?.[0]
                if (!row) return <p className="empty-projection">The archive returned no diagnostic row.</p>
                return <KVList pairs={[
                    ["T1 diagnostic", row.t1_diagnostic ?? "not recorded"],
                    ["D1 diagnostic", row.d1_diagnostic ?? "not recorded"],
                    ["T1 norm", row.t1_norm ?? "not recorded"],
                    ["Largest T2 amplitude", row.largest_t2_amplitude ?? "not recorded"],
                ]} />
            }}
        </LazySection>
    )
}

function SpinDiagnosticSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationSpinDiagnostic[] | null>(calculationRef, "spin_diagnostic")
    return (
        <LazySection
            heading="Spin diagnostic"
            available={available}
            notAvailableText="No spin-contamination diagnostic is recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => {
                const row = rows?.[0]
                if (!row) return <p className="empty-projection">The archive returned no diagnostic row.</p>
                return <KVList pairs={[
                    ["<S^2>", row.s_squared ?? "not recorded"],
                    ["Expected <S^2>", row.s_squared_expected ?? "not recorded"],
                    ["Annihilated <S^2>", row.s_squared_annihilated ?? "not recorded"],
                ]} />
            }}
        </LazySection>
    )
}

function ParametersSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationParameter[] | null>(calculationRef, "parameters")
    return (
        <LazySection
            heading="Parsed parameters"
            available={available}
            notAvailableText="No execution parameters were parsed for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => (rows?.length ? (
                <table className="stage-table" aria-label="Parsed execution parameters">
                    <thead><tr><th scope="col">Key</th><th scope="col">Value</th><th scope="col">Section</th></tr></thead>
                    <tbody>
                        {rows.map((row, index) => (
                            <tr key={`param-${index}`}>
                                <td data-label="Key">{row.canonical_key ?? row.raw_key}</td>
                                <td data-label="Value">{row.canonical_value ?? row.raw_value}{row.unit ? ` ${row.unit}` : ""}</td>
                                <td data-label="Section">{row.section ?? "not recorded"}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : <p className="empty-projection">The archive returned no parameter rows.</p>)}
        </LazySection>
    )
}

function ConstraintsSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationConstraint[] | null>(calculationRef, "constraints")
    return (
        <LazySection
            heading="Constraints"
            available={available}
            notAvailableText="No geometry constraints are recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => (rows?.length ? (
                <table className="stage-table" aria-label="Calculation constraints">
                    <thead><tr><th scope="col">Kind</th><th scope="col">Atoms</th><th scope="col">Target value</th></tr></thead>
                    <tbody>
                        {rows.map((row) => (
                            <tr key={`constraint-${row.constraint_index}`}>
                                <td data-label="Kind">{statusLabel(row.constraint_kind)}</td>
                                <td data-label="Atoms">{row.atom_indices.join(", ")}</td>
                                <td data-label="Target value">{row.target_value ?? "not recorded"}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : <p className="empty-projection">The archive returned no constraint rows.</p>)}
        </LazySection>
    )
}

function FreqModesSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationFreqMode[] | null>(calculationRef, "freq_modes")
    return (
        <LazySection
            heading="Vibrational modes"
            available={available}
            notAvailableText="No per-mode vibrational frequencies are recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => (rows?.length ? (
                <table className="stage-table" aria-label="Vibrational modes">
                    <thead><tr><th scope="col">Mode</th><th scope="col">Frequency (cm-1)</th><th scope="col">Imaginary</th></tr></thead>
                    <tbody>
                        {rows.map((row) => (
                            <tr key={`mode-${row.mode_index}`}>
                                <td data-label="Mode">{row.mode_index}</td>
                                <td data-label="Frequency (cm-1)">{row.frequency_cm1}</td>
                                <td data-label="Imaginary">{boolLabel(row.is_imaginary)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : <p className="empty-projection">The archive returned no mode rows.</p>)}
        </LazySection>
    )
}

function ImaginaryModeProjectionsSection({ calculationRef, hessianAvailable }: { calculationRef: string; hessianAvailable: boolean }) {
    const [state, open] = useSection<CalculationImaginaryModeProjection | null>(calculationRef, "imaginary_mode_projections")
    return (
        <LazySection
            heading="Imaginary-mode projections"
            available={hessianAvailable}
            notAvailableText="Not determinable — no Hessian is stored for this calculation, so imaginary modes cannot be projected."
            state={state}
            onOpen={open}
        >
            {(projection) => {
                if (!projection) return <p className="empty-projection">The archive returned no projection.</p>
                const modes = projection.modes ?? []
                return (
                    <>
                        <KVList pairs={[
                            ["Status", statusLabel(projection.status)],
                            ["Conflicts", projection.conflict_count ?? 0],
                            ["Linear molecule", boolLabel(projection.is_linear)],
                        ]} />
                        {modes.length > 0 && (
                            <table className="stage-table" aria-label="Imaginary mode projections">
                                <thead>
                                    <tr>
                                        <th scope="col">Mode</th>
                                        <th scope="col">Frequency (cm-1)</th>
                                        <th scope="col">Declared</th>
                                        <th scope="col">Determination</th>
                                        <th scope="col">Agreement</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {modes.map((mode) => (
                                        <tr key={`imag-${mode.mode_index}`}>
                                            <td data-label="Mode">{mode.mode_index}</td>
                                            <td data-label="Frequency (cm-1)">{mode.frequency_cm1}</td>
                                            <td data-label="Declared">{mode.declared_disposition ? statusLabel(mode.declared_disposition) : "not recorded"}</td>
                                            <td data-label="Determination">{mode.determination ? statusLabel(mode.determination) : "not determined"}</td>
                                            <td data-label="Agreement">{statusLabel(mode.agreement)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </>
                )
            }}
        </LazySection>
    )
}

function ScanSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationScan | null>(calculationRef, "scan")
    return (
        <LazySection
            heading="Scan trajectory"
            available={available}
            notAvailableText="This calculation has no scan result."
            state={state}
            onOpen={open}
        >
            {(scan) => (!scan ? <p className="empty-projection">The archive returned no scan summary.</p> : (
                <KVList pairs={[
                    ["Dimension", scan.dimension],
                    ["Relaxed", boolLabel(scan.is_relaxed)],
                    ["Coordinates", scan.coordinate_count],
                    ["Points", scan.point_count],
                    ["Min electronic energy (hartree)", formatQuantity("calculation_electronic_energy_hartree", scan.min_electronic_energy_hartree, null)?.value],
                    ["Max electronic energy (hartree)", formatQuantity("calculation_electronic_energy_hartree", scan.max_electronic_energy_hartree, null)?.value],
                ]} />
            ))}
        </LazySection>
    )
}

function IRCSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationIRC | null>(calculationRef, "irc")
    return (
        <LazySection
            heading="IRC trajectory"
            available={available}
            notAvailableText="This calculation has no IRC result."
            state={state}
            onOpen={open}
        >
            {(irc) => (!irc ? <p className="empty-projection">The archive returned no IRC summary.</p> : (
                <KVList pairs={[
                    ["Direction", statusLabel(irc.direction)],
                    ["Has forward leg", boolLabel(irc.has_forward)],
                    ["Has reverse leg", boolLabel(irc.has_reverse)],
                    ["Forward points", irc.forward_point_count],
                    ["Reverse points", irc.reverse_point_count],
                    ["TS points", irc.ts_point_count],
                ]} />
            ))}
        </LazySection>
    )
}

function PathSearchSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationPathSearch | null>(calculationRef, "path_search")
    return (
        <LazySection
            heading="Path-search trajectory"
            available={available}
            notAvailableText="This calculation has no path-search result."
            state={state}
            onOpen={open}
        >
            {(pathSearch) => (!pathSearch ? <p className="empty-projection">The archive returned no path-search summary.</p> : (
                <KVList pairs={[
                    ["Method", statusLabel(pathSearch.method)],
                    ["Converged", boolLabel(pathSearch.converged)],
                    ["Stored points", pathSearch.stored_point_count],
                    ["TS guesses", pathSearch.ts_guess_count],
                    ["Climbing images", pathSearch.climbing_image_count],
                ]} />
            ))}
        </LazySection>
    )
}

function ArtifactsSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationArtifact[] | null>(calculationRef, "artifacts")
    return (
        <LazySection
            heading="Artifacts"
            available={available}
            notAvailableText="No artifact metadata is recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(rows) => (rows?.length ? (
                <table className="stage-table" aria-label="Calculation artifacts">
                    <thead>
                        <tr>
                            <th scope="col">Kind</th>
                            <th scope="col">Filename</th>
                            <th scope="col">Size</th>
                            <th scope="col">Artifact ref</th>
                            <th scope="col">SHA-256</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, index) => (
                            <tr key={`artifact-${index}`}>
                                <td data-label="Kind">{statusLabel(row.kind)}</td>
                                <td data-label="Filename">{row.filename ?? "not recorded"}</td>
                                <td data-label="Size">{row.bytes.toLocaleString()} bytes</td>
                                <td data-label="Artifact ref">{row.artifact_ref ?? "not recorded"}</td>
                                {/* The sha256 is the artifact's identity — the storage URI (row.uri)
                                    is not a downloadable link, so this is the one stable handle for
                                    the bytes this row describes. */}
                                <td data-label="SHA-256"><code>{row.sha256}</code></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            ) : <p className="empty-projection">The archive returned no artifact rows.</p>)}
        </LazySection>
    )
}

function ExecutionEnvironmentSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationExecutionEnvironment | null>(calculationRef, "execution_environment")
    return (
        <LazySection
            heading="Execution environment"
            available={available}
            notAvailableText="No revalidated execution-environment manifest is recorded for this calculation."
            state={state}
            onOpen={open}
        >
            {(env) => (!env ? <p className="empty-projection">The archive returned no manifest.</p> : (
                <KVList pairs={[
                    ["Environment ref", env.environment_ref],
                    ["Runtime kind", env.runtime?.runtime_kind ?? "not recorded"],
                    ["Executable", env.executable?.locator ?? "not recorded"],
                ]} />
            ))}
        </LazySection>
    )
}
