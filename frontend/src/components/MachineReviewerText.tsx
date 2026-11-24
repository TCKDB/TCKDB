import { useId } from "react"
import type { ReactNode } from "react"

/** The visible label of a block of model text. */
const MACHINE_REVIEWER_LABEL = "Written by the machine reviewer"

/**
 * A block of text the machine reviewer wrote. The model's words reach a reader only through this block, under its
 * label, as plain text children: no markup and no markdown is interpreted, so nothing in it reads as the archive's
 * own statement.
 */
export function MachineReviewerText({ children }: { children: ReactNode }) {
    const labelId = useId()
    return (
        <div role="group" aria-labelledby={labelId} className="machine-reviewer-text">
            <p id={labelId} className="machine-reviewer-label">{MACHINE_REVIEWER_LABEL}</p>
            {children}
        </div>
    )
}
