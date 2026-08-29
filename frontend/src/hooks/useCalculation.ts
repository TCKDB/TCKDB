import { loadCalculation, type CalculationRecord } from "../api/calculationApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type CalculationState = ScientificRecordState<CalculationRecord>

/**
 * Loads a calculation with the page's eager section set (results,
 * dependencies, review, input/output geometries — see
 * `EAGER_SECTION_TOKENS` in `api/calculationApi.ts`). The remaining heavy
 * sections are opt-in-per-disclosure via `useCalculationSection`.
 */
export function useCalculation(ref: string): CalculationState {
    return useScientificRecord(ref, loadCalculation)
}
