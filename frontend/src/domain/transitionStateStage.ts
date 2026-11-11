import type { TransitionStateEntryRecord } from "../api/transitionStateEntryApi"

/** The geometry to draw: the first final geometry the entry's calculations produced; nothing is chosen among others. */
export function stageGeometryRef(record: Pick<TransitionStateEntryRecord, "geometries">): string | null {
    const final = (record.geometries ?? []).filter((geometry) => geometry.role === "final")
    return final.length > 0 ? final[0].geometry_ref : null
}
