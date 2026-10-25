import type { ListMeta } from "../api/listEnvelope"
import {
    displayOrderSentence,
    hiddenByDefaultNote,
    nextPageRequest,
    pageRangeSentence,
    previousPageRequest,
    type PageRequest,
} from "../domain/paging"
import "../list-pager.css"

/**
 * The paging control for one scientific list, driven by the response's own
 * `pagination` window and `request` echo. It says which records are on screen
 * ("Showing 51-100 of 120 thermo records."), lets the reader move between
 * pages, and states what the server said about the list: the display order it
 * was served in, and how many records the default visibility left out.
 *
 * It never decides which record matters. The order line is the server's own
 * echo, worded as an order for browsing; nothing here ranks, and the first
 * record of a page is not marked in any way.
 *
 * `onPage` receives a `PageRequest` built by `domain/paging.ts`, so the control
 * is indifferent to whether a page is named by an offset or, later, a cursor.
 */
export function ListPager({ meta, noun, nounPlural, onPage, busy = false, label }: {
    meta: ListMeta
    noun: string
    nounPlural?: string
    onPage: (request: PageRequest) => void
    busy?: boolean
    /** Accessible name of the region, e.g. "Thermo records paging". */
    label?: string
}) {
    const plural = nounPlural ?? `${noun}s`
    const { pagination } = meta
    const previous = previousPageRequest(pagination)
    const next = nextPageRequest(pagination)
    const paged = previous !== null || next !== null
    const hidden = hiddenByDefaultNote(meta.hiddenByDefault, plural)
    const order = displayOrderSentence(meta.sort)
    const showRange = paged || pagination.total === 0 || pagination.returned === 0

    return (
        <div className="list-pager" role="group" aria-label={label ?? `${plural} paging`} aria-busy={busy}>
            {showRange && <p className="list-pager-range">{pageRangeSentence(pagination, noun, plural)}</p>}
            {hidden && <p className="list-pager-note list-pager-hidden">{hidden}</p>}
            {order && <p className="list-pager-note list-pager-order">{order}</p>}
            {paged && (
                <div className="list-pager-buttons">
                    <button type="button" disabled={previous === null || busy} onClick={() => previous && onPage(previous)}>
                        Previous
                    </button>
                    <button type="button" disabled={next === null || busy} onClick={() => next && onPage(next)}>
                        Next
                    </button>
                </div>
            )}
        </div>
    )
}
