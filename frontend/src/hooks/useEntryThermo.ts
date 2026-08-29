import { loadEntryThermo, type ThermoListResponse } from "../api/thermoApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type EntryThermoState = ScientificRecordState<ThermoListResponse>

/**
 * Loads every thermo record deposited for one species entry. No include
 * tokens: see the module docstring on `api/thermoApi.ts` — nothing on this
 * surface is include-gated.
 */
export function useEntryThermo(entryRef: string): EntryThermoState {
    return useScientificRecord(entryRef, loadEntryThermo)
}
