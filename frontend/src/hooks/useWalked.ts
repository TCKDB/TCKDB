import { useEffect, useState } from "react"
import type { WalkControl } from "../api/entryNumbersApi"
import { ScientificRateLimitError } from "../api/scientificTransport"

export type WalkedState<T> =
    | { ref: string; status: "loading" | "failed" }
    | { ref: string; status: "retrying" | "rate-limited"; retryAfterSeconds: number }
    | { ref: string; status: "ready"; data: T }

/** Runs a capped walk for one ref, showing a rate-limit wait while it lasts and letting go of its requests on unmount. */
export function useWalked<T>(ref: string, load: (ref: string, control: WalkControl) => Promise<T>): WalkedState<T> {
    const [state, setState] = useState<WalkedState<T>>({ ref, status: "loading" })
    useEffect(() => {
        let mounted = true
        const control: WalkControl = {
            subscriptions: [],
            abandoned: () => !mounted,
            onWaiting: (retryAfterSeconds) => {
                if (mounted) setState({ ref, status: "retrying", retryAfterSeconds })
            },
        }
        load(ref, control)
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
        // eslint-disable-next-line react-hooks/exhaustive-deps -- `load` is a module-level function that can also switch with the `preview` flag, which is fixed per instance; only the ref re-runs the walk
    }, [ref])
    return state.ref === ref ? state : { ref, status: "loading" }
}
