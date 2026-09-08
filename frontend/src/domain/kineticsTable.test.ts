import { describe, expect, it } from "vitest"
import { arrheniusTermK, computeKineticsTable, convertKineticsTableRows, formatArrheniusValue, log10Text, scientificText, TABLE_POINT_COUNT } from "./kineticsTable"

// A=3025.44 cm³ mol⁻¹ s⁻¹, n=3.11242, Ea=39.9711 kJ/mol, 300–3000 K --
// `kin_spkzatwjlvmmnja3i5im4fl7hq`'s own live values, independently
// hand-computed (python, not this module) as k(300 K) = 17028.619...,
// k(3000 K) = 40467390077148.48 -- the same figures (to displayed
// precision) the PR 0 design-review mock's own hand-computed table cited.
const A = 3025.44
const n = 3.11242
const Ea = 39.9711

function record(overrides: Record<string, unknown> = {}) {
    return {
        plog_entries: null,
        chebyshev: null,
        falloff: null,
        is_third_body: false,
        temperature_coverage: { record_min_k: 300, record_max_k: 3000 },
        multi_arrhenius: null,
        parameters: { A, n, Ea_kj_mol: Ea },
        ...overrides,
    }
}

describe("arrheniusTermK", () => {
    it("k(300 K) matches the hand-computed value to 4 significant figures", () => {
        expect(arrheniusTermK(A, n, Ea, 300)).toBeCloseTo(17028.619287800688, 3)
    })

    it("k(3000 K) matches the hand-computed value", () => {
        expect(arrheniusTermK(A, n, Ea, 3000)).toBeCloseTo(40467390077148.48, -4)
    })
})

describe("computeKineticsTable", () => {
    // The literal `12` here is deliberate, not `TABLE_POINT_COUNT` --
    // comparing the module's own output against its own (possibly
    // mutated) constant would pass unconditionally regardless of what
    // that constant is set to. MEASURED (post-review): changing
    // `TABLE_POINT_COUNT` from 12 to 6 left a `toHaveLength(TABLE_POINT_COUNT)`
    // version of this assertion green.
    it("samples exactly 12 points, first and last at the record's own fitted range", () => {
        const rows = computeKineticsTable(record())!
        expect(TABLE_POINT_COUNT).toBe(12) // pins the constant's own value too
        expect(rows).toHaveLength(12)
        expect(rows[0].temperatureK).toBe(300)
        expect(rows[rows.length - 1].temperatureK).toBe(3000)
    })

    it("pins k and log10(k) at the first and last sampled temperatures against the hand-computed values", () => {
        const rows = computeKineticsTable(record())!
        expect(rows[0].k).toBeCloseTo(17028.619287800688, 3)
        expect(Math.log10(rows[0].k)).toBeCloseTo(4.231179435983835, 6)
        expect(rows[rows.length - 1].k).toBeCloseTo(40467390077148.48, -4)
        expect(Math.log10(rows[rows.length - 1].k)).toBeCloseTo(13.60710519570258, 6)
    })

    it("sums multi_arrhenius terms rather than using the (null) top-level parameters", () => {
        const rows = computeKineticsTable(record({
            parameters: { A: null, n: null, Ea_kj_mol: null },
            multi_arrhenius: [
                { entry_index: 0, A: A / 2, n, Ea_kj_mol: Ea },
                { entry_index: 1, A: A / 2, n, Ea_kj_mol: Ea },
            ],
        }))!
        // Two half-A terms at the same n/Ea sum back to the single-term value.
        expect(rows[0].k).toBeCloseTo(17028.619287800688, 3)
    })

    it("returns null for a pressure-dependent form (plog/chebyshev/falloff), never a fabricated k(T)", () => {
        expect(computeKineticsTable(record({ plog_entries: [{}] }))).toBeNull()
        expect(computeKineticsTable(record({ chebyshev: {} }))).toBeNull()
        expect(computeKineticsTable(record({ falloff: {} }))).toBeNull()
    })

    // PR 3 review finding: `ArrheniusChart.tsx` refuses to plot a third-body
    // record ("rate depends on bath-gas concentration, not on temperature
    // alone") but used to hand the SAME numbers to this table one row lower
    // on the page -- the same T-only formula (`arrheniusTermK`) is not this
    // record's actual k(T) either, regardless of which surface renders it.
    it("returns null for a third-body record, the same as a pressure-dependent one", () => {
        expect(computeKineticsTable(record({ is_third_body: true }))).toBeNull()
    })

    it("returns null when the record has no fitted T range", () => {
        expect(computeKineticsTable(record({ temperature_coverage: null }))).toBeNull()
    })

    it("returns null when there is no A to compute from at all", () => {
        expect(computeKineticsTable(record({ parameters: { A: null, n: null, Ea_kj_mol: null } }))).toBeNull()
    })
})

