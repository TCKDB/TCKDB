import { useEffect, useState } from "react"
import { dedupedFetch } from "../api/requestCache"
import { loadEntryConformers, loadSpeciesEntry } from "../api/speciesEntryApi"
import type { ConformerProjection, SpeciesEntryProjection } from "../api/speciesEntryApi"
import { ScientificApiError, ScientificRateLimitError } from "../api/scientificTransport"
import { loadSpeciesSinglePointEnergies } from "../api/speciesCalculationsApi"
import type { SpeciesCalculationEnergyRecord } from "../api/speciesCalculationsApi"

export type SpeciesEntryState =
    | { entryRef: string; entry: SpeciesEntryProjection; conformers: ConformerProjection[]; spEnergies: SpeciesCalculationEnergyRecord[] }
    | { entryRef: string; status: "missing" | "malformed" | "http-error" }
    // A 429 was seen on at least one of the three requests below and
    // `requestScientificJson` is in the middle of its own automatic
    // `Retry-After` wait (up to a minute -- `rate_limit_anon_read_per_minute`,
    // `backend/app/api/config.py`). Never terminal: resolves into either the
    // ready branch above (the retry worked) or `rate-limited` below (it
    // didn't). Distinct from the `null`/loading state `SpeciesEntryPage`
    // renders as "Loading species entry" -- that copy is honest for an
    // ordinary load, not for a wait the reader has no way to distinguish
    // from a stuck page otherwise.
    | { entryRef: string; status: "retrying"; retryAfterSeconds: number }
    // See `ScientificRateLimitError` -- distinct from `http-error` because
    // "try again later" is not honest here: `requestScientificJson` already
    // retried once automatically, and this only fires when the archive was
    // STILL over its anonymous-read budget a `Retry-After` window later.
    | { entryRef: string; status: "rate-limited"; retryAfterSeconds: number }

export function useSpeciesEntry(entryRef: string): SpeciesEntryState | null {
    const [state, setState] = useState<SpeciesEntryState | null>(null)

    useEffect(() => {
        let mounted = true
        // Each loader is cached (and in-flight calls deduplicated) by its
        // own function identity + `entryRef` -- see `requestCache.ts`. A
        // section-tab click no longer remounts this page at all (see
        // `App.tsx`'s `SpeciesEntrySectionRoute`), but Back/forward across
        // a real navigation away from the entry still does, and this makes
        // that case free too: the three requests below fire once per
        // `entryRef` per page life, not once per mount.
        //
        // `dedupedFetch` owns each request's `AbortSignal`, never this
        // effect -- see the module docstring on `requestCache.ts`. An
        // `AbortError` here always means "the shared request was cancelled
        // because every subscriber left" (e.g. every mount, including a
        // React StrictMode dev-only probe mount, has unmounted), so it is
        // always safe to ignore rather than needing to distinguish "my own
        // abort" from "someone else's".
        const entrySubscription = dedupedFetch(
            loadSpeciesEntry, entryRef, (signal, onRateLimited) => loadSpeciesEntry(entryRef, signal, onRateLimited),
        )
        const conformersSubscription = dedupedFetch(
            loadEntryConformers, entryRef, (signal, onRateLimited) => loadEntryConformers(entryRef, signal, onRateLimited),
        )
        // Best-effort enrichment (see the module docstring on
        // `loadSpeciesSinglePointEnergies`) -- it resolves to `[]` rather
        // than rejecting, so a failure here never turns the whole entry
        // page into an error state.
        const spEnergiesSubscription = dedupedFetch(
            loadSpeciesSinglePointEnergies,
            entryRef,
            (signal, onRateLimited) => loadSpeciesSinglePointEnergies(entryRef, signal, onRateLimited),
        )
        // All three requests share the same anonymous-read budget, so a
        // 429 on one typically means a 429 on the others too -- whichever
        // reports last simply wins, which is fine: they report
        // near-identical `retryAfterSeconds` values from the same window.
        const onWaiting = (retryAfterSeconds: number) => {
            if (mounted) setState({ entryRef, status: "retrying", retryAfterSeconds })
        }
        const stopWaitingListeners = [
            entrySubscription.onWaiting(onWaiting),
            conformersSubscription.onWaiting(onWaiting),
            spEnergiesSubscription.onWaiting(onWaiting),
        ]
        void Promise.all([entrySubscription.promise, conformersSubscription.promise, spEnergiesSubscription.promise])
            .then(([entry, conformers, spEnergies]) => {
                if (mounted) setState(entry ? { entryRef, entry, conformers, spEnergies } : { entryRef, status: "missing" })
            })
            .catch((error: unknown) => {
                if (!mounted) return
                if (error instanceof DOMException && error.name === "AbortError") return
                if (error instanceof ScientificRateLimitError) {
                    setState({ entryRef, status: "rate-limited", retryAfterSeconds: error.retryAfterSeconds })
                    return
                }
                const status = error instanceof ScientificApiError && error.status === 200
                    ? "malformed"
                    : "http-error"
                setState({ entryRef, status })
            })
        return () => {
            mounted = false
            for (const stop of stopWaitingListeners) stop()
            entrySubscription.unsubscribe()
            conformersSubscription.unsubscribe()
            spEnergiesSubscription.unsubscribe()
        }
    }, [entryRef])

    return state
}
