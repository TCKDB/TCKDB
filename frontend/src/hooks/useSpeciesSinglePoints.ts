import { useEffect, useState } from "react"
import { dedupedFetch } from "../api/requestCache"
import {
    loadSpeciesOptimisationEnergies,
    loadSpeciesSinglePointEnergies,
    type SinglePointEnergies,
} from "../api/speciesCalculationsApi"

export type SinglePointsState =
    | { status: "idle" }
    | { status: "loading" }
    | { status: "failed" }
    | ({ status: "ready" } & SinglePointEnergies)

type Walk = (entryRef: string, signal?: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<SinglePointEnergies>

/**
 * One energy walk of one entry, read only while `enabled`, so loading an entry never waits for it. Idle, loading and
 * failed are all different from "ready with none": a caller must not show an energy as absent while it is still being
 * read or when the read failed.
 */
function useEnergyWalk(load: Walk, entryRef: string, enabled: boolean): SinglePointsState {
    const [result, setResult] = useState<{ entryRef: string; state: SinglePointsState } | null>(null)

    useEffect(() => {
        if (!enabled) return
        let mounted = true
        const subscription = dedupedFetch(load, entryRef, (signal, onRateLimited) => load(entryRef, signal, onRateLimited))
        subscription.promise
            .then((data) => { if (mounted) setResult({ entryRef, state: { status: "ready", ...data } }) })
            .catch((error: unknown) => {
                if (!mounted || (error instanceof DOMException && error.name === "AbortError")) return
                setResult({ entryRef, state: { status: "failed" } })
            })
        return () => {
            mounted = false
            subscription.unsubscribe()
        }
    }, [load, entryRef, enabled])

    if (!enabled) return { status: "idle" }
    return result?.entryRef === entryRef ? result.state : { status: "loading" }
}

/** The single-point energies of one entry. */
export function useSpeciesSinglePoints(entryRef: string, enabled: boolean): SinglePointsState {
    return useEnergyWalk(loadSpeciesSinglePointEnergies, entryRef, enabled)
}

/** The final energies of the entry's optimisations. */
export function useSpeciesOptimisations(entryRef: string, enabled: boolean): SinglePointsState {
    return useEnergyWalk(loadSpeciesOptimisationEnergies, entryRef, enabled)
}
