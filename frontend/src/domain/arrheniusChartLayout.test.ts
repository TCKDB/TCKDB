import { describe, expect, it } from "vitest"
import {
    ARRHENIUS_SAMPLE_COUNT,
    type ArrheniusSeries,
    arrheniusPanelKey,
    arrheniusPointX,
    arrheniusUnitLabel,
    buildArrheniusChartData,
    computeArrheniusSeries,
    convertArrheniusSeriesUnits,
    panelLog10KDomain,
    panelTemperatureDomain,
    panelXDomain,
} from "./arrheniusChartLayout"
import { convertArrheniusValue } from "./arrheniusUnits"

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

    it("carries the record's OWN deposited A_units, unconverted", () => {
        const series = computeArrheniusSeries(record())!
        expect(series.depositedUnits).toBe("cm3_mol_s")
        const unrecorded = computeArrheniusSeries(record({ parameters: { A, n, Ea_kj_mol: Ea, A_units: null } }))!
        expect(unrecorded.depositedUnits).toBeNull()
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

describe("buildArrheniusChartData -- per-ORDER-FAMILY panel split (defect fix: no longer per raw A_units token)", () => {
    it("a page mixing per_s and cm3_mol_s records gets two panels (different order families), each holding only its own family's series", () => {
        const perSRecord = record({
            kinetics_ref: "kin_unimolecular",
            parameters: { A: 9444750000, n: 0, Ea_kj_mol: 10, A_units: "per_s" },
        })
        const cm3Record = record({ kinetics_ref: "kin_bimolecular" })
        const { panels, excluded } = buildArrheniusChartData([perSRecord, cm3Record])

        expect(excluded).toHaveLength(0)
        expect(panels).toHaveLength(2)
        expect(panels[0].orderFamily).toBe(1)
        expect(panels[0].series.map((s) => s.kinetics_ref)).toEqual(["kin_unimolecular"])
        expect(panels[1].orderFamily).toBe(2)
        expect(panels[1].series.map((s) => s.kinetics_ref)).toEqual(["kin_bimolecular"])
    })

    it("two records sharing one A_units land in the SAME panel, in served order", () => {
        const first = record({ kinetics_ref: "kin_a" })
        const second = record({ kinetics_ref: "kin_b" })
        const { panels } = buildArrheniusChartData([first, second])
        expect(panels).toHaveLength(1)
        expect(panels[0].series.map((s) => s.kinetics_ref)).toEqual(["kin_a", "kin_b"])
    })

    // THE headline fix: a cm3_mol_s record and an m3_mol_s record are the
    // SAME physical quantity, 1e6 apart -- they must now land in ONE panel
    // (the old per-raw-token grouping put them in two uncomparable panels).
    it("a cm3_mol_s record and an m3_mol_s record -- same order family -- share ONE panel", () => {
        const cm3Record = record({ kinetics_ref: "kin_cm3" })
        const m3Record = record({ kinetics_ref: "kin_m3", parameters: { A, n, Ea_kj_mol: Ea, A_units: "m3_mol_s" } })
        const { panels } = buildArrheniusChartData([cm3Record, m3Record])
        expect(panels).toHaveLength(1)
        expect(panels[0].orderFamily).toBe(2)
        expect(panels[0].series.map((s) => s.kinetics_ref)).toEqual(["kin_cm3", "kin_m3"])
    })

    it("availableUnits lists every unit in the panel's family, base unit first, regardless of which ones were actually deposited", () => {
        const cm3Record = record({ kinetics_ref: "kin_cm3" })
        const { panels } = buildArrheniusChartData([cm3Record])
        expect(panels[0].availableUnits).toEqual(["cm3_mol_s", "m3_mol_s", "cm3_molecule_s"])
    })

    it("a per_s panel's availableUnits has exactly one member -- no selector control should ever be offered", () => {
        const perSRecord = record({ kinetics_ref: "kin_uni", parameters: { A: 1, n: 0, Ea_kj_mol: 0, A_units: "per_s" } })
        const { panels } = buildArrheniusChartData([perSRecord])
        expect(panels[0].availableUnits).toEqual(["per_s"])
    })

    it("defaultUnits is the panel's own modal deposited unit -- a single-record panel defaults to that record's own unit", () => {
        const cm3Record = record({ kinetics_ref: "kin_cm3" })
        const { panels } = buildArrheniusChartData([cm3Record])
        expect(panels[0].defaultUnits).toBe("cm3_mol_s")
    })

    // Tie-break rule (documented on `ArrheniusPanel.defaultUnits`): the
    // tied unit whose FIRST deposit appears earliest in served order. Two
    // cm3_mol_s records (first) vs one m3_mol_s (2 vs 1) -- cm3_mol_s wins
    // outright here (no tie), pinning the non-tied majority case.
    it("defaultUnits is the STRICT majority when one unit outnumbers the others", () => {
        const a = record({ kinetics_ref: "kin_a" })
        const b = record({ kinetics_ref: "kin_b" })
        const c = record({ kinetics_ref: "kin_c", parameters: { A, n, Ea_kj_mol: Ea, A_units: "m3_mol_s" } })
        const { panels } = buildArrheniusChartData([a, b, c])
        expect(panels[0].defaultUnits).toBe("cm3_mol_s")
    })

    // A genuine tie (one m3_mol_s, one cm3_mol_s): the winner is whichever
    // unit's FIRST deposit came first in the served array -- here m3_mol_s
    // is served FIRST, so it wins the tie despite not being the family's
    // "base" unit. A tie-break that instead used `arrheniusUnits.ts`'s own
    // base-first table order would pick cm3_mol_s here and this test would
    // catch it.
    it("a genuine tie breaks on served order, not on the family table's own base-first order", () => {
        const m3First = record({ kinetics_ref: "kin_m3_first", parameters: { A, n, Ea_kj_mol: Ea, A_units: "m3_mol_s" } })
        const cm3Second = record({ kinetics_ref: "kin_cm3_second" })
        const { panels } = buildArrheniusChartData([m3First, cm3Second])
        expect(panels[0].defaultUnits).toBe("m3_mol_s")
    })

    it("an unrecorded-units record keeps its own panel, with no available units and no default", () => {
        const unrecorded = record({ kinetics_ref: "kin_unrecorded", parameters: { A, n, Ea_kj_mol: Ea, A_units: null } })
        const { panels } = buildArrheniusChartData([unrecorded])
        expect(panels).toHaveLength(1)
        expect(panels[0].orderFamily).toBeNull()
        expect(panels[0].availableUnits).toEqual([])
        expect(panels[0].defaultUnits).toBeUndefined()
    })

    it("an unrecognised A_units token gets its own panel too, distinct from unrecorded and from a known family, and names itself as the (unconvertible) default", () => {
        const weird = record({ kinetics_ref: "kin_weird", parameters: { A, n, Ea_kj_mol: Ea, A_units: "weird_future_unit" } })
        const unrecorded = record({ kinetics_ref: "kin_unrecorded", parameters: { A, n, Ea_kj_mol: Ea, A_units: null } })
        const { panels } = buildArrheniusChartData([weird, unrecorded])
        expect(panels).toHaveLength(2)
        expect(panels[0].orderFamily).toBeNull()
        expect(panels[0].availableUnits).toEqual([])
        expect(panels[0].defaultUnits).toBe("weird_future_unit")
        expect(panels[1].defaultUnits).toBeUndefined()
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

describe("arrheniusPanelKey", () => {
    it("is stable and distinct per family/unrecorded/unrecognised-token panel", () => {
        const perS = buildArrheniusChartData([record({ kinetics_ref: "kin_uni", parameters: { A: 1, n: 0, Ea_kj_mol: 0, A_units: "per_s" } })]).panels[0]
        const cm3 = buildArrheniusChartData([record({ kinetics_ref: "kin_cm3" })]).panels[0]
        const unrecorded = buildArrheniusChartData([record({ kinetics_ref: "kin_none", parameters: { A, n, Ea_kj_mol: Ea, A_units: null } })]).panels[0]
        const keys = [perS, cm3, unrecorded].map(arrheniusPanelKey)
        expect(new Set(keys).size).toBe(3)
        // Deterministic across independent computations of the "same" panel identity.
        expect(arrheniusPanelKey(perS)).toBe(arrheniusPanelKey(perS))
    })
})

describe("panelTemperatureDomain -- the union of every plotted series' own fitted range", () => {
    it("takes the min of every series' minK and the max of every series' maxK, unpadded", () => {
        const narrow = computeArrheniusSeries(record({ kinetics_ref: "kin_narrow", temperature_coverage: { record_min_k: 500, record_max_k: 1500 } }))!
        const wide = computeArrheniusSeries(record({ kinetics_ref: "kin_wide", temperature_coverage: { record_min_k: 300, record_max_k: 3000 } }))!
        const [lo, hi] = panelTemperatureDomain([narrow, wide])
        expect(lo).toBe(300)
        expect(hi).toBe(3000)
    })
})

describe("arrheniusPointX / panelXDomain -- the inverse-temperature (1000/T) axis", () => {
    it("temperature mode: x is T itself, unchanged", () => {
        const series = computeArrheniusSeries(record())!
        expect(arrheniusPointX(series.points[0], "temperature")).toBe(300)
        expect(arrheniusPointX(series.points[series.points.length - 1], "temperature")).toBe(3000)
        expect(panelXDomain([series], "temperature")).toEqual([300, 3000])
    })

    it("inverse_temperature mode: x is 1000/T -- pinned against hand-computed values", () => {
        const series = computeArrheniusSeries(record())!
        expect(arrheniusPointX(series.points[0], "inverse_temperature")).toBeCloseTo(1000 / 300, 9) // 3.3333...
        expect(arrheniusPointX(series.points[series.points.length - 1], "inverse_temperature")).toBeCloseTo(1000 / 3000, 9) // 0.3333...
    })

    // MUTATION TARGET (c): plotting T instead of 1000/T in inverse mode.
    // This test fails if `arrheniusPointX` (or its caller) ever returns the
    // raw temperature under `"inverse_temperature"` mode.
    it("inverse_temperature mode is genuinely a DIFFERENT value from temperature mode at the same point", () => {
        const series = computeArrheniusSeries(record())!
        const point = series.points[0]
        expect(arrheniusPointX(point, "inverse_temperature")).not.toBeCloseTo(arrheniusPointX(point, "temperature"), 0)
    })

    // High temperature must map to the SMALL end of the 1000/T domain (the
    // domain's own `[0]` slot, which `linearScale` always places at the LOW
    // pixel end) -- that is what puts high T at the left on the rendered
    // axis. domain[0] = 1000/maxK (the smallest 1000/T value, from the
    // HIGHEST T), domain[1] = 1000/minK (the largest 1000/T value, from the
    // LOWEST T) -- the reverse of the temperature-mode domain's own
    // [minK, maxK] ordering.
    it("inverse_temperature domain is [1000/maxK, 1000/minK] -- reversed relative to the temperature domain's own [minK, maxK]", () => {
        const series = computeArrheniusSeries(record())!
        const [tLo, tHi] = panelXDomain([series], "temperature")
        const [invLo, invHi] = panelXDomain([series], "inverse_temperature")
        expect(tLo).toBe(300)
        expect(tHi).toBe(3000)
        expect(invLo).toBeCloseTo(1000 / 3000, 9)
        expect(invHi).toBeCloseTo(1000 / 300, 9)
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
        const [unionLo, unionHi] = panelTemperatureDomain(panel.series)
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
        const [lo, hi] = panelLog10KDomain([series])
        const rawValues = series.points.map((p) => p.log10k)
        expect(lo).toBeLessThan(Math.min(...rawValues))
        expect(hi).toBeGreaterThan(Math.max(...rawValues))
    })
})

describe("convertArrheniusSeriesUnits -- the series-level half of the unit selector", () => {
    // MUTATION TARGET (a): the cm³->m³ factor. 1e-6, not 1e6 --
    // independently hand-computed as 3025.44 * 1e-6 = 0.00302544 (matches
    // `arrheniusUnits.test.ts`'s own pinned value for the same conversion).
    it("cm3_mol_s -> m3_mol_s multiplies k by 1e-6, shifting log10k down by exactly 6", () => {
        const series = computeArrheniusSeries(record())!
        const converted = convertArrheniusSeriesUnits(series, "m3_mol_s")
        expect(converted.points[0].k).toBeCloseTo(17028.619287800688 * 1e-6, 9)
        expect(converted.points[0].k).toBeCloseTo(0.017028619287800688, 9)
        expect(converted.points[0].log10k).toBeCloseTo(series.points[0].log10k - 6, 6)
    })

    // MUTATION TARGET (b): dropping the N_A division for the per-molecule
    // unit. Independently hand-computed (python): 17028.619287800688 /
    // 6.02214076e23 = 2.827668758742313e-20.
    it("cm3_mol_s -> cm3_molecule_s divides k by N_A -- NOT left unconverted", () => {
        const series = computeArrheniusSeries(record())!
        const converted = convertArrheniusSeriesUnits(series, "cm3_molecule_s")
        const expected = 2.827668758742313e-20
        expect(Math.abs(converted.points[0].k - expected) / expected).toBeLessThan(1e-9)
        // Sanity: genuinely different from the unconverted value, by many
        // orders of magnitude -- catches a no-op conversion outright.
        expect(converted.points[0].k / series.points[0].k).toBeLessThan(1e-15)
    })

    it("converting a unit to ITSELF is the exact identity -- no float churn at all", () => {
        const series = computeArrheniusSeries(record())!
        const converted = convertArrheniusSeriesUnits(series, "cm3_mol_s")
        expect(converted.points[0].k).toBe(series.points[0].k)
        expect(converted).toBe(series) // the function's own documented identity shortcut
    })

    // MUTATION TARGET (d): letting a cross-family conversion through. A
    // per_s series asked to convert into a bimolecular unit must be
    // returned UNCHANGED (the factor is refused, `null`), never silently
    // reinterpreted as a bimolecular value.
    it("refuses a cross-family conversion -- returns the series unconverted, never a fabricated cross-family value", () => {
        const perSSeries = computeArrheniusSeries(record({
            kinetics_ref: "kin_uni",
            parameters: { A: 9444750000, n: 0, Ea_kj_mol: 10, A_units: "per_s" },
        }))!
        const attempted = convertArrheniusSeriesUnits(perSSeries, "cm3_mol_s")
        expect(attempted.points[0].k).toBe(perSSeries.points[0].k)
        expect(attempted).toBe(perSSeries)
    })

    // NOTE: `convertArrheniusSeriesUnits` always converts FROM the series'
    // own IMMUTABLE `depositedUnits` (never from "whatever unit the points
    // currently happen to be in") -- `panel.series` (the raw, deposited-unit
    // array) is what every render starts from, never a previously-converted
    // series, so there is no real "convert, then convert the RESULT again"
    // path in this app. This test instead checks the series-level
    // conversion agrees with the value-level round trip
    // (`arrheniusUnits.ts`'s own `convertArrheniusValue`, converting the
    // ALREADY-converted k back using the SAME from/to pair) -- the
    // meaningful cross-check that the two mechanisms compute the identical
    // factor, not a chain this function was never meant to support.
    it("the value-level round trip on a series-converted k reproduces the original k with no meaningful drift", () => {
        const series = computeArrheniusSeries(record())!
        const toM3 = convertArrheniusSeriesUnits(series, "m3_mol_s")
        const back = convertArrheniusValue(toM3.points[0].k, "m3_mol_s", "cm3_mol_s")!
        expect(back).toBeCloseTo(series.points[0].k, 6)
    })

    it("a series with unrecorded units is returned unchanged (nothing to convert from)", () => {
        const unrecorded = computeArrheniusSeries(record({ parameters: { A, n, Ea_kj_mol: Ea, A_units: null } }))!
        const attempted = convertArrheniusSeriesUnits(unrecorded, "cm3_mol_s")
        expect(attempted).toBe(unrecorded)
    })

    // Post-merge review finding: a converted `k` that underflows to EXACTLY
    // 0 (a double cannot represent the true tiny positive value) must be
    // SKIPPED, mirroring `computeArrheniusSeries`'s own `if (!(k > 0))
    // continue` guard on the RAW k -- without it, `Math.log10(0)` is
    // `-Infinity`, which poisons `panelLog10KDomain`'s `Math.min`/`Math.max`
    // into `[-Infinity, Infinity]` and every plotted y becomes `NaN`: the
    // WHOLE panel renders blank with no error. `Number.MIN_VALUE` (the
    // smallest positive subnormal double, ~5e-324) times ANY of this file's
    // own conversion factors below 1 rounds to exactly 0 in IEEE-754 double
    // arithmetic -- a clean, deterministic way to force the underflow
    // without needing a physically implausible A.
    it("a converted k that underflows to exactly 0 is SKIPPED, not left in as a -Infinity log10k", () => {
        const raw = computeArrheniusSeries(record())!
        const tinyPointSeries: ArrheniusSeries = {
            ...raw,
            points: [
                { temperatureK: 300, k: Number.MIN_VALUE, log10k: Math.log10(Number.MIN_VALUE) },
                ...raw.points.slice(1),
            ],
        }
        const converted = convertArrheniusSeriesUnits(tinyPointSeries, "cm3_molecule_s")
        // The underflowed point is gone entirely -- not present as a
        // `k: 0` / `log10k: -Infinity` point that would corrupt the domain.
        expect(converted.points.some((p) => p.temperatureK === 300)).toBe(false)
        expect(converted.points.length).toBe(tinyPointSeries.points.length - 1)
        expect(converted.points.every((p) => Number.isFinite(p.log10k))).toBe(true)
        expect(converted.points.every((p) => p.k > 0)).toBe(true)
    })
})
