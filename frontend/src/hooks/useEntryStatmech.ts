import { loadEntryStatmech } from "../api/statmechApi"
import { usePagedRecordList } from "./usePagedRecordList"

/**
 * Loads one page of the statmech records deposited for one species entry (the first, until `goTo` moves; the list is paged, never silently cut), with the
 * eager (always-present) field set only. The six heavy include tokens are
 * opt-in per disclosure via `useEntryListSection` — see
 * `components/EntryStatmechSection.tsx`.
 */
export function useEntryStatmech(entryRef: string) {
    return usePagedRecordList(entryRef, "statmech", loadEntryStatmech, (record) => record.statmech.statmech_ref)
}
