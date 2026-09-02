import { describe, expect, it } from "vitest"
import type { ConformerGroupFingerprint, ConformerProjection } from "../api/speciesEntryApi"
import {
    basinRangeDeg,
    buildBasinRotors,
    buildGroupDifferences,
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

function conformer(
    ref: string,
    label: string,
    fingerprint: ConformerGroupFingerprint | null,
): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: ref, label, fingerprint },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: 1,
            optimization_chain_count: 1,
            geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 0, sp: 0 },
            levels_of_theory: {},
        },
        observations: [],
        calculations: [],
        geometries: [],
    } as unknown as ConformerProjection
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

describe("buildGroupDifferences", () => {
    it("returns null for a single-group entry -- nothing to compare", () => {
        const single = [conformer("cg_one", "conformer_1", oneRotorFingerprint(4, 65.0))]
        expect(buildGroupDifferences(single)).toBeNull()
    })

    it("returns null when no group has a fingerprint at all", () => {
        const none = [
            conformer("cg_one", "conformer_1", null),
            conformer("cg_two", "conformer_2", null),
        ]
        expect(buildGroupDifferences(none)).toBeNull()
    })

    // The owner's own worked example, as a THREE-group comparison.
    it("three groups with bins [4], [20], [18] produce three distinct ranges for the shared rotor", () => {
        const three = [
            conformer("cg_one", "conformer_1", oneRotorFingerprint(4, 65.0)),
            conformer("cg_two", "conformer_2", oneRotorFingerprint(20, 305.0)),
            conformer("cg_three", "conformer_3", oneRotorFingerprint(18, 275.0)),
        ]
        const rows = buildGroupDifferences(three)
        expect(rows).not.toBeNull()
        expect(rows).toHaveLength(1)
        const [row] = rows!
        expect(row.cells.map((c) => c.binRangeDeg)).toEqual([[60, 75], [300, 315], [270, 285]])
        // No cell exposes a raw bin index either.
        for (const cell of row.cells) expect(cell).not.toHaveProperty("quantizedBin", undefined)
    })

    it("omits a rotor row when every group that tracks it agrees on the bin", () => {
        const sameBin = oneRotorFingerprint(5, 80.0)
        const sameBinOtherRepresentative = oneRotorFingerprint(5, 81.4)
        const two = [
            conformer("cg_one", "conformer_1", sameBin),
            conformer("cg_two", "conformer_2", sameBinOtherRepresentative),
        ]
        expect(buildGroupDifferences(two)).toBeNull()
    })

    it("matches rotors across groups by key, not by array position", () => {
        // Group A lists its one rotor first; group B's fingerprint lists a
        // DIFFERENT rotor it doesn't share, then the shared one second. A
        // positional (index-based) comparison would wrongly pair them.
        const groupA: ConformerGroupFingerprint = {
            rotor_count: 1,
            bin_width_deg: 15,
            torsions: [{ rotor_key: "R_1_2", quantized_bin: 2, raw_torsion_deg: 40.0, folded_torsion_deg: 40.0 }],
        }
        const groupB: ConformerGroupFingerprint = {
            rotor_count: 2,
            bin_width_deg: 15,
            torsions: [
                { rotor_key: "R_5_6", quantized_bin: 9, raw_torsion_deg: 140.0, folded_torsion_deg: 140.0 },
                { rotor_key: "R_1_2", quantized_bin: 6, raw_torsion_deg: 95.0, folded_torsion_deg: 95.0 },
            ],
        }
        const rows = buildGroupDifferences([
            conformer("cg_a", "conformer_1", groupA),
            conformer("cg_b", "conformer_2", groupB),
        ])
        expect(rows).not.toBeNull()
        // R_5_6 is tracked by only one group -- not a comparison row.
        expect(rows!.some((r) => r.rotorKey === "R_5_6")).toBe(false)
        const r1_2 = rows!.find((r) => r.rotorKey === "R_1_2")!
        expect(r1_2.cells[0]).toMatchObject({ conformerGroupRef: "cg_a", binRangeDeg: [30, 45] })
        expect(r1_2.cells[1]).toMatchObject({ conformerGroupRef: "cg_b", binRangeDeg: [90, 105] })
    })

    // `spe_pv7f7evlv422ab54ackh7m4qnq`: two groups, both rotor_count 0,
    // both quantized_bins [], and (per the archive) the identical
    // fingerprint_hash. The fingerprint is what DEFINES a basin, so by the
    // archive's own criterion these two groups are not distinguished from
    // each other -- the comparison must not invent one. This falls out of
    // the ordinary "nothing shared to compare" rule with no special case:
    // two zero-rotor groups share no rotor keys at all.
    it("two groups with identical (here: empty) fingerprints produce no fabricated difference", () => {
        const rigidA: ConformerGroupFingerprint = { rotor_count: 0, bin_width_deg: 15, torsions: [] }
        const rigidB: ConformerGroupFingerprint = { rotor_count: 0, bin_width_deg: 15, torsions: [] }
        const two = [
            conformer("cg_one", "conformer_1", rigidA),
            conformer("cg_two", "conformer_2", rigidB),
        ]
        expect(buildGroupDifferences(two)).toBeNull()
    })

    // Same guarantee, but with actual (non-empty) identical fingerprints --
    // the general case the zero-rotor scenario above is one instance of.
    it("two groups with identical non-empty fingerprints produce no fabricated difference", () => {
        const same = oneRotorFingerprint(4, 65.0)
        const sameAgain = oneRotorFingerprint(4, 65.2) // representative angle may differ; bin does not
        const two = [
            conformer("cg_one", "conformer_1", same),
            conformer("cg_two", "conformer_2", sameAgain),
        ]
        expect(buildGroupDifferences(two)).toBeNull()
    })
})
