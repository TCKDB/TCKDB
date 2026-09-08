import { describe, expect, it } from "vitest"
import {
    arrheniusUnitConversionFactor,
    arrheniusUnitFamily,
    convertArrheniusValue,
    familyUnits,
} from "./arrheniusUnits"

// Hand-computed independently (python, not this module -- same precedent
// `kineticsTable.test.ts` follows), NOT by re-running this file's own
// formula:
//   cm3->m3:        3025.44 * 1e-6                    = 0.00302544
//   cm3->molecule:  3025.44 / 6.02214076e23            = 5.0238613153904425e-21
//   cm6->m6:        4.5e11  * 1e-12                    = 0.45
//   cm6->molecule2: 4.5e11  / (6.02214076e23)**2       = 1.240825497124765e-36
const ORDER2_A = 3025.44
const ORDER3_A = 4.5e11

describe("arrheniusUnitFamily", () => {
    it("places every ArrheniusAUnits value in its own order family", () => {
        expect(arrheniusUnitFamily("per_s")).toBe(1)
        expect(arrheniusUnitFamily("cm3_mol_s")).toBe(2)
        expect(arrheniusUnitFamily("m3_mol_s")).toBe(2)
        expect(arrheniusUnitFamily("cm3_molecule_s")).toBe(2)
        expect(arrheniusUnitFamily("cm6_mol2_s")).toBe(3)
        expect(arrheniusUnitFamily("m6_mol2_s")).toBe(3)
        expect(arrheniusUnitFamily("cm6_molecule2_s")).toBe(3)
    })

    it("is null for unrecorded (null/undefined) and unrecognised units -- never guesses a family", () => {
        expect(arrheniusUnitFamily(null)).toBeNull()
        expect(arrheniusUnitFamily(undefined)).toBeNull()
        expect(arrheniusUnitFamily("weird_future_unit")).toBeNull()
    })
})

describe("familyUnits", () => {
    it("order 1 (per_s) has exactly one member -- no sibling unit exists", () => {
        expect(familyUnits(1)).toEqual(["per_s"])
    })

    it("order 2 lists cm3_mol_s, m3_mol_s, cm3_molecule_s -- base unit first", () => {
        expect(familyUnits(2)).toEqual(["cm3_mol_s", "m3_mol_s", "cm3_molecule_s"])
    })

    it("order 3 lists cm6_mol2_s, m6_mol2_s, cm6_molecule2_s -- base unit first", () => {
        expect(familyUnits(3)).toEqual(["cm6_mol2_s", "m6_mol2_s", "cm6_molecule2_s"])
    })
})

describe("arrheniusUnitConversionFactor / convertArrheniusValue -- pinned against hand-computed values, one per family", () => {
    it("order 1 (per_s): the identity, the family's only member", () => {
        expect(arrheniusUnitConversionFactor("per_s", "per_s")).toBe(1)
        expect(convertArrheniusValue(9444750000, "per_s", "per_s")).toBe(9444750000)
    })

    it("order 2: cm3_mol_s -> m3_mol_s multiplies by 1e-6 (1 m³ = 1e6 cm³)", () => {
        expect(arrheniusUnitConversionFactor("cm3_mol_s", "m3_mol_s")).toBe(1e-6)
        expect(convertArrheniusValue(ORDER2_A, "cm3_mol_s", "m3_mol_s")).toBeCloseTo(0.00302544, 12)
    })

    it("order 2: cm3_mol_s -> cm3_molecule_s divides by N_A", () => {
        const converted = convertArrheniusValue(ORDER2_A, "cm3_mol_s", "cm3_molecule_s")!
        // Relative comparison (not `toBeCloseTo`'s absolute decimal-place
        // check, which is meaningless at ~1e-21 magnitude): must match the
        // independently hand-computed value to 1 part in 1e10.
        expect(Math.abs(converted - 5.0238613153904425e-21) / 5.0238613153904425e-21).toBeLessThan(1e-10)
    })

    it("order 3: cm6_mol2_s -> m6_mol2_s multiplies by 1e-12 (1 m⁶ = 1e12 cm⁶)", () => {
        expect(arrheniusUnitConversionFactor("cm6_mol2_s", "m6_mol2_s")).toBe(1e-12)
        expect(convertArrheniusValue(ORDER3_A, "cm6_mol2_s", "m6_mol2_s")).toBeCloseTo(0.45, 9)
    })

    it("order 3: cm6_mol2_s -> cm6_molecule2_s divides by N_A² (TWO concentration factors)", () => {
        const converted = convertArrheniusValue(ORDER3_A, "cm6_mol2_s", "cm6_molecule2_s")!
        expect(Math.abs(converted - 1.240825497124765e-36) / 1.240825497124765e-36).toBeLessThan(1e-10)
    })

    it("refuses a cross-family conversion (null), regardless of direction, order 1<->2, 1<->3, and 2<->3", () => {
        expect(arrheniusUnitConversionFactor("per_s", "cm3_mol_s")).toBeNull()
        expect(arrheniusUnitConversionFactor("cm3_mol_s", "per_s")).toBeNull()
        expect(arrheniusUnitConversionFactor("per_s", "cm6_mol2_s")).toBeNull()
        expect(arrheniusUnitConversionFactor("cm3_mol_s", "cm6_mol2_s")).toBeNull()
        expect(arrheniusUnitConversionFactor("m3_mol_s", "m6_mol2_s")).toBeNull()
        expect(convertArrheniusValue(1, "per_s", "cm3_mol_s")).toBeNull()
    })

    it("refuses conversion involving an unrecognised or unrecorded token", () => {
        expect(arrheniusUnitConversionFactor("weird_future_unit", "cm3_mol_s")).toBeNull()
        expect(arrheniusUnitConversionFactor("cm3_mol_s", "weird_future_unit")).toBeNull()
    })

    it("the identical-token case is always exactly 1, even for an unrecognised token converted to itself", () => {
        expect(arrheniusUnitConversionFactor("weird_future_unit", "weird_future_unit")).toBe(1)
    })

    it("round-trips exactly: converting to another unit and back reproduces the original value with no drift", () => {
        const toM3 = convertArrheniusValue(ORDER2_A, "cm3_mol_s", "m3_mol_s")!
        const back = convertArrheniusValue(toM3, "m3_mol_s", "cm3_mol_s")!
        expect(back).toBeCloseTo(ORDER2_A, 9)

        const toMolecule2 = convertArrheniusValue(ORDER3_A, "cm6_mol2_s", "cm6_molecule2_s")!
        const backOrder3 = convertArrheniusValue(toMolecule2, "cm6_molecule2_s", "cm6_mol2_s")!
        expect(Math.abs(backOrder3 - ORDER3_A) / ORDER3_A).toBeLessThan(1e-9)
    })
})
