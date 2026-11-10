import type { HiddenByDefault, OrderEcho, Pagination } from "./listEnvelope"
import { dedupedFetch, type Subscription } from "./requestCache"
import { loadEntryStatmech, type StatmechRecord } from "./statmechApi"
import { loadEntryThermo, type ThermoRecord } from "./thermoApi"
import { loadEntryTransportWithSources, type TransportRecord } from "./transportApi"
import { MAX_PAGES } from "../domain/entryNumbers"
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

type Page<T> = { records: T[]; pagination: Pagination; request?: { sort?: OrderEcho | null; hidden_by_default?: HiddenByDefault | null } | null }
type Loader<T> = (ref: string, signal?: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<Page<T>>

/** What a walk hands back to its caller so it can show a wait and let go of its requests. */
export type WalkControl = {
    onWaiting: (retryAfterSeconds: number) => void
    subscriptions: Subscription<unknown>[]
}

/**
 * Walks one list page by page. The first page is asked for by the plain ref, and later pages by the window the server
 * advertised, which is exactly how the tabs ask, so a page a tab already holds is not fetched twice.
 */
async function walk<T>(entryRef: string, load: Loader<T>, control: WalkControl): Promise<AllPages<T>> {
    const records: T[] = []
    let request: PageRequest | null = null
    let first: Page<T> | null = null
    let total = 0
    let pages = 0
    do {
        const key: string = request ? pagedRef(entryRef, request) : entryRef
        const subscription = dedupedFetch(load, key, (signal, onRateLimited) => load(key, signal, onRateLimited))
        control.subscriptions.push(subscription as Subscription<unknown>)
        subscription.onWaiting(control.onWaiting)
        const page: Page<T> = await subscription.promise
        first ??= page
        records.push(...page.records)
        total = page.pagination.total
        pages += 1
        request = nextPageRequest(page.pagination)
    } while (request && pages < MAX_PAGES)
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
