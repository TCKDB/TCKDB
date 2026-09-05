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
 * Deliberately simple: no TTL, no eviction, no persistence across a page
 * reload. Only SUCCESSFUL responses are cached -- a failure (including a
 * rate-limited request, see `requestScientificJson`) is never cached, so
 * the next mount always gets a fresh attempt rather than being stuck
 * replaying a transient error forever.
 */

type PendingEntry<T> = { status: "pending"; promise: Promise<T> }
type DoneEntry<T> = { status: "done"; value: T }
type CacheEntry<T> = PendingEntry<T> | DoneEntry<T>

let caches = new WeakMap<object, Map<string, CacheEntry<unknown>>>()

/**
 * Returns the cached value for `(scope, key)` if one is already settled;
 * joins an in-flight request for the same `(scope, key)` if one is
 * currently pending; otherwise calls `fetcher()` and caches the result.
 *
 * A rejected `fetcher()` clears its own cache entry before rethrowing, so
 * the failure is never treated as cached -- the next call for the same
 * `(scope, key)` starts a fresh attempt.
 */
export function dedupedFetch<T>(scope: object, key: string, fetcher: () => Promise<T>): Promise<T> {
    let scopeCache = caches.get(scope)
    if (!scopeCache) {
        scopeCache = new Map()
        caches.set(scope, scopeCache)
    }
    const existing = scopeCache.get(key)
    if (existing) {
        return existing.status === "done" ? Promise.resolve(existing.value as T) : (existing.promise as Promise<T>)
    }

    const promise = fetcher().then(
        (value) => {
            scopeCache!.set(key, { status: "done", value })
            return value
        },
        (error: unknown) => {
            scopeCache!.delete(key)
            throw error
        },
    )
    scopeCache.set(key, { status: "pending", promise })
    return promise
}

/**
 * Test-only escape hatch: wipe every cached entry for one loader.
 */
export function clearRequestCache(scope: object): void {
    caches.delete(scope)
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
