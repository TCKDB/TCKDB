import { useEffect, useState } from "react"
import { loadEntryNumbers, type EntryNumbers, type WalkControl } from "../api/entryNumbersApi"
import { ScientificRateLimitError } from "../api/scientificTransport"

export type EntryNumbersState =
    | { ref: string; status: "loading" | "failed" }
    | { ref: string; status: "retrying" | "rate-limited"; retryAfterSeconds: number }
    | { ref: string; status: "ready"; data: EntryNumbers }

/** Loads every thermo, statmech and transport record of one entry, showing a rate-limit wait while it lasts. */
export function useEntryNumbers(ref: string): EntryNumbersState {
    const [state, setState] = useState<EntryNumbersState>({ ref, status: "loading" })
    useEffect(() => {
        let mounted = true
        const control: WalkControl = {
            subscriptions: [],
            onWaiting: (retryAfterSeconds) => {
                if (mounted) setState({ ref, status: "retrying", retryAfterSeconds })
            },
        }
        loadEntryNumbers(ref, control)
            .then((data) => { if (mounted) setState({ ref, status: "ready", data }) })
            .catch((error: unknown) => {
                if (!mounted || (error instanceof DOMException && error.name === "AbortError")) return
                setState(error instanceof ScientificRateLimitError
                    ? { ref, status: "rate-limited", retryAfterSeconds: error.retryAfterSeconds }
                    : { ref, status: "failed" })
            })
        return () => {
            mounted = false
            for (const subscription of control.subscriptions) subscription.unsubscribe()
        }
    }, [ref])
    return state.ref === ref ? state : { ref, status: "loading" }
}
