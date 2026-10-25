import { useEffect, useState } from "react"
import type { ListMeta } from "../api/listEnvelope"
import { loadTransitionStateSiblings, type TransitionStateSiblingRecord } from "../api/transitionStateEntryApi"
import type { PageRequest } from "../domain/paging"

export type SiblingsFetchStatus = "loading" | "loaded" | "error"

/**
 * Fetches "Other saddle points deposited for this reaction" as soon as
 * the reaction ref is known (a second, best-effort round trip -- see
 * `loadTransitionStateSiblings`). A still-loading fetch renders as no
 * siblings (the section simply hasn't appeared yet) -- this section is
 * additive context, and it must never block the rest of an otherwise-
 * successful page render. A FAILED fetch is distinguished from "this
 * reaction genuinely has no siblings": both return an empty
 * `siblings` array, but `status === "error"` lets the caller render a
 * short "could not load" line instead of silently claiming there are
 * none. MEASURED gap this fixes: the previous version's `.catch(() =>
 * setSiblings([]))` made a network failure look identical to zero
 * siblings.
 */
export function useTransitionStateSiblings(
    reactionRef: string | null | undefined,
    excludeEntryRef: string,
): {
    siblings: TransitionStateSiblingRecord[]
    meta: ListMeta | null
    status: SiblingsFetchStatus
    /** A different page of the siblings is being fetched; the previous page is still shown. */
    busy: boolean
    goTo: (request: PageRequest) => void
} {
    // Keyed on (reactionRef, excludeEntryRef, page) rather than reset with a
    // synchronous `setState([])` at the top of the effect (the
    // `useScientificRecord` pattern): a stale result is discarded by
    // comparing the key at READ time instead, so the effect body only
    // ever calls `setState` from inside the async callback.
    const [position, setPosition] = useState<{ scope: string; request: PageRequest | null }>({ scope: "", request: null })
    const scope = `${reactionRef ?? ""}:${excludeEntryRef}`
    const request = position.scope === scope ? position.request : null
    const key = request ? `${scope}#${request.offset}:${request.limit}` : scope
    const [state, setState] = useState<{
        key: string
        scope: string
        siblings: TransitionStateSiblingRecord[]
        meta: ListMeta | null
        status: SiblingsFetchStatus
    }>({ key, scope, siblings: [], meta: null, status: "loading" })
    // A different PAGE of the same reaction keeps the page on screen (marked busy) while the next one loads, so the
    // list does not vanish and come back; a different reaction or entry starts empty.
    const current = state.key === key
        ? state
        : state.scope === scope && state.status === "loaded"
            ? { ...state, key, status: "loading" as const }
            : { key, scope, siblings: [] as TransitionStateSiblingRecord[], meta: null, status: "loading" as const }

    useEffect(() => {
        if (!reactionRef) return
        const controller = new AbortController()
        loadTransitionStateSiblings(reactionRef, excludeEntryRef, controller.signal, request ?? undefined)
            .then((page) => {
                if (!controller.signal.aborted) setState({ key, scope, siblings: page.records, meta: page.meta, status: "loaded" })
            })
            .catch(() => {
                if (!controller.signal.aborted) setState({ key, scope, siblings: [], meta: null, status: "error" })
            })
        return () => controller.abort()
        // eslint-disable-next-line react-hooks/exhaustive-deps -- `request` is fully named by `key`
    }, [key, reactionRef, excludeEntryRef])
    return {
        busy: current.status === "loading" && current.meta !== null,
        siblings: current.siblings,
        meta: current.meta,
        status: current.status,
        goTo: (next) => setPosition({ scope, request: next }),
    }
}
