import { describe, expect, it } from "vitest"
import { isotopeHillFormula, isotopeLabel } from "./isotopeLabel"

const H = { element: "H" }
const D = { element: "H", isotope_mass_number: 2 }
const O = { element: "O" }

describe("isotope labels", () => {
    it("labels only atoms with a stored mass number", () => {
        expect(isotopeLabel(H)).toBe("H")
        expect(isotopeLabel(D)).toBe("²H")
        expect(isotopeLabel({ element: "C", isotope_mass_number: 13 })).toBe("¹³C")
    })
    it("never infers an isotope from a D symbol with no mass", () => {
        expect(isotopeLabel({ element: "D", isotope_mass_number: null })).toBe("D")
    })
    it("formulas keep isotopologues distinct", () => {
        expect(isotopeHillFormula([O, D, D])).toBe("²H2O")
        expect(isotopeHillFormula([O, H, D])).toBe("H²HO")
        expect(isotopeHillFormula([O, H, H])).toBe("H2O")
        const c13 = { element: "C", isotope_mass_number: 13 }
        expect(isotopeHillFormula([c13, { element: "C" }, H, H, H, H, H, H])).toBe("C¹³CH6")
    })
})
