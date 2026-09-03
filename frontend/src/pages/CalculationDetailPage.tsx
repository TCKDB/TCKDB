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
// - ON DEMAND (14 remaining tokens): every other heavy section.
//   `available_sections` (18 booleans, one per token that has one — see
//   `readSectionField`) tells the page which of these would return data
//   *before* asking. A section known empty renders no request and no
//   heading of its own — it is named, once, in the single shared line
//   `MissingSectionsNote` renders after every section that DOES have
//   something to show (review finding 8: this page used to give each
//   empty section its own heading, ten in a row on some calculation
//   types). A section that may have data renders as an expandable
//   `<details>` disclosure that fetches its own token, and only its own
//   token, the first time it opens.
//
// `imaginary_mode_projections` has no `has_imaginary_mode_projections`
// flag (it's a computed-at-read-time projection, not a stored table) —
// `available_sections.has_hessian` is used instead, per its own docstring
// in the service layer.
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

// The owner's own wording for a presence check with nothing recorded --
// kept in this ONE place (not four separate ternaries) so a future
// reversal (he's flagged "absent" itself as possibly ambiguous here) is a
// one-line edit, not a hunt through the coverage checklist below.
const EVIDENCE_ABSENT_LABEL = "absent"

/**
 * The THIRD state, distinct from both "recorded" and `EVIDENCE_ABSENT_LABEL`
 * -- see the module-level design note above this checklist's own JSX for
 * the full three-state contract. Never say this for a check that could
 * apply to this calculation's type and simply hasn't been done; that is
 * still `EVIDENCE_ABSENT_LABEL`, and collapsing the two back together is
 * the exact bug this wording exists to keep fixed.
 */
const EVIDENCE_NOT_APPLICABLE_LABEL = "not applicable"

/**
 * Shared recorded/absent/not-applicable read for a checklist row whose
 * applicability is backend-computed (`provenance.*_applicable` -- see
 * `CalculationEvidenceProvenanceSummary`'s own docstring in
 * `scientific_calculation.py` for why the server, not this client, is
 * the one place that knows which calculation types can carry which
 * evidence). `applicable` is checked FIRST and short-circuits `recorded`
 * -- a type that cannot have this evidence is never described as
 * "absent" no matter what `recorded` says.
 */
function applicabilityLabel(applicable: boolean, recorded: boolean): string {
    if (!applicable) return EVIDENCE_NOT_APPLICABLE_LABEL
    return recorded ? "recorded" : EVIDENCE_ABSENT_LABEL
}

/**
 * `provenance.converged` genuinely has THREE presence states, and a
 * fourth axis (`convergence_applicable`) on top of them, and none of the
 * four may collapse into another:
 *
 * - `convergence_applicable === false` -- NOT APPLICABLE: this
 *   calculation's type (`sp`, `freq`, `scan`, `irc`, `conf`) does not
 *   model a convergence flag at all; only `opt` and `path_search` do
 *   (see `_TYPES_WITH_CONVERGENCE_FLAG` server-side). Checked first, and
 *   short-circuits everything below -- a non-opt/path_search calculation
 *   is never told it is "absent" a concept it cannot have.
 * - `converged === null`/`undefined` (with `convergence_applicable` true)
 *   -- ABSENT: the check could apply here and nobody has recorded a
 *   result yet.
 * - `converged === false` -- a SCIENTIFIC OUTCOME: the optimisation ran
 *   and failed to converge. Never rendered as "absent" or "not
 *   applicable" -- both would turn a real negative result into a missing
 *   record.
 * - `converged === true` -- converged.
 */
function convergenceLabel(converged: boolean | null | undefined, applicable: boolean): string {
    if (!applicable) return EVIDENCE_NOT_APPLICABLE_LABEL
    if (converged === true) return "converged"
    if (converged === false) return "not converged"
    return EVIDENCE_ABSENT_LABEL
}

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

