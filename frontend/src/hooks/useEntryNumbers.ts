import { loadEntryNumbers, loadEntryNumbersPreview, type EntryNumbers } from "../api/entryNumbersApi"
import { useWalked, type WalkedState } from "./useWalked"

export type EntryNumbersState = WalkedState<EntryNumbers>

/** Loads every thermo, statmech and transport record of one entry, showing a rate-limit wait while it lasts. */
/** `preview` reads only the first records of each list. */
export function useEntryNumbers(ref: string, preview = false): EntryNumbersState {
    return useWalked(ref, preview ? loadEntryNumbersPreview : loadEntryNumbers)
}
