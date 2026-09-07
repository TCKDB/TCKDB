import { describe, expect, it } from "vitest"
import type { LevelOfTheory } from "../api/scientificSchemas"
import {
    EMPTY_PRODUCT_LEVELS,
    allProductLevelsAgree,
    deriveProductLevelsFromSourceCalculations,
    levelsOfTheoryEqual,
    productLevelsAgree,
    resolveProductLevels,
    type ProductLevels,
    type RoleLevelSource,
} from "./productLevels"

const b3lyp: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", level_of_theory_ref: "lot_opt" }
const ccsdt: LevelOfTheory = { method: "ccsd(t)", basis: "cc-pvtz", display: "ccsd(t)/cc-pvtz", level_of_theory_ref: "lot_sp" }
const freqLot: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp", display: "b3lyp/def2tzvp", level_of_theory_ref: "lot_freq" }

function role(roleName: string, level: LevelOfTheory | null): RoleLevelSource {
    return { role: roleName, level_of_theory: level }
}

describe("deriveProductLevelsFromSourceCalculations", () => {
    it("resolves geometry/frequency/energy from distinct opt/freq/sp roles, energy_source sp", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("opt", b3lyp), role("freq", freqLot), role("sp", ccsdt)])
        expect(levels).toEqual({ geometry: b3lyp, frequency: freqLot, energy: ccsdt, energy_source: "sp" })
    })

    it("falls back frequency to the opt's level when no freq role is present", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("opt", b3lyp), role("sp", ccsdt)])
        expect(levels.frequency).toEqual(b3lyp)
        expect(levels.energy_source).toBe("sp")
    })

    it("falls back energy to the opt's level, energy_source opt, when no sp role is present", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("opt", b3lyp), role("freq", freqLot)])
        expect(levels.energy).toEqual(b3lyp)
        expect(levels.energy_source).toBe("opt")
    })

    it("an optimisation-only record (opt role alone) collapses all three to the opt's level", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("opt", b3lyp)])
        expect(levels).toEqual({ geometry: b3lyp, frequency: b3lyp, energy: b3lyp, energy_source: "opt" })
    })

    it("no opt role: geometry stays null even when freq/sp are present (no role represents geometry to fall back to)", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("freq", freqLot), role("sp", ccsdt)])
        expect(levels).toEqual({ geometry: null, frequency: freqLot, energy: ccsdt, energy_source: "sp" })
    })

    it("no opt and no sp role: energy stays null, energy_source null (nothing to fall back to)", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("freq", freqLot)])
        expect(levels).toEqual({ geometry: null, frequency: freqLot, energy: null, energy_source: null })
    })

    it("empty source_calculations array returns EMPTY_PRODUCT_LEVELS", () => {
        expect(deriveProductLevelsFromSourceCalculations([])).toEqual(EMPTY_PRODUCT_LEVELS)
    })

    it("null source_calculations returns EMPTY_PRODUCT_LEVELS", () => {
        expect(deriveProductLevelsFromSourceCalculations(null)).toEqual(EMPTY_PRODUCT_LEVELS)
    })

    it("undefined source_calculations returns EMPTY_PRODUCT_LEVELS", () => {
        expect(deriveProductLevelsFromSourceCalculations(undefined)).toEqual(EMPTY_PRODUCT_LEVELS)
    })

    it("roles present but with no level_of_theory of their own still yield EMPTY_PRODUCT_LEVELS", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("opt", null), role("freq", null), role("sp", null)])
        expect(levels).toEqual(EMPTY_PRODUCT_LEVELS)
    })

    it("a role missing level_of_theory entirely (field absent, not just null) is treated the same as null", () => {
        const levels = deriveProductLevelsFromSourceCalculations([{ role: "opt" }, role("sp", ccsdt)])
        expect(levels.geometry).toBeNull()
        expect(levels.energy).toEqual(ccsdt)
    })

    it("the FIRST matching role wins when a role repeats (a re-run) — order in the array is authoritative", () => {
        const secondOpt: LevelOfTheory = { method: "b3lyp", basis: "6-31g" }
        const levels = deriveProductLevelsFromSourceCalculations([role("opt", b3lyp), role("opt", secondOpt)])
        expect(levels.geometry).toEqual(b3lyp)
    })

    it("roles unrelated to opt/freq/sp are ignored", () => {
        const levels = deriveProductLevelsFromSourceCalculations([role("scan", ccsdt), role("opt", b3lyp)])
        expect(levels.geometry).toEqual(b3lyp)
        expect(levels.energy).toEqual(b3lyp)
    })
})

