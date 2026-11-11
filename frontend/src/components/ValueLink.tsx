import { Stored } from "./QuantityValue"
import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import "../record-blocks.css"

/** A value and its unit as a link to the record that evidences it; plain text when no page exists for it. */
export function ValueLink({ value, unit, to, full }: { value: ReactNode; unit?: string; to: string | null; full?: string }) {
    const body = (
        <>
            <span className="value-link-number"><Stored full={full}>{value}</Stored></span>
            {unit && <> <span className="u t-preserve-case">{unit}</span></>}
        </>
    )
    if (!to) return <span className="value-link value-link--plain">{body}</span>
    return <Link className="value-link" to={to}>{body}</Link>
}
