import { useEffect, useState } from "react"
import { dedupedFetch } from "../api/requestCache"
import {
    conformerKey,
    loadEntryConformerByRef,
    loadEntryConformerLabels,
    type ConformerProjection,
} from "../api/speciesEntryApi"

export type ConformerByRefState =
    | { status: "idle" }
    | { status: "loading" }
    | { status: "ready"; record: ConformerProjection }
    | { status: "missing" }
    | { status: "error" }

/**
 * One conformer group fetched by its own ref, for a selection that is not on the page of the list on screen. `wanted`
 * is `null` when the page already holds it (or nothing is selected); nothing is requested then. Keyed on the entry and
 * the ref, so a stale answer for a previous ref is never read.
 */
export function useConformerByRef(entryRef: string, wanted: string | null): ConformerByRefState {
    const key = wanted === null ? null : conformerKey(entryRef, wanted)
    const [result, setResult] = useState<{ key: string; state: ConformerByRefState } | null>(null)

    useEffect(() => {
        if (key === null) return
        let mounted = true
        const subscription = dedupedFetch(
            loadEntryConformerByRef, key, (signal, onRateLimited) => loadEntryConformerByRef(key, signal, onRateLimited),
        )
        subscription.promise
            .then((record) => {
                if (mounted) setResult({ key, state: record ? { status: "ready", record } : { status: "missing" } })
            })
            .catch((error: unknown) => {
                if (!mounted || (error instanceof DOMException && error.name === "AbortError")) return
                setResult({ key, state: { status: "error" } })
            })
        return () => {
            mounted = false
            subscription.unsubscribe()
        }
    }, [key])

    if (key === null) return { status: "idle" }
    return result?.key === key ? result.state : { status: "loading" }
}

/**
 * Every conformer group of the entry without the heavy includes, read once and only when the list is longer than the
 * page on screen (`enabled`), so a label can be resolved for a basin on a page the reader has not opened. Empty until
 * it arrives, and empty if it fails: a basin it does not know is then shown as its ref, never guessed.
 */
export function useConformerLabelIndex(entryRef: string, enabled: boolean): ConformerProjection[] {
    const [result, setResult] = useState<{ entryRef: string; records: ConformerProjection[] } | null>(null)

    useEffect(() => {
        if (!enabled) return
        let mounted = true
        const subscription = dedupedFetch(
            loadEntryConformerLabels, entryRef, (signal, onRateLimited) => loadEntryConformerLabels(entryRef, signal, onRateLimited),
        )
        subscription.promise
            .then((records) => { if (mounted) setResult({ entryRef, records }) })
            .catch(() => undefined)
        return () => {
            mounted = false
            subscription.unsubscribe()
        }
    }, [entryRef, enabled])

    return enabled && result?.entryRef === entryRef ? result.records : EMPTY
}

const EMPTY: ConformerProjection[] = []