describe("resolveProductLevels", () => {
    it("prefers the server's own `levels` object when present, over any source_calculations", () => {
        const wireLevels = { geometry: b3lyp, frequency: freqLot, energy: ccsdt, energy_source: "sp" }
        const levels = resolveProductLevels(wireLevels, [role("opt", ccsdt)])
        expect(levels).toEqual(wireLevels)
    })

    it("normalises a `levels` object with missing fields to null rather than undefined", () => {
        const levels = resolveProductLevels({ geometry: b3lyp }, null)
        expect(levels).toEqual({ geometry: b3lyp, frequency: null, energy: null, energy_source: null })
    })

    it("an explicitly-empty `levels` object (every field null) is trusted as-is, never re-derived from source_calculations", () => {
        const levels = resolveProductLevels(
            { geometry: null, frequency: null, energy: null, energy_source: null },
            [role("opt", b3lyp)],
        )
        expect(levels).toEqual(EMPTY_PRODUCT_LEVELS)
    })

    it("falls back to deriving from source_calculations when `levels` is null", () => {
        const levels = resolveProductLevels(null, [role("opt", b3lyp), role("sp", ccsdt)])
        expect(levels).toEqual({ geometry: b3lyp, frequency: b3lyp, energy: ccsdt, energy_source: "sp" })
    })

    it("falls back to deriving from source_calculations when `levels` is undefined", () => {
        const levels = resolveProductLevels(undefined, [role("opt", b3lyp)])
        expect(levels.geometry).toEqual(b3lyp)
    })

    it("both levels and source_calculations absent: EMPTY_PRODUCT_LEVELS, not an error", () => {
        expect(resolveProductLevels(null, null)).toEqual(EMPTY_PRODUCT_LEVELS)
    })
})

describe("levelsOfTheoryEqual", () => {
    it("two nulls are equal", () => {
        expect(levelsOfTheoryEqual(null, null)).toBe(true)
    })

    it("a null and a recorded level are never equal", () => {
        expect(levelsOfTheoryEqual(null, b3lyp)).toBe(false)
        expect(levelsOfTheoryEqual(b3lyp, null)).toBe(false)
    })

    it("two levels sharing the same ref are equal even if their labels differ", () => {
        const a: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp", level_of_theory_ref: "lot_1" }
        const b: LevelOfTheory = { method: "different-label-somehow", level_of_theory_ref: "lot_1" }
        expect(levelsOfTheoryEqual(a, b)).toBe(true)
    })

    it("two levels with different refs are never equal, even with identical method/basis", () => {
        const a: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp", level_of_theory_ref: "lot_1" }
        const b: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp", level_of_theory_ref: "lot_2" }
        expect(levelsOfTheoryEqual(a, b)).toBe(false)
    })

    it("without a ref on either side, falls back to method/basis/dispersion/solvent content equality", () => {
        const a: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp" }
        const b: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp" }
        expect(levelsOfTheoryEqual(a, b)).toBe(true)
    })

    it("same method/basis but different dispersion treatment: not equal", () => {
        const a: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp", dispersion: "d3bj" }
        const b: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp", dispersion: null }
        expect(levelsOfTheoryEqual(a, b)).toBe(false)
    })
})

function levels(overrides: Partial<ProductLevels> = {}): ProductLevels {
    return { geometry: b3lyp, frequency: b3lyp, energy: b3lyp, energy_source: "opt", ...overrides }
}

describe("productLevelsAgree", () => {
    it("all three the same level of theory: agree", () => {
        expect(productLevelsAgree(levels())).toBe(true)
    })

    it("all three null: agree (one shared 'not recorded' fact)", () => {
        expect(productLevelsAgree(EMPTY_PRODUCT_LEVELS)).toBe(true)
    })

    it("energy at a different level of theory than geometry/frequency: disagree", () => {
        expect(productLevelsAgree(levels({ energy: ccsdt }))).toBe(false)
    })

    it("frequency null while geometry/energy are recorded: disagree, not agree-by-coincidence", () => {
        expect(productLevelsAgree(levels({ frequency: null }))).toBe(false)
    })

    it("every field individually equal by content (not object identity) still agrees", () => {
        const a: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp" }
        const b: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp" }
        const c: LevelOfTheory = { method: "b3lyp", basis: "def2tzvp" }
        expect(productLevelsAgree({ geometry: a, frequency: b, energy: c, energy_source: "opt" })).toBe(true)
    })
})

describe("allProductLevelsAgree", () => {
    it("a single-record list trivially agrees", () => {
        expect(allProductLevelsAgree([levels()])).toBe(true)
    })

    it("an empty list agrees vacuously", () => {
        expect(allProductLevelsAgree([])).toBe(true)
    })

    it("multiple records with identical geometry/frequency/energy agree", () => {
        expect(allProductLevelsAgree([levels(), levels(), levels()])).toBe(true)
    })

    it("energy_source differing alone does not break agreement — only the levels themselves are compared", () => {
        expect(allProductLevelsAgree([levels({ energy_source: "opt" }), levels({ energy_source: "sp" })])).toBe(true)
    })

    it("one record's energy at a different level of theory than the rest: disagree", () => {
        expect(allProductLevelsAgree([levels(), levels(), levels({ energy: ccsdt })])).toBe(false)
    })

    it("one record's geometry missing while the rest have it recorded: disagree", () => {
        expect(allProductLevelsAgree([levels(), levels({ geometry: null })])).toBe(false)
    })
})
