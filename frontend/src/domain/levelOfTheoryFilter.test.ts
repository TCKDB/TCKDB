import { describe, expect, it } from "vitest"
import { levelOfTheoryBrowseQuery, type LevelOfTheoryRecord } from "../api/methodsApi"
import { EMPTY_LOT_FILTER, filterLevelOfTheoryRecords, isLotFilterActive } from "./levelOfTheoryFilter"

function record(ref: string, core: string | null): LevelOfTheoryRecord {
    return {
        level_of_theory: { level_of_theory_ref: ref, method: "ccsd(t)", basis: "cc-pcvtz", core_treatment: core, lot_hash: "h", created_at: "2026-10-03" },
        evidence_summary: { calculation_usage_count: 1, has_correction_schemes: false, has_frequency_scale_factors: false, distinct_software_count: 1 },
        available_sections: { has_correction_schemes: false, has_frequency_scale_factors: false, has_used_by: false, has_software: false },
    } as LevelOfTheoryRecord
}

const rows = [record("a", "frozen_core"), record("b", "all_electron"), record("c", null)]

describe("core_treatment filter", () => {
    it("sends the API's own core_treatment parameter, only when chosen", () => {
        expect(levelOfTheoryBrowseQuery({ coreTreatment: "frozen_core" }).get("core_treatment")).toBe("frozen_core")
        expect(levelOfTheoryBrowseQuery({ coreTreatment: "" }).has("core_treatment")).toBe(false)
        expect(levelOfTheoryBrowseQuery().get("limit")).toBe("200")
    })

    it("keeps only levels that state the chosen value; a level that states nothing matches neither", () => {
        const frozen = filterLevelOfTheoryRecords(rows, { ...EMPTY_LOT_FILTER, coreTreatment: "frozen_core" })
        expect(frozen.map((r) => r.level_of_theory.level_of_theory_ref)).toEqual(["a"])
        const allElectron = filterLevelOfTheoryRecords(rows, { ...EMPTY_LOT_FILTER, coreTreatment: "all_electron" })
        expect(allElectron.map((r) => r.level_of_theory.level_of_theory_ref)).toEqual(["b"])
    })

    it("any keeps every row, including those that state nothing", () => {
        expect(filterLevelOfTheoryRecords(rows, EMPTY_LOT_FILTER)).toHaveLength(3)
    })

    it("counts as an active filter", () => {
        expect(isLotFilterActive(EMPTY_LOT_FILTER)).toBe(false)
        expect(isLotFilterActive({ ...EMPTY_LOT_FILTER, coreTreatment: "all_electron" })).toBe(true)
    })
})
