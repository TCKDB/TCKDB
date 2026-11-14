import { useMemo, type ReactNode } from "react"
import { numberRefs } from "../domain/recordNumbers"
import { RecordNumbersContext } from "../hooks/useRecordNumbers"

/** Every heading, table row and link below uses this section's one numbering; `offset` is the records before this page. */
export function RecordNumbersProvider({ refs, offset = 0, children }: { refs: readonly string[]; offset?: number; children: ReactNode }) {
    const key = refs.join("\u0000")
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` stands for the ordered contents of `refs`
    const numbers = useMemo(() => numberRefs(refs, offset), [key, offset])
    return <RecordNumbersContext.Provider value={numbers}>{children}</RecordNumbersContext.Provider>
}
