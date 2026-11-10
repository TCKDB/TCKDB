import { Fragment, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { recordRoute } from "../domain/recordRoute"
import "../record-blocks.css"
import { ReviewChip } from "./ReviewChip"

/**
 * The line under a value saying where it came from. Only the calculation is required; a part the
 * record does not carry is left out, never filled from another record. `compact` is the short form.
 */
export function ValueSource({ calculationRef, did, levelOfTheory, program, reviewStatus, compact = false }: {
    calculationRef: string
    did?: string | null
    levelOfTheory?: ReactNode
    program?: string | null
    reviewStatus?: string | null
    compact?: boolean
}) {
    const to = recordRoute("calculation", calculationRef)
    const ref = <code className="t-preserve-case">{calculationRef}</code>
    const link = to ? <Link to={to}>{ref}</Link> : ref
    if (compact) {
        return <span className="value-source value-source--compact"><span className="value-source-key">from</span> {link}</span>
    }
    const parts: ReactNode[] = []
    if (did) parts.push(did)
    if (levelOfTheory) parts.push(<span className="t-preserve-case">{levelOfTheory}</span>)
    if (program) parts.push(<span className="t-preserve-case">{program}</span>)
    if (reviewStatus) parts.push(<ReviewChip status={reviewStatus} explain={false} />)
    return (
        <span className="value-source">
            <span className="value-source-key">Source</span> {link}
            {parts.map((part, index) => <Fragment key={index}>{" · "}{part}</Fragment>)}
        </span>
    )
}
