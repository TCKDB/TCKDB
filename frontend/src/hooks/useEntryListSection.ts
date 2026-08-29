import { useCallback, useEffect, useRef, useState } from "react"
import { ScientificApiError } from "../api/scientificTransport"

/**
 * State for one heavy include token on an entry-scoped LIST surface
 * (thermo/statmech/transport). Distinct from `CalculationSectionState`
 * (`hooks/useCalculationSection.ts`) only in what "ready" carries: a
 * ref-keyed map of every record's own value for that token, rather than one
 * record's own field. A section that hasn't been opened yet is `idle`, not
 * `loading` — nothing has been asked of the archive.
 */
export type EntryListSectionState<TField> =
    | { status: "idle" }
    | { status: "loading" }
    | { status: "error"; message: string }
    | { status: "ready"; dataByRef: Map<string, TField> }

/**
 * Fetches exactly one heavy include token for an ENTRY-SCOPED LIST surface,
 * on demand, the first time `request()` is called. Shared by
 * `EntryStatmechSection.tsx` and `EntryTransportSection.tsx` — the state
 * machine (idle/loading/error/ready, fetch-once via `inFlightKeyRef`,
 * stale-response guarding via a `(entryRef, token)` key, abort-on-unmount
 * via a cleanup-only effect) mirrors `useCalculationSection` exactly. What
 * differs is the shape of "ready": the calculation surface addresses one
 * record directly (`/calculations/{ref}?include=<token>`), but
 * thermo/statmech/transport are ENTRY-SCOPED LISTS — `include=<token>` on
 * this endpoint gates that field on EVERY record the list returns, not on
 * one record singled out by ref (verified live — see the module docstring
 * on `api/statmechApi.ts`). So "open this section" here means "refetch the
 * whole entry-scoped list with exactly this one additional token", once,
 * and the result is a ref-keyed map so each record's own disclosure reads
 * only its own key — never the first record's, never another record's.
 *
 * One hook call is made per TOKEN at the section level (not per record), so
 * opening any one record's disclosure for a token shares its single fetch
 * with every other record's disclosure for that same token — the caller
 * passes the same `[state, open]` pair to every per-record row.
 */
export function useEntryListSection<TRecord, TField, TToken extends string = string>(
    entryRef: string,
    token: TToken,
    load: (entryRef: string, token: TToken, signal: AbortSignal) => Promise<TRecord[]>,
    recordRef: (record: TRecord) => string,
    readField: (record: TRecord) => TField,
): [EntryListSectionState<TField>, () => void] {
    const key = `${entryRef}::${token}`
    const [entry, setEntry] = useState<{ key: string; state: EntryListSectionState<TField> }>(
        { key, state: { status: "idle" } },
    )
    const visible = entry.key === key ? entry.state : { status: "idle" as const }
    const inFlightKeyRef = useRef<string | null>(null)
    const controllerRef = useRef<AbortController | null>(null)

    useEffect(() => {
        // Cleanup only — aborts whatever this (entryRef, token) pair has in
        // flight when the key changes or the component unmounts. No
        // setState here, only in `request()`, an event handler.
        return () => {
            controllerRef.current?.abort()
        }
    }, [key])

    const request = useCallback(() => {
        if (inFlightKeyRef.current === key) return
        inFlightKeyRef.current = key
        setEntry({ key, state: { status: "loading" } })
        const controller = new AbortController()
        controllerRef.current = controller
        load(entryRef, token, controller.signal)
            .then((records) => {
                const dataByRef = new Map<string, TField>()
                for (const record of records) dataByRef.set(recordRef(record), readField(record))
                setEntry({ key, state: { status: "ready", dataByRef } })
            })
            .catch((error: unknown) => {
                if (controller.signal.aborted) return
                inFlightKeyRef.current = null
                const message = error instanceof ScientificApiError
                    ? error.message
                    : "This section could not be loaded from the archive."
                setEntry({ key, state: { status: "error", message } })
            })
        // eslint-disable-next-line react-hooks/exhaustive-deps -- load/recordRef/readField are stable per token from the caller's module scope
    }, [entryRef, token, key])

    return [visible, request]
}
