import { useEffect, useState } from "react"
import { ScientificApiError } from "../api/scientificTransport"

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
    | { ref: string; status: "missing" }
    | { ref: string; status: "invalid"; detail: string }
    | { ref: string; status: "unprocessable"; detail: string }
    | { ref: string; status: "malformed" }
    | { ref: string; status: "unavailable" }
    | { ref: string; status: "ready"; record: T }

const INVALID_HANDLE_CODES = new Set(["handle_type_mismatch", "invalid_handle"])

export function useScientificRecord<T>(
    ref: string,
    load: (ref: string, signal: AbortSignal) => Promise<T>,
): ScientificRecordState<T> {
    const [state, setState] = useState<ScientificRecordState<T>>({ ref, status: "loading" })
    const visibleState = state.ref === ref ? state : { ref, status: "loading" as const }

    useEffect(() => {
        const controller = new AbortController()
        load(ref, controller.signal)
            .then((record) => setState({ ref, status: "ready", record }))
            .catch((error: unknown) => {
                if (controller.signal.aborted) return
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
        return () => controller.abort()
    }, [ref, load])
    return visibleState
}
