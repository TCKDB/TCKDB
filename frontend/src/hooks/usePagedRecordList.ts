import { useEffect, useRef, useState } from "react"
import { useLocation } from "react-router-dom"
import { walk, type Loader, type Page, type WalkControl } from "../api/entryNumbersApi"
import type { NumberKind } from "../domain/entryNumbers"
import { usePagedList } from "./usePagedList"

/** The record ref a `#<kind>-heading-<ref>` fragment names, with a grouped card's suffix removed; `null` for any other fragment. */
export function linkedRecordRef(hash: string, kind: NumberKind): string | null {
    const prefix = `#${kind}-heading-`
    if (!hash.startsWith(prefix)) return null
    const ref = decodeURIComponent(hash.slice(prefix.length)).replace(/-group$/, "")
    return ref || null
}

type PageLoader<R, P extends Page<R>> = Loader<R> & ((ref: string, signal: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<P>)

const NOT_IN_LIST = "The linked record isn't in this list."
const NOT_IN_CAPPED_LIST = "The linked record isn't among the first records of this list that were searched."

/**
 * A paged tab list that opens on the page holding the record the fragment names. When that record is not on the page
 * on screen, the entry's list is read in the same order (the first page is already held) and the tab moves to the page
 * with the record. A record the list does not hold leaves the page where it is and `linkedNote` says so.
 */
export function usePagedRecordList<R, P extends Page<R>>(
    entryRef: string,
    kind: NumberKind,
    load: PageLoader<R, P>,
    refOf: (record: R) => string,
) {
    const paged = usePagedList(entryRef, load)
    const { hash } = useLocation()
    const target = linkedRecordRef(hash, kind)
    const key = `${entryRef}${hash}`
    const page = paged.state.status === "ready" ? paged.state.record : null
    const searched = useRef<string | null>(null)
    const [missing, setMissing] = useState<{ key: string; note: string } | null>(null)
    const { goTo } = paged
    const offset = page?.pagination.offset
    const limit = page?.pagination.limit
    const onPage = page ? page.records.some((record) => refOf(record) === target) : true

    useEffect(() => {
        if (!target || onPage || offset === undefined || limit === undefined || searched.current === key) return
        let mounted = true
        const control: WalkControl = { subscriptions: [], onWaiting: () => {}, abandoned: () => !mounted }
        walk(entryRef, load, control)
            .then((all) => {
                if (!mounted) return
                searched.current = key
                const index = all.records.findIndex((record) => refOf(record) === target)
                if (index < 0) {
                    setMissing({ key, note: all.capped ? NOT_IN_CAPPED_LIST : NOT_IN_LIST })
                    return
                }
                const start = Math.floor(index / limit) * limit
                if (start !== offset) goTo({ offset: start, limit })
            })
            .catch(() => {})
        return () => {
            mounted = false
            for (const subscription of control.subscriptions) subscription.unsubscribe()
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` and `refOf` are module-level functions that never change
    }, [entryRef, hash, key, target, onPage, offset, limit, goTo])

    return { ...paged, linkedNote: missing && missing.key === key ? missing.note : null }
}
