import type { HiddenByDefault, OrderEcho, Pagination } from "../api/listEnvelope"

// ---------------------------------------------------------------------------
// Paging a scientific list. Today a page is named by an offset and a limit;
// the server's list routes may later add a keyset cursor. EVERYTHING that
// decides which page to ask for next lives in this file: `PageRequest` is the
// one type a loader receives, and `nextPageRequest` / `previousPageRequest` /
// `firstPageRequest` / `pageQuery` are the only functions that build or send
// one. A cursor (`{ cursor: string; limit: number }` from a response's
// `next_cursor`) is a change to those definitions, not to every page that
// shows a list.
// ---------------------------------------------------------------------------

export type PageRequest = { offset: number; limit: number }

export const DEFAULT_PAGE_SIZE = 50

export function firstPageRequest(limit: number = DEFAULT_PAGE_SIZE): PageRequest {
    return { offset: 0, limit }
}

/** The request for the page after the one `page` describes, or `null` when it was the last. */
export function nextPageRequest(page: Pagination): PageRequest | null {
    const end = page.offset + page.returned
    if (page.returned === 0 || end >= page.total) return null
    return { offset: end, limit: page.limit }
}

/** The request for the page before the one `page` describes, or `null` when it was the first. */
export function previousPageRequest(page: Pagination): PageRequest | null {
    if (page.offset <= 0) return null
    return { offset: Math.max(0, page.offset - page.limit), limit: page.limit }
}

/** Query parameters for a request. The only place a `PageRequest` becomes wire text. */
export function pageQuery(request: PageRequest): URLSearchParams {
    return new URLSearchParams({ offset: String(request.offset), limit: String(request.limit) })
}

/** Cache key text for one entry's one page: `ref#offset:limit`. */
export function pagedRef(ref: string, request: PageRequest): string {
    return `${ref}#${request.offset}:${request.limit}`
}

/** A plain ref (no `#`) names the list as the server pages it by default: `request` is `null` and nothing is sent. */
export function parsePagedRef(value: string): { ref: string; request: PageRequest | null } {
    const at = value.lastIndexOf("#")
    if (at < 0) return { ref: value, request: null }
    const [offset, limit] = value.slice(at + 1).split(":").map(Number)
    if (!Number.isInteger(offset) || !Number.isInteger(limit) || limit < 1 || offset < 0) {
        return { ref: value, request: null }
    }
    return { ref: value.slice(0, at), request: { offset, limit } }
}

/** "Showing 51–100 of 120 thermo records." / "No thermo records." / the whole list when it fits one page. */
export function pageRangeSentence(page: Pagination, noun: string, nounPlural: string = `${noun}s`): string {
    if (page.total === 0) return `No ${nounPlural}.`
    if (page.returned === 0) return `No ${nounPlural} at this position; ${page.total} in all.`
    const first = page.offset + 1
    const last = page.offset + page.returned
    if (first === 1 && last === page.total) return `${page.total} ${page.total === 1 ? noun : nounPlural}.`
    return `Showing ${first}–${last} of ${page.total} ${nounPlural}.`
}

/** Plain-language note for records the default visibility hid, or `null` when it hid none. */
export function hiddenByDefaultNote(hidden: HiddenByDefault | null | undefined, nounPlural: string): string | null {
    if (!hidden) return null
    const entries = Object.entries(hidden.counts).filter(([, count]) => count > 0)
    if (entries.length === 0) return null
    const parts = entries.map(([status, count]) => `${count} ${status.replaceAll("_", " ")}`)
    const counted = hidden.counted && hidden.counted !== "records" ? hidden.counted.replaceAll("_", " ") : nounPlural
    return `Not listed by default: ${parts.join(" and ")} ${counted}.`
}

function isTieBreaker(key: { key: string; meaning: string }): boolean {
    return key.key === "id" || key.key.endsWith("_id") || /^unique tie-breaker/.test(key.meaning)
}

/**
 * Plain wording for the keys every default browse order uses, built from the echo's key and direction. A key not named
 * here is worded from the server's own meaning.
 */
const KNOWN_KEY_WORDS: Record<string, (direction: string) => string> = {
    review_rank: (direction) => (direction === "asc" ? "approved first" : "approved last"),
    created_at: (direction) => (direction === "desc" ? "newest deposited first" : "oldest deposited first"),
}

/** One key in words: plain wording for a known key, else the server's own meaning (its first clause) with the direction added when it does not say it. */
function describeOrderKey(key: { key: string; meaning: string; direction: string }): string {
    const known = KNOWN_KEY_WORDS[key.key]
    if (known) return known(key.direction)
    const base = key.meaning.split(";")[0].trim()
    if (/\b(first|last)\b/.test(base)) return base
    return `${base} (${key.direction === "desc" ? "descending" : "ascending"})`
}

/**
 * The order statement, from the server's own echo. A DEFAULT order is worded as an order for browsing, not a
 * preference. An order the request itself asked for (`requested_by`, e.g. a named ranking) is reported as asked for and
 * is not called "not a ranking", because it is one. Internal tie-break keys are not named.
 */
export function displayOrderSentence(sort: OrderEcho | null | undefined): string | null {
    if (!sort || sort.keys.length === 0) return null
    const keys = sort.keys.filter((key) => !isTieBreaker(key))
    if (keys.length === 0) return null
    const words = keys.map(describeOrderKey).join(", then ")
    if (sort.requested_by) return `Order requested (${sort.requested_by}): ${words}.`
    return `Display order: ${words}. An order for browsing, not a ranking or a recommendation.`
}

/** The list is longer than the window on screen, or the window is not the first: what is shown is not "every" record. */
export function isPartialWindow(page: Pagination): boolean {
    return page.total > page.returned || page.offset > 0
}

/** "Every" when the window holds the whole list, else "The N" with the window's own size: for sentences about what is on screen. */
export function everyOrThisPage(page: Pagination | null | undefined, noun: string): string {
    if (!page || !isPartialWindow(page)) return `Every ${noun} deposited for this entry`
    return `The ${page.returned} ${noun}${page.returned === 1 ? "" : "s"} on this page (of ${page.total} deposited for this entry)`
}
