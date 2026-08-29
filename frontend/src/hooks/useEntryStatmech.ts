import { loadEntryStatmech, type StatmechListResponse } from "../api/statmechApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type EntryStatmechState = ScientificRecordState<StatmechListResponse>

/**
 * Loads every statmech record deposited for one species entry, with the
 * eager (always-present) field set only. The six heavy include tokens are
 * opt-in per disclosure via `useEntryListSection` — see
 * `components/EntryStatmechSection.tsx`.
 */
export function useEntryStatmech(entryRef: string): EntryStatmechState {
    return useScientificRecord(entryRef, loadEntryStatmech)
}
