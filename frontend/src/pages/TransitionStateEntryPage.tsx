import { useMemo, type ReactNode } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../transition-state-entry.css"
import { loadReactionEntryParticipants } from "../api/reactionEntryApi"
import { BROWSE_KIND_PATHS } from "../api/browseApi"
import type { StructureAssessment, TransitionStateEntryRecord } from "../api/transitionStateEntryApi"
import { loadTransitionStateEntry } from "../api/transitionStateEntryApi"
import { Breadcrumbs } from "../components/Breadcrumbs"
import { DepositorNote } from "../components/DepositorNote"
import { Disclosure } from "../components/Disclosure"
import { EnergyProfile } from "../components/EnergyProfile"
import { ListPager } from "../components/ListPager"
import { LevelOfTheoryLink } from "../components/LevelOfTheoryLink"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { ReactionEquation } from "../components/ReactionEquation"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { RefsDisclosure } from "../components/RefsDisclosure"
import { MadeUnderLine } from "../components/MadeUnderLine"
import { Term } from "../components/Term"
import { TransitionStateStage } from "../components/TransitionStateStage"
import { ValueLink } from "../components/ValueLink"
import { liveMadeUnder } from "../domain/madeUnder"
import { SIBLING_SUMMARY_STAGE_NOTE, summariseSiblingStage } from "../domain/transitionStateSiblings"
import { formatEnergyForDisplay } from "../domain/energyUnits"
import { formatQuantity } from "../domain/quantityFormat"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import type { TransitionStateIdentity } from "../domain/recordIdentity"
import { equationNeedsStructure, equationText } from "../domain/reactionEquation"
import { reviewPillClass } from "../domain/reviewPillFormat"
import { transitionStateChecks, UNKNOWN_OUTCOME } from "../domain/transitionStateChecks"
import { stageGeometryRef } from "../domain/transitionStateStage"
import { transitionStateEntryLede } from "../domain/transitionStateLede"
import { useLoaded } from "../hooks/useLoaded"
import { useScientificRecord } from "../hooks/useScientificRecord"
import { useTransitionStateSiblings } from "../hooks/useTransitionStateSiblings"
import { useDocumentTitle } from "../hooks/useDocumentTitle"

const statusLabel = (status: string) => status.replaceAll("_", " ")
const isoDate = (value?: string | null) => (value ? value.slice(0, 10) : "not recorded")

type Calculation = NonNullable<TransitionStateEntryRecord["calculations"]>[number]
type Geometry = NonNullable<TransitionStateEntryRecord["geometries"]>[number]
type SaddlePoint = NonNullable<TransitionStateEntryRecord["saddle_point"]>
type EvidenceSummary = TransitionStateEntryRecord["evidence_summary"]
type Validation = TransitionStateEntryRecord["validation"]

// "irc_forward"/"irc_reverse" get their conventional capitalised spelling
// (matching the finding this section was rewritten against: "IRC forward
// — 50 points"); `path_search_point` gets its own label for the same
// reason. Any other role this archive has not been taught yet falls back
// to the same underscore-to-space transcription every other role/status
// token on this page gets, rather than inventing a label for it.
function geometryRoleSummaryLabel(role: string): string {
    if (role === "irc_forward") return "IRC forward"
    if (role === "irc_reverse") return "IRC reverse"
    if (role === "path_search_point") return "Path search"
    if (!role) return "Role not recorded"
    return statusLabel(role)
}

// Deterministic group order for the non-final geometry disclosures:
// forward, then reverse, then every other role in first-encountered
// order -- never archive/output order, which is not guaranteed stable
// across roles.
const GEOMETRY_ROLE_ORDER = ["irc_forward", "irc_reverse"]

// Deterministic calculation-table row order -- opt precedes freq precedes
// sp precedes irc precedes path_search, replacing the previous row order
// (archive/`created_at` return order, whatever it happened to be) and the
// caption that claimed it ("Rows are listed in the order the archive
// returned them."), which this sort makes untrue and which was dropped
// along with it. Ties within a stage keep their original relative order
// (a stable sort, via the `index` tiebreak below).
export const CALCULATION_ROW_ORDER_NOTE =
    "Rows are grouped by stage (opt, freq, sp, irc, path search), and keep the order the archive returned "
    + "within a stage. That is a display order for reading, not a ranking: no row is preferred over another."

const STAGE_ORDER: Record<string, number> = {
    opt: 0,
    freq: 1,
    sp: 2,
    irc: 3,
    path_search: 4,
}

function sortCalculationsByStage(calculations: Calculation[]): Calculation[] {
    return calculations
        .map((calc, index) => ({ calc, index }))
        .sort((a, b) => {
            const rankA = STAGE_ORDER[a.calc.type] ?? Number.MAX_SAFE_INTEGER
            const rankB = STAGE_ORDER[b.calc.type] ?? Number.MAX_SAFE_INTEGER
            return rankA !== rankB ? rankA - rankB : a.index - b.index
        })
        .map(({ calc }) => calc)
}

