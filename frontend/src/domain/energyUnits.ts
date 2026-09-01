/**
 * Display-only energy unit conversions, following the same rule
 * `GeometryDetailPage`'s Å/bohr toggle and `ThermoCpChart`'s J/cal toggle
 * already established: the archive always stores an electronic energy in
 * hartree (`electronic_energy_hartree` / `final_energy_hartree` on the
 * wire); every other unit here is a DISPLAY conversion computed straight
 * from that one stored hartree number, never from another converted
 * value, and never fed back into anything the archive holds.
 *
 * Conversion factors are CODATA/NIST values, hand-checked against the
 * brief:
 *   1 hartree = 2625.4996394799 kJ/mol
 *             = 627.50947 kcal/mol
 *             = 27.211386245988 eV
 *             = 219474.6313632 cm^-1
 * (`energyUnits.test.ts` asserts each of these exactly.)
 */

export type EnergyDisplayUnit = "hartree" | "kj_mol" | "kcal_mol" | "ev" | "cm1"

export const KJ_MOL_PER_HARTREE = 2625.4996394799
export const KCAL_MOL_PER_HARTREE = 627.50947
export const EV_PER_HARTREE = 27.211386245988
export const CM1_PER_HARTREE = 219474.6313632

export const ENERGY_DISPLAY_UNITS: readonly EnergyDisplayUnit[] = ["hartree", "kj_mol", "kcal_mol", "ev", "cm1"]

export function energyUnitLabel(unit: EnergyDisplayUnit): string {
    switch (unit) {
        case "hartree": return "hartree"
        case "kj_mol": return "kJ/mol"
        case "kcal_mol": return "kcal/mol"
        case "ev": return "eV"
        case "cm1": return "cm⁻¹"
    }
}

/**
 * How many decimal places a converted value is shown at. Chosen per unit
 * so the printed number carries a sensible number of significant digits
 * across the range this archive's electronic energies actually span —
 * this is a display choice only, never used by `convertEnergyForDisplay`
 * itself, which always returns the full-precision converted float.
 */
const DISPLAY_DIGITS: Record<EnergyDisplayUnit, number> = {
    hartree: 6,
    kj_mol: 2,
    kcal_mol: 2,
    ev: 4,
    cm1: 1,
}

/**
 * Converts one hartree-valued electronic energy to the requested display
 * unit. `valueHartree` is always the archive's own stored number — this
 * function never accepts (and this module never produces) an
 * already-converted value as input, so switching units twice can never
 * compound rounding error: every conversion starts from the same source
 * float.
 */
export function convertEnergyForDisplay(valueHartree: number, unit: EnergyDisplayUnit): number {
    switch (unit) {
        case "hartree": return valueHartree
        case "kj_mol": return valueHartree * KJ_MOL_PER_HARTREE
        case "kcal_mol": return valueHartree * KCAL_MOL_PER_HARTREE
        case "ev": return valueHartree * EV_PER_HARTREE
        case "cm1": return valueHartree * CM1_PER_HARTREE
    }
}

/**
 * Formats a hartree-valued electronic energy in the requested display
 * unit, rounded to that unit's own display precision, with the unit
 * string always attached — a bare number with no unit reads as a wrong
 * answer once a reader has switched away from hartree, so this function
 * has no "value only" mode.
 */
export function formatEnergyForDisplay(valueHartree: number, unit: EnergyDisplayUnit): string {
    const converted = convertEnergyForDisplay(valueHartree, unit)
    return `${converted.toFixed(DISPLAY_DIGITS[unit])} ${energyUnitLabel(unit)}`
}
