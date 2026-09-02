import { describe, expect, it } from "vitest"
import type { ConformerGroupFingerprint } from "../api/speciesEntryApi"
import {
    basinRangeDeg,
    buildBasinRotors,
    formatDeg,
    formatRangeDeg,
    parseRotorKey,
    rotorBondLabel,
} from "./conformerFingerprint"

// The owner's own worked example (spe_mbdqifmaclaakukr7agxbuq3wa, one
// rotor, three groups): bins [4], [20], [18] at a 15deg width -> ranges
// 60-75, 300-315, 270-285. Reused below because it is the exact shape a
// correct answer must produce.
const ROTOR_KEY = "R_8_10"

function oneRotorFingerprint(quantizedBin: number, rawDeg: number): ConformerGroupFingerprint {
    return {
        rotor_count: 1,
        bin_width_deg: 15,
        torsions: [{
            rotor_key: ROTOR_KEY,
            quantized_bin: quantizedBin,
            raw_torsion_deg: rawDeg,
            folded_torsion_deg: rawDeg,
        }],
    }
}

describe("parseRotorKey / rotorBondLabel", () => {
    it("decodes a canonical R_<a>_<b> key into its atom index pair", () => {
        expect(parseRotorKey("R_8_10")).toEqual({ atomA: 8, atomB: 10 })
        expect(rotorBondLabel("R_8_10")).toBe("atoms 8–10")
    })

    it("falls back to the raw key, never blank, for anything that doesn't parse", () => {
        expect(parseRotorKey("not-a-rotor-key")).toBeNull()
        expect(rotorBondLabel("not-a-rotor-key")).toBe("not-a-rotor-key")
    })
})

describe("basinRangeDeg / formatDeg -- the owner's own worked example", () => {
    it("bins [4], [20], [18] at 15deg width produce 60-75, 300-315, 270-285", () => {
        expect(basinRangeDeg(4, 15)).toEqual([60, 75])
        expect(basinRangeDeg(20, 15)).toEqual([300, 315])
        expect(basinRangeDeg(18, 15)).toEqual([270, 285])
    })

    it("rounds to one decimal place for display, and formats a range with one trailing degree sign", () => {
        expect(formatDeg(359.9994)).toBe("360°")
        expect(formatRangeDeg([345, 360])).toBe("345–360°")
    })
})

describe("buildBasinRotors -- never the bin index, only the range", () => {
    it("view carries binRangeDeg but no quantizedBin field at all", () => {
        const rotors = buildBasinRotors(oneRotorFingerprint(4, 65.0))
        expect(rotors).toHaveLength(1)
        expect(rotors[0]).toMatchObject({
            rotorKey: ROTOR_KEY,
            bondLabel: "atoms 8–10",
            binRangeDeg: [60, 75],
            isFolded: false,
            representativeRawDeg: 65.0,
            representativeFoldedDeg: null,
        })
        // No `quantizedBin` property anywhere on the rendered view -- a
        // reader must never be shown the internal bin index (the owner:
        // "I just think showing bin 23, 3 makes no sense to the user").
        expect(rotors[0]).not.toHaveProperty("quantizedBin")
    })

    it("flags isFolded and carries both angles when symmetry folding actually moved the angle", () => {
        const fp: ConformerGroupFingerprint = {
            rotor_count: 1,
            bin_width_deg: 15,
            torsions: [{ rotor_key: "R_1_2", quantized_bin: 0, raw_torsion_deg: 370.0, folded_torsion_deg: 10.0 }],
        }
        const [rotor] = buildBasinRotors(fp)
        expect(rotor.isFolded).toBe(true)
        expect(rotor.representativeRawDeg).toBe(370.0)
        expect(rotor.representativeFoldedDeg).toBe(10.0)
    })

    it("does not flag isFolded when raw and folded already agree", () => {
        const [rotor] = buildBasinRotors(oneRotorFingerprint(4, 65.0))
        expect(rotor.isFolded).toBe(false)
        expect(rotor.representativeFoldedDeg).toBeNull()
    })

    // The majority case, measured: 37 of 66 groups have NO rotors at all.
    it("returns an empty array for a rigid group with zero rotors -- not null, not a crash", () => {
        const rigid: ConformerGroupFingerprint = { rotor_count: 0, bin_width_deg: 15, torsions: [] }
        expect(buildBasinRotors(rigid)).toEqual([])
    })

    // The exact assertion the brief calls "the assertion that catches a
    // zip/index bug": rotor keys NOT in sorted order, angles distinguishable
    // enough that a swapped pairing is unmistakable.
    it("preserves rotor/angle pairing even when rotor keys are not in sorted order", () => {
        const fp: ConformerGroupFingerprint = {
            rotor_count: 3,
            bin_width_deg: 10,
            torsions: [
                { rotor_key: "R_9_10", quantized_bin: 7, raw_torsion_deg: 70.5, folded_torsion_deg: 70.5 },
                { rotor_key: "R_1_2", quantized_bin: 1, raw_torsion_deg: 12.2, folded_torsion_deg: 12.2 },
                { rotor_key: "R_20_21", quantized_bin: 30, raw_torsion_deg: 305.9, folded_torsion_deg: 305.9 },
            ],
        }
        const rotors = buildBasinRotors(fp)
        const byKey = Object.fromEntries(rotors.map((r) => [r.rotorKey, r]))
        expect(byKey["R_9_10"]).toMatchObject({ binRangeDeg: [70, 80], representativeRawDeg: 70.5 })
        expect(byKey["R_1_2"]).toMatchObject({ binRangeDeg: [10, 20], representativeRawDeg: 12.2 })
        expect(byKey["R_20_21"]).toMatchObject({ binRangeDeg: [300, 310], representativeRawDeg: 305.9 })
    })
})