function energyKindLabel(kind: string): string {
    if (kind === "electronic_energy") return "SP electronic energy"
    if (kind === "final_energy") return "Opt final energy"
    return statusLabel(kind)
}

/**
 * A software release without a recorded version (Molpro on
 * `tse_4fzvo2qpovgytr5yduytj3mmh4`) must say so, not silently print just
 * the name -- `softwareLabel` alone collapses "no version" and "version
 * IS the whole string" into the same bare-name output, which reads as
 * the version being unremarkable rather than absent.
 */
function softwareCellText(release: { software: string; version?: string | null } | null | undefined): string | null {
    if (!release) return null
    if (release.version === null || release.version === undefined || release.version === "") {
        return `${release.software} (version not recorded)`
    }
    return softwareLabel(release)
}

// Three states an include-gated section can be in: absence describes the request, null/empty describes the data.
type SectionAvailability = "not-requested" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

/**
 * `/transition-state-entries/:entryRef`: one deposited saddle point. Renders what `GET /scientific/transition-state-entries/{ref}`
 * serves (identity, imaginary frequency, validation tokens, trust, IRC, calculations, geometries, sibling saddle points,
 * review history), the energy hill of the parent reaction entry narrowed to this entry, and the 3D picture. The reaction's
 * participants come from the parent reaction entry, which is named by this entry, never from a sibling.
 */
export default function TransitionStateEntryPage() {
    const { entryRef = "" } = useParams<{ entryRef: string }>()
    const state = useScientificRecord(entryRef, loadTransitionStateEntry)

    if (state.status === "ready") return <EntryDetail record={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="transition state"
            loadingDetail="Retrieving the deposited saddle-point entry and its reaction context."
        />
    )
}

const CHECK_STATUS = { recorded: "Recorded", not_recorded: "Not recorded", unknown: "Not known" } as const

