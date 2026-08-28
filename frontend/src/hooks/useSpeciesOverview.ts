import { useEffect, useState } from "react"
import { loadSpeciesOverview, type SpeciesOverview } from "../api/speciesOverviewApi"
import { ScientificApiError } from "../api/scientificTransport"

export type SpeciesOverviewState =
    | { speciesRef: string; species: SpeciesOverview; status: "ready" }
    | { speciesRef: string; status: "loading" | "missing" | "malformed" | "http-error" }

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
