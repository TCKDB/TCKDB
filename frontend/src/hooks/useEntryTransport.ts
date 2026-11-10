import { loadEntryTransportWithSources } from "../api/transportApi"
import { usePagedRecordList } from "./useLinkedRecordPage"

/**
 * Loads one page of the transport records deposited for one species entry (the first, until `goTo` moves; the list is
 * paged, never silently cut), with the source calculations each record cites, so the numbers table on the same page
 * and this tab share one request. The review history is opt-in per disclosure via `useEntryListSection`: see
 * `components/EntryTransportSection.tsx`.
 */
export function useEntryTransport(entryRef: string) {
    return usePagedRecordList(entryRef, "transport", loadEntryTransportWithSources, (record) => record.transport.transport_ref)
}
