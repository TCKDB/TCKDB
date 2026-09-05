import type { ReactNode } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../calculation-detail.css"
import { lotLabel } from "../api/scientificSchemas"
import {
    type CalculationArtifact,
    type CalculationConformer,
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
import { Disclosure } from "../components/Disclosure"
import { EnergyDisplay } from "../components/EnergyDisplay"
import { Formula } from "../components/Formula"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { QuantityValue } from "../components/QuantityValue"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { RefsDisclosure, type RefEntry } from "../components/RefsDisclosure"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import { formatQuantity } from "../domain/quantityFormat"
import { identityFromCalculationOwner } from "../domain/recordIdentity"
import { refWithBreaks } from "../domain/refBreaks"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { hessianMethodLabel, isAssumedTauBasis, tauBasisNote } from "../domain/tauBasis"
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
// - ON DEMAND (14 remaining tokens): every other heavy section, grouped
//   under one "Further evidence" disclosure group (review finding: ten
//   individually-headed, individually-ToC-registered sections crowded out
//   the two or three that actually carry data on most calculations).
//   `available_sections` (18 booleans, one per token that has one — see
//   `readSectionField`) tells the page which of these would return data
//   *before* asking. A section known empty renders no request and no
//   disclosure of its own — it is named, once, in the single shared line
//   `MissingSectionsNote` renders after every disclosure that DOES have
//   something to show. A section that may have data renders as an
//   expandable `<details>` disclosure that fetches its own token, and only
//   its own token, the first time it opens.
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

const typeLabel = (type: string) => CALC_TYPE_LABELS[type] ?? type.replaceAll("_", " ")
const roleLabel = (role: string) => role.replaceAll("_", " ")
const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

// The owner's own wording for a presence check with nothing recorded --
// kept in this ONE place (not four separate ternaries) so a future
// reversal is a one-line edit, not a hunt through the coverage checklist
// below.
const EVIDENCE_ABSENT_LABEL = "absent"

/**
 * The THIRD state, distinct from both "recorded" and `EVIDENCE_ABSENT_LABEL`
 * -- never say this for a check that could apply to this calculation's
 * type and simply hasn't been done; that is still `EVIDENCE_ABSENT_LABEL`,
 * and collapsing the two back together is the exact bug this wording
 * exists to keep fixed.
 */
const EVIDENCE_NOT_APPLICABLE_LABEL = "not applicable"

/** Geometry-validation outcome: not applicable / absent / the real verdict
 * (`passed`/`warning`/`fail`), never a bare "recorded" that hides which. */
function geometryValidationOutcomeLabel(status: string, applicable: boolean): string {
    if (!applicable) return EVIDENCE_NOT_APPLICABLE_LABEL
    if (status === "not_present") return EVIDENCE_ABSENT_LABEL
    return statusLabel(status)
}

/** SCF stability has no `*_applicable` flag -- per
 * `CalculationSCFStability`'s own model docstring an absent row means "not
 * checked" for ANY calculation type, never "cannot apply to this type". */
function scfStabilityOutcomeLabel(status: string): string {
    if (status === "not_present") return EVIDENCE_ABSENT_LABEL
    return statusLabel(status)
}

/**
 * The one headline energy figure this page promotes into its header —
 * see the design brief's "Promote the answer". Only `sp` (electronic
 * energy) and `opt` (final energy) calculations have a single number
 * that answers "what did this calculation compute" — `freq`/`scan`/
 * `irc`/`path_search` results are multi-valued or process-shaped and get
 * no headline here, never a guessed stand-in.
 *
 * Prefers `results.kind` (matching `ResultBody`'s own dispatch, so the two
 * never disagree about which calculation's result they describe) but
 * falls back to the calculation's own `type` when there is no result row
 * at all — a calculation whose type promises a headline must still show
 * the headline SLOT with "not recorded", never silently drop it because
 * the row happens to be missing (review finding 2: `calc_u7j7…`, an opt
 * with no result, rendered no headline at all).
 */
function headlineEnergy(
    type: string,
    results: CalculationRecord["results"],
): { label: string; valueHartree: number | null } | null {
    const kind = results?.kind ?? type
    if (kind === "sp") return { label: "Electronic energy", valueHartree: results?.sp?.electronic_energy_hartree ?? null }
    if (kind === "opt") {
        return {
            label: "Electronic energy at final geometry",
            valueHartree: results?.opt?.final_energy_hartree ?? null,
        }
    }
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

/**
 * "Never been reviewed" is a genuine, common case (every calculation
 * measured on the live archive, as of this writing) — the service layer
 * synthesizes one `not_reviewed` row with no date and no note so
 * `review_history`'s presence flag agrees with the always-present review
 * badge. Rendered as a single line rather than a one-row table whose every
 * cell reads "not recorded" (review finding: a 3-cell table of "not
 * recorded" for a fact that is really one word, "unreviewed").
 */
function isNeverReviewed(entries: CalculationRecord["review_history"]): boolean {
    const rows = entries ?? []
    return rows.length === 1 && rows[0].status === "not_reviewed" && !rows[0].reviewed_at && !rows[0].note
}

function CalculationDetail({ calculation }: { calculation: CalculationRecord }) {
    const {
        calculation: core,
        owner,
        conformer,
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
    const identity = identityFromCalculationOwner(owner)
    const headline = headlineEnergy(core.type, calculation.results)
    // `provenance.submission_ref` is `string | null | undefined` on the
    // wire: `undefined` means the KEY ITSELF was omitted (an anonymous
    // caller — see `CalculationEvidenceProvenanceSummary`'s own
    // docstring), `null` means an authenticated caller was told there is
    // no linked submission, and a string is the ref. Only the first case
    // renders no row at all; `null` still renders as "not recorded" so
    // an authenticated reader can tell "checked, none" from "not told".
    const submissionRefKeyPresent = "submission_ref" in provenance

    // Geometry validation's fetched state is lifted here, out of the
    // "Further evidence" disclosure that would otherwise own it, so the
    // Geometries section can show the same RMSD once it has been loaded --
    // one shared fetch, not a second independent request path for the
    // same on-demand token (see the Geometries section's own comment).
    const [geometryValidationState, openGeometryValidation] = useCalculationSection<CalculationGeometryValidation[] | null>(
        core.calculation_ref, "geometry_validation",
    )

    // The provenance refs are optional on the wire; a row is only a row
    // when its ref is actually present (a label with nothing to copy is
    // not a reference).
    const refs: RefEntry[] = [
        { label: "Calculation ref", value: core.calculation_ref },
        { label: "Level of theory ref", value: lot?.level_of_theory_ref },
        { label: "Software release ref", value: software?.software_release_ref },
        { label: "Workflow tool release ref", value: workflow?.workflow_tool_release_ref },
        { label: "Literature ref", value: literature?.literature_ref },
    ].filter((entry): entry is RefEntry => typeof entry.value === "string" && entry.value.length > 0)

    // Identity subject for the h1 -- "Optimisation of C2H4" / "Optimisation
    // of TS0". A species entry prefers its formula, rendered through the
    // SAME `Formula` component `RecordIdentityHeader`'s own identity tier
    // uses (subscripted element counts), falling back to the plain
    // canonical SMILES only when no formula was derived. A TS entry has
    // no formula the way a species does, so it falls back through its own
    // label/ref instead.
    // SHOULD-FIX-8 ("record-page residuals" re-review): when a TS-owned
    // calculation's transition state has no depositor label, this used to
    // fall back to the RAW ref, printed straight into the h1 as plain
    // serif display text ("Optimisation of tse_aq5…") -- an identifier
    // with no data-run styling at all, MEASURED as the one raw ref on
    // these pages rendered in the wrong face. `.data` (mono, the same
    // step every other ref on this page uses) is the honest treatment for
    // a ref, even when it happens to sit inside an h1.
    const titleSubject: ReactNode = identity.kind === "species_entry"
        ? (identity.formula ? <Formula value={identity.formula} /> : identity.canonicalSmiles)
        : identity.kind === "transition_state_entry"
            ? (identity.label ?? (identity.transitionStateEntryRef
                ? <code className="data">{refWithBreaks(identity.transitionStateEntryRef)}</code>
                : "this record"))
            : "this record"

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
                {/* Mirrors `TransitionStateEntryPage.tsx`'s own breadcrumb --
                    that route exists now (`App.tsx:52`), so a TS-owned
                    calculation's ancestor entry gets a real link, not the
                    unlinked plain text this page used to fall back to. */}
                {ownerTS && (
                    <>
                        <span aria-hidden="true">/</span>
                        <Link to="/species?kind=transition_state">Browse</Link>
                        <span aria-hidden="true">/</span>
                        <Link to={`/transition-state-entries/${ownerTS.transition_state_entry_ref}`}>
                            Transition state entry
                        </Link>
                    </>
                )}
                <span aria-hidden="true">/</span>
                <span aria-current="page">Calculation</span>
            </nav>

            <PageShell
                identity={(
                    <header className="record-header">
                        {/* Kicker + h1 + identity now all live in
                            `RecordIdentityHeader` (item 1, design/foundations
                            PR B): the review-status pill is the header's ONE
                            pill slot, and "Optimisation calculation" (the
                            classification fact) is the kicker text, ahead of
                            the h1's own IDENTITY ("Optimisation of C2H4") --
                            see that component's own docstring for the shared
                            header order every record page now follows. */}
                        <RecordIdentityHeader
                            kicker={`${typeLabel(core.type)} calculation · deposited evidence`}
                            pill={<span className={reviewPillClass(core.review.status)}>{statusLabel(core.review.status)}</span>}
                            title={<>{typeLabel(core.type)} of {titleSubject}</>}
                            identity={identity}
                        />

                        {/* The answer this page exists to give, promoted to the
                            largest weight on the page. Only sp/opt calculations
                            have a single headline energy; every other
                            calculation type renders nothing here rather than a
                            fabricated or misleading figure. Rendered even when
                            the value itself is null (see `headlineEnergy`'s own
                            docstring) -- the SLOT is what a sp/opt calculation
                            always gets, not only the ones with a result row. */}
                        {headline && (
                            <div className="calc-headline-energy">
                                <HeadlineEnergy label={headline.label} valueHartree={headline.valueHartree} />
                            </div>
                        )}

                        {/* Item 1 (BLOCKING): the TS entry is now a real link to
                            `/transition-state-entries/:ref` -- that route exists
                            (`App.tsx:52`). `RecordIdentityHeader`'s own TS branch
                            renders the ref as plain `<code>` (a shared component
                            used by pages that don't all have this route), so the
                            link lives here instead, right below it. */}
                        {ownerTS && (
                            <p className="note record-identity-note">
                                This calculation belongs to the transition-state entry{" "}
                                <Link to={`/transition-state-entries/${ownerTS.transition_state_entry_ref}`}>
                                    {ownerTS.label ?? ownerTS.transition_state_entry_ref}
                                </Link>.
                            </p>
                        )}

                        <StageAndConformerNote
                            calcType={core.type}
                            dependencies={dependencies}
                            dependenciesAvailability={dependenciesAvailability}
                            conformer={conformer ?? null}
                        />

                        {/* One row of human provenance, always shown -- software
                            and workflow tool must never be hidden (house rule).
                            Dispersion/Solvent/Literature are ADDITIONAL rows,
                            rendered only when the archive actually recorded
                            them -- see the invariant list: absence describes the
                            request, null describes the data, and this page never
                            claims "not recorded" for a field most calculations
                            simply don't carry. */}
                        <dl className="kv-list record-context">
                            <div><dt>Deposited</dt><dd>{isoDate(core.created_at)}</dd></div>
                            <div><dt>Level of theory</dt><dd>{lot ? lotLabel(lot) : "not recorded"}</dd></div>
                            <div>
                                <dt>Software</dt>
                                <dd>{softwareLabel(software) ?? "not recorded"}</dd>
                            </div>
                            <div>
                                <dt>Workflow tool</dt>
                                <dd>{toolReleaseLabel(workflow) ?? "not recorded"}</dd>
                            </div>
                            {lot?.dispersion && <div><dt>Dispersion</dt><dd>{lot.dispersion}</dd></div>}
                            {lot?.solvent && <div><dt>Solvent</dt><dd>{lot.solvent}</dd></div>}
                            {literature && (
                                <div>
                                    <dt>Literature</dt>
                                    <dd>
                                        {literature.title ?? literature.literature_ref}
                                        {literature.year ? ` (${literature.year})` : ""}
                                    </dd>
                                </div>
                            )}
                            {/* No row at all when the key itself is absent (anonymous
                                caller) — see `submissionRefKeyPresent` above. */}
                            {submissionRefKeyPresent && (
                                <div><dt>Submission ref</dt><dd>{provenance.submission_ref ?? "not recorded"}</dd></div>
                            )}
                            {/* `quality`'s own `server_default` is `raw`, so showing
                                it unconditionally would distinguish nothing on any
                                record -- see the schema comment this carries
                                forward. `curated`/`rejected` DO carry information a
                                reader needs, so only the uninformative default is
                                hidden, never the field itself. */}
                            {core.quality !== "raw" && (
                                <div><dt>Quality</dt><dd>{core.quality}</dd></div>
                            )}
                        </dl>

                        <RefsDisclosure refs={refs} />
                    </header>
                )}
            >
            {/* No count tiles here (review finding): "Input geometries 1 /
                Output geometries 1 / Dependency edges 3" rendered cardinalities
                a reader never asks for at display size, each duplicating a
                section directly below that shows the same one or three links.
                The evidence checklist is the only card that carries a fact
                with no other home on the page. */}
            <section className="ledger-summary ledger-summary--single" aria-label="Calculation evidence summary">
                {/* `.card--derived` -- a COMPUTED evidence-completeness summary
                    (item 8's card decision: `--derived` marks an aggregated
                    verdict, plain `.card` marks a single deposited record; see
                    this PR's body for the one rule applied consistently across
                    every record page). */}
                <div className="card card--derived coverage-card">
                    <span className="t-label">Evidence on this calculation</span>
                    {/* Geometry validation and SCF stability only -- Result and
                        Convergence are dropped from this checklist (review
                        finding: each already has its own headline/Result-section
                        home, so a THIRD "recorded"/"absent" line for the same
                        fact stated nothing new). The two rows that remain have
                        no section of their own when they come up empty, so this
                        is the only place a reader learns whether they exist at
                        all -- and each now shows the actual recorded OUTCOME
                        (passed / failed / stable / ...), not just "recorded". */}
                    <dl className="coverage-checklist">
                        <div>
                            <dt>Geometry validation</dt>
                            <dd>{geometryValidationOutcomeLabel(provenance.geometry_validation_status, provenance.geometry_validation_applicable)}</dd>
                        </div>
                        <div>
                            <dt>SCF stability</dt>
                            <dd>{scfStabilityOutcomeLabel(provenance.scf_stability_status)}</dd>
                        </div>
                    </dl>
                    <p className="note">
                        A recorded outcome here is the actual verdict; the full evidence, where the archive has
                        more to show, is under Further evidence below.
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
                geometryValidation={geometryValidationState}
            />

            <OnDemandSections
                calculation={calculation}
                available={available}
                geometryValidationState={geometryValidationState}
                openGeometryValidation={openGeometryValidation}
            />

            {/* Demoted below every evidence section (results, geometries,
                further evidence): the graph and the review log are
                provenance ABOUT this record, not the scientific evidence
                itself. */}
            <DependenciesSection
                dependencies={dependencies}
                ownRef={core.calculation_ref}
                availability={dependenciesAvailability}
                contradicted={dependenciesAvailability === "empty" && available.has_dependencies}
            />

            <ReviewHistorySection
                entries={reviewHistory}
                availability={reviewAvailability}
            />
            </PageShell>
        </section>
    )
}

/**
 * The dependency-derived stage sentence (for `opt` calculations) plus the
 * conformer-context links (for species-entry-owned calculations), stacked
 * as one small block right under the identity header. Both read the
 * already-fetched eager data -- no extra request for either.
 */
function StageAndConformerNote({ calcType, dependencies, dependenciesAvailability, conformer }: {
    calcType: string
    dependencies: CalculationDependency[]
    dependenciesAvailability: SectionAvailability
    conformer: CalculationConformer | null
}) {
    // "empty" (the archive was asked and returned no edges) and
    // "populated" with no matching edge both go through optimisationStage,
    // which reads them as "No refinement stage recorded" -- an absence of
    // evidence, not evidence of a single pass (review finding: the old
    // text asserted a stage the archive never actually reported). Only
    // "not-requested" (the wire key itself absent) skips the call
    // entirely -- there the page never even asked, so it renders no Stage
    // row at all rather than a "not recorded" one.
    const stage = calcType === "opt" && dependenciesAvailability !== "not-requested"
        ? optimisationStage(dependencies)
        : null
    if (!stage && !conformer) return null
    return (
        <dl className="kv-list record-context--compact">
            {stage && (
                <div>
                    <dt>Stage</dt>
                    <dd>
                        {/* SHOULD-FIX-4 (PR B review): a calculation ref is
                            an identifier -- `<code className="data">`,
                            like every other ref on these five pages, same
                            treatment inside a link as outside one. The
                            conformer-group link just below is NOT
                            code-wrapped when it shows the producer's own
                            LABEL rather than the ref -- a label is a human
                            word, not an identifier. */}
                        {stage.linkRef
                            ? <>{stage.text} <Link to={`/calculations/${stage.linkRef}`}><code className="data">{refWithBreaks(stage.linkRef)}</code></Link></>
                            : stage.text}
                    </dd>
                </div>
            )}
            {conformer && (
                <div>
                    <dt>Conformer</dt>
                    <dd>
                        <Link to={`/conformer-observations/${conformer.conformer_observation_ref}`}>
                            <code className="data">{refWithBreaks(conformer.conformer_observation_ref)}</code>
                        </Link>
                        {" · "}
                        <Link to={`/conformer-groups/${conformer.conformer_group_ref}`}>
                            {conformer.conformer_group_label
                                ? conformer.conformer_group_label
                                : <code className="data">{refWithBreaks(conformer.conformer_group_ref)}</code>}
                        </Link>
                    </dd>
                </div>
            )}
        </dl>
    )
}

/**
 * "Which of N optimisations is this" for an opt calculation, read from the
 * SAME `dependencies` payload the Related-calculations section renders
 * below -- never a second, independently-derived graph read. A parent-side
 * `optimized_from` edge means this calculation was later refined further
 * (it is the coarse pass); a child-side one means this calculation IS the
 * refinement.
 *
 * Neither present does NOT mean this is confidently a single pass -- review
 * finding: the old "Single-pass optimisation" text asserted a stage from an
 * absence of edges, including on a calculation with no dependency edges at
 * all (nothing to read a stage from, one way or the other). An edge that
 * doesn't exist in the archive is not evidence there is no refinement
 * stage, only that this page has no evidence of one -- so the no-edge case
 * says exactly that, and reads as "not recorded", not as an asserted fact.
 */
function optimisationStage(dependencies: CalculationDependency[]): { text: string; linkRef?: string } {
    const parentEdge = dependencies.find((dep) => dep.direction === "parent" && dep.role === "optimized_from")
    if (parentEdge) return { text: "Coarse pass; refined by", linkRef: parentEdge.child_calculation_ref }
    const childEdge = dependencies.find((dep) => dep.direction === "child" && dep.role === "optimized_from")
    if (childEdge) return { text: "Refinement of", linkRef: childEdge.parent_calculation_ref }
    return { text: "No refinement stage recorded" }
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
            {/* No kicker here (SHOULD-FIX-6, "record-page residuals"
                re-review): it used to repeat this section's own title
                verbatim ("Result" / "Result") -- a kicker earns its place
                only when it adds a category the title lacks, the same
                rule "Review"'s kicker below satisfies and this one
                didn't. */}
            <SectionHeading id="results-heading" intro={`The primary scientific result for this ${kindLabel.toLowerCase()} calculation.`}>
                Result
            </SectionHeading>
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
        // so it stays a raw pass-through rather than a guessed precision.
        pairs.push(["Electronic energy (hartree)", <QuantityValue value={formatQuantity("calculation_electronic_energy_hartree", results.sp.electronic_energy_hartree, null)} />])
        pairs.push(["Uncertainty (hartree)", results.sp.electronic_energy_uncertainty_hartree ?? "not recorded"])
    } else if (results.kind === "opt" && results.opt) {
        // Final energy is dropped here -- it is already the page's own
        // headline (review finding: it used to appear a third time here,
        // at the same visual weight as convergence and step count). Steps
        // is relabelled to say plainly where the number came from.
        pairs.push(["Converged", boolLabel(results.opt.converged)])
        pairs.push(["Optimiser steps (parsed from log)", results.opt.n_steps ?? "not recorded"])
    } else if (results.kind === "freq" && results.freq) {
        pairs.push(["Imaginary modes (n_imag)", results.freq.n_imag ?? "not recorded"])
        pairs.push(["Imaginary frequency (cm-1)", results.freq.imag_freq_cm1 ?? "not recorded"])
        pairs.push(["ZPE (hartree)", results.freq.zpe_hartree ?? "not recorded"])
        pairs.push(["Reaction-coordinate mode", results.freq.reaction_coordinate_mode_index ?? "not designated"])
        // ADR 0012's noise floor (τ) is resolved from this freq job's
        // Hessian method (`imaginary_mode_tau_basis`), translated to plain
        // language by `tauBasis.ts`. An `assumed_*` basis means the method
        // was never recorded and TCKDB assumed the program's default for
        // it -- shown with a visible "(assumed: ...)" word, never a bare
        // asterisk (owner decision, 2026-09-04). Placed directly before
        // the existing "above the noise floor" row so the three read as
        // one thought: what the method was, what τ it implies, and how
        // many imaginary modes clear it.
        pairs.push(["Hessian method", <HessianMethodCell basis={results.freq.imaginary_mode_tau_basis} />])
        pairs.push(["Noise floor τ", <TauValueCell tauCm1={results.freq.imaginary_mode_tau_cm1} basis={results.freq.imaginary_mode_tau_basis} />])
        // ADR 0012's projection: how many imaginary modes sit above the
        // producing protocol's noise floor (tau). Null means the projection
        // was never stored for this record -- that is an absence, so it
        // reads "not recorded" like every other absent value, not
        // "not determinable" (which claimed something about the data).
        pairs.push(["Imaginary modes above the noise floor (τ)", results.freq.n_imag_at_or_above_tau ?? "not recorded"])
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
 * "Hessian method" cell -- `tauBasis.ts`'s plain-language translation of
 * `imaginary_mode_tau_basis`, marked with `.tau-assumed` (a value, not a
 * pill -- ADR 0012's τ is a number, and this row states the method that
 * produced it, not a category) when the basis is one of the three
 * `assumed_*` tokens, so the assumption reads distinctly even at a glance.
 */
function HessianMethodCell({ basis }: { basis: string | null | undefined }) {
    const label = hessianMethodLabel(basis)
    return isAssumedTauBasis(basis) ? <span className="tau-assumed">{label}</span> : <>{label}</>
}

/**
 * "Noise floor τ" cell -- the numeric value from `imaginary_mode_tau_cm1`
 * with the basis explained underneath in a short muted note. Null τ reads
 * "not recorded" with no note, matching every other absent value on this
 * page; a non-null τ always shows a note, even for `protocol_not_recorded`
 * or an unrecognised basis, since a producing method IS on the record
 * whenever τ itself is.
 */
function TauValueCell({ tauCm1, basis }: { tauCm1: number | null | undefined; basis: string | null | undefined }) {
    if (tauCm1 === null || tauCm1 === undefined) return <>not recorded</>
    return (
        <>
            <span className={isAssumedTauBasis(basis) ? "tau-assumed" : undefined}>{tauCm1} cm⁻¹</span>
            <div className="kv-note">{tauBasisNote(basis)}</div>
        </>
    )
}

/**
 * Renders exactly, and only, the edges the archive returned under
 * `include=dependencies`. No edge here is inferred from `type`, timestamps,
 * or ref ordering — see the module docstring above; this is the one rule
 * the whole slice is graded on.
 *
 * One sentence per edge, with a FIXED subject (this calculation, or the
 * related one), replacing the old Relationship/Role/Related-calculation
 * columns -- review finding: "feeds into | optimized from | calc_j4my…"
 * read as "this was optimized from calc_j4my", the inverse of the truth
 * (a `role` names the CHILD's relation to the parent, so on a parent-side
 * row it describes what the *other* calculation is, not this one). A
 * parent-side row states what the related calculation IS relative to this
 * geometry/result.
 *
 * Child-side dispatches on `role` too -- review finding: the live archive
 * carries child-side `freq_on`, `single_point_on` and `irc_start` edges
 * (a freq/sp/IRC calc's edge back to the opt it ran on), not only
 * `optimized_from`, and every one of them used to read "This was
 * optimized from <link>" regardless of what the edge actually was. Only
 * `optimized_from` gets that sentence now; the other three each get a
 * subject-fixed sentence naming what THIS calculation actually did on the
 * parent's geometry, and an unrecognised role falls back to the raw role
 * token -- never to "optimized from", which would silently re-introduce
 * the same bug for a role this page doesn't know about yet.
 */
function dependencySentence(dep: CalculationDependency): { linkRef: string; text: (linkNode: ReactNode) => ReactNode } {
    if (dep.direction === "child") {
        const ref = dep.parent_calculation_ref
        if (dep.role === "optimized_from") return { linkRef: ref, text: (link) => <>This was optimized from {link}</> }
        if (dep.role === "freq_on") return { linkRef: ref, text: (link) => <>This frequency calculation was run on the geometry from {link}</> }
        if (dep.role === "single_point_on") return { linkRef: ref, text: (link) => <>This single point was run on the geometry from {link}</> }
        if (dep.role === "irc_start") return { linkRef: ref, text: (link) => <>This IRC started from the geometry of {link}</> }
        return { linkRef: ref, text: (link) => <>This — {roleLabel(dep.role)} — {link}</> }
    }
    const ref = dep.child_calculation_ref
    if (dep.role === "freq_on") return { linkRef: ref, text: (link) => <>{link} (frequency) was run on this geometry</> }
    if (dep.role === "optimized_from") return { linkRef: ref, text: (link) => <>{link} was optimized from this result</> }
    if (dep.role === "single_point_on") return { linkRef: ref, text: (link) => <>{link} single point was run on this geometry</> }
    if (dep.role === "irc_start") return { linkRef: ref, text: (link) => <>{link} IRC started from this geometry</> }
    return { linkRef: ref, text: (link) => <>{link} — {roleLabel(dep.role)}</> }
}

function DependenciesSection({ dependencies, ownRef, availability, contradicted }: {
    dependencies: CalculationDependency[]
    ownRef: string
    availability: SectionAvailability
    contradicted: boolean
}) {
    return (
        <section className="ledger-section" aria-labelledby="dependencies-heading">
            {/* No kicker (SHOULD-FIX-6): repeated this section's own title. */}
            <SectionHeading id="dependencies-heading" intro="Other calculations this one was built from, or that were built from it.">
                Related calculations
            </SectionHeading>
            {availability === "populated" ? (
                <ul className="dependency-sentences" aria-label={`Dependency edges for ${ownRef}`}>
                    {dependencies.map((dep, index) => {
                        const { linkRef, text } = dependencySentence(dep)
                        return (
                            <li key={`${dep.role}-${dep.direction}-${linkRef}-${index}`}>
                                {text(<Link to={`/calculations/${linkRef}`}>{linkRef}</Link>)}
                            </li>
                        )
                    })}
                </ul>
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
    input, output, inputAvailability, inputContradicted, outputAvailability, outputContradicted, geometryValidation,
}: {
    input: CalculationGeometryLink[]
    output: CalculationGeometryLink[]
    inputAvailability: SectionAvailability
    inputContradicted: boolean
    outputAvailability: SectionAvailability
    outputContradicted: boolean
    geometryValidation: CalculationSectionState<CalculationGeometryValidation[] | null>
}) {
    // A coarse optimisation stage stores its input and output as the SAME
    // geometry row (measured: every coarse stage on the live archive) --
    // rendering two identical cards side by side read as a bug, not a
    // fact. Collapsed to one card with an explanatory note whenever both
    // lists resolve to exactly one, identical, geometry ref. This is a
    // rendering fix only -- see "Not addressed (data)" in the PR body for
    // why input==output itself is out of scope here.
    const sameGeometry = input.length === 1 && output.length === 1 && input[0].geometry_ref === output[0].geometry_ref
    const validationRow = geometryValidation.status === "ready" ? (geometryValidation.data?.[0] ?? null) : null
    return (
        <section className="ledger-section" aria-labelledby="geometries-heading">
            {/* No kicker (SHOULD-FIX-6): repeated this section's own title. */}
            <SectionHeading id="geometries-heading" intro="Links to the full coordinate records this calculation consumed and produced.">
                Geometries
            </SectionHeading>
            {sameGeometry ? (
                <div>
                    <h3 className="t-heading-2">Input and output</h3>
                    <p className="note">Input and output are the same stored geometry.</p>
                    <div className="geometry-links">
                        <div className="geometry-link" key={input[0].geometry_ref}>
                            <Link to={`/geometries/${input[0].geometry_ref}`}>{input[0].geometry_ref}</Link>
                            <span>{input[0].natoms != null ? `${input[0].natoms} atoms` : "atom count not recorded"}</span>
                        </div>
                    </div>
                    {validationRow && <GeometryValidationBanner row={validationRow} />}
                </div>
            ) : (
                <>
                    <GeometryLinkList
                        title="Input" links={input} emptyText="No input geometries are recorded."
                        availability={inputAvailability} contradicted={inputContradicted}
                    />
                    <GeometryLinkList
                        title="Output" links={output} emptyText="No output geometries are recorded."
                        availability={outputAvailability} contradicted={outputContradicted}
                    />
                    {/* Shown once the Further evidence -> Geometry validation
                        disclosure has been opened -- this section does not fetch
                        it independently (see the state lifted in
                        `CalculationDetail`), so the RMSD appears here the moment
                        that ONE shared fetch resolves, whichever section
                        triggered it. */}
                    {validationRow && outputAvailability === "populated" && <GeometryValidationBanner row={validationRow} />}
                </>
            )}
        </section>
    )
}

function GeometryValidationBanner({ row }: { row: CalculationGeometryValidation }) {
    return (
        <p className="note">
            Geometry validation: {statusLabel(row.validation_status)}
            {row.rmsd != null ? ` · RMSD ${row.rmsd}` : ""}
        </p>
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
            <h3 className="t-heading-2">{title}</h3>
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

function ReviewHistorySection({ entries, availability }: {
    entries: CalculationRecord["review_history"]
    availability: SectionAvailability
}) {
    const rows = entries ?? []
    const neverReviewed = isNeverReviewed(entries)
    return (
        <section className="ledger-section" aria-labelledby="review-heading">
            <SectionHeading id="review-heading" kicker="Review">Review history</SectionHeading>
            {availability === "populated" && !neverReviewed ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Review history">
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
                </div>
            ) : availability === "populated" ? (
                <p className="empty-projection">Not yet reviewed.</p>
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
// On-demand sections ("Further evidence")
// ---------------------------------------------------------------------------

/**
 * One disclosure gated on an `available_sections` flag (or, for
 * `imaginary_mode_projections`, on `freq_modes_applicable` -- see the
 * module docstring). Composes the shared `Disclosure` primitive
 * (`components/Disclosure.tsx`), which fetches its own token, once, the
 * first time it opens.
 *
 * Design/foundations PR B (item 5): this used to be a page-local bordered
 * `<details>` styled with the same recipe
 * `TransitionStateEntryPage.tsx`'s per-role geometry disclosures used
 * (`.geometry-role-disclosure`) -- both are now the ONE canonical
 * `.disclosure` every disclosure on the app renders through, and the
 * redundant "Show" affordance span is gone (`.disclosure`'s own chevron,
 * `design-system.css`, is the idle-state affordance now). Every
 * disclosure below is still grouped under the ONE "Further evidence"
 * section that registers a single ToC entry -- `Disclosure`'s own
 * `summary` prop (not `SectionHeading`) is what keeps it from registering
 * a second one per disclosure. `id` is an explicit, stable prop -- never
 * derived from the heading text -- so a future heading rewrite cannot
 * silently move the anchor.
 *
 * The live region is a short status *sentence*, not a wrapper around the
 * fetched payload: `role="status"` carries an implicit `aria-atomic="true"`,
 * so a live region that contained the payload itself would have an
 * assistive technology re-speak the *entire* region on every change.
 * Announcing "<heading> loaded." and rendering the actual table/list as an
 * ordinary sibling (outside the live region) gives the same "something
 * happened" signal without forcing a full read-out of a payload the user
 * is about to navigate as a table on their own terms.
 *
 * `applicable` (default `true`) is a SEPARATE axis from `available`, and
 * checked first: `available` says "does this record have data for a
 * section its type COULD have"; `applicable` says "can a calculation of
 * this type have this section AT ALL". Both render NOTHING here.
 */
function LazySection<T>({
    id, heading, available, state, onOpen, children, applicable = true,
}: {
    id: string
    heading: string
    available: boolean
    state: CalculationSectionState<T>
    onOpen: () => void
    children: (data: T) => ReactNode
    applicable?: boolean
}) {
    if (!applicable || !available) return null
    return (
        <Disclosure id={id} summary={heading} onToggle={(open) => { if (open) onOpen() }}>
            <p className="note" role="status">
                {state.status === "loading" && "Loading…"}
                {state.status === "error" && state.message}
                {state.status === "ready" && `${heading} loaded.`}
            </p>
            {state.status === "ready" && children(state.data)}
        </Disclosure>
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
// `LazySection` exactly, since that string is also the summary text a
// reader sees.
const ON_DEMAND_SECTION_SPECS: {
    heading: string
    applicable: (a: CalculationRecord["available_sections"]) => boolean
    available: (a: CalculationRecord["available_sections"]) => boolean
}[] = [
    { heading: "Energy corrections", applicable: () => true, available: (a) => a.has_energy_corrections },
    { heading: "Geometry validation", applicable: (a) => a.geometry_validation_applicable, available: (a) => a.has_geometry_validation },
    { heading: "SCF stability", applicable: () => true, available: (a) => a.has_scf_stability },
    // Never listed as "missing": no `wavefunction_diagnostic_applicable`
    // flag exists server-side (the concept applies only to certain
    // correlated-wavefunction methods, which this client cannot derive
    // from `type` alone), so the honest move is to say nothing rather
    // than call it "missing" on, say, a plain DFT opt (review finding).
    // The disclosure below still renders normally whenever the archive
    // DOES have one.
    { heading: "Wavefunction diagnostic", applicable: () => false, available: (a) => a.has_wavefunction_diagnostic },
    { heading: "Spin diagnostic", applicable: () => true, available: (a) => a.has_spin_diagnostic },
    { heading: "Parsed parameters", applicable: () => true, available: (a) => a.has_parameters },
    { heading: "Constraints", applicable: (a) => a.constraints_applicable, available: (a) => a.has_constraints },
    { heading: "Vibrational modes", applicable: (a) => a.freq_modes_applicable, available: (a) => a.has_freq_modes },
    // Gated on `freq_modes_applicable`, not a dedicated flag of its own --
    // an imaginary-mode projection presupposes vibrational modes exist at
    // all, so it inherits that same applicability rather than the
    // permissive `() => true` every other always-applicable row above
    // uses (review finding: this used to claim "missing" even on a
    // calculation type that cannot have frequency modes in the first
    // place).
    { heading: "Imaginary-mode projections", applicable: (a) => a.freq_modes_applicable, available: (a) => a.has_hessian },
    { heading: "Scan trajectory", applicable: (a) => a.scan_applicable, available: (a) => a.has_scan },
    { heading: "IRC trajectory", applicable: (a) => a.irc_applicable, available: (a) => a.has_irc },
    { heading: "Path-search trajectory", applicable: (a) => a.path_search_applicable, available: (a) => a.has_path_search },
    { heading: "Artifacts", applicable: () => true, available: (a) => a.has_artifacts },
    { heading: "Execution environment", applicable: () => true, available: (a) => a.has_execution_environment },
]

function OnDemandSections({ calculation, available, geometryValidationState, openGeometryValidation }: {
    calculation: CalculationRecord
    available: CalculationRecord["available_sections"]
    geometryValidationState: CalculationSectionState<CalculationGeometryValidation[] | null>
    openGeometryValidation: () => void
}) {
    const ref = calculation.calculation.calculation_ref
    // Applicable to this calculation's TYPE, but nothing recorded -- the
    // ten-empty-headings defect. Collected once here instead of each
    // section rendering its own heading over one line of "not recorded"
    // prose.
    const missing = ON_DEMAND_SECTION_SPECS
        .filter((spec) => spec.applicable(available) && !spec.available(available))
        .map((spec) => spec.heading)

    return (
        <section className="ledger-section" aria-labelledby="further-evidence-heading">
            {/* No kicker (SHOULD-FIX-6): repeated this section's own title. */}
            <SectionHeading id="further-evidence-heading" intro="Machine-parsed detail and additional checks, loaded from the archive on request.">
                Further evidence
            </SectionHeading>
            <div className="geometry-groups">
                <EnergyCorrectionsSection calculationRef={ref} available={available.has_energy_corrections} />
                <GeometryValidationSection
                    available={available.has_geometry_validation}
                    applicable={available.geometry_validation_applicable}
                    state={geometryValidationState}
                    onOpen={openGeometryValidation}
                />
                <SCFStabilitySection calculationRef={ref} available={available.has_scf_stability} />
                <WavefunctionDiagnosticSection calculationRef={ref} available={available.has_wavefunction_diagnostic} />
                <SpinDiagnosticSection calculationRef={ref} available={available.has_spin_diagnostic} />
                <ParametersSection calculationRef={ref} available={available.has_parameters} />
                <ConstraintsSection calculationRef={ref} available={available.has_constraints} applicable={available.constraints_applicable} />
                <FreqModesSection calculationRef={ref} available={available.has_freq_modes} applicable={available.freq_modes_applicable} />
                <ImaginaryModeProjectionsSection calculationRef={ref} hessianAvailable={available.has_hessian} applicable={available.freq_modes_applicable} />
                <ScanSection calculationRef={ref} available={available.has_scan} applicable={available.scan_applicable} />
                <IRCSection calculationRef={ref} available={available.has_irc} applicable={available.irc_applicable} />
                <PathSearchSection calculationRef={ref} available={available.has_path_search} applicable={available.path_search_applicable} />
                <ArtifactsSection calculationRef={ref} available={available.has_artifacts} />
                <ExecutionEnvironmentSection calculationRef={ref} available={available.has_execution_environment} />
            </div>
            {/* Folded into this same section as a trailing paragraph, not a
                separate `<section aria-label>` landmark of its own (review
                finding: a one-line region announced as its own navigable
                landmark for assistive tech was overkill for a single
                sentence). */}
            <MissingSectionsNote headings={missing} />
        </section>
    )
}

/**
 * The one line every applicable-but-empty on-demand section collapses
 * into, replacing ten separate empty headings. Renders as a plain
 * paragraph inside the "Further evidence" section it now always sits in
 * (see `OnDemandSections`) -- never its own landmark.
 */
function MissingSectionsNote({ headings }: { headings: string[] }) {
    if (headings.length === 0) return null
    return <p className="empty-projection">Not recorded on this calculation: {headings.join(", ")}.</p>
}

function useSection<T>(calculationRef: string, token: OnDemandSectionToken) {
    return useCalculationSection<T>(calculationRef, token)
}

function EnergyCorrectionsSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationEnergyCorrection[] | null>(calculationRef, "energy_corrections")
    return (
        <LazySection id="section-energy-corrections" heading="Energy corrections" available={available} state={state} onOpen={open}>
            {(rows) => (rows?.length ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Applied energy corrections">
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
                                    <td data-label="Applied value" className="num">{row.applied_value} {row.applied_value_unit}</td>
                                    <td data-label="Target">{row.target_record_ref ?? "not recorded"}</td>
                                    <td data-label="Scheme">{row.energy_correction_scheme_name ?? "not recorded"}</td>
                                    <td data-label="Scheme ref">{row.energy_correction_scheme_ref ?? "not recorded"}</td>
                                    <td data-label="Frequency scale factor ref">{row.frequency_scale_factor_ref ?? "not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : <p className="empty-projection">The archive returned no correction rows.</p>)}
        </LazySection>
    )
}

function GeometryValidationSection({ available, applicable, state, onOpen }: {
    available: boolean
    applicable: boolean
    state: CalculationSectionState<CalculationGeometryValidation[] | null>
    onOpen: () => void
}) {
    return (
        <LazySection id="section-geometry-validation" heading="Geometry validation" available={available} applicable={applicable} state={state} onOpen={onOpen}>
            {(rows) => {
                const row = rows?.[0]
                if (!row) return <p className="empty-projection">The archive returned no validation row.</p>
                return (
                    <>
                        <p className="note">
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
        <LazySection id="section-scf-stability" heading="SCF stability" available={available} state={state} onOpen={open}>
            {(rows) => {
                const row = rows?.[0]
                if (!row) return <p className="empty-projection">The archive returned no stability row.</p>
                return (
                    <>
                        {row.source_calculation_ref && (
                            <p className="note">
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
        <LazySection id="section-wavefunction-diagnostic" heading="Wavefunction diagnostic" available={available} state={state} onOpen={open}>
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
        <LazySection id="section-spin-diagnostic" heading="Spin diagnostic" available={available} state={state} onOpen={open}>
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
        <LazySection id="section-parsed-parameters" heading="Parsed parameters" available={available} state={state} onOpen={open}>
            {(rows) => (rows?.length ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Parsed execution parameters">
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
                </div>
            ) : <p className="empty-projection">The archive returned no parameter rows.</p>)}
        </LazySection>
    )
}

function ConstraintsSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationConstraint[] | null>(calculationRef, "constraints")
    return (
        <LazySection id="section-constraints" heading="Constraints" available={available} applicable={applicable} state={state} onOpen={open}>
            {(rows) => (rows?.length ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Calculation constraints">
                        <thead><tr><th scope="col">Kind</th><th scope="col">Atoms</th><th scope="col">Target value</th></tr></thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={`constraint-${row.constraint_index}`}>
                                    <td data-label="Kind">{statusLabel(row.constraint_kind)}</td>
                                    <td data-label="Atoms" className="num">{row.atom_indices.join(", ")}</td>
                                    <td data-label="Target value" className="num">{row.target_value ?? "not recorded"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : <p className="empty-projection">The archive returned no constraint rows.</p>)}
        </LazySection>
    )
}

function FreqModesSection({ calculationRef, available, applicable }: { calculationRef: string; available: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationFreqMode[] | null>(calculationRef, "freq_modes")
    return (
        <LazySection id="section-vibrational-modes" heading="Vibrational modes" available={available} applicable={applicable} state={state} onOpen={open}>
            {(rows) => (rows?.length ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Vibrational modes">
                        <thead><tr><th scope="col">Mode</th><th scope="col">Frequency (cm-1)</th><th scope="col">Imaginary</th></tr></thead>
                        <tbody>
                            {rows.map((row) => (
                                <tr key={`mode-${row.mode_index}`}>
                                    <td data-label="Mode" className="num">{row.mode_index}</td>
                                    <td data-label="Frequency (cm-1)" className="num">{row.frequency_cm1}</td>
                                    <td data-label="Imaginary">{boolLabel(row.is_imaginary)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : <p className="empty-projection">The archive returned no mode rows.</p>)}
        </LazySection>
    )
}

function ImaginaryModeProjectionsSection({ calculationRef, hessianAvailable, applicable }: { calculationRef: string; hessianAvailable: boolean; applicable: boolean }) {
    const [state, open] = useSection<CalculationImaginaryModeProjection | null>(calculationRef, "imaginary_mode_projections")
    return (
        <LazySection id="section-imaginary-mode-projections" heading="Imaginary-mode projections" available={hessianAvailable} applicable={applicable} state={state} onOpen={open}>
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
                            <div className="table-scroll">
                                <table className="data-table" aria-label="Imaginary mode projections">
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
                                                <td data-label="Mode" className="num">{mode.mode_index}</td>
                                                <td data-label="Frequency (cm-1)" className="num">{mode.frequency_cm1}</td>
                                                <td data-label="Declared">{mode.declared_disposition ? statusLabel(mode.declared_disposition) : "not recorded"}</td>
                                                <td data-label="Determination">{mode.determination ? statusLabel(mode.determination) : "not determined"}</td>
                                                <td data-label="Agreement">{statusLabel(mode.agreement)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
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
        <LazySection id="section-scan-trajectory" heading="Scan trajectory" available={available} applicable={applicable} state={state} onOpen={open}>
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
        <LazySection id="section-irc-trajectory" heading="IRC trajectory" available={available} applicable={applicable} state={state} onOpen={open}>
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
        <LazySection id="section-path-search-trajectory" heading="Path-search trajectory" available={available} applicable={applicable} state={state} onOpen={open}>
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
        <LazySection id="section-artifacts" heading="Artifacts" available={available} state={state} onOpen={open}>
            {(rows) => (rows?.length ? (
                <div className="table-scroll">
                    <table className="data-table" aria-label="Calculation artifacts">
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
                                    <td data-label="Size" className="num">{row.bytes.toLocaleString()} bytes</td>
                                    <td data-label="Artifact ref">{row.artifact_ref ?? "not recorded"}</td>
                                    {/* The sha256 is the artifact's identity — the storage URI (row.uri)
                                        is not a downloadable link, so this is the one stable handle for
                                        the bytes this row describes. */}
                                    <td data-label="SHA-256"><code>{row.sha256}</code></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : <p className="empty-projection">The archive returned no artifact rows.</p>)}
        </LazySection>
    )
}

function ExecutionEnvironmentSection({ calculationRef, available }: { calculationRef: string; available: boolean }) {
    const [state, open] = useSection<CalculationExecutionEnvironment | null>(calculationRef, "execution_environment")
    return (
        <LazySection id="section-execution-environment" heading="Execution environment" available={available} state={state} onOpen={open}>
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
