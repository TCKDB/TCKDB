import { useState } from "react"
import { listMetaOf, type ListMeta, type ListRequestEcho, type Pagination } from "../api/listEnvelope"
import { pagedRef, type PageRequest } from "../domain/paging"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

type PagedResponse = { pagination: Pagination; request?: ListRequestEcho | null }

const envelopeMeta = (response: PagedResponse): ListMeta => listMetaOf(response)

/**
 * An entry-scoped list the reader can page through. Wraps `useScientificRecord`
 * with a page position kept here, so a section needs no paging state of its
 * own: `state` is the usual record state for the CURRENT page, `listKey` names
 * that page (the key a section's own on-demand hooks must use, so their data
 * is never read across pages), `meta` is what `ListPager` reads, and `goTo`
 * moves.
 *
 * The position is a `PageRequest` (`domain/paging.ts`), never a bare offset:
 * moving to a cursor-named page changes that module, not this hook.
 *
 * A change of `ref` returns to the first page, so the page position of one
 * entry is never applied to another.
 */
export function usePagedList<T>(
    ref: string,
    load: (ref: string, signal: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<T>,
    /** Reads the paging window and server notes off a loaded page; the default reads a standard list envelope. */
    metaOf: (page: T) => ListMeta = envelopeMeta as unknown as (page: T) => ListMeta,
): {
    state: ScientificRecordState<T>
    listKey: string
    meta: ListMeta | null
    goTo: (request: PageRequest) => void
} {
    const [position, setPosition] = useState<{ ref: string; request: PageRequest | null }>({ ref, request: null })
    const request = position.ref === ref ? position.request : null
    const listKey = request ? pagedRef(ref, request) : ref
    const state = useScientificRecord(listKey, load)
    const meta = state.status === "ready" ? metaOf(state.record) : null
    return { state, listKey, meta, goTo: (next) => setPosition({ ref, request: next }) }
}
