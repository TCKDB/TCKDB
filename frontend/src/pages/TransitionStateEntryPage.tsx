import { useEffect, useState, type ReactNode } from "react"
import { Link, useParams } from "react-router-dom"
import "../conformer-group.css"
import "../transition-state-entry.css"
import type { TransitionStateEntryRecord, TransitionStateSiblingRecord } from "../api/transitionStateEntryApi"
import { loadTransitionStateEntry, loadTransitionStateSiblings } from "../api/transitionStateEntryApi"
import { lotLabel } from "../api/scientificSchemas"
import { PageShell } from "../components/PageShell"
import { SectionHeading } from "../components/PageSections"
import { RecordIdentityHeader } from "../components/RecordIdentityHeader"
import { RecordStatus } from "../components/RecordStatus"
import { RefsDisclosure } from "../components/RefsDisclosure"
import { formatEnergyForDisplay } from "../domain/energyUnits"
import { softwareLabel, toolReleaseLabel } from "../domain/provenanceFormat"
import type { TransitionStateIdentity } from "../domain/recordIdentity"
import { useScientificRecord } from "../hooks/useScientificRecord"

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

// Priority order for picking one "primary" calculation off a sibling
// entry's calc list to summarise its level of theory/software with --
// `sp` first (the most-refined stage, and the one this archive's own
// hydrazine siblings actually differ on: wb97xd opt/freq, MRCI+Davidson
// or CCSD(T)-F12 sp), falling down through progressively earlier stages.
const SIBLING_PRIMARY_STAGE_PRIORITY = ["sp", "irc", "freq", "opt", "path_search"]

