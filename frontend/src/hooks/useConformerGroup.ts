import { loadConformerGroup, type ConformerGroup } from "../api/conformerGroupApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type ConformerGroupState = ScientificRecordState<ConformerGroup>

export function useConformerGroup(ref: string): ConformerGroupState {
    return useScientificRecord(ref, loadConformerGroup)
}
