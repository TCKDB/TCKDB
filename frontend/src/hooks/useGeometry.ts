import { loadGeometry, type GeometryRecord } from "../api/geometryApi"
import { useScientificRecord, type ScientificRecordState } from "./useScientificRecord"

export type GeometryState = ScientificRecordState<GeometryRecord>

/**
 * Loads a geometry with its full payload in one request — unlike the
 * calculation surface, this endpoint has no `available_sections` and no
 * include token actually gates a field, so there is nothing to defer
 * behind a disclosure (see the shape notes in `api/geometryApi.ts`).
 */
export function useGeometry(ref: string): GeometryState {
    return useScientificRecord(ref, loadGeometry)
}
