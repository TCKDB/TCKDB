import type { HiddenByDefault, OrderEcho, Pagination } from "./listEnvelope"
import { dedupedFetch, type Subscription } from "./requestCache"
import { ScientificApiError } from "./scientificTransport"
import { loadEntryStatmech, type StatmechRecord } from "./statmechApi"
import { loadEntryThermo, type ThermoRecord } from "./thermoApi"
import { loadEntryTransportWithSources, type TransportRecord } from "./transportApi"
import { LATER_PAGE_LIMIT, MAX_RECORDS } from "../domain/entryNumbers"
import { nextPageRequest, pagedRef, type PageRequest } from "../domain/paging"

/** Every record of one list the cap allowed, and what the archive said about the list. */
export type AllPages<T> = {
    records: T[]
    total: number
    /** True when the list is longer than the cap: `records` holds only the first ones. */
    capped: boolean
    sort: OrderEcho | null
    hiddenByDefault: HiddenByDefault | null
}

export type Page<T> = { records: T[]; pagination: Pagination; request?: { sort?: OrderEcho | null; hidden_by_default?: HiddenByDefault | null } | null }
export type Loader<T> = (ref: string, signal?: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<Page<T>>

/** What a walk hands back to its caller so it can show a wait, stop early and let go of its requests. */
export type WalkControl = {
    onWaiting: (retryAfterSeconds: number) => void
    subscriptions: Subscription<unknown>[]
    /** True once the caller has gone; checked before each page is asked for. */
    abandoned: () => boolean
}

/**
 * Walks one list. The first page is asked for by the plain ref, exactly as the tabs ask, so a page a tab already holds
 * is not fetched twice. Later pages ask for `LATER_PAGE_LIMIT` records at a time, or for the first page's own window
 * when the server refuses that size, so 1000 records cost 1 + 5 requests.
 */
export async function walk<T>(entryRef: string, load: Loader<T>, control: WalkControl): Promise<AllPages<T>> {
    const records: T[] = []
    let request: PageRequest | null = null
    let first: Page<T> | null = null
    let total = 0
    let limit = LATER_PAGE_LIMIT
    do {
        if (control.abandoned()) throw new DOMException("The numbers were no longer wanted.", "AbortError")
        const key: string = request ? pagedRef(entryRef, request) : entryRef
        const subscription = dedupedFetch(load, key, (signal, onRateLimited) => load(key, signal, onRateLimited))
        control.subscriptions.push(subscription as Subscription<unknown>)
        subscription.onWaiting(control.onWaiting)
        let page: Page<T>
        try {
            page = await subscription.promise
        } catch (error) {
            const ownLimit = first?.pagination.limit
            if (request && ownLimit !== undefined && limit !== ownLimit && error instanceof ScientificApiError && error.status === 422) {
                limit = ownLimit
                request = { offset: request.offset, limit: Math.min(limit, MAX_RECORDS - records.length) }
                continue
            }
            throw error
        }
        first ??= page
        records.push(...page.records)
        total = page.pagination.total
        const next = nextPageRequest(page.pagination)
        request = next ? { offset: next.offset, limit: Math.min(limit, MAX_RECORDS - records.length) } : null
    } while (request && records.length < MAX_RECORDS)
    return {
        records,
        total,
        capped: request !== null,
        sort: first?.request?.sort ?? null,
        hiddenByDefault: first?.request?.hidden_by_default ?? null,
    }
}

export type EntryNumbers = {
    thermo: AllPages<ThermoRecord>
    statmech: AllPages<StatmechRecord>
    transport: AllPages<TransportRecord>
}

/** Every thermo, statmech and transport record of one species entry, each list walked up to the cap. */
export async function loadEntryNumbers(entryRef: string, control: WalkControl): Promise<EntryNumbers> {
    const [thermo, statmech, transport] = await Promise.all([
        walk(entryRef, loadEntryThermo, control),
        walk(entryRef, loadEntryStatmech, control),
        walk(entryRef, loadEntryTransportWithSources, control),
    ])
    return { thermo, statmech, transport }
}
