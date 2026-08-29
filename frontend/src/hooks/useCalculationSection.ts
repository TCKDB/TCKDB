import { useCallback, useRef, useState } from "react"
import {
    loadCalculationSection,
    readSectionField,
    type CalculationRecord,
    type OnDemandSectionToken,
} from "../api/calculationApi"
import { ScientificApiError } from "../api/scientificTransport"

/**
 * State for one of the fourteen on-demand ("behind a disclosure") heavy
 * calculation sections. Distinct from `ScientificRecordState`: a section
 * that hasn't been opened yet is `idle`, not `loading` — nothing has been
 * asked of the archive, so nothing should look like it is waiting on one.
 */
export type CalculationSectionState<T> =
    | { status: "idle" }
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; data: T }

/**
 * Fetches exactly one heavy include token, on demand, the first time
 * `request()` is called (opening the section's `<details>`). Reusing this
 * per section — rather than one eager fetch of all twenty tokens — is the
 * point of the page's section-loading design: see the module docstring on
 * `CalculationDetailPage.tsx`.
 *
 * Never called for a section `available_sections` already reports empty —
 * callers gate on that first so a known-empty section costs no request.
 *
 * State is keyed by `(calculationRef, token)` and only ever set from
 * inside `request()` (an event handler, not an effect), so a stale
 * response for a since-changed key is recognised and dropped rather than
 * painted over the current one.
 */
export function useCalculationSection<T>(
    calculationRef: string,
    token: OnDemandSectionToken,
): [CalculationSectionState<T>, () => void] {
    const key = `${calculationRef}::${token}`
    const [entry, setEntry] = useState<{ key: string; state: CalculationSectionState<T> }>(
        { key, state: { status: "idle" } },
    )
    const visible = entry.key === key ? entry.state : { status: "idle" as const }
    const inFlightKeyRef = useRef<string | null>(null)

    const request = useCallback(() => {
        if (inFlightKeyRef.current === key) return
        inFlightKeyRef.current = key
        setEntry({ key, state: { status: "loading" } })
        const controller = new AbortController()
        loadCalculationSection(calculationRef, token, controller.signal)
            .then((record: CalculationRecord) => {
                setEntry({ key, state: { status: "ready", data: readSectionField<T>(record, token) } })
            })
            .catch((error: unknown) => {
                if (controller.signal.aborted) return
                inFlightKeyRef.current = null
                const message = error instanceof ScientificApiError
                    ? error.message
                    : "This section could not be loaded from the archive."
                setEntry({ key, state: { status: "error", message } })
            })
    }, [calculationRef, token, key])

    return [visible, request]
}
