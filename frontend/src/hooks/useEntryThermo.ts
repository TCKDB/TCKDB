import { loadEntryThermo } from "../api/thermoApi"
import { usePagedRecordList } from "./usePagedRecordList"

/**
 * Loads one page of the thermo records deposited for one species entry (the first, until `goTo` moves or a fragment names
 * a record on a later page; the list is paged, never silently cut). No include
 * tokens: see the module docstring on `api/thermoApi.ts` — nothing on this
 * surface is include-gated.
 */
export function useEntryThermo(entryRef: string) {
    return usePagedRecordList(entryRef, "thermo", loadEntryThermo, (record) => record.thermo_ref)
}
