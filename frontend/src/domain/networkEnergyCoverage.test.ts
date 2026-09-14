import { describe, expect, it } from "vitest"
import { computeSpeciesEnergyCoverage, coverageFraction } from "./networkEnergyCoverage"

describe("computeSpeciesEnergyCoverage", () => {
    it("counts how many of the unique species entry refs have thermo, live from the presence map", () => {
        const refs = ["spe_a", "spe_b", "spe_c"]
        const presence = { spe_a: true, spe_b: false, spe_c: false }
        expect(computeSpeciesEnergyCoverage(refs, presence)).toEqual({ withThermo: 1, total: 3 })
    })

    it("the hydrazine network's own measured shape: 0 of 9 species entries have thermo", () => {
        const refs = [
            "spe_7ioiqvdqm6cyumgnrammbhefum", "spe_knzmwhplnwnpodfp6frphrl7ei", "spe_wi6mz65sb47vzsyop3tqrqrcai",
            "spe_zsvxdy7fbktworbhss2da62doi", "spe_cft35qrkqphdifcfqlcenqgdau", "spe_qefrbgmpyryylyocpghhc2ikki",
            "spe_fvlbwfuoauoeknq6l5esaozqai", "spe_c4ty3ixcmyuljgqfs73pdecmte", "spe_gzk56q4jegyyg7ylbcdqb2xvka",
        ]
        const presence = Object.fromEntries(refs.map((ref) => [ref, false]))
        expect(computeSpeciesEnergyCoverage(refs, presence)).toEqual({ withThermo: 0, total: 9 })
    })

    it("counts a species entry referenced by more than one state exactly once", () => {
        // `[H][H]` (spe_7io...) participates in three of the hydrazine
        // network's seven states -- the coverage figure is about DISTINCT
        // species entries, not state-participation slots.
        const refs = ["spe_h2", "spe_h2", "spe_h2", "spe_other"]
        const presence = { spe_h2: true, spe_other: false }
        expect(computeSpeciesEnergyCoverage(refs, presence)).toEqual({ withThermo: 1, total: 2 })
    })

    it("a ref absent from the presence map counts as not-covered, never throws", () => {
        expect(computeSpeciesEnergyCoverage(["spe_unknown"], {})).toEqual({ withThermo: 0, total: 1 })
    })

    it("no species entry refs at all -- zero and zero, not a division-by-zero surprise", () => {
        expect(computeSpeciesEnergyCoverage([], {})).toEqual({ withThermo: 0, total: 0 })
    })

    // MUTATION TABLE (a): hardcode the energy-coverage figure instead of
    // computing it. Land `return { withThermo: 0, total: 9 }` as the first
    // line of `computeSpeciesEnergyCoverage` in `networkEnergyCoverage.ts`
    // and this test goes RED -- a fixture with non-hydrazine data proves the
    // function is not just returning the one figure this archive happens to
    // measure today.
    it("does not regress to the hydrazine network's own hardcoded 0-of-9 figure on a DIFFERENT fixture", () => {
        const refs = ["spe_x", "spe_y", "spe_z", "spe_w", "spe_v"]
        const presence = { spe_x: true, spe_y: true, spe_z: true, spe_w: false, spe_v: false }
        const result = computeSpeciesEnergyCoverage(refs, presence)
        expect(result).toEqual({ withThermo: 3, total: 5 })
        expect(result).not.toEqual({ withThermo: 0, total: 9 })
    })
})

describe("coverageFraction", () => {
    it("passes the covered/total counts through unchanged -- a named seam, not a computation of its own", () => {
        expect(coverageFraction(4, 21)).toEqual({ covered: 4, total: 21 })
        expect(coverageFraction(0, 0)).toEqual({ covered: 0, total: 0 })
    })
})
