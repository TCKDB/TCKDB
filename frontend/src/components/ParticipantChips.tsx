import { Fragment } from "react"

/**
 * A participant's facet chips (`participantChips`) as text where each chip is an atomic inline-block (`.chip-unit`):
 * it moves to the next line as a unit when it fits there, and wraps inside itself only when it is wider than the
 * line (a long isotopologue SMILES). The leading `·` (or the opening parenthesis) travels with the first chip rather
 * than being stranded at the end of the previous line, and the comma stays with the chip it follows. The text
 * content is the chips joined by ", ", as before.
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
                        <span className="chip-unit">{open}{chip}{close}</span>
                        {!last && " "}
                    </Fragment>
                )
            })}
        </>
    )
}
