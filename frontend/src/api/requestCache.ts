/**
 * A tiny per-loader response cache, module-scoped (lives as long as the
 * page does -- a hard reload clears it, same as any other in-memory
 * state). Exists to answer one specific complaint: on the species-entry
 * page, switching tabs and pressing Back used to refire the SAME request
 * (entry projection, conformer list, per-section thermo/statmech/transport
 * lists) every time a component remounted, even though nothing about the
 * underlying record had changed. Sixty-ish requests per minute is the
 * anonymous-read budget (`rate_limit_anon_read_per_minute`,
 * `backend/app/api/config.py`) -- a handful of redundant remounts is
 * enough to trip it.
 *
 * Scoped by the LOADER FUNCTION ITSELF (e.g. `loadEntryThermo`), not by a
 * string name -- every caller passes a stable, module-level exported
 * function (see `useScientificRecord`'s callers), so the function
 * reference doubles as a collision-free cache namespace with no string to
 * keep in sync and no risk of two unrelated loaders sharing a key. A
 * `WeakMap` means a loader that nothing references any more (never happens
 * in practice, since these are all top-level exports) can still be
 * garbage-collected along with its cache.
 *
 * A cache entry OWNS its own `AbortController` and its own subscriber
 * count -- `load(signal)` is called exactly ONCE per `(scope, key)` while
 * an entry is pending, using a signal this module controls, never a
 * caller's own. Earlier versions of this file instead ran the caller's
 * OWN signal into the shared fetch: whichever caller arrived first "won"
 * and every later joiner rode along on a request that caller could cancel
 * out from under them. Under `React.StrictMode` (`main.tsx`; dev-only
 * double-invoke of mount -> cleanup -> mount) that meant the discarded
 * probe mount's cleanup aborted the ONE real request before the mount
 * that stays ever got its own -- the page hung on "Loading …" forever
 * (`useSpeciesEntry`'s `AbortError`-name check swallowed it unconditionally)
 * or fell back to a generic `unavailable`/`could not be displayed` state
 * instead of the real classification (`useScientificRecord`'s
 * `controller.signal.aborted` check saw ITS OWN, un-aborted signal and
 * proceeded to misclassify someone else's abort). Subscriber counting
 * fixes both: an `AbortError` now ALWAYS means "this shared request was
 * cancelled because nobody wants it any more", so both hooks can treat it
 * uniformly as "do nothing, a fresher effect run (or a real unmount) will
 * handle this" -- see `useScientificRecord.ts` / `useSpeciesEntry.ts`.
 *
 * The abort itself is deferred by one tick (see `SUBSCRIBER_DRAIN_MS`
 * below) rather than firing the instant the subscriber count hits zero:
 * StrictMode's cleanup and remount happen synchronously, in the same
 * tick, so a resubscribe within that window cancels the scheduled abort
 * and the ONE underlying request keeps running for the mount that stays.
 * A REAL unmount with no resubscribe still aborts, just one tick later
 * than before -- not observable to a caller.
 *
 * Deliberately simple otherwise: a flat five-minute TTL on a successful
 * response (see `CACHE_TTL_MS`), no other eviction beyond "abandoned by
 * every subscriber", no persistence across a page reload. Only
 * SUCCESSFUL responses are cached -- a failure (including a rate-limited
 * request, see `requestScientificJson`) is never cached, so the next
 * subscription always gets a fresh attempt rather than being stuck
 * replaying a transient error forever.
 */

type PendingEntry<T> = {
    status: "pending"
    promise: Promise<T>
    controller: AbortController
    subscribers: number
    /** Set while a deferred abort is scheduled (subscribers hit 0); cleared if someone resubscribes first. */
    abortTimer: ReturnType<typeof setTimeout> | null
    /** Seconds reported by the most recent `notifyWaiting` call (see `dedupedFetch`'s `load` parameter), or `null` if this entry has never been rate-limited. Lets a subscriber that joins WHILE already waiting see the wait immediately, not just one that was already subscribed when it started. */
    currentWait: number | null
    waitingListeners: Set<(retryAfterSeconds: number) => void>
}
type DoneEntry<T> = { status: "done"; value: T; expiresAt: number }
type CacheEntry<T> = PendingEntry<T> | DoneEntry<T>

let caches = new WeakMap<object, Map<string, CacheEntry<unknown>>>()

/** How long to wait, after the last subscriber leaves, before actually cancelling the shared request -- long enough for a StrictMode remount's synchronous resubscribe to cancel it, short enough that a genuine abandonment still cancels promptly. */
const SUBSCRIBER_DRAIN_MS = 0

/**
 * A successful response is trusted for this long before a fresh
 * subscription refetches instead of reusing it. Cheap insurance against a
 * long-lived tab: this cache exists to make the SAME browsing session
 * (tab clicks, Back/forward) free, not to promise a page open for hours
 * never sees a review-status change or a newly deposited record. Five
 * minutes is long enough to cover the "clicking around one entry" session
 * this was built for, short enough that a tab left open across a coffee
 * break self-heals on the next visit rather than needing a hard reload.
 */
const CACHE_TTL_MS = 5 * 60 * 1000

