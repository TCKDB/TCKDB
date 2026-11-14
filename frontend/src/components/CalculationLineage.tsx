import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import type { LineageStep } from "../domain/calculationLineage"

/**
 * The calculations this one depends on, as one ordered row: each step in the order it ran, then this calculation.
 * The words name what each step is; the ref is the link. Tab visits the links in that same order.
 */
export function DependencyStrip({ steps, ownWords }: { steps: readonly LineageStep[]; ownWords: string }) {
    return (
        <ol className="dep-strip" aria-label="Calculations this one depends on, in the order they ran">
            {steps.map((step, index) => (
                <li className="dep-strip-step" key={`${step.role}-${step.ref}`}>
                    {index > 0 && <span className="dep-strip-arrow" aria-hidden="true">→</span>}
                    <span className="dep-strip-body">
                        <span className="dep-strip-words">{step.words}</span>
                        <Link to={`/calculations/${step.ref}`}><code className="data">{step.ref}</code></Link>
                    </span>
                </li>
            ))}
            <li className="dep-strip-step dep-strip-step--own" aria-current="step">
                <span className="dep-strip-arrow" aria-hidden="true">→</span>
                <span className="dep-strip-body">
                    <span className="dep-strip-words">This calculation</span>
                    <span>{ownWords}</span>
                </span>
            </li>
        </ol>
    )
}

/** One line of "What this produced": optional plain words, then the link to the evidence. */
export function ProducedItem({ lead, to, children }: { lead?: string; to: string; children: ReactNode }) {
    return (
        <li>
            {lead}<Link to={to}>{children}</Link>
        </li>
    )
}
