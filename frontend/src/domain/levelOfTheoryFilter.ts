import type { CoreTreatmentValue, LevelOfTheoryRecord } from "../api/methodsApi"

/**
 * The `/methods` index's level-of-theory filter. `coreTreatment` is the one
 * facet the SERVER filters (the API's own `core_treatment` parameter, ADR
 * 0021, P7a), so changing it re-queries; the other facets narrow what is
 * already in the browser, as they always did (see `MethodsIndexPage.tsx`).
 */
export type LevelOfTheoryFilter = {
    query: string
    hasCorrectionSchemes: boolean
    hasFrequencyScaleFactors: boolean
    /** `""` is "any"; otherwise the API's `core_treatment` value. */
    coreTreatment: CoreTreatmentValue | ""
}

export const EMPTY_LOT_FILTER: LevelOfTheoryFilter = {
    query: "",
    hasCorrectionSchemes: false,
    hasFrequencyScaleFactors: false,
    coreTreatment: "",
}

export const CORE_TREATMENT_OPTIONS: { value: CoreTreatmentValue | ""; label: string }[] = [
    { value: "", label: "Any" },
    { value: "frozen_core", label: "Frozen core" },
    { value: "all_electron", label: "All electron" },
]

export function isLotFilterActive(filter: LevelOfTheoryFilter): boolean {
    return filter.query.trim() !== ""
        || filter.hasCorrectionSchemes
        || filter.hasFrequencyScaleFactors
        || filter.coreTreatment !== ""
}

/**
 * Narrows the already-fetched rows in the browser rather than re-querying per
 * keystroke. MEASURED before designing: the browse endpoint's `method`/`basis`
 * filters are exact-match, not substring (`_run_lot_query`,
 * `backend/app/services/scientific_read/level_of_theory_search.py`), so
 * sending partially-typed text would silently return zero rows for almost any
 * real query. The whole usage-derived candidate set arrives in one call
 * (`limit=200`), so every row this could narrow is already in hand.
 *
 * `hasCorrectionSchemes`/`hasFrequencyScaleFactors` mirror the same two
 * booleans the endpoint accepts, applied to the `evidence_summary` the fetch
 * already returned. `coreTreatment` is ALSO asked of the server; it is checked
 * here too so a server that ignored the parameter still cannot list a level
 * that does not state the chosen value. A level that states nothing matches
 * neither choice.
 */
export function filterLevelOfTheoryRecords(records: LevelOfTheoryRecord[], filter: LevelOfTheoryFilter): LevelOfTheoryRecord[] {
    const query = filter.query.trim().toLowerCase()
    return records.filter((record) => {
        if (filter.coreTreatment && record.level_of_theory.core_treatment !== filter.coreTreatment) return false
        if (filter.hasCorrectionSchemes && !record.evidence_summary.has_correction_schemes) return false
        if (filter.hasFrequencyScaleFactors && !record.evidence_summary.has_frequency_scale_factors) return false
        if (query) {
            const method = record.level_of_theory.method.toLowerCase()
            const basis = (record.level_of_theory.basis ?? "").toLowerCase()
            if (!method.includes(query) && !basis.includes(query)) return false
        }
        return true
    })
}
