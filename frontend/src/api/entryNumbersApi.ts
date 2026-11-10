import type { HiddenByDefault, OrderEcho, Pagination } from "./listEnvelope"
import { loadEntryStatmech, type StatmechRecord } from "./statmechApi"
import { loadEntryThermoPlain, type ThermoRecord } from "./thermoApi"
import { loadEntryTransport, type TransportRecord } from "./transportApi"
import { MAX_PAGES, PAGE_LIMIT } from "../domain/entryNumbers"
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

async function walk<T>(entryRef: string, load: (ref: string, signal?: AbortSignal) => Promise<Page<T>>, signal?: AbortSignal): Promise<AllPages<T>> {
    const records: T[] = []
    let request: PageRequest | null = { offset: 0, limit: PAGE_LIMIT }
    let first: Page<T> | null = null
    let total = 0
    let pages = 0
    while (request && pages < MAX_PAGES) {
        const page: Page<T> = await load(pagedRef(entryRef, request), signal)
        first ??= page
        records.push(...page.records)
        total = page.pagination.total
        pages += 1
        request = nextPageRequest(page.pagination)
    }
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

/** Every thermo, statmech and transport record of one species entry, each list walked page by page up to the cap. */
export async function loadEntryNumbers(entryRef: string, signal?: AbortSignal): Promise<EntryNumbers> {
    const [thermo, statmech, transport] = await Promise.all([
        walk(entryRef, loadEntryThermoPlain, signal),
        walk(entryRef, loadEntryStatmech, signal),
        walk(entryRef, loadEntryTransport, signal),
    ])
    return { thermo, statmech, transport }
}
