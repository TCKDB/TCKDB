import { afterEach, describe, expect, it, vi } from "vitest"
import { dedupedFetch, resetAllRequestCaches } from "./requestCache"

// ---------------------------------------------------------------------------
// Regression coverage for the StrictMode bug found in review of the
// tab-remount/429 PR: `dedupedFetch` used to run the FIRST subscriber's own
// `AbortSignal` into the shared `load()` call. A second, concurrent
// subscriber for the same `(scope, key)` joined that same promise -- so when
// the FIRST subscriber's cleanup ran (e.g. React StrictMode's dev-only
// mount -> cleanup -> mount, which happens synchronously before the second,
// surviving mount's effect runs), it aborted the one real request out from
// under the second subscriber, who never gets a response of its own.
//
// `dedupedFetch` now owns its own `AbortController` per cache entry and
// counts subscribers: the underlying request is only ever cancelled once
// EVERY subscriber has left, and even then only after a deferred tick (long
// enough for a StrictMode-shaped synchronous resubscribe to cancel it).
// ---------------------------------------------------------------------------

afterEach(() => {
    resetAllRequestCaches()
    vi.useRealTimers()
})

function deferred<T>() {
    let resolve!: (value: T) => void
    let reject!: (error: unknown) => void
    const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
}

describe("dedupedFetch: concurrent subscribers share one request", () => {
    it("calls the loader exactly once for two concurrent subscriptions, and both resolve when only one unsubscribes", async () => {
        const scope = {}
        let loadCalls = 0
        const { promise: loadPromise, resolve } = deferred<string>()
        const load = () => {
            loadCalls += 1
            return loadPromise
        }

        const first = dedupedFetch(scope, "key", load)
        const second = dedupedFetch(scope, "key", load)
        expect(loadCalls).toBe(1) // the second subscription joined the first's in-flight request, not a second call

        // The first subscriber leaves (its own component unmounted) --
        // the second is still subscribed, so nothing should be cancelled
        // and the shared request should still resolve normally for BOTH.
        first.unsubscribe()

        resolve("the value")
        await expect(first.promise).resolves.toBe("the value")
        await expect(second.promise).resolves.toBe("the value")
        expect(loadCalls).toBe(1)
    })

    it("does not call the loader a second time for a StrictMode-shaped synchronous unsubscribe -> resubscribe", async () => {
        vi.useFakeTimers()
        const scope = {}
        let loadCalls = 0
        let capturedSignal: AbortSignal | undefined
        const { promise: loadPromise, resolve } = deferred<string>()
        const load = (signal: AbortSignal) => {
            loadCalls += 1
            capturedSignal = signal
            return loadPromise
        }

        // Mount -> cleanup -> mount, synchronously, exactly like React
        // StrictMode's dev-only double-invoke.
        const probe = dedupedFetch(scope, "key", load)
        probe.unsubscribe()
        const real = dedupedFetch(scope, "key", load)

        expect(loadCalls).toBe(1) // never refetched for the resubscribe
        expect(capturedSignal?.aborted).toBe(false) // the deferred abort was cancelled, not fired

        // Advancing time past the deferred-abort window must NOT cancel it
        // now that a subscriber (the surviving mount) is present.
        await vi.advanceTimersByTimeAsync(1000)
        expect(capturedSignal?.aborted).toBe(false)

        resolve("the value")
        await expect(real.promise).resolves.toBe("the value")
    })

    it("actually cancels the underlying request once every subscriber has genuinely left", async () => {
        vi.useFakeTimers()
        const scope = {}
        let capturedSignal: AbortSignal | undefined
        const load = (signal: AbortSignal) => {
            capturedSignal = signal
            return new Promise<string>(() => { /* never resolves -- only the abort matters here */ })
        }

        const subscription = dedupedFetch(scope, "key", load)
        subscription.unsubscribe()
        expect(capturedSignal?.aborted).toBe(false) // not yet -- still within the deferred window

        await vi.advanceTimersByTimeAsync(1000)
        expect(capturedSignal?.aborted).toBe(true)
    })

    it("re-fetches on the next subscription after a genuine abandonment (no resubscribe)", async () => {
        vi.useFakeTimers()
        const scope = {}
        let loadCalls = 0
        const load = () => {
            loadCalls += 1
            return new Promise<string>(() => { /* never resolves -- only the call count matters here */ })
        }

        const first = dedupedFetch(scope, "key", load)
        expect(loadCalls).toBe(1)
        first.unsubscribe()
        await vi.advanceTimersByTimeAsync(1000) // the abandoned request is now actually cancelled

        dedupedFetch(scope, "key", load)
        expect(loadCalls).toBe(2) // a fresh subscription after genuine abandonment starts a NEW request, not a resurrected one
    })

    // The cache has a 5-minute TTL (review of #370: a long-lived tab should
    // eventually see a curation change). Without this test, CACHE_TTL_MS =
    // Infinity passes the whole suite.
    it("serves a completed response for five minutes, then refetches", async () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date("2026-09-05T10:00:00Z"))
        const scope = {}
        let loadCalls = 0
        const load = async () => { loadCalls += 1; return `value-${loadCalls}` }

        const first = dedupedFetch(scope, "key", load)
        await expect(first.promise).resolves.toBe("value-1")

        await vi.advanceTimersByTimeAsync(4 * 60 * 1000 + 59 * 1000) // 4:59 later
        const second = dedupedFetch(scope, "key", load)
        await expect(second.promise).resolves.toBe("value-1")
        expect(loadCalls).toBe(1) // still within the TTL: served from cache

        await vi.advanceTimersByTimeAsync(2 * 1000) // 5:01 after the original response
        const third = dedupedFetch(scope, "key", load)
        await expect(third.promise).resolves.toBe("value-2")
        expect(loadCalls).toBe(2) // expired: a fresh request
    })
})
