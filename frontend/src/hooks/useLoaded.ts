import { useEffect, useState } from "react"
import { dedupedFetch } from "../api/requestCache"
import { ScientificRateLimitError } from "../api/scientificTransport"

export type LoadedState<T> =
    | { status: "idle" | "loading" | "failed" }
    | { status: "rate-limited"; retryAfterSeconds: number }
    | { status: "ready"; data: T }

type Loader<T> = (key: string, signal?: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<T>

/** One shared, cached read keyed by a string; `key` null means nothing is wanted yet. A failure never throws into the page. */
export function useLoaded<T>(load: Loader<T>, key: string | null): LoadedState<T> {
    const [result, setResult] = useState<{ key: string; state: LoadedState<T> } | null>(null)
    useEffect(() => {
        if (key === null) return
        let mounted = true
        const subscription = dedupedFetch(load, key, (signal, onRateLimited) => load(key, signal, onRateLimited))
        subscription.promise
            .then((data) => { if (mounted) setResult({ key, state: { status: "ready", data } }) })
            .catch((error: unknown) => {
                if (!mounted || (error instanceof DOMException && error.name === "AbortError")) return
                setResult({
                    key,
                    state: error instanceof ScientificRateLimitError
                        ? { status: "rate-limited", retryAfterSeconds: error.retryAfterSeconds }
                        : { status: "failed" },
                })
            })
        return () => {
            mounted = false
            subscription.unsubscribe()
        }
    }, [load, key])
    if (key === null) return { status: "idle" }
    return result?.key === key ? result.state : { status: "loading" }
}
