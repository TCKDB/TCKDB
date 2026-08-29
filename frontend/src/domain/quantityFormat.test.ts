import { describe, expect, it } from "vitest"
import { ABSENT, fillValue, fixed, formatQuantity, QUANTITY_SPECS, scientific } from "./quantityFormat"

describe("fixed", () => {
    it("rounds to the requested number of digits", () => {
        expect(fixed(143.8942674605864, 2, "kJ/mol")).toEqual({ value: "143.89", unit: "kJ/mol" })
    })

    it("uses a different digit count correctly (regression guard against a hardcoded 2)", () => {
        // If `fixed` ever ignored its `digits` argument and always rounded
        // to 2 places, this would still read "1.2346" -- catching that
        // requires a digit count that is NOT 2.
        expect(fixed(1.23456789, 4)).toEqual({ value: "1.2346", unit: null })
    })

    it("pads short decimals out to the requested precision", () => {
        expect(fixed(5, 2, "K")).toEqual({ value: "5.00", unit: "K" })
    })

    it("returns null for a null or undefined value rather than '0.00'", () => {
        expect(fixed(null, 2, "kJ/mol")).toBeNull()
        expect(fixed(undefined, 2, "kJ/mol")).toBeNull()
    })

    it("defaults unit to null when omitted", () => {
        expect(fixed(1.5, 1)).toEqual({ value: "1.5", unit: null })
    })
})

describe("scientific", () => {
    it("renders a large value in mantissa/exponent form with U+00D7-ready parts and U+2212 exponent sign", () => {
        // 1.2e13 -> magnitude >= 1e4, so this takes the exponential branch.
        const result = scientific(1.2e13, 3, "cm3 mol-1 s-1")
        expect(result).toEqual({ value: "1.20", exponent: "13", unit: "cm3 mol-1 s-1" })
    })

    it("uses U+2212 (real minus) for a negative exponent, never ASCII hyphen", () => {
        const result = scientific(1.2e-13, 3)
        expect(result?.exponent).toBe("−13")
        expect(result?.exponent?.includes("-")).toBe(false)
    })

    it("falls through to plain digits for a value inside the everyday range", () => {
        // magnitude in [1e-2, 1e4) never gets an exponent field.
        const result = scientific(4.2, 4)
        expect(result).toEqual({ value: "4.2", unit: null })
        expect(result && "exponent" in result).toBe(false)
    })

    it("renders exact zero as a plain '0', not '0e+0'", () => {
        expect(scientific(0, 3)).toEqual({ value: "0", unit: null })
    })

    it("returns null for a null, undefined, or non-finite value", () => {
        expect(scientific(null, 3)).toBeNull()
        expect(scientific(undefined, 3)).toBeNull()
        expect(scientific(Number.NaN, 3)).toBeNull()
        expect(scientific(Number.POSITIVE_INFINITY, 3)).toBeNull()
    })

    it("respects the requested significant-figure count", () => {
        // 4 sig figs, not the 3 used in the A-factor example above.
        const result = scientific(6.02214e23, 4)
        expect(result?.value).toBe("6.022")
        expect(result?.exponent).toBe("23")
    })
})

describe("fillValue", () => {
    it("renders null as absent, not as a skipped row", () => {
        expect(fillValue(null)).toEqual({ kind: "absent", text: ABSENT })
    })

    it("renders undefined as absent", () => {
        expect(fillValue(undefined)).toEqual({ kind: "absent", text: ABSENT })
    })

    it("renders an empty string as absent", () => {
        expect(fillValue("")).toEqual({ kind: "absent", text: ABSENT })
    })

    it("renders a named absence using its own sentence, not the generic ABSENT word", () => {
        expect(fillValue({ sentence: "not assigned to a conformer" }))
            .toEqual({ kind: "named-absence", text: "not assigned to a conformer" })
    })

    it("renders a Quantity with its value/unit/exponent intact", () => {
        expect(fillValue({ value: "1.20", unit: "K", exponent: "13" }))
            .toEqual({ kind: "quantity", value: "1.20", unit: "K", exponent: "13" })
    })

    it("renders a plain string or number as plain text", () => {
        expect(fillValue("approved")).toEqual({ kind: "plain", text: "approved" })
        expect(fillValue(7)).toEqual({ kind: "plain", text: "7" })
    })

    it("renders a genuine numeric zero as plain '0', never as absent", () => {
        // 0 is falsy but must not be treated the same as null/undefined/"".
        expect(fillValue(0)).toEqual({ kind: "plain", text: "0" })
    })
})

describe("formatQuantity / QUANTITY_SPECS", () => {
    it("formats thermo H298 at 2dp with kJ/mol", () => {
        expect(formatQuantity("thermo_h298_kj_mol", 143.8942674605864)).toEqual({ value: "143.89", unit: "kJ/mol" })
    })

    it("formats the frequency scale factor at 4dp with no unit", () => {
        expect(formatQuantity("statmech_frequency_scale_factor", 0.9887)).toEqual({ value: "0.9887", unit: null })
    })

    it("formats transport sigma at 3dp with Å", () => {
        expect(formatQuantity("transport_sigma_angstrom", 3.4)).toEqual({ value: "3.400", unit: "Å" })
    })

    it("formats transport epsilon/k at 1dp with K", () => {
        expect(formatQuantity("transport_epsilon_over_k_k", 100)).toEqual({ value: "100.0", unit: "K" })
    })

    it("formats calculation electronic energy at 6dp with hartree", () => {
        expect(formatQuantity("calculation_electronic_energy_hartree", -76.123456789))
            .toEqual({ value: "-76.123457", unit: "hartree" })
    })

    it("formats kinetics A in scientific notation with a caller-supplied unit", () => {
        expect(formatQuantity("kinetics_a", 1.2e13, "cm³ mol⁻¹ s⁻¹"))
            .toEqual({ value: "1.20", exponent: "13", unit: "cm³ mol⁻¹ s⁻¹" })
    })

    it("formats kinetics n in scientific notation with 4 significant figures", () => {
        expect(formatQuantity("kinetics_n", 2.5)).toEqual({ value: "2.5", unit: null })
    })

    it("every spec key has a positive digit count", () => {
        for (const [key, spec] of Object.entries(QUANTITY_SPECS)) {
            expect(spec.digits, key).toBeGreaterThan(0)
        }
    })
})
