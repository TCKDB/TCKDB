import type { ReactNode } from "react"
import { RecordLink } from "./RecordLink"
import { Link } from "react-router-dom"
import type { LineageStep } from "../domain/calculationLineage"

/**
 * The calculations this one depends on, as one list with one arrow to this calculation. The edges say each of these
 * fed this one directly; they say nothing about an order among them, so none is drawn.
 */
export function DependencyStrip({ steps, ownWords }: { steps: readonly LineageStep[]; ownWords: string }) {
    return (
        <div className="dep-strip link-list">
            <ul className="dep-strip-parents" aria-label="Calculations this one depends on">
                {steps.map((step) => (
                    <li className="dep-strip-step" key={`${step.role}-${step.ref}`}>
                        <span className="dep-strip-words">{step.words}</span>
                        <RecordLink kind="calculation" to={`/calculations/${step.ref}`} label="Calculation" context={step.words} />
                    </li>
                ))}
            </ul>
            <span className="dep-strip-arrow" aria-hidden="true">→</span>
            <div className="dep-strip-own">
                <span className="dep-strip-words">This calculation</span>
                <span>{ownWords}</span>
            </div>
        </div>
    )
}

/** The calculations that depend on this one, each named by its type and linked. */
export function UsedByList({ steps }: { steps: readonly LineageStep[] }) {
    return (
        <ul className="used-by-list link-list">
            {steps.map((step) => (
                <li key={`${step.role}-${step.ref}`}>
                    <RecordLink kind="calculation" to={`/calculations/${step.ref}`} label={step.words} />
                </li>
            ))}
        </ul>
    )
}

/** One line of "What this produced": the link to the evidence. */
export function ProducedItem({ to, children }: { to: string; children: ReactNode }) {
    return (
        <li>
            <Link to={to}>{children}</Link>
        </li>
    )
}
