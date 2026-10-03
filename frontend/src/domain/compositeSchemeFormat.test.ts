import { describe, expect, it } from "vitest"
import { asFraction, formatCoefficient, linearSummary, linearityText, termExpression } from "./compositeSchemeFormat"

const lot = (basis: string) => ({ method: "CCSD(T)", basis, display: `CCSD(T)/${basis}`, level_of_theory_ref: `lot_${basis}` })

describe("coefficients", () => {
    it("shows an exact closed-form weight as a fraction with its decimal", () => {
        expect(formatCoefficient(-27 / 37)).toBe("-27/37 (-0.729730)")
        expect(formatCoefficient(64 / 37)).toBe("64/37 (1.729730)")
    })

    it("shows plus and minus one plainly", () => {
        expect(formatCoefficient(1)).toBe("+1")
        expect(formatCoefficient(-1)).toBe("-1")
    })

    it("falls back to a decimal when no small fraction matches", () => {
        expect(asFraction(Math.PI)).toBeNull()
        expect(formatCoefficient(0.123456789)).toBe("+0.123457")
    })
})

describe("termExpression", () => {
    const twoPoint = {
        linearity: "linear",
        inputs: [
            { slot: "cardinal", cardinal_number: 3, coefficient: -27 / 37, level_of_theory: lot("cc-pVTZ") },
            { slot: "cardinal", cardinal_number: 4, coefficient: 64 / 37, level_of_theory: lot("cc-pVQZ") },
        ],
    }

    it("writes a two-point extrapolation as the sum it stands for", () => {
        expect(termExpression(twoPoint)).toBe("−27/37 E(TZ) + 64/37 E(QZ)")
    })

    it("writes a difference with its unit weights", () => {
        const diff = {
            linearity: "linear",
            inputs: [
                { slot: "high", coefficient: 1, level_of_theory: lot("cc-pVQZ") },
                { slot: "low", coefficient: -1, level_of_theory: lot("cc-pVTZ") },
            ],
        }
        expect(termExpression(diff)).toBe("E(CCSD(T)/cc-pVQZ) − E(CCSD(T)/cc-pVTZ)")
    })

    it("gives no expression for a non-linear term, and never invents weights", () => {
        const exp3 = {
            linearity: "nonlinear",
            inputs: [{ slot: "cardinal", cardinal_number: 2, coefficient: null, level_of_theory: lot("cc-pVDZ") }],
        }
        expect(termExpression(exp3)).toBeNull()
        expect(termExpression({ ...twoPoint, linearity: "nonlinear" })).toBeNull()
    })

    it("gives none when any input lacks a coefficient", () => {
        const partial = { linearity: "linear", inputs: [{ ...twoPoint.inputs[0] }, { ...twoPoint.inputs[1], coefficient: null }] }
        expect(termExpression(partial)).toBeNull()
    })
})

describe("linearity wording", () => {
    it("says non-linear with the formula and no fixed coefficients", () => {
        expect(linearityText({ linearity: "nonlinear", formula: "exponential_three_point" }))
            .toBe("non-linear (three-point exponential), no fixed coefficients")
    })

    it("summarises linear_in_energies for true, false and null", () => {
        expect(linearSummary(true)).toContain("fixed weighted sum")
        expect(linearSummary(false)).toContain("not a fixed weighted sum")
        expect(linearSummary(null)).toContain("states no terms")
    })
})
