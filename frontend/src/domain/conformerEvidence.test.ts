import { describe, expect, it } from "vitest"
import type { ConformerProjection } from "../api/speciesEntryApi"
import {
    conformerLabel,
    partitionByConformer,
    statmechMatchesConformer,
    thermoMatchesConformer,
} from "./conformerEvidence"

// Two DISTINCT conformer basins, each with its own observation and its own
// non-overlapping set of calculation refs -- this is the fixture shape the
// design brief calls out by name: a fixture with only one conformer cannot
// prove *which* conformer a match resolved to, only that matching happened
// at all.
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

describe("thermoMatchesConformer", () => {
    it("matches a record whose sp_calculation_ref belongs to this conformer's own observation", () => {
        const record = { provenance: { sp_calculation_ref: "calc_sp_1", freq_calculation_ref: null, primary_calculation: null } }
        expect(thermoMatchesConformer(record, conformerOne)).toBe(true)
        // The exact same ref must NOT match the other conformer -- this is
        // what a single-conformer fixture could never prove.
        expect(thermoMatchesConformer(record, conformerTwo)).toBe(false)
    })

    it("matches on freq_calculation_ref alone", () => {
        const record = { provenance: { sp_calculation_ref: null, freq_calculation_ref: "calc_freq_2", primary_calculation: null } }
        expect(thermoMatchesConformer(record, conformerTwo)).toBe(true)
        expect(thermoMatchesConformer(record, conformerOne)).toBe(false)
    })

    it("matches on primary_calculation.calculation_ref alone", () => {
        const record = { provenance: { sp_calculation_ref: null, freq_calculation_ref: null, primary_calculation: { calculation_ref: "calc_opt_1" } } }
        expect(thermoMatchesConformer(record, conformerOne)).toBe(true)
    })

    it("treats a record with no matching ref as population B -- not an error, just unmatched", () => {
        const arkaneRecord = { provenance: { sp_calculation_ref: null, freq_calculation_ref: null, primary_calculation: null } }
        expect(thermoMatchesConformer(arkaneRecord, conformerOne)).toBe(false)
        expect(thermoMatchesConformer(arkaneRecord, conformerTwo)).toBe(false)
    })

    it("treats a record with no provenance block at all the same way", () => {
        expect(thermoMatchesConformer({}, conformerOne)).toBe(false)
    })

    it("never matches a conformer with no observations or calculations", () => {
        const bare = group({ observations: [], calculations: [] })
        const record = { provenance: { sp_calculation_ref: "calc_sp_1", freq_calculation_ref: null, primary_calculation: null } }
        expect(thermoMatchesConformer(record, bare)).toBe(false)
    })
})

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