function EntryDetail({ record }: { record: TransitionStateEntryRecord }) {
    const {
        transition_state_entry: entry,
        transition_state: ts,
        reaction,
        evidence_summary: evidence,
        validation,
        available_sections: available,
    } = record
    const trust = record.trust ?? null

    const participants = useLoaded(loadReactionEntryParticipants, reaction.reaction_entry_ref ?? null)
    const species = participants.status === "ready" ? participants.data.species : null
    const reactants = useMemo(() => species?.reactants ?? [], [species])
    const products = useMemo(() => species?.products ?? [], [species])
    const equationKnown = reactants.length > 0 && products.length > 0
    const reversible = participants.status === "ready" ? participants.data.reaction_entry.reversible : (reaction.reversible ?? false)
    const formulaOnly = !equationNeedsStructure(reactants, products)
    const speciesLabels = useMemo(
        () => new Map([...reactants, ...products].map((participant) => [participant.species_entry_ref, participant.formula ?? participant.smiles])),
        [reactants, products],
    )
    useDocumentTitle("Transition state", equationKnown ? equationText(reactants, products, reversible) : null)

    // No `transitionStateEntryRef`: this page is that entry; its own ref is `ownRef`. The producer's label is not read.
    const identity: TransitionStateIdentity = {
        kind: "transition_state_entry",
        formula: null,
        charge: entry.charge,
        multiplicity: entry.multiplicity,
        transitionStateRef: ts?.transition_state_ref,
    }

    const calculationsAvailability = sectionAvailability(record.calculations)
    const calculations = sortCalculationsByStage(record.calculations ?? [])
    const ircCalc = calculations.find((calc) => calc.type === "irc") ?? null
    const checks = transitionStateChecks(record)

    const geometriesAvailability = sectionAvailability(record.geometries)
    // One geometry can be linked from several calculations; rows are deduplicated by ref, first in output order winning.
    const seenGeometryRefs = new Set<string>()
    const geometries = [...(record.geometries ?? [])]
        .sort((a, b) => (a.output_order ?? a.input_order ?? 0) - (b.output_order ?? b.input_order ?? 0))
        .filter((geometry) => {
            if (seenGeometryRefs.has(geometry.geometry_ref)) return false
            seenGeometryRefs.add(geometry.geometry_ref)
            return true
        })
    // `final` leads; every other role is grouped behind one collapsed disclosure showing its point count.
    const finalGeometries = geometries.filter((geometry) => geometry.role === "final")
    const otherGeometriesByRole = new Map<string, Geometry[]>()
    for (const role of GEOMETRY_ROLE_ORDER) {
        const bucket = geometries.filter((geometry) => geometry.role === role)
        if (bucket.length > 0) otherGeometriesByRole.set(role, bucket)
    }
    for (const geometry of geometries) {
        if (geometry.role === "final" || GEOMETRY_ROLE_ORDER.includes(geometry.role ?? "")) continue
        const key = geometry.role ?? ""
        const bucket = otherGeometriesByRole.get(key)
        if (bucket) bucket.push(geometry)
        else otherGeometriesByRole.set(key, [geometry])
    }
    const forwardPointCount = otherGeometriesByRole.get("irc_forward")?.length ?? 0
    const reversePointCount = otherGeometriesByRole.get("irc_reverse")?.length ?? 0

    const reviewAvailability = sectionAvailability(record.review_history)
    const reviewHistory = record.review_history ?? []
    const onlyDefaultReviewRow = reviewHistory.length === 1
        && reviewHistory[0].status === "not_reviewed"
        && (reviewHistory[0].reviewed_at ?? null) === null
        && (reviewHistory[0].note ?? null) === null

    // Related refs only; this entry's own ref renders first in the header.
    const refs = [
        ...(ts ? [{ label: "Transition state", value: ts.transition_state_ref }] : []),
        ...(reaction.reaction_entry_ref ? [{ label: "Reaction entry", value: reaction.reaction_entry_ref }] : []),
    ]

    const { siblings, meta: siblingsMeta, status: siblingsStatus, busy: siblingsBusy, goTo: goToSiblingsPage } = useTransitionStateSiblings(reaction.reaction_ref, entry.transition_state_entry_ref)
    const siblingsFailed = siblingsStatus === "error"

    const equation = equationKnown
        ? <ReactionEquation reactants={reactants} products={products} reversible={reversible} formulaOnly={formulaOnly} />
        : null
    const crumbs = [
        { label: "Reactions", to: BROWSE_KIND_PATHS.reaction },
        ...(reaction.reaction_ref
            ? [{
                label: equationKnown
                    ? <ReactionEquation reactants={reactants} products={products} reversible={reversible} formulaOnly={formulaOnly} linkParticipants={false} />
                    : "This reaction",
                to: `/reactions/${reaction.reaction_ref}`,
            }]
            : []),
        ...(reaction.reaction_entry_ref ? [{ label: "This deposited version", to: `/reaction-entries/${reaction.reaction_entry_ref}` }] : []),
        { label: "This transition state" },
    ]

    return (
        <section className="conformer-page tse-page">
            <PageShell
                contents="list"
                identity={(
                    <>
                        <Breadcrumbs crumbs={crumbs} />
                        <header className="basin-header">
                            <RecordIdentityHeader
                                kicker="Transition state · one deposited saddle point"
                                glyph="transition-state"
                                review={entry.review.status}
                                lede={transitionStateEntryLede(entry, record.evidence_summary.calculation_count)}
                                title={<>Transition state of {equation ?? "this reaction"}</>}
                                identity={identity}
                                explainTransitionStateIdentity={false}
                                ownRef={{ label: "Transition state entry ref", value: entry.transition_state_entry_ref }}
                            />
                            <dl className="kv-list">
                                <div><dt>Entry status</dt><dd>{statusLabel(entry.status)}</dd></div>
                                <div><dt>Deposited</dt><dd>{isoDate(entry.created_at)}</dd></div>
                            </dl>
                            <DepositorNote>{ts?.note}</DepositorNote>
                            <RefsDisclosure refs={refs} />
                        </header>
                    </>
                )}
            >
                {reaction.reaction_entry_ref ? (
                    <EnergyProfile entryRef={reaction.reaction_entry_ref} speciesLabels={speciesLabels} onlyTransitionState={entry.transition_state_entry_ref} />
                ) : (
                    <section className="energy-profile" aria-labelledby="energy-heading">
                        <SectionHeading id="energy-heading" kicker="How the energy changes">Energy hill</SectionHeading>
                        <p className="empty-projection">This entry does not name its reaction entry, so no energy profile can be drawn for it.</p>
                    </section>
                )}

                <section className="ledger-section" aria-labelledby="shape-heading">
                    <SectionHeading id="shape-heading" kicker="Its 3D structure">Shape</SectionHeading>
                    <TransitionStateStage transitionStateEntryRef={entry.transition_state_entry_ref} geometryRef={stageGeometryRef(record)} />
                </section>

                <section className="ledger-section" aria-labelledby="imaginary-heading">
                    <SectionHeading id="imaginary-heading" kicker="The motion over the hill">Imaginary frequency</SectionHeading>
                    <p className="note">
                        The <Term term="imaginary-frequency">imaginary frequency</Term> is the one motion that carries the transition state downhill.
                    </p>
                    <SaddlePointStatement saddlePoint={record.saddle_point} evidence={evidence} />
                </section>

                <section className="ledger-section" aria-labelledby="checks-heading">
                    <SectionHeading id="checks-heading" kicker="What was recorded" intro="Each check is either recorded with its outcome or not recorded. Not recorded means nobody deposited it; it does not mean the check failed.">
                        Checks
                    </SectionHeading>
                    <div className="table-scroll tse-scroll" tabIndex={0} role="region" aria-label="Checks on this transition state">
                        <table className="data-table tse-checks-table">
                            <thead>
                                <tr>
                                    <th scope="col">Check</th>
                                    <th scope="col">Status</th>
                                    <th scope="col">Outcome</th>
                                    <th scope="col">Evidence</th>
                                </tr>
                            </thead>
                            <tbody>
                                {checks.map((check) => (
                                    <tr key={check.key}>
                                        <th scope="row">{check.label}</th>
                                        <td data-label="Status">{CHECK_STATUS[check.status]}</td>
                                        <td data-label="Outcome">
                                            {check.token !== undefined && <><code className="t-preserve-case">{check.token}</code>{": "}</>}
                                            {check.outcome}
                                            {check.note && <span className="tse-check-note">{check.note}</span>}
                                        </td>
                                        <td data-label="Evidence">
                                            {check.evidence.length > 0
                                                ? <ul className="tse-evidence-links">{check.evidence.map((link) => <li key={link.to}><Link to={link.to}>{link.text}</Link></li>)}</ul>
                                                : <span className="tse-absent">none to link</span>}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </section>

                <section className="ledger-section" aria-labelledby="trust-irc-heading">
                    <SectionHeading id="trust-irc-heading" kicker="Two separate questions">Trust and IRC</SectionHeading>
                    <div className="tse-pair">
                        <section className="tse-pair-card" aria-labelledby="trust-card-heading">
                            <h3 className="t-heading-2" id="trust-card-heading">Trust</h3>
                            <p className="note">Asks how much of the evidence TCKDB expects for a saddle point was deposited, by a fixed list of checks.</p>
                            {trust ? (
                                <>
                                    <p>
                                        <strong>{statusLabel(trust.trust_status)}</strong> · {trust.evidence.passed_count} of {trust.evidence.possible_count} checks passed
                                        {trust.evidence.rubric && trust.evidence.rubric_version != null && (
                                            <MadeUnderLine madeUnder={liveMadeUnder(trust.evidence.rubric, trust.evidence.rubric_version)} />
                                        )}
                                    </p>
                                    {trust.structure_assessment && (
                                        <p><span className="tse-pair-label">Structure assessment</span> {structureAssessmentSentence(trust.structure_assessment)}</p>
                                    )}
                                    <TrustWarnings checks={trust.evidence.checks} />
                                </>
                            ) : (
                                <p className="empty-projection">No trust assessment was served for this entry.</p>
                            )}
                        </section>
                        <section className="tse-pair-card tse-irc-summary" aria-labelledby="irc-card-heading">
                            <h3 className="t-heading-2" id="irc-card-heading">IRC</h3>
                            <p className="note">Asks whether a reaction-path calculation connects this saddle point to the reactants and products it was deposited for.</p>
                            <IrcStatus
                                validation={validation}
                                evidence={evidence}
                                ircCalc={ircCalc}
                                forwardPointCount={forwardPointCount}
                                reversePointCount={reversePointCount}
                            />
                        </section>
                    </div>
                </section>

                <section className="ledger-section" aria-labelledby="reaction-context">
                    <SectionHeading id="reaction-context" kicker="Reaction context" intro="A transition state is identified by the reaction it connects, not a molecular graph of its own.">
                        Reaction
                    </SectionHeading>
                    <dl className="kv-list">
                        <div><dt>Family</dt><dd>{reaction.family ? statusLabel(reaction.family) : "not recorded"}</dd></div>
                        <div><dt>Reversible</dt><dd>{reaction.reversible === null || reaction.reversible === undefined ? "not recorded" : (reaction.reversible ? "yes" : "no")}</dd></div>
                        {reaction.reaction_ref && (
                            <div>
                                <dt>Reaction record</dt>
                                <dd>
                                    <Link to={`/reactions/${reaction.reaction_ref}`}>{reaction.reaction_ref}</Link>
                                </dd>
                            </div>
                        )}
                    </dl>
                </section>

            {(siblings.length > 0 || (siblingsMeta?.pagination.total ?? 0) > 1) && (
                <section className="ledger-section" aria-labelledby="siblings-ledger">
                    <SectionHeading
                        id="siblings-ledger"
                        kicker="Reaction context"
                        intro="Other candidate transition-state entries attached to the same reaction as this one."
                    >
                        Other saddle points deposited for this reaction
                    </SectionHeading>
                    {siblingsMeta && (
                        <ListPager
                            meta={siblingsMeta}
                            noun="saddle point"
                            onPage={goToSiblingsPage}
                            busy={siblingsBusy}
                            label="Other saddle points paging"
                        />
                    )}
                    <p className="note tse-siblings-note">
                        This entry is left out of the list below; a count above includes it unless it is hidden by default. {SIBLING_SUMMARY_STAGE_NOTE}
                    </p>
                    {siblings.length === 0 && <p className="empty-projection">No other saddle points are on this page.</p>}
                    <ul className="tse-siblings-list">
                        {siblings.map((sibling) => {
                            const summary = summariseSiblingStage(sibling.calculations ?? [])
                            const primary = summary?.calculation ?? null
                            const siblingRef = sibling.transition_state_entry.transition_state_entry_ref
                            const reviewStatus = sibling.transition_state_entry.review.status
                            return (
                                <li key={siblingRef}>
                                    {/* The link text is the sibling's own ref, never
                                        `sibling.transition_state.label` (house rule
                                        widened 2026-09: no depositor-typed labels on
                                        public pages -- three siblings on one reaction
                                        used to all read "TS4", indistinguishable, and
                                        a fourth reaction on this same page's own
                                        MEASURED live data carries the identical label
                                        "TS1" on every one of its own siblings). Deposit
                                        date + level of theory + software below already
                                        do the differentiating work the label never
                                        could. */}
                                    <Link to={`/transition-state-entries/${siblingRef}`}>
                                        {siblingRef}
                                    </Link>
                                    <span>deposited {isoDate(sibling.transition_state_entry.created_at)}</span>
                                    {summary && summary.varies ? (
                                        <span>
                                            {summary.stageCalculationCount} calculations at the {statusLabel(summary.stage)} stage differ in
                                            level of theory or software; open the entry to see them
                                        </span>
                                    ) : (
                                        <>
                                            <span>
                                                {summary ? `${statusLabel(summary.stage)} stage: ` : ""}
                                                {primary?.level_of_theory ? <LevelOfTheoryLink levelOfTheory={primary.level_of_theory} /> : "level of theory not recorded"}
                                            </span>
                                            <span>{primary?.software_release ? (softwareCellText(primary.software_release) ?? "software not recorded") : "software not recorded"}</span>
                                        </>
                                    )}
                                    <span className={reviewPillClass(reviewStatus)}>
                                        {statusLabel(reviewStatus)}
                                    </span>
                                </li>
                            )
                        })}
                    </ul>
                </section>
            )}
            {siblingsFailed && (
                <section className="ledger-section" aria-labelledby="siblings-ledger-error">
                    <SectionHeading id="siblings-ledger-error" kicker="Reaction context">
                        Other saddle points deposited for this reaction
                    </SectionHeading>
                    <p className="empty-projection">Could not load sibling saddle points for this reaction.</p>
                </section>
            )}

            <section className="ledger-section" aria-labelledby="calc-ledger">
                {/* SHOULD-FIX-6 ("record-page residuals" re-review): every
                    OTHER section on this page carries a kicker; this one
                    didn't, which read as arbitrary rather than a signal.
                    "Machine detail" matches the kicker
                    `ConformerObservationPage` uses for the same section
                    (its own "Calculation evidence" heading) -- the same
                    category, worded the same way, on both pages. */}
                <SectionHeading id="calc-ledger" kicker="Machine detail">Calculation evidence</SectionHeading>
                <EvidencePills evidence={evidence} entryRef={entry.transition_state_entry_ref} />
                {calculationsAvailability === "populated" ? (
                    <>
                        <p className="note calculation-order-note">{CALCULATION_ROW_ORDER_NOTE}</p>
                        <CalculationTable calculations={calculations} entryRef={entry.transition_state_entry_ref} />
                    </>
                ) : (
                    <SectionEmptyMessage
                        availability={calculationsAvailability}
                        emptyText="No calculation rows were returned for this entry."
                        contradicted={calculationsAvailability === "empty" && available.has_calculations}
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="geometry-ledger">
                <SectionHeading
                    id="geometry-ledger"
                    kicker="Stored coordinates"
                    intro="These are stored geometry objects linked from this entry's calculation output — the saddle point itself and, where an IRC ran, the reaction-path points either side of it."
                >
                    Geometry records
                </SectionHeading>
                {geometriesAvailability === "populated" ? (
                    <div className="geometry-groups">
                        {finalGeometries.length > 0 ? (
                            <div className="geometry-final">
                                <h3 className="t-heading-2">Saddle-point geometry</h3>
                                <div className="geometry-links">
                                    {finalGeometries.map((geometry) => (
                                        <div className="geometry-link geometry-link--final" key={geometry.geometry_ref}>
                                            <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                                            <span>final{geometry.natoms != null ? ` · ${geometry.natoms} atoms` : ""}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <p className="empty-projection">No saddle-point (final) geometry was deposited for this entry.</p>
                        )}
                        {[...otherGeometriesByRole.entries()].map(([role, roleGeometries]) => {
                            const natoms = roleGeometries[0]?.natoms ?? null
                            return (
                                <Disclosure
                                    key={role || "role-not-recorded"}
                                    id={`geometry-role-${role || "role-not-recorded"}`}
                                    summary={(
                                        <>
                                            {geometryRoleSummaryLabel(role)}
                                            {" — "}{roleGeometries.length} point{roleGeometries.length === 1 ? "" : "s"}
                                            {natoms != null ? ` · ${natoms} atoms` : ""}
                                        </>
                                    )}
                                >
                                    {ircCalc && (role === "irc_forward" || role === "irc_reverse") && (
                                        <p className="note">
                                            From <Link to={`/calculations/${ircCalc.calculation_ref}`}>{ircCalc.calculation_ref}</Link>.
                                        </p>
                                    )}
                                    <ol className="tse-irc-point-list">
                                        {roleGeometries.map((geometry, index) => (
                                            <li key={geometry.geometry_ref}>
                                                <span>{index + 1}</span>
                                                <Link to={`/geometries/${geometry.geometry_ref}`}>{geometry.geometry_ref}</Link>
                                            </li>
                                        ))}
                                    </ol>
                                </Disclosure>
                            )
                        })}
                    </div>
                ) : (
                    <SectionEmptyMessage
                        availability={geometriesAvailability}
                        emptyText="No stored geometry links were returned for this entry."
                        contradicted={geometriesAvailability === "empty" && available.has_geometries}
                    />
                )}
            </section>

            <section className="ledger-section" aria-labelledby="review-ledger">
                <SectionHeading id="review-ledger" kicker="Review & trust">Review history</SectionHeading>
                {reviewAvailability === "populated" ? (
                    onlyDefaultReviewRow ? (
                        <p className="empty-projection">Not yet reviewed — no review events recorded.</p>
                    ) : (
                        <div className="table-scroll">
                            <table className="data-table" aria-label={`Review history for ${entry.transition_state_entry_ref}`}>
                                <thead>
                                    <tr>
                                        <th scope="col">Status</th>
                                        <th scope="col">Reviewed at</th>
                                        <th scope="col">Note</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {reviewHistory.map((historyEntry, index) => (
                                        <tr key={`review-entry-${index}`}>
                                            <td data-label="Status">{statusLabel(historyEntry.status)}</td>
                                            <td data-label="Reviewed at">{isoDate(historyEntry.reviewed_at)}</td>
                                            <td data-label="Note">{historyEntry.note ?? "not recorded"}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )
                ) : (
                    <SectionEmptyMessage
                        availability={reviewAvailability}
                        emptyText="No review history was returned for this entry."
                        contradicted={reviewAvailability === "empty" && available.has_review}
                    />
                )}
            </section>
            </PageShell>
        </section>
    )
}

/**
 * The imaginary-frequency statement. `saddlePoint` stays in its raw tri-state because the three are different facts:
 * `undefined` is a deployment that does not serve the block, `null` with `has_freq` false is no frequency calculation,
 * `null` with `has_freq` true is a frequency calculation with no parsed result row, and an object is the verdict.
 */
function SaddlePointStatement({ saddlePoint, evidence }: {
    saddlePoint: SaddlePoint | null | undefined
    evidence: EvidenceSummary
}) {
    if (saddlePoint === undefined) {
        return <p className="tse-saddle-point">Saddle-point verdict not served by this deployment.</p>
    }
    if (saddlePoint === null) {
        if (evidence.has_freq) {
            return <p className="tse-saddle-point">Frequency calculation deposited; no imaginary-mode result recorded.</p>
        }
        return <p className="tse-saddle-point">No frequency calculation deposited for this entry.</p>
    }
    const lotText = saddlePoint.level_of_theory ? <LevelOfTheoryLink levelOfTheory={saddlePoint.level_of_theory} /> : "level of theory not recorded"
    // Programs print the mode negative or with an i; TCKDB keeps the depositor's sign, so the magnitude is shown with an i.
    const stored = saddlePoint.imag_freq_cm1
    const quantity = formatQuantity("ts_imaginary_frequency_cm1", stored == null ? null : Math.abs(stored))
    const freq = quantity && stored != null
        ? <ValueLink value={`${quantity.value}i`} unit={quantity.unit ?? undefined} full={`stored as ${String(stored).replace("-", "\u2212")}`} to={`/calculations/${saddlePoint.calculation_ref}`} />
        : null

    let verdict: ReactNode
    if (saddlePoint.n_imag == null) {
        verdict = "Imaginary-mode count not recorded"
    } else if (saddlePoint.n_imag === 0) {
        verdict = "0 imaginary modes recorded at this level — needs investigation"
    } else if (saddlePoint.n_imag === 1) {
        verdict = "1 imaginary mode"
    } else if (saddlePoint.reaction_coordinate_mode_index == null) {
        verdict = `${saddlePoint.n_imag} imaginary modes; reaction-coordinate mode not designated`
    } else {
        verdict = `${saddlePoint.n_imag} imaginary modes · reaction-coordinate mode designated (index ${saddlePoint.reaction_coordinate_mode_index})`
    }

    return (
        <div className="tse-saddle-point">
            <p><strong>{verdict}</strong></p>
            <dl className="kv-list">
                <div><dt>Imaginary frequency</dt><dd>{freq ?? "not recorded"}</dd></div>
                <div><dt>Level of theory</dt><dd>{lotText}</dd></div>
                <div><dt>Frequency calculation</dt><dd><Link to={`/calculations/${saddlePoint.calculation_ref}`}>{saddlePoint.calculation_ref}</Link></dd></div>
            </dl>
            {saddlePoint.imaginary_mode_structural_flag && <p>Flagged as a higher-order saddle point.</p>}
            {saddlePoint.basis === "declared_curvature_witness" && <p>This is the frequency result the declared saddle target pins.</p>}
        </div>
    )
}

// One plain sentence per warning-kind trust check. A warning means an attached
// result (or recorded fact) needs investigation: never that the transition state is
// disproved, and the absence of a warning certifies nothing. A warning-kind check
// appears in the served map only when it fires, so a key whose value is "warning" is
// listed; a key this page has not been taught renders as its raw token, so a new
// server-side warning is never silently dropped.
const TRUST_WARNING_SENTENCES: Record<string, string> = {
    no_frequency_result_lacks_an_imaginary_mode: "An attached frequency result reports no imaginary mode",
    no_frequency_result_has_undesignated_imaginary_modes:
        "An attached frequency result reports several imaginary modes and does not say which is the reaction coordinate",
    extra_imaginary_modes_not_flagged: "The frequency result carries the recorded structural flag for an extra imaginary mode",
    geometry_validation_not_failed_for_source_calculations: "An automated geometry check raised a warning",
    declared_saddle_target_qualifies: "The structure assessment finds no declared saddle target that qualifies, or a declared target is contested",
}

// The structure assessment (`trust.structure_assessment`) in plain words. It is the assessment's own answer to one fixed
// question -- does a declared target have the evidence for a first-order saddle -- and nothing more: a declared target that
// qualifies is scoped to that question, and anything else is "needs investigation", never a disproof. A status or outcome
// token this page has not been taught renders as itself, so a new server-side token is never silently dropped.
const STRUCTURE_ASSESSMENT_OUTCOME_SENTENCES: Record<string, string> = {
    qualified_evidence: "a declared target has the evidence for a first-order saddle. Scoped to this question.",
    no_applicable_candidate: "no declared target qualifies for a first-order saddle — needs investigation",
    evidence_conflict: "the declared evidence conflicts — needs investigation",
}

function structureAssessmentSentence(assessment: StructureAssessment): string {
    switch (assessment.status) {
        case "assessed": {
            const outcome = assessment.outcome ?? null
            const sentence = outcome === null
                ? "assessed"
                : (STRUCTURE_ASSESSMENT_OUTCOME_SENTENCES[outcome] ?? outcome)
            const contested = (assessment.contested_target_count ?? 0) > 0 && outcome === "qualified_evidence"
            return `Declared targets, first-order saddle question: ${sentence}${contested ? " A declared target is contested — needs investigation." : ""}`
        }
        case "no_declared_target":
            return "No saddle target is declared, so the structure assessment has nothing to assess."
        case "not_assessed_on_lists":
            return "Not assessed on lists."
        case "not_assessed_over_bound":
            return "Not run: the evidence is over the server's size limits. Use the evidence-selection endpoint."
        default:
            return assessment.status
    }
}

function TrustWarnings({ checks }: { checks: Record<string, string> | undefined }) {
    const warnings = Object.entries(checks ?? {}).filter(([, outcome]) => outcome === "warning").map(([name]) => name)
    if (warnings.length === 0) return null
    return (
        <ul className="tse-trust-warnings" aria-label="Trust warnings">
            {warnings.map((name) => (
                <li key={name}>
                    {TRUST_WARNING_SENTENCES[name] ?? name} — needs investigation
                </li>
            ))}
        </ul>
    )
}

const EVIDENCE_PILL_KINDS: Array<{ key: keyof EvidenceSummary; label: string }> = [
    { key: "has_opt", label: "opt" },
    { key: "has_freq", label: "freq" },
    { key: "has_sp", label: "sp" },
    { key: "has_irc", label: "irc" },
    { key: "has_path_search", label: "path search" },
    { key: "has_geometry_validation", label: "geometry validation" },
    { key: "has_scf_stability", label: "scf stability" },
]

/**
 * Positive-only phrasing, matching `TransitionStateBrowseRow`'s own
 * evidence line ("opt · freq · sp · irc") rather than the "opt yes · freq
 * no" this replaces -- every kind is still shown, with an absent one in
 * the muted pill register instead of dropped, since (unlike the browse
 * row) this is the one place a reader can see the full seven-kind set at
 * a glance rather than reconstructing it from an implicit omission.
 */
function EvidencePills({ evidence, entryRef }: { evidence: EvidenceSummary; entryRef: string }) {
    return (
        <>
            <ul className="tse-evidence-pills" aria-label={`Evidence present on ${entryRef}`}>
                {EVIDENCE_PILL_KINDS.map(({ key, label }) => {
                    const present = Boolean(evidence[key])
                    return (
                        <li key={key} className={present ? "value-pill" : "value-pill value-pill--muted"}>
                            {label}
                        </li>
                    )
                })}
            </ul>
            <p className="note">Presence says this entry carries that stage, not that its result was favourable.</p>
        </>
    )
}

/**
 * Four distinct states, per the finding that "IRC validation: not
 * established" was true for every entry (no `TransitionStateValidationEvidence`
 * row has ever been deposited by an ARC upload) while the IRC calculation
 * itself, when it ran, has real point counts this page already has in
 * hand (from its own `geometries` include -- no extra request): passed /
 * failed / ran-but-no-evidence-record / no-IRC-calculation-at-all.
 */
function IrcStatus({ validation, evidence, ircCalc, forwardPointCount, reversePointCount }: {
    validation: Validation
    evidence: EvidenceSummary
    ircCalc: Calculation | null
    forwardPointCount: number
    reversePointCount: number
}) {
    if (validation.irc === "present") {
        return <p><strong>IRC validation passed</strong> — a structured pass/fail evidence record was deposited for this entry.</p>
    }
    if (validation.irc === "failed") {
        return <p><strong>IRC validation failed</strong> — a structured evidence record was deposited for this entry, but it did not pass.</p>
    }
    if (validation.irc !== "absent") {
        return <p>IRC validation record: <code className="t-preserve-case">{validation.irc}</code> — {UNKNOWN_OUTCOME}.</p>
    }
    if (!evidence.has_irc) {
        return <p className="empty-projection">No IRC calculation was deposited for this entry.</p>
    }
    // validation.irc === "absent" && evidence.has_irc -- an IRC ran, but no
    // structured pass/fail record exists for it. Say what DID happen
    // (point counts, a link to the run) rather than stop at "not
    // established".
    return (
        <>
            <p>
                <strong>IRC ran</strong> — {forwardPointCount} forward · {reversePointCount} reverse points
                {ircCalc && <> · <Link to={`/calculations/${ircCalc.calculation_ref}`}>{ircCalc.calculation_ref}</Link></>}
            </p>
            <p>Endpoint identity (reactant/product) not deposited — no structured pass/fail validation evidence record exists for this entry.</p>
        </>
    )
}

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
                ? " The archive marks this entry as having recorded evidence here; this view did not return it."
                : ""}
        </p>
    )
}

function CalculationTable({ calculations, entryRef }: { calculations: Calculation[]; entryRef: string }) {
    return (
        <div className="table-scroll tse-scroll tse-calc-scroll" tabIndex={0} role="region" aria-label="Calculations of this transition state">
        <table className="data-table" aria-label={`Calculations for ${entryRef}`}>
            <thead>
                <tr>
                    <th scope="col">Stage</th>
                    <th scope="col">Level of theory</th>
                    <th scope="col">Software / workflow</th>
                    <th scope="col">Energy</th>
                    <th scope="col">Review</th>
                    <th scope="col">Record</th>
                </tr>
            </thead>
            <tbody>
                {calculations.map((calculation) => (
                    <tr key={calculation.calculation_ref}>
                        <td data-label="Stage">{calculation.type}</td>
                        <td data-label="Level of theory">
                            {calculation.level_of_theory ? <LevelOfTheoryLink levelOfTheory={calculation.level_of_theory} /> : "not recorded"}
                        </td>
                        <td data-label="Software / workflow">
                            {softwareCellText(calculation.software_release) ?? "not recorded"}
                            {toolReleaseLabel(calculation.workflow_tool_release)
                                ? ` · ${toolReleaseLabel(calculation.workflow_tool_release)}`
                                : ""}
                        </td>
                        <td data-label="Energy">
                            {calculation.energy && calculation.energy.energy_hartree != null
                                ? <>{formatEnergyForDisplay(calculation.energy.energy_hartree, "hartree")} ({energyKindLabel(calculation.energy.energy_kind)})</>
                                : "not recorded"}
                        </td>
                        <td data-label="Review">
                            {calculation.review ? statusLabel(calculation.review.status) : "not recorded"}
                        </td>
                        <td data-label="Record">
                            <Link to={`/calculations/${calculation.calculation_ref}`}>{calculation.calculation_ref}</Link>
                        </td>
                    </tr>
                ))}
            </tbody>
        </table>
        </div>
    )
}
