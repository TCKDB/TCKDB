import { useEffect, useState } from "react"
import { dedupedFetch } from "../api/requestCache"
import { loadSpeciesSinglePointEnergies, type SinglePointEnergies } from "../api/speciesCalculationsApi"

export type SinglePointsState =
    | { status: "idle" }
    | { status: "loading" }
    | ({ status: "ready" } & SinglePointEnergies)

/**
 * The single-point energies of one entry, read only while `enabled` (the single-point tab is open), so loading an entry
 * never waits for them and never spends the read budget on them. Idle and loading are different from "ready with none":
 * a caller must not show an energy as absent while it is still being read.
 */
export function useSpeciesSinglePoints(entryRef: string, enabled: boolean): SinglePointsState {
    const [result, setResult] = useState<{ entryRef: string; data: SinglePointEnergies } | null>(null)

    useEffect(() => {
        if (!enabled) return
        let mounted = true
        const subscription = dedupedFetch(
            loadSpeciesSinglePointEnergies,
            entryRef,
            (signal, onRateLimited) => loadSpeciesSinglePointEnergies(entryRef, signal, onRateLimited),
        )
        subscription.promise
            .then((data) => { if (mounted) setResult({ entryRef, data }) })
            .catch(() => undefined)
        return () => {
            mounted = false
            subscription.unsubscribe()
        }
    }, [entryRef, enabled])

    if (!enabled) return { status: "idle" }
    return result?.entryRef === entryRef ? { status: "ready", ...result.data } : { status: "loading" }
}