function pickPrimaryCalcSummary(calculations: Calculation[]): Calculation | null {
    for (const stage of SIBLING_PRIMARY_STAGE_PRIORITY) {
        const found = calculations.find((calc) => calc.type === stage)
        if (found) return found
    }
    return calculations[0] ?? null
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

// Splits a reaction equation at its arrow(s) and `+` separators, inserting
// `<wbr>` right after each -- a long species name either side of one of
// these tokens should wrap there, not at an arbitrary character
// `overflow-wrap: anywhere` happens to land on.
function renderEquationWithBreaks(equation: string): ReactNode {
    const parts = equation.split(/(<=>|->|\+)/)
    const nodes: ReactNode[] = []
    parts.forEach((part, index) => {
        nodes.push(part)
        if (part === "<=>" || part === "->" || part === "+") nodes.push(<wbr key={`eq-wbr-${index}`} />)
    })
    return nodes
}

// Three states an include-gated section can be in, kept distinct per the
// house rule this app already applies on `ConformerObservationPage`:
// absence describes the request, null/empty describes the data.
type SectionAvailability = "not-requested" | "empty" | "populated"

function sectionAvailability<T>(value: T[] | null | undefined): SectionAvailability {
    if (value === undefined) return "not-requested"
    if (value === null || value.length === 0) return "empty"
    return "populated"
}

/**
 * `/transition-state-entries/:entryRef` -- the record view 34 browsable
 * transition-state entries had no page to land on before this (browse
 * only linked to `/reactions/:ref`, a placeholder). Modeled on
 * `ConformerObservationPage`'s shell (self-contained record, no
 * conformer-picker state to carry), not `SpeciesEntryPage`'s tabbed one --
 * a TS entry has no conformer basin to select between.
 *
 * Renders what `GET /scientific/transition-state-entries/{ref}` serves:
 * identity, the saddle-point (imaginary-frequency) verdict, reaction
 * classification, evidence pills, IRC status (including point counts and
 * the honest "endpoint identity not deposited" state), calculations,
 * geometries, sibling saddle points on the same reaction, and review
 * history. It does NOT render a literature/citations section -- the
 * endpoint (see `TRANSITION_STATE_RECORD_SECTIONS` in
 * `backend/app/api/routes/scientific/_response.py`) has no such include
 * token to ask for one; there is nothing to omit by choice, only nothing
 * on the wire to show.
 */
export default function TransitionStateEntryPage() {
    const { entryRef = "" } = useParams<{ entryRef: string }>()
    const state = useScientificRecord(entryRef, loadTransitionStateEntry)

    if (state.status === "ready") return <EntryDetail record={state.record} />
    return (
        <RecordStatus
            state={state}
            ref={entryRef}
            kind="transition state entry"
            loadingDetail="Retrieving the deposited saddle-point entry and its reaction context."
        />
    )
}

function EntryDetail({ record }: { record: TransitionStateEntryRecord }) {
    const {
        transition_state_entry: entry,
        transition_state: ts,
        reaction,
        evidence_summary: evidence,
        validation,
        available_sections: available,
    } = record
    // `record.saddle_point` is passed through to `SaddlePointStatement`
    // WITHOUT folding to `?? null` here -- `undefined` (field not served by
    // this deployment yet) and `null` (served, and this entry genuinely has
    // no freq-result row) are NOT the same claim, and collapsing them was
    // the BLOCKING #2 defect: every live page has a lit `freq` evidence
    // pill and a `freq` calculation-table row sitting directly below a
    // sentence that said "No frequency calculation deposited for this
    // entry" -- false whenever a freq calc exists but never got a result
    // row. See `SaddlePointStatement`'s own docstring for the three-way
    // split this now makes.
    const trust = record.trust ?? null

    const identity: TransitionStateIdentity = {
        kind: "transition_state_entry",
        // Never served on this endpoint (see `TransitionStateEntryCoreBlock`)
        // -- a TS has no molecular-graph formula the way a species does.
        formula: null,
        unmappedSmiles: entry.unmapped_smiles ?? null,
        charge: entry.charge,
        multiplicity: entry.multiplicity,
        transitionStateRef: ts.transition_state_ref,
        transitionStateEntryRef: entry.transition_state_entry_ref,
        label: ts.label,
    }

    const calculationsAvailability = sectionAvailability(record.calculations)
    const calculations = sortCalculationsByStage(record.calculations ?? [])
    const ircCalc = calculations.find((calc) => calc.type === "irc") ?? null

    const geometriesAvailability = sectionAvailability(record.geometries)
    // One geometry object can be linked from several of this entry's
    // calculations -- on the live archive `tse_4fzvo2qpovgytr5yduytj3mmh4`
    // returns its single saddle point three times (opt, freq and sp all
    // cite it), which rendered as three identical "final" cards and three
    // colliding React keys. Rows are deduplicated by ref, first occurrence
    // in output order winning, so the count below is of geometries, not of
    // calculation-to-geometry links.
    const seenGeometryRefs = new Set<string>()
    const geometries = [...(record.geometries ?? [])]
        .sort((a, b) => (a.output_order ?? a.input_order ?? 0) - (b.output_order ?? b.input_order ?? 0))
        .filter((geometry) => {
            if (seenGeometryRefs.has(geometry.geometry_ref)) return false
            seenGeometryRefs.add(geometry.geometry_ref)
            return true
        })
    // On the live record this section serves 1 `final` + 50 `irc_forward` +
    // 33 `irc_reverse` -- an undifferentiated grid of 84 identical-looking
    // cards buried the one geometry that matters (the saddle point itself)
    // in ~1,900px of IRC trajectory points. `final` is split out and led
    // with, prominently and linked; every other role is grouped and put
    // behind one collapsed disclosure per role, showing its point count
    // rather than one card per point. Group order is deterministic:
    // forward, reverse, then every other role in first-encountered order.
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

    // The reaction-record link lives ONCE, in the Reaction section below
    // (with a "record view not yet available" note, since `/reactions/:ref`
    // is a placeholder route) -- not duplicated here as well.
    const refs = [
        { label: "Transition state entry", value: entry.transition_state_entry_ref },
        { label: "Transition state", value: ts.transition_state_ref },
        ...(reaction.reaction_entry_ref ? [{ label: "Reaction entry", value: reaction.reaction_entry_ref }] : []),
    ]

    const { siblings, status: siblingsStatus } = useTransitionStateSiblings(reaction.reaction_ref, entry.transition_state_entry_ref)
    const siblingsFailed = siblingsStatus === "error"

    return (
        <section className="conformer-page tse-page">
            <nav className="record-breadcrumbs" aria-label="Breadcrumb">
                <Link to="/">TCKDB</Link>
                <span aria-hidden="true">/</span>
                <Link to="/species?kind=transition_state">Browse</Link>
                <span aria-hidden="true">/</span>
                <span aria-current="page">Transition state entry</span>
            </nav>
            <PageShell
                identity={(
                    <header className="basin-header">
                        <p className="eyebrow">Transition state entry · deposited scientific record</p>
                        <div className="tse-title-row">
                            <h1 className="tse-equation-heading">
                                {reaction.equation ? renderEquationWithBreaks(reaction.equation) : "Reaction not recorded"}
                            </h1>
                            <div className="tse-title-badges">
                                <span className="review-badge">{statusLabel(entry.review.status)}</span>
                                {trust && (
                                    <span className="tse-trust-badge">
                                        trust {statusLabel(trust.trust_status)} · {trust.evidence.passed_count}/{trust.evidence.possible_count}
                                    </span>
                                )}
                                <span className="tse-label-facet">label {ts.label ?? "not recorded"}</span>
                            </div>
                        </div>
                        {/* explainTransitionStateIdentity=false: the Reaction
                            section's own lede below is this page's one
                            "no canonical SMILES" sentence -- see that
                            section and `RecordIdentityHeader`'s own
                            docstring for the duplication this avoids. */}
                        <RecordIdentityHeader identity={identity} explainTransitionStateIdentity={false} />
                        <SaddlePointStatement saddlePoint={record.saddle_point} evidence={evidence} />
                        {/* No "Charge / multiplicity" row here: `RecordIdentityHeader`
                            above already carries it as one of its identity
                            facts for every `transition_state_entry` -- see
                            its own docstring. Restating it here duplicated
                            the exact same "0 / doublet (2)" text twice on
                            one page. */}
                        {/* No "Transition state review" row here: the review-badge
                            pill beside the h1 above already states this entry's
                            review status. Restating it here duplicated that status
                            text twice on one page. */}
                        <dl className="basin-context">
                            <div><dt>Entry status</dt><dd>{statusLabel(entry.status)}</dd></div>
                            <div><dt>Deposited</dt><dd>{isoDate(entry.created_at)}</dd></div>
                            {ts.note && <div><dt>Transition state note</dt><dd>{ts.note}</dd></div>}
                        </dl>
                        <RefsDisclosure refs={refs} />
                    </header>
                )}
            >
            <section className="ledger-section" aria-labelledby="reaction-context">
                <div className="ledger-heading">
                    <p className="eyebrow">Reaction context</p>
                    <SectionHeading id="reaction-context">Reaction</SectionHeading>
                    <p>A transition state is identified by the reaction it connects, not a molecular graph of its own.</p>
                </div>
                {/* No "Equation" fact here: the h1 above already IS the
                    rendered equation (see the h1 rework). Restating it
                    here duplicated the exact same text twice on one page. */}
                <dl className="basin-context">
                    <div><dt>Family</dt><dd>{reaction.family ? statusLabel(reaction.family) : "not recorded"}</dd></div>
                    <div><dt>Reversible</dt><dd>{reaction.reversible === null || reaction.reversible === undefined ? "not recorded" : (reaction.reversible ? "yes" : "no")}</dd></div>
                    {reaction.reaction_ref && (
                        <div>
                            <dt>Reaction record</dt>
                            <dd>
                                <Link to={`/reactions/${reaction.reaction_ref}`}>{reaction.reaction_ref}</Link>
                                <span className="tse-placeholder-note"> (record view not yet available)</span>
                            </dd>
                        </div>
                    )}
                </dl>
            </section>

            {siblings.length > 0 && (
                <section className="ledger-section" aria-labelledby="siblings-ledger">
                    <div className="ledger-heading">
                        <p className="eyebrow">Reaction context</p>
                        <SectionHeading id="siblings-ledger">Other saddle points deposited for this reaction</SectionHeading>
                        <p>Other candidate transition-state entries attached to the same reaction as this one.</p>
                    </div>
                    <ul className="tse-siblings-list">
                        {siblings.map((sibling) => {
                            const primary = pickPrimaryCalcSummary(sibling.calculations ?? [])
                            const siblingRef = sibling.transition_state_entry.transition_state_entry_ref
                            const reviewStatus = sibling.transition_state_entry.review.status
                            return (
                                <li key={siblingRef}>
                                    <Link to={`/transition-state-entries/${siblingRef}`}>
                                        {sibling.transition_state.label ?? "Unlabeled transition state"}
                                    </Link>
                                    {/* Own ref + deposited date -- MEASURED report: three
                                        siblings on one reaction (hydrazine TS8's page) all
                                        read "TS4 · MRCI+Davidson/... · Molpro (version not
                                        recorded) · NOT REVIEWED", indistinguishable without
                                        these two facts. */}
                                    <code>{siblingRef}</code>
                                    <span>deposited {isoDate(sibling.transition_state_entry.created_at)}</span>
                                    <span>{primary?.level_of_theory ? lotLabel(primary.level_of_theory) : "level of theory not recorded"}</span>
                                    <span>{primary?.software_release ? (softwareCellText(primary.software_release) ?? "software not recorded") : "software not recorded"}</span>
                                    <span className={reviewStatus === "not_reviewed" ? "value-pill value-pill--muted" : "value-pill"}>
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
                    <div className="ledger-heading">
                        <p className="eyebrow">Reaction context</p>
                        <SectionHeading id="siblings-ledger-error">Other saddle points deposited for this reaction</SectionHeading>
                    </div>
                    <p className="empty-projection">Could not load sibling saddle points for this reaction.</p>
                </section>
            )}

            <section className="ledger-section" aria-labelledby="calc-ledger">
                <div className="ledger-heading">
                    <SectionHeading id="calc-ledger">Calculation evidence</SectionHeading>
                </div>
                <EvidencePills evidence={evidence} entryRef={entry.transition_state_entry_ref} />
                {calculationsAvailability === "populated" ? (
                    <CalculationTable calculations={calculations} entryRef={entry.transition_state_entry_ref} />
                ) : (
                    <SectionEmptyMessage
                        availability={calculationsAvailability}
                        emptyText="No calculation rows were returned for this entry."
                        contradicted={calculationsAvailability === "empty" && available.has_calculations}
                    />
                )}
            </section>

            <section className="ledger-section geometry-ledger" aria-labelledby="geometry-ledger">
                <p className="eyebrow">Stored coordinates</p>
                <SectionHeading id="geometry-ledger">Geometry records</SectionHeading>
                <p>
                    These are stored geometry objects linked from this entry's calculation output — the saddle
                    point itself and, where an IRC ran, the reaction-path points either side of it.
                </p>
                <div className="tse-irc-summary">
                    <IrcStatus
                        validation={validation}
                        evidence={evidence}
                        ircCalc={ircCalc}
                        forwardPointCount={forwardPointCount}
                        reversePointCount={reversePointCount}
                    />
                </div>
                {geometriesAvailability === "populated" ? (
                    <div className="geometry-groups">
                        {finalGeometries.length > 0 ? (
                            <div className="geometry-final">
                                <p className="geometry-final-label">Saddle-point geometry</p>
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
                                <details className="ledger-section geometry-role-disclosure" key={role || "role-not-recorded"}>
                                    <summary>
                                        {geometryRoleSummaryLabel(role)}
                                        {" — "}{roleGeometries.length} point{roleGeometries.length === 1 ? "" : "s"}
                                        {natoms != null ? ` · ${natoms} atoms` : ""}
                                    </summary>
                                    {ircCalc && (role === "irc_forward" || role === "irc_reverse") && (
                                        <p className="section-note">
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
                                </details>
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
                <div className="ledger-heading">
                    <p className="eyebrow">Review &amp; trust</p>
                    <SectionHeading id="review-ledger">Review history</SectionHeading>
                </div>
                {reviewAvailability === "populated" ? (
                    onlyDefaultReviewRow ? (
                        <p className="empty-projection">Not yet reviewed — no review events recorded.</p>
                    ) : (
                        <table className="stage-table" aria-label={`Review history for ${entry.transition_state_entry_ref}`}>
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

type SiblingsFetchStatus = "loading" | "loaded" | "error"

/**
 * Fetches "Other saddle points deposited for this reaction" as soon as
 * the reaction ref is known (a second, best-effort round trip -- see
 * `loadTransitionStateSiblings`). A still-loading fetch renders as no
 * siblings (the section simply hasn't appeared yet) -- this section is
 * additive context, and it must never block the rest of an otherwise-
 * successful page render. A FAILED fetch is distinguished from "this
 * reaction genuinely has no siblings": both return an empty
 * `siblings` array, but `status === "error"` lets the caller render a
 * short "could not load" line instead of silently claiming there are
 * none. MEASURED gap this fixes: the previous version's `.catch(() =>
 * setSiblings([]))` made a network failure look identical to zero
 * siblings.
 */
function useTransitionStateSiblings(
    reactionRef: string | null | undefined,
    excludeEntryRef: string,
): { siblings: TransitionStateSiblingRecord[]; status: SiblingsFetchStatus } {
    // Keyed on (reactionRef, excludeEntryRef) rather than reset with a
    // synchronous `setState([])` at the top of the effect (the
    // `useScientificRecord` pattern): a stale result is discarded by
    // comparing the key at READ time instead, so the effect body only
    // ever calls `setState` from inside the async callback.
    const key = `${reactionRef ?? ""}:${excludeEntryRef}`
    const [state, setState] = useState<{
        key: string
        siblings: TransitionStateSiblingRecord[]
        status: SiblingsFetchStatus
    }>({ key, siblings: [], status: "loading" })
    const current = state.key === key ? state : { key, siblings: [] as TransitionStateSiblingRecord[], status: "loading" as const }

    useEffect(() => {
        if (!reactionRef) return
        const controller = new AbortController()
        loadTransitionStateSiblings(reactionRef, excludeEntryRef, controller.signal)
            .then((records) => {
                if (!controller.signal.aborted) setState({ key, siblings: records, status: "loaded" })
            })
            .catch(() => {
                if (!controller.signal.aborted) setState({ key, siblings: [], status: "error" })
            })
        return () => controller.abort()
    }, [key, reactionRef, excludeEntryRef])
    return { siblings: current.siblings, status: current.status }
}

/**
 * The first evidence statement on the page, directly under identity --
 * MEASURED absent from the live TS0 page entirely ("imaginary" and "768"
 * both zero hits in a `--dump-dom`) despite every freq result serving
 * `n_imag`/`imag_freq_cm1` and the trust rubric reading them.
 *
 * `saddlePoint` is passed through in its RAW, un-folded tri-state on
 * purpose -- `undefined` / `null` / populated are three different facts,
 * not two:
 *
 * - `undefined`: this deployment does not serve `saddle_point` at all
 *   (the field is additive and the live archive predates it as of this
 *   writing). Absence describes the *request* here -- the page asked a
 *   backend that never learned to answer -- so it must not be rendered
 *   the same as a populated backend's honest "nothing recorded".
 * - `null` with `evidence.has_freq === false`: no freq calculation was
 *   deposited at all.
 * - `null` with `evidence.has_freq === true`: a freq-type calculation
 *   IS attached to this entry, but the backend's join found no
 *   `calc_freq_result` row for it (the calc has no parsed result yet).
 *   MEASURED BLOCKING DEFECT this fixes: every live page shows a lit
 *   `freq` evidence pill and a `freq` calculation-table row directly
 *   below what used to be an unconditional "No frequency calculation
 *   deposited for this entry." -- a false claim about deposited data,
 *   not merely an unfinished feature. `evidence_summary.has_freq` (any
 *   freq-type calc) is the one signal already on this page that can
 *   tell those two `null` cases apart; `saddle_point === null` alone
 *   cannot, since the backend returns `None` for both "no freq calc"
 *   and "freq calc with no result row" alike (see
 *   `_build_saddle_point_index`'s own docstring).
 * - populated: the full verdict, as before.
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
    const lotText = saddlePoint.level_of_theory ? lotLabel(saddlePoint.level_of_theory) : "level of theory not recorded"
    const freqText = saddlePoint.imag_freq_cm1 != null ? `${saddlePoint.imag_freq_cm1.toFixed(1)} cm⁻¹` : null

    let verdict: ReactNode
    if (saddlePoint.n_imag == null) {
        verdict = "Imaginary-mode count not recorded"
    } else if (saddlePoint.n_imag === 0) {
        verdict = "0 imaginary modes — not a saddle point at this level"
    } else if (saddlePoint.n_imag === 1) {
        verdict = <>1 imaginary mode{freqText ? ` · ${freqText}` : ""}</>
    } else if (saddlePoint.reaction_coordinate_mode_index == null) {
        verdict = `${saddlePoint.n_imag} imaginary modes; reaction-coordinate mode not designated`
    } else {
        verdict = (
            <>
                {saddlePoint.n_imag} imaginary modes · reaction-coordinate mode designated (index {saddlePoint.reaction_coordinate_mode_index})
                {freqText ? ` · ${freqText}` : ""}
            </>
        )
    }

    return (
        <p className="tse-saddle-point">
            <strong>{verdict}</strong> · {lotText} · <Link to={`/calculations/${saddlePoint.calculation_ref}`}>{saddlePoint.calculation_ref}</Link>
            {saddlePoint.imaginary_mode_structural_flag ? " · flagged as a higher-order saddle point" : ""}
        </p>
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
            <p className="section-note">Presence says this entry carries that stage, not that its result was favourable.</p>
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
        <table className="stage-table" aria-label={`Calculations for ${entryRef}`}>
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
                            {calculation.level_of_theory ? lotLabel(calculation.level_of_theory) : "not recorded"}
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
    )
}
