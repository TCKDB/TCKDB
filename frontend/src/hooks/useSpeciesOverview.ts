import { useEffect, useState } from "react"
import { loadSpeciesOverview, type SpeciesOverview } from "../api/speciesOverviewApi"
import { ScientificApiError, ScientificRateLimitError } from "../api/scientificTransport"

export type SpeciesOverviewState =
    | { speciesRef: string; species: SpeciesOverview; status: "ready" }
    | { speciesRef: string; status: "loading" | "missing" | "malformed" | "http-error" }
    // See `ScientificRateLimitError` -- distinct from `http-error` because
    // "try again later" is not honest here: `requestScientificJson` already
    // retried once automatically, and this only fires when the archive was
    // STILL over its anonymous-read budget a `Retry-After` window later.
    | { speciesRef: string; status: "rate-limited"; retryAfterSeconds: number }

export function useSpeciesOverview(speciesRef: string): SpeciesOverviewState {
    const [state, setState] = useState<SpeciesOverviewState>({ speciesRef, status: "loading" })

    useEffect(() => {
        const controller = new AbortController()
        void loadSpeciesOverview(speciesRef, controller.signal)
            .then((species) => {
                if (!controller.signal.aborted) {
                    setState(species
                        ? { speciesRef, species, status: "ready" }
                        : { speciesRef, status: "missing" })
                }
            })
            .catch((error: unknown) => {
                if (controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
                    return
                }
                if (error instanceof ScientificRateLimitError) {
                    setState({ speciesRef, status: "rate-limited", retryAfterSeconds: error.retryAfterSeconds })
                    return
                }
                setState({
                    speciesRef,
                    status: error instanceof ScientificApiError && error.status === 200
                        ? "malformed"
                        : "http-error",
                })
            })
        return () => controller.abort()
    }, [speciesRef])

    return state
}
