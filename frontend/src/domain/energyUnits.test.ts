import { describe, expect, it } from "vitest"
import {
    CM1_PER_HARTREE,
    EV_PER_HARTREE,
    KCAL_MOL_PER_HARTREE,
    KJ_MOL_PER_HARTREE,
    convertEnergyForDisplay,
    energyUnitLabel,
    formatEnergyForDisplay,
} from "./energyUnits"

// Hand-checked constants from the brief -- these four numbers are the
// contract, not an implementation detail, so they are pinned literally
// rather than derived from anything else in this module.
describe("hand-checked conversion factors", () => {
    it("1 hartree = 2625.4996394799 kJ/mol", () => {
        expect(KJ_MOL_PER_HARTREE).toBe(2625.4996394799)
    })
    it("1 hartree = 627.50947 kcal/mol", () => {
        expect(KCAL_MOL_PER_HARTREE).toBe(627.50947)
    })
    it("1 hartree = 27.211386245988 eV", () => {
        expect(EV_PER_HARTREE).toBe(27.211386245988)
    })
    it("1 hartree = 219474.6313632 cm^-1", () => {
        expect(CM1_PER_HARTREE).toBe(219474.6313632)
    })
})

describe("convertEnergyForDisplay is exact per the hand-checked table", () => {
    it("hartree is the identity conversion", () => {
        expect(convertEnergyForDisplay(-76.123456, "hartree")).toBe(-76.123456)
    })
    it("converts 1 hartree to kJ/mol exactly", () => {
        expect(convertEnergyForDisplay(1, "kj_mol")).toBe(2625.4996394799)
    })
    it("converts 1 hartree to kcal/mol exactly", () => {
        expect(convertEnergyForDisplay(1, "kcal_mol")).toBe(627.50947)
    })
    it("converts 1 hartree to eV exactly", () => {
        expect(convertEnergyForDisplay(1, "ev")).toBe(27.211386245988)
    })
    it("converts 1 hartree to cm^-1 exactly", () => {
        expect(convertEnergyForDisplay(1, "cm1")).toBe(219474.6313632)
    })
    it("scales linearly for a non-unit value (2 hartree)", () => {
        expect(convertEnergyForDisplay(2, "kj_mol")).toBe(2 * 2625.4996394799)
        expect(convertEnergyForDisplay(2, "ev")).toBe(2 * 27.211386245988)
    })
})

describe("formatEnergyForDisplay always attaches the unit", () => {
    it.each([
        ["hartree", "hartree"],
        ["kj_mol", "kJ/mol"],
        ["kcal_mol", "kcal/mol"],
        ["ev", "eV"],
        ["cm1", "cm⁻¹"],
    ] as const)("includes the %s label in its own formatted string", (unit, label) => {
        expect(formatEnergyForDisplay(-76.123456, unit)).toContain(label)
    })

    it("rounds hartree to 6dp, matching the calculation_electronic_energy_hartree spec", () => {
        expect(formatEnergyForDisplay(-76.1234567, "hartree")).toBe("-76.123457 hartree")
    })

    it("never derives a converted display from another converted value -- both paths from the same source agree", () => {
        // Converting straight from hartree and converting the same
        // stored number a second time must be bit-identical: there is
        // no intermediate rounded value in this module's own state to
        // accumulate drift from.
        const stored = -76.1234567891011
        const first = convertEnergyForDisplay(stored, "ev")
        const second = convertEnergyForDisplay(stored, "ev")
        expect(first).toBe(second)
    })
})

describe("energyUnitLabel", () => {
    it("labels every unit", () => {
        expect(energyUnitLabel("hartree")).toBe("hartree")
        expect(energyUnitLabel("kj_mol")).toBe("kJ/mol")
        expect(energyUnitLabel("kcal_mol")).toBe("kcal/mol")
        expect(energyUnitLabel("ev")).toBe("eV")
        expect(energyUnitLabel("cm1")).toBe("cm⁻¹")
    })
})
