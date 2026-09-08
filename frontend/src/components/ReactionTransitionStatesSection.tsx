import { Link } from "react-router-dom"
import type { CalculationDependency } from "../api/calculationApi"
import type { ReactionFullCalculationEvidence, ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import { lotLabel } from "../api/scientificSchemas"
import { CalculationDependencyGraph, type CentreSubject } from "./CalculationDependencyGraph"
import { ProductLevelsFact } from "./ProductLevels"
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
 * Owner complaint, round 1 (2026-09): "in reaction we see Optimisation but
 * no idea what the optimisation is of? and its not clickable link to the
 * calc?". Fixed two ways:
 * - `centreLinked` (opt-in on `CalculationDependencyGraph`) -- the centre
 *   node here is the TS's own `ts_opt`, a calculation the reader is NOT
 *   already viewing (unlike on `CalculationDetailPage.tsx`, where it IS
 *   the page), so it gets the same link affordance a parent/child node
 *   has.
 * - `centreSubject` (also opt-in on `CalculationDependencyGraph`) -- the
 *   node itself now carries a third row naming what the optimisation is
 *   OF ("Transition state"), a real link to this TS entry so the
 *   `tse_...` ref stays reachable from the node area, never printed in
 *   prose above the graph. See that component's own docstring for the
 *   full design.
 *
 * Owner complaint, round 2 (2026-09), on an earlier version of this fix
 * that put the subject and the reaction equation in a caption block ABOVE
 * the graph instead: "This does need repeating what reaction since the
 * top of the page says which reaction" -- the page's own `<h1>` already IS
 * that equation, a few screens up, on the one page this graph ever renders
 * on. The equation caption (and the `reactants`/`products`/`reversible`
 * props that existed only to feed it) is gone; the subject moved onto the
 * node itself per the round-1 fix above, so it isn't stated twice either.
 * What remains above the graph is a single, OPTIONAL note on what the
 * graph does/doesn't show about the IRC -- genuinely something the graph
 * itself cannot say, kept only when this TS entry actually has an IRC slot
 * (`ts.calculations.ts_irc`, `hasIrc` below) -- 17 of 34 live TS entries
 * have only opt/freq/sp, and an unconditional version of this note used to
 * name "the IRC" for those too, asserting one was run when none was.
 */
export function ReactionTransitionStatesSection({ transitionStates, calculations }: {
    transitionStates: ReactionTransitionStateInFull[]
    calculations: ReactionFullCalculationEvidence[] | null | undefined
}) {
    if (transitionStates.length === 0) {
        return <p className="empty-projection">No transition-state entry has been deposited for this reaction entry.</p>
    }
    const calculationsByRef = buildCalculationsByRef(calculations)
    return (
        <>
            {transitionStates.map((ts) => (
                <TransitionStateBlock key={ts.transition_state_entry_ref} ts={ts} calculationsByRef={calculationsByRef} />
            ))}
        </>
    )
}

function TransitionStateBlock({ ts, calculationsByRef }: {
    ts: ReactionTransitionStateInFull
    calculationsByRef: Map<string, ReactionFullCalculationEvidence>
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

    // "An optimisation of WHAT" -- see this component's own docstring.
    // Kept short and non-repeating (the ref itself lives in the link's
    // own accessible name, not printed a second time here).
    const centreSubject: CentreSubject = {
        label: "Transition state",
        ref: ts.transition_state_entry_ref,
        href: `/transition-state-entries/${ts.transition_state_entry_ref}`,
    }

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
                                    <td data-label="Record"><Link to={`/calculations/${slot.calculation_ref}`}><code className="data">{slot.calculation_ref}</code></Link></td>
                                </tr>
                            )
                        })}
                    </tbody>
                </table>
            </div>

            <dl className="kv-list reaction-product-levels">
                <ProductLevelsFact levels={levels} />
            </dl>

            <h3 className="t-heading-2 reaction-ts-dep-graph-heading">Calculation dependency graph</h3>
            {dependencies.length > 0 && centreRef
                ? (
                    <>
                        {hasIrc && (
                            <p className="note dep-graph-irc-note" data-testid="dep-graph-context-note">
                                This graph shows calculations and how data flows between them — it does not show
                                which species the IRC connects. That evidence, when deposited, lives on the
                                transition-state entry.
                            </p>
                        )}
                        <CalculationDependencyGraph
                            dependencies={dependencies}
                            ownRef={centreRef}
                            ownType="opt"
                            centreLinked
                            centreSubject={centreSubject}
                        />
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
