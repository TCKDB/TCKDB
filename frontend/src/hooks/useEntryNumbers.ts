import { loadEntryNumbers, type EntryNumbers } from "../api/entryNumbersApi"
import { useWalked, type WalkedState } from "./useWalked"

export type EntryNumbersState = WalkedState<EntryNumbers>

/** Loads every thermo, statmech and transport record of one entry, showing a rate-limit wait while it lasts. */
export function useEntryNumbers(ref: string): EntryNumbersState {
    return useWalked(ref, loadEntryNumbers)
}
