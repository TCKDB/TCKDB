import { describe, expect, it } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import {
    conformerLabel,
    partitionByConformer,
    statmechMatchesConformer,
} from "./conformerEvidence"

// Two DISTINCT conformer basins -- a fixture with only one conformer could
// never prove that `statmechMatchesConformer` resolved to a SPECIFIC group
// rather than just returning true for anything. The per-observation
// calculation refs below are otherwise unused now (no thermo-side matching
// exists) but are kept as realistic shape, not exercised by these tests.
function group(overrides: Partial<ConformerProjection> = {}): ConformerProjection {
    return {
        conformer_group: { conformer_group_ref: "cg_one", label: "conformer_1" },
        observations_summary: { total: 1 },
        evidence_summary: {
            calculation_count: 3,
            optimization_chain_count: 1,
            geometry_count: 1,
            evidence_coverage: { opt: 1, freq: 1, sp: 1 },
            levels_of_theory: {},
        },
        observations: [{
            conformer_observation: { conformer_observation_ref: "co_one" },
            calculations: [
                { calculation_ref: "calc_opt_1", type: "opt" },
                { calculation_ref: "calc_freq_1", type: "freq" },
                { calculation_ref: "calc_sp_1", type: "sp" },
            ],
        }],
        calculations: [],
        geometries: [],
        ...overrides,
    } as ConformerProjection
}

const conformerOne = group()
const conformerTwo = group({
    conformer_group: { conformer_group_ref: "cg_two", label: "conformer_2" },
    observations: [{
        conformer_observation: { conformer_observation_ref: "co_two" },
        calculations: [
            { calculation_ref: "calc_opt_2", type: "opt" },
            { calculation_ref: "calc_freq_2", type: "freq" },
            { calculation_ref: "calc_sp_2", type: "sp" },
        ],
    }],
})

describe("conformerLabel", () => {
    it("prefers the deposited basin label", () => {
        expect(conformerLabel(conformerOne)).toBe("conformer_1")
    })

    it("falls back to the stable ref when no label was deposited", () => {
        const unlabeled = group({ conformer_group: { conformer_group_ref: "cg_unlabeled", label: null } })
        expect(conformerLabel(unlabeled)).toBe("cg_unlabeled")
    })
})

// No `thermoMatchesConformer` test suite: the function it covered was
// removed (see the header comment in `conformerEvidence.ts`) because it
// could not actually distinguish the two thermo provenance shapes on the
// current wire -- both carry populated `sp_calculation_ref`/etc, one via
// its own opt/freq/sp chain, the other via a statmech-borrowed route this
// client cannot tell apart from the first. A test suite that verified the
// function's arithmetic would still be lying about what the function
// proved, so it is gone along with the function.

describe("statmechMatchesConformer", () => {
    it("matches on the real conformer_group_ref the archive returned", () => {
        expect(statmechMatchesConformer([{ conformer_group_ref: "cg_one" }], conformerOne)).toBe(true)
        expect(statmechMatchesConformer([{ conformer_group_ref: "cg_one" }], conformerTwo)).toBe(false)
    })

    it("treats an unrequested/empty conformer context as unmatched, not an error", () => {
        expect(statmechMatchesConformer(null, conformerOne)).toBe(false)
        expect(statmechMatchesConformer(undefined, conformerOne)).toBe(false)
        expect(statmechMatchesConformer([], conformerOne)).toBe(false)
    })
})

describe("partitionByConformer", () => {
    it("keeps matched and entry-level records in their original order, never merging or dropping either group", () => {
        const records = ["a", "b", "c", "d"]
        const result = partitionByConformer(records, (value) => value === "b" || value === "d")
        expect(result.matched).toEqual(["b", "d"])
        expect(result.entryLevel).toEqual(["a", "c"])
    })

    it("puts everything in entryLevel when nothing matches, and nothing in matched", () => {
        const result = partitionByConformer([1, 2, 3], () => false)
        expect(result.matched).toEqual([])
        expect(result.entryLevel).toEqual([1, 2, 3])
    })
})
