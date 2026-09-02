import { describe, expect, it } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import type { ThermoRecord } from "../api/thermoApi"
import { buildCpChartSeries } from "./thermoCpSeries"

function conformer(ref: string, label: string): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: ref, label },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: 1, optimization_chain_count: 1, geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 }, levels_of_theory: {},
        },
        observations: [], calculations: [], geometries: [],
    } as unknown as ConformerProjection
}

const conformers = [conformer("cg_one", "conformer_1"), conformer("cg_two", "conformer_2")]

// Two records with DELIBERATELY DIFFERENT nasa coefficients, points, and
// conformer links — a fixture where every field under test differs between
// the two records, so a bug that renders every series from records[0] (or
// that mixes the two up) is observable on the SECOND record, not just
// invisible-because-identical to the first.
function recordA(): ThermoRecord {
    return {
        thermo_ref: "thm_a",
        scientific_origin: "computed",
        model_kind: "nasa",
        review: { status: "not_reviewed" },
        supersession: null,
        h298_kj_mol: null, s298_j_mol_k: null, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
        nasa: {
            t_low: 100, t_mid: 500, t_high: 1000,
            low_temperature_coefficients: [1, 0, 0, 0, 0, 0, 0],
            high_temperature_coefficients: [1.5, 0, 0, 0, 0, 0, 0],
        },
        nasa9: null, wilhoit: null,
        points: [
            { temperature_k: 200, cp_j_mol_k: 10 },
            { temperature_k: 800, cp_j_mol_k: 20 },
        ],
        temperature_coverage: null,
        evidence_completeness: { score: 0, max: 8, checklist: {} },
        provenance: { conformer_group_ref: "cg_one" },
        group_additivity: null,
    } as unknown as ThermoRecord
}

function recordB(): ThermoRecord {
    return {
        thermo_ref: "thm_b",
        scientific_origin: "computed",
        model_kind: "nasa",
        review: { status: "not_reviewed" },
        supersession: null,
        h298_kj_mol: null, s298_j_mol_k: null, h298_uncertainty_kj_mol: null, s298_uncertainty_j_mol_k: null,
        nasa: {
            t_low: 200, t_mid: 600, t_high: 1200,
            low_temperature_coefficients: [4, 0, 0, 0, 0, 0, 0],
            high_temperature_coefficients: [5, 0, 0, 0, 0, 0, 0],
        },
        nasa9: null, wilhoit: null,
        points: [
            { temperature_k: 300, cp_j_mol_k: 77 },
            { temperature_k: 900, cp_j_mol_k: 88 },
            { temperature_k: 950, cp_j_mol_k: 99 },
        ],
        temperature_coverage: null,
        evidence_completeness: { score: 0, max: 8, checklist: {} },
        provenance: { conformer_group_ref: "cg_two" },
        group_additivity: null,
    } as unknown as ThermoRecord
}

describe("buildCpChartSeries", () => {
    it("builds one series per record, in the same order, each carrying its OWN data", () => {
        const series = buildCpChartSeries([recordA(), recordB()], conformers, null, "j_mol_k")
        expect(series).toHaveLength(2)
        expect(series[0].thermoRef).toBe("thm_a")
        expect(series[1].thermoRef).toBe("thm_b")

        // The second record's own measured points -- asserted directly,
        // not merely "some series has 3 points somewhere" -- catches a
        // records[0]-only bug, which would make series[1] either absent
        // or an exact copy of series[0]'s two-point/cg_one data.
        expect(series[1].measured).toEqual([
            { temperatureK: 300, cpDisplay: 77 },
            { temperatureK: 900, cpDisplay: 88 },
            { temperatureK: 950, cpDisplay: 99 },
        ])
        expect(series[1].label).toBe("Conformer Group 2")
        expect(series[0].label).toBe("Conformer Group 1")

        // The two records' fitted curves are genuinely different (t_mid,
        // t_high, and coefficients all differ) -- confirms series[1] was
        // built from recordB's own nasa block, not recordA's.
        expect(series[0].fitted!.high.at(-1)!.temperatureK).toBe(1000)
        expect(series[1].fitted!.high.at(-1)!.temperatureK).toBe(1200)
    })

    it("distinguishes the SELECTED conformer's series from the others via isSelected, driven by the link only", () => {
        const series = buildCpChartSeries([recordA(), recordB()], conformers, "cg_two", "j_mol_k")
        expect(series[0].isSelected).toBe(false)
        expect(series[1].isSelected).toBe(true)

        // No selection at all -> nothing is selected, never a default first-record highlight.
        const noneSelected = buildCpChartSeries([recordA(), recordB()], conformers, null, "j_mol_k")
        expect(noneSelected[0].isSelected).toBe(false)
        expect(noneSelected[1].isSelected).toBe(false)
    })

    it("falls back to a label naming the record's own ref when it has no conformer link -- never the bare ref alone", () => {
        // Bare `thm_unlinked` would collide, string-for-string, with the
        // record card's own `<code>{thermo_ref}</code>` elsewhere on the
        // same tab -- see the comment on `seriesLabel` in thermoCpSeries.ts.
        const unlinked = { ...recordA(), thermo_ref: "thm_unlinked", provenance: { conformer_group_ref: null } } as ThermoRecord
        const series = buildCpChartSeries([unlinked], conformers, null, "j_mol_k")
        expect(series[0].label).toBe("Unlinked record thm_unlinked")
    })

    it("reports hasUsableFit/hasMeasuredPoints independently -- a record can have either, both, or neither", () => {
        const fitOnly = { ...recordA(), points: null } as ThermoRecord
        const pointsOnly = { ...recordA(), nasa: null } as ThermoRecord

        expect(buildCpChartSeries([fitOnly], conformers, null, "j_mol_k")[0].hasUsableFit).toBe(true)
        expect(buildCpChartSeries([fitOnly], conformers, null, "j_mol_k")[0].hasMeasuredPoints).toBe(false)

        expect(buildCpChartSeries([pointsOnly], conformers, null, "j_mol_k")[0].hasUsableFit).toBe(false)
        expect(buildCpChartSeries([pointsOnly], conformers, null, "j_mol_k")[0].hasMeasuredPoints).toBe(true)
    })

    it("converts measured and fitted values for display without touching the underlying arithmetic", () => {
        const jSeries = buildCpChartSeries([recordA()], conformers, null, "j_mol_k")[0]
        const calSeries = buildCpChartSeries([recordA()], conformers, null, "cal_mol_k")[0]
        expect(jSeries.measured[0].cpDisplay).toBe(10)
        expect(calSeries.measured[0].cpDisplay).toBeCloseTo(10 / 4.184, 10)
        // Round-tripping the displayed cal value back to J reproduces the
        // original stored measured value exactly.
        expect(calSeries.measured[0].cpDisplay * 4.184).toBeCloseTo(jSeries.measured[0].cpDisplay, 9)
    })
})
