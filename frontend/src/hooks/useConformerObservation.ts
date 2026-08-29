import { loadConformerObservation, type ConformerObservation } from "../api/conformerObservationApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type ConformerObservationState = ScientificRecordState<ConformerObservation>

export function useConformerObservation(ref: string): ConformerObservationState {
    return useScientificRecord(ref, loadConformerObservation)
}
