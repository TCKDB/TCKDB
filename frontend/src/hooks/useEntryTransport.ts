import { loadEntryTransport } from "../api/transportApi"
import { usePagedList } from "./usePagedList"

/**
 * Loads one page of the transport records deposited for one species entry (the first, until `goTo` moves; the list is paged, never silently cut), with the
 * eager (always-present) field set only. The two heavy include tokens are
 * opt-in per disclosure via `useEntryListSection` — see
 * `components/EntryTransportSection.tsx`.
 */
export function useEntryTransport(entryRef: string) {
    return usePagedList(entryRef, loadEntryTransport)
}
