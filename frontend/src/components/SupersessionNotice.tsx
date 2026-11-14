import { RECORD_KIND_NOUN } from "../domain/recordWords"
import { useRecordNumbers } from "../hooks/useRecordNumbers"

type Superseded = "thermo" | "statmech" | "transport"

/**
 * Correction notice for a superseded record, shown beside its own data and
 * never instead of it. The replacing record is named by the number the page
 * gives it and linked to its card when it is on the page, else "a newer
 * record"; its ref is under References on its own card.
 */
export function SupersessionNotice({ kind, supersession }: {
    kind: Superseded
    supersession: {
        superseded_by: string
        current: string
        reason: string
        superseded_at: string
        chain_length: number
    }
}) {
    const numbers = useRecordNumbers()
    const supersededDate = supersession.superseded_at.slice(0, 10)
    const name = (ref: string) => {
        const position = numbers.get(ref)
        return position
            ? <a href={`#${kind}-heading-${ref}`}>{`${RECORD_KIND_NOUN[kind]} ${position}`}</a>
            : "a newer record"
    }
    return (
        <div className="supersession-notice">
            <strong>Superseded</strong>
            <p>
                This record was replaced by {name(supersession.superseded_by)}.
                {supersession.chain_length > 1 && (
                    <> The current record in this chain is {name(supersession.current)} ({supersession.chain_length} corrections since this one).</>
                )}
                {" "}Reason: {supersession.reason}. Superseded {supersededDate}.
            </p>
        </div>
    )
}
