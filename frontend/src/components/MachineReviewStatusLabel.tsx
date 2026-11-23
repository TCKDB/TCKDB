import { statusClassName, wordsFor } from "../domain/machineReviewWords"

/**
 * A machine-review status as a word, never as colour alone. `withMeaning` adds what the status does and does not say, for
 * screen readers, so the explanation is not carried by a tooltip.
 */
export function MachineReviewStatusLabel({ status, withMeaning = false }: { status: string; withMeaning?: boolean }) {
    const { label, meaning, known } = wordsFor("status", status)
    const cls = statusClassName(status)
    return (
        <>
            {known ? <span className={cls ?? undefined}>{label}</span> : <code>{status}</code>}
            {withMeaning && meaning !== "" && <span className="admin-visually-hidden">. {meaning}</span>}
        </>
    )
}
