import { describe, expect, it } from "vitest"
import {
    ARRHENIUS_SAMPLE_COUNT,
    arrheniusUnitLabel,
    buildArrheniusChartData,
    computeArrheniusSeries,
    panelLog10KDomain,
    panelTemperatureDomain,
} from "./arrheniusChartLayout"

// The SAME live values `kineticsTable.test.ts` pins (`kin_spkzatwjlvmmnja3i5im4fl7hq`,
// A=3025.44 cm³ mol⁻¹ s⁻¹, n=3.11242, Ea=39.9711 kJ/mol, 300-3000 K) --
// independently hand-computed as k(300 K) = 17028.619287800688, k(3000 K) =
// 40467390077148.48. Reused here rather than re-derived, per this PR's own
// "reuse the maths, do not fork it" brief.
const A = 3025.44
const n = 3.11242
const Ea = 39.9711

function record(overrides: Record<string, unknown> = {}) {
    return {
        kinetics_ref: "kin_spkzatwjlvmmnja3i5im4fl7hq",
        model_kind: "modified_arrhenius",
        plog_entries: null,
        chebyshev: null,
        falloff: null,
        is_third_body: false,
        temperature_coverage: { record_min_k: 300, record_max_k: 3000 },
        multi_arrhenius: null,
        parameters: { A, n, Ea_kj_mol: Ea, A_units: "cm3_mol_s" },
        ...overrides,
    } as never
}

describe("computeArrheniusSeries -- k(T) at the domain ends against the hand-computed values", () => {
    it("samples ARRHENIUS_SAMPLE_COUNT (60) points, first and last exactly at the record's own fitted range", () => {
        const series = computeArrheniusSeries(record())!
        expect(ARRHENIUS_SAMPLE_COUNT).toBe(60) // pins the constant's own value too
        expect(series.points).toHaveLength(60)
        expect(series.points[0].temperatureK).toBe(300)
        expect(series.points[series.points.length - 1].temperatureK).toBe(3000)
        expect(series.minK).toBe(300)
        expect(series.maxK).toBe(3000)
    })

    it("k(300 K) and k(3000 K) match the independently hand-computed values", () => {
        const series = computeArrheniusSeries(record())!
        expect(series.points[0].k).toBeCloseTo(17028.619287800688, 3)
        expect(series.points[series.points.length - 1].k).toBeCloseTo(40467390077148.48, -4)
    })

    it("log10k at the domain ends matches the hand-computed log10 of those same values", () => {
        const series = computeArrheniusSeries(record())!
        expect(series.points[0].log10k).toBeCloseTo(4.231179435983835, 6)
        expect(series.points[series.points.length - 1].log10k).toBeCloseTo(13.60710519570258, 6)
    })

    it("sums multi_arrhenius terms rather than the (null) top-level parameters, mirroring computeKineticsTable", () => {
        const series = computeArrheniusSeries(record({
            parameters: { A: null, n: null, Ea_kj_mol: null, A_units: "cm3_mol_s" },
            multi_arrhenius: [
                { entry_index: 0, A: A / 2, n, Ea_kj_mol: Ea },
                { entry_index: 1, A: A / 2, n, Ea_kj_mol: Ea },
            ],
        }))!
        expect(series.points[0].k).toBeCloseTo(17028.619287800688, 3)
    })

    it("returns null with no fitted T range, and with no A to compute from -- never a fabricated curve", () => {
        expect(computeArrheniusSeries(record({ temperature_coverage: null }))).toBeNull()
        expect(computeArrheniusSeries(record({ parameters: { A: null, n: null, Ea_kj_mol: null, A_units: "cm3_mol_s" } }))).toBeNull()
    })
})

describe("arrheniusUnitLabel", () => {
    it("matches ReactionKineticsSection's own k(T) table header text byte-for-byte", () => {
        expect(arrheniusUnitLabel("cm3_mol_s")).toBe("cm³ mol⁻¹ s⁻¹")
        expect(arrheniusUnitLabel("per_s")).toBe("s⁻¹")
    })

    it("falls back to a spaced token for an unrecognised unit, and names unrecorded units plainly", () => {
        expect(arrheniusUnitLabel("weird_future_unit")).toBe("weird future unit")
        expect(arrheniusUnitLabel(null)).toBe("unrecorded units")
        expect(arrheniusUnitLabel(undefined)).toBe("unrecorded units")
    })
})

