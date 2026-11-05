import { Fragment } from "react"

/**
 * A participant's facet chips (`participantChips`) as text that wraps only BETWEEN chips: each chip is a
 * no-break span, so `state X ³Σg⁻` never splits across lines at a narrow width. The leading `·` (or the opening
 * parenthesis) travels with the first chip rather than being stranded at the end of the previous line, and the
 * comma stays with the chip it follows. The text content is the chips joined by ", ", as before.
 */
export function ParticipantChips({ chips, style = "dot" }: { chips: readonly string[]; style?: "dot" | "paren" }) {
    if (chips.length === 0) return null
    return (
        <>
            {" "}
            {chips.map((chip, index) => {
                const last = index === chips.length - 1
                const open = index === 0 ? (style === "dot" ? "· " : "(") : ""
                const close = last ? (style === "paren" ? ")" : "") : ","
                return (
                    <Fragment key={index}>
                        <span className="chip-nowrap">{open}{chip}{close}</span>
                        {!last && " "}
                    </Fragment>
                )
            })}
        </>
    )
}
