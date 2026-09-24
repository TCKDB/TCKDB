import { describe, expect, it } from "vitest"
import { QUANTITY_SPECS } from "./quantityFormat"

/**
 * Closed unit whitelist for `QUANTITY_SPECS` (issue #531).
 *
 * The per-quantity tests in `quantityFormat.test.ts` and the section
 * component tests pin the unit of each spec that exists today, so a typo in
 * one of those is already caught. What nothing caught: a NEW spec carrying a
 * misspelled or invented unit (`unit: "kJ/mlo"` on an added key passed all
 * 3316 frontend tests). This file closes the set over every spec, present
 * and future.
 *
 * Both lists below are written out by hand ON PURPOSE. Deriving either from
 * `QUANTITY_SPECS` would make the check agree with whatever the table says,
 * which is the one thing it exists not to do. Adding a quantity with a new
 * unit means adding that unit here, in review, as a deliberate act.
 *
 * Non-ASCII units are spelled with escapes so a look-alike character (U+212B
 * ANGSTROM SIGN for U+00C5, a full stop or U+22C5 for the U+00B7 middle dot)
 * cannot slip into the allowed set by copy-paste and silently match.
 */
const ALLOWED_UNITS: ReadonlySet<string> = new Set([
    "kJ/mol",
    "J/mol·K", // J/mol·K, U+00B7 MIDDLE DOT
    "Å", // Å, U+00C5 LATIN CAPITAL LETTER A WITH RING ABOVE
    "K",
    "D", // debye
    "hartree",
])

/**
 * Specs that carry no fixed unit. A null unit is not a free pass: it is
 * allowed only for these named quantities, so an existing dimensional spec
 * losing its unit (`unit: null` on an enthalpy) fails here too.
 */
const UNITLESS_SPECS: ReadonlySet<string> = new Set([
    "statmech_frequency_scale_factor", // dimensionless
    "kinetics_n", // dimensionless temperature exponent
    "kinetics_a", // unit is per record (A_units), supplied by the caller
])

const entries = Object.entries(QUANTITY_SPECS) as [string, { unit: string | null }][]

describe("QUANTITY_SPECS unit whitelist", () => {
    it("enumerates the whole spec table, not an empty or truncated one", () => {
        // 12 specs at the time of writing. A floor rather than an exact
        // count so adding a quantity does not need a second edit here, but
        // an import that resolved to {} (or a table cut in half) fails.
        expect(entries.length).toBeGreaterThanOrEqual(12)
    })

    it("gives every spec a unit from the explicit allowed set", () => {
        const offenders = entries
            .filter(([key, spec]) => (spec.unit === null ? !UNITLESS_SPECS.has(key) : !ALLOWED_UNITS.has(spec.unit)))
            .map(([key, spec]) => `${key}: ${JSON.stringify(spec.unit)}`)
        expect(offenders).toEqual([])
    })

    it("actually exercises the allowed set (at least one spec with a unit, one without)", () => {
        // Guards the check above against passing because every spec
        // happened to take the null branch, or none did.
        expect(entries.filter(([, spec]) => spec.unit !== null).length).toBeGreaterThanOrEqual(9)
        expect(entries.filter(([, spec]) => spec.unit === null).length).toBeGreaterThanOrEqual(1)
    })

    it("names only real specs as unitless, so the exemption list cannot rot", () => {
        const keys = new Set(entries.map(([key]) => key))
        for (const key of UNITLESS_SPECS) expect(keys.has(key), key).toBe(true)
    })
})