describe("buildArrheniusChartData -- per-unit panel split", () => {
    it("a page mixing per_s and cm3_mol_s records gets two panels, each holding only its own unit's series", () => {
        const perSRecord = record({
            kinetics_ref: "kin_unimolecular",
            parameters: { A: 9444750000, n: 0, Ea_kj_mol: 10, A_units: "per_s" },
        })
        const cm3Record = record({ kinetics_ref: "kin_bimolecular" })
        const { panels, excluded } = buildArrheniusChartData([perSRecord, cm3Record])

        expect(excluded).toHaveLength(0)
        expect(panels).toHaveLength(2)
        expect(panels[0].aUnits).toBe("per_s")
        expect(panels[0].series.map((s) => s.kinetics_ref)).toEqual(["kin_unimolecular"])
        expect(panels[1].aUnits).toBe("cm3_mol_s")
        expect(panels[1].series.map((s) => s.kinetics_ref)).toEqual(["kin_bimolecular"])
    })

    it("two records sharing one A_units land in the SAME panel, in served order", () => {
        const first = record({ kinetics_ref: "kin_a" })
        const second = record({ kinetics_ref: "kin_b" })
        const { panels } = buildArrheniusChartData([first, second])
        expect(panels).toHaveLength(1)
        expect(panels[0].series.map((s) => s.kinetics_ref)).toEqual(["kin_a", "kin_b"])
    })
})

describe("buildArrheniusChartData -- PLOG/Chebyshev/falloff/third-body exclusion", () => {
    it("a PLOG record is excluded, named by its own ref, with a reason mentioning PLOG -- and never plotted", () => {
        const plogRecord = record({ kinetics_ref: "kin_plog_one", plog_entries: [{ pressure_bar: 1 }] })
        const { panels, excluded } = buildArrheniusChartData([plogRecord])
        expect(panels).toHaveLength(0)
        expect(excluded).toHaveLength(1)
        expect(excluded[0].kinetics_ref).toBe("kin_plog_one")
        expect(excluded[0].reasons.join(" ")).toMatch(/PLOG/)
    })

    it("a Chebyshev record is excluded with a reason mentioning Chebyshev", () => {
        const chebyshevRecord = record({ kinetics_ref: "kin_cheb_one", chebyshev: { coefficients: [[1]] } })
        const { excluded } = buildArrheniusChartData([chebyshevRecord])
        expect(excluded).toHaveLength(1)
        expect(excluded[0].reasons.join(" ")).toMatch(/Chebyshev/)
    })

    it("a falloff record is excluded with a reason mentioning falloff", () => {
        const falloffRecord = record({ kinetics_ref: "kin_falloff_one", falloff: { kind: "troe" } })
        const { excluded } = buildArrheniusChartData([falloffRecord])
        expect(excluded).toHaveLength(1)
        expect(excluded[0].reasons.join(" ")).toMatch(/falloff/)
    })

    it("a third-body record is excluded with a reason mentioning third-body", () => {
        const thirdBodyRecord = record({ kinetics_ref: "kin_third_body_one", is_third_body: true })
        const { excluded } = buildArrheniusChartData([thirdBodyRecord])
        expect(excluded).toHaveLength(1)
        expect(excluded[0].reasons.join(" ")).toMatch(/third-body/)
    })

    it("a record carrying more than one excluding flag names every applicable reason, not just the first", () => {
        const multiFlagRecord = record({
            kinetics_ref: "kin_multi",
            falloff: { kind: "troe" },
            is_third_body: true,
        })
        const { excluded } = buildArrheniusChartData([multiFlagRecord])
        expect(excluded[0].reasons).toHaveLength(2)
    })

    it("a mix of plottable and excluded records: the excluded one is listed, the plottable one still gets its own panel", () => {
        const plottable = record({ kinetics_ref: "kin_ok" })
        const plog = record({ kinetics_ref: "kin_plog", plog_entries: [{}] })
        const { panels, excluded } = buildArrheniusChartData([plottable, plog])
        expect(panels).toHaveLength(1)
        expect(panels[0].series.map((s) => s.kinetics_ref)).toEqual(["kin_ok"])
        expect(excluded.map((e) => e.kinetics_ref)).toEqual(["kin_plog"])
    })

    it("zero plottable records: every record is excluded, panels is empty", () => {
        const plog = record({ kinetics_ref: "kin_plog_only", plog_entries: [{}] })
        const { panels, excluded } = buildArrheniusChartData([plog])
        expect(panels).toHaveLength(0)
        expect(excluded).toHaveLength(1)
    })
})

