import { useEffect, useState } from "react"
import type { BrowseFilters, BrowseKind, BrowseResult } from "../api/browseApi"
import { loadBrowse } from "../api/browseApi"
import { ScientificApiError } from "../api/scientificTransport"

/**
 * A filter edit fires a request per keystroke (see `BrowseFilterForm`'s
 * doc comment); each earlier in-flight request is aborted, but without
 * this delay every keystroke would still open and immediately cancel a
 * request, and a slow one landing after its abort-check races the next
 * keystroke's "loading" flash. A short, deliberately-invisible debounce
 * -- well under `waitFor`'s default 1s polling window used throughout
 * this page's tests -- coalesces a burst of keystrokes into one request.
 */
const REQUEST_DEBOUNCE_MS = 200

/**
 * Five states, matching `useScientificRecord`'s "never collapse distinct
 * failures into one copy" precedent at the surface `useBrowse` actually
 * needs: a page still LOADING its first page; three separate ways a load
 * can fail -- `invalid` (HTTP 422, the request itself was rejected, e.g. a
 * bad filter value or an offset past the archive's cap -- `detail` carries
 * the archive's own reason, permanently wrong until the request changes,
 * so it must never share copy with a transient outage), `malformed` (a 200
 * whose body failed our own schema validation -- an archive-side bug, not
 * a connection problem), and `unavailable` (5xx, network failure, or
 * anything else -- the one case where "try again later" is honest advice);
 * and a page that loaded successfully whether or not it came back with
 * zero rows. The zero-rows-vs-not distinction, and WHY it is zero, is a
 * `BrowsePage` concern (`domain/browseEmptyState.ts`) -- `ready` always
 * carries whatever the archive returned, empty or not, so that split can
 * be made from real data rather than baked into this hook's state machine.
 */
export type BrowseState =
    | { status: "loading" }
    | { status: "ready"; result: BrowseResult }
    | { status: "invalid"; detail: string }
    | { status: "malformed" }
    | { status: "unavailable" }

export function useBrowse(kind: BrowseKind, filters: BrowseFilters, offset: number, limit: number): BrowseState {
    const [state, setState] = useState<BrowseState>({ status: "loading" })

    useEffect(() => {
        const controller = new AbortController()
        setState({ status: "loading" })
        const timer = setTimeout(() => {
            loadBrowse(kind, filters, offset, limit, controller.signal)
                .then((result) => setState({ status: "ready", result }))
                .catch((error: unknown) => {
                    if (controller.signal.aborted) return
                    if (error instanceof DOMException && error.name === "AbortError") return
                    if (error instanceof ScientificApiError && error.status === 422) {
                        setState({ status: "invalid", detail: error.message })
                        return
                    }
                    if (error instanceof ScientificApiError && /malformed/i.test(error.message)) {
                        setState({ status: "malformed" })
                        return
                    }
                    setState({ status: "unavailable" })
                })
        }, REQUEST_DEBOUNCE_MS)
        return () => { clearTimeout(timer); controller.abort() }
        // eslint-disable-next-line react-hooks/exhaustive-deps -- `filters` is a fresh object every render; re-run on its VALUES (JSON.stringify), not its identity, or every keystroke-adjacent render would spuriously refetch.
    }, [kind, JSON.stringify(filters), offset, limit])

    return state
}
