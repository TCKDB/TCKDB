import { describe, expect, it } from "vitest"
import {
    GAS_CONSTANT_J_MOL_K,
    JOULES_PER_CALORIE,
    convertCpForDisplay,
    cpResiduals,
    evaluateNasaCpJMolK,
    isNasaFitUsable,
    nasaCpAtTemperature,
    nasaCpCurve,
    type NasaBlock,
} from "./thermoNasa"

describe("evaluateNasaCpJMolK", () => {
    it("matches a hand-checked value: a=[3.5, 1e-3, -2e-7, 0, 0] at T=1000K", () => {
        // Cp/R = 3.5 + 0.001*1000 + (-0.0000002)*1000^2 + 0 + 0
        //      = 3.5 + 1.0 - 0.2 = 4.3
        // Cp   = 4.3 * 8.314462618 = 35.7521892574 J/mol/K (hand arithmetic)
        const cp = evaluateNasaCpJMolK([3.5, 0.001, -0.0000002, 0, 0], 1000)
        expect(cp).toBeCloseTo(35.7521892574, 6)
    })

    it("reduces to the gas constant itself when a1=1 and every other coefficient is 0", () => {
        expect(evaluateNasaCpJMolK([1, 0, 0, 0, 0], 250)).toBeCloseTo(GAS_CONSTANT_J_MOL_K, 12)
    })
})

function nasaFixture(overrides: Partial<NasaBlock> = {}): NasaBlock {
    return {
        t_low: 100,
        t_mid: 500,
        t_high: 1000,
        // Deliberately DIFFERENT low/high sets (a1 = 1 vs a1 = 2, everything
        // else zero) so a wrong branch produces a visibly wrong Cp (R vs 2R)
        // rather than a fixture that can't distinguish the two branches at all.
        low_temperature_coefficients: [1, 0, 0, 0, 0, 0, 0],
        high_temperature_coefficients: [2, 0, 0, 0, 0, 0, 0],
        ...overrides,
    }
}

describe("isNasaFitUsable", () => {
    it("is usable when boundaries are ordered and both coefficient sets are full", () => {
        expect(isNasaFitUsable(nasaFixture())).toBe(true)
    })

    it("is not usable when nasa itself is null", () => {
        expect(isNasaFitUsable(null)).toBe(false)
    })

    it("is not usable when t_mid is missing", () => {
        expect(isNasaFitUsable(nasaFixture({ t_mid: null }))).toBe(false)
    })

    it("is not usable when the boundaries are out of order", () => {
        expect(isNasaFitUsable(nasaFixture({ t_low: 500, t_mid: 100, t_high: 1000 }))).toBe(false)
    })

    it("is not usable when a coefficient set is short", () => {
        expect(isNasaFitUsable(nasaFixture({ low_temperature_coefficients: [1, 2, 3] }))).toBe(false)
    })

    it("is not usable when a coefficient set contains a null entry", () => {
        expect(isNasaFitUsable(nasaFixture({ high_temperature_coefficients: [2, null, 0, 0, 0, 0, 0] }))).toBe(false)
    })
})

describe("nasaCpAtTemperature — the t_mid split", () => {
    const nasa = nasaFixture()

    it("uses the LOW coefficients strictly below t_mid", () => {
        // T=200 < t_mid=500 -> a1=1 -> Cp = R
        expect(nasaCpAtTemperature(nasa, 200)).toBeCloseTo(GAS_CONSTANT_J_MOL_K, 9)
    })

    it("uses the HIGH coefficients at and above t_mid", () => {
        // T=800 > t_mid=500 -> a1=2 -> Cp = 2R
        expect(nasaCpAtTemperature(nasa, 800)).toBeCloseTo(2 * GAS_CONSTANT_J_MOL_K, 9)
        // T=500 == t_mid -> CHEMKIN convention: high branch, not low.
        expect(nasaCpAtTemperature(nasa, 500)).toBeCloseTo(2 * GAS_CONSTANT_J_MOL_K, 9)
    })

    it("never extrapolates past t_low/t_high", () => {
        expect(nasaCpAtTemperature(nasa, 99)).toBeNull()
        expect(nasaCpAtTemperature(nasa, 1001)).toBeNull()
    })

    it("returns null for an unusable fit rather than guessing a branch", () => {
        expect(nasaCpAtTemperature(nasaFixture({ t_mid: null }), 200)).toBeNull()
    })
})

