import { describe, expect, it } from "vitest"
import type { CalculationChild } from "../api/calculationChildrenApi"
import type { PathSearchSummary } from "../api/calculationPathsApi"
import { pathSearchProfile } from "./pathProfile"
import {
    childFacts,
    childOrder,
    climbingWords,
    convergedWords,
    edgeWords,
    endsWords,
    guessWords,
    methodWords,
    partialBandNote,
    pointsWords,
} from "./pathSearchFacts"

const SUMMARY: PathSearchSummary = {
    method: "gsm",
    is_double_ended: true,
    converged: null,
    n_points: 15,
    selected_ts_point_index: 8,
    climbing_image_index: null,
    source_endpoint_count: 2,
    stored_point_count: 15,
    ts_guess_count: 1,
    climbing_image_count: 0,
}

const profile = pathSearchProfile({
    summary: SUMMARY,
    points: [0, 7, 8, 14].map((index) => ({ point_index: index, path_coordinate: { 0: 0, 7: 2.0113, 8: 3.758883, 14: 5.7398 }[index], is_ts_guess: index === 8 })),
})

describe("path-search facts", () => {
    it("spells out the method and the ends", () => {
        expect(methodWords("gsm")).toBe("GSM (growing string)")
        expect(methodWords("neb")).toBe("NEB (nudged elastic band)")
        expect(methodWords("freezing_string")).toBe("Freezing string")
        expect(endsWords(SUMMARY)).toBe("double-ended · 2 endpoints")
        expect(endsWords({ ...SUMMARY, is_double_ended: null, source_endpoint_count: null })).toBe("not recorded")
    })

    it("counts stored points against the stated total", () => {
        expect(pointsWords(SUMMARY)).toBe("15 stored of 15")
        expect(pointsWords({ ...SUMMARY, n_points: null, stored_point_count: 1 })).toBe("1 stored")
    })

    it("places the picked guess along the path", () => {
        expect(guessWords(SUMMARY, profile)).toBe("point 8 · 3.759 along the path")
        expect(guessWords({ ...SUMMARY, selected_ts_point_index: null }, profile)).toBe("point 8 · 3.759 along the path")
        expect(guessWords({ ...SUMMARY, selected_ts_point_index: 3 }, profile)).toBe("point 3 (not among the stored points)")
    })

    it("places the picked guess in angstrom when the unit is recorded", () => {
        const inAngstrom = pathSearchProfile({ summary: { ...SUMMARY, path_coordinate_unit: "angstrom" }, points: profile.points.map((p) => ({ point_index: p.index, path_coordinate: p.coordinate, is_ts_guess: p.index === 8 })) })
        expect(guessWords(SUMMARY, inAngstrom)).toBe("point 8 · 3.76 Å along the path")
    })

    it("tells a non-NEB from a NEB whose climbing image was not recorded", () => {
        expect(climbingWords(SUMMARY, profile)).toBe("none (not a NEB)")
        expect(climbingWords({ ...SUMMARY, method: "neb" }, profile)).toBe("not recorded")
        expect(climbingWords({ ...SUMMARY, method: "neb", climbing_image_index: 5 }, profile)).toBe("point 5")
        expect(convergedWords(null)).toBe("not recorded")
        expect(convergedWords(false)).toBe("no")
    })

    it("says plainly when a band kept one image", () => {
        expect(partialBandNote({ ...SUMMARY, method: "neb", stored_point_count: 1 })).toBe("Only one image of this band was deposited, so the path is a single point.")
        expect(partialBandNote(SUMMARY)).toBeNull()
    })
})

function child(type: string, results: Record<string, unknown>, ref = `calc_${type}`): CalculationChild {
    return { calculation: { calculation_ref: ref, type }, results: { kind: type, ...results } }
}

describe("lineage facts", () => {
    it("reads each child's own result", () => {
        expect(childFacts(child("opt", { opt: { converged: true, n_steps: 2, final_energy_hartree: -117.786148505 } }))).toBe("converged in 2 steps · −117.786149 hartree")
        expect(childFacts(child("opt", { opt: { converged: null } }))).toBe("convergence not recorded")
        expect(childFacts(child("freq", { freq: { n_imag: 1, imag_freq_cm1: -1886.4263 } }))).toBe("1 imaginary · 1886.43i cm⁻¹")
        expect(childFacts(child("irc", { irc: { direction: "both", point_count: 92 } }))).toBe("both legs · 92 points")
        expect(childFacts(child("sp", { sp: null }))).toBe("energy not recorded")
    })

    it("orders children opt, freq, sp, irc, scan and names the edge from the guess", () => {
        const ordered = childOrder([child("scan", {}), child("irc", {}), child("freq", {}), child("sp", {})])
        expect(ordered.map((c) => c.calculation.type)).toEqual(["freq", "sp", "irc", "scan"])
        expect(edgeWords("optimized_from", 8)).toBe("optimised from point 8")
        expect(edgeWords("irc_start", null)).toBe("irc start")
    })
})
