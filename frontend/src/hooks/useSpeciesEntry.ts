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
    // See `ScientificRateLimitError` -- distinct from `http-error` because
    // "try again later" is not honest here: `requestScientificJson` already
    // retried once automatically, and this only fires when the archive was
    // STILL over its anonymous-read budget a `Retry-After` window later.
    | { entryRef: string; status: "rate-limited"; retryAfterSeconds: number }

export function useSpeciesEntry(entryRef: string): SpeciesEntryState | null {
    const [state, setState] = useState<SpeciesEntryState | null>(null)

    useEffect(() => {
        const controller = new AbortController()
        // Each loader is cached (and in-flight calls deduplicated) by its
        // own function identity + `entryRef` -- see `requestCache.ts`. A
        // section-tab click no longer remounts this page at all (see
        // `App.tsx`'s `SpeciesEntrySectionRoute`), but Back/forward across
        // a real navigation away from the entry still does, and this makes
        // that case free too: the three requests below fire once per
        // `entryRef` per page life, not once per mount.
        void Promise.all([
            dedupedFetch(loadSpeciesEntry, entryRef, () => loadSpeciesEntry(entryRef, controller.signal)),
            dedupedFetch(loadEntryConformers, entryRef, () => loadEntryConformers(entryRef, controller.signal)),
            // Best-effort enrichment (see the module docstring on
            // `loadSpeciesSinglePointEnergies`) -- it resolves to `[]`
            // rather than rejecting, so a failure here never turns the
            // whole entry page into an error state.
            dedupedFetch(
                loadSpeciesSinglePointEnergies,
                entryRef,
                () => loadSpeciesSinglePointEnergies(entryRef, controller.signal),
            ),
        ])
            .then(([entry, conformers, spEnergies]) => {
                setState(entry ? { entryRef, entry, conformers, spEnergies } : { entryRef, status: "missing" })
            })
            .catch((error: unknown) => {
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
        return () => controller.abort()
    }, [entryRef])

    return state
}
