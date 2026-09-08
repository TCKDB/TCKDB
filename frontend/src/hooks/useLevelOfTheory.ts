import { loadLevelOfTheory, type LevelOfTheoryRecord } from "../api/methodsApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type LevelOfTheoryState = ScientificRecordState<LevelOfTheoryRecord>

/** Loads a level of theory with every heavy section requested eagerly -- see `loadLevelOfTheory`'s own doc comment. */
export function useLevelOfTheory(ref: string): LevelOfTheoryState {
    return useScientificRecord(ref, loadLevelOfTheory)
}
