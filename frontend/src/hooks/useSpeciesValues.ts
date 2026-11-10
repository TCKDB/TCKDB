import { loadSpeciesValues, type SpeciesValues } from "../api/speciesValuesApi"
import { useWalked, type WalkedState } from "./useWalked"

export type SpeciesValuesState = WalkedState<SpeciesValues>

/** Loads every thermo and statmech record of every version of one molecule. */
export function useSpeciesValues(speciesRef: string): SpeciesValuesState {
    return useWalked(speciesRef, loadSpeciesValues)
}
