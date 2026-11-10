import { Fragment, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { calculationDid } from "../domain/calculationTypeFormat"
import { resolveRecordLocation } from "../domain/recordRoute"
import "../record-blocks.css"
import { numberAnchor, type NumberKind } from "../domain/entryNumbers"
import { ReviewChip } from "./ReviewChip"

/** Record types whose own row on their container's page carries an id. */
const ANCHORED: ReadonlySet<string> = new Set(["thermo", "statmech", "transport"])

export type SourceLevel = { role: "geometry" | "frequency" | "energy"; label: string }

export type ValueSourceRecord = {
    /** The API's record type, e.g. `calculation`, `thermo`, `statmech`. */
    recordType: string
    ref: string
    /** The record that shows it, for a type with no page of its own. */
    container?: { recordType: string; ref: string }
    /** A link on the page the reader is already on; it replaces the resolved one. */
    to?: string
}

/**
 * The line under a value saying where it came from. Only the source record is required; a part the
 * record does not carry is left out, never filled from another record. The link goes to the record's
 * own page, else its container's, else the ref is plain text. `review` is the review of the linked
 * record. `compact` is the short form: "from <ref>".
 */
export function ValueSource({ source, did, levels, programs, review, compact = false }: {
    source: ValueSourceRecord
    /** A calculation type code (`opt`, `freq`, `sp`...); shown as TCKDB wording, omitted when unknown. */
    did?: string | null
    levels?: readonly SourceLevel[]
    programs?: readonly string[]
    review?: string | null
    compact?: boolean
}) {
    const location = resolveRecordLocation(source.recordType, source.ref, source.container?.recordType ?? null, source.container?.ref ?? null)
    const ref = <code className="t-preserve-case">{source.ref}</code>
    const target = source.to ?? (location.kind === "container" && source.container?.recordType === "species_entry" && ANCHORED.has(source.recordType) ? `${location.href}#${numberAnchor(source.recordType as NumberKind, source.ref)}` : location.kind === "record" || location.kind === "container" ? location.href : null)
    const link = target ? <Link to={target}>{ref}</Link> : ref
    if (compact) {
        return <span className="value-source value-source--compact"><span className="value-source-key">from</span> {link}</span>
    }
    const parts: ReactNode[] = []
    const didWords = did ? calculationDid(did) : null
    if (didWords) parts.push(didWords)
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
