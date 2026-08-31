import { useEffect, useState } from "react"
import type { BrowseFilters, BrowseKind, BrowseResult } from "../api/browseApi"
import { loadBrowse } from "../api/browseApi"

/**
 * Three states, matching `RecordStatus`'s "never collapse distinct
 * failures into one copy" rule at the surface `useBrowse` actually needs:
 * a page still LOADING its first page, a page whose request FAILED
 * (network/HTTP -- "try again later" is honest advice here), and a page
 * that loaded successfully whether or not it came back with zero rows.
 * The zero-rows-vs-not distinction, and WHY it is zero, is a `BrowsePage`
 * concern (`domain/browseEmptyState.ts`) -- `ready` always carries
 * whatever the archive returned, empty or not, so that split can be made
 * from real data rather than baked into this hook's state machine.
 */
export type BrowseState =
    | { status: "loading" }
    | { status: "ready"; result: BrowseResult }
    | { status: "error"; detail: string }

export function useBrowse(kind: BrowseKind, filters: BrowseFilters, offset: number, limit: number): BrowseState {
    const [state, setState] = useState<BrowseState>({ status: "loading" })

    useEffect(() => {
        const controller = new AbortController()
        setState({ status: "loading" })
        loadBrowse(kind, filters, offset, limit, controller.signal)
            .then((result) => setState({ status: "ready", result }))
            .catch((error: unknown) => {
                if (controller.signal.aborted) return
                if (error instanceof DOMException && error.name === "AbortError") return
                setState({ status: "error", detail: "The archive service could not load this listing. Try again later." })
            })
        return () => controller.abort()
        // eslint-disable-next-line react-hooks/exhaustive-deps -- `filters` is a fresh object every render; re-run on its VALUES (JSON.stringify), not its identity, or every keystroke-adjacent render would spuriously refetch.
    }, [kind, JSON.stringify(filters), offset, limit])

    return state
}
