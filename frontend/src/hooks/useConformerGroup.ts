import { useEffect, useState } from "react"
import { loadConformerGroup, type ConformerGroup } from "../api/conformerGroupApi"
import { ScientificApiError } from "../api/scientificTransport"

export type ConformerGroupState =
    | { ref: string; status: "loading" }
    | { ref: string; status: "missing" | "malformed" | "unavailable" }
    | { ref: string; status: "ready"; group: ConformerGroup }

export function useConformerGroup(ref: string): ConformerGroupState {
    const [state, setState] = useState<ConformerGroupState>({ ref, status: "loading" })
    const visibleState = state.ref === ref ? state : { ref, status: "loading" as const }

    useEffect(() => {
        const controller = new AbortController()
        loadConformerGroup(ref, controller.signal)
            .then((group) => setState({ ref, status: "ready", group }))
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
