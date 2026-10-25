import { loadEntryThermo } from "../api/thermoApi"
import { usePagedList } from "./usePagedList"

/**
 * Loads one page of the thermo records deposited for one species entry (the first, until `goTo` moves; the list is paged, never silently cut). No include
 * tokens: see the module docstring on `api/thermoApi.ts` — nothing on this
 * surface is include-gated.
 */
export function useEntryThermo(entryRef: string) {
    return usePagedList(entryRef, loadEntryThermo)
}
