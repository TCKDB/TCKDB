import { Link } from "react-router-dom"
import type { CalculationDependency } from "../api/calculationApi"
import type { ReactionEntrySpeciesParticipant, ReactionFullCalculationEvidence, ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import { lotLabel } from "../api/scientificSchemas"
import { CalculationDependencyGraph } from "./CalculationDependencyGraph"
import { ProductLevelsFact } from "./ProductLevels"
import { ReactionEquation } from "./ReactionEquation"
import { StageFlow, type StageFlowBox } from "./StageFlow"
import { deriveProductLevelsFromSourceCalculations } from "../domain/productLevels"
import { buildCalculationsByRef } from "../domain/reactionKineticsLevels"
import { softwareLabel } from "../domain/provenanceFormat"
import { reviewPillClass } from "../domain/reviewPillFormat"

function token(value: string): string {
    return value.replaceAll("_", " ")
}

const STAGE_ORDER = ["ts_opt", "ts_freq", "ts_sp", "ts_irc", "ts_guess"]
const STAGE_LABEL: Record<string, string> = {
    ts_opt: "opt",
    ts_freq: "freq",
    ts_sp: "sp",
    ts_irc: "irc",
    ts_guess: "guess",
}

const ENTRY_STATUS_STAGES: { key: string; label: string }[] = [
    { key: "guess", label: "Guess" },
    { key: "optimized", label: "Optimized" },
    { key: "validated", label: "Validated" },
]

/**
 * The Transition states section (plan §2 item 5): one block per
 * `TransitionStateInFull`. Renders the entry's own ref/review, its status
 * as the guess -> optimized -> validated stage strip (a single muted pill
 * for `rejected`, which is not a step on that flow), a calculations-by-
 * stage table cross-referenced against the `/full` top-level
 * `calculations[]` list for level-of-theory/software (see
 * `reactionEntryApi.ts`'s `calculationEvidenceSummarySchema`), a
 * Geometry/Frequencies/Energy fact block, and the calculation dependency
 * graph centred on this TS's own `ts_opt` -- reusing
 * `CalculationDependencyGraph` (the same component `CalculationDetailPage`
 * renders, not a fork) whenever this entry's `dependencies[]` is
 * non-empty.
 *
 * Owner complaint (2026-09): "in reaction we see Optimisation but no idea
 * what the optimisation is of? and its not clickable link to the calc?
 * also i think the graph needs the reactants and products". Three fixes,
 * all rendered directly above the graph as one `.dep-graph-context` block
 * (never inside the SVG itself -- see `CalculationDependencyGraph.tsx`'s
 * own docstring for why the node boxes stay unchanged):
 * - `centreLinked` (new opt-in prop on `CalculationDependencyGraph`) --
 *   the centre node here is the TS's `ts_opt`, a calculation the reader is
 *   NOT already viewing (unlike on `CalculationDetailPage`, where it is
 *   the page), so it gets the same link affordance a parent/child node has.
 * - A subject caption naming what the centre node's optimisation IS the
 *   optimisation OF (this TS entry), linking to the TS entry itself. Kept
 *   OUT of the node's own pill/ref text (which is sized to fit today's
 *   `calc_...` refs at every layout width, `dependencyGraphLayout.ts`'s
 *   own box-sizing estimate) rather than risk overflow inside the SVG.
 * - `reactants`/`products`/`reversible` (new, OPTIONAL props, wired from
 *   `ReactionEntryPage.tsx`'s own already-loaded `species.reactants`/
 *   `.products`/`entry.reversible`) let this section reuse
 *   `ReactionEquation` for the reaction's own participants, each still
 *   linking to its species entry. The caption renders only when BOTH
 *   sides carry at least one participant (`equationParticipants` below) --
 *   `species` is a nullable §3A field on `/full`
 *   (`api/reactionEntryApi.ts`'s `reactionFullResponseSchema`), and
 *   `ReactionEntryPage.tsx` normalises that absence to `{reactants: [],
 *   products: []}`, so a plain `reactants && products` truthiness check
 *   (an earlier version of this gate) rendered a bare "Reaction: ⇌" for
 *   every reaction `/full` omits `species` for -- caught in review since
 *   this section's own unit tests passed `undefined` directly, a shape no
 *   real caller produces, rather than exercising the page's own `[]`
 *   normalisation. An absent equation is an honest "not served," never a
 *   broken one.
 * - A short, separate note on what the graph itself does and does NOT
 *   show: calculations and their data flow, never which species the IRC
 *   connects. Rendered only when this TS entry actually has an IRC
 *   calculation (`ts.calculations.ts_irc`, `hasIrc` below) -- an earlier,
 *   unconditional version named "the IRC" even for the 17 of 34 live TS
 *   entries with only opt/freq/sp, asserting an IRC was run when none
 *   was. The wording itself is deliberately narrower than "the archive
 *   does not record this": the linkage genuinely CAN be recorded, on
 *   `TransitionStateValidationEvidence` (`backend/app/db/models/
 *   transition_state.py`, `reactant_participant_mapping`/
 *   `product_participant_mapping`, served at `/scientific/transition-
 *   state-entries/{ref}?include=validation_evidence`) -- it is only this
 *   VIEW (`/full`, no species-level calculation edge of any kind) that
 *   never carries it, and only true of today's deposited rows that the
 *   evidence itself is empty. A claim that the ARCHIVE lacks it would
 *   have kept asserting itself, wrongly, the day the first such row
 *   lands.
 */
export function ReactionTransitionStatesSection({ transitionStates, calculations, reactants, products, reversible }: {
    transitionStates: ReactionTransitionStateInFull[]
    calculations: ReactionFullCalculationEvidence[] | null | undefined
    /** The reaction's own participants (`/full`'s `species.reactants`/
     * `.products`), for the equation caption above each TS's dependency
     * graph. Optional -- see this component's own docstring. */
    reactants?: ReactionEntrySpeciesParticipant[]
    products?: ReactionEntrySpeciesParticipant[]
    reversible?: boolean
}) {
    if (transitionStates.length === 0) {
        return <p className="empty-projection">No transition-state entry has been deposited for this reaction entry.</p>
    }
    const calculationsByRef = buildCalculationsByRef(calculations)
    return (
        <>
            {transitionStates.map((ts) => (
                <TransitionStateBlock
                    key={ts.transition_state_entry_ref}
                    ts={ts}
                    calculationsByRef={calculationsByRef}
                    reactants={reactants}
                    products={products}
                    reversible={reversible}
                />
            ))}
        </>
    )
}

function TransitionStateBlock({ ts, calculationsByRef, reactants, products, reversible }: {
    ts: ReactionTransitionStateInFull
    calculationsByRef: Map<string, ReactionFullCalculationEvidence>
    reactants?: ReactionEntrySpeciesParticipant[]
    products?: ReactionEntrySpeciesParticipant[]
    reversible?: boolean
}) {
    const stageEntries = STAGE_ORDER
        .filter((key) => ts.calculations[key])
        .map((key) => ({ key, slot: ts.calculations[key] }))

    const levels = deriveProductLevelsFromSourceCalculations(
        stageEntries.map(({ key, slot }) => ({
            role: key === "ts_opt" ? "opt" : key === "ts_freq" ? "freq" : key === "ts_sp" ? "sp" : "",
            level_of_theory: calculationsByRef.get(slot.calculation_ref)?.level_of_theory ?? null,
        })),
    )

    const centreRef = ts.calculations.ts_opt?.calculation_ref ?? null
    const dependencies: CalculationDependency[] = centreRef
        ? ts.dependencies
            .filter((dep) => dep.parent_calculation_ref === centreRef || dep.child_calculation_ref === centreRef)
            .map((dep) => ({
                role: dep.role,
                direction: dep.parent_calculation_ref === centreRef ? "parent" : "child",
                parent_calculation_ref: dep.parent_calculation_ref,
                child_calculation_ref: dep.child_calculation_ref,
            }))
        : []

    // Empty arrays are truthy: `reactants && products` alone renders a
    // bare "Reaction: ⇌" caption when `/full` omits `species` and
    // `ReactionEntryPage.tsx` normalises the absence to `[]` (the schema's
    // own nullable `species` -- see `api/reactionEntryApi.ts`). Gated on
    // actual content instead -- `null` (not a bare boolean) so the JSX
    // below gets a type-narrowed, non-empty pair without a `!` assertion.
    const equationParticipants = reactants?.length && products?.length ? { reactants, products } : null
    // The IRC note names a SPECIFIC calculation kind ("the IRC") -- render
    // it only when this TS entry actually has one (`ts.calculations.ts_irc`,
    // the same slot the "Calculations by stage" table above reads). 17 of
    // 34 live TS entries have only opt/freq/sp; for those, the unconditional
    // wording asserted an IRC was run and only its endpoints were
    // unrecorded, which is a stronger and false claim.
    const hasIrc = Boolean(ts.calculations.ts_irc)

    return (
        <div className="reaction-ts-block">
            <dl className="kv-list">
                <div><dt>Entry</dt><dd><Link to={`/transition-state-entries/${ts.transition_state_entry_ref}`}>{ts.transition_state_entry_ref}</Link></dd></div>
                <div><dt>Review</dt><dd><span className={reviewPillClass(ts.review.status)}>{token(ts.review.status)}</span></dd></div>
            </dl>

            <p className="t-label reaction-ts-status-label">Status</p>
            <StatusStrip status={ts.status} />

            <p className="t-label reaction-ts-stage-label">Calculations by stage</p>
            <div className="table-scroll">
                <table className="data-table" aria-label={`Calculations for ${ts.transition_state_entry_ref}`}>
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
                        {stageEntries.map(({ key, slot }) => {
                            const evidence = calculationsByRef.get(slot.calculation_ref)
                            return (
                                <tr key={key}>
                                    <td data-label="Stage">{STAGE_LABEL[key] ?? slot.type}</td>
                                    <td data-label="Level of theory">
                                        {evidence?.level_of_theory ? lotLabel(evidence.level_of_theory) : "not recorded"}
                                    </td>
                                    <td data-label="Software / workflow">
                                        {evidence?.software ? (softwareLabel(evidence.software) ?? "not recorded") : "not recorded"}
                                    </td>
                                    <td data-label="Energy"><span className="note">not served by <code>/full</code></span></td>
                                    <td data-label="Review"><span className="note">not served by <code>/full</code></span></td>
                                    <td data-label="Record"><Link to={`/calculations/${slot.calculation_ref}`}><code className="data">{slot.calculation_ref}</code></Link></td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>
            <p className="note">
                Energy and Review are not carried by <code>/full</code>'s embedded <code>calculations[]</code> summary
                — structurally absent from this view, not fabricated as "not recorded".
            </p>

            <dl className="kv-list reaction-product-levels">
                <ProductLevelsFact levels={levels} />
            </dl>

            <h3 className="t-heading-2 reaction-ts-dep-graph-heading">Calculation dependency graph</h3>
            {dependencies.length > 0 && centreRef
                ? (
                    <>
                        <div className="dep-graph-context">
                            {equationParticipants && (
                                <p className="note" data-testid="dep-graph-equation-caption">
                                    Reaction:{" "}
                                    <ReactionEquation
                                        reactants={equationParticipants.reactants}
                                        products={equationParticipants.products}
                                        reversible={reversible ?? false}
                                    />
                                </p>
                            )}
                            <p className="note" data-testid="dep-graph-subject-caption">
                                The centre node below is this transition state's own geometry optimisation, from{" "}
                                <Link to={`/transition-state-entries/${ts.transition_state_entry_ref}`}>{ts.transition_state_entry_ref}</Link>.
                            </p>
                            {hasIrc && (
                                <p className="note" data-testid="dep-graph-context-note">
                                    This graph shows calculations and how data flows between them — it does not show
                                    which species the IRC connects. That evidence, when deposited, lives on the
                                    transition-state entry.
                                </p>
                            )}
                        </div>
                        <CalculationDependencyGraph dependencies={dependencies} ownRef={centreRef} ownType="opt" centreLinked />
                    </>
                )
                : (
                    <p className="empty-projection">
                        No dependency edges are served for this TS entry's calculations.
                    </p>
                )}
        </div>
    )
}

function StatusStrip({ status }: { status: string | null | undefined }) {
    if (status === "rejected") {
        return <span className="value-pill value-pill--muted">rejected</span>
    }
    const boxes: StageFlowBox[] = ENTRY_STATUS_STAGES.map((stage) => ({
        key: stage.key,
        label: stage.label,
        content: null,
        selected: stage.key === status,
    }))
    return <StageFlow boxes={boxes} ariaLabel="Transition-state entry status" testId="ts-status-strip" />
}