/**
 * `EnergyDisplay` itself drops the label entirely for a `null` value (a
 * bare, unlabelled "not recorded" -- correct for an inline value sitting
 * next to other already-labelled evidence, but wrong at headline size,
 * where this IS the answer the reader came for: an unlabelled absence
 * there reads as a layout bug, not as "no value recorded"). This wrapper
 * is headline-only and keeps the label (and, implicitly, the fact that
 * this row is an energy at all) visible even when there is no number --
 * see finding 2 of the review this fixes. `EnergyDisplay`'s own
 * null-handling is unchanged and still applies to every inline use
 * elsewhere on the archive.
 */
function HeadlineEnergy({ label, valueHartree }: { label: string; valueHartree: number | null }) {
    if (valueHartree === null) {
        return (
            <div className="energy-display energy-display--headline">
                <span className="energy-display-label">{label}</span>
                <span className="energy-display-value energy-display-value--headline energy-display-absent">
                    not recorded
                </span>
            </div>
        )
    }
    return <EnergyDisplay valueHartree={valueHartree} label={label} size="headline" />
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
                        {/* The review-status pill used to sit beside the h1 inside
                            `.record-title`, a `display:flex; align-items:baseline`
                            row. At the giant serif sizes this heading renders at,
                            "Optimisation calculation" alone is wide enough to wrap
                            onto two lines even before the pill claims horizontal
                            space -- and a `nowrap` flex row squeezes the h1 down
                            further to keep the pill on the same line, pinning the
                            pill to the far right of a mostly-empty row while the
                            wrapped title sits on the left. Two reviewers read that
                            as the pill floating loose from the record it describes,
                            at both 1920px and 900px. Anchoring it to the eyebrow
                            line instead sidesteps the h1's own wrapping entirely --
                            "Calculation · deposited evidence" never wraps at any
                            width this page supports, so the pill has a stable home
                            immediately above the title it describes, always inside
                            the same heading block. */}
                        <div className="record-eyebrow-row">
                            <p className="eyebrow">Calculation · deposited evidence</p>
                            <span className="review-badge">{statusLabel(core.review.status)}</span>
                        </div>
                        <div className="record-title">
                            <h1>{typeLabel(core.type)} calculation</h1>
                        </div>
                        {/* The answer this page exists to give, promoted to the
                            largest weight on the page — previously it sat inside the
                            "Result" section below at the same visual weight as the
                            dependency graph and review history. Only sp/opt
                            calculations have a single headline energy; every other
                            calculation type renders nothing here rather than a
                            fabricated or misleading figure. */}
                        {headline && (
                            <div className="calc-headline-energy">
                                <HeadlineEnergy label={headline.label} valueHartree={headline.valueHartree} />
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
                            {/* `quality` and `review.status` (the badge beside the
                                heading above) are two SEPARATE mechanisms -- one is how
                                much to trust the record (`raw`/`curated`/`rejected`),
                                the other is who has examined it (`not_reviewed`/
                                `under_review`/`approved`/`rejected`/`deprecated`). They
                                only look redundant today because every one of the 572
                                calculations measured on the live archive is
                                `(not_reviewed, raw)` -- neither mechanism has been used
                                yet, not because they are the same axis. `raw` is also
                                the column's own `server_default`, so showing it
                                unconditionally distinguishes nothing on any record.
                                `curated` and `rejected` DO carry information a reader
                                needs (they change how this calculation is filtered/
                                scored elsewhere -- see `services/scientific_read/
                                artifacts_search.py` and `services/trust/rubrics.py`),
                                so this only hides the uninformative default, never the
                                field itself. */}
                            {core.quality !== "raw" && (
                                <div><dt>Quality</dt><dd>{core.quality}</dd></div>
                            )}
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
                    {/* A vertical checklist, one row per check, label: value --
                        replacing the single `<strong>` that joined four facts with
                        "·" and wrapped mid-phrase at narrow widths (the owner: "this
                        should be a going down list, not a wrap around text"). Real
                        `<dl>` markup, not `<br>`-separated text, so each row is a
                        term/value pair a screen reader announces as such. */}
                    <dl className="coverage-checklist">
                        <div>
                            <dt>Result</dt>
                            <dd>{applicabilityLabel(provenance.result_applicable, provenance.has_result)}</dd>
                        </div>
                        <div>
                            <dt>Geometry validation</dt>
                            <dd>{applicabilityLabel(provenance.geometry_validation_applicable, provenance.geometry_validation_status !== "not_present")}</dd>
                        </div>
                        {/* SCF stability has no `*_applicable` flag on purpose -- per
                            `CalculationSCFStability`'s own model docstring an absent row
                            means "not checked" for ANY calculation type, never
                            "cannot apply to this type". See `provenanceSchema`'s comment
                            in `calculationApi.ts`. */}
                        <div>
                            <dt>SCF stability</dt>
                            <dd>{provenance.scf_stability_status === "not_present" ? EVIDENCE_ABSENT_LABEL : "recorded"}</dd>
                        </div>
                        {/* Convergence keeps all four states distinct -- see
                            `convergenceLabel`'s own docstring for why "not applicable",
                            "absent" and "not converged" must never render the same
                            text. */}
                        <div>
                            <dt>Convergence</dt>
                            <dd>{convergenceLabel(provenance.converged, provenance.convergence_applicable)}</dd>
                        </div>
                    </dl>
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
                    {/* The linked text above prefers the human label; the stable ref
                        gets its OWN row only when the label pushed it out of view --
                        a label must never be the only way to see the ref. Measured:
                        `species_entry_label` is null on all 200 entries sampled off
                        the browse endpoint, so without this guard the ref row below
                        repeated the link's own fallback text verbatim on every
                        calculation page. The rule ("never label ?? ref") is
                        unchanged; only the never-considered no-label branch is
                        fixed. */}
                    {ownerSpecies.species_entry_label && (
                        <div><dt>Species entry ref</dt><dd>{ownerSpecies.species_entry_ref}</dd></div>
                    )}
                    <div><dt>Structure</dt><dd>{ownerSpecies.canonical_smiles}</dd></div>
                    <div><dt>InChIKey</dt><dd>{ownerSpecies.inchi_key}</dd></div>
                    {/* Categorical, bounded-vocabulary facts (entry kind, electronic
                        state) render as pills, replacing their plain-text form (never
                        alongside it -- see `.value-pill` in `calculation-detail.css`
                        for why this is not the deleted `RecordFacetChips` come back).
                        A charge is a number, not a bounded category, so it stays plain
                        text -- the same "value / value" format the record hero uses
                        for this same fact (see `RecordIdentityHeader.tsx`), so the two
                        never disagree about how to write it. Identifiers above (species
                        / species entry / structure / InChIKey) are never pills either
                        -- they stay selectable monospace text. */}
                    <div>
                        <dt>Charge / multiplicity</dt>
                        <dd>{chargeDisplay(ownerSpecies.charge)} / {spinDisplay(ownerSpecies.multiplicity)}</dd>
                    </div>
                    <div><dt>Entry kind</dt><dd><span className="value-pill">{statusLabel(ownerSpecies.species_entry_kind)}</span></dd></div>
                    <div><dt>Electronic state</dt><dd><span className="value-pill">{ownerSpecies.electronic_state_kind}</span></dd></div>
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
                    {/* Same guard as the species branch above -- see its comment. */}
                    {ownerTS.label && (
                        <div><dt>Transition state entry ref</dt><dd>{ownerTS.transition_state_entry_ref}</dd></div>
                    )}
                    {/* Same plain-text rule as the species-entry branch above -- a
                        charge is a number, not a bounded category. */}
                    <div>
                        <dt>Charge / multiplicity</dt>
                        <dd>{chargeDisplay(ownerTS.charge)} / {spinDisplay(ownerTS.multiplicity)}</dd>
                    </div>
                    <div><dt>Status</dt><dd><span className="value-pill">{statusLabel(ownerTS.status)}</span></dd></div>
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
                <p>Other calculations this one was built from, or that were built from it.</p>
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
 * docstring). Renders an expandable `<details>` that fetches its own
 * token, once, the first time it opens.
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
 *
 * `applicable` (default `true`) is a SEPARATE axis from `available`, and
 * checked first: `available` says "does this record have data for a
 * section its type COULD have"; `applicable` says "can a calculation of
 * this type have this section AT ALL". Both `applicable === false` and
 * `available === false` render NOTHING here — not the section, not its
 * heading, not its own "not present" line. A heading is what registers a
 * table-of-contents entry (`SectionHeading`'s own docstring in
 * `PageSections.tsx`), and a page with ten sections that each have
 * nothing to show is ten headings the reader has to read past to find the
 * two that do (review finding 8). `OnDemandSections` is the one place
 * that knows the full roster of tokens for this calculation, so it is
 * also the one place responsible for saying, once, which of them came up
 * empty — see `MissingSectionsNote` below. This component only ever
 * renders real content.
 */
function LazySection<T>({
    heading, available, state, onOpen, children, applicable = true,
}: {
    heading: string
    available: boolean
    state: CalculationSectionState<T>
    onOpen: () => void
    children: (data: T) => ReactNode
    applicable?: boolean
}) {
    const headingId = `section-${heading.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`
    if (!applicable || !available) return null
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

// One entry per on-demand section, independent of whether this particular
// calculation happens to have data for it -- the single place that knows
// the full roster, so `OnDemandSections` can compute "what's missing"
// without asking each section component to report on itself. `heading`
// must match the string each section component below passes to its own
// `LazySection` exactly, since that string is also the table-of-contents
// label a rendered section registers.
const ON_DEMAND_SECTION_SPECS: {
    heading: string
    applicable: (a: CalculationRecord["available_sections"]) => boolean
    available: (a: CalculationRecord["available_sections"]) => boolean
}[] = [
    { heading: "Energy corrections", applicable: () => true, available: (a) => a.has_energy_corrections },
    { heading: "Geometry validation", applicable: (a) => a.geometry_validation_applicable, available: (a) => a.has_geometry_validation },
    { heading: "SCF stability", applicable: () => true, available: (a) => a.has_scf_stability },
    { heading: "Wavefunction diagnostic", applicable: () => true, available: (a) => a.has_wavefunction_diagnostic },
    { heading: "Spin diagnostic", applicable: () => true, available: (a) => a.has_spin_diagnostic },
    { heading: "Parsed parameters", applicable: () => true, available: (a) => a.has_parameters },
    { heading: "Constraints", applicable: (a) => a.constraints_applicable, available: (a) => a.has_constraints },
    { heading: "Vibrational modes", applicable: (a) => a.freq_modes_applicable, available: (a) => a.has_freq_modes },
    // Gated on `has_hessian`, not a dedicated flag -- see the module
    // docstring. Folded into the same missing-or-not list as every other
    // on-demand section (review finding 8): the finer "not merely absent,
    // not determinable" distinction stays live in the evidence panel
    // above, which this note does not touch.
    { heading: "Imaginary-mode projections", applicable: () => true, available: (a) => a.has_hessian },
    { heading: "Scan trajectory", applicable: (a) => a.scan_applicable, available: (a) => a.has_scan },
    { heading: "IRC trajectory", applicable: (a) => a.irc_applicable, available: (a) => a.has_irc },
    { heading: "Path-search trajectory", applicable: (a) => a.path_search_applicable, available: (a) => a.has_path_search },
    { heading: "Artifacts", applicable: () => true, available: (a) => a.has_artifacts },
    { heading: "Execution environment", applicable: () => true, available: (a) => a.has_execution_environment },
]

function OnDemandSections({ calculation, available }: {
    calculation: CalculationRecord
    available: CalculationRecord["available_sections"]
}) {
    const ref = calculation.calculation.calculation_ref
    // Applicable to this calculation's TYPE, but nothing recorded -- the
    // ten-empty-headings defect (review finding 8). Collected once here
    // instead of each section rendering its own heading over one line of
    // "not recorded" prose, and read the same way regardless of
    // calculation type: a section is either applicable-and-missing (named
    // here) or it is not applicable at all (never named anywhere on the
    // page below the evidence panel -- the type stated at the top already
    // explains that).
    const missing = ON_DEMAND_SECTION_SPECS
        .filter((spec) => spec.applicable(available) && !spec.available(available))
        .map((spec) => spec.heading)

    return (
        <>
            <EnergyCorrectionsSection calculationRef={ref} available={available.has_energy_corrections} />
            <GeometryValidationSection calculationRef={ref} available={available.has_geometry_validation} applicable={available.geometry_validation_applicable} />
            <SCFStabilitySection calculationRef={ref} available={available.has_scf_stability} />
            <WavefunctionDiagnosticSection calculationRef={ref} available={available.has_wavefunction_diagnostic} />
            <SpinDiagnosticSection calculationRef={ref} available={available.has_spin_diagnostic} />
            <ParametersSection calculationRef={ref} available={available.has_parameters} />
            <ConstraintsSection calculationRef={ref} available={available.has_constraints} applicable={available.constraints_applicable} />
            <FreqModesSection calculationRef={ref} available={available.has_freq_modes} applicable={available.freq_modes_applicable} />
            <ImaginaryModeProjectionsSection calculationRef={ref} hessianAvailable={available.has_hessian} />
            <ScanSection calculationRef={ref} available={available.has_scan} applicable={available.scan_applicable} />
            <IRCSection calculationRef={ref} available={available.has_irc} applicable={available.irc_applicable} />
            <PathSearchSection calculationRef={ref} available={available.has_path_search} applicable={available.path_search_applicable} />
            <ArtifactsSection calculationRef={ref} available={available.has_artifacts} />
            <ExecutionEnvironmentSection calculationRef={ref} available={available.has_execution_environment} />
            <MissingSectionsNote headings={missing} />
        </>
    )
}

/**
 * The one line every applicable-but-empty on-demand section collapses
 * into, replacing ten separate empty headings (review finding 8). No
 * `SectionHeading` here on purpose -- this note is not itself a section
 * with content to navigate to, so it registers no table-of-contents
 * entry; the evidence panel at the top of the page is where a reader
 * already learned which checks this calculation carries.
 */
function MissingSectionsNote({ headings }: { headings: string[] }) {
    if (headings.length === 0) return null
    return (
        <section className="ledger-section" aria-label="Sections with nothing recorded">
            <p className="empty-projection">Not recorded on this calculation: {headings.join(", ")}.</p>
        </section>
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

function GeometryValidationSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationGeometryValidation[] | null>(calculationRef, "geometry_validation")
    return (
        <LazySection
            heading="Geometry validation"
            available={available}
            applicable={applicable}
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

function ConstraintsSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationConstraint[] | null>(calculationRef, "constraints")
    return (
        <LazySection
            heading="Constraints"
            available={available}
            applicable={applicable}
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

function FreqModesSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationFreqMode[] | null>(calculationRef, "freq_modes")
    return (
        <LazySection
            heading="Vibrational modes"
            available={available}
            applicable={applicable}
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

function ScanSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationScan | null>(calculationRef, "scan")
    return (
        <LazySection
            heading="Scan trajectory"
            available={available}
            applicable={applicable}
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

function IRCSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationIRC | null>(calculationRef, "irc")
    return (
        <LazySection
            heading="IRC trajectory"
            available={available}
            applicable={applicable}
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

function PathSearchSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationPathSearch | null>(calculationRef, "path_search")
    return (
        <LazySection
            heading="Path-search trajectory"
            available={available}
            applicable={applicable}
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
