import type { ReactNode } from "react"
import { Link } from "react-router-dom"
import { looksLikeRef, RECORD_KIND_NOUN, type RecordKind } from "../domain/recordWords"
import "../record-link.css"

/**
 * A link to a record, read as words. The visible text is chemistry or words
 * (never the ref); `label` is the same words as plain text and, with the
 * kind, becomes the accessible name, so rows of one table are told apart.
 * `detail` is an optional second line beneath (a model kind, a review word).
 * A label that is itself a public ref is replaced by the kind's noun.
 */
export function RecordLink({ to, kind, label, children, detail, hash, context }: {
    to: string
    kind: RecordKind
    /** The words as plain text. */
    label: string
    /** The words as shown, when they are richer than text (a SMILES with its formula). Defaults to `label`. */
    children?: ReactNode
    detail?: ReactNode
    hash?: string
    /** What tells this link from its siblings when the words repeat (a row's role, a term number); added to the accessible name only. */
    context?: string
}) {
    const noun = RECORD_KIND_NOUN[kind]
    const words = looksLikeRef(label) ? noun : label
    const base = words === noun || words.startsWith(`${noun} `) ? words : `${noun}: ${words}`
    const name = context ? `${base}, ${context}` : base
    return (
        <>
            <Link className="record-link" to={hash ? `${to}#${hash}` : to} aria-label={name}>{children ?? words}</Link>
            {detail && <span className="record-link-detail">{detail}</span>}
        </>
    )
}

/** Participants of an equation, each on its own line. */
export function ParticipantLines({ children }: { children: ReactNode[] }) {
    return <ul className="participant-lines">{children.map((child, index) => <li key={index}>{child}</li>)}</ul>
}
