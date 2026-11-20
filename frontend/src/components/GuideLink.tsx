import { Link } from "react-router-dom"
import "../guide-link.css"

/** A guide entry and the question it answers, e.g. `{ to: "level-of-theory", question: "What is a level of theory?" }`. */
export type GuideRef = { to: string; question: string }

type GuideLinkProps =
    /** An info glyph after a kicker or heading; the question is its accessible name and hover title. */
    | { to: string; question: string; children?: never }
    /** Inline link text at the end of a sentence; it names the term and takes the sentence's font. */
    | { to: string; children: string; question?: never }

const NAMES_NOTHING = /^\s*what is this\??\s*$/i

/** A link to a guide entry that names what it explains; `to` is the entry's anchor on /guide. */
export function GuideLink({ to, question, children }: GuideLinkProps) {
    const text = question ?? children ?? ""
    if (import.meta.env.MODE === "test" && (text.trim() === "" || NAMES_NOTHING.test(text))) {
        throw new Error(`GuideLink to "${to}" must name the term it explains, not "${text}".`)
    }
    if (question !== undefined) {
        const label = `${question} Open the guide`
        return (
            <Link className="guide-link guide-link--icon" to={`/guide#${to}`} aria-label={label} title={label}>
                <svg className="guide-link-glyph" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
                    <circle cx="8" cy="8" r="6.6" fill="none" />
                    <path d="M8 7.2v4.3" fill="none" strokeLinecap="round" />
                    <circle cx="8" cy="4.9" r=".55" />
                </svg>
            </Link>
        )
    }
    return <Link className="guide-link" to={`/guide#${to}`}>{children}</Link>
}
