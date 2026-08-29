import { loadEntryTransport, type TransportListResponse } from "../api/transportApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type EntryTransportState = ScientificRecordState<TransportListResponse>

/**
 * Loads every transport record deposited for one species entry, with the
 * eager (always-present) field set only. The two heavy include tokens are
 * opt-in per disclosure via `useEntryListSection` — see
 * `components/EntryTransportSection.tsx`.
 */
export function useEntryTransport(entryRef: string): EntryTransportState {
    return useScientificRecord(entryRef, loadEntryTransport)
}
