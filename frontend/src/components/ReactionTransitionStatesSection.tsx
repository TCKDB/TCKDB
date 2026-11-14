import { Link } from "react-router-dom"
import { RecordLink } from "./RecordLink"
import { calculationWords, transitionStateWords } from "../domain/recordWords"
import type { CalculationDependency } from "../api/calculationApi"
import type { ReactionFullCalculationEvidence, ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import { CalculationDependencyGraph, type CentreSubject } from "./CalculationDependencyGraph"
import { LevelLabel } from "./LevelLabel"
import { ProductLevelsFact } from "./ProductLevels"
import { ReviewChip } from "./ReviewChip"
import { deriveProductLevelsFromSourceCalculations } from "../domain/productLevels"
import { buildCalculationsByRef } from "../domain/reactionKineticsLevels"
import { softwareLabel } from "../domain/provenanceFormat"

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
 * The Transition states section: one block per
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
 * Two opt-in props of `CalculationDependencyGraph` make the centre node say what it is:
 * - `centreLinked` (opt-in on `CalculationDependencyGraph`) -- the centre
 *   node here is the TS's own `ts_opt`, a calculation the reader is NOT
 *   already viewing (unlike on `CalculationDetailPage.tsx`, where it IS
 *   the page), so it gets the same link affordance a parent/child node
 *   has.
 * - `centreSubject` (also opt-in on `CalculationDependencyGraph`) -- the
 *   node itself carries a third row naming what the optimisation is
 *   OF ("Transition state"), a real link to this TS entry so the
 *   `tse_...` ref stays reachable from the node area, never printed in
 *   prose above the graph. See that component's own docstring for the
 *   full design.
 *
 * The page's own `<h1>` already IS the reaction equation, so no
 * equation caption (or `reactants`/`products`/`reversible` props) is
 * rendered above the graph; the subject sits on the
 * node itself (`centreSubject`), so it isn't stated twice either.
 * What remains above the graph is a single, OPTIONAL note on what the
 * graph does/doesn't show about the IRC -- genuinely something the graph
 * itself cannot say, kept only when this TS entry actually has an IRC slot
 * (`ts.calculations.ts_irc`, `hasIrc` below), so the note never names
 * "the IRC" for an entry where none was run.
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
            {transitionStates.map((ts, index) => (
                <TransitionStateBlock key={ts.transition_state_entry_ref} ts={ts} words={transitionStateWords(transitionStates.length > 1 ? index + 1 : undefined)} calculationsByRef={calculationsByRef} />
            ))}
        </>
    )
}

function TransitionStateBlock({ ts, words, calculationsByRef }: {
    ts: ReactionTransitionStateInFull
    words: string
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
                <div><dt>Entry</dt><dd><RecordLink kind="transition-state" to={`/transition-state-entries/${ts.transition_state_entry_ref}`} label={words} /></dd></div>
                <div><dt>Review</dt><dd><ReviewChip status={ts.review.status} explain={false} /></dd></div>
            </dl>

            <p className="t-label reaction-ts-status-label">Status</p>
            <StatusStages status={ts.status} />

            <p className="reaction-ts-picture-link"><Link to={`/transition-state-entries/${ts.transition_state_entry_ref}`}>Picture and 3D view{" "}<span className="reaction-ts-picture-link-for">of {words.toLowerCase()}</span></Link></p>

            <p className="t-label reaction-ts-stage-label">Calculations by stage</p>
            <div className="table-scroll">
                <table className="data-table" aria-label={`Calculations for ${words.toLowerCase()}`}>
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
                                        {evidence?.level_of_theory ? <LevelLabel levelOfTheory={evidence.level_of_theory} /> : "not recorded"}
                                    </td>
                                    <td data-label="Software / workflow">
                                        {evidence?.software ? (softwareLabel(evidence.software) ?? "not recorded") : "not recorded"}
                                    </td>
                                    <td data-label="Record"><RecordLink kind="calculation" to={`/calculations/${slot.calculation_ref}`} label={calculationWords(key.replace("ts_", ""))} /></td>
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
                                Calculations only; what the IRC connects is on the transition-state entry.
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

function StatusStages({ status }: { status: string | null | undefined }) {
    if (status === "rejected") return <p className="reaction-ts-stages">rejected</p>
    return (
        <ol className="reaction-ts-stages" aria-label="Transition-state entry status" data-testid="ts-status-strip">
            {ENTRY_STATUS_STAGES.map((stage) => (
                <li key={stage.key} aria-current={stage.key === status ? "step" : undefined} className={stage.key === status ? "reaction-ts-stage--current" : undefined}>
                    {stage.label}{stage.key === status && <span className="reaction-ts-stage-mark"> (current)</span>}
                </li>
            ))}
        </ol>
    )
}
