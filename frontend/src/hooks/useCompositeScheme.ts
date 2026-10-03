import { loadCompositeScheme, type CompositeSchemeRecord } from "../api/methodsApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type CompositeSchemeState = ScientificRecordState<CompositeSchemeRecord>

/** Loads a composite recipe for its standalone `/methods/composite-schemes/:schemeRef` page. */
export function useCompositeScheme(ref: string): CompositeSchemeState {
    return useScientificRecord(ref, loadCompositeScheme)
}
