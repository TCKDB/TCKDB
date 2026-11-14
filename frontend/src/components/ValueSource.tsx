import { Fragment, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { calculationDid } from "../domain/calculationTypeFormat"
import { levelKey, type LevelFields } from "../domain/levelLabel"
import { calculationWords, RECORD_KIND_NOUN, type RecordKind } from "../domain/recordWords"
import { LevelDetails, LevelLabel } from "./LevelLabel"
import { resolveRecordLocation } from "../domain/recordRoute"
import "../record-blocks.css"
import { numberAnchor, type NumberKind } from "../domain/entryNumbers"
import { ReviewChip } from "./ReviewChip"

/** Record types whose own row on their container's page carries an id. */
const ANCHORED: ReadonlySet<string> = new Set(["thermo", "statmech", "transport"])

export type SourceLevel = {
    role: string
    label: string
    /** The structured level the label was built from, when the view holds it. */
    level?: LevelFields
}

const SOURCE_KIND: Readonly<Record<string, RecordKind>> = {
    calculation: "calculation", thermo: "thermo", statmech: "statmech", transport: "transport", kinetics: "kinetics",
}

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
export function ValueSource({ source, did, position, levels, programs, review, compact = false, showDetails = false, own = false }: {
    source: ValueSourceRecord
    /** A calculation type code (`opt`, `freq`, `sp`...); shown as TCKDB wording, omitted when unknown. */
    did?: string | null
    /** The record's number among records of its kind on the page ("Thermo record 2"); omitted when it is the only one. */
    position?: number
    levels?: readonly SourceLevel[]
    programs?: readonly string[]
    review?: string | null
    compact?: boolean
    /** Put each level's qualifiers (as a "details" disclosure) after the line. Leave off when the line sits in a paragraph. */
    showDetails?: boolean
    /** The line sits on the source record's own card: the record is not named, only what it was made from. */
    own?: boolean
}) {
    const location = resolveRecordLocation(source.recordType, source.ref, source.container?.recordType ?? null, source.container?.ref ?? null)
    const kind = SOURCE_KIND[source.recordType]
    const words = source.recordType === "calculation" ? calculationWords(did) : kind ? RECORD_KIND_NOUN[kind] : "Record"
    const named = position ? `${words} ${position}` : words
    const ref = <span className="t-preserve-case">{named}</span>
    const target = source.to ?? (location.kind === "container" && source.container?.recordType === "species_entry" && ANCHORED.has(source.recordType) ? `${location.href}#${numberAnchor(source.recordType as NumberKind, source.ref)}` : location.kind === "record" || location.kind === "container" ? location.href : null)
    const link = target ? <Link to={target} aria-label={`Source: ${named}`}>{ref}</Link> : ref
    if (compact) {
        return <span className="value-source value-source--compact"><span className="value-source-key">from</span> {link}</span>
    }
    const parts: ReactNode[] = []
    const didWords = did ? calculationDid(did) : null
    if (didWords) parts.push(didWords)
    const labelled = (levels ?? []).filter((level) => level.label)
    if (labelled.length > 1 && labelled.every((level) => level.label === labelled[0].label)) {
        parts.push(<>{labelled.map((level) => level.role).join(", ")} at <SourceLevelLabel level={labelled[0]} /></>)
    } else {
        for (const level of labelled) parts.push(<>{level.role} <SourceLevelLabel level={level} /></>)
    }
    const shownPrograms = (programs ?? []).filter(Boolean)
    if (shownPrograms.length > 0) parts.push(<span className="t-preserve-case">{shownPrograms.join(", ")}</span>)
    if (review) parts.push(<>Review of this record: <ReviewChip status={review} explain={false} /></>)
    const detailed = showDetails ? [...new Map(labelled.flatMap((level) => (level.level ? [[levelKey(level.level), level.level] as const] : []))).values()] : []
    return (
        <>
            <span className="value-source">
                {own
                    ? parts.map((part, index) => <Fragment key={index}>{index > 0 && " · "}{part}</Fragment>)
                    : <><span className="value-source-key">Source</span> {link}{parts.map((part, index) => <Fragment key={index}>{" · "}{part}</Fragment>)}</>}
            </span>
            {detailed.map((level) => <LevelDetails key={levelKey(level)} levelOfTheory={level} />)}
        </>
    )
}

function SourceLevelLabel({ level }: { level: SourceLevel }) {
    return level.level ? <LevelLabel levelOfTheory={level.level} plain /> : <span className="t-preserve-case">{level.label}</span>
}
