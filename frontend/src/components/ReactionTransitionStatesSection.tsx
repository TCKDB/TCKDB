import { Link } from "react-router-dom"
import type { CalculationDependency } from "../api/calculationApi"
import type { ReactionFullCalculationEvidence, ReactionTransitionStateInFull } from "../api/reactionEntryApi"
import { lotLabel } from "../api/scientificSchemas"
import { CalculationDependencyGraph } from "./CalculationDependencyGraph"
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
                ? <CalculationDependencyGraph dependencies={dependencies} ownRef={centreRef} ownType="opt" />
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
