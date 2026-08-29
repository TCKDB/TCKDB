import { useEffect, useState } from "react"
import { loadConformerObservation, type ConformerObservation } from "../api/conformerObservationApi"
import { ScientificApiError } from "../api/scientificTransport"

export type ConformerObservationState =
    | { ref: string; status: "loading" }
    | { ref: string; status: "missing" | "malformed" | "unavailable" }
    | { ref: string; status: "ready"; observation: ConformerObservation }

export function useConformerObservation(ref: string): ConformerObservationState {
    const [state, setState] = useState<ConformerObservationState>({ ref, status: "loading" })
    const visibleState = state.ref === ref ? state : { ref, status: "loading" as const }

    useEffect(() => {
        const controller = new AbortController()
        loadConformerObservation(ref, controller.signal)
            .then((observation) => setState({ ref, status: "ready", observation }))
            .catch((error: unknown) => {
                if (controller.signal.aborted) return
                const status = error instanceof ScientificApiError && error.status === 404
                    ? "missing"
                    : error instanceof ScientificApiError && /malformed/i.test(error.message)
                        ? "malformed"
                        : "unavailable"
                setState({ ref, status })
            })
        return () => controller.abort()
    }, [ref])
    return visibleState
}