describe("nasaCpCurve", () => {
    it("splits into a low branch spanning t_low..t_mid and a high branch spanning t_mid..t_high", () => {
        const curve = nasaCpCurve(nasaFixture(), 5)
        expect(curve).not.toBeNull()
        const { low, high } = curve!
        expect(low[0].temperatureK).toBe(100)
        expect(low.at(-1)!.temperatureK).toBe(500)
        expect(high[0].temperatureK).toBe(500)
        expect(high.at(-1)!.temperatureK).toBe(1000)
    })

    it("evaluates both branches' shared t_mid point with THEIR OWN coefficients, exposing the split as a step", () => {
        const curve = nasaCpCurve(nasaFixture(), 5)!
        // Both branches sample t_mid=500, but with different a1 -> different Cp.
        expect(curve.low.at(-1)!.cpJMolK).toBeCloseTo(GAS_CONSTANT_J_MOL_K, 9)
        expect(curve.high[0].cpJMolK).toBeCloseTo(2 * GAS_CONSTANT_J_MOL_K, 9)
    })

    it("returns null (never a truncated curve) when the fit isn't usable", () => {
        expect(nasaCpCurve(nasaFixture({ t_high: null }))).toBeNull()
    })
})

describe("cpResiduals", () => {
    const nasa = nasaFixture() // low a1=1 (Cp=R) below 500K, high a1=2 (Cp=2R) at/above 500K

    it("computes measured-minus-fitted at each stored temperature, skipping what it can't evaluate", () => {
        const points = [
            { temperature_k: 200, cp_j_mol_k: GAS_CONSTANT_J_MOL_K + 1 }, // low branch, +1 J/mol/K over fit
            { temperature_k: 800, cp_j_mol_k: null }, // no measured Cp -> skipped
            { temperature_k: 1500, cp_j_mol_k: 999 }, // outside t_high -> skipped, never extrapolated
            { temperature_k: 600, cp_j_mol_k: 2 * GAS_CONSTANT_J_MOL_K }, // high branch, exact match -> 0 residual
        ]
        const residuals = cpResiduals(nasa, points)
        expect(residuals).toHaveLength(2)
        // Sorted by temperature, not wire order.
        expect(residuals[0].temperatureK).toBe(200)
        expect(residuals[0].residualJMolK).toBeCloseTo(1, 9)
        expect(residuals[0].residualPercent).toBeCloseTo(100 / GAS_CONSTANT_J_MOL_K, 6)
        expect(residuals[1].temperatureK).toBe(600)
        expect(residuals[1].residualJMolK).toBeCloseTo(0, 9)
    })

    it("returns an empty list when there are no points or no usable fit", () => {
        expect(cpResiduals(nasa, null)).toEqual([])
        expect(cpResiduals(nasa, [])).toEqual([])
        expect(cpResiduals(nasaFixture({ t_mid: null }), [{ temperature_k: 200, cp_j_mol_k: 10 }])).toEqual([])
    })
})

describe("convertCpForDisplay", () => {
    it("is the identity for J/mol/K", () => {
        expect(convertCpForDisplay(42.195, "j_mol_k")).toBe(42.195)
    })

    it("round-trips the stored J/mol/K value through a cal/mol/K display conversion", () => {
        const storedJ = 29.1
        const displayedCal = convertCpForDisplay(storedJ, "cal_mol_k")
        // The display conversion must never feed back into what's "stored" --
        // this asserts converting cal back to J (by hand, the way a reader
        // checking the toggle against a calculator would) reproduces the
        // original stored number exactly.
        expect(displayedCal * JOULES_PER_CALORIE).toBeCloseTo(storedJ, 10)
        expect(displayedCal).toBeCloseTo(storedJ / 4.184, 10)
    })
})