export type Subscription<T> = {
    /** Resolves/rejects exactly like the underlying `load(signal)` call this subscription is riding along on. Rejects with `AbortError` if and only if every subscriber (including this one) has unsubscribed -- see the module docstring. */
    promise: Promise<T>
    /** Call from cleanup (e.g. a `useEffect` return). Decrements the shared subscriber count; the underlying request is only actually cancelled once it reaches zero and stays there past `SUBSCRIBER_DRAIN_MS`. Safe to call more than once (a no-op after the first call). */
    unsubscribe: () => void
    /**
     * Registers `listener` to be called every time the underlying `load`
     * reports a rate-limit wait (see `dedupedFetch`'s `load` parameter and
     * `requestScientificJson`'s `onRateLimited`), and IMMEDIATELY if a
     * wait is already in progress when this is called -- a subscriber
     * that joins mid-wait (e.g. a second tab-panel mounting while the
     * first is still waiting on a shared entry's retry) sees it too, not
     * just the subscriber that happened to be there when it started.
     * Returns a function that removes the listener; not tied to
     * `unsubscribe` above, since a caller may want to stop listening for
     * waits without giving up the subscription itself (rare in practice,
     * but keeps the two concerns independent). A no-op, returning a no-op
     * remover, once this entry has settled (done or a caller only ever
     * subscribed after settlement) -- nothing left to wait on.
     */
    onWaiting: (listener: (retryAfterSeconds: number) => void) => () => void
}

const NO_OP_UNSUBSCRIBE = () => {}

/**
 * Subscribes to the response for `(scope, key)`: returns the already-
 * settled value if one is cached, joins an in-flight request for the same
 * `(scope, key)` if one is pending, or starts one via `load(signal,
 * notifyWaiting)` (called at most once per pending entry, with a signal
 * THIS function owns -- see the module docstring for why the caller never
 * supplies its own -- and a `notifyWaiting` callback the loader should
 * call, synchronously, the instant it sees a rate-limit response, so
 * every current AND future subscriber's `onWaiting` listener hears about
 * it -- see `requestScientificJson`'s `onRateLimited` parameter).
 */
export function dedupedFetch<T>(
    scope: object,
    key: string,
    load: (signal: AbortSignal, notifyWaiting: (retryAfterSeconds: number) => void) => Promise<T>,
): Subscription<T> {
    let scopeCache = caches.get(scope)
    if (!scopeCache) {
        scopeCache = new Map()
        caches.set(scope, scopeCache)
    }

    const existingRaw = scopeCache.get(key) as CacheEntry<T> | undefined
    const existing = existingRaw?.status === "done" && existingRaw.expiresAt <= Date.now() ? undefined : existingRaw
    if (existing?.status === "done") {
        return { promise: Promise.resolve(existing.value), unsubscribe: NO_OP_UNSUBSCRIBE, onWaiting: () => NO_OP_UNSUBSCRIBE }
    }

    let entry = existing
    if (!entry) {
        const controller = new AbortController()
        const notifyWaiting = (retryAfterSeconds: number) => {
            const current = scopeCache!.get(key)
            if (current === undefined || current !== entry || current.status !== "pending") return
            current.currentWait = retryAfterSeconds
            for (const listener of current.waitingListeners) listener(retryAfterSeconds)
        }
        const promise = load(controller.signal, notifyWaiting).then(
            (value) => {
                scopeCache!.set(key, { status: "done", value, expiresAt: Date.now() + CACHE_TTL_MS })
                return value
            },
            (error: unknown) => {
                const current = scopeCache!.get(key)
                if (current === entry) scopeCache!.delete(key)
                throw error
            },
        )
        entry = { status: "pending", promise, controller, subscribers: 0, abortTimer: null, currentWait: null, waitingListeners: new Set() }
        scopeCache.set(key, entry)
    } else if (entry.abortTimer !== null) {
        // A previous subscriber left and scheduled the drain-abort below,
        // but this new subscription arrived before it fired -- wanted
        // again, cancel the abort.
        clearTimeout(entry.abortTimer)
        entry.abortTimer = null
    }

    entry.subscribers += 1
    const activeEntry = entry
    let unsubscribed = false
    return {
        promise: entry.promise,
        unsubscribe: () => {
            if (unsubscribed) return
            unsubscribed = true
            activeEntry.subscribers -= 1
            if (activeEntry.subscribers > 0) return
            activeEntry.abortTimer = setTimeout(() => {
                if (activeEntry.subscribers > 0) return
                activeEntry.controller.abort()
                const current = scopeCache!.get(key)
                if (current === activeEntry) scopeCache!.delete(key)
            }, SUBSCRIBER_DRAIN_MS)
        },
        onWaiting: (listener) => {
            activeEntry.waitingListeners.add(listener)
            if (activeEntry.currentWait !== null) listener(activeEntry.currentWait)
            return () => activeEntry.waitingListeners.delete(listener)
        },
    }
}

/**
 * Test-only escape hatch: wipe EVERY loader's cache at once, by dropping
 * the outer `WeakMap` and starting a fresh one. Wired into
 * `src/test/setup.ts`'s global `afterEach` -- without it, every test file
 * that mounts a page built on `useScientificRecord`/`useSpeciesEntry` more
 * than once against the SAME ref (nearly every one of them: `entryRef`,
 * `groupOneRef`, etc. are shared per-file constants) would see only its
 * FIRST render actually hit MSW; every later render in that file would
 * silently replay the module-level cache's contents (which is exactly the
 * production behavior this cache exists to provide -- it just needs a
 * reset point between tests, the way MSW's own handlers get
 * `server.resetHandlers()`).
 */
export function resetAllRequestCaches(): void {
    caches = new WeakMap()
}
