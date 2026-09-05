import { useEffect, useState } from "react"
import { dedupedFetch } from "../api/requestCache"
import { ScientificApiError, ScientificRateLimitError } from "../api/scientificTransport"

/**
 * Shared load-and-classify state behind every `/scientific/*` detail page.
 *
 * A failed load is classified into five distinct, mutually exclusive
 * reasons rather than collapsed into one generic "unavailable":
 *
 * - `missing`       — HTTP 404. This reference does not exist.
 * - `invalid`       — HTTP 422 with `code` `handle_type_mismatch` or
 *   `invalid_handle`. The reference is not even the right *shape* of
 *   handle for this surface. Permanently wrong — retrying changes
 *   nothing — so it must never share copy with a transient outage.
 *   `detail` carries the archive's own explanation (e.g. "expected a
 *   conformer_observation handle (prefix 'co') but got prefix 'cg'") so
 *   the reader learns *why*, not just *that*.
 * - `unprocessable` — HTTP 422 with any OTHER `code` (or none). The
 *   reference is validly shaped and the record exists, but the archive
 *   declined to serve the full response — e.g. `geometry_too_large`
 *   (`backend/app/services/scientific_read/geometry.py`, the one surface
 *   with a public size cap as of this writing). This must never share
 *   copy with `invalid`: "not a valid reference" would contradict a
 *   `detail` that names a real record and a concrete reason. `detail`
 *   again carries the archive's own explanation.
 * - `malformed`     — the archive answered 200 but the payload failed our
 *   own schema validation. An archive-side bug, distinct from all of the
 *   above.
 * - `retrying`      — a 429 was seen and `requestScientificJson` is in the
 *   middle of its own automatic `Retry-After` wait (which can be up to a
 *   minute — `rate_limit_anon_read_per_minute`, `backend/app/api/config.py`).
 *   Never terminal: this always resolves into either `ready` (the retry
 *   worked) or `rate-limited` (it didn't). Distinct from `loading` so the
 *   reader sees an honest "the archive is busy, retrying automatically"
 *   instead of an indefinite spinner for however long that wait takes —
 *   see `requestCache.ts`'s `onWaiting` for how this reaches every
 *   subscriber of a shared request, not just the one that started it.
 * - `rate-limited`  — the anonymous-read budget
 *   (`rate_limit_anon_read_per_minute`, `backend/app/api/config.py`) is
 *   exhausted. `requestScientificJson` already retries once automatically
 *   after the archive's own `Retry-After` wait, so this only shows up when
 *   TWO consecutive attempts hit 429 — sustained pressure, not one burst.
 *   Distinct from `unavailable`: absence describes the request, null
 *   describes the data, and a rate limit is neither — it needs its own
 *   wording (and `retryAfterSeconds`) rather than the generic "try again
 *   later" that used to be shown here (and was wrong: the record was
 *   never actually unavailable, just throttled).
 * - `unavailable`   — anything else (5xx, network failure, aborted
 *   request that still surfaced an error) — the one case where "try
 *   again later" is honest advice.
 *
 * Originally duplicated near-verbatim inside `useConformerGroup` and
 * `useConformerObservation`; factored out so a fix here reaches every
 * `/scientific/*` detail page instead of needing to be reapplied file by
 * file.
 */
export type ScientificRecordState<T> =
    | { ref: string; status: "loading" }
    | { ref: string; status: "retrying"; retryAfterSeconds: number }
    | { ref: string; status: "missing" }
    | { ref: string; status: "invalid"; detail: string }
    | { ref: string; status: "unprocessable"; detail: string }
    | { ref: string; status: "malformed" }
    | { ref: string; status: "rate-limited"; retryAfterSeconds: number }
    | { ref: string; status: "unavailable" }
    | { ref: string; status: "ready"; record: T }

const INVALID_HANDLE_CODES = new Set(["handle_type_mismatch", "invalid_handle"])

export function useScientificRecord<T>(
    ref: string,
    load: (ref: string, signal: AbortSignal, onRateLimited?: (retryAfterSeconds: number) => void) => Promise<T>,
): ScientificRecordState<T> {
    const [state, setState] = useState<ScientificRecordState<T>>({ ref, status: "loading" })
    const visibleState = state.ref === ref ? state : { ref, status: "loading" as const }

    useEffect(() => {
        let mounted = true
        // `load` (e.g. `loadEntryThermo`) is a stable, module-level export
        // for every caller of this hook -- see `requestCache.ts` -- so it
        // doubles as the cache's namespace. Remounting into the same tab
        // (`EntryStatmechSection`/`EntryThermoSection`/`EntryTransportSection`
        // unmount on every section switch, per `SpeciesEntryPage`'s
        // `TabPanelBody`) or navigating Back to a page this hook already
        // loaded reuses the cached response instead of refiring the request.
        //
        // `dedupedFetch` owns the request's `AbortSignal`, never this
        // effect -- see the module docstring on `requestCache.ts` for why
        // (in short: a signal this effect owned could be aborted by an
        // UNRELATED subscriber's cleanup, e.g. React StrictMode's dev-only
        // double-invoke, silently starving this one of any response at
        // all). This effect only tracks its OWN `mounted` flag, and an
        // `AbortError` here always means "the shared request was cancelled
        // because every subscriber left" -- never "someone else's cleanup
        // ran" -- so treating it as "do nothing" is always correct.
        const subscription = dedupedFetch(load, ref, (signal, onRateLimited) => load(ref, signal, onRateLimited))
        const stopWaiting = subscription.onWaiting((retryAfterSeconds) => {
            if (mounted) setState({ ref, status: "retrying", retryAfterSeconds })
        })
        subscription.promise
            .then((record) => {
                if (mounted) setState({ ref, status: "ready", record })
            })
            .catch((error: unknown) => {
                if (!mounted) return
                if (error instanceof DOMException && error.name === "AbortError") return
                if (error instanceof ScientificRateLimitError) {
                    setState({ ref, status: "rate-limited", retryAfterSeconds: error.retryAfterSeconds })
                    return
                }
                if (error instanceof ScientificApiError && error.status === 404) {
                    setState({ ref, status: "missing" })
                    return
                }
                if (error instanceof ScientificApiError && error.status === 422) {
                    if (error.code !== undefined && !INVALID_HANDLE_CODES.has(error.code)) {
                        setState({ ref, status: "unprocessable", detail: error.message })
                        return
                    }
                    setState({ ref, status: "invalid", detail: error.message })
                    return
                }
                if (error instanceof ScientificApiError && /malformed/i.test(error.message)) {
                    setState({ ref, status: "malformed" })
                    return
                }
                setState({ ref, status: "unavailable" })
            })
        return () => {
            mounted = false
            stopWaiting()
            subscription.unsubscribe()
        }
    }, [ref, load])
    return visibleState
}