describe("convertKineticsTableRows -- the table's own half of the unit selector", () => {
    it("factor 1 (identity -- viewing the record's own deposited unit) returns the SAME array, no float churn", () => {
        const rows = computeKineticsTable(record())!
        const converted = convertKineticsTableRows(rows, 1)
        expect(converted).toBe(rows)
        expect(converted[0].k).toBe(rows[0].k)
    })

    // Independently hand-computed (python): 17028.619287800688 * 1e-6 =
    // 0.017028619287800688 -- the SAME cm³->m³ factor `arrheniusUnits.test.ts`
    // and `arrheniusChartLayout.test.ts` pin for the chart's own curve, so a
    // wrong factor here would put the table and the chart in disagreement
    // even if each looked internally consistent on its own.
    it("multiplies every row's k by the given factor, leaving temperatureK untouched", () => {
        const rows = computeKineticsTable(record())!
        const converted = convertKineticsTableRows(rows, 1e-6)
        expect(converted[0].temperatureK).toBe(rows[0].temperatureK)
        expect(converted[0].k).toBeCloseTo(0.017028619287800688, 12)
        expect(converted[0].k).toBeCloseTo(rows[0].k * 1e-6, 12)
    })
})

describe("log10Text / scientificText", () => {
    it("log10Text pins the same 4-decimal figures the mock cites", () => {
        expect(log10Text(17028.619287800688)).toBe("4.2312")
        expect(log10Text(40467390077148.48)).toBe("13.6071")
    })

    it("scientificText renders a mantissa × 10^exponent with unicode superscripts", () => {
        expect(scientificText(17028.619287800688)).toBe("1.7029×10⁴")
        expect(scientificText(40467390077148.48)).toBe("4.0467×10¹³")
    })
})

// Round-2 review finding: a live `A` (per_s-order, `rxe_snamm...`) rendered
// as a bare 10-digit integer ("9444750000") -- unreadable next to the same
// record's own k(T) column, which already uses scientific notation.
describe("formatArrheniusValue", () => {
    it("renders a LARGE value (>= 1e4) in scientific notation, matching the k(T) column's own format", () => {
        expect(formatArrheniusValue(9444750000)).toBe(scientificText(9444750000))
        expect(formatArrheniusValue(9444750000)).toBe("9.4448×10⁹")
        expect(formatArrheniusValue(9444750000)).not.toContain("9444750000")
    })

    it("renders a SMALL value (< 1e-2, nonzero) in scientific notation", () => {
        expect(formatArrheniusValue(0.00234)).toBe(scientificText(0.00234))
        expect(formatArrheniusValue(0.00234)).toBe("2.3400×10⁻³")
    })

    it("leaves an ordinary-magnitude value EXACTLY as served -- never rounded, never notated", () => {
        // The exact case `ReactionEntryPage.test.tsx`'s DOM-vs-payload
        // identity test pins: A = 3025.44 must still render as the
        // literal string "3025.44", not "3.0254×10³" or a rounded "3030".
        expect(formatArrheniusValue(3025.44)).toBe("3025.44")
        expect(formatArrheniusValue(3.11242)).toBe("3.11242")
        expect(formatArrheniusValue(39.9711)).toBe("39.9711")
    })

    it("handles zero and negative values without throwing", () => {
        expect(formatArrheniusValue(0)).toBe("0")
        expect(formatArrheniusValue(-3025.44)).toBe("-3025.44")
    })

    it("boundary: exactly 1e4 and exactly 1e-2 (inclusive/exclusive per domain/quantityFormat.ts's own convention)", () => {
        expect(formatArrheniusValue(9999)).toBe("9999") // just under 1e4 -- plain
        expect(formatArrheniusValue(10000)).toBe(scientificText(10000)) // >= 1e4 -- scientific
        expect(formatArrheniusValue(0.01)).toBe("0.01") // exactly 1e-2 -- plain (not < 1e-2)
        expect(formatArrheniusValue(0.0099)).toBe(scientificText(0.0099)) // < 1e-2 -- scientific
    })
})
