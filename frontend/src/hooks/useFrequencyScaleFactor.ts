import { loadFrequencyScaleFactor, type FrequencyScaleFactorRecord } from "../api/methodsApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type FrequencyScaleFactorState = ScientificRecordState<FrequencyScaleFactorRecord>

/** Loads a frequency scale factor for its standalone `/methods/frequency-scale-factors/:fsfRef` page. */
export function useFrequencyScaleFactor(ref: string): FrequencyScaleFactorState {
    return useScientificRecord(ref, loadFrequencyScaleFactor)
}
