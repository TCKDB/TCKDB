import { Fragment, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { recordRoute } from "../domain/recordRoute"
import "../record-blocks.css"
import type { RecordKind } from "./RecordGlyph"
import { ReviewChip } from "./ReviewChip"

const RECORD_TYPE: Readonly<Record<RecordKind, string>> = {
    species: "species",
    "species-entry": "species_entry",
    "conformer-group": "conformer_group",
    calculation: "calculation",
    reaction: "reaction",
    "transition-state": "transition_state_entry",
    network: "network",
}

export type SourceLevel = { role: "geometry" | "frequency" | "energy" | (string & {}); label: string }

/**
 * The line under a value saying where it came from. Only the source record is required; a part the
 * record does not carry is left out, never filled from another record. `review` is the review of the
 * linked record. `compact` is the short form: "from <ref>".
 */
export function ValueSource({ source, levels, programs, review, compact = false }: {
    source: { kind: RecordKind; ref: string }
    levels?: readonly SourceLevel[]
    programs?: readonly string[]
    review?: string | null
    compact?: boolean
}) {
    const to = recordRoute(RECORD_TYPE[source.kind], source.ref)
    const ref = <code className="t-preserve-case">{source.ref}</code>
    const link = to ? <Link to={to}>{ref}</Link> : ref
    if (compact) {
        return <span className="value-source value-source--compact"><span className="value-source-key">from</span> {link}</span>
    }
    const parts: ReactNode[] = []
    for (const level of levels ?? []) {
        if (level.label) parts.push(<>{level.role} <span className="t-preserve-case">{level.label}</span></>)
    }
    const shownPrograms = (programs ?? []).filter(Boolean)
    if (shownPrograms.length > 0) parts.push(<span className="t-preserve-case">{shownPrograms.join(", ")}</span>)
    if (review) parts.push(<>Review of this record: <ReviewChip status={review} explain={false} /></>)
    return (
        <span className="value-source">
            <span className="value-source-key">Source</span> {link}
            {parts.map((part, index) => <Fragment key={index}>{" · "}{part}</Fragment>)}
        </span>
    )
}