describe("panelTemperatureDomain -- the union of every plotted series' own fitted range", () => {
    it("takes the min of every series' minK and the max of every series' maxK, unpadded", () => {
        const narrow = computeArrheniusSeries(record({ kinetics_ref: "kin_narrow", temperature_coverage: { record_min_k: 500, record_max_k: 1500 } }))!
        const wide = computeArrheniusSeries(record({ kinetics_ref: "kin_wide", temperature_coverage: { record_min_k: 300, record_max_k: 3000 } }))!
        const [lo, hi] = panelTemperatureDomain({ aUnits: "cm3_mol_s", unitLabel: "cm³ mol⁻¹ s⁻¹", series: [narrow, wide] })
        expect(lo).toBe(300)
        expect(hi).toBe(3000)
    })
})

// Review finding: every fixture elsewhere in this file uses the SAME
// 300-3000 K range for every record, so a bug that sampled a record across
// the PANEL's wider union domain instead of its own range would change
// nothing observable in any of those tests. This block uses two records
// with GENUINELY different ranges in the SAME panel and checks the
// narrower one never extends past its own `record_min_k..record_max_k`,
// per plan §4: "each curve drawn only within its own
// record_min_k..record_max_k" -- not the panel's union.
describe("computeArrheniusSeries -- per-record range clipping when panelled beside a wider record", () => {
    it("a narrow record (1000-2000 K) sampled beside a wide one (300-3000 K) in the same panel stays entirely within its OWN range", () => {
        const wideRecord = record({ kinetics_ref: "kin_wide", temperature_coverage: { record_min_k: 300, record_max_k: 3000 } })
        const narrowRecord = record({ kinetics_ref: "kin_narrow", temperature_coverage: { record_min_k: 1000, record_max_k: 2000 } })
        const { panels } = buildArrheniusChartData([wideRecord, narrowRecord])

        expect(panels).toHaveLength(1) // same A_units -> one panel, so the union domain is genuinely wider than the narrow record
        const panel = panels[0]
        const [unionLo, unionHi] = panelTemperatureDomain(panel)
        expect(unionLo).toBe(300)
        expect(unionHi).toBe(3000)

        const narrowSeries = panel.series.find((s) => s.kinetics_ref === "kin_narrow")!
        const temperatures = narrowSeries.points.map((p) => p.temperatureK)
        // Every sampled point sits inside the record's OWN range -- never as
        // low as the panel union's 300 K floor, never as high as its 3000 K
        // ceiling. A bug that samples across the panel's own union domain
        // (rather than the record's own `temperature_coverage`) would put
        // points at 300 K and 3000 K here instead.
        expect(Math.min(...temperatures)).toBeGreaterThanOrEqual(1000)
        expect(Math.max(...temperatures)).toBeLessThanOrEqual(2000)
        expect(narrowSeries.points[0].temperatureK).toBe(1000)
        expect(narrowSeries.points[narrowSeries.points.length - 1].temperatureK).toBe(2000)
        // Sanity: the wide record's OWN series, by contrast, does span the
        // full union -- so this fixture genuinely exercises "one series
        // narrower than the panel it's drawn in", not two identical ranges.
        const wideSeries = panel.series.find((s) => s.kinetics_ref === "kin_wide")!
        expect(wideSeries.points[0].temperatureK).toBe(300)
        expect(wideSeries.points[wideSeries.points.length - 1].temperatureK).toBe(3000)
    })
})

describe("panelLog10KDomain -- padded, but strictly wider than the raw log10k extremes", () => {
    it("returns a domain that contains every plotted log10k value with room either side", () => {
        const series = computeArrheniusSeries(record())!
        const [lo, hi] = panelLog10KDomain({ aUnits: "cm3_mol_s", unitLabel: "cm³ mol⁻¹ s⁻¹", series: [series] })
        const rawValues = series.points.map((p) => p.log10k)
        expect(lo).toBeLessThan(Math.min(...rawValues))
        expect(hi).toBeGreaterThan(Math.max(...rawValues))
    })
})
