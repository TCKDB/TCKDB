import { loadCorrectionScheme, type EnergyCorrectionSchemeRecord } from "../api/methodsApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type CorrectionSchemeState = ScientificRecordState<EnergyCorrectionSchemeRecord>

/** Loads an energy-correction scheme for its standalone `/methods/schemes/:ecsRef` page. */
export function useCorrectionScheme(ref: string): CorrectionSchemeState {
    return useScientificRecord(ref, loadCorrectionScheme)
}
